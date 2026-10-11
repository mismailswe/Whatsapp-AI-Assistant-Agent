"""Signed dashboard sessions for company admin and agent users."""
import hashlib
import hmac
import os
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from app import users as user_store

SESSION_COOKIE = "dash_session"
SESSION_SECONDS = 30 * 24 * 3600
SHORT_SESSION_SECONDS = 12 * 3600


@dataclass
class SessionUser:
    user_id: int
    org_id: int
    role: str  # admin | agent
    email: str
    name: str
    bridge_token: str
    org_name: str = ""

    @property
    def agent_id(self) -> int:
        return self.user_id if self.role == "agent" else 0

    def is_admin(self) -> bool:
        return self.role == "admin"


def _secret() -> bytes:
    raw = os.getenv("SESSION_SECRET") or os.getenv("DASHBOARD_PASSWORD") or os.getenv("BRIDGE_API_TOKEN") or ""
    if not raw:
        raw = "dev-insecure-change-me"
    return hashlib.sha256(b"session-v2:" + raw.encode()).digest()


def sign_user_session(user_id: int, expires: int) -> str:
    payload = f"v2.{user_id}.{expires}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def parse_user_session(value: str) -> int | None:
    try:
        parts = value.split(".")
        if len(parts) != 4 or parts[0] != "v2":
            return None
        user_id = int(parts[1])
        expires = int(parts[2])
        sig = parts[3]
    except ValueError:
        return None
    if expires < time.time():
        return None
    payload = f"v2.{user_id}.{expires}"
    good = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return None
    return user_id


def load_session_user(user_id: int) -> SessionUser | None:
    row = user_store.get_user(user_id)
    if not row or not row["active"]:
        return None
    return SessionUser(
        user_id=row["id"],
        org_id=row["org_id"],
        role=row["role"],
        email=row["email"],
        name=row["display_name"],
        bridge_token=row["bridge_token"],
        org_name=row.get("org_name") or "",
    )


def require_session_user(request: Request) -> SessionUser:
    uid = parse_user_session(request.cookies.get(SESSION_COOKIE, ""))
    if uid is None:
        raise HTTPException(401, "Please sign in.")
    user = load_session_user(uid)
    if not user:
        raise HTTPException(401, "Please sign in.")
    request.state.user = user
    return user
