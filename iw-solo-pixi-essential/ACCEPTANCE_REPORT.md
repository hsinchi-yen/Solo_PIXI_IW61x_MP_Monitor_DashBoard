# IW Solo PIXI Essential Acceptance Report

Date: 2026-07-24

## Dataset

- Source: `rawlogs/5101-260715003/`
- Work order: `5101-260715003`
- Accepted legacy log files: 180
- Ignored non-run text files: summary/template files that do not match `YYYYMMDD_HHMMSS_MAC1_MAC2_RESULT.txt`

## Parser Dry Run

```json
{
  "total_files": 180,
  "results": {
    "PASS": 121,
    "FAIL": 13,
    "STOP": 46
  },
  "unknown_mac_files": 7,
  "work_orders": {
    "5101-260715003": 180
  },
  "total_measurements": 196661,
  "errors": []
}
```

## API Upload

- First browser-upload API run:
  - total: 180
  - uploaded: 180
  - duplicates: 0
  - rejected: 0
  - warnings: 15
- Second browser-upload API run:
  - total: 180
  - uploaded: 0
  - duplicates: 180
  - rejected: 0

## Desktop GUI Uploader Verification

- Application: `log_iw_uploader_app.py`
- Window title: `IW Solo PIXI Log Uploader`
- Runtime: Python 3.12, PyQt5 5.15.11, psycopg2-binary 2.9.12.
- Reproducible dependencies are declared in `requirements-desktop.txt` and the
  installation command is documented in `README.md`.
- The GUI `Test Connection` action connected successfully to PostgreSQL on
  port 5434.
- A real PyQt event loop and `UploadWorker` processed all 180 files from
  `rawlogs/5101-260715003/` against a newly created isolated database:
  - first run: uploaded 180, duplicates 0, rejected 0, warnings 15;
  - second run: uploaded 0, duplicates 180, rejected 0, warnings 0.
- The GUI table showed 180 `uploaded` statuses on the first run and 180
  `duplicate` statuses on the second run.
- Isolated database verification:
  - test results: 180;
  - measurements: 196,661;
  - data-quality issues: 15;
  - PASS / FAIL / STOP: 121 / 13 / 46.
- The production `pixi_test` database remained unchanged at 180 results and
  196,661 measurements. Both isolated acceptance databases were removed after
  verification.
- Acceptance screenshot: `UPLOADER_ACCEPTANCE.png`.
- A defect found during this verification was fixed: schema initialization now
  uses the database URL selected in the GUI. Previously, a fresh custom
  database could receive the upload connection while its schema was
  incorrectly initialized in the default database.

## Database Verification

```text
work_order      result  count
5101-260715003  FAIL    13
5101-260715003  PASS    121
5101-260715003  STOP    46

unknown_mac: 7
measurements: 196661
```

## API Verification

- `http://localhost:8003/health`: 200, database connected
- `http://localhost:8004/health`: 200, database connected
- `http://localhost:8003/docs`: 200
- `/api/summary`:
  - main Raw KPI (any-pass): 119 / 120, 99.17%
  - attempts: 180
  - attempt yield: 67.22%
  - first-pass unique-unit yield: 72.5%
  - latest-result unique-unit yield: 99.17%
  - any-pass unique-unit yield: 99.17%
  - unknown-MAC attempts: 7
  - unique units excluding unknown MAC: 120

## Reference UI Parity

- The IW dashboard uses the reference project's single-page shell and page
  switching model.
- The reference and IW inline CSS blocks are byte-for-byte identical:
  - bytes: 41,772
  - SHA-256:
    `d7adf3ac6d4131d2ebf8e86543ca1a4881de6500115f4afb50eacc6c15770e16`
- Chart.js 4.4.1 and marked 12.0.1 are vendored under `static/vendor/` for
  offline use.
- Reference pages retained: Dashboard, Work Orders, Fail List, BT Analysis,
  WiFi Analysis, Advanced Analytics, Fail Analysis, DB Tweak, Data Alignment.
- IW-only page added in the same design language: Log Upload.
- Old multi-page URLs redirect to the matching single-page tab.
- Acceptance screenshot: `ACCEPTANCE_DASHBOARD_1440x900.png`.

## Aligned vs Raw KPI

- Raw KPI uses IW any-pass unit semantics and excludes unknown-MAC attempts.
- Configured target for `5101-260715003`: 120.
- Tested unique units: 120.
- Gap (untested): 0.
- Aligned PASS / FAIL / STOP / Total: 119 / 1 / 0 / 120.
- Aligned Yield: 99.17%.
- STOP-to-PASS informational units: 22.

## Management Verification

- Data Alignment target read/write works without DB Tweak authentication.
- DB Tweak login works with expiring signed tokens.
- DB Tweak lists 180 records over four 50-row pages.
- Authenticated raw-log download succeeds.
- Authenticated Reparse updated 180 / 180 records and preserved the row count.
- Individual TXT uploads without a Work Order are rejected with HTTP 400.
- Browser upload with Work Order reports the expected duplicate result.

## Local LLM Verification

- Endpoint: environment-configurable `LLM_API_BASE`.
- Model: `Qwen3.6-35B-A3B-Q6_K`.
- `/api/llm-status`: connected.
- A real Traditional Chinese carousel summary for `5101-260715003` completed
  successfully (1,210 characters).

## Browser Smoke Test

Headless Chrome exercised every single-page tab through Nginx with no console
or page errors:

- Dashboard (Yield doughnut, hourly trend, Aligned and Raw KPI)
- Work Orders
- Fail List
- BT Analysis
- WiFi Analysis, including HE/11ax EVM
- Advanced Analytics
- Fail Analysis
- DB Tweak login, paging, and raw-log download
- Data Alignment target save
- Log Upload duplicate report

Additional browser checks:

- Theme switching: Blue, Cyber, Space.
- Cascading Day filter selected `2026-07-21`; the Dashboard reloaded to 84
  any-pass units and 100% yield, and all analysis tabs stayed operational.
- Responsive widths: 320, 768, 1024, 1440 pixels, without horizontal body
  overflow.
- All 17 charts expose `role="img"` and a descriptive accessible name.
- Legacy URL `/wifi-analysis.html` redirects to `/?page=wifi`.

## Regression Revalidation

- Full unit suite: 13 / 13 passed.
- Python compile check: `api`, `ingestion`, `parsers`, uploader, and tests
  passed `compileall`.
- Parser dry run: 180 files, 196,661 measurements, no parse errors.
- Docker Compose configuration validated and the current API image was rebuilt.
- Post-rebuild services:
  - API: healthy on port 8003;
  - Nginx: serving and proxying health on port 8004;
  - PostgreSQL: healthy on port 5434.
- Post-rebuild API regression:
  - health: 200, DB connected, 180 results;
  - docs: 200;
  - summary: 119 / 120 PASS, 99.17% yield, 180 attempts;
  - data quality: 7 unknown-MAC attempts;
  - local LLM status: connected.
- Browser regression repeated after the uploader fix:
  - all ten pages opened;
  - Blue, Cyber, and Space themes switched correctly;
  - `2026-07-21` filter returned 84 units and 100% yield;
  - DB Tweak login returned page 1 / 4 with 50 rows and 180 total records;
  - 320, 768, 1024, and 1440 pixel widths had no body overflow;
  - console errors: 0;
  - page errors: 0.

## Notes

- IW416 remains out of scope for this release.
