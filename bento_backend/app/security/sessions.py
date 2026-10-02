"""Opaque bearer session tokens.

Sessions are random 32-byte values; only their SHA-256 hash is stored, so a
database leak does not hand out usable tokens.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from ..config import Settings
from ..timeutil import now_tokyo, now_tokyo_iso


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(conn, employee_id: int, settings: Settings) -> dict:
    token = new_session_token()
    expires_at = now_tokyo() + timedelta(hours=settings.session_ttl_hours)
    conn.execute(
        "INSERT INTO sessions (token_hash, employee_id, created_at, expires_at)"
        " VALUES (?, ?, ?, ?)",
        (hash_token(token), employee_id, now_tokyo_iso(), expires_at.isoformat(timespec="seconds")),
    )
    return {"token": token, "expires_at": expires_at.isoformat(timespec="seconds")}


def resolve_session(conn, token: str) -> dict | None:
    """Return the employee row bound to a live session, else None."""
    row = conn.execute(
        "SELECT s.expires_at, s.revoked_at, e.id AS employee_id, e.employee_code, e.name,"
        "       e.role, e.is_active"
        "  FROM sessions s"
        "  JOIN employees e ON e.id = s.employee_id"
        " WHERE s.token_hash = ?",
        (hash_token(token),),
    ).fetchone()
    if row is None:
        return None
    if row["revoked_at"] is not None:
        return None
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
    except ValueError:
        return None
    if expires_at <= now_tokyo():
        return None
    if not row["is_active"]:
        return None
    return dict(row)


def revoke_session(conn, token: str) -> None:
    conn.execute(
        "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
        (now_tokyo_iso(), hash_token(token)),
    )