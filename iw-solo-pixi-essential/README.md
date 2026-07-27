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
python -m pip install -r requirements-desktop.txt
python log_iw_uploader_app.py
```

Uploader behavior:

- `Browse Folder` searches the selected folder and its subfolders.
- `Browse Files` accepts one or more IW log `.txt` files directly.
- Leave Work Order blank to infer it from each log's parent folder, or enter a
  value to override every selected log.
- A first upload of the 180-file acceptance dataset can take about two minutes
  because it writes 196,661 measurements. The progress bar and per-file status
  remain active while the background upload runs.
- `Uploaded 0` with duplicate files is reported as a completed upload with no
  new logs, rather than as a failure.

The browser dashboard's **Management → Log Upload** page has separate controls
for loose TXT/ZIP files and a complete Work Order folder. Folder selection
infers the Work Order from the selected root directory and ignores files such
as `summary.txt` that do not match the IW run filename format. The page shows
elapsed processing time while the request is active.

## Port Allocation

| Service | Default host port | Override |
|---------|------------------:|----------|
| Nginx | 8004 | `IW_WEB_PORT` |
| FastAPI | 8003 | `IW_API_PORT` |
| PostgreSQL | 5434 | `IW_DB_PORT` |

## Linux Deployment

Place the project in `/mnt/md127/iw-solo-pixi-essential`, then run:

```bash
cd /mnt/md127/iw-solo-pixi-essential
chmod +x system_up.sh
./system_up.sh
```

`system_up.sh` builds the images, starts all services, waits for both API and
Nginx health checks, and prints the resulting URLs. If a preferred host port
is occupied by another process or Compose project, the script searches the
next 100 ports and saves the selected values in `.system_up.env`. Existing
ports already owned by this project are retained on subsequent runs.

To request different starting ports:

```bash
IW_API_PORT=8103 IW_WEB_PORT=8104 IW_DB_PORT=5534 ./system_up.sh
```

To inspect conflict resolution without changing the running deployment:

```bash
SYSTEM_UP_PORT_CHECK_ONLY=1 \
  IW_API_PORT=8000 IW_WEB_PORT=8001 IW_DB_PORT=5432 ./system_up.sh
```

Review the deployment:

```bash
docker compose --project-name iw-solo-pixi-essential \
  --env-file .env --env-file .system_up.env ps
source .system_up.env
curl "http://127.0.0.1:${IW_API_PORT}/health"
```

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
