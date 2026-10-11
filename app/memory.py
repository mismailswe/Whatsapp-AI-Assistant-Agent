"""Per-contact conversation memory, stored in SQLite so it survives restarts."""
from app import db

MAX_TURNS_PER_CONTACT = 8  # 8 user+assistant exchanges = 16 messages of context
_KEEP = MAX_TURNS_PER_CONTACT * 2


def add_user_message(contact_id: str, text: str, agent_id: int = db.LEGACY_AGENT_ID) -> None:
    db.add_message(contact_id, "user", text, keep=_KEEP, agent_id=agent_id)


def add_assistant_message(contact_id: str, text: str, agent_id: int = db.LEGACY_AGENT_ID) -> None:
    db.add_message(contact_id, "assistant", text, keep=_KEEP, agent_id=agent_id)


def get_history(contact_id: str, agent_id: int = db.LEGACY_AGENT_ID) -> list[dict]:
    return db.get_messages(contact_id, _KEEP, agent_id=agent_id)


def clear(contact_id: str, agent_id: int = db.LEGACY_AGENT_ID) -> None:
    db.clear_messages(contact_id, agent_id=agent_id)
