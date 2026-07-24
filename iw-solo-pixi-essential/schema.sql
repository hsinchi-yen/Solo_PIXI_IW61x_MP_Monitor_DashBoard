-- ============================================================
-- IW Solo PIXI Test Analytics Dashboard — PostgreSQL Schema
-- Target: PostgreSQL 16
-- Idempotent: safe to re-run via CREATE ... IF NOT EXISTS
-- ============================================================

-- -----------------------------------------------------------
-- test_results: one row per uploaded log file (run-level)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_results (
    id              SERIAL PRIMARY KEY,
    work_order      TEXT NOT NULL,
    product         TEXT NOT NULL DEFAULT 'IW611',
    mac1            TEXT,                          -- Wi-Fi MAC, nullable for unknown-MAC
    mac2            TEXT,                          -- BT BD Address
    mac1_source     TEXT DEFAULT 'filename',       -- 'filename', 'log_body', 'unknown'
    start_time      TIMESTAMP NOT NULL,
    end_time        TIMESTAMP,
    test_duration_sec FLOAT,
    result          TEXT NOT NULL CHECK (result IN ('PASS', 'FAIL', 'STOP')),
    flow_file       TEXT,
    package_name    TEXT,
    tester_sn       TEXT,
    fw_version      TEXT,
    fail_step_num   INT,
    fail_step_name  TEXT,
    fail_message    TEXT,
    fail_category   TEXT,                          -- 'TestFail', 'DeviceConfigure', etc.
    raw_log         TEXT,                          -- full raw log content
    file_hash       TEXT UNIQUE NOT NULL,          -- SHA-256 for idempotent uploads
    source_file     TEXT,                          -- original filename
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------
-- test_measurements: normalized metric rows (measurement-level)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_measurements (
    id              SERIAL PRIMARY KEY,
    test_result_id  INT NOT NULL REFERENCES test_results(id) ON DELETE CASCADE,
    technology      TEXT NOT NULL,                  -- 'WIFI', 'BT', 'BLE', 'CALIBRATION'
    direction       TEXT,                           -- 'TX', 'RX', 'CAL'
    standard        TEXT,                           -- '11b','11g','11a','11n','11ac','11ax','BDR','EDR','LE'
    band            TEXT,                           -- '2.4GHz', '5GHz'
    frequency_mhz   INT,
    bandwidth       TEXT,                           -- 'BW20', 'BW40', 'BW80'
    rate            TEXT,                           -- 'CCK11','OFDM54','MCS7','MCS9','MCS11','1DH5', etc.
    antenna         TEXT,
    step_num        INT,
    step_name       TEXT,
    metric_name     TEXT NOT NULL,                  -- 'EVM_DB','POWER_DBM','FREQ_ERROR','BER', etc.
    value           FLOAT,
    unit            TEXT,                           -- 'dB','dBm','ppm','%'
    limit_low       FLOAT,
    limit_high      FLOAT,
    passed          BOOLEAN
);

-- -----------------------------------------------------------
-- upload_batches: track each upload operation
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS upload_batches (
    id              SERIAL PRIMARY KEY,
    uploaded_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    total_files     INT DEFAULT 0,
    uploaded        INT DEFAULT 0,                  -- new records inserted
    duplicates      INT DEFAULT 0,                  -- skipped by hash
    rejected        INT DEFAULT 0,                  -- parse/validation errors
    warnings        INT DEFAULT 0,                  -- data quality issues
    work_order      TEXT,                           -- override value if provided
    upload_source   TEXT DEFAULT 'api'              -- 'api', 'gui', 'cli'
);

-- -----------------------------------------------------------
-- data_quality_issues: parser and identity warnings
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_quality_issues (
    id              SERIAL PRIMARY KEY,
    test_result_id  INT REFERENCES test_results(id) ON DELETE CASCADE,
    batch_id        INT REFERENCES upload_batches(id) ON DELETE SET NULL,
    issue_type      TEXT NOT NULL,                  -- 'unknown_mac', 'mac_conflict', 'missing_field'
    description     TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- -----------------------------------------------------------
-- alignment_targets: production count targets per work order
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS alignment_targets (
    work_order      TEXT PRIMARY KEY,
    target_total    INT NOT NULL,
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Indexes for analytics query performance
-- ============================================================

-- test_results indexes
CREATE INDEX IF NOT EXISTS idx_tr_work_order       ON test_results (work_order);
CREATE INDEX IF NOT EXISTS idx_tr_start_time       ON test_results (start_time);
CREATE INDEX IF NOT EXISTS idx_tr_result           ON test_results (result);
CREATE INDEX IF NOT EXISTS idx_tr_mac1             ON test_results (mac1);
CREATE INDEX IF NOT EXISTS idx_tr_product          ON test_results (product);
CREATE INDEX IF NOT EXISTS idx_tr_wo_mac_time      ON test_results (work_order, mac1, start_time);
CREATE INDEX IF NOT EXISTS idx_tr_wo_time          ON test_results (work_order, start_time);
CREATE INDEX IF NOT EXISTS idx_tr_wo_result        ON test_results (work_order, result);

-- test_measurements indexes
CREATE INDEX IF NOT EXISTS idx_tm_result_id        ON test_measurements (test_result_id);
CREATE INDEX IF NOT EXISTS idx_tm_tech_std         ON test_measurements (technology, standard);
CREATE INDEX IF NOT EXISTS idx_tm_result_tech      ON test_measurements (test_result_id, technology, standard);
CREATE INDEX IF NOT EXISTS idx_tm_metric           ON test_measurements (metric_name);
CREATE INDEX IF NOT EXISTS idx_tm_tech_dir_band    ON test_measurements (technology, direction, band);

-- data_quality_issues indexes
CREATE INDEX IF NOT EXISTS idx_dqi_result_id       ON data_quality_issues (test_result_id);
CREATE INDEX IF NOT EXISTS idx_dqi_type            ON data_quality_issues (issue_type);
CREATE INDEX IF NOT EXISTS idx_dqi_batch_id        ON data_quality_issues (batch_id);

-- ============================================================
-- Views for common analytics queries
-- ============================================================

-- First-pass yield: first attempt per (work_order, mac1)
CREATE OR REPLACE VIEW v_first_pass_yield AS
SELECT DISTINCT ON (work_order, mac1)
    id, work_order, mac1, result, start_time
FROM test_results
WHERE mac1 IS NOT NULL
  AND mac1_source != 'unknown'
ORDER BY work_order, mac1, start_time ASC;

-- Latest-result yield: latest attempt per (work_order, mac1)
CREATE OR REPLACE VIEW v_latest_result_yield AS
SELECT DISTINCT ON (work_order, mac1)
    id, work_order, mac1, result, start_time
FROM test_results
WHERE mac1 IS NOT NULL
  AND mac1_source != 'unknown'
ORDER BY work_order, mac1, start_time DESC;

-- Any-pass yield: PASS wins if any attempt passed
CREATE OR REPLACE VIEW v_any_pass_yield AS
SELECT DISTINCT ON (work_order, mac1)
    id, work_order, mac1, result, start_time
FROM test_results
WHERE mac1 IS NOT NULL
  AND mac1_source != 'unknown'
ORDER BY work_order, mac1,
    CASE WHEN result = 'PASS' THEN 0 ELSE 1 END,
    start_time DESC;

-- Retry units: MACs with more than one attempt
CREATE OR REPLACE VIEW v_retry_units AS
SELECT
    work_order,
    mac1,
    COUNT(*) AS attempt_count,
    COUNT(*) FILTER (WHERE result = 'PASS') AS pass_count,
    COUNT(*) FILTER (WHERE result = 'FAIL') AS fail_count,
    COUNT(*) FILTER (WHERE result = 'STOP') AS stop_count,
    MIN(start_time) AS first_attempt,
    MAX(start_time) AS last_attempt
FROM test_results
WHERE mac1 IS NOT NULL
  AND mac1_source != 'unknown'
GROUP BY work_order, mac1
HAVING COUNT(*) > 1;

-- Fail summary: group by fail step and category
CREATE OR REPLACE VIEW v_fail_summary AS
SELECT
    work_order,
    fail_step_name,
    fail_category,
    COUNT(*) AS fail_count,
    fail_message
FROM test_results
WHERE result = 'FAIL'
  AND fail_step_name IS NOT NULL
GROUP BY work_order, fail_step_name, fail_category, fail_message
ORDER BY fail_count DESC;
