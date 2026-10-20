"""Health, LLM status, statistics refresh, page metadata."""
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import config
import db
from errors import ApiError, Msg
from services import llm, stats

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    """DB round-trip, LLM status, view freshness, version. 503 when the DB is unreachable."""
    t0 = time.perf_counter()
    try:
        with db.db() as conn:
            count = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM test_results")["n"]
            refreshed = stats.refreshed_at(conn)
    except Exception:
        return JSONResponse({"status": "error", "db": "unavailable", "version": config.APP_VERSION}, status_code=503)
    return {
        "status": "ok",
        "db": "connected",
        "version": config.APP_VERSION,
        "test_results_count": count,
        "db_ms": round((time.perf_counter() - t0) * 1000, 1),
        "stats_refreshed_at": refreshed,
        "llm": {k: v for k, v in llm.status().items() if k in ("enabled", "connected", "model")},
    }


@router.get("/api/llm-status")
def llm_status():
    s = llm.status()
    return {"enabled": s["enabled"], "connected": s["connected"], "status": "ok" if s["connected"] else "error",
            "model": s["model"], "last_ok_at": s["last_ok_at"]}


_last_manual_refresh = {"at": 0.0}


@router.post("/api/stats/refresh")
def refresh_stats():
    """"Refresh now": recompute the statistics (public like the dashboard, at most every 10 seconds)."""
    if time.time() - _last_manual_refresh["at"] < 10:
        raise ApiError(429, Msg("Just refreshed. Please try again in a few seconds.", "剛剛才更新過，請稍候再試"),
                       "too_soon")
    _last_manual_refresh["at"] = time.time()
    return {"stats_refreshed_at": stats.refresh(wait=True)}


@router.get("/api/meta")
def meta():
    """Version, shared thresholds and data freshness for the page header."""
    with db.db() as conn:
        refreshed = stats.refreshed_at(conn)
    return {
        "version": config.APP_VERSION,
        "thresholds": {"yield_good": config.YIELD_GOOD, "yield_warn": config.YIELD_WARN,
                       "retry_good": config.RETRY_GOOD, "retry_warn": config.RETRY_WARN,
                       "cpk_good": config.CPK_GOOD, "cpk_warn": config.CPK_WARN},
        "stats_refreshed_at": refreshed,
        "stats_refresh_sec": config.STATS_REFRESH_SEC,
        "report_daily_at": config.REPORT_DAILY_AT,
        "llm": llm.status(),
    }
