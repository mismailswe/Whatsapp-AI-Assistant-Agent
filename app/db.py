"""SQLite store (data/assistant.db): held replies, audit log, conversation
memory, per-contact modes and settings."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "assistant.db"
BRIDGE_AGENTS_FILE = Path(__file__).resolve().parent.parent / "data" / "bridge_agents.json"
LEGACY_AGENT_ID = 0
_last_purge = 0.0


@contextmanager
def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _add_column(c, table: str, column: str, ddl: str) -> None:
    """Tiny migration helper so an existing assistant.db keeps working."""
    if column not in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init() -> None:
    with _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS held (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, chat_id TEXT,
            incoming TEXT, reply TEXT, status TEXT DEFAULT 'pending')""")
        _add_column(c, "held", "reason", "TEXT DEFAULT ''")
        _add_column(c, "held", "number", "TEXT DEFAULT ''")
        c.execute("""CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, target TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, chat_id TEXT, role TEXT, content TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages (chat_id, id)")
        c.execute("CREATE TABLE IF NOT EXISTS contact_modes (name TEXT PRIMARY KEY, mode TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        c.execute("""CREATE TABLE IF NOT EXISTS message_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, chat_id TEXT, name TEXT,
            direction TEXT, body TEXT, outcome TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_message_log_ts ON message_log (ts)")
        c.execute("""CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_ts REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, org_id INTEGER NOT NULL, email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL,
            bridge_token TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1, created_ts REAL,
            FOREIGN KEY(org_id) REFERENCES organizations(id))""")
        for table in ("held", "audit", "messages", "message_log"):
            _add_column(c, table, "agent_id", "INTEGER NOT NULL DEFAULT 0")
        _add_column(c, "held", "agent_name", "TEXT DEFAULT ''")
        _add_column(c, "message_log", "agent_name", "TEXT DEFAULT ''")
        _add_column(c, "contact_modes", "agent_id", "INTEGER NOT NULL DEFAULT 0")
        c.execute("CREATE INDEX IF NOT EXISTS idx_message_log_agent ON message_log (agent_id, ts)")
        purge_messages()
    sync_bridge_agents_file()


# ---- held replies ----

def add_held(
    chat_id: str,
    incoming: str,
    reply: str,
    reason: str = "",
    number: str | None = None,
    agent_id: int = LEGACY_AGENT_ID,
    agent_name: str = "",
) -> int:
    with _db() as c:
        cur = c.execute(
            """INSERT INTO held (ts, chat_id, incoming, reply, reason, number, agent_id, agent_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), chat_id, incoming, reply, reason, number or "", agent_id, agent_name or ""),
        )
        return cur.lastrowid


def pending(agent_id: int | None = None, org_agent_ids: list[int] | None = None) -> list[dict]:
    with _db() as c:
        if agent_id is not None:
            rows = c.execute(
                "SELECT * FROM held WHERE status = 'pending' AND agent_id = ? ORDER BY id DESC LIMIT 50",
                (agent_id,),
            )
        elif org_agent_ids is not None:
            placeholders = ",".join("?" * len(org_agent_ids))
            rows = c.execute(
                f"SELECT * FROM held WHERE status = 'pending' AND agent_id IN ({placeholders}) ORDER BY id DESC LIMIT 100",
                org_agent_ids,
            )
        else:
            rows = c.execute("SELECT * FROM held WHERE status = 'pending' ORDER BY id DESC LIMIT 50")
        return [dict(r) for r in rows]


def get(item_id: int) -> dict | None:
    with _db() as c:
        row = c.execute("SELECT * FROM held WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None


def transition(item_id: int, expected: str, new: str) -> bool:
    """Atomic status change. Moving pending -> sending first means a double
    click or a retry can never send the same reply twice."""
    with _db() as c:
        cur = c.execute("UPDATE held SET status = ? WHERE id = ? AND status = ?", (new, item_id, expected))
        return cur.rowcount == 1


# ---- audit ----

def log(kind: str, target: str = "", agent_id: int = LEGACY_AGENT_ID) -> None:
    with _db() as c:
        c.execute("INSERT INTO audit (ts, kind, target, agent_id) VALUES (?, ?, ?, ?)", (time.time(), kind, target, agent_id))


def recent_audit(limit: int = 50, agent_id: int | None = None, org_agent_ids: list[int] | None = None) -> list[dict]:
    with _db() as c:
        if agent_id is not None:
            rows = c.execute(
                "SELECT ts, kind, target, agent_id FROM audit WHERE agent_id = ? ORDER BY id DESC LIMIT ?",
                (agent_id, limit),
            )
        elif org_agent_ids is not None:
            placeholders = ",".join("?" * len(org_agent_ids))
            rows = c.execute(
                f"SELECT ts, kind, target, agent_id FROM audit WHERE agent_id IN ({placeholders}) ORDER BY id DESC LIMIT ?",
                (*org_agent_ids, limit),
            )
        else:
            rows = c.execute("SELECT ts, kind, target, agent_id FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


# ---- conversation memory (survives restarts; only the last `keep` messages per chat) ----

def add_message(chat_id: str, role: str, content: str, keep: int = 16, agent_id: int = LEGACY_AGENT_ID) -> None:
    with _db() as c:
        c.execute(
            "INSERT INTO messages (ts, chat_id, role, content, agent_id) VALUES (?, ?, ?, ?, ?)",
            (time.time(), chat_id, role, content, agent_id),
        )
        c.execute(
            """DELETE FROM messages WHERE chat_id = ? AND agent_id = ? AND id NOT IN
                     (SELECT id FROM messages WHERE chat_id = ? AND agent_id = ? ORDER BY id DESC LIMIT ?)""",
            (chat_id, agent_id, chat_id, agent_id, keep),
        )


def get_messages(chat_id: str, limit: int = 16, agent_id: int = LEGACY_AGENT_ID) -> list[dict]:
    with _db() as c:
        rows = c.execute(
            "SELECT role, content FROM messages WHERE chat_id = ? AND agent_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, agent_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def clear_messages(chat_id: str, agent_id: int = LEGACY_AGENT_ID) -> None:
    with _db() as c:
        c.execute("DELETE FROM messages WHERE chat_id = ? AND agent_id = ?", (chat_id, agent_id))


# ---- per-contact modes: "auto" (default) | "hold" (always ask me) | "ignore" ----

MODES = ("auto", "hold", "ignore")


def set_mode(name: str, mode: str, agent_id: int = LEGACY_AGENT_ID) -> None:
    if mode not in MODES:
        raise ValueError(f"bad mode: {mode}")
    key = name.strip().lower()
    with _db() as c:
        c.execute("DELETE FROM contact_modes WHERE name = ? AND agent_id = ?", (key, agent_id))
        if mode != "auto":
            c.execute("INSERT INTO contact_modes (name, mode, agent_id) VALUES (?, ?, ?)", (key, mode, agent_id))


def get_mode(name: str | None, agent_id: int = LEGACY_AGENT_ID) -> str:
    if not name:
        return "auto"
    with _db() as c:
        row = c.execute(
            "SELECT mode FROM contact_modes WHERE name = ? AND agent_id = ?",
            (name.strip().lower(), agent_id),
        ).fetchone()
        return row["mode"] if row else "auto"


def all_modes(agent_id: int = LEGACY_AGENT_ID) -> dict[str, str]:
    with _db() as c:
        return {
            r["name"]: r["mode"]
            for r in c.execute("SELECT name, mode FROM contact_modes WHERE agent_id = ?", (agent_id,))
        }


# ---- settings (global kill switch) ----

def _setting_key(key: str, agent_id: int) -> str:
    return key if agent_id == LEGACY_AGENT_ID else f"agent:{agent_id}:{key}"


def get_setting(key: str, default: str = "", agent_id: int = LEGACY_AGENT_ID) -> str:
    with _db() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (_setting_key(key, agent_id),)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str, agent_id: int = LEGACY_AGENT_ID) -> None:
    sk = _setting_key(key, agent_id)
    with _db() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (sk, value),
        )


def auto_reply_enabled(agent_id: int = LEGACY_AGENT_ID) -> bool:
    return get_setting("auto_reply", "1", agent_id) == "1"


def direct_send_enabled(agent_id: int = LEGACY_AGENT_ID) -> bool:
    """When on, "send X to Mom" in the dashboard goes out without a confirmation card."""
    return get_setting("direct_send", "1", agent_id) == "1"


# ---- message log: what people sent you and what went out (for "show me today's messages") ----
# Only chats the assistant handled are here. Stored on disk, so it is purged after
# MESSAGE_RETENTION_DAYS (default 30).

def retention_days() -> int:
    try:
        return max(1, int(os.getenv("MESSAGE_RETENTION_DAYS", "30")))
    except ValueError:
        return 30


def log_message(
    chat_id: str,
    name: str | None,
    direction: str,
    body: str,
    outcome: str = "",
    agent_id: int = LEGACY_AGENT_ID,
    agent_name: str = "",
) -> None:
    """direction: "in" (they wrote to you) or "out" (a message that was sent as you)."""
    with _db() as c:
        c.execute(
            """INSERT INTO message_log (ts, chat_id, name, direction, body, outcome, agent_id, agent_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), chat_id or "", name or "", direction, body, outcome, agent_id, agent_name or ""),
        )
    if time.time() - _last_purge > 86400:
        purge_messages()


def purge_messages(days: int | None = None) -> int:
    global _last_purge
    cutoff = time.time() - (days or retention_days()) * 86400
    with _db() as c:
        deleted = c.execute("DELETE FROM message_log WHERE ts < ?", (cutoff,)).rowcount
    _last_purge = time.time()
    return deleted


def query_messages(
    name: str | None = None,
    since: float | None = None,
    until: float | None = None,
    direction: str | None = None,
    limit: int = 100,
    agent_id: int | None = None,
    org_agent_ids: list[int] | None = None,
) -> list[dict]:
    sql = "SELECT ts, chat_id, name, direction, body, outcome, agent_id, agent_name FROM message_log WHERE 1=1"
    params: list = []
    if agent_id is not None:
        sql += " AND agent_id = ?"
        params.append(agent_id)
    elif org_agent_ids is not None:
        placeholders = ",".join("?" * len(org_agent_ids))
        sql += f" AND agent_id IN ({placeholders})"
        params.extend(org_agent_ids)
    if name:
        sql += " AND LOWER(name) = LOWER(?)"
        params.append(name)
    if since is not None:
        sql += " AND ts >= ?"
        params.append(since)
    if until is not None:
        sql += " AND ts < ?"
        params.append(until)
    if direction in ("in", "out"):
        sql += " AND direction = ?"
        params.append(direction)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with _db() as c:
        return [dict(r) for r in c.execute(sql, params)]


def org_user_ids(org_id: int) -> list[int]:
    with _db() as c:
        return [r["id"] for r in c.execute("SELECT id FROM users WHERE org_id = ? AND active = 1", (org_id,))]


def sync_bridge_agents_file() -> None:
    """Bridge reads this to map agent sessions to webhook tokens."""
    backend = os.getenv("BACKEND_URL", "http://127.0.0.1:8001/webhook")
    legacy = os.getenv("BRIDGE_API_TOKEN", "")
    agents: dict[str, dict] = {}
    if legacy:
        agents[str(LEGACY_AGENT_ID)] = {"token": legacy, "name": "Legacy"}
    with _db() as c:
        rows = c.execute(
            "SELECT id, display_name, bridge_token, role FROM users WHERE active = 1 ORDER BY id"
        ).fetchall()
    for row in rows:
        agents[str(row["id"])] = {
            "token": row["bridge_token"],
            "name": row["display_name"],
            "role": row["role"],
        }
    BRIDGE_AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    BRIDGE_AGENTS_FILE.write_text(json.dumps({"backend_url": backend, "agents": agents}, indent=2), encoding="utf-8")
