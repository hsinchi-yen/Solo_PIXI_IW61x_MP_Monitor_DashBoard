import asyncio
import json
import logging
import os
import threading
import time
import urllib.request
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse

import auth
import config
import errors
from db import init_schema, db, check_health
from errors import ApiError, Msg, not_found
from query_service import (
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
from scope import Scope, file_name
from ai_summary_helper import build_summary_messages

from upload_routes import router as upload_router
from admin_routes import auth_router, router as admin_router
from reference_routes import router as reference_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("iw.app")

app = FastAPI(title="IW Solo PIXI Essential API")
errors.install(app)

if config.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Tweak-Token"],
    )

app.include_router(auth_router)
app.include_router(upload_router)
app.include_router(admin_router)
app.include_router(reference_router)

STATIC_DIR = "/app/static" if os.path.exists("/app/static") else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static"))
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

LLM_API_BASE = os.environ.get("LLM_API_BASE", "http://10.20.30.23:8000/v1").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "Qwen3.6-35B-A3B-Q6_K")

LLM_STATUS_CACHE = {"status": "error", "connected": False}
AI_SUMMARY_CACHE_TTL_SEC = 600
AI_SUMMARY_CACHE: dict[str, dict] = {}
AI_SUMMARY_LOCKS: dict[str, threading.Lock] = {}
_AI_CACHE_LOCK = threading.Lock()


def _llm_request(path: str, payload: dict | None = None, timeout: int = 3):
    url = f"{LLM_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}
    if payload is None:
        req = urllib.request.Request(url, headers=headers)
    else:
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


async def update_llm_status_loop():
    while True:
        try:
            status, _ = await asyncio.to_thread(_llm_request, "/models", None, 3)
            LLM_STATUS_CACHE.update({"status": "ok", "connected": status == 200})
        except Exception:
            LLM_STATUS_CACHE.update({"status": "error", "connected": False})
        await asyncio.sleep(30)


@app.on_event("startup")
def startup_event():
    config.warn_on_defaults()
    try:
        init_schema()
    except Exception:
        log.exception("schema initialization failed")


@app.on_event("startup")
async def start_llm_status_loop():
    asyncio.create_task(update_llm_status_loop())

@app.get("/health")
def health_check():
    status = check_health()
    if status["status"] != "ok":
        raise HTTPException(status_code=503, detail="database unavailable")
    return status


@app.get("/api/filter-options")
def api_filter_options(scope: Scope = Depends()):
    with db() as conn:
        return fetch_filter_options(conn, **scope.kw())


@app.get("/api/summary")
def api_summary(scope: Scope = Depends()):
    with db() as conn:
        return build_summary_from_records(fetch_records(conn, **scope.kw()))


@app.get("/api/yield-trend")
def api_yield_trend(scope: Scope = Depends()):
    with db() as conn:
        return build_yield_trend_from_records(fetch_records(conn, **scope.kw()))


@app.get("/api/work-order-summary")
def api_work_order_summary(scope: Scope = Depends()):
    with db() as conn:
        return fetch_work_order_summary(conn, **scope.kw())


@app.get("/api/retries")
def api_retries(scope: Scope = Depends()):
    with db() as conn:
        return fetch_retry_summary(conn, **scope.kw())


@app.get("/api/fail-analysis")
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


@app.get("/api/fail-list")
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


@app.get("/api/bt-analysis")
def api_bt_analysis():
    with db() as conn:
        return fetch_metric_summary(conn, "BT")


@app.get("/api/wifi-analysis")
def api_wifi_analysis():
    with db() as conn:
        return fetch_metric_summary(conn, "WIFI")


@app.get("/api/advanced")
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


@app.get("/api/hourly-throughput")
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


@app.get("/api/llm-status")
def api_llm_status():
    return LLM_STATUS_CACHE


@app.get("/api/workorders/{wo}/ai-summary")
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
            _, res_data = _llm_request("/chat/completions", {"model": LLM_MODEL, "messages": messages}, timeout=180)
            content = res_data["choices"][0]["message"]["content"]
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


@app.get("/api/raw-log/{result_id}")
def api_raw_log(result_id: int, _user=Depends(auth.current_user)):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT source_file, raw_log FROM test_results WHERE id = %s", (result_id,))
        row = cur.fetchone()
    if not row:
        raise not_found(Msg("Record not found", "找不到這筆紀錄"))
    return {"source_file": file_name(row[0]), "raw_log": row[1]}

@app.get("/")
def serve_index():
    index_path = _static_path("solo_pixi_dashboard.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "solo_pixi_dashboard.html not found"}


@app.get("/{page_name}.html")
def serve_page(page_name: str):
    page_map = {
        "index": "overview",
        "work-orders": "workorders",
        "fail-list": "fails",
        "fail-analysis": "failanalysis",
        "bt-analysis": "bt",
        "wifi-analysis": "wifi",
        "advanced": "advanced",
        "upload": "upload",
        "admin": "dbtweak",
        "alignment": "dataalign",
    }
    if page_name not in page_map:
        raise HTTPException(status_code=404, detail="Page not found")
    return RedirectResponse(url=f"/?page={page_map[page_name]}", status_code=307)


def _static_path(name: str) -> str:
    return os.path.join(STATIC_DIR, name)


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
