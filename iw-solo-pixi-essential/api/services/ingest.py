"""Web Log Uploader back end: temp files, the upload rules and the ingest thread.

Flow: analyze (facts, no test_results writes) → validate / chat (rules R1–R9) →
commit (NDJSON progress stream from a background thread) → history / undo.

Accepted files (decision Q20): a work-order zip with each station's Log_all.txt (split here,
exactly like the desktop splitter), already-split run files (YYYYMMDD_HHMMSS_MAC1_MAC2_RESULT.txt)
in a zip or as plain .txt, or a single Log_all.txt.
"""
import json
import logging
import os
import queue
import shutil
import time
import zipfile
from collections import Counter
from pathlib import PureWindowsPath

import psycopg2.extras

import config
import db
from errors import ApiError, Msg
from ingestion.postgres import PostgresRunRepository
from ingestion.splitter import infer_work_order, is_valid_work_order, iter_upload_runs, zip_entries
from parsers.iw61x import parse_iw61x_content
from services import stats

log = logging.getLogger("iw.upload")

EXPECTED_PRODUCT = "IW611"


def file_name(source_file):
    """Only the file name of an uploaded log; the uploading PC's folders are not shown."""
    if not source_file:
        return source_file
    return PureWindowsPath(source_file).name


# ─── Temp files ──────────────────────────────────────────────────────────────

def valid_upload_id(upload_id):
    return isinstance(upload_id, str) and len(upload_id) == 32 and all(c in "0123456789abcdef" for c in upload_id)


def upload_dir(upload_id):
    return os.path.join(config.UPLOAD_TMP_DIR, upload_id)


def remove_upload_files(upload_id):
    shutil.rmtree(upload_dir(upload_id), ignore_errors=True)


def cleanup_stale_uploads():
    if not os.path.isdir(config.UPLOAD_TMP_DIR):
        return
    cutoff = time.time() - config.UPLOAD_TTL_SEC
    for name in os.listdir(config.UPLOAD_TMP_DIR):
        path = os.path.join(config.UPLOAD_TMP_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def _stored_entries(upload_id):
    """(path, loader) for every stored upload file; zips are opened and listed."""
    base = upload_dir(upload_id)
    entries, handles = [], []
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        if name.lower().endswith(".zip"):
            zf = zipfile.ZipFile(path)
            handles.append(zf)
            entries.extend(zip_entries(zf))
        elif name.lower().endswith(".txt"):
            # Stored as "0000_<original name>"; the original name carries the run metadata.
            entries.append((name[5:], (lambda p=path: open(p, "rb").read())))
    return entries, handles


def iter_runs(upload_id):
    """Yield each run of a stored upload once (duplicates inside the upload are dropped)."""
    entries, handles = _stored_entries(upload_id)
    try:
        seen = set()
        for item in iter_upload_runs(entries):
            if "content" in item:
                key = hash(item["content"])
                if key in seen:
                    continue
                seen.add(key)
            yield item
    finally:
        for zf in handles:
            zf.close()


# ─── Analyze ─────────────────────────────────────────────────────────────────

def analyze(upload_id, file_names):
    """Split and parse every run (no DB writes) and collect the facts the user confirms."""
    stations = {}
    invalid_items, hashes = [], []
    mac1s, products, flows = set(), Counter(), Counter()
    starts = []
    totals = Counter()
    duplicates_in_upload = 0
    seen_hashes = set()
    for item in iter_runs(upload_id):
        st = stations.setdefault(item["station"], {"station": item["station"], "records": 0, "pass": 0, "fail": 0,
                                                   "stop": 0, "unknown_mac": 0, "invalid": 0})
        if "invalid" in item:
            st["invalid"] += 1
            invalid_items.append({"station": item["station"], "source": file_name(item["source"]),
                                  "reason": item["invalid"]})
            continue
        try:
            parsed = parse_iw61x_content(item["content"], item["filename"])
        except Exception as exc:
            st["invalid"] += 1
            invalid_items.append({"station": item["station"], "source": item["filename"], "reason": str(exc)[:200]})
            continue
        if parsed.file_hash in seen_hashes:
            duplicates_in_upload += 1
            continue
        seen_hashes.add(parsed.file_hash)
        hashes.append(parsed.file_hash)
        st["records"] += 1
        st[parsed.result.lower()] += 1
        totals[parsed.result] += 1
        if parsed.mac1_source == "unknown" or not parsed.mac1:
            st["unknown_mac"] += 1
        else:
            mac1s.add(parsed.mac1)
        products[parsed.product or "?"] += 1
        flows[parsed.flow_file or "?"] += 1
        if parsed.start_time:
            starts.append(parsed.start_time)
    if not hashes and not invalid_items:
        raise ApiError(400, Msg("No IW611 test logs found. Upload a work-order zip with each station's Log_all.txt, "
                                "or split run files (YYYYMMDD_HHMMSS_MAC1_MAC2_RESULT.txt).",
                                "找不到 IW611 測試 log。請上傳含各站 Log_all.txt 的工單 zip，"
                                "或已切好的單筆檔案（YYYYMMDD_HHMMSS_MAC1_MAC2_RESULT.txt）。"), "no_logs")
    names = list(file_names)
    for fname in file_names:
        if fname.lower().endswith(".zip"):
            with zipfile.ZipFile(os.path.join(upload_dir(upload_id), fname)) as zf:
                names += sorted({i.filename.split("/")[0] for i in zf.infolist()})
    facts = {
        "file_names": list(file_names),
        "inferred_wo": infer_work_order(*names),
        "stations": sorted(stations.values(), key=lambda s: s["station"]),
        "total_records": len(hashes),
        "pass": totals.get("PASS", 0),
        "fail": totals.get("FAIL", 0),
        "stop": totals.get("STOP", 0),
        "unknown_mac": sum(s["unknown_mac"] for s in stations.values()),
        "invalid": len(invalid_items),
        "invalid_items": invalid_items[:100],
        "duplicates_in_upload": duplicates_in_upload,
        "unique_mac1": len(mac1s),
        "products": dict(products),
        "flow_files": dict(flows.most_common(5)),
        "first_test": min(starts).isoformat(sep=" ") if starts else None,
        "last_test": max(starts).isoformat(sep=" ") if starts else None,
    }
    return facts, hashes


def existing_by_wo(conn, hashes):
    """{work_order: count} of hashes already in test_results."""
    if not hashes:
        return {}
    rows = db.fetch_all(conn, """
        SELECT work_order AS wo, COUNT(*) AS n FROM test_results WHERE file_hash = ANY(%s) GROUP BY 1
    """, (hashes,))
    return {r["wo"]: int(r["n"]) for r in rows}


def current_target(conn, work_order):
    if not is_valid_work_order(work_order or ""):
        return None
    row = db.fetch_one(conn, "SELECT target_total FROM alignment_targets WHERE work_order = %s", (work_order,))
    return row["target_total"] if row else None


# ─── Rules (shared by the form and the AI assistant; the rules decide, never the LLM) ──

def _issue(code, level, message_en, message_zh):
    return {"code": code, "level": level, "message_en": message_en, "message": message_zh}


def parse_quantity(value):
    """'4,000' / 4000 / ' 4000 ' → 4000; anything else → None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value if value is not None else "").replace(",", "").strip()
    return int(text) if text.lstrip("-").isdigit() else None


def validate_upload(facts, work_order, planned_qty, existing=None, target=None):
    """[{code, level, message_en, message}] — error: must fix; warn: must acknowledge; info: shown only."""
    issues = []
    existing = existing or {}
    work_order = (work_order or "").strip()
    wo_ok = is_valid_work_order(work_order)

    if not work_order:
        issues.append(_issue("R1", "error", "Please enter the work order.", "請輸入工單號。"))
    elif not wo_ok:
        issues.append(_issue("R1", "error",
                             f'Work order "{work_order}" has the wrong format: 4 digits, a hyphen and 8 or more digits, '
                             "for example 5101-260715003.",
                             f"工單號「{work_order}」格式不正確，應為 4 碼-8 碼以上數字，例如 5101-260715003。"))

    qty = parse_quantity(planned_qty)
    if planned_qty in (None, ""):
        issues.append(_issue("R2", "error", "Please enter the planned quantity.", "請輸入工單計畫生產數。"))
    elif qty is None or qty <= 0:
        issues.append(_issue("R2", "error", f'Planned quantity "{planned_qty}" must be a positive whole number.',
                             f"計畫生產數「{planned_qty}」必須是正整數。"))

    inferred = facts.get("inferred_wo")
    if wo_ok and inferred and inferred != work_order:
        issues.append(_issue("R3", "warn", f"The work order {work_order} differs from {inferred} found in the upload.",
                             f"輸入的工單號 {work_order} 與上傳檔案推斷的 {inferred} 不一致。"))

    other = {wo: n for wo, n in existing.items() if wo != work_order}
    if wo_ok and other:
        ordered = sorted(other.items(), key=lambda kv: str(kv[0]))
        detail_en = ", ".join(f"{wo}: {n:,} runs" for wo, n in ordered)
        detail_zh = "、".join(f"{wo} {n:,} 筆" for wo, n in ordered)
        issues.append(_issue("R4", "warn",
                             f"Some runs already belong to other work orders ({detail_en}); they will be skipped, "
                             f"not moved to {work_order}.",
                             f"有資料已存在於其他工單（{detail_zh}），這些資料將略過，不會改掛到 {work_order}。"))

    if qty and qty > 0:
        unique = facts.get("unique_mac1", 0)
        diff = qty - unique
        issues.append(_issue("R5", "info",
                             f"Planned {qty:,} units; the logs have {unique:,} unique MAC1, a difference of {diff:+,} "
                             "(for reference).",
                             f"計畫 {qty:,} 台，log 實測不重複 MAC1 {unique:,} 台，差 {diff:+,} 台（僅供參考）。"))

    same = existing.get(work_order, 0) if wo_ok else 0
    if same:
        issues.append(_issue("R6", "info", f"{same:,} runs already exist in {work_order} and will be skipped (re-upload).",
                             f"{same:,} 筆已存在於工單 {work_order}，將略過（視同重傳）。"))

    if wo_ok and qty and qty > 0:
        if target is None:
            issues.append(_issue("R7", "info", f"After the upload, the Data Alignment target will be set to {qty:,}.",
                                 f"上傳成功後，Data Alignment 目標數將設為 {qty:,}。"))
        elif int(target) != qty:
            issues.append(_issue("R7", "info", f"The Data Alignment target will change from {int(target):,} to {qty:,}.",
                                 f"Data Alignment 目標數將由 {int(target):,} 更新為 {qty:,}。"))

    if facts.get("invalid") or facts.get("duplicates_in_upload"):
        parts_en, parts_zh = [], []
        if facts.get("invalid"):
            parts_en.append(f"{facts['invalid']} unreadable run blocks")
            parts_zh.append(f"{facts['invalid']} 筆無法解析的段落")
        if facts.get("duplicates_in_upload"):
            parts_en.append(f"{facts['duplicates_in_upload']} runs that appear twice in the upload")
            parts_zh.append(f"{facts['duplicates_in_upload']} 筆在上傳檔案中重複出現的資料")
        issues.append(_issue("R8", "info", " and ".join(parts_en).capitalize() + " will be skipped (see details).",
                             "、".join(parts_zh) + "將略過（見明細）。"))

    other_products = {p: n for p, n in (facts.get("products") or {}).items() if p != EXPECTED_PRODUCT}
    if other_products:
        detail = ", ".join(f"{p}: {n}" for p, n in other_products.items())
        issues.append(_issue("R9", "error",
                             f"Some logs are not {EXPECTED_PRODUCT} ({detail}). This dashboard only takes {EXPECTED_PRODUCT} logs.",
                             f"部分 log 不是 {EXPECTED_PRODUCT}（{detail}），本系統只接受 {EXPECTED_PRODUCT} 的 log。"))
    return issues


def has_errors(issues):
    return any(i["level"] == "error" for i in issues)


def has_warnings(issues):
    return any(i["level"] == "warn" for i in issues)


def validation_payload(issues):
    return {"issues": issues, "can_upload": not has_errors(issues), "needs_ack": has_warnings(issues)}


# ─── upload_log rows ─────────────────────────────────────────────────────────

def fetch_upload(conn, upload_id):
    if not valid_upload_id(upload_id):
        return None
    return db.fetch_one(conn, "SELECT * FROM upload_log WHERE id = %s", (upload_id,))


def owned_upload(conn, upload_id, identity, missing=None):
    """The upload_log row; 404 if missing, 403 if an uploader asks for someone else's batch (decision Q25)."""
    row = fetch_upload(conn, upload_id)
    if not row:
        raise ApiError(404, missing or Msg("Upload batch not found.", "找不到這個上傳批次"), "no_upload")
    if identity.get("role") != "admin" and row["username"] != identity.get("username"):
        raise ApiError(403, Msg("This upload batch belongs to another user.", "這不是你的上傳批次"), "not_owner")
    return row


# ─── Commit (background thread) ──────────────────────────────────────────────

def ndjson(event):
    return json.dumps(event, ensure_ascii=False, default=str) + "\n"


def ndjson_events(events):
    while True:
        try:
            event = events.get(timeout=15)
        except queue.Empty:
            yield ndjson({"type": "heartbeat"})
            continue
        yield ndjson(event)
        if event["type"] in ("done", "error"):
            break


def run_ingest(conn, events, upload_id, work_order, planned_qty, total, username):
    """Insert every run with the work order; record each new row in upload_runs. Owns `conn`
    (and its upload advisory lock). Commits every 25 runs; one bad run never stops the batch."""
    st = {"total": total, "done": 0, "uploaded": 0, "skipped": 0, "failed": 0}
    failures = []
    t0 = time.time()
    repo = PostgresRunRepository(conn)
    try:
        repo.preload_hashes()
        batch_id = repo.begin_batch(work_order, "web")
        cur = conn.cursor()
        cur.execute("UPDATE upload_log SET batch_id = %s WHERE id = %s", (batch_id, upload_id))
        events.put({"type": "start", "upload_id": upload_id, "work_order": work_order, "total": total})
        report = _Report()
        for item in iter_runs(upload_id):
            if "invalid" in item:
                continue
            try:
                parsed = parse_iw61x_content(item["content"], item["filename"], work_order_override=work_order,
                                             source_file=item["filename"])
                if repo.has_hash(parsed.file_hash):
                    st["skipped"] += 1
                    report.duplicates += 1
                else:
                    result_id = repo.save(parsed, source="web")
                    cur.execute("INSERT INTO upload_runs (upload_id, test_result_id) VALUES (%s, %s)",
                                (upload_id, result_id))
                    st["uploaded"] += 1
                    report.uploaded += 1
                    report.warnings += len(parsed.issues)
            except Exception as exc:
                st["failed"] += 1
                report.rejected += 1
                if len(failures) < 50:
                    failures.append({"station": item["station"], "filename": item["filename"], "error": str(exc)[:300]})
            st["done"] += 1
            if st["done"] % 25 == 0:
                conn.commit()
                events.put({"type": "progress", **st})
        report.total_files = st["done"]
        repo.finish_batch(report)
        if planned_qty:
            cur.execute("""
                INSERT INTO alignment_targets (work_order, target_total, updated_at) VALUES (%s, %s, NOW())
                ON CONFLICT (work_order) DO UPDATE SET target_total = EXCLUDED.target_total, updated_at = NOW()
            """, (work_order, planned_qty))
        elapsed = round(time.time() - t0, 1)
        cur.execute("""
            UPDATE upload_log SET status = 'done', finished_at = NOW(), uploaded = %s, skipped = %s, failed = %s,
                   failures = %s, elapsed_sec = %s
            WHERE id = %s
        """, (st["uploaded"], st["skipped"], st["failed"], psycopg2.extras.Json(failures), elapsed, upload_id))
        cur.execute("""
            INSERT INTO account_audit (actor, action, target, detail) VALUES (%s, 'upload', %s, %s)
        """, (username, work_order, json.dumps({"upload_id": upload_id, "batch_id": batch_id, **st})))
        conn.commit()
        remove_upload_files(upload_id)
        after_change(work_order, queue_report=st["uploaded"] > 0)
        events.put({"type": "done", **st, "elapsed_sec": elapsed, "failures": failures})
    except Exception as exc:
        log.exception("log upload %s failed", upload_id)
        try:
            conn.rollback()
            conn.cursor().execute("""
                UPDATE upload_log SET status = 'failed', finished_at = NOW(), uploaded = %s, skipped = %s,
                       failed = %s, failures = %s, elapsed_sec = %s, error = %s
                WHERE id = %s
            """, (st["uploaded"], st["skipped"], st["failed"], psycopg2.extras.Json(failures),
                  round(time.time() - t0, 1), str(exc)[:500], upload_id))
            conn.commit()
        except Exception:
            pass
        after_change(work_order, queue_report=False)
        events.put({"type": "error", "message": "Upload interrupted. Rows already written can be undone or uploaded again.",
                    "message_zh": "上傳中斷，已寫入的資料可撤銷或重新上傳", **st})
    finally:
        try:
            conn.close()
        except Exception:
            pass


class _Report:
    """The counters PostgresRunRepository.finish_batch writes to upload_batches."""

    def __init__(self):
        self.total_files = self.uploaded = self.duplicates = self.rejected = self.warnings = 0


def after_change(work_order, queue_report):
    """Make the dashboard and the AI report see the change right away."""
    try:
        stats.refresh(wait=True)
    except Exception:
        log.exception("statistics refresh after upload failed")
    if queue_report and work_order:
        try:
            from services import reports
            reports.enqueue(work_order, "upload")
        except Exception:
            log.exception("could not queue the AI report for %s", work_order)


def revert(conn, row, username):
    """Delete exactly the rows this upload inserted and restore the alignment target it set."""
    n = db.execute(conn, """
        DELETE FROM test_results WHERE id IN (SELECT test_result_id FROM upload_runs WHERE upload_id = %s)
    """, (row["id"],))
    wo, qty = row["work_order"], row["planned_qty"]
    if wo and qty and current_target(conn, wo) == qty:
        if row["prev_target"] is None:
            db.execute(conn, "DELETE FROM alignment_targets WHERE work_order = %s", (wo,))
        else:
            db.execute(conn, "UPDATE alignment_targets SET target_total = %s, updated_at = NOW() WHERE work_order = %s",
                       (row["prev_target"], wo))
    db.execute(conn, """
        UPDATE upload_log SET status = 'reverted', reverted = %s, reverted_by = %s, reverted_at = NOW() WHERE id = %s
    """, (n, username, row["id"]))
    db.execute(conn, "INSERT INTO account_audit (actor, action, target, detail) VALUES (%s, 'revert_upload', %s, %s)",
               (username, wo, json.dumps({"upload_id": row["id"], "deleted": n})))
    return n
