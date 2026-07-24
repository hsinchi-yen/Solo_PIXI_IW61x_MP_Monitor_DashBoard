from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import APIRouter, Header, HTTPException

from db import get_connection

router = APIRouter(prefix="/api/admin", tags=["admin"])
ADMIN_USER = os.getenv("ADMIN_USER", "pixi")
ADMIN_PASS = os.getenv("ADMIN_PASS", "pixipass")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "change-me-local-secret")
TOKEN_TTL_SEC = int(os.getenv("ADMIN_TOKEN_TTL_SEC", "3600"))


@router.post("/login")
def login(payload: dict):
    if payload.get("username") != ADMIN_USER or payload.get("password") != ADMIN_PASS:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    expires_at = int(time.time()) + TOKEN_TTL_SEC
    return {"token": _sign({"sub": ADMIN_USER, "exp": expires_at}), "expires_at": expires_at}


@router.get("/alignment-targets")
def get_alignment_targets():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT work_order, target_total FROM alignment_targets ORDER BY work_order")
            return {"targets": [{"work_order": row[0], "target_total": row[1]} for row in cur.fetchall()]}


@router.post("/alignment-targets")
def set_alignment_targets(payload: dict, x_admin_token: str | None = Header(default=None)):
    _require_token(x_admin_token)
    targets = payload.get("targets", [])
    with get_connection() as conn:
        with conn.cursor() as cur:
            for item in targets:
                cur.execute(
                    """
                    INSERT INTO alignment_targets (work_order, target_total, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (work_order) DO UPDATE
                    SET target_total = EXCLUDED.target_total, updated_at = NOW()
                    """,
                    (item["work_order"], int(item["target_total"])),
                )
        conn.commit()
    return {"status": "ok"}


@router.delete("/records/{record_id}")
def delete_record(record_id: int, confirm: str, x_admin_token: str | None = Header(default=None)):
    _require_token(x_admin_token)
    if confirm != "DELETE":
        raise HTTPException(status_code=400, detail="Confirmation text must be DELETE")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM test_results WHERE id = %s", (record_id,))
            deleted = cur.rowcount
        conn.commit()
    return {"deleted": deleted}


def _sign(payload: dict) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(ADMIN_SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def _require_token(token: str | None) -> dict:
    if not token or "." not in token:
        raise HTTPException(status_code=401, detail="Missing token")
    body, signature = token.split(".", 1)
    expected = _b64(hmac.new(ADMIN_SECRET.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid token")
    payload = json.loads(base64.urlsafe_b64decode(_pad(body)).decode("utf-8"))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="Expired token")
    return payload


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _pad(value: str) -> bytes:
    return (value + "=" * (-len(value) % 4)).encode("ascii")
