"""AI report text: prompts, output schema, number check, Markdown, comparison.

Pure functions (no DB, no network) so they can be unit-tested anywhere.
"""
import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal

LANGS = ("zh", "en")
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
        "recommendations": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
    },
    "required": ["summary", "findings", "recommendations"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = {
    "zh": (
        "你是 NXP IW611（Wi-Fi 6 + BT）無線模組產線的測試品質工程師，依照系統提供的事實表撰寫工單品質報告。\n"
        "事實表說明：一台機器（unit）以 MAC1 計；yield.latest 是每台最後一次測試的結果（主要良率），"
        "yield.first_pass 是第一次測試就通過，yield.any_pass 是任一次通過；attempts 是測試次數（包含重測與沒有 MAC 的測試），"
        "不是台數；top_fail_steps 的 attempts 是失敗次數、units 是台數、recovered_units 是之後重測通過的台數；"
        "spec_watch 是離規格最近的量測項目（cpk 越小、margin 越小越接近規格，負的 margin 表示超出規格）。\n"
        "規則：\n"
        "1. 全部使用繁體中文。\n"
        "2. 只能引用事實表中出現過的數字，照原樣寫出；不可以自行計算新的數字（例如差值、比例、平均），也可以不寫數字。\n"
        "3. 寫數量時一定要分清楚是「台」還是「次」。\n"
        "4. summary 是一句總評；findings 與 recommendations 各 3 點，每點一句。\n"
        "5. 提到失敗原因時寫出測試步驟名稱；提到量測項目時寫出它的名稱與條件（頻段、標準、速率）。\n"
        "6. 風險等級已由系統依門檻判定（risk.level），內容必須與它一致，不要自行改判。\n"
        "7. 不要使用 Markdown 符號。"
    ),
    "en": (
        "You are a test-quality engineer for an NXP IW611 (Wi-Fi 6 + BT) module production line. "
        "Write a work-order quality report from the facts table provided by the system.\n"
        "About the facts: a unit is one MAC1; yield.latest is each unit's last result (the headline yield), "
        "yield.first_pass means passed on the first attempt, yield.any_pass means passed on any attempt; attempts are "
        "test runs (including retests and runs without a MAC), not units; in top_fail_steps, attempts are failed runs, "
        "units are distinct units and recovered_units later passed on a retest; spec_watch lists the measurements "
        "closest to their limits (lower cpk and margin are closer; a negative margin is out of spec).\n"
        "Rules:\n"
        "1. Write in English.\n"
        "2. Only use numbers that appear in the facts table, written as they appear. Do not compute new numbers "
        "(no differences, ratios or averages); you may also write no numbers at all.\n"
        "3. Always say whether a count is units or test runs.\n"
        "4. 'summary' is one sentence; 'findings' and 'recommendations' have exactly 3 items, one sentence each.\n"
        "5. Name the test step for a failure cause; name a measurement with its conditions (band, standard, rate).\n"
        "6. The risk level was decided by the system from thresholds (risk.level); stay consistent with it.\n"
        "7. Do not use Markdown symbols."
    ),
}

ASK_PROMPT = (
    "You are the test-quality assistant for this IW611 work order. Answer only from the facts table and the "
    "report below.\nRules: 1) Answer in the language of the question (default {default}). 2) Only quote numbers "
    "from the facts; never compute or invent them. 3) If the facts do not cover it, say the data does not show it. "
    "4) At most 5 sentences, no Markdown headings or tables.\n"
)

NUM_RE = re.compile(r"(?<![A-Za-z_\d.])-?\d[\d,]*(?:\.\d+)?")
IDENT_RE = re.compile(r"\d{4}-\d{6,}|[A-Za-z]+_[A-Za-z0-9_]*\d+[A-Za-z0-9_]*|IW\d+\w*|\b\d+(?:\.\d+)?\s*(?:GHz|MHz)\b",
                      re.IGNORECASE)


def plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def level(value, good, warn, higher_is_better=True):
    if value is None:
        return "unknown"
    if higher_is_better:
        return "good" if value >= good else "warn" if value >= warn else "bad"
    return "good" if value <= good else "warn" if value <= warn else "bad"


def facts_hash(facts):
    return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ─── Number check ────────────────────────────────────────────────────────────

def _fact_numbers(obj, out):
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _fact_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _fact_numbers(v, out)
    elif isinstance(obj, str):
        for m in re.findall(r"\d+(?:\.\d+)?", obj):   # dates, times, band names inside strings
            out.add(float(m))
    return out


def numbers_in(text):
    cleaned = IDENT_RE.sub(" ", text or "")
    values = []
    for m in NUM_RE.findall(cleaned):
        try:
            values.append((m, float(m.replace(",", ""))))
        except ValueError:
            pass
    return values


def check_numbers(report, facts):
    """→ {"ok": bool, "unknown": [numbers not found in the facts]}."""
    allowed = _fact_numbers(facts, set())
    allowed |= {abs(v) for v in allowed}
    allowed |= {1.0, 2.0, 3.0, 5.0}           # "3 work orders", "top 5"
    texts = [report.get("summary", "")] + list(report.get("findings", [])) + list(report.get("recommendations", []))
    unknown = []
    for text in texts:
        for raw, value in numbers_in(text):
            if not any(abs(value - a) < 1e-9 or any(abs(value - round(a, d)) < 1e-9 for d in (0, 1, 2, 3))
                       for a in allowed):
                unknown.append(raw)
    return {"ok": not unknown, "unknown": sorted(set(unknown))}


# ─── Presentation ────────────────────────────────────────────────────────────

RISK_LABEL = {"zh": {"low": "低", "medium": "中", "high": "高"}, "en": {"low": "Low", "medium": "Medium", "high": "High"}}


def item_label(item):
    parts = [item.get("technology"), item.get("band"), item.get("standard"), item.get("bandwidth"), item.get("rate"),
             f"{item['frequency_mhz']} MHz" if item.get("frequency_mhz") else None]
    return f"{item.get('metric_name')} ({' '.join(p for p in parts if p)})"


def to_markdown(row, lang, tz="Asia/Taipei"):
    facts = row["facts"] or {}
    report = row.get(f"report_{lang}") or {}
    zh = lang == "zh"
    risk = (facts.get("risk") or {}).get("level", "")
    lines = [f"# {'工單品質報告' if zh else 'Work Order Quality Report'} — {facts.get('work_order', '')}", ""]
    gen = row.get("finished_at")
    if gen:
        from zoneinfo import ZoneInfo
        gen = gen.astimezone(ZoneInfo(tz)) if gen.tzinfo else gen
        lines.append(f"{'產生時間' if zh else 'Generated'}: {gen:%Y-%m-%d %H:%M}")
    lines.append(f"{'風險等級' if zh else 'Risk level'}: {RISK_LABEL[lang].get(risk, risk)}")
    y = facts.get("yield") or {}
    att = facts.get("attempts") or {}
    lines += ["", f"## {'數據' if zh else 'Numbers'}", "",
              f"| {'項目' if zh else 'Item'} | {'數值' if zh else 'Value'} |", "|---|---|"]
    rows = [("測試台數" if zh else "Units", facts.get("units")),
            ("良率（最後結果）" if zh else "Yield (latest result)", f"{(y.get('latest') or {}).get('yield_pct')}%"),
            ("一次通過率" if zh else "First-pass yield", f"{(y.get('first_pass') or {}).get('yield_pct')}%"),
            ("任一次通過" if zh else "Any-pass yield", f"{(y.get('any_pass') or {}).get('yield_pct')}%"),
            ("測試次數 PASS / FAIL / STOP" if zh else "Test runs PASS / FAIL / STOP",
             f"{att.get('pass')} / {att.get('fail')} / {att.get('stop')}"),
            ("沒有 MAC 的測試" if zh else "Runs without MAC", att.get("unknown_mac")),
            ("重測率" if zh else "Retest rate", f"{facts.get('retry_rate')}%")]
    planned = facts.get("planned")
    if planned:
        rows += [("計畫數" if zh else "Planned", planned.get("planned_qty")),
                 ("Aligned 良率" if zh else "Aligned yield", f"{planned.get('aligned_yield_pct')}%")]
    period = facts.get("period") or {}
    rows.append(("測試期間" if zh else "Period", f"{period.get('first_test', '')} ~ {period.get('last_test', '')}"))
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines += ["", f"## {'總評' if zh else 'Summary'}", "", report.get("summary", ""), "",
              f"## {'發現' if zh else 'Findings'}", ""]
    lines += [f"{i}. {t}" for i, t in enumerate(report.get("findings", []), 1)]
    lines += ["", f"## {'建議' if zh else 'Recommendations'}", ""]
    lines += [f"{i}. {t}" for i, t in enumerate(report.get("recommendations", []), 1)]
    steps = facts.get("top_fail_steps") or []
    if steps:
        lines += ["", f"## {'主要失敗步驟' if zh else 'Top failing steps'}", "",
                  f"| {'步驟' if zh else 'Step'} | {'次數' if zh else 'Runs'} | {'台數' if zh else 'Units'} | "
                  f"{'重測後通過' if zh else 'Recovered'} |", "|---|---|---|---|"]
        lines += [f"| {s['step']} | {s['attempts']} | {s['units']} | {s['recovered_units']} |" for s in steps]
    watch = facts.get("spec_watch") or []
    if watch:
        lines += ["", f"## {'接近規格的量測' if zh else 'Measurements closest to spec'}", "",
                  f"| {'項目' if zh else 'Item'} | {'平均' if zh else 'Mean'} | {'規格' if zh else 'Limits'} | Cpk | "
                  f"{'餘裕' if zh else 'Margin'} |", "|---|---|---|---|---|"]
        lines += [f"| {w['item']} | {w['mean']} | {w['limit_low']} ~ {w['limit_high']} | {w['cpk']} | {w['margin']} |"
                  for w in watch]
    return "\n".join(lines) + "\n"


def compare(base_facts, target_facts):
    """What changed between two reports' facts."""
    def g(f, *path):
        for p in path:
            f = (f or {}).get(p)
        return f
    keys = [("units", ("units",)), ("latest_yield", ("yield", "latest", "yield_pct")),
            ("first_pass_yield", ("yield", "first_pass", "yield_pct")),
            ("any_pass_yield", ("yield", "any_pass", "yield_pct")), ("retry_rate", ("retry_rate",)),
            ("attempts", ("attempts", "total")), ("aligned_yield_pct", ("planned", "aligned_yield_pct")),
            ("risk", ("risk", "level"))]
    changes = []
    for name, path in keys:
        a, b = g(base_facts, *path), g(target_facts, *path)
        if a != b:
            delta = round(b - a, 2) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
            changes.append({"field": name, "before": a, "after": b, "delta": delta})
    a_steps = {s["step"]: s["attempts"] for s in base_facts.get("top_fail_steps") or []}
    b_steps = {s["step"]: s["attempts"] for s in target_facts.get("top_fail_steps") or []}
    return {
        "changes": changes,
        "steps_added": sorted(set(b_steps) - set(a_steps)),
        "steps_removed": sorted(set(a_steps) - set(b_steps)),
        "steps_changed": [{"step": k, "before": a_steps[k], "after": b_steps[k]}
                          for k in sorted(set(a_steps) & set(b_steps)) if a_steps[k] != b_steps[k]],
    }
