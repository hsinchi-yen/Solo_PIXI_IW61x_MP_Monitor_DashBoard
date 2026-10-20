"""Background reparse: refresh fail_step_* / fail_message / fail_category from raw_log.

Runs in batches of BATCH rows (ordered by id) with one raw log in memory at a time, so the
HTTP request returns immediately; progress and cancel live in job_runs, which both uvicorn
workers can read. Only the four fail_* columns are updated, as the legacy reparse did.
"""
import logging
import threading

import config
import db
from errors import ApiError, Msg
from parsers.iw61x import parse_iw61x_content
from services.ingest import file_name

log = logging.getLogger("iw.reparse")

BATCH = 200
FIELDS = ("fail_step_num", "fail_step_name", "fail_message", "fail_category")
REPARSE_LOCK_KEY = config.SCHEMA_LOCK_KEY + 100


def latest_job(conn):
    return db.fetch_one(conn, "SELECT * FROM job_runs WHERE kind = 'reparse' ORDER BY id DESC LIMIT 1")


def start(username):
    """Create a job row and run it in a daemon thread. 409 if one is already running."""
    with db.db() as conn:
        db.execute(conn, """
            UPDATE job_runs SET status = 'failed', error = 'worker stopped', finished_at = NOW()
            WHERE kind = 'reparse' AND status = 'running' AND heartbeat_at < NOW() - INTERVAL '5 minutes'
        """)
        running = db.fetch_one(conn, "SELECT id FROM job_runs WHERE kind = 'reparse' AND status = 'running'")
        if running:
            raise ApiError(409, Msg("A reparse is already running.", "已有重新解析正在進行"), "reparse_running",
                           job_id=running["id"])
        total = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM test_results WHERE raw_log IS NOT NULL AND raw_log <> ''")["n"]
        job = db.fetch_one(conn, """
            INSERT INTO job_runs (kind, status, total, requested_by) VALUES ('reparse', 'running', %s, %s) RETURNING *
        """, (total, username))
    threading.Thread(target=_run, args=(job["id"],), daemon=True, name=f"reparse-{job['id']}").start()
    return job


def cancel(job_id):
    with db.db() as conn:
        return db.execute(conn, "UPDATE job_runs SET cancel = TRUE WHERE id = %s AND status = 'running'", (job_id,))


def _run(job_id):
    conn = db.connect()
    done = updated = failed = 0
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (REPARSE_LOCK_KEY,))
        if not cur.fetchone()[0]:
            cur.execute("UPDATE job_runs SET status = 'failed', error = 'locked', finished_at = NOW() WHERE id = %s",
                        (job_id,))
            conn.commit()
            return
        conn.commit()
        last_id = 0
        while True:
            cur.execute("SELECT cancel FROM job_runs WHERE id = %s", (job_id,))
            if cur.fetchone()[0]:
                cur.execute("""UPDATE job_runs SET status = 'cancelled', done = %s, updated = %s, failed = %s,
                               finished_at = NOW() WHERE id = %s""", (done, updated, failed, job_id))
                conn.commit()
                return
            cur.execute("""
                SELECT id FROM test_results WHERE id > %s AND raw_log IS NOT NULL AND raw_log <> ''
                ORDER BY id LIMIT %s
            """, (last_id, BATCH))
            ids = [r[0] for r in cur.fetchall()]
            if not ids:
                break
            for record_id in ids:
                last_id = record_id
                cur.execute("SELECT work_order, raw_log, source_file FROM test_results WHERE id = %s", (record_id,))
                row = cur.fetchone()
                try:
                    work_order, raw_log, source_file = row
                    parsed = parse_iw61x_content(raw_log, file_name(source_file) or f"record_{record_id}.txt",
                                                 work_order_override=work_order)
                    cur.execute(f"UPDATE test_results SET {', '.join(f + ' = %s' for f in FIELDS)} WHERE id = %s",
                                [getattr(parsed, f) for f in FIELDS] + [record_id])
                    updated += cur.rowcount
                except Exception as exc:  # one bad log must not stop the job
                    failed += 1
                    log.warning("reparse id %s failed: %s", record_id, exc)
                done += 1
            cur.execute("UPDATE job_runs SET done = %s, updated = %s, failed = %s, heartbeat_at = NOW() WHERE id = %s",
                        (done, updated, failed, job_id))
            conn.commit()
        cur.execute("""UPDATE job_runs SET status = 'done', done = %s, updated = %s, failed = %s, finished_at = NOW()
                       WHERE id = %s""", (done, updated, failed, job_id))
        conn.commit()
        log.info("reparse job %s done: %s rows, %s updated, %s failed", job_id, done, updated, failed)
    except Exception as exc:
        log.exception("reparse job %s failed", job_id)
        try:
            conn.rollback()
            conn.cursor().execute("UPDATE job_runs SET status = 'failed', error = %s, finished_at = NOW() WHERE id = %s",
                                  (str(exc)[:300], job_id))
            conn.commit()
        except Exception:
            pass
    finally:
        try:
            conn.cursor().execute("SELECT pg_advisory_unlock(%s)", (REPARSE_LOCK_KEY,))
            conn.commit()
        except Exception:
            pass
        conn.close()
