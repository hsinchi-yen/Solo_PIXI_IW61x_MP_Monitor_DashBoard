"""Request bodies and a few response models (they also document the API at /docs)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ─── Accounts ────────────────────────────────────────────────────────────────

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


# ─── Log Uploader ────────────────────────────────────────────────────────────

class ValidateBody(BaseModel):
    upload_id: str = Field("", max_length=64)
    work_order: Optional[str] = Field("", max_length=64)
    planned_qty: Optional[str | int] = None


class CommitBody(ValidateBody):
    ack_warnings: bool = False
    mode: Literal["chat", "form"] = "form"


class ChatBody(BaseModel):
    upload_id: str = Field("", max_length=64)
    messages: list = Field(default_factory=list)
    known: Optional[dict] = None
    lang: str = Field("en", max_length=8)      # UI language for the reply


# ─── AI reports ──────────────────────────────────────────────────────────────

class AskBody(BaseModel):
    messages: list = Field(default_factory=list)
    lang: str = Field("en", max_length=8)
