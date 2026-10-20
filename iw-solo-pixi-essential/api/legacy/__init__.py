"""The pre-2.0 single-file dashboard (web/legacy.html) and its API, mounted at /api/legacy.

Kept unchanged so the old page keeps working side by side with the new one until it is
retired (decision Q23). New code does not import from here.
"""
from fastapi import APIRouter

from legacy import endpoints, reference_routes, upload_routes

router = APIRouter(prefix="/api/legacy", tags=["legacy"], include_in_schema=False)
for _module in (endpoints, reference_routes, upload_routes):
    router.include_router(_module.router)
