"""DB Tweak: browse / delete test records, download raw logs, reparse (administrator only)."""
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse

import db
from errors import Msg, bad_request, not_found
from routers.analytics import raw_log, scope_params
from services import auth, reparse, stats
from services.filters import Scope, raw_where
from services.ingest import file_name

router = APIRouter(prefix="/api/tweak", tags=["tweak"])
jobs = APIRouter(prefix="/api/reparse", tags=["tweak"])


@router.get("/records")
def records(scope: Scope = Depends(scope_params), page: int = Query(1, ge=1),
            page_size: int = Query(50, ge=1, le=500), search: str = Query(None, max_length=64),
            _admin=Depends(auth.require_admin)):
    extra, s_params = [], []
    if search and search.strip():
        extra.append("(mac1 ILIKE %s OR mac2 ILIKE %s OR source_file ILIKE %s)")
        s_params = [f"%{search.strip()}%"] * 3
    where, params = raw_where(scope, extra)
    params += s_params
    with db.db() as conn:
        total = db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM test_results {where}", params)["n"]
        pages = max(1, -(-total // page_size))
        page = min(page, pages)
        items = db.fetch_all(conn, f"""
            SELECT t.*, r.upload_id
            FROM (SELECT id, mac1, mac2, mac1_source, work_order, start_time, test_duration_sec, result,
                         source_file, fail_step_name, fail_message, (raw_log IS NOT NULL) AS has_raw_log
                  FROM test_results {where}
                  ORDER BY start_time DESC, id DESC LIMIT %s OFFSET %s) t
            LEFT JOIN upload_runs r ON r.test_result_id = t.id
            ORDER BY t.start_time DESC, t.id DESC
        """, params + [page_size, (page - 1) * page_size])
    for item in items:
        item["source_file"] = file_name(item["source_file"])
    return {"items": items, "total": total, "page": page, "page_size": page_size, "pages": pages}


@router.delete("/delete-scope")
def delete_scope(request: Request, scope: Scope = Depends(scope_params), confirm: str = Query(""),
                 admin=Depends(auth.require_admin)):
    """Delete every record in the scope (needs a date range or a work order, and confirm=DELETE)."""
    if scope.is_empty:
        raise bad_request(Msg("Choose a date range or a work order before deleting in bulk.",
                              "請至少指定日期範圍或工單，才能批次刪除"), "scope_required")
    auth.require_delete_confirmation(confirm)
    where, params = raw_where(scope)
    with db.db() as conn:
        deleted = db.execute(conn, f"DELETE FROM test_results {where}", params)
        auth.audit(conn, request, admin["username"], "delete_scope", scope.work_order,
                   {**scope.as_params(), "deleted": deleted})
    stats.refresh(wait=True)
    return {"deleted": deleted, "scope": scope.describe()}


@router.delete("/records/{record_id}")
def delete_record(record_id: int, request: Request, confirm: str = Query(""), admin=Depends(auth.require_admin)):
    auth.require_delete_confirmation(confirm)
    with db.db() as conn:
        deleted = db.execute(conn, "DELETE FROM test_results WHERE id = %s", (record_id,))
        if not deleted:
            raise not_found(Msg("Record not found.", "找不到這筆紀錄"), "no_record")
        auth.audit(conn, request, admin["username"], "delete_record", str(record_id))
    stats.refresh(wait=True)
    return {"deleted": deleted, "id": record_id}


@router.get("/raw-log/{record_id}", response_class=PlainTextResponse)
def tweak_raw_log(record_id: int, user=Depends(auth.current_user)):
    """Same as /api/raw-log (token in the header, never in the URL)."""
    return raw_log(record_id, user)


@jobs.post("")
def start_reparse(request: Request, admin=Depends(auth.require_admin)):
    """Start a background reparse of every raw log; poll GET /api/reparse for progress."""
    job = reparse.start(admin["username"])
    with db.db() as conn:
        auth.audit(conn, request, admin["username"], "reparse", None, {"job_id": job["id"]})
    return job


@jobs.get("")
def reparse_status(_admin=Depends(auth.require_admin)):
    with db.db() as conn:
        return reparse.latest_job(conn) or {}


@jobs.post("/{job_id}/cancel")
def cancel_reparse(job_id: int, _admin=Depends(auth.require_admin)):
    if not reparse.cancel(job_id):
        raise not_found(Msg("This job is not running.", "這個工作沒有在執行"), "no_job")
    return {"ok": True}
