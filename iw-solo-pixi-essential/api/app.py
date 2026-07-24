import asyncio
import json
import os
import threading
import time
import urllib.request

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from db import init_schema, get_connection, check_health
from query_service import (
    build_ai_stats_from_records,
    build_fail_analysis_from_records,
    build_hourly_throughput_from_records,
    build_summary_from_records,
    build_yield_trend_from_records,
    fetch_filter_options,
    fetch_metric_summary,
    fetch_records,
    fetch_retry_summary,
    fetch_work_order_summary,
)
from ai_summary_helper import build_summary_messages

from upload_routes import router as upload_router
from admin_routes import router as admin_router
from reference_routes import router as reference_router

app = FastAPI(title="IW Solo PIXI Essential API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    try:
        init_schema()
        print("Schema initialized successfully.")
    except Exception as e:
        print(f"Error initializing schema: {e}")


@app.on_event("startup")
async def start_llm_status_loop():
    asyncio.create_task(update_llm_status_loop())

@app.get("/health")
def health_check():
    status = check_health()
    if status["status"] != "ok":
        raise HTTPException(status_code=503, detail=status)
    return status


@app.get("/api/filter-options")
def api_filter_options(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
):
    with get_connection() as conn:
        return fetch_filter_options(conn, work_order, year, month, week, day)


@app.get("/api/summary")
def api_summary(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
):
    with get_connection() as conn:
        return build_summary_from_records(
            fetch_records(conn, work_order=work_order, year=year, month=month, week=week, day=day)
        )


@app.get("/api/yield-trend")
def api_yield_trend(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
):
    with get_connection() as conn:
        return build_yield_trend_from_records(
            fetch_records(conn, work_order=work_order, year=year, month=month, week=week, day=day)
        )


@app.get("/api/work-order-summary")
def api_work_order_summary(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
):
    with get_connection() as conn:
        return fetch_work_order_summary(conn, work_order, year, month, week, day)


@app.get("/api/retries")
def api_retries(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
):
    with get_connection() as conn:
        return fetch_retry_summary(conn, work_order, year, month, week, day)


@app.get("/api/fail-analysis")
def api_fail_analysis(
    work_order: str | None = None,
    year: int | None = None,
    month: int | None = None,
    week: int | None = None,
    day: str | None = None,
):
    with get_connection() as conn:
        analysis = build_fail_analysis_from_records(
            fetch_records(conn, work_order=work_order, result=None, year=year, month=month, week=week, day=day)
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
def api_fail_list(work_order: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500)):
    with get_connection() as conn:
        records = fetch_records(conn, work_order=work_order, limit=10000)
    filtered = [row for row in records if row.result in {"FAIL", "STOP"}]
    start = (page - 1) * page_size
    page_rows = filtered[start:start + page_size]
    return {
        "total": len(filtered),
        "page": page,
        "page_size": page_size,
        "rows": [_record_public(row) for row in page_rows],
    }


@app.get("/api/bt-analysis")
def api_bt_analysis():
    with get_connection() as conn:
        return fetch_metric_summary(conn, "BT")


@app.get("/api/wifi-analysis")
def api_wifi_analysis():
    with get_connection() as conn:
        return fetch_metric_summary(conn, "WIFI")


@app.get("/api/advanced")
def api_advanced(work_order: str | None = None):
    with get_connection() as conn:
        records = fetch_records(conn, work_order=work_order)
        retry_rows = fetch_retry_summary(conn)
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
def api_hourly_throughput(work_order: str | None = None, hours: int = Query(24, ge=1, le=168)):
    with get_connection() as conn:
        records = fetch_records(conn, work_order=work_order, limit=20000)
    return build_hourly_throughput_from_records(records, hours=hours)


@app.get("/api/llm-status")
def api_llm_status():
    return LLM_STATUS_CACHE


@app.get("/api/workorders/{wo}/ai-summary")
def api_ai_summary(wo: str, lang: str = "zh", mode: str = "normal"):
    cache_key = f"{wo}_{lang}_{mode}"
    with _AI_CACHE_LOCK:
        entry = AI_SUMMARY_CACHE.get(cache_key)
        if entry and time.time() - entry["timestamp"] < AI_SUMMARY_CACHE_TTL_SEC:
            return {"summary": entry["summary"]}
        lock = AI_SUMMARY_LOCKS.setdefault(cache_key, threading.Lock())

    with lock:
        entry = AI_SUMMARY_CACHE.get(cache_key)
        if entry and time.time() - entry["timestamp"] < AI_SUMMARY_CACHE_TTL_SEC:
            return {"summary": entry["summary"]}

        with get_connection() as conn:
            records = fetch_records(conn, work_order=wo)
        if not records:
            raise HTTPException(status_code=404, detail="Work order not found or has no data")

        stats = build_ai_stats_from_records(records)
        fails_text = ", ".join(f"{row['step']}({row['count']} units)" for row in stats["top_fails"]) or "No specific failures"
        messages = build_summary_messages(stats, fails_text, wo, lang, mode)

        try:
            _, res_data = _llm_request("/chat/completions", {"model": LLM_MODEL, "messages": messages}, timeout=180)
            content = res_data["choices"][0]["message"]["content"]
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"LLM request failed: {e}")

        with _AI_CACHE_LOCK:
            AI_SUMMARY_CACHE[cache_key] = {"summary": content, "timestamp": time.time()}
        return {"summary": content}


@app.get("/api/raw-log/{result_id}")
def api_raw_log(result_id: int):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT source_file, raw_log FROM test_results WHERE id = %s", (result_id,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"source_file": row[0], "raw_log": row[1]}

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
        "source_file": row.source_file,
    }
