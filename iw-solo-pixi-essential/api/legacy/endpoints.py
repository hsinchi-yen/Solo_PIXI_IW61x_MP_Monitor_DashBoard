"""The pre-2.0 dashboard API, kept unchanged for /legacy.html (served under /api/legacy)."""
import logging
import threading
import time
from typing import Literal

from fastapi import APIRouter, Depends, Query

from db import db
from errors import ApiError, Msg, not_found
from legacy.ai_summary_helper import build_summary_messages
from legacy.query_service import (
    build_ai_stats_from_records,
    build_fail_analysis_from_records,
    build_hourly_throughput_from_records,
    build_summary_from_records,
    build_yield_trend_from_records,
    count_records,
    fetch_filter_options,
    fetch_metric_summary,
    fetch_records,
    fetch_retry_summary,
    fetch_work_order_summary,
)
from legacy.scope import Scope, file_name
from services import auth, llm

log = logging.getLogger("iw.legacy")

router = APIRouter()

AI_SUMMARY_CACHE_TTL_SEC = 600
AI_SUMMARY_CACHE: dict[str, dict] = {}
AI_SUMMARY_LOCKS: dict[str, threading.Lock] = {}
_AI_CACHE_LOCK = threading.Lock()


@router.get("/filter-options")
def api_filter_options(scope: Scope = Depends()):
    with db() as conn:
        return fetch_filter_options(conn, **scope.kw())


@router.get("/summary")
def api_summary(scope: Scope = Depends()):
    with db() as conn:
        return build_summary_from_records(fetch_records(conn, **scope.kw()))


@router.get("/yield-trend")
def api_yield_trend(scope: Scope = Depends()):
    with db() as conn:
        return build_yield_trend_from_records(fetch_records(conn, **scope.kw()))


@router.get("/work-order-summary")
def api_work_order_summary(scope: Scope = Depends()):
    with db() as conn:
        return fetch_work_order_summary(conn, **scope.kw())


@router.get("/retries")
def api_retries(scope: Scope = Depends()):
    with db() as conn:
        return fetch_retry_summary(conn, **scope.kw())


@router.get("/fail-analysis")
def api_fail_analysis(scope: Scope = Depends()):
    with db() as conn:
        analysis = build_fail_analysis_from_records(
            fetch_records(conn, results=("FAIL", "STOP"), **scope.kw())
        )
    return [
        {
            "fail_step": row["step"],
            "fail_step_name": row["step"],
            "fail_error_code": None,
            "count": row["count"],
        }
        for row in analysis["fail_steps"]
    ]


@router.get("/fail-list")
def api_fail_list(scope: Scope = Depends(), page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500)):
    results = ("FAIL", "STOP")
    with db() as conn:
        total = count_records(conn, results=results, **scope.kw())
        rows = fetch_records(conn, results=results, limit=page_size, offset=(page - 1) * page_size, **scope.kw())
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": [_record_public(row) for row in rows],
    }


@router.get("/bt-analysis")
def api_bt_analysis():
    with db() as conn:
        return fetch_metric_summary(conn, "BT")


@router.get("/wifi-analysis")
def api_wifi_analysis():
    with db() as conn:
        return fetch_metric_summary(conn, "WIFI")


@router.get("/advanced")
def api_advanced(work_order: str | None = Query(None, max_length=64)):
    with db() as conn:
        records = fetch_records(conn, work_order=work_order)
        retry_rows = fetch_retry_summary(conn, work_order=work_order)
    durations = [row.test_duration_sec for row in records if row.test_duration_sec is not None]
    return {
        "duration": {
            "count": len(durations),
            "avg_sec": round(sum(durations) / len(durations), 3) if durations else 0,
            "min_sec": min(durations) if durations else None,
            "max_sec": max(durations) if durations else None,
        },
        "retries": retry_rows,
        "fail_analysis": build_fail_analysis_from_records(records),
    }


@router.get("/hourly-throughput")
def api_hourly_throughput(work_order: str | None = Query(None, max_length=64), hours: int = Query(24, ge=1, le=168)):
    with db() as conn:
        latest = _latest_start(conn, work_order)
        if latest is None:
            return build_hourly_throughput_from_records([], hours=hours)
        records = [
            row for row in fetch_records(conn, work_order=work_order)
            if row.start_time and (latest - row.start_time).total_seconds() < (hours + 1) * 3600
        ]
    return build_hourly_throughput_from_records(records, hours=hours)


@router.get("/llm-status")
def api_llm_status():
    connected = llm.status()["connected"]
    return {"status": "ok" if connected else "error", "connected": connected}


@router.get("/workorders/{wo}/ai-summary")
def api_ai_summary(wo: str, lang: Literal["zh", "en"] = "zh", mode: Literal["normal", "carousel"] = "normal"):
    with db() as conn:
        fingerprint = _data_fingerprint(conn, wo)
    # The fingerprint changes on any upload or delete of this work order, so a stale
    # report is never served — in either uvicorn worker.
    cache_key = f"{wo}_{lang}_{mode}_{fingerprint}"
    with _AI_CACHE_LOCK:
        entry = AI_SUMMARY_CACHE.get(cache_key)
        if entry and time.time() - entry["timestamp"] < AI_SUMMARY_CACHE_TTL_SEC:
            return {"summary": entry["summary"]}
        lock = AI_SUMMARY_LOCKS.setdefault(cache_key, threading.Lock())

    with lock:
        entry = AI_SUMMARY_CACHE.get(cache_key)
        if entry and time.time() - entry["timestamp"] < AI_SUMMARY_CACHE_TTL_SEC:
            return {"summary": entry["summary"]}

        with db() as conn:
            records = fetch_records(conn, work_order=wo)
        if not records:
            raise not_found(Msg("Work order not found or has no data", "找不到這張工單或沒有資料"))

        stats = build_ai_stats_from_records(records)
        fails_text = ", ".join(f"{row['step']}({row['count']} failed attempts)" for row in stats["top_fails"]) or "No specific failures"
        messages = build_summary_messages(stats, fails_text, wo, lang, mode)

        try:
            content = llm.complete(messages, timeout=180)
        except Exception:
            log.exception("LLM request failed for %s", wo)
            raise ApiError(502, Msg("The AI service did not answer. Please try again later.",
                                    "AI 服務沒有回應，請稍後再試"), "llm_unavailable")

        with _AI_CACHE_LOCK:
            now = time.time()
            for key in [k for k, v in AI_SUMMARY_CACHE.items() if now - v["timestamp"] >= AI_SUMMARY_CACHE_TTL_SEC]:
                AI_SUMMARY_CACHE.pop(key, None)
                AI_SUMMARY_LOCKS.pop(key, None)
            AI_SUMMARY_CACHE[cache_key] = {"summary": content, "timestamp": now}
        return {"summary": content}


@router.get("/raw-log/{result_id}")
def api_raw_log(result_id: int, _user=Depends(auth.current_user)):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT source_file, raw_log FROM test_results WHERE id = %s", (result_id,))
        row = cur.fetchone()
    if not row:
        raise not_found(Msg("Record not found", "找不到這筆紀錄"))
    return {"source_file": file_name(row[0]), "raw_log": row[1]}

def _latest_start(conn, work_order):
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(start_time) FROM test_results WHERE (%s::text IS NULL OR work_order = %s)",
                    (work_order, work_order))
        return cur.fetchone()[0]


def _data_fingerprint(conn, work_order: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM test_results WHERE work_order = %s", (work_order,))
        count, max_id = cur.fetchone()
    return f"{count}-{max_id}"


def _record_public(row) -> dict:
    return {
        "id": row.id,
        "work_order": row.work_order,
        "product": row.product,
        "mac1": row.mac1,
        "mac2": row.mac2,
        "mac1_source": row.mac1_source,
        "start_time": row.start_time.isoformat() if row.start_time else None,
        "test_duration_sec": row.test_duration_sec,
        "result": row.result,
        "fail_step_num": row.fail_step_num,
        "fail_step_name": row.fail_step_name,
        "fail_message": row.fail_message,
        "fail_category": row.fail_category,
        "source_file": file_name(row.source_file),
    }
