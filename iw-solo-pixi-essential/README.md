# IW61x Solo PIXI Dashboard

Production-test analytics for NXP IW61x / IW611 (Wi-Fi 6 + BT) Solo PIXI modules: yields,
failures, measurements against their spec limits, a web log uploader with an AI assistant,
and AI quality reports. Version 2.0 follows the layout and structure of the Solo PIXI
(QCA9377) dashboard v3.1, with IW611 content.

| Page | What it shows |
|---|---|
| Dashboard | Yield by latest result (headline), first-pass and any-pass yield, units vs test runs, retests, items out of spec; yield trend, runs per hour, top failing steps, units per month |
| Work Orders | One row per work order with the three yields, runs, runs without MAC, retest rate; AI report |
| Fail List | FAIL / STOP runs, paged, searchable, raw-log download (signed in) |
| Measurements | **Spec check**: every IW611 test item with limits, worst first (out of spec → Cpk → margin), click for its histogram with limit lines; BT, Wi-Fi and Other tabs |
| Fail Analysis | Failing steps (runs, units, units passed on retest), categories, messages |
| DB Maintenance | Browse / delete records, raw logs, background reparse (administrator) |
| Data Alignment | Planned quantity per work order, aligned yield (administrator edits) |
| Log Upload | Work-order zip or split run files → AI assistant (or form) → upload with progress → history / undo |
| Accounts | Uploader accounts, account audit, sign-in log (administrator) |

English by default with a one-click 中文 switch; Dark (default) and Light themes. The pre-2.0
single page and its API were removed in 2.0 (decision Q28).

## Quick Start

```bash
cd iw-solo-pixi-essential
./system_up.sh          # Linux: build, start, wait for health, print the URLs
run.cmd                 # Windows: the same, then opens http://localhost:8003
```

- Dashboard: http://localhost:8003 (one public port; nginx passes `/api`, `/health`, `/docs` to the API)
- API: http://localhost:8003/health, http://localhost:8003/docs

Settings live in `.env` next to `docker-compose.yml` (template: `.env.example`).

## Structure

```
api/            FastAPI (python 3.12)
  app.py        entry point (lifespan: schema, new tables, statistics, scheduler)
  routers/      system, analytics, auth, admin, tweak, upload (Log Upload), reports (AI)
  services/     stats, measurements, filters, ingest, upload_chat, reports, report_text,
                llm, migrations, scheduler, reparse, auth
parsers/        IW611 IQfact log parser (shared with the desktop tools)
ingestion/      repository + splitter (shared with the desktop tools)
web/            static dashboard served by nginx (ES modules, no build step)
nginx/          reverse proxy: web/ + /api
tests/          pytest (unit, API integration, contract) and browser checks
```

`docker compose up -d --build` bakes the code into the API image. For development, add the
overlay that mounts the source: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`
(then `restart api` after Python edits; `web/` is always live).

## Database

The existing tables are unchanged (`test_results`, `test_measurements`, `upload_batches`,
`data_quality_issues`, `alignment_targets`), so the desktop uploader and older data keep
working. Version 2.0 only adds:

| Object | Purpose |
|---|---|
| `app_users`, `app_sessions`, `auth_log`, `account_audit` | accounts, sign-ins, who changed what |
| `upload_log`, `upload_runs` | web upload batches (facts, checks, AI conversation) and the rows each added, for undo |
| `ai_reports` | stored AI reports (both languages, facts, number check) |
| `app_meta`, `job_runs` | refresh times, daily batch marker, reparse progress |
| `mv_unit_day`, `mv_unit` | per unit (work order + MAC1): first / latest / any-pass result |
| `metric_day`, `metric_wo_state` | per day, work order and test item: n, mean inputs, min, max, limits, fails — rebuilt only for work orders whose runs changed |

Statistics refresh every `STATS_REFRESH_SEC` (default 300 s) and right after web uploads,
undo and deletes; desktop uploads appear within one interval or after **Refresh**.

## Uploading Logs

**Web (Log Upload page)** — sign in (administrator or uploader), then drop either a work-order
zip whose station folders (30, 40, …) hold `Log_all.txt`, or split run files
(`YYYYMMDD_HHMMSS_MAC1_MAC2_RESULT.txt`, loose or zipped). The server splits `Log_all.txt`
exactly like the desktop splitter (same content hashes, so duplicates are found across both),
shows the facts, and the AI assistant asks for the work order and the planned quantity.
Rules decide, never the AI:

| Code | Rule | Level |
|---|---|---|
| R1 | Work order required, format `dddd-dddddddd…` | error |
| R2 | Planned quantity required, positive whole number | error |
| R3 | Work order differs from the one found in the files | warning (confirm) |
| R4 | Some runs already belong to another work order (skipped) | warning (confirm) |
| R5 | Planned quantity vs unique MAC1 | info |
| R6 | Runs already in this work order (skipped, re-upload) | info |
| R7 | Data Alignment target will be set / changed | info |
| R8 | Unreadable or repeated blocks skipped | info |
| R9 | Logs that are not IW611 | error |

When AI is off or does not answer, the page switches to the form with the same rules. Uploads
run in the background with progress; each batch can be undone by its uploader or the
administrator (only the rows it added are deleted, and the previous planned quantity returns).

**Desktop** — `python ..\Utilities\log_iw_uploader_app.py` (after
`python -m pip install -r requirements-desktop.txt`) writes to PostgreSQL directly on port
5434; `Utilities/iw_log_splitter_app.py` splits `Log_all.txt` files.

## Accounts and Permissions

Viewing needs no sign-in. Everything that changes data or exposes raw logs does:

| Role | Who | Can |
|---|---|---|
| Administrator | `ADMIN_USER` / `ADMIN_PASS` from `.env` | DB Maintenance (deletes need confirmation), planned quantities, reparse, accounts, every upload batch |
| Uploader | accounts created on the Accounts page | Log Upload (own batches), raw-log downloads |

Sessions are random tokens stored as SHA-256 in `app_sessions`, sent in the `X-Tweak-Token`
header, valid for `SESSION_TTL_HOURS`. **Set `ADMIN_PASS` in `.env` on any shared server.**

## AI

Off unless `.env` sets `LLM_ENABLED=true` and `LLM_API_BASE` (OpenAI-compatible, e.g. a local
llama.cpp server); `LLM_MODEL` defaults to `Qwen3.8-27B-UD-Q6_K_L`, thinking off.

- **Upload assistant** — extracts the work order (regex) and planned quantity (LLM, JSON
  schema), then explains the rule results in the UI language.
- **Quality report** — the server builds a facts table (three yields with units and runs kept
  apart, top failing steps, measurements closest to spec, previous 3 work orders, period,
  planned quantity); the model writes a summary, 3 findings and 3 recommendations in English
  and Chinese; every number must appear in the facts (one retry, then flagged). Reports are
  stored, generated after uploads and daily at `REPORT_DAILY_AT`, and can be copied,
  downloaded as Markdown, compared and asked about.

## Deploying

`scripts/deploy.sh` backs up the server (files + `pg_dump` into `oldbackup/<YYMMDD>/`), syncs the
code, runs `system_up.sh`, checks health and restores the previous files automatically on failure;
`scripts/backup_db.sh` is the daily backup (14 days). Details in Traditional Chinese:
[docs/deployguide.md](docs/deployguide.md); user guide: [docs/operator_manual.md](docs/operator_manual.md).

## Port Allocation

| Service | Default host port | Override |
|---------|------------------:|----------|
| Nginx (dashboard + API) | 8003 | `IW_WEB_PORT` |
| FastAPI | — (internal only, container port 8003) | |
| PostgreSQL | 5434 | `IW_DB_PORT` |

Until 2026-10-20 nginx used 8004 and the API 8003; `system_up.sh` moves an old two-port
`.system_up.env` to the single port automatically (the old API port is kept as the address).

`system_up.sh` searches the next 100 ports when one is taken and saves the choice in
`.system_up.env` (`SYSTEM_UP_PORT_CHECK_ONLY=1` to only check).

## Tests

```bash
# Python (unit, API integration against a throwaway database on port 5434, API contract)
docker run --rm --network host -v "$PWD/..":/w:ro -w /w/iw-solo-pixi-essential python:3.12-slim \
  sh -c 'pip install -q -r api/requirements.txt pytest httpx && python -m pytest tests/ -q'
python tests/dry_run_180.py            # parse the 180 acceptance logs without a database

# Browser (npm i playwright-core; a running stack)
node tests/browser/dashboard.js        # every page, both languages, spec check, report, admin
UPLOADER_USER=… UPLOADER_PASS=… node tests/browser/upload_assistant.js <zip> <dir>   # LOCAL ONLY: deletes and re-uploads the work order
```

## Acceptance Baseline

Work order `5101-260715003` (`rawlogs/5101-260715003/`): 180 runs — PASS 121, FAIL 13, STOP 46,
7 without MAC; 120 units; yield 99.17 % (latest), 72.50 % (first pass), 99.17 % (any pass).

IW416 is out of scope until representative IW416 fixtures are available.
