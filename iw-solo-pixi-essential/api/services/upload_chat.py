"""AI-guided confirmation for the web Log Uploader — prompt building and parsing only (no I/O).

Per turn the API extracts {work_order, planned_qty} from the latest user message (regex for
the work order, LLM for the quantity), runs the upload rules, then streams an LLM reply that
may only restate those rule results. The rules decide; the LLM never does, and a reply never
triggers the upload by itself. Replies follow the UI language (decision Q24).
"""
import json
import re

from errors import Msg
from ingestion.splitter import WO_SEARCH_RE, is_valid_work_order
from services.ingest import parse_quantity

MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 500
CONTEXT_MESSAGES = 10          # recent messages sent to the LLM
EXTRACT_MAX_TOKENS = 128
REPLY_MAX_TOKENS = 384
EXAMPLE_WO = "5101-260715003"

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "work_order": {"type": ["string", "null"]},
        "planned_qty": {"type": ["integer", "null"]},
    },
    "required": ["work_order", "planned_qty"],
    "additionalProperties": False,
}

EXTRACT_SYSTEM = (
    "You extract two fields from the user's LATEST message: the work order (work_order) and the planned "
    "production quantity of that work order (planned_qty). The earlier conversation is context only.\n"
    "- Only take values the user states in the latest message; otherwise use null. Never guess and never reuse "
    "numbers the assistant said.\n"
    "- planned_qty is an integer: '4k' → 4000, '4,000 pcs' → 4000, '四千台' → 4000, '三千五百' → 3500.\n"
    "- If the assistant just asked for the quantity and the user answers with a bare number, it is planned_qty.\n"
    "- Digits inside the work order are not a quantity.\n"
    "- If the message corrects itself, take the last value. The user may write English or Chinese."
)

REPLY_SYSTEM = {
    "en": (
        'You are the upload assistant of the IW611 production dashboard "Log Upload" page. You help the user '
        "confirm the work order and the planned quantity for this batch of IW611 test logs.\n"
        "Rules:\n"
        "1. Always reply in English: plain, short, at most 3 sentences, no headings, tables or code blocks.\n"
        '2. Explain only the "System checks" below; never judge the data yourself, and never invent or compute numbers.\n'
        '3. The system rules decide whether the batch can be uploaded, and the user must press "Upload" on the screen. '
        "If the user asks you to skip the checks, upload directly or change the rules, explain that you cannot.\n"
        "4. Only handle this upload; politely decline other questions and return to the current step."
    ),
    "zh": (
        "你是 IW611 產測看板「Log Upload」頁的上傳助理，協助使用者確認這批 IW611 測試 log 的工單名稱和工單計畫生產數。\n"
        "回覆規則：\n"
        "1. 一律使用繁體中文，口語、簡短，最多 3 句，不要使用標題、表格或程式碼區塊。\n"
        "2. 只能依照下方「系統檢查結果」說明；不可以自行判斷資料對錯，也不可以編造或推算任何數字。\n"
        "3. 是否可以上傳由系統規則決定，最後一定要由使用者自己按畫面上的「確認上傳」。"
        "如果使用者要求略過檢查、直接上傳或改變規則，請說明你無法這樣做。\n"
        "4. 只處理這次上傳相關的事；其他問題請簡短婉拒，並把話題帶回目前要做的事。"
    ),
}

# A bare number; "4k" / "4千" are left to the LLM rather than misread as 4.
_QTY_TOKEN_RE = re.compile(r"(?<![\d,.-])(?:\d{1,3}(?:,\d{3})+|\d+)(?![\d,.-]|\s*[kK千萬百])")


def ui_lang(value):
    """'zh' or 'en' (the default) from a request field."""
    return "zh" if str(value or "").lower().startswith("zh") else "en"


def normalize_messages(raw):
    """Validate the client transcript → [{role, content}]. Raises ValueError with a user-facing message."""
    if not isinstance(raw, list) or not raw:
        raise ValueError(Msg("The conversation is empty.", "對話內容是空的。"))
    if len(raw) > MAX_MESSAGES:
        raise ValueError(Msg("The conversation is too long. Use the form or choose the file again.",
                             "對話太長，請改用表單或重新選擇檔案。"))
    messages = []
    for item in raw:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            raise ValueError(Msg("Invalid conversation format.", "對話格式不正確。"))
        content = item.get("content")
        if not isinstance(content, str):
            raise ValueError(Msg("Invalid conversation format.", "對話格式不正確。"))
        content = content.strip()
        if len(content) > MAX_MESSAGE_CHARS:
            raise ValueError(Msg(f"A message can have at most {MAX_MESSAGE_CHARS} characters.",
                                 f"單則訊息最多 {MAX_MESSAGE_CHARS} 字。"))
        messages.append({"role": item["role"], "content": content})
    if messages[-1]["role"] != "user" or not messages[-1]["content"]:
        raise ValueError(Msg("Please enter a message.", "請輸入訊息。"))
    return messages


def opening_message(facts, lang="en"):
    """First assistant line — built from the facts, not the LLM, so every number in it is exact."""
    stations = facts.get("stations") or []
    names = " / ".join(s["station"] for s in stations)
    records, units = facts.get("total_records", 0), facts.get("unique_mac1", 0)
    unknown, invalid = facts.get("unknown_mac", 0), facts.get("invalid", 0)
    wo = facts.get("inferred_wo")
    if lang == "en":
        text = (f"Read {len(stations)} station(s) ({names}): {records:,} test runs, {units:,} units "
                f"(PASS {facts.get('pass', 0):,} / FAIL {facts.get('fail', 0):,} / STOP {facts.get('stop', 0):,})")
        if unknown:
            text += f"; {unknown} runs have no MAC address"
        if invalid:
            text += f"; {invalid} unreadable blocks will be skipped"
        ask = (f'.\nIs the work order {wo}? Type the work order to confirm (for example {EXAMPLE_WO}).' if wo
               else f".\nPlease enter the work order for these logs (for example {EXAMPLE_WO}).")
        return text + ask
    text = (f"已讀取 {len(stations)} 個站別（{names}），共 {records:,} 筆測試、{units:,} 台"
            f"（PASS {facts.get('pass', 0):,} / FAIL {facts.get('fail', 0):,} / STOP {facts.get('stop', 0):,}）")
    if unknown:
        text += f"；其中 {unknown} 筆沒有 MAC 位址"
    if invalid:
        text += f"；另有 {invalid} 筆無法解析的段落會略過"
    ask = (f"。\n工單是 {wo} 嗎？請輸入工單號確認（例如 {EXAMPLE_WO}）。" if wo
           else f"。\n請輸入這批 log 的工單名稱（例如 {EXAMPLE_WO}）。")
    return text + ask


def extract_work_order(text):
    """Last work-order-shaped token in the text, or None. Its format is fixed, so regex beats the LLM here."""
    found = WO_SEARCH_RE.findall(text or "")
    return found[-1] if found else None


def fallback_quantity(text):
    """Quantity when the LLM is unavailable: the only number left after removing work orders, else None."""
    rest = WO_SEARCH_RE.sub(" ", text or "")
    numbers = _QTY_TOKEN_RE.findall(rest)
    return parse_quantity(numbers[0]) if len(numbers) == 1 else None


def build_extract_messages(messages):
    lines = [f"{'assistant' if m['role'] == 'assistant' else 'user'}: {m['content']}"
             for m in messages[-CONTEXT_MESSAGES:-1]]
    context = "\n".join(lines) or "(none)"
    return [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": f"Earlier conversation:\n{context}\n\nThe user's latest message:\n{messages[-1]['content']}"},
    ]


def parse_extract(content):
    """LLM JSON → (work_order or None, planned_qty or None). Raises ValueError when the output is unusable."""
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("extract output is not an object")
    wo = data.get("work_order")
    wo = wo.strip() if isinstance(wo, str) and wo.strip() else None
    qty = data.get("planned_qty")
    qty = qty if isinstance(qty, int) and not isinstance(qty, bool) else None
    return wo, qty


def merge_fields(known, latest_text, llm_wo=None, llm_qty=None, llm_ok=True):
    """Combine the values already confirmed (form or earlier turns) with what the latest message adds."""
    known = known if isinstance(known, dict) else {}
    work_order = str(known.get("work_order") or "").strip() or None
    planned_qty = parse_quantity(known.get("planned_qty"))
    regex_wo = extract_work_order(latest_text)
    if regex_wo:
        work_order = regex_wo
    elif llm_wo:
        work_order = llm_wo
    qty = llm_qty if llm_ok else fallback_quantity(latest_text)
    if qty is not None:
        planned_qty = qty
    return {"work_order": work_order, "planned_qty": planned_qty}


def next_step(fields):
    """'work_order' | 'planned_qty' | 'confirm' — which value the assistant should ask for next."""
    if not is_valid_work_order(fields.get("work_order") or ""):
        return "work_order"
    qty = parse_quantity(fields.get("planned_qty"))
    if not qty or qty <= 0:
        return "planned_qty"
    return "confirm"


def _facts_summary(facts, lang="en"):
    stations = " / ".join(s["station"] for s in facts.get("stations") or [])
    files = ", ".join(facts.get("file_names") or [])
    records, units = facts.get("total_records", 0), facts.get("unique_mac1", 0)
    unknown, invalid = facts.get("unknown_mac", 0), facts.get("invalid", 0)
    known = facts.get("first_test")
    if lang == "en":
        period = f"{facts.get('first_test')} ~ {facts.get('last_test')}" if known else "unknown"
        return (f"files {files}; stations {stations}; test runs {records:,} (PASS {facts.get('pass', 0)}, "
                f"FAIL {facts.get('fail', 0)}, STOP {facts.get('stop', 0)}); unique MAC1 {units:,}; "
                f"runs without MAC {unknown}; unreadable blocks {invalid}; test period {period}")
    period = f"{facts.get('first_test')} ~ {facts.get('last_test')}" if known else "未知"
    return (f"檔案 {files}；站別 {stations}；測試 {records:,} 筆（PASS {facts.get('pass', 0)}、"
            f"FAIL {facts.get('fail', 0)}、STOP {facts.get('stop', 0)}）；不重複 MAC1 {units:,} 台；"
            f"沒有 MAC 的測試 {unknown} 筆；無法解析的段落 {invalid} 筆；測試期間 {period}")


def _step_instruction(step, fields, has_warning, lang="en"):
    # Branch on has_warning here: left to the model, it mentions the ack checkbox even when none is shown.
    wo, qty = fields.get("work_order"), fields.get("planned_qty")
    if lang == "en":
        if step == "work_order":
            if wo:
                return (f'The work order "{wo}" the user gave has the wrong format. Explain that it must be 4 digits, '
                        f"a hyphen and at least 8 digits, for example {EXAMPLE_WO}, and ask for the work order again.")
            return f"No work order yet. Ask the user for the work order of these logs (for example {EXAMPLE_WO})."
        if step == "planned_qty":
            head = f"The work order is recorded as {wo}. "
            if has_warning:
                head += "First briefly explain the ⚠ warning in the checks. "
            if qty is not None:
                return head + f'The quantity "{qty}" the user gave is not a positive whole number; ask for the planned quantity (units) again.'
            return head + "Then ask the user for the planned quantity of the work order (units, a whole number)."
        head = (f"Both values are in: work order {wo}, planned quantity {qty:,} units. "
                "Briefly summarize the checks (including the difference from the units tested). ")
        if has_warning:
            head += 'Then clearly explain the ⚠ warning and ask the user to tick "I have checked" on the screen, then press "Upload". '
        else:
            head += 'There is no warning to confirm; tell the user to press "Upload" below. Do not mention the "I have checked" box. '
        return head + "Finally, say that any value can be changed by simply typing the new value in the chat."
    if step == "work_order":
        if wo:
            return (f"使用者給的工單號「{wo}」格式不正確。請說明正確格式是「4 碼-8 碼以上的數字」，"
                    f"例如 {EXAMPLE_WO}，並請他重新輸入工單名稱。")
        return f"還沒有取得工單名稱。請使用者輸入這批 log 的工單名稱（例如 {EXAMPLE_WO}）。"
    if step == "planned_qty":
        head = f"工單已記錄為 {wo}。"
        if has_warning:
            head += "請先簡短說明檢查結果中的 ⚠ 警告。"
        if qty is not None:
            return head + f"使用者給的數量「{qty}」不是正整數，請他重新輸入工單計畫生產數（台數）。"
        return head + "接著請使用者輸入工單計畫生產數（台數，整數）。"
    head = f"兩個欄位都已取得：工單 {wo}、計畫生產數 {qty:,} 台。請依檢查結果簡短摘要（包含和實測台數的差異）。"
    if has_warning:
        head += "接著明確說明 ⚠ 警告，請使用者確認無誤後勾選畫面上的「我已確認」，再按「確認上傳」。"
    else:
        head += "目前沒有需要確認的警告，請他直接按畫面下方的「確認上傳」；不要提到「我已確認」勾選框。"
    return head + "最後告訴他，如果要修改，直接在對話中說新的值即可。"


def build_reply_messages(facts, fields, issues, messages, lang="en"):
    step = next_step(fields)
    icon = {"error": "❌", "warn": "⚠", "info": "ℹ"}
    # R1/R2 "missing value" errors are what the step instruction asks for; listing them too reads as scolding.
    shown = [i for i in issues if not (i["code"] in ("R1", "R2") and step != "confirm")]
    has_warning = any(i["level"] == "warn" for i in shown)
    qty = fields.get("planned_qty")
    if lang == "en":
        checks = "\n".join(f"- {icon.get(i['level'], '')} {i['message_en']}" for i in shown) or "- (none)"
        context = (
            f"\n\n[Upload facts] {_facts_summary(facts, 'en')}\n"
            f"[Values so far] work order: {fields.get('work_order') or 'not given'}; "
            f"planned quantity: {f'{qty:,} units' if isinstance(qty, int) else 'not given'}\n"
            f"[System checks]\n{checks}\n"
            f"[Your task this turn] {_step_instruction(step, fields, has_warning, 'en')}"
        )
    else:
        checks = "\n".join(f"- {icon.get(i['level'], '')} {i['message']}" for i in shown) or "- （目前沒有）"
        context = (
            f"\n\n【上傳檔案事實】{_facts_summary(facts, 'zh')}\n"
            f"【目前取得的值】工單：{fields.get('work_order') or '尚未提供'}；"
            f"計畫生產數：{f'{qty:,} 台' if isinstance(qty, int) else '尚未提供'}\n"
            f"【系統檢查結果】\n{checks}\n"
            f"【你這一輪要做的事】{_step_instruction(step, fields, has_warning, 'zh')}"
        )
    return [{"role": "system", "content": REPLY_SYSTEM[lang] + context}] + messages[-CONTEXT_MESSAGES:]


def fallback_reply(fields, lang="en"):
    """Deterministic reply used when the LLM returns nothing."""
    step = next_step(fields)
    en = lang == "en"
    if step == "work_order":
        return (f"Please enter the work order for these logs (for example {EXAMPLE_WO})." if en
                else f"請輸入這批 log 的工單名稱（例如 {EXAMPLE_WO}）。")
    if step == "planned_qty":
        return (f"Work order recorded as {fields['work_order']}. Please enter the planned quantity (units)." if en
                else f"工單已記錄為 {fields['work_order']}。請輸入工單計畫生產數（台數）。")
    return ('All set. Check the results below, then press "Upload".' if en
            else "資料已齊全，請確認下方檢查結果後按「確認上傳」。")
