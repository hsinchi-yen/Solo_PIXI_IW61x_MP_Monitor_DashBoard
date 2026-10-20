"""Sign-in, uploader accounts and administrator-only data changes."""
from __future__ import annotations

import hmac
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

import auth
import auth_utils
import config
from db import db
from errors import ApiError, Msg, bad_request, not_found

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
router = APIRouter(prefix="/api/admin", tags=["admin"])

USER_FIELDS = ("username", "display_name", "department", "email", "enabled", "created_at", "created_by",
               "updated_at", "password_changed_at", "last_login_at", "last_login_ip")


class LoginBody(BaseModel):
    username: str = Field("", max_length=64)
    password: str = Field("", max_length=256)


class LoginResult(BaseModel):
    ok: bool = True
    token: str
    role: Literal["admin", "uploader"]
    username: str
    display_name: Optional[str] = None
    expires_at: datetime


class ChangePasswordBody(BaseModel):
    old_password: str = Field("", max_length=256)
    new_password: str = Field("", max_length=256)


class UserCreate(BaseModel):
    username: str = Field(..., max_length=64)
    display_name: str = Field("", max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=200)
    password: str = Field("", max_length=256)


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=200)
    enabled: Optional[bool] = None


class PasswordBody(BaseModel):
    password: str = Field("", max_length=256)


class TargetItem(BaseModel):
    work_order: str = Field(..., min_length=1, max_length=64)
    target_total: int = Field(..., ge=0, le=10_000_000)


class TargetsBody(BaseModel):
    targets: list[TargetItem] = Field(default_factory=list)


def _rows(cur):
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


# ─── Sign-in ─────────────────────────────────────────────────────────────────

@auth_router.post("/login", response_model=LoginResult)
def login(request: Request, body: LoginBody):
    """Sign in as the administrator or an uploader. The token is valid for SESSION_TTL_HOURS."""
    raw_user = body.username.strip()
    with db() as conn, conn.cursor() as cur:
        if raw_user == config.ADMIN_USER:
            username, role, display_name = config.ADMIN_USER, "admin", config.ADMIN_USER
            ok = hmac.compare_digest(body.password.encode(), config.ADMIN_PASS.encode())
            reason = None if ok else "bad_password"
        else:
            username, role = auth_utils.normalize_username(raw_user), "uploader"
            cur.execute("SELECT display_name, password_hash, enabled FROM app_users WHERE username = %s", (username,))
            user = cur.fetchone()
            ok = auth_utils.verify_password(body.password, user[1] if user else auth_utils.dummy_hash())
            display_name = user[0] if user else None
            if not user:
                ok, reason = False, "unknown_user"
            elif not ok:
                reason = "bad_password"
            elif not user[2]:
                ok, reason = False, "disabled"
            else:
                reason = None
        if not ok:
            auth.auth_log(conn, request, raw_user, "login", False, reason)
            conn.commit()
            if reason == "disabled":
                raise ApiError(403, Msg("This account is disabled. Please contact the administrator.",
                                        "此帳號已停用，請聯絡管理者"), "disabled")
            raise ApiError(401, Msg("Wrong username or password.", "帳號或密碼錯誤"), "bad_credentials")

        cur.execute("DELETE FROM app_sessions WHERE expires_at < NOW() - INTERVAL '7 days'")
        token = auth_utils.new_session_token()
        cur.execute("""
            INSERT INTO app_sessions (token_hash, username, role, cred_fp, expires_at, client_ip)
            VALUES (%s, %s, %s, %s, NOW() + make_interval(secs => %s), %s)
            RETURNING expires_at
        """, (auth_utils.token_hash(token), username, role,
              auth.admin_fingerprint() if role == "admin" else None,
              config.SESSION_TTL_HOURS * 3600, auth.client_ip(request)))
        expires_at = cur.fetchone()[0]
        if role == "uploader":
            cur.execute("UPDATE app_users SET last_login_at = NOW(), last_login_ip = %s WHERE username = %s",
                        (auth.client_ip(request), username))
        auth.auth_log(conn, request, username, "login", True)
    return {"ok": True, "token": token, "role": role, "username": username,
            "display_name": display_name, "expires_at": expires_at}


@auth_router.post("/logout")
def logout(request: Request, token: str = Depends(auth.token_header)):
    identity = auth.session_identity(token)
    if identity:
        with db() as conn, conn.cursor() as cur:
            cur.execute("UPDATE app_sessions SET revoked_at = NOW() WHERE token_hash = %s", (identity["token_hash"],))
            auth.auth_log(conn, request, identity["username"], "logout", True)
    return {"ok": True}


@auth_router.get("/me")
def me(user=Depends(auth.current_user)):
    return auth.public_identity(user)


@auth_router.post("/change-password")
def change_password(request: Request, body: ChangePasswordBody, user=Depends(auth.current_user)):
    """Uploaders change their own password; their other sessions are signed out, this one stays."""
    if user["role"] != "uploader":
        raise bad_request(Msg("The administrator password is set on the server and cannot be changed here.",
                              "管理者密碼由伺服器環境變數設定，無法在網頁上修改"), "admin_password")
    error = auth_utils.password_error(body.new_password)
    if error:
        raise bad_request(error, "weak_password")
    if body.new_password == body.old_password:
        raise bad_request(Msg("The new password must differ from the current one.", "新密碼不可以和舊密碼相同。"),
                          "same_password")
    username = user["username"]
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT password_hash FROM app_users WHERE username = %s", (username,))
        row = cur.fetchone()
        if not row or not auth_utils.verify_password(body.old_password, row[0]):
            auth.auth_log(conn, request, username, "change_password", False, "bad_password")
            conn.commit()
            raise bad_request(Msg("The current password is incorrect.", "舊密碼不正確。"), "bad_password")
        cur.execute("""
            UPDATE app_users SET password_hash = %s, password_changed_at = NOW(), updated_at = NOW()
            WHERE username = %s
        """, (auth_utils.hash_password(body.new_password), username))
        auth.revoke_sessions(conn, username, keep_token_hash=user["token_hash"])
        auth.audit(conn, request, username, "change_password", username)
        auth.auth_log(conn, request, username, "change_password", True)
    return {"ok": True}


@router.post("/login", response_model=LoginResult, include_in_schema=False)
def admin_login(request: Request, body: LoginBody):
    """Older static pages signed in here; it is the same login as POST /api/auth/login."""
    return login(request, body)


# ─── Uploader accounts (administrator) ───────────────────────────────────────

@router.get("/users")
def list_users(_admin=Depends(auth.require_admin)):
    with db() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(USER_FIELDS)} FROM app_users ORDER BY enabled DESC, username")
        return {"items": _rows(cur)}


@router.post("/users")
def create_user(request: Request, body: UserCreate, admin=Depends(auth.require_admin)):
    username = auth_utils.normalize_username(body.username)
    profile, error = auth_utils.clean_profile(body.display_name, body.department, body.email)
    error = auth_utils.username_error(username, reserved=[config.ADMIN_USER]) or error \
        or auth_utils.password_error(body.password)
    if error:
        raise bad_request(error, "invalid_user")
    with db() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO app_users (username, display_name, department, email, password_hash, created_by,
                                   password_changed_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (username) DO NOTHING
        """, (username, profile["display_name"], profile["department"], profile["email"],
              auth_utils.hash_password(body.password), admin["username"]))
        if cur.rowcount == 0:
            raise bad_request(Msg(f"Username {username} already exists.", f"帳號 {username} 已存在。"),
                              "duplicate_user")
        auth.audit(conn, request, admin["username"], "create", username, profile)
    return {"ok": True, "username": username}


@router.patch("/users/{username}")
def update_user(username: str, request: Request, body: UserUpdate, admin=Depends(auth.require_admin)):
    """Edit the profile and/or enable/disable. Disabling signs the account out everywhere."""
    username = auth_utils.normalize_username(username)
    sent = body.model_dump(exclude_unset=True)
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT display_name, department, email, enabled FROM app_users WHERE username = %s",
                    (username,))
        rows = _rows(cur)
        if not rows:
            raise not_found(Msg("Account not found.", "找不到這個帳號。"), "no_user")
        before = rows[0]
        merged = {k: sent.get(k, before[k]) for k in ("display_name", "department", "email")}
        profile, error = auth_utils.clean_profile(merged["display_name"], merged["department"], merged["email"])
        if error:
            raise bad_request(error, "invalid_user")
        enabled = before["enabled"] if sent.get("enabled") is None else bool(sent["enabled"])
        changed = {k: (before[k], profile[k]) for k in profile if before[k] != profile[k]}
        if changed:
            auth.audit(conn, request, admin["username"], "update", username,
                       {"before": {k: v[0] for k, v in changed.items()}, "after": {k: v[1] for k, v in changed.items()}})
        if enabled != before["enabled"]:
            auth.audit(conn, request, admin["username"], "enable" if enabled else "disable", username)
            if not enabled:
                auth.revoke_sessions(conn, username)
        cur.execute("""
            UPDATE app_users SET display_name = %s, department = %s, email = %s, enabled = %s, updated_at = NOW()
            WHERE username = %s
        """, (profile["display_name"], profile["department"], profile["email"], enabled, username))
    return {"ok": True, "username": username, "enabled": enabled}


@router.post("/users/{username}/reset-password")
def reset_password(username: str, request: Request, body: PasswordBody, admin=Depends(auth.require_admin)):
    username = auth_utils.normalize_username(username)
    error = auth_utils.password_error(body.password)
    if error:
        raise bad_request(error, "weak_password")
    with db() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE app_users SET password_hash = %s, password_changed_at = NOW(), updated_at = NOW()
            WHERE username = %s
        """, (auth_utils.hash_password(body.password), username))
        if cur.rowcount == 0:
            raise not_found(Msg("Account not found.", "找不到這個帳號。"), "no_user")
        auth.revoke_sessions(conn, username)
        auth.audit(conn, request, admin["username"], "reset_password", username)
    return {"ok": True}


# ─── Data changes (administrator) ────────────────────────────────────────────

@router.get("/alignment-targets")
def get_alignment_targets():
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT work_order, target_total FROM alignment_targets ORDER BY work_order")
        return {"targets": [{"work_order": row[0], "target_total": row[1]} for row in cur.fetchall()]}


@router.post("/alignment-targets")
def set_alignment_targets(request: Request, body: TargetsBody, admin=Depends(auth.require_admin)):
    with db() as conn, conn.cursor() as cur:
        for item in body.targets:
            cur.execute(
                """
                INSERT INTO alignment_targets (work_order, target_total, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (work_order) DO UPDATE
                SET target_total = EXCLUDED.target_total, updated_at = NOW()
                """,
                (item.work_order, item.target_total),
            )
        auth.audit(conn, request, admin["username"], "set_alignment_targets", None,
                   {t.work_order: t.target_total for t in body.targets})
    return {"status": "ok"}


@router.delete("/records/{record_id}")
def delete_record(record_id: int, request: Request, confirm: str = Query(""), admin=Depends(auth.require_admin)):
    require_delete_confirmation(confirm)
    with db() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM test_results WHERE id = %s", (record_id,))
        deleted = cur.rowcount
        auth.audit(conn, request, admin["username"], "delete_record", str(record_id))
    return {"deleted": deleted}


def require_delete_confirmation(confirm: str) -> None:
    if confirm != "DELETE":
        raise bad_request(Msg("Deleting needs confirm=DELETE.", "刪除前需要確認（confirm=DELETE）。"), "confirm_required")
