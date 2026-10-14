"""PostgreSQL paid-leave store using psycopg3.

Mirrors schedules_db.py and bike_db.py: a single table, schema bootstrapped
on first import. Reuses the same DATABASE_URL (Neon) already configured for
schedules and bike-logs, so no new env var is required.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)

_CONNECTION_LOCK = threading.Lock()
_INITIALIZED = False


def _parse_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


@contextmanager
def _connect() -> Iterator["psycopg.Connection"]:
    """One connection per call (same reason as schedules_db / bike_db)."""
    import psycopg  # lazy
    conn = psycopg.connect(_parse_database_url(), autocommit=True)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_schema() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _CONNECTION_LOCK:
        if _INITIALIZED:
            return
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paid_leaves (
                        id          TEXT PRIMARY KEY,
                        date        TEXT NOT NULL,
                        type        TEXT NOT NULL,
                        reason      TEXT NOT NULL DEFAULT '',
                        created     TEXT NOT NULL
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS paid_leaves_date_idx ON paid_leaves (date DESC)"
                )
        _INITIALIZED = True
        logger.info("Postgres paid_leaves schema ready")


def list_paid_leaves(limit: int = 200) -> list[dict]:
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, date, type, reason, created "
                "FROM paid_leaves ORDER BY date DESC, created DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "date": r[1],
            "type": r[2],
            "reason": r[3],
            "created": r[4],
        }
        for r in rows
    ]


def insert_paid_leave(entry: dict) -> None:
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO paid_leaves (id, date, type, reason, created) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    entry["id"],
                    entry["date"],
                    entry.get("type", ""),
                    entry.get("reason", ""),
                    entry.get("created", ""),
                ),
            )


def update_paid_leave(leave_id: str, updates: dict) -> dict | None:
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, date, type, reason, created FROM paid_leaves WHERE id = %s",
                (leave_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            current = {
                "id": row[0], "date": row[1], "type": row[2],
                "reason": row[3], "created": row[4],
            }
            for key in ("date", "type", "reason"):
                if key in updates and updates[key] is not None:
                    current[key] = str(updates[key])
            cur.execute(
                "UPDATE paid_leaves SET date=%s, type=%s, reason=%s WHERE id=%s",
                (current["date"], current["type"], current["reason"], leave_id),
            )
    return current


def delete_paid_leave(leave_id: str) -> bool:
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM paid_leaves WHERE id = %s", (leave_id,))
            return cur.rowcount > 0
