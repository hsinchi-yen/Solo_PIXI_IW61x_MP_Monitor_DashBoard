"""Unit and attempt statistics from the mv_unit / mv_unit_day views and test_results.

A unit is one (work order, MAC1). Every unit has three results (approved decision #4):
    first_pass     result of its first attempt in scope
    latest         result of its last attempt in scope     ← the headline yield (decision Q1)
    any_pass       PASS if any attempt in scope passed, otherwise its last result
Runs without a usable MAC1 are not units; they are counted as attempts (unknown-MAC attempts).

mv_unit holds those per unit over all dates (used when there is no date filter); with a date
filter the unit rows are rebuilt from the few mv_unit_day rows inside the range.
"""
import logging
import threading
import time
from collections import OrderedDict
from datetime import date, timedelta

import config
import db
from services.filters import Scope, mv_where, raw_where
from services.migrations import UNIT_ROWS

log = logging.getLogger("iw.stats")

VIEWS = ("latest", "first_pass", "any_pass")
RESULT_COL = {"latest": "latest_result", "first_pass": "first_result", "any_pass": "any_result"}


def units_cte(scope: Scope):
    """CTE `c`: one row per unit in scope with first_result, latest_result and any_result."""
    if not scope.has_dates:
        where, params = ("WHERE wo_key = %s", [scope.work_order]) if scope.work_order else ("", [])
        return f"""c AS (
            SELECT *, CASE WHEN has_pass THEN 'PASS' ELSE latest_result END AS any_result
            FROM mv_unit {where})""", params
    where, params = mv_where(scope)
    return f"""
    d AS (SELECT * FROM mv_unit_day {where}),
    u AS (
        SELECT wo_key, mac1,
               SUM(n)::int                                           AS attempts,
               SUM(n_pass)::int                                      AS pass_count,
               SUM(n_fail)::int                                      AS fail_count,
               SUM(n_stop)::int                                      AS stop_count,
               BOOL_OR(n_pass > 0)                                   AS has_pass,
               MIN(first_time)                                       AS first_time,
               (ARRAY_AGG(first_result ORDER BY first_time))[1]      AS first_result,
               (ARRAY_AGG(eff_date ORDER BY first_time))[1]          AS first_date,
               MAX(last_time)                                        AS last_time,
               (ARRAY_AGG(last_result ORDER BY last_time DESC))[1]   AS latest_result,
               (ARRAY_AGG(eff_date ORDER BY last_time DESC))[1]      AS latest_date,
               MAX(last_pass_time)                                   AS last_pass_time,
               (ARRAY_AGG(last_mac2 ORDER BY last_time DESC))[1]     AS last_mac2,
               SUM(dur_sum)                                          AS dur_sum,
               SUM(dur_n)::int                                       AS dur_n
        FROM d GROUP BY wo_key, mac1
    ),
    c AS (SELECT *, CASE WHEN has_pass THEN 'PASS' ELSE latest_result END AS any_result FROM u)""", params


def _pct(n, d):
    return round(n * 100.0 / d, 2) if d else None


def _round2(value):
    return round(float(value), 2) if value is not None else None


def _view_counts_sql():
    parts = []
    for view, col in RESULT_COL.items():
        for result in ("PASS", "FAIL", "STOP"):
            parts.append(f"COUNT(*) FILTER (WHERE {col} = '{result}') AS {view}_{result.lower()}")
    return ",\n               ".join(parts)


def _views_from_row(row, total):
    out = {}
    for view in VIEWS:
        p, f, s = (row.get(f"{view}_pass") or 0), (row.get(f"{view}_fail") or 0), (row.get(f"{view}_stop") or 0)
        out[view] = {"pass": p, "fail": f, "stop": s, "yield_pct": _pct(p, total)}
    return out


def attempts(conn, scope: Scope):
    where, params = raw_where(scope)
    row = db.fetch_one(conn, f"""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE result = 'PASS') AS pass,
               COUNT(*) FILTER (WHERE result = 'FAIL') AS fail,
               COUNT(*) FILTER (WHERE result = 'STOP') AS stop,
               COUNT(*) FILTER (WHERE NOT ({UNIT_ROWS})) AS unknown_mac,
               AVG(test_duration_sec) AS avg_duration_sec,
               MIN(start_time) AS first_test, MAX(start_time) AS last_test
        FROM test_results {where}
    """, params)
    row["yield_pct"] = _pct(row["pass"], row["total"])
    row["avg_duration_sec"] = _round2(row["avg_duration_sec"])
    return row


def summary(conn, scope: Scope):
    """Units with the three yields, attempts (including unknown-MAC runs) and retries."""
    cte, params = units_cte(scope)
    row = db.fetch_one(conn, f"""
        WITH {cte}
        SELECT COUNT(*) AS units,
               {_view_counts_sql()},
               COUNT(*) FILTER (WHERE attempts > 1) AS retry_units,
               COUNT(*) FILTER (WHERE first_result <> 'PASS' AND has_pass) AS recovered_units
        FROM c
    """, params)
    units = row["units"] or 0
    return {
        "units": units,
        "yield": _views_from_row(row, units),
        "retry_units": row["retry_units"] or 0,
        "retry_rate": _pct(row["retry_units"] or 0, units),
        "recovered_units": row["recovered_units"] or 0,
        "attempts": attempts(conn, scope),
    }


def trend(conn, scope: Scope):
    """Three yields over time, by the time each unit finished (its last attempt).
    Hourly when the scope covers one day, otherwise daily; gaps filled."""
    cte, params = units_cte(scope)
    span = db.fetch_one(conn, f"""
        WITH {cte} SELECT MIN(last_time)::date AS lo, MAX(last_time)::date AS hi, COUNT(*) AS n FROM c
    """, params)
    if not span or not span["n"]:
        return {"unit": "day", "points": []}
    unit = "hour" if span["lo"] == span["hi"] else "day"
    bucket = "date_trunc('hour', last_time)" if unit == "hour" else "last_time::date"
    step = "1 hour" if unit == "hour" else "1 day"
    series = f"generate_series(MIN(t), MAX(t), interval '{step}')" + ("" if unit == "hour" else "::date")
    rows = db.fetch_all(conn, f"""
        WITH {cte},
        data AS (
            SELECT {bucket} AS t, COUNT(*) AS units,
                   COUNT(*) FILTER (WHERE latest_result = 'PASS') AS latest_pass,
                   COUNT(*) FILTER (WHERE first_result = 'PASS')  AS first_pass,
                   COUNT(*) FILTER (WHERE any_result = 'PASS')    AS any_pass
            FROM c GROUP BY 1
        ),
        slots AS (SELECT {series} AS t FROM data)
        SELECT s.t, COALESCE(d.units, 0) AS units, COALESCE(d.latest_pass, 0) AS latest_pass,
               COALESCE(d.first_pass, 0) AS first_pass, COALESCE(d.any_pass, 0) AS any_pass
        FROM slots s LEFT JOIN data d ON d.t = s.t ORDER BY s.t
    """, params)
    points = [{"t": r["t"], "units": r["units"],
               "latest": _pct(r["latest_pass"], r["units"]),
               "first_pass": _pct(r["first_pass"], r["units"]),
               "any_pass": _pct(r["any_pass"], r["units"])} for r in rows]
    return {"unit": unit, "points": points}


def hourly(conn, scope: Scope, hours=24):
    """Test attempts per hour over the last `hours` hours of data in scope (is the line running?)."""
    where, params = raw_where(scope)
    latest = db.fetch_one(conn, f"SELECT MAX(start_time) AS t FROM test_results {where}", params)["t"]
    if latest is None:
        return {"hours": hours, "points": []}
    end = latest.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=hours - 1)
    w2, p2 = raw_where(scope, ["start_time >= %s"])
    rows = db.fetch_all(conn, f"""
        WITH data AS (
            SELECT date_trunc('hour', start_time) AS t,
                   COUNT(*) FILTER (WHERE result = 'PASS') AS pass,
                   COUNT(*) FILTER (WHERE result = 'FAIL') AS fail,
                   COUNT(*) FILTER (WHERE result = 'STOP') AS stop
            FROM test_results {w2} GROUP BY 1
        )
        SELECT s.t, COALESCE(d.pass, 0) AS pass, COALESCE(d.fail, 0) AS fail, COALESCE(d.stop, 0) AS stop
        FROM generate_series(%s::timestamp, %s::timestamp, interval '1 hour') AS s(t)
        LEFT JOIN data d ON d.t = s.t ORDER BY s.t
    """, p2 + [start, start, end])
    return {"hours": hours, "points": rows}


def top_fail_steps(conn, scope: Scope, limit=5):
    """FAIL / STOP attempts by failing step: attempts, distinct units, and units that later passed."""
    where, params = raw_where(scope, ["result IN ('FAIL', 'STOP')"], alias="r")
    rows = db.fetch_all(conn, f"""
        SELECT COALESCE(r.fail_step_name, CASE WHEN r.result = 'STOP' THEN '(STOP)' ELSE '(unknown)' END) AS step,
               COALESCE(r.fail_category, '') AS category,
               COUNT(*) AS attempts,
               COUNT(*) FILTER (WHERE r.result = 'FAIL') AS fail_attempts,
               COUNT(*) FILTER (WHERE r.result = 'STOP') AS stop_attempts,
               COUNT(DISTINCT (r.work_order, r.mac1)) FILTER (WHERE r.mac1 IS NOT NULL) AS units,
               COUNT(DISTINCT (r.work_order, r.mac1)) FILTER (WHERE u.has_pass) AS recovered_units
        FROM test_results r
        LEFT JOIN mv_unit u ON u.wo_key = r.work_order AND u.mac1 = r.mac1
        {where}
        GROUP BY 1, 2 ORDER BY attempts DESC, step LIMIT %s
    """, params + [limit])
    return rows


def year_of_latest(conn, scope: Scope):
    where, params = mv_where(scope)
    row = db.fetch_one(conn, f"SELECT EXTRACT(YEAR FROM MAX(eff_date))::int AS y FROM mv_unit_day {where}", params)
    return row["y"] if row and row["y"] else None


def monthly(conn, scope: Scope, year):
    """Jan–Dec: units by the month of their last attempt, split by latest result."""
    year_scope = Scope(max(filter(None, [scope.date_from, date(year, 1, 1)])),
                       min(filter(None, [scope.date_to, date(year + 1, 1, 1)])),
                       scope.work_order)
    cte, params = units_cte(year_scope)
    rows = db.fetch_all(conn, f"""
        WITH {cte},
        data AS (
            SELECT EXTRACT(MONTH FROM last_time)::int AS m, COUNT(*) AS units,
                   COUNT(*) FILTER (WHERE latest_result = 'PASS') AS pass,
                   COUNT(*) FILTER (WHERE latest_result = 'FAIL') AS fail,
                   COUNT(*) FILTER (WHERE latest_result = 'STOP') AS stop
            FROM c GROUP BY 1
        )
        SELECT m.m AS month, COALESCE(d.units, 0) AS units, COALESCE(d.pass, 0) AS pass,
               COALESCE(d.fail, 0) AS fail, COALESCE(d.stop, 0) AS stop
        FROM generate_series(1, 12) AS m(m) LEFT JOIN data d ON d.m = m.m ORDER BY m.m
    """, params)
    return {"year": year, "months": rows}


def work_order_summary(conn, scope: Scope):
    cte, params = units_cte(scope)
    rows = db.fetch_all(conn, f"""
        WITH {cte},
        per_wo AS (
            SELECT wo_key, COUNT(*) AS units,
                   {_view_counts_sql()},
                   COUNT(*) FILTER (WHERE attempts > 1) AS retry_units,
                   SUM(attempts) AS unit_attempts
            FROM c GROUP BY wo_key
        )
        SELECT p.*, t.target_total
        FROM per_wo p LEFT JOIN alignment_targets t ON t.work_order = p.wo_key
    """, params)
    where, aparams = raw_where(scope)
    att = {r["work_order"]: r for r in db.fetch_all(conn, f"""
        SELECT work_order, COUNT(*) AS attempts,
               COUNT(*) FILTER (WHERE NOT ({UNIT_ROWS})) AS unknown_mac,
               AVG(test_duration_sec) AS avg_duration_sec,
               MIN(start_time) AS first_test, MAX(start_time) AS last_test
        FROM test_results {where} GROUP BY work_order
    """, aparams)}
    out = []
    for r in rows:
        a = att.get(r["wo_key"], {})
        units = r["units"]
        out.append({
            "work_order": r["wo_key"],
            "units": units,
            "yield": _views_from_row(r, units),
            "attempts": a.get("attempts", r["unit_attempts"]),
            "unknown_mac": a.get("unknown_mac", 0),
            "retry_units": r["retry_units"],
            "retry_rate": _pct(r["retry_units"], units),
            "avg_duration_sec": _round2(a.get("avg_duration_sec")),
            "first_test": a.get("first_test"),
            "last_test": a.get("last_test"),
            "target_total": r["target_total"],
        })
    out.sort(key=lambda r: r["last_test"] or date.min, reverse=True)
    return out


def retries(conn, scope: Scope, limit=200):
    """Units tested more than once, most attempts first; plus totals by risk bucket."""
    cte, params = units_cte(scope)
    counts = db.fetch_one(conn, f"""
        WITH {cte}
        SELECT COUNT(*) FILTER (WHERE attempts > 1)  AS units,
               COUNT(*) FILTER (WHERE attempts >= 4) AS high,
               COUNT(*) FILTER (WHERE attempts = 3)  AS medium,
               COUNT(*) FILTER (WHERE attempts = 2)  AS low
        FROM c
    """, params)
    rows = db.fetch_all(conn, f"""
        WITH {cte}
        SELECT wo_key AS work_order, mac1, last_mac2 AS mac2, attempts, pass_count, fail_count, stop_count,
               first_result, latest_result, first_time AS first_attempt, last_time AS last_attempt,
               CASE WHEN attempts >= 4 THEN 'high' WHEN attempts >= 3 THEN 'medium' ELSE 'low' END AS retry_risk
        FROM c WHERE attempts > 1
        ORDER BY attempts DESC, last_time DESC, wo_key, mac1
        LIMIT %s
    """, params + [limit])
    return {**counts, "items": rows}


def filter_options(conn):
    """Work orders by most recent activity, plus the overall date range."""
    wos = db.fetch_all(conn, """
        SELECT work_order, MIN(start_time)::date AS first_date, MAX(start_time)::date AS last_date,
               MAX(start_time) AS last_test, COUNT(*) AS attempts
        FROM test_results GROUP BY work_order ORDER BY MAX(start_time) DESC, work_order
    """)
    span = db.fetch_one(conn, "SELECT MIN(start_time)::date AS min_date, MAX(start_time)::date AS max_date FROM test_results")
    return {"work_orders": wos, **(span or {})}


def latest_work_order(conn):
    row = db.fetch_one(conn, "SELECT work_order FROM test_results ORDER BY start_time DESC, id DESC LIMIT 1")
    return row["work_order"] if row else None


def alignment(conn, scope: Scope):
    """Per work order: units vs planned quantity and the aligned yield (latest-result PASS ÷ planned)."""
    rows = work_order_summary(conn, scope)
    totals = {"work_orders": len(rows), "gap": 0, "aligned_pass": 0, "aligned_total": 0}
    items = []
    for r in rows:
        target = r.get("target_total")
        passed = r["yield"]["latest"]["pass"]
        gap = max(0, target - r["units"]) if target is not None else 0
        aligned_total = r["units"] + gap
        items.append({
            "work_order": r["work_order"], "units": r["units"], "target_total": target, "gap": gap,
            "pass": passed, "fail": r["yield"]["latest"]["fail"], "stop": r["yield"]["latest"]["stop"],
            "yield_pct": r["yield"]["latest"]["yield_pct"],
            "aligned_total": aligned_total, "aligned_fail": r["yield"]["latest"]["fail"] + gap,
            "aligned_yield": _pct(passed, aligned_total), "last_test": r["last_test"],
        })
        totals["gap"] += gap
        totals["aligned_pass"] += passed
        totals["aligned_total"] += aligned_total
    totals["aligned_yield"] = _pct(totals["aligned_pass"], totals["aligned_total"])
    return {"items": items, "totals": totals}


# ─── Raw-row queries ─────────────────────────────────────────────────────────

def fail_analysis(conn, scope: Scope, limit=20):
    """Failing steps with category and message, and the category totals."""
    where, params = raw_where(scope, ["result IN ('FAIL', 'STOP')"])
    steps = db.fetch_all(conn, f"""
        SELECT COALESCE(fail_step_name, CASE WHEN result = 'STOP' THEN '(STOP)' ELSE '(unknown)' END) AS step,
               fail_step_num, COALESCE(fail_category, '') AS category,
               COALESCE(fail_message, '') AS message, result, COUNT(*) AS count
        FROM test_results {where}
        GROUP BY 1, 2, 3, 4, 5 ORDER BY count DESC, step LIMIT %s
    """, params + [limit])
    categories = db.fetch_all(conn, f"""
        SELECT COALESCE(fail_category, CASE WHEN result = 'STOP' THEN 'STOP' ELSE 'Unknown' END) AS category,
               COUNT(*) AS count
        FROM test_results {where} GROUP BY 1 ORDER BY count DESC, 1
    """, params)
    return {"steps": steps, "categories": categories}


def fails(conn, scope: Scope, page=1, page_size=50, search=None, result=None):
    extra = ["result IN ('FAIL', 'STOP')"] if result not in ("FAIL", "STOP") else ["result = %s"]
    s_params = [result] if result in ("FAIL", "STOP") else []
    if search:
        extra.append("(mac1 ILIKE %s OR mac2 ILIKE %s OR fail_step_name ILIKE %s OR fail_message ILIKE %s "
                     "OR work_order ILIKE %s)")
        s_params += [f"%{search}%"] * 5
    where, params = raw_where(scope)
    where = (where + " AND " if where else "WHERE ") + " AND ".join(extra)
    params = params + s_params
    total = db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM test_results {where}", params)["n"]
    pages = max(1, -(-total // page_size))
    page = min(max(1, page), pages)
    rows = db.fetch_all(conn, f"""
        SELECT id, work_order, mac1, mac2, mac1_source, start_time, test_duration_sec, result,
               fail_step_num, fail_step_name, fail_message, fail_category, source_file
        FROM test_results {where}
        ORDER BY start_time DESC, id DESC LIMIT %s OFFSET %s
    """, params + [page_size, (page - 1) * page_size])
    return {"items": rows, "total": total, "page": page, "page_size": page_size, "pages": pages}


# ─── Result cache ────────────────────────────────────────────────────────────
# What the dashboard reads changes only when the views are refreshed (or a planned
# quantity is saved), so results are cached per worker under that data version.

_cache = OrderedDict()
_cache_lock = threading.Lock()
CACHE_MAX = 256


def data_version(conn):
    row = db.fetch_one(conn, """
        SELECT (SELECT updated_at FROM app_meta WHERE key = 'stats_refreshed_at') AS stats,
               (SELECT MAX(updated_at)::text || '/' || COUNT(*) FROM alignment_targets) AS targets
    """)
    return (str(row["stats"]), row["targets"])


def cached(conn, key, compute):
    k = (key, data_version(conn))
    with _cache_lock:
        if k in _cache:
            _cache.move_to_end(k)
            return _cache[k]
    value = compute()
    with _cache_lock:
        _cache[k] = value
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return value


# ─── View refresh ────────────────────────────────────────────────────────────

_refresh_lock = threading.Lock()


def refreshed_at(conn):
    row = db.fetch_one(conn, "SELECT updated_at FROM app_meta WHERE key = 'stats_refreshed_at'")
    return row["updated_at"] if row else None


def refresh(wait=False):
    """REFRESH MATERIALIZED VIEW CONCURRENTLY for all views (readers are never blocked).

    Returns the refresh time, or None when another worker is already refreshing and
    wait=False. With wait=True (after an upload or delete) it waits for that refresh
    and then runs its own, so the caller's change is included."""
    with _refresh_lock:
        conn = db.connect()
        try:
            conn.autocommit = True
            cur = conn.cursor()
            if wait:
                cur.execute("SELECT pg_advisory_lock(%s)", (config.STATS_REFRESH_LOCK_KEY,))
            else:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (config.STATS_REFRESH_LOCK_KEY,))
                if not cur.fetchone()[0]:
                    return None
            try:
                t0 = time.perf_counter()
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_unit_day")
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_unit")
                changed = _refresh_metric_day(conn)
                cur.execute("""
                    INSERT INTO app_meta (key, value, updated_at) VALUES ('stats_refreshed_at', NOW()::text, NOW())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    RETURNING updated_at
                """)
                at = cur.fetchone()[0]
                log.info("statistics refreshed in %.0f ms (%d work orders re-aggregated)",
                         (time.perf_counter() - t0) * 1000, changed)
                return at
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)", (config.STATS_REFRESH_LOCK_KEY,))
        finally:
            conn.close()


def _refresh_metric_day(conn):
    """Re-aggregate metric_day only for work orders whose runs changed (count, max id, id sum).
    Each work order is replaced in its own transaction, so readers never see half of one."""
    from services.migrations import METRIC_DAY_SELECT, METRIC_KEY
    autocommit = conn.autocommit
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT work_order, COUNT(*), MAX(id), SUM(id) FROM test_results GROUP BY work_order")
        current = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
        cur.execute("SELECT wo_key, runs, max_id, id_sum FROM metric_wo_state")
        stored = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
    stale = [wo for wo, sig in current.items() if stored.get(wo) != sig] + [wo for wo in stored if wo not in current]
    conn.autocommit = False
    try:
        for wo in stale:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM metric_day WHERE wo_key = %s", (wo,))
                cur.execute("DELETE FROM metric_wo_state WHERE wo_key = %s", (wo,))
                if wo in current:
                    cur.execute(f"INSERT INTO metric_day ({METRIC_KEY}, unit, limit_low, limit_high, n, s, ss, vmin, vmax, n_fail) "
                                + METRIC_DAY_SELECT, (wo,))
                    runs, max_id, id_sum = current[wo]
                    cur.execute("INSERT INTO metric_wo_state (wo_key, runs, max_id, id_sum) VALUES (%s, %s, %s, %s)",
                                (wo, runs, max_id, id_sum))
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = autocommit
    return len(stale)


def refresh_metric_day():
    """Build metric_day for any work order that is missing or changed (startup)."""
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (config.STATS_REFRESH_LOCK_KEY,))   # both workers start together
        conn.commit()
        try:
            n = _refresh_metric_day(conn)
            if n:
                log.info("metric_day built for %d work orders", n)
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (config.STATS_REFRESH_LOCK_KEY,))
            conn.commit()
    finally:
        conn.close()


def refresh_if_due():
    """Called by the scheduler: refresh when the last one is older than STATS_REFRESH_SEC."""
    with db.db() as conn:
        row = db.fetch_one(conn, """
            SELECT NOW() - updated_at >= %s AS due FROM app_meta WHERE key = 'stats_refreshed_at'
        """, (timedelta(seconds=config.STATS_REFRESH_SEC),))
    if row is None or row["due"]:
        return refresh(wait=False)
    return None
