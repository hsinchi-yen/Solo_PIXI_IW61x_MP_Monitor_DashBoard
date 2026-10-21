"""Dashboard data (public, read-only), planned quantities, raw-log download."""
from typing import Dict, Literal, Optional

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import PlainTextResponse

import db
from errors import Msg, bad_request, not_found
from services import auth, measurements, stats
from services.filters import Scope, parse_scope

router = APIRouter(prefix="/api", tags=["analytics"])


def scope_params(
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD, inclusive"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD, inclusive"),
    work_order: Optional[str] = Query(None, max_length=64),
    year: Optional[int] = Query(None, ge=2000, le=2100, description="older links"),
    month: Optional[int] = Query(None, ge=1, le=12, description="older links, needs year"),
    week: Optional[int] = Query(None, ge=1, le=53, description="older links, ISO week, needs year"),
    day: Optional[str] = Query(None, description="older links, YYYY-MM-DD"),
) -> Scope:
    return parse_scope(date_from, date_to, work_order, year, month, week, day)


def _scope_info(scope: Scope):
    return {"label": scope.describe("en"), "label_zh": scope.describe("zh"), "params": scope.as_params()}


@router.get("/filter-options")
def filter_options():
    """Work orders by most recent activity and the overall date range (for the filter bar)."""
    with db.db() as conn:
        return stats.cached(conn, ("filter_options",), lambda: stats.filter_options(conn))


@router.get("/overview")
def overview(scope: Scope = Depends(scope_params), latest: bool = Query(False, description="use the latest work order")):
    """Everything the Dashboard page shows, in one request (decisions Q1–Q3; layout of pixi-dashboard 3.2).

    Numbers, trend and hourly output follow the selected scope. The two monthly charts show the
    whole year of that scope across all work orders, for context."""
    with db.db() as conn:
        if latest and scope.is_empty:
            wo = stats.latest_work_order(conn)
            scope = Scope(work_order=wo) if wo else scope
        return stats.cached(conn, ("overview", scope), lambda: _overview(conn, scope))


def _overview(conn, scope):
    year = stats.year_of_latest(conn, scope)
    align = stats.alignment(conn, scope)
    return {
        "scope": _scope_info(scope),
        "summary": stats.summary(conn, scope),
        "aligned": {"yield_pct": align["totals"]["aligned_yield"], "gap": align["totals"]["gap"],
                    "has_target": any(r["target_total"] is not None for r in align["items"])},
        "trend": stats.trend(conn, scope),
        "hourly": stats.hourly_output(conn, scope),
        "monthly": stats.monthly(conn, Scope(), year) if year else {"year": None, "months": []},
        "data_as_of": stats.refreshed_at(conn),
    }


@router.get("/summary")
def summary(scope: Scope = Depends(scope_params)):
    with db.db() as conn:
        return stats.cached(conn, ("summary", scope), lambda: stats.summary(conn, scope))


@router.get("/trend")
def trend(scope: Scope = Depends(scope_params)):
    with db.db() as conn:
        return stats.cached(conn, ("trend", scope), lambda: stats.trend(conn, scope))


@router.get("/hourly")
def hourly(scope: Scope = Depends(scope_params), hours: int = Query(24, ge=1, le=168)):
    with db.db() as conn:
        return stats.hourly(conn, scope, hours)


@router.get("/hourly-output")
def hourly_output(scope: Scope = Depends(scope_params)):
    """Test runs per work hour (config.WORK_HOURS) on the latest day of the selected or latest work order."""
    with db.db() as conn:
        return stats.cached(conn, ("hourly_output", scope), lambda: stats.hourly_output(conn, scope))


@router.get("/monthly-count")
def monthly_count(scope: Scope = Depends(scope_params), year: Optional[int] = Query(None, ge=2000, le=2100)):
    with db.db() as conn:
        y = year or stats.year_of_latest(conn, scope)
        return stats.monthly(conn, scope, y) if y else {"year": None, "months": []}


@router.get("/work-order-summary")
def work_order_summary(scope: Scope = Depends(scope_params)):
    with db.db() as conn:
        return stats.cached(conn, ("work_order_summary", scope), lambda: stats.work_order_summary(conn, scope))


@router.get("/retries")
def retries(scope: Scope = Depends(scope_params), limit: int = Query(200, ge=1, le=10000)):
    with db.db() as conn:
        return stats.cached(conn, ("retries", scope, limit), lambda: stats.retries(conn, scope, limit))


@router.get("/fail-analysis")
def fail_analysis(scope: Scope = Depends(scope_params), limit: int = Query(20, ge=1, le=100)):
    with db.db() as conn:
        return {"top_steps": stats.top_fail_steps(conn, scope, 10), **stats.fail_analysis(conn, scope, limit)}


@router.get("/fails")
def fails(scope: Scope = Depends(scope_params), page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
          search: Optional[str] = Query(None, max_length=64), result: Optional[Literal["FAIL", "STOP"]] = None):
    """FAIL / STOP test records, newest first, paged on the server."""
    with db.db() as conn:
        out = stats.fails(conn, scope, page, page_size, (search or "").strip() or None, result)
    from services.ingest import file_name
    for row in out["items"]:
        row["source_file"] = file_name(row["source_file"])
    return out


@router.get("/measurements")
def measurement_data(scope: Scope = Depends(scope_params)):
    """Measurement page: BT / Wi-Fi tables, worst test items, test time and calibration (a few KB)."""
    with db.db() as conn:
        return stats.cached(conn, ("measurements", scope), lambda: _measurements(conn, scope))


def _measurements(conn, scope):
    items = measurements.spec_items(conn, scope)
    retry = stats.retries(conn, scope, limit=20)
    return {
        "scope": _scope_info(scope),
        "spec": {"status": measurements.status_counts(items), "worst": items[:10]},
        "bt": measurements.bt(conn, scope),
        "wifi": measurements.wifi(conn, scope),
        "other": {**measurements.other(conn, scope),
                  "retry": {k: retry[k] for k in ("units", "high", "medium", "low")}},
        "top_retries": retry["items"],
    }


@router.get("/measurements/items")
def measurement_items(scope: Scope = Depends(scope_params),
                      technology: Optional[Literal["WIFI", "BT", "CALIBRATION", "OTHER"]] = None,
                      band: Optional[str] = Query(None, max_length=16), standard: Optional[str] = Query(None, max_length=16),
                      rate: Optional[str] = Query(None, max_length=16), search: Optional[str] = Query(None, max_length=64),
                      all_items: bool = Query(False, description="include items without limits"),
                      limit: int = Query(200, ge=1, le=2000)):
    """Every test item in scope with mean, sd, Cpk and margin to spec, worst first."""
    with db.db() as conn:
        items = stats.cached(conn, ("items", scope, technology, band, standard, rate, search, all_items),
                             lambda: measurements.spec_items(conn, scope, technology, band, standard, rate, search,
                                                             not all_items))
    return {"total": len(items), "status": measurements.status_counts(items), "items": items[:limit]}


@router.get("/measurements/histogram")
def measurement_histogram(scope: Scope = Depends(scope_params),
                          technology: str = Query(..., max_length=16), metric_name: str = Query(..., max_length=64),
                          direction: Optional[str] = Query(None, max_length=8),
                          standard: Optional[str] = Query(None, max_length=16),
                          band: Optional[str] = Query(None, max_length=16),
                          frequency_mhz: Optional[int] = Query(None, ge=0, le=10000),
                          bandwidth: Optional[str] = Query(None, max_length=16),
                          rate: Optional[str] = Query(None, max_length=16),
                          antenna: Optional[str] = Query(None, max_length=16)):
    key = {"technology": technology, "metric_name": metric_name, "direction": direction, "standard": standard,
           "band": band, "frequency_mhz": frequency_mhz, "bandwidth": bandwidth, "rate": rate, "antenna": antenna}
    with db.db() as conn:
        return stats.cached(conn, ("hist", scope, tuple(sorted((k, str(v)) for k, v in key.items()))),
                            lambda: measurements.histogram(conn, scope, key))


@router.get("/alignment")
def alignment(scope: Scope = Depends(scope_params)):
    """Data Alignment: units vs planned quantity per work order, and the aligned yield."""
    with db.db() as conn:
        return stats.cached(conn, ("alignment", scope), lambda: stats.alignment(conn, scope))


@router.get("/alignment-targets")
def get_alignment_targets() -> Dict[str, int]:
    with db.db() as conn:
        rows = db.fetch_all(conn, "SELECT work_order, target_total FROM alignment_targets")
    return {r["work_order"]: r["target_total"] for r in rows}


@router.post("/alignment-targets")
def set_alignment_targets(request: Request,
                          targets: Dict[str, Optional[int]] = Body(..., examples=[{"5101-260715003": 200}]),
                          admin=Depends(auth.require_admin)):
    """Set planned quantities ({work order: number}; null clears). Administrator only."""
    if len(targets) > 500:
        raise bad_request(Msg("At most 500 work orders at a time.", "一次最多 500 張工單"), "too_many")
    with db.db() as conn:
        for wo, target in targets.items():
            wo = (wo or "").strip()
            if not wo or len(wo) > 64:
                raise bad_request(Msg("Invalid work order.", "工單號不正確"), "bad_work_order")
            if target is None:
                db.execute(conn, "DELETE FROM alignment_targets WHERE work_order = %s", (wo,))
            elif not 0 < target <= 10_000_000:
                raise bad_request(Msg(f"The planned quantity for {wo} must be a whole number from 1 to 10,000,000.",
                                      f"{wo} 的計畫數必須是 1 到 10,000,000 的整數"), "bad_target")
            else:
                db.execute(conn, """
                    INSERT INTO alignment_targets (work_order, target_total, updated_at) VALUES (%s, %s, NOW())
                    ON CONFLICT (work_order) DO UPDATE SET target_total = EXCLUDED.target_total, updated_at = NOW()
                """, (wo, target))
        auth.audit(conn, request, admin["username"], "set_alignment_targets", None, targets)
    return {"ok": True, "saved": len(targets)}


@router.get("/raw-log/{record_id}", response_class=PlainTextResponse)
def raw_log(record_id: int, _user=Depends(auth.current_user)):
    """Download one test record's original log. Any signed-in account."""
    from services.ingest import file_name
    with db.db() as conn:
        row = db.fetch_one(conn, "SELECT raw_log, source_file FROM test_results WHERE id = %s", (record_id,))
    if not row or not row["raw_log"]:
        raise not_found(Msg("This record has no raw log.", "這筆紀錄沒有原始 log"), "no_raw_log")
    name = file_name(row["source_file"]) or f"record_{record_id}.txt"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
    return PlainTextResponse(row["raw_log"], headers={"Content-Disposition": f'attachment; filename="{safe}"'})
