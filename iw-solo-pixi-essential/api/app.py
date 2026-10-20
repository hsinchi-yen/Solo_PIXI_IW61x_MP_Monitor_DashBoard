"""
IW61x Solo PIXI production-test dashboard — FastAPI entry point.

    routers/   HTTP endpoints, one module per area (system, analytics, auth, admin, tweak, upload, reports)
    services/  queries and business logic (statistics views, measurements, AI reports, ingest, scheduler)
    db.py      connection pool;  config.py  settings;  errors.py  {"error", "code"} responses

Shared with the desktop tools (one directory up): parsers/, ingestion/.
The dashboard itself (web/) is served by nginx; StaticFiles below only covers running
uvicorn without nginx.
"""
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

_HERE = os.path.dirname(os.path.abspath(__file__))
for _path in (_HERE, os.path.dirname(_HERE)):   # api/ and the shared packages beside it
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402
import errors  # noqa: E402
from routers import admin, analytics, auth, reports, system, tweak, upload  # noqa: E402
from services import migrations, scheduler  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("iw.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.warn_on_defaults()
    migrations.run()
    if os.environ.get("IW_DISABLE_SCHEDULER") != "1":
        scheduler.start()
    log.info("IW dashboard API %s ready", config.APP_VERSION)
    yield
    scheduler.stop()
    db.close_pool()


app = FastAPI(
    title="IW61x Solo PIXI Dashboard API",
    version=config.APP_VERSION,
    description='IW611 Wi-Fi 6 + BT module test analytics. Errors are `{"error": …, "code": …}`.',
    lifespan=lifespan,
)
errors.install(app)

if config.CORS_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-Tweak-Token"])


@app.middleware("http")
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    response.headers["X-Response-Time-Ms"] = f"{ms:.0f}"
    path = request.url.path
    if path != "/health" and path.startswith("/api"):
        level = logging.WARNING if ms >= config.SLOW_QUERY_MS * 2 else logging.INFO
        log.log(level, "%s %s → %s in %.0f ms", request.method, path, response.status_code, ms)
    return response


for module in (system, analytics, auth, admin, tweak, upload, reports):
    app.include_router(module.router)
app.include_router(tweak.jobs)

if os.path.isdir(config.WEB_DIR):
    app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
