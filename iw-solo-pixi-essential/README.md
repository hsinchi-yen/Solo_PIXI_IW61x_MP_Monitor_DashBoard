# IW Solo PIXI Essential Dashboard

An independent production-test analytics dashboard for NXP IW61x Solo PIXI modules.

The browser UI reproduces the reference Solo PIXI single-page dashboard,
including Yield doughnut charts, Aligned vs Raw KPI, three themes, Work Order
and failure analysis, BT/Wi-Fi analytics, DB Tweak, Data Alignment, and AI
Summary. Chart.js and marked are bundled locally for offline operation.

## Quick Start

Run the following command to build and start the infrastructure:

```powershell
run.cmd
```

This will automatically build and start the Docker containers and open your browser to `http://localhost:8004`.

API health and docs:

```powershell
curl http://localhost:8003/health
start http://localhost:8003/docs
```

Dry-run the required acceptance dataset without touching PostgreSQL:

```powershell
cd iw-solo-pixi-essential
C:\Users\lance.yen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe tests\dry_run_180.py
```

Launch the standalone uploader after the Docker stack is running:

```powershell
cd iw-solo-pixi-essential
python log_iw_uploader_app.py
```

## Port Allocation

| Service | Port |
|---------|------|
| Nginx (Host) | 8004 |
| FastAPI | 8003 |
| PostgreSQL (Host) | 5434 |

## Acceptance Baseline

The first supported Work Order is `5101-260715003`, inferred from the parent folder of `rawlogs/5101-260715003/`.

| Result | Attempts |
|---|---:|
| PASS | 121 |
| FAIL | 13 |
| STOP | 46 |
| Unknown MAC | 7 |

IW416 is intentionally out of scope for this release until representative IW416 fixtures are available.

## Upload Rules

- Individual `.txt` files require an explicit Work Order in the browser upload
  form because browsers do not preserve the source folder name.
- ZIP uploads may infer Work Order from their internal folder structure.
- Duplicate files are skipped by SHA-256 content hash.

## Local AI Summary

The Compose defaults use the configured local OpenAI-compatible model service.
Override these values in the environment when needed:

```powershell
$env:LLM_API_BASE = "http://10.20.30.23:8000/v1"
$env:LLM_API_KEY = "<your-llm-api-key>"
$env:LLM_MODEL = "Qwen3.6-35B-A3B-Q6_K"
```

## Manual Docker Commands

```powershell
# Start services
docker compose up -d

# Check status
docker compose ps

# View logs
docker compose logs -f

# Stop services
docker compose down
```
