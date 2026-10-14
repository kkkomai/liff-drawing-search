"""Admin page (管理ページ) authentication.

The LIFF app authenticates through LINE (id_token -> employees table). The
admin page at /admin.html runs in a plain browser outside LINE, so it needs
its own credential model: an ``admin_users`` table (username + pbkdf2
password hash) and ``admin_sessions`` (opaque token, only its SHA-256 hash
stored — same discipline as the employee ``sessions`` table).

Password hashing uses stdlib pbkdf2_hmac (200k iterations, per-user salt)
so no extra dependency is required. Session tokens are
``secrets.token_urlsafe(32)``; only the hash reaches the database.

This module deliberately does NOT import FastAPI — it is pure crypto +
DB logic, usable from both the router and tests.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from ..db import bind
from ..timeutil import now_tokyo, now_tokyo_iso

# pbkdf2 iterations. 200k is a reasonable 2024-era default for interactive
# logins on shared infrastructure; raising it later only slows new logins.
_PBKDF2_ITERATIONS = 200_000
_TOKEN_BYTES = 32


class AdminAuthError(Exception):
    """Raised for any authentication failure (wrong password, no such
    user, expired session). The caller maps it to a 401."""


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #

def hash_password(password: str) -> str:
    """Return ``pbkdf2$<salt-hex>$<dk-hex>`` for storage."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return f"pbkdf2${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time comparison of a candidate password against a stored hash."""
    try:
        algo, salt_hex, dk_hex = stored.split("$", 2)
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _PBKDF2_ITERATIONS
        )
        return secrets.compare_digest(dk.hex(), dk_hex)
    except Exception:
        # Malformed stored hash must never raise into a login attempt.
        return False


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_admin_session(conn, admin_user_id: int, ttl_hours: int = 12) -> dict:
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = now_tokyo() + timedelta(hours=ttl_hours)
    conn.execute(
        bind(
            "INSERT INTO admin_sessions (token_hash, admin_user_id, created_at, expires_at)"
            " VALUES (?, ?, ?, ?)"
        ),
        (_hash_token(token), admin_user_id, now_tokyo_iso(),
         expires_at.isoformat(timespec="seconds")),
    )
    return {"token": token, "expires_at": expires_at.isoformat(timespec="seconds")}


def resolve_admin_session(conn, token: str) -> dict | None:
    """Return the admin user bound to a live session token, else None."""
    if not token:
        return None
    row = conn.execute(
        bind(
            "SELECT s.expires_at, s.revoked_at, u.id AS admin_user_id, u.username, u.display_name"
            "  FROM admin_sessions s"
            "  JOIN admin_users u ON u.id = s.admin_user_id"
            " WHERE s.token_hash = ?"
        ),
        (_hash_token(token),),
    ).fetchone()
    if row is None or row["revoked_at"] is not None:
        return None
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
    except ValueError:
        return None
    if expires_at <= now_tokyo():
        return None
    return dict(row)


def revoke_admin_session(conn, token: str) -> None:
    conn.execute(
        bind("UPDATE admin_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL"),
        (now_tokyo_iso(), _hash_token(token)),
    )


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #

def count_admin_users(conn) -> int:
    row = conn.execute(bind("SELECT COUNT(*) AS n FROM admin_users")).fetchone()
    return int(row["n"] or 0)


def find_admin_user(conn, username: str) -> dict | None:
    row = conn.execute(
        bind("SELECT id, username, password_hash, display_name, created_at FROM admin_users WHERE username = ?"),
        (username,),
    ).fetchone()
    return dict(row) if row else None


def create_admin_user(conn, username: str, password: str, display_name: str = "") -> dict:
    """Insert a new admin user. Raises on duplicate username (unique index)."""
    conn.execute(
        bind(
            "INSERT INTO admin_users (username, password_hash, display_name, created_at)"
            " VALUES (?, ?, ?, ?)"
        ),
        (username, hash_password(password), display_name, now_tokyo_iso()),
    )
    user = find_admin_user(conn, username)
    # Never hand the hash back to callers.
    if user:
        user.pop("password_hash", None)
    return user


def update_admin_password(conn, admin_user_id: int, new_password: str) -> None:
    conn.execute(
        bind("UPDATE admin_users SET password_hash = ? WHERE id = ?"),
        (hash_password(new_password), admin_user_id),
    )
