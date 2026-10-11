"""Company admin: register organization, manage agents, WhatsApp QR linking."""
import base64
import io

import httpx
import os
import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import db, users
from app.auth_context import SessionUser, require_session_user

router = APIRouter()


class RegisterOrgRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=120)
    admin_name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=8, max_length=200)


class EmailLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=200)
    remember: bool = True


class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=8, max_length=200)


@router.post("/api/org/register")
def register_org(body: RegisterOrgRequest):
    try:
        return users.register_organization(body.company_name, body.admin_name, body.email, body.password)
    except ValueError as err:
        raise HTTPException(400, str(err)) from err


@router.get("/api/org/exists")
def org_exists():
    exists = users.org_count() > 0
    multi = os.getenv("ALLOW_MULTI_ORG", "").lower() in ("1", "true", "yes")
    return {"exists": exists, "can_register": (not exists) or multi}


@router.get("/api/me", dependencies=[Depends(require_session_user)])
def me(user: SessionUser = Depends(require_session_user)):
    return {
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "org_name": user.org_name,
        "user_id": user.user_id,
    }


@router.get("/api/agents", dependencies=[Depends(require_session_user)])
def list_agents(user: SessionUser = Depends(require_session_user)):
    if not user.is_admin():
        raise HTTPException(403, "Admin only.")
    return {"agents": users.list_agents(user.org_id)}


@router.post("/api/agents", dependencies=[Depends(require_session_user)])
def add_agent(body: CreateAgentRequest, user: SessionUser = Depends(require_session_user)):
    if not user.is_admin():
        raise HTTPException(403, "Admin only.")
    try:
        agent = users.create_agent(user.org_id, body.name, body.email, body.password)
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    return agent


def _qr_png_data_url(text: str) -> str:
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _bridge_url(path: str) -> str:
    base = os.getenv("BRIDGE_API_URL", "http://127.0.0.1:3001").rstrip("/")
    return f"{base}{path}"


def _require_agent(user: SessionUser) -> str:
    if user.role != "agent":
        raise HTTPException(403, "Only customer-service agents link a WhatsApp account here.")
    return str(user.user_id)


@router.post("/api/whatsapp/connect", dependencies=[Depends(require_session_user)])
async def whatsapp_connect(user: SessionUser = Depends(require_session_user)):
    """Start (or refresh) this user's WhatsApp session in the bridge."""
    agent_key = _require_agent(user)
    token = os.getenv("BRIDGE_API_TOKEN", "")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            st = await client.get(
                _bridge_url(f"/sessions/{agent_key}/status"),
                headers={"x-bridge-token": token},
            )
            if st.status_code == 200 and st.json().get("state") == "logged_out":
                await client.post(
                    _bridge_url("/sessions/reset"),
                    json={"agent_id": agent_key},
                    headers={"x-bridge-token": token},
                )
            r = await client.post(
                _bridge_url("/sessions/start"),
                json={"agent_id": agent_key},
                headers={"x-bridge-token": token},
            )
    except httpx.HTTPError as err:
        raise HTTPException(502, "Can't reach WhatsApp bridge. Is npm start running?") from err
    if r.status_code != 200:
        raise HTTPException(502, f"Bridge error: {r.text[:160]}")
    return r.json()


@router.get("/api/whatsapp/status", dependencies=[Depends(require_session_user)])
async def whatsapp_status(user: SessionUser = Depends(require_session_user)):
    if user.role == "admin":
        return {"state": "admin", "ready": True, "qr": None}
    agent_key = _require_agent(user)
    token = os.getenv("BRIDGE_API_TOKEN", "")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                _bridge_url(f"/sessions/{agent_key}/status"),
                headers={"x-bridge-token": token},
            )
    except httpx.HTTPError as err:
        raise HTTPException(502, "Can't reach WhatsApp bridge.") from err
    if r.status_code != 200:
        return {"state": "offline", "ready": False, "qr": None}
    return r.json()


@router.get("/api/whatsapp/qr", dependencies=[Depends(require_session_user)])
async def whatsapp_qr(user: SessionUser = Depends(require_session_user)):
    agent_key = _require_agent(user)
    token = os.getenv("BRIDGE_API_TOKEN", "")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                _bridge_url(f"/sessions/{agent_key}/qr"),
                headers={"x-bridge-token": token},
            )
    except httpx.HTTPError as err:
        raise HTTPException(502, "Can't reach WhatsApp bridge.") from err
    if r.status_code != 200:
        raise HTTPException(502, "Could not get QR code.")
    data = r.json()
    if data.get("qr"):
        data["qr_image"] = _qr_png_data_url(data["qr"])
    return data
