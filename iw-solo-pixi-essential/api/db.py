"""
Database access for the IW Solo PIXI dashboard.

    with db() as conn:            # pooled; commit on success, rollback on error, always returned
        ...

Uploads keep a dedicated connection from get_connection() because they commit in batches;
the caller closes it (see upload_routes.py).
"""
import logging
import os
import threading
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

import config

log = logging.getLogger("iw.db")

DATABASE_URL = config.DATABASE_URL

_pool = None
_pool_lock = threading.Lock()
_slots = threading.BoundedSemaphore(config.DB_POOL_MAX)


class _Timed:
    """Logs statements slower than SLOW_QUERY_MS."""

    def execute(self, query, vars=None):
        t0 = time.perf_counter()
        try:
            return super().execute(query, vars)
        finally:
            ms = (time.perf_counter() - t0) * 1000
            if ms >= config.SLOW_QUERY_MS:
                log.warning("slow query %.0f ms: %s", ms, " ".join(str(query).split())[:300])


class TimedCursor(_Timed, psycopg2.extensions.cursor):
    pass


class _TimedDictCursor(_Timed, psycopg2.extras.RealDictCursor):
    pass


def get_connection(timeout: int = 5, database_url: str | None = None):
    """A dedicated (non-pooled) connection; the caller must close it."""
    return psycopg2.connect(database_url or DATABASE_URL, connect_timeout=timeout)


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadedConnectionPool(1, config.DB_POOL_MAX, DATABASE_URL,
                                           connect_timeout=5, cursor_factory=TimedCursor)
        return _pool


@contextmanager
def db():
    """Pooled connection. Waits for a free slot instead of failing when all are busy."""
    _slots.acquire()
    conn = None
    broken = False
    pool = None
    try:
        pool = _get_pool()
        conn = pool.getconn()
        if conn.closed:
            pool.putconn(conn, close=True)
            conn = pool.getconn()
        yield conn
        conn.commit()
    except (psycopg2.InterfaceError, psycopg2.OperationalError):
        broken = True
        raise
    except BaseException:
        if conn is not None and not conn.closed:
            try:
                conn.rollback()
            except psycopg2.Error:
                broken = True
        raise
    finally:
        if conn is not None and pool is not None:
            try:
                pool.putconn(conn, close=broken or bool(conn.closed))
            except psycopg2.pool.PoolError:
                pass
        _slots.release()


def connect():
    """A dedicated connection for long work that holds a session advisory lock; the caller closes it."""
    return psycopg2.connect(DATABASE_URL, connect_timeout=5, cursor_factory=TimedCursor)


def _dict_cursor(conn):
    return conn.cursor(cursor_factory=_TimedDictCursor)


def fetch_all(conn, sql, params=None):
    with _dict_cursor(conn) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_one(conn, sql, params=None):
    with _dict_cursor(conn) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def execute(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def close_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


def _schema_path():
    search_paths = [
        "/app/schema.sql",                                              # Docker container
        os.path.join(os.path.dirname(__file__), "..", "schema.sql"),    # Local dev
        os.path.join(os.path.dirname(__file__), "schema.sql"),
    ]
    for path in search_paths:
        resolved = os.path.abspath(path)
        if os.path.exists(resolved):
            return resolved
    return None


def init_schema(database_url: str | None = None):
    """
    Apply schema.sql. Idempotent (CREATE ... IF NOT EXISTS); an advisory lock keeps the
    two uvicorn workers from running it at the same time.
    """
    schema_path = _schema_path()
    if schema_path is None:
        log.warning("schema.sql not found, skipping schema initialization")
        return
    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()

    conn = get_connection(database_url=database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (config.SCHEMA_LOCK_KEY,))
            cur.execute(sql)
        conn.commit()
        log.info("schema initialized from %s", schema_path)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def check_health() -> dict:
    """Database connectivity; never returns exception text (it can carry host/user)."""
    try:
        t0 = time.perf_counter()
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM test_results")
            count = cur.fetchone()[0]
        return {"status": "ok", "db": "connected", "test_results_count": count,
                "db_ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception:
        log.exception("health check failed")
        return {"status": "error", "db": "unavailable"}
