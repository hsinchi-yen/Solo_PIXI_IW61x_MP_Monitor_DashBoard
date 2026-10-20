"""Settings, read once from the environment (docker-compose passes them from .env)."""
import logging
import os

log = logging.getLogger("iw.config")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://pixi:pixipass@localhost:5434/pixi_test")
DB_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "8"))          # per uvicorn worker
SLOW_QUERY_MS = int(os.environ.get("SLOW_QUERY_MS", "500"))

# Administrator account (DB Tweak, alignment targets, accounts). Uploader accounts live in app_users.
ADMIN_USER = os.environ.get("ADMIN_USER", "pixi")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "pixipass")
SESSION_TTL_HOURS = float(os.environ.get("SESSION_TTL_HOURS", "8"))

# Browser origins allowed to call the API directly (comma separated). Empty: same origin only.
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]

# Postgres advisory lock keys (shared by both uvicorn workers)
SCHEMA_LOCK_KEY = 7461001


def warn_on_defaults():
    if ADMIN_PASS == "pixipass":
        log.warning("ADMIN_PASS is the built-in default; set ADMIN_PASS in .env on any shared server")
