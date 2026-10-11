"""Owner controls: auto-reply kill switch, per-contact modes, activity feed."""
import os
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app import contacts, db
from app.dashboard import require_auth
from app.log import log_event

router = APIRouter()
AUTH = [Depends(require_auth)]
_NAMED_KINDS = {"auto_reply", "held", "held_sent"}  # audit rows whose target is a chat id
CAIRO = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Karachi"))
RANGE_LABELS = {"today": "today", "yesterday": "yesterday", "last_7_days": "in the last 7 days", "all": "in the stored history"}


def time_bounds(label: str, now: datetime | None = None) -> tuple[float | None, float | None]:
    """(since, until) as timestamps, using Cairo midnight as the start of a day."""
    now = now or datetime.now(CAIRO)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if label == "today":
        return midnight.timestamp(), None
    if label == "yesterday":
        return (midnight - timedelta(days=1)).timestamp(), midnight.timestamp()
    if label == "last_7_days":
        return (midnight - timedelta(days=6)).timestamp(), None
    return None, None


def find_messages(
    contact: str | None,
    period: str,
    direction: str,
    limit: int = 100,
    agent_id: int | None = None,
    org_agent_ids: list[int] | None = None,
) -> list[dict]:
    """Plain database query: no model involved, so listing messages costs no tokens."""
    since, until = time_bounds(period)
    rows = db.query_messages(
        contact,
        since,
        until,
        {"received": "in", "sent": "out"}.get(direction),
        limit,
        agent_id=agent_id,
        org_agent_ids=org_agent_ids,
    )
    return [
        {
            "ts": r["ts"],
            "direction": r["direction"],
            "body": r["body"],
            "outcome": r["outcome"],
            "name": r["name"] or (r["chat_id"].split("@")[0] if r["chat_id"] else ""),
            "agent_name": r.get("agent_name") or "",
        }
        for r in rows
    ]


def _scope(request: Request, agent_filter: str = "") -> tuple[int | None, list[int] | None]:
    user = getattr(request.state, "user", None)
    if not user:
        return None, None
    if user.is_admin():
        if agent_filter.strip().isdigit():
            return int(agent_filter), None
        return None, db.org_user_ids(user.org_id)
    return user.user_id, None


class ToggleRequest(BaseModel):
    enabled: bool


class ModeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    mode: Literal["auto", "hold", "ignore"]


def _status(request: Request) -> dict:
    agent_id, org_ids = _scope(request)
    held = db.pending(agent_id, org_ids)
    aid = agent_id if agent_id is not None else db.LEGACY_AGENT_ID
    if org_ids is not None:
        aid = db.LEGACY_AGENT_ID
    auto = db.auto_reply_enabled(agent_id) if agent_id is not None else db.auto_reply_enabled()
    direct = db.direct_send_enabled(agent_id) if agent_id is not None else db.direct_send_enabled()
    if org_ids is not None:
        auto = True
        direct = db.direct_send_enabled()
    return {"auto_reply": auto, "direct_send": direct, "held": len(held)}


@router.get("/api/status", dependencies=AUTH)
def status(request: Request):
    return _status(request)


@router.post("/api/auto-reply", dependencies=AUTH)
def set_auto_reply(body: ToggleRequest, request: Request):
    agent_id, _ = _scope(request)
    aid = agent_id if agent_id is not None else db.LEGACY_AGENT_ID
    db.set_setting("auto_reply", "1" if body.enabled else "0", aid)
    db.log("auto_reply_toggled", "on" if body.enabled else "off", aid)
    log_event("auto_reply_toggled", enabled=body.enabled)
    return _status(request)


@router.post("/api/direct-send", dependencies=AUTH)
def set_direct_send(body: ToggleRequest, request: Request):
    agent_id, _ = _scope(request)
    aid = agent_id if agent_id is not None else db.LEGACY_AGENT_ID
    db.set_setting("direct_send", "1" if body.enabled else "0", aid)
    db.log("direct_send_toggled", "on" if body.enabled else "off", aid)
    log_event("direct_send_toggled", enabled=body.enabled)
    return _status(request)


@router.get("/api/messages", dependencies=AUTH)
def messages(
    request: Request,
    contact: str = "",
    period: Literal["today", "yesterday", "last_7_days", "all"] = Query("today", alias="range"),
    direction: Literal["received", "sent", "both"] = "both",
    limit: int = 100,
    agent: str = Query("", description="Admin only: filter by agent user id"),
):
    name = None
    if contact:
        name = contacts.canonical(contact)
        if not name:
            raise HTTPException(400, "Unknown contact.")
    agent_id, org_ids = _scope(request, agent)
    return {"items": find_messages(name, period, direction, limit, agent_id, org_ids)}


@router.get("/api/contact-modes", dependencies=AUTH)
def list_modes():
    modes = db.all_modes()
    return {"contacts": [{"name": n, "mode": modes.get(n.lower(), "auto")} for n in contacts.names()]}


@router.post("/api/contact-modes", dependencies=AUTH)
def change_mode(body: ModeRequest):
    canonical = contacts.canonical(body.name)
    if not canonical:
        raise HTTPException(400, "Unknown contact. Add the name to contacts.json.")
    db.set_mode(canonical, body.mode)
    db.log("mode_changed", f"{canonical}: {body.mode}")
    log_event("mode_changed", mode=body.mode)
    return {"name": canonical, "mode": body.mode}


@router.get("/api/audit", dependencies=AUTH)
def audit(request: Request, limit: int = 50):
    limit = max(1, min(limit, 200))
    agent_id, org_ids = _scope(request)
    items = []
    for r in db.recent_audit(limit, agent_id, org_ids):
        name = contacts.name_for(r["target"]) if r["kind"] in _NAMED_KINDS and r["target"] else None
        items.append({"ts": r["ts"], "kind": r["kind"], "target": "" if name else r["target"], "name": name})
    return {"items": items}
