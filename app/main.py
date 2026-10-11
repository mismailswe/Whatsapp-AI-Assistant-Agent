import hmac
import os

# PyTorch + faster-whisper on macOS often link duplicate OpenMP runtimes; without
# this, the process can abort the first time Whisper loads (voice notes).
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from dotenv import load_dotenv

load_dotenv()  # first, so every module below sees the .env values (USER_NAME, tokens...)

from fastapi import FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from app import db
from app import users as user_accounts
from app.agent_graph import run_agent
from app.approvals import router as approvals_router
from app.assistant import router as assistant_router
from app.contacts import _same_number, name_for
from app.controls import router as controls_router
from app.dashboard import router as dashboard_router
from app.log import anon, log_event
from app.memory import add_assistant_message, add_user_message
from app.team import router as team_router
from app.tts import synthesize_to_base64

app = FastAPI()
app.include_router(dashboard_router)
app.include_router(assistant_router)
app.include_router(approvals_router)
app.include_router(controls_router)
app.include_router(team_router)
db.init()


def _resolve_agent(x_bridge_token: str) -> tuple[int, str, str]:
    """Returns (agent_id, agent_display_name, role)."""
    row = user_accounts.user_by_bridge_token(x_bridge_token)
    if row:
        return row["id"], row["display_name"], row["role"]
    expected = os.getenv("BRIDGE_API_TOKEN", "")
    if expected and hmac.compare_digest(x_bridge_token.encode(), expected.encode()):
        return db.LEGACY_AGENT_ID, "", "legacy"
    raise HTTPException(401, "unauthorized")


def _voice_reply_mode() -> str:
    return os.getenv("REPLY_WITH_VOICE", "false").strip().lower()


def _should_send_voice_reply(message_type: str) -> bool:
    mode = _voice_reply_mode()
    if mode in ("false", "0", "no", ""):
        return False
    if mode in ("always", "all"):
        return True
    return message_type == "voice"


ALLOW_GROUPS = os.getenv("ALLOW_GROUPS", "false") == "true"


def _load_allowed_contacts() -> set[str]:
    raw = os.getenv("ALLOWED_CONTACTS", "")
    return {c.strip() for c in raw.split(",") if c.strip()}


def is_allowed(contact_id: str, number: str | None = None) -> bool:
    if contact_id.endswith("@g.us") and not ALLOW_GROUPS:
        return False

    allowed = _load_allowed_contacts()
    if not allowed:
        return True

    candidate = (number or contact_id.split("@")[0]).strip()
    for entry in allowed:
        if "@" in entry and contact_id == entry:
            return True
        if candidate == entry or _same_number(candidate, entry):
            return True
        if number and _same_number(number, entry):
            return True
    return False


class IncomingMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(default=None, alias="from")
    body: str = ""
    type: str = "text"
    media_base64: str | None = None
    mimetype: str | None = None
    number: str | None = None


@app.post("/webhook")
async def webhook(msg: IncomingMessage, x_bridge_token: str = Header(default="")):
    try:
        agent_id, agent_name, _role = _resolve_agent(x_bridge_token)
    except HTTPException:
        raise

    if not is_allowed(msg.from_, msg.number):
        return {"action": "ignore"}

    name = name_for(msg.from_, msg.number)
    mode = db.get_mode(name, agent_id)
    if mode == "ignore":
        return {"action": "ignore"}

    try:
        result = await run_in_threadpool(
            run_agent,
            contact_id=msg.from_,
            message_type=msg.type,
            raw_text=msg.body,
            media_base64=msg.media_base64,
            agent_id=agent_id,
        )
    except Exception as err:
        log_event(
            "webhook_agent_failed",
            level="error",
            chat=anon(msg.from_),
            error=type(err).__name__,
            detail=str(err)[:200],
            message_type=msg.type,
        )
        raise HTTPException(503, "agent failed") from err

    add_user_message(msg.from_, result["resolved_text"], agent_id)

    action, reason = result["action"], result.get("hold_reason", "")
    if action == "send" and not db.auto_reply_enabled(agent_id):
        action, reason = "hold", "paused"
    elif action == "send" and mode == "hold":
        action, reason = "hold", "contact_mode"

    if action == "hold":
        db.add_held(
            msg.from_,
            result["resolved_text"],
            result["reply_text"],
            reason=reason,
            number=msg.number,
            agent_id=agent_id,
            agent_name=agent_name,
        )
        db.log("held", msg.from_, agent_id)
        db.log_message(msg.from_, name, "in", result["resolved_text"], "held", agent_id, agent_name)
        log_event("held", chat=anon(msg.from_), reason=reason)
        return {"action": "hold", "reply_text": result["reply_text"], "reason": reason}

    add_assistant_message(msg.from_, result["reply_text"], agent_id)
    db.log("auto_reply", msg.from_, agent_id)
    db.log_message(msg.from_, name, "in", result["resolved_text"], "replied", agent_id, agent_name)
    db.log_message(msg.from_, name, "out", result["reply_text"], "auto", agent_id, agent_name)
    log_event("auto_reply", chat=anon(msg.from_))

    response = {
        "action": "send",
        "reply_text": result["reply_text"],
        "reply_audio_base64": None,
        "reply_audio_mimetype": None,
    }

    if _should_send_voice_reply(msg.type):
        try:
            audio_b64, mime = await synthesize_to_base64(result["reply_text"])
            response["reply_audio_base64"] = audio_b64
            response["reply_audio_mimetype"] = mime
        except Exception as err:
            log_event("whatsapp_tts_failed", level="warning", chat=anon(msg.from_), error=type(err).__name__)

    return response


@app.get("/health")
async def health():
    return {"status": "ok"}
