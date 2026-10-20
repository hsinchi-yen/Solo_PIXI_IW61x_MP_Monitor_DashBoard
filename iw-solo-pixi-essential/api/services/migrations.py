"""Schema setup at API startup.

Rule (decision Q19): existing tables keep their columns and data. test_results,
test_measurements, upload_batches, data_quality_issues and alignment_targets are only
read here. Everything new lives in separate objects:

    upload_log, upload_runs        web Log Uploader batches and the rows each one added (undo)
    ai_reports, app_meta, job_runs AI reports, small key/value store, background jobs
    mv_unit_day, mv_unit           one row per unit (work order, MAC1) per day / overall
    metric_day, metric_wo_state    per day, work order and test item: n, sum, sum², min, max, limits —
                                   a table rebuilt per work order only when its runs change (stats.py),
                                   because re-aggregating every measurement takes minutes at scale

schema.sql (base tables + accounts) runs first; this module adds the rest.
"""
import logging

import config
import db

log = logging.getLogger("iw.migrations")

# Bump when a view definition changes; startup then rebuilds the views.
MV_VERSION = "2"

# A unit is (work order, MAC1). Runs without a usable MAC1 are not units (they stay in
# the attempt counts as unknown-MAC attempts).
UNIT_ROWS = "mac1 IS NOT NULL AND mac1 <> '' AND mac1_source IS DISTINCT FROM 'unknown'"

MV_UNIT_DAY_SQL = f"""
CREATE MATERIALIZED VIEW mv_unit_day AS
SELECT
    start_time::date                                                  AS eff_date,
    work_order                                                        AS wo_key,
    mac1,
    COUNT(*)::int                                                     AS n,
    (COUNT(*) FILTER (WHERE result = 'PASS'))::int                    AS n_pass,
    (COUNT(*) FILTER (WHERE result = 'FAIL'))::int                    AS n_fail,
    (COUNT(*) FILTER (WHERE result = 'STOP'))::int                    AS n_stop,
    MIN(start_time)                                                   AS first_time,
    (ARRAY_AGG(result ORDER BY start_time, id))[1]                    AS first_result,
    MAX(start_time)                                                   AS last_time,
    (ARRAY_AGG(result ORDER BY start_time DESC, id DESC))[1]          AS last_result,
    MAX(start_time) FILTER (WHERE result = 'PASS')                    AS last_pass_time,
    (ARRAY_AGG(mac2 ORDER BY start_time DESC, id DESC))[1]            AS last_mac2,
    SUM(test_duration_sec)                                            AS dur_sum,
    COUNT(test_duration_sec)::int                                     AS dur_n
FROM test_results
WHERE {UNIT_ROWS}
GROUP BY 1, 2, 3
"""

MV_UNIT_SQL = """
CREATE MATERIALIZED VIEW mv_unit AS
SELECT wo_key, mac1,
       SUM(n)::int                                                      AS attempts,
       SUM(n_pass)::int                                                 AS pass_count,
       SUM(n_fail)::int                                                 AS fail_count,
       SUM(n_stop)::int                                                 AS stop_count,
       BOOL_OR(n_pass > 0)                                              AS has_pass,
       MIN(first_time)                                                  AS first_time,
       (ARRAY_AGG(first_result ORDER BY first_time))[1]                 AS first_result,
       (ARRAY_AGG(eff_date ORDER BY first_time))[1]                     AS first_date,
       MAX(last_time)                                                   AS last_time,
       (ARRAY_AGG(last_result ORDER BY last_time DESC))[1]              AS latest_result,
       (ARRAY_AGG(eff_date ORDER BY last_time DESC))[1]                 AS latest_date,
       MAX(last_pass_time)                                              AS last_pass_time,
       (ARRAY_AGG(last_mac2 ORDER BY last_time DESC))[1]                AS last_mac2,
       SUM(dur_sum)                                                     AS dur_sum,
       SUM(dur_n)::int                                                  AS dur_n
FROM mv_unit_day GROUP BY wo_key, mac1
"""

METRIC_DAY_TABLE = """
CREATE TABLE IF NOT EXISTS metric_day (
    eff_date DATE NOT NULL, wo_key TEXT NOT NULL, technology TEXT NOT NULL, direction TEXT NOT NULL,
    standard TEXT NOT NULL, band TEXT NOT NULL, frequency_mhz INT NOT NULL, bandwidth TEXT NOT NULL,
    rate TEXT NOT NULL, antenna TEXT NOT NULL, metric_name TEXT NOT NULL, unit TEXT,
    limit_low DOUBLE PRECISION, limit_high DOUBLE PRECISION, n INT NOT NULL, s DOUBLE PRECISION,
    ss DOUBLE PRECISION, vmin DOUBLE PRECISION, vmax DOUBLE PRECISION, n_fail INT NOT NULL
)
"""

METRIC_WO_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS metric_wo_state (
    wo_key        TEXT PRIMARY KEY,
    runs          BIGINT NOT NULL,
    max_id        BIGINT NOT NULL,
    id_sum        NUMERIC NOT NULL,
    refreshed_at  TIMESTAMPTZ DEFAULT NOW()
)
"""

# Rows of metric_day for one work order. Older uploads stored legacy-rate Wi-Fi items (CCK / OFDM)
# as 11n; the standard is reported from the rate so old and new rows group together. The stored
# measurement rows are not changed.
METRIC_DAY_SELECT = """
SELECT
    tr.start_time::date                                               AS eff_date,
    tr.work_order                                                     AS wo_key,
    tm.technology,
    COALESCE(tm.direction, '')                                        AS direction,
    CASE WHEN tm.technology = 'WIFI' AND tm.rate LIKE 'CCK%%' THEN '11b'
         WHEN tm.technology = 'WIFI' AND tm.rate LIKE 'OFDM%%'
              THEN CASE WHEN tm.band = '2.4GHz' THEN '11g' ELSE '11a' END
         ELSE COALESCE(tm.standard, '') END                           AS standard,
    COALESCE(tm.band, '')                                             AS band,
    COALESCE(tm.frequency_mhz, 0)                                     AS frequency_mhz,
    COALESCE(tm.bandwidth, '')                                        AS bandwidth,
    COALESCE(tm.rate, '')                                             AS rate,
    COALESCE(tm.antenna, '')                                          AS antenna,
    tm.metric_name,
    MAX(tm.unit)                                                      AS unit,
    MIN(tm.limit_low)                                                 AS limit_low,
    MAX(tm.limit_high)                                                AS limit_high,
    COUNT(tm.value)::int                                              AS n,
    SUM(tm.value)                                                     AS s,
    SUM(tm.value * tm.value)                                          AS ss,
    MIN(tm.value)                                                     AS vmin,
    MAX(tm.value)                                                     AS vmax,
    (COUNT(*) FILTER (WHERE tm.passed IS FALSE))::int                 AS n_fail
FROM test_results tr
JOIN test_measurements tm ON tm.test_result_id = tr.id
WHERE tr.work_order = %s AND tm.value IS NOT NULL AND tm.value = tm.value
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
"""

METRIC_KEY = "eff_date, wo_key, technology, direction, standard, band, frequency_mhz, bandwidth, rate, antenna, metric_name"

NEW_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS app_meta (
        key         TEXT PRIMARY KEY,
        value       TEXT,
        updated_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    # Web Log Uploader: one row per analyzed zip (status analyzed → running → done | failed | reverted).
    """
    CREATE TABLE IF NOT EXISTS upload_log (
        id              TEXT PRIMARY KEY,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        finished_at     TIMESTAMPTZ,
        client_ip       TEXT,
        username        TEXT,
        user_role       TEXT,
        file_name       TEXT,
        file_size       BIGINT,
        work_order      TEXT,
        inferred_wo     TEXT,
        planned_qty     INT,
        prev_target     INT,
        unique_mac1     INT,
        total_records   INT,
        facts           JSONB,
        validation      JSONB,
        chat_transcript JSONB,
        mode            TEXT,
        status          TEXT,
        batch_id        INT,
        uploaded        INT,
        skipped         INT,
        failed          INT,
        failures        JSONB,
        reverted        INT,
        reverted_by     TEXT,
        reverted_at     TIMESTAMPTZ,
        elapsed_sec     REAL,
        error           TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_upload_log_created ON upload_log (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_upload_log_username ON upload_log (username, created_at DESC)",
    # Which test_results rows a web upload inserted — undo deletes exactly these (decision Q21).
    """
    CREATE TABLE IF NOT EXISTS upload_runs (
        upload_id       TEXT NOT NULL REFERENCES upload_log(id) ON DELETE CASCADE,
        test_result_id  INT NOT NULL REFERENCES test_results(id) ON DELETE CASCADE,
        PRIMARY KEY (upload_id, test_result_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_upload_runs_result ON upload_runs (test_result_id)",
    # AI quality reports: one row per generation, both languages.
    """
    CREATE TABLE IF NOT EXISTS ai_reports (
        id            BIGSERIAL PRIMARY KEY,
        work_order    TEXT NOT NULL,
        status        TEXT NOT NULL,
        trigger       TEXT,
        requested_by  TEXT,
        data_hash     TEXT,
        facts         JSONB,
        report_zh     JSONB,
        report_en     JSONB,
        checks        JSONB,
        model         TEXT,
        error         TEXT,
        created_at    TIMESTAMPTZ DEFAULT NOW(),
        started_at    TIMESTAMPTZ,
        finished_at   TIMESTAMPTZ,
        elapsed_sec   REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ai_reports_wo ON ai_reports (work_order, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ai_reports_status ON ai_reports (status, created_at)",
    # Background jobs (reparse): progress shared by both workers.
    """
    CREATE TABLE IF NOT EXISTS job_runs (
        id            BIGSERIAL PRIMARY KEY,
        kind          TEXT NOT NULL,
        status        TEXT NOT NULL,
        total         INT,
        done          INT DEFAULT 0,
        updated       INT DEFAULT 0,
        failed        INT DEFAULT 0,
        cancel        BOOLEAN DEFAULT FALSE,
        requested_by  TEXT,
        error         TEXT,
        started_at    TIMESTAMPTZ DEFAULT NOW(),
        heartbeat_at  TIMESTAMPTZ DEFAULT NOW(),
        finished_at   TIMESTAMPTZ
    )
    """,
]


def _meta_get(cur, key):
    cur.execute("SELECT value FROM app_meta WHERE key = %s", (key,))
    row = cur.fetchone()
    return row[0] if row else None


def _meta_set(cur, key, value):
    cur.execute("""
        INSERT INTO app_meta (key, value, updated_at) VALUES (%s, %s, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
    """, (key, value))


def build_views(cur):
    for name in ("mv_unit", "mv_unit_day", "mv_metric_day"):
        cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name}")
    cur.execute("DROP TABLE IF EXISTS metric_day")
    cur.execute("DROP TABLE IF EXISTS metric_wo_state")
    cur.execute(MV_UNIT_DAY_SQL)
    cur.execute("CREATE UNIQUE INDEX ux_mv_unit_day ON mv_unit_day (eff_date, wo_key, mac1)")
    cur.execute("CREATE INDEX ix_mv_unit_day_wo ON mv_unit_day (wo_key, eff_date)")
    cur.execute(MV_UNIT_SQL)
    cur.execute("CREATE UNIQUE INDEX ux_mv_unit ON mv_unit (wo_key, mac1)")
    cur.execute(METRIC_DAY_TABLE)
    cur.execute(f"CREATE UNIQUE INDEX ux_metric_day ON metric_day ({METRIC_KEY})")
    cur.execute("CREATE INDEX ix_metric_day_wo ON metric_day (wo_key, eff_date)")
    cur.execute(METRIC_WO_STATE_TABLE)      # empty: the first refresh builds every work order
    _meta_set(cur, "mv_version", MV_VERSION)
    _meta_set(cur, "stats_refreshed_at", "built")


def run():
    db.init_schema()
    conn = db.connect()
    try:
        cur = conn.cursor()
        # Both uvicorn workers start together; serialise so CREATE … IF NOT EXISTS cannot race.
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (config.SCHEMA_LOCK_KEY,))
        for ddl in NEW_TABLES:
            cur.execute(ddl)
        cur.execute("""
            SELECT to_regclass('mv_unit_day') IS NOT NULL AND to_regclass('mv_unit') IS NOT NULL
               AND to_regclass('metric_day') IS NOT NULL AND to_regclass('metric_wo_state') IS NOT NULL
        """)
        exists = cur.fetchone()[0]
        if not exists or _meta_get(cur, "mv_version") != MV_VERSION:
            build_views(cur)
            log.info("built statistics views (version %s)", MV_VERSION)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    from services import stats
    stats.refresh_metric_day()
