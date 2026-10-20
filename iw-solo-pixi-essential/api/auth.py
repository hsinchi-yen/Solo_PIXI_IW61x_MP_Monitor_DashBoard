"""Sessions and permissions.

The X-Tweak-Token header carries a random session token; only its SHA-256 is stored in
app_sessions, so there is no signing secret to leak or guess.
role 'admin'    = ADMIN_USER / ADMIN_PASS (DB Tweak, alignment targets, accounts, reparse)
role 'uploader' = an app_users row (log upload, raw-log download)

Routes declare what they need with FastAPI dependencies:
    user = Depends(current_user)     # any signed-in account, else 401
    user = Depends(require_admin)    # administrator, else 403
"""
import hashlib
import json

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

import auth_utils
import config
from db import db
from errors import ApiError, Msg

token_header = APIKeyHeader(name="X-Tweak-Token", auto_error=False,
                            description="Session token from POST /api/auth/login")


def client_ip(request: Request):
    return request.headers.get("x-real-ip") or (request.client.host if request.client else None)


def admin_fingerprint():
    """Bound into admin sessions so changing ADMIN_USER/ADMIN_PASS signs the administrator out."""
    return hashlib.sha256(f"{config.ADMIN_USER}\0{config.ADMIN_PASS}".encode()).hexdigest()[:16]


def session_identity(token):
    """Session token → {username, role, display_name, expires_at, token_hash}, or None."""
    if not token or len(token) > 128:
        return None
    digest = auth_utils.token_hash(token)
    with db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT s.username, s.role, s.cred_fp, s.expires_at, u.display_name, u.enabled
            FROM app_sessions s LEFT JOIN app_users u ON u.username = s.username
            WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > NOW()
        """, (digest,))
        row = cur.fetchone()
    if not row:
        return None
    username, role, cred_fp, expires_at, display_name, enabled = row
    if role == "admin":
        if username != config.ADMIN_USER or cred_fp != admin_fingerprint():
            return None
    elif not enabled:
        return None
    return {"username": username, "role": role, "display_name": display_name or username,
            "expires_at": expires_at, "token_hash": digest}


def current_user(token: str = Depends(token_header)):
    identity = session_identity(token)
    if not identity:
        raise ApiError(401, Msg("Please sign in (or your session has expired).", "請先登入，或登入已失效"),
                       "unauthorized")
    return identity


def require_admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise ApiError(403, Msg("Administrators only.", "此功能僅限管理者使用"), "forbidden")
    return user


def public_identity(identity):
    return {k: identity[k] for k in ("username", "role", "display_name", "expires_at")}


def auth_log(conn, request, username, event, success, reason=None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO auth_log (username, event, success, reason, client_ip, user_agent)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, ((username or "")[:64], event, success, reason, client_ip(request),
              (request.headers.get("user-agent") or "")[:200]))


def audit(conn, request, actor, action, target, detail=None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO account_audit (actor, action, target, detail, client_ip)
            VALUES (%s, %s, %s, %s, %s)
        """, (actor, action, target, json.dumps(detail, default=str) if detail else None, client_ip(request)))


def revoke_sessions(conn, username, keep_token_hash=None):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE app_sessions SET revoked_at = NOW()
            WHERE username = %s AND revoked_at IS NULL AND token_hash IS DISTINCT FROM %s
        """, (username, keep_token_hash))
