"""PostgreSQL schedules store using psycopg3.

Replaces the JSON-file storage on Render's free tier, where the disk is
ephemeral and schedules disappear after the 15-minute cold-start. The module
is imported only when DATABASE_URL is set; the JSON file path remains the
fallback for local dev.

Schema is created on first import (idempotent), so an empty Neon database
works without manual setup. The connection uses Neon's pooled endpoint
recommended for serverless-style workers.
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
    import psycopg  # imported lazily so the import is optional

    conn = psycopg.connect(_parse_database_url(), autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def _ensure_schema() -> None:
    """Create the schedules table if it does not exist yet."""
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
                    CREATE TABLE IF NOT EXISTS schedules (
                        id          TEXT PRIMARY KEY,
                        title       TEXT NOT NULL,
                        date        TEXT NOT NULL,
                        time        TEXT NOT NULL DEFAULT '',
                        description TEXT NOT NULL DEFAULT '',
                        created     TEXT NOT NULL
                    )
                    """
                )
        _INITIALIZED = True
        logger.info("Postgres schedules schema ready")


def list_schedules() -> list[dict]:
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, date, time, description, created "
                "FROM schedules ORDER BY created DESC"
            )
            rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "title": r[1],
            "date": r[2],
            "time": r[3],
            "description": r[4],
            "created": r[5],
        }
        for r in rows
    ]


def insert_schedule(schedule: dict) -> None:
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO schedules (id, title, date, time, description, created) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    schedule["id"],
                    schedule["title"],
                    schedule["date"],
                    schedule.get("time", ""),
                    schedule.get("description", ""),
                    schedule.get("created", ""),
                ),
            )


def update_schedule(schedule_id: str, updates: dict) -> dict | None:
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, date, time, description, created "
                "FROM schedules WHERE id = %s",
                (schedule_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            current = {
                "id": row[0],
                "title": row[1],
                "date": row[2],
                "time": row[3],
                "description": row[4],
                "created": row[5],
            }
            for key in ("title", "date", "time", "description"):
                if key in updates:
                    current[key] = str(updates[key]).strip()
            cur.execute(
                "UPDATE schedules SET title=%s, date=%s, time=%s, description=%s "
                "WHERE id=%s",
                (
                    current["title"],
                    current["date"],
                    current["time"],
                    current["description"],
                    schedule_id,
                ),
            )
    return current


def delete_schedule(schedule_id: str) -> bool:
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM schedules WHERE id = %s", (schedule_id,))
            return cur.rowcount > 0
