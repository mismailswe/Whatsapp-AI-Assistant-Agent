"""Company accounts: one admin (head) and many agent users."""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time

from app import db

PBKDF2_ROUNDS = 260_000


def _pepper() -> bytes:
    raw = os.getenv("SESSION_SECRET") or os.getenv("DASHBOARD_PASSWORD") or os.getenv("BRIDGE_API_TOKEN") or "dev-insecure"
    return hashlib.sha256(b"user-password:" + raw.encode()).digest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt + _pepper(), PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds_s, salt_hex, digest_hex = stored.split("$", 3)
        rounds = int(rounds_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt + _pepper(), rounds)
    return hmac.compare_digest(got, expected)


def new_bridge_token() -> str:
    return secrets.token_urlsafe(32)


def org_count() -> int:
    with db._db() as c:
        return c.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]


def register_organization(company_name: str, admin_name: str, email: str, password: str) -> dict:
    company_name = company_name.strip()
    admin_name = admin_name.strip()
    email = email.strip().lower()
    if not company_name or not admin_name or not email or len(password) < 8:
        raise ValueError("invalid registration")
    if org_count() > 0 and not os.getenv("ALLOW_MULTI_ORG", "").lower() in ("1", "true", "yes"):
        raise ValueError("organization already exists")
    token = new_bridge_token()
    now = time.time()
    with db._db() as c:
        try:
            cur = c.execute("INSERT INTO organizations (name, created_ts) VALUES (?, ?)", (company_name, now))
            org_id = cur.lastrowid
            cur = c.execute(
                """INSERT INTO users (org_id, email, password_hash, display_name, role, bridge_token, active, created_ts)
                   VALUES (?, ?, ?, ?, 'admin', ?, 1, ?)""",
                (org_id, email, hash_password(password), admin_name, token, now),
            )
            admin_id = cur.lastrowid
        except sqlite3.IntegrityError as err:
            raise ValueError("email already registered") from err
    db.sync_bridge_agents_file()
    return {"org_id": org_id, "user_id": admin_id, "email": email, "role": "admin", "name": admin_name}


def authenticate(email: str, password: str) -> dict | None:
    email = email.strip().lower()
    with db._db() as c:
        row = c.execute(
            """SELECT u.id, u.org_id, u.email, u.password_hash, u.display_name, u.role, u.bridge_token, u.active,
                      o.name AS org_name
               FROM users u JOIN organizations o ON o.id = u.org_id WHERE u.email = ?""",
            (email,),
        ).fetchone()
    if not row or not row["active"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return {
        "user_id": row["id"],
        "org_id": row["org_id"],
        "email": row["email"],
        "name": row["display_name"],
        "role": row["role"],
        "bridge_token": row["bridge_token"],
        "org_name": row["org_name"],
    }


def get_user(user_id: int) -> dict | None:
    with db._db() as c:
        row = c.execute(
            """SELECT u.id, u.org_id, u.email, u.display_name, u.role, u.bridge_token, u.active, o.name AS org_name
               FROM users u JOIN organizations o ON o.id = u.org_id WHERE u.id = ?""",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def user_by_bridge_token(token: str) -> dict | None:
    if not token:
        return None
    with db._db() as c:
        row = c.execute(
            """SELECT id, org_id, email, display_name, role, bridge_token, active FROM users WHERE bridge_token = ?""",
            (token,),
        ).fetchone()
    if not row or not row["active"]:
        return None
    return dict(row)


def create_agent(org_id: int, name: str, email: str, password: str) -> dict:
    name = name.strip()
    email = email.strip().lower()
    if len(password) < 8:
        raise ValueError("password too short")
    token = new_bridge_token()
    now = time.time()
    with db._db() as c:
        try:
            cur = c.execute(
                """INSERT INTO users (org_id, email, password_hash, display_name, role, bridge_token, active, created_ts)
                   VALUES (?, ?, ?, ?, 'agent', ?, 1, ?)""",
                (org_id, email, hash_password(password), name, token, now),
            )
            agent_id = cur.lastrowid
        except sqlite3.IntegrityError as err:
            raise ValueError("email already registered") from err
    db.set_setting("auto_reply", "1", agent_id)
    db.sync_bridge_agents_file()
    return {"user_id": agent_id, "email": email, "name": name, "role": "agent"}


def list_agents(org_id: int) -> list[dict]:
    with db._db() as c:
        rows = c.execute(
            """SELECT id, email, display_name, role, active, created_ts FROM users
               WHERE org_id = ? ORDER BY role DESC, display_name""",
            (org_id,),
        ).fetchall()
    return [dict(r) for r in rows]
