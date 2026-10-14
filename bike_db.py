"""PostgreSQL bike-log store using psycopg3.

Mirrors schedules_db.py: a single table for cycling logs, schema bootstrapped
on first import. Reuses the same DATABASE_URL (Neon) already configured for
schedules, so no new env var is required.
"""
from __future__ import annotations

import json
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
    """Yield the shared process-wide connection (pg_pool).

    The old per-call connect paid a ~0.6s Neon handshake on every
    request; reuse answers in ~0.08s. A dead socket after a Render
    cold-start is transparently reconnected inside pg_pool.get_conn()
    via the closed/broken check, so the failure mode the fresh-connect
    design was working around is handled without the cost.
    """
    import pg_pool

    yield pg_pool.get_conn()


def _ensure_schema() -> None:
    """Create the bike_logs table if it does not exist yet."""
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
                    CREATE TABLE IF NOT EXISTS bike_logs (
                        id          TEXT PRIMARY KEY,
                        date        TEXT NOT NULL,
                        distance_km NUMERIC(6,2) NOT NULL DEFAULT 0,
                        duration_min INTEGER NOT NULL DEFAULT 0,
                        avg_speed_kmh NUMERIC(5,2) NOT NULL DEFAULT 0,
                        title       TEXT NOT NULL DEFAULT '',
                        note        TEXT NOT NULL DEFAULT '',
                        polyline    TEXT NOT NULL DEFAULT '[]',
                        created     TEXT NOT NULL
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS bike_logs_date_idx ON bike_logs (date DESC)"
                )
        _INITIALIZED = True
        logger.info("Postgres bike_logs schema ready")


def list_logs(limit: int = 200) -> list[dict]:
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, date, distance_km, duration_min, avg_speed_kmh, "
                "title, note, polyline, created "
                "FROM bike_logs ORDER BY date DESC, created DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "date": r[1],
            "distance_km": float(r[2]) if r[2] is not None else 0.0,
            "duration_min": int(r[3]) if r[3] is not None else 0,
            "avg_speed_kmh": float(r[4]) if r[4] is not None else 0.0,
            "title": r[5],
            "note": r[6],
            "polyline": r[7],
            "created": r[8],
        }
        for r in rows
    ]


def insert_log(entry: dict) -> None:
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bike_logs (id, date, distance_km, duration_min, "
                "avg_speed_kmh, title, note, polyline, created) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    entry["id"],
                    entry["date"],
                    entry.get("distance_km", 0),
                    entry.get("duration_min", 0),
                    entry.get("avg_speed_kmh", 0),
                    entry.get("title", ""),
                    entry.get("note", ""),
                    entry.get("polyline", "[]"),
                    entry.get("created", ""),
                ),
            )


def update_log(log_id: str, updates: dict) -> dict | None:
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, date, distance_km, duration_min, avg_speed_kmh, "
                "title, note, polyline, created "
                "FROM bike_logs WHERE id = %s",
                (log_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            current = {
                "id": row[0],
                "date": row[1],
                "distance_km": float(row[2]) if row[2] is not None else 0.0,
                "duration_min": int(row[3]) if row[3] is not None else 0,
                "avg_speed_kmh": float(row[4]) if row[4] is not None else 0.0,
                "title": row[5],
                "note": row[6],
                "polyline": row[7],
                "created": row[8],
            }
            for key in ("date", "title", "note", "polyline"):
                if key in updates and updates[key] is not None:
                    current[key] = str(updates[key])
            for key in ("distance_km", "duration_min", "avg_speed_kmh"):
                if key in updates and updates[key] is not None:
                    try:
                        current[key] = float(updates[key])
                    except (TypeError, ValueError):
                        pass
            cur.execute(
                "UPDATE bike_logs SET date=%s, distance_km=%s, duration_min=%s, "
                "avg_speed_kmh=%s, title=%s, note=%s, polyline=%s "
                "WHERE id=%s",
                (
                    current["date"],
                    current["distance_km"],
                    current["duration_min"],
                    current["avg_speed_kmh"],
                    current["title"],
                    current["note"],
                    current["polyline"],
                    log_id,
                ),
            )
    return current


def delete_log(log_id: str) -> bool:
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bike_logs WHERE id = %s", (log_id,))
            return cur.rowcount > 0


def monthly_summary(year_month: str) -> dict:
    """Return totals for ``YYYY-MM``.

    ``year_month`` is matched against the ``date`` column's TEXT prefix.
    Returns total distance (km), total duration (min), ride count, and average speed.
    """
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), COALESCE(SUM(distance_km), 0), "
                "COALESCE(SUM(duration_min), 0) "
                "FROM bike_logs WHERE date LIKE %s",
                (year_month + "%",),
            )
            count, total_km, total_min = cur.fetchone()
            avg_speed = (float(total_km) / (float(total_min) / 60.0)) if total_min else 0.0
    return {
        "year_month": year_month,
        "count": int(count or 0),
        "total_km": float(total_km or 0),
        "total_min": int(total_min or 0),
        "avg_speed_kmh": round(avg_speed, 2),
    }
