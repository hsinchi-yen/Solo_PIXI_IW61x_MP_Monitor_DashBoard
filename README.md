# Solo PIXI IW61x MP Monitor Dashboard

A production-test monitoring dashboard for **IW61x / IW611 Solo PIXI** wireless
module testing at TechNexion. It ingests the text logs produced by the PIXI
test fixtures, stores every test attempt and measurement in PostgreSQL, and
serves a browser dashboard for tracking yield, failures, BT/Wi-Fi RF metrics,
and hourly production throughput on the line — with an optional AI/LLM
assistant that can generate a plain-language quality report for any work
order.

## Why this exists

Line operators and process engineers need a fast way to answer "how is this
work order doing right now" without digging through raw `.txt` test logs by
hand. This project turns that log-file pile into a queryable database and a
live dashboard so yield trends, failure patterns, and retry/STOP behavior are
visible at a glance — with drill-down views for BT and Wi-Fi RF measurements,
manual data-correction tools, and per-work-order AI summaries.

## What's in this repo

| Path | Purpose |
|---|---|
| `iw-solo-pixi-essential/` | The deployable application: FastAPI backend, log parser/ingestion pipeline, PostgreSQL schema, and the browser dashboard. See its [README](iw-solo-pixi-essential/README.md) for setup and usage. |
| `Utilities/` | Standalone PyQt5 desktop tool (`iw_log_splitter_app.py`) that splits a combined IW611 `Log_all.txt` capture into individual per-run log files in the naming format the dashboard's uploader expects. |
| `docs/` | Supporting specs for the utilities above. |

## Core features

- **Log ingestion** — parses IW61x/IW611 IQfact-style test logs (via browser
  upload or the standalone uploader app), extracting identity, result,
  timing, and per-metric BT/Wi-Fi measurements into PostgreSQL.
- **Dashboard** — yield trend, Aligned vs. Raw KPI views, work-order and
  retry/STOP analysis, fail-step Pareto, BT/Wi-Fi metric breakdowns, and
  hourly test-throughput tracking.
- **Data Alignment** — lets a process owner set a per-work-order production
  target so untested units and STOP→PASS retests are reflected in the yield
  numbers, not just raw pass/fail counts.
- **DB Tweak** — an authenticated admin view for browsing, correcting, and
  deleting individual test records.
- **AI quality reports** — on-demand, LLM-generated summaries (Traditional
  Chinese or English) of a work order's yield, retry rate, and top failure
  causes, via any OpenAI-compatible chat-completions endpoint.

## Getting started

See [`iw-solo-pixi-essential/README.md`](iw-solo-pixi-essential/README.md) for
the full quick-start (Docker Compose stack, port layout, upload rules, and AI
summary configuration).

```powershell
cd iw-solo-pixi-essential
run.cmd
```

Then open `http://localhost:8004`.
