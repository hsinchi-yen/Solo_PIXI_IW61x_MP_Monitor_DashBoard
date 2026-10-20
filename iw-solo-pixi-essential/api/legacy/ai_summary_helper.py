from __future__ import annotations

"""Prompt builder for IW61x/IW611 production test AI summaries.

Pure and network-free by design: builds the `messages` payload sent to an
OpenAI-compatible chat-completions endpoint. The caller (api/app.py) owns the
actual HTTP call so this module stays trivially testable.
"""


def _alert_labels(yield_pct: float) -> str:
    if yield_pct >= 99.2:
        return "NORMAL"
    if yield_pct >= 98.5:
        return "WARNING"
    return "ALARM"


def _retest_alert_labels(retry_rate: float) -> str:
    if retry_rate <= 3:
        return "NORMAL"
    if retry_rate <= 5:
        return "WARNING"
    if retry_rate <= 8:
        return "ALARM"
    return "CRITICAL"


def build_summary_messages(stats: dict, fails_text: str, wo: str, lang: str = "zh", mode: str = "normal") -> list[dict]:
    yield_alert = _alert_labels(stats.get("yield_pct", 0))
    retry_alert = _retest_alert_labels(stats.get("retry_rate", 0))

    if mode == "carousel":
        system = (
            "You are a production-quality assistant for IW61x/IW611 wireless module testing. "
            "Respond with PLAIN TEXT ONLY (no Markdown). Use this exact numbered-section template, "
            "and wrap every numeric value in <num>...</num>, every PASS/good status in <ok>...</ok>, "
            "every FAIL/bad status in <err>...</err>, and every WARNING-level status in <warn>...</warn>:\n\n"
            "1. General Information\n"
            "2. Test Statistics\n"
            "3. Yield Analysis\n"
            "4. Failure Analysis & Recommendations\n\n"
            "Keep it concise enough to read comfortably on a kiosk screen (roughly 25-35 lines)."
            if lang == "en"
            else (
                "你是 IW61x/IW611 無線模組產測的助理。只能輸出「純文字」(不要 Markdown)。"
                "請依照以下固定編號段落模板撰寫，並將所有數字包在 <num>...</num>、"
                "所有 PASS/良好狀態包在 <ok>...</ok>、所有 FAIL/不良狀態包在 <err>...</err>、"
                "所有 WARNING 等級狀態包在 <warn>...</warn> 標籤內：\n\n"
                "1. 基本資訊\n"
                "2. 測試統計\n"
                "3. 良率分析\n"
                "4. 失敗分析與建議\n\n"
                "內容請精簡到適合看板螢幕閱讀（約 25-35 行）。"
            )
        )
    else:
        system = (
            "You are a production-quality assistant for IW61x/IW611 wireless module testing. "
            "Respond with a well-structured Markdown report using headers, bold key metrics, and bullet lists. "
            "Cover: yield analysis, retry-rate analysis, and concrete failure-reduction recommendations."
            if lang == "en"
            else (
                "你是 IW61x/IW611 無線模組產測的助理。請用結構清楚的 Markdown 報告回覆，"
                "使用標題、粗體標示關鍵數字、以及條列清單。"
                "內容需涵蓋：良率分析、重測率分析、以及具體的失敗改善建議。"
            )
        )

    user = (
        f"Work Order: {wo}\n"
        f"Total units: {stats.get('total', 0)}\n"
        f"Passed: {stats.get('passed', 0)}\n"
        f"Failed: {stats.get('failed', 0)}\n"
        f"Stopped: {stats.get('stopped', 0)}\n"
        f"Yield: {stats.get('yield_pct', 0)}% (alert level: {yield_alert})\n"
        f"Retry rate: {stats.get('retry_rate', 0)}% (alert level: {retry_alert})\n"
        f"Top failure reasons: {fails_text}\n"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
