"""Settings, read once from the environment (docker-compose passes them from .env)."""
import logging
import os

log = logging.getLogger("iw.config")

APP_VERSION = os.environ.get("APP_VERSION", "2.1.0")
APP_TZ = os.environ.get("APP_TZ", "Asia/Taipei")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://pixi:pixipass@localhost:5434/pixi_test")
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "8"))          # per uvicorn worker
SLOW_QUERY_MS = int(os.environ.get("SLOW_QUERY_MS", "500"))

# Administrator account (DB Tweak, alignment targets, accounts). Uploader accounts live in app_users.
ADMIN_USER = os.environ.get("ADMIN_USER", "pixi")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "pixipass")
SESSION_TTL_HOURS = float(os.environ.get("SESSION_TTL_HOURS", "8"))

# Browser origins allowed to call the API directly (comma separated). Empty: same origin only.
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]

# Local LLM (llama.cpp, OpenAI-compatible). Off unless LLM_ENABLED=true and LLM_API_BASE are set
# (decision Q13); production turns it on in its .env. Thinking off: much faster on the dense 27B.
LLM_ENABLED = os.environ.get("LLM_ENABLED", "false").lower() == "true"
LLM_API_BASE = os.environ.get("LLM_API_BASE", "").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "Qwen3.8-27B-UD-Q6_K_L")
LLM_ENABLE_THINKING = os.environ.get("LLM_ENABLE_THINKING", "false").lower() == "true"
LLM_REPORT_MAX_TOKENS = int(os.environ.get("LLM_REPORT_MAX_TOKENS", "1536"))
LLM_CHAT_TIMEOUT = 60          # seconds per call; the uploader falls back to the form on timeout
LLM_REPORT_TIMEOUT = 180
LLM_STATUS_PROBE_SEC = 60

# Statistics materialized views: refreshed on this interval and right after web uploads,
# undo and DB Tweak deletes. Desktop uploads show up within one interval.
STATS_REFRESH_SEC = int(os.environ.get("STATS_REFRESH_SEC", "300"))

# Line work hours [start, end) for the dashboard's Hourly Output chart (local wall-clock hours),
# the same as pixi-dashboard.
WORK_HOURS = (7, 19)

# AI reports: generated after each web upload and in a daily batch at this local time.
REPORT_DAILY_AT = os.environ.get("REPORT_DAILY_AT", "18:00")
REPORT_AUTO = os.environ.get("REPORT_AUTO", "true").lower() == "true"

# Shared quality thresholds — the dashboard colours and the AI report use the same values.
YIELD_GOOD = float(os.environ.get("YIELD_GOOD", "98"))      # ≥ green
YIELD_WARN = float(os.environ.get("YIELD_WARN", "95"))      # ≥ amber, below → red
RETRY_GOOD = float(os.environ.get("RETRY_GOOD", "3"))       # ≤ green
RETRY_WARN = float(os.environ.get("RETRY_WARN", "5"))       # ≤ amber, above → red
CPK_GOOD = 1.33
CPK_WARN = 1.0

# Log Uploader
UPLOAD_TMP_DIR = os.environ.get("UPLOAD_TMP_DIR", "/tmp/iw_uploads")
UPLOAD_TTL_SEC = 3600
UPLOAD_MAX_BYTES = 512 * 1024 * 1024

# Postgres advisory lock keys (shared by both uvicorn workers)
SCHEMA_LOCK_KEY = 7461001
UPLOAD_LOCK_KEY = 7461002
STATS_REFRESH_LOCK_KEY = 7461003
REPORT_WORKER_LOCK_KEY = 7461004
DAILY_BATCH_LOCK_KEY = 7461005

WEB_DIR = os.environ.get("WEB_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))


def llm_configured():
    return LLM_ENABLED and bool(LLM_API_BASE)


def warn_on_defaults():
    if ADMIN_PASS == "pixipass":
        log.warning("ADMIN_PASS is the built-in default; set ADMIN_PASS in .env on any shared server")
    if not llm_configured():
        log.info("AI features are off (set LLM_ENABLED=true and LLM_API_BASE to turn them on)")
