from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any

import bcrypt
import jwt

from shared import config


def _conn() -> sqlite3.Connection:
    path = config.SQLITE_PATH
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                display_name TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'local',
                created_at REAL NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def register_user(email: str, password: str, display_name: str) -> dict[str, Any]:
    init_db()
    email_norm = email.strip().lower()
    if not email_norm or not password:
        raise ValueError("Email and password are required")
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    uid = str(uuid.uuid4())
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO users (id, email, password_hash, display_name, provider, created_at) VALUES (?,?,?,?,?,?)",
            (uid, email_norm, pw_hash, display_name or email_norm, "local", time.time()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError("Email already registered")
    finally:
        conn.close()
    return {
        "id": uid,
        "email": email_norm,
        "display_name": display_name or email_norm,
        "provider": "local",
    }


def verify_user(email: str, password: str) -> dict[str, Any]:
    init_db()
    email_norm = email.strip().lower()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, email, password_hash, display_name, provider FROM users WHERE email = ?",
            (email_norm,),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["password_hash"]:
        raise ValueError("Invalid credentials")
    if not bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
        raise ValueError("Invalid credentials")
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "provider": row["provider"],
    }


def upsert_oauth_user(email: str, display_name: str, provider: str) -> dict[str, Any]:
    init_db()
    email_norm = email.strip().lower()
    conn = _conn()
    try:
        row = conn.execute("SELECT id, email, display_name FROM users WHERE email = ?", (email_norm,)).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET display_name = ?, provider = ? WHERE email = ?",
                (display_name, provider, email_norm),
            )
            conn.commit()
            return {"id": row["id"], "email": row["email"], "display_name": display_name, "provider": provider}
        uid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO users (id, email, password_hash, display_name, provider, created_at) VALUES (?,?,?,?,?,?)",
            (uid, email_norm, None, display_name, provider, time.time()),
        )
        conn.commit()
        return {"id": uid, "email": email_norm, "display_name": display_name, "provider": provider}
    finally:
        conn.close()


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    init_db()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, email, display_name, provider FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return dict(row)


def issue_token(user: dict[str, Any]) -> str:
    now = int(time.time())
    payload = {
        "sub": user["id"],
        "email": user["email"],
        "name": user.get("display_name") or user["email"],
        "provider": user.get("provider", "local"),
        "iat": now,
        "exp": now + config.JWT_EXPIRE_HOURS * 3600,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALG)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALG])


def parse_bearer(req) -> str | None:
    auth = req.headers.get("Authorization") or req.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def issue_oauth_state(provider: str) -> str:
    now = int(time.time())
    payload = {
        "typ": "oauth_state",
        "provider": provider,
        "iat": now,
        "exp": now + 600,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALG)


def verify_oauth_state(token: str, provider: str) -> None:
    payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALG])
    if payload.get("typ") != "oauth_state" or payload.get("provider") != provider:
        raise ValueError("Invalid OAuth state")
