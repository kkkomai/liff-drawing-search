"""SQLite connection handling plus a tiny forward-only migration runner.

Design notes
------------
* WAL + ``busy_timeout`` so concurrent readers never block on the writer.
* Foreign keys are ON (SQLite defaults them OFF) so ``ON DELETE CASCADE``
  actually fires.
* Migrations are ``NNN_name.sql`` files in ``app/migrations`` applied in
  lexical order exactly once, tracked in ``schema_migrations``.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .config import get_settings
from .timeutil import now_tokyo_iso

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_local = threading.local()


def _connect(path: str) -> sqlite3.Connection:
    db_path = Path(path)
    if db_path.parent and str(db_path.parent) not in ("", "."):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_conn() -> sqlite3.Connection:
    """Per-thread connection to the configured database."""
    conn = getattr(_local, "conn", None)
    key = getattr(_local, "db_key", None)
    settings = get_settings()
    if conn is None or key != settings.database_path:
        if conn is not None:
            conn.close()
        conn = _connect(settings.database_path)
        _local.conn = conn
        _local.db_key = settings.database_path
    return conn


def close_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
    _local.conn = None
    _local.db_key = None


def _applied_versions(conn: sqlite3.Connection) -> set[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY,"
        " applied_at TEXT NOT NULL)"
    )
    return {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}


def migrate(conn: sqlite3.Connection | None = None) -> list[str]:
    """Apply pending migrations; returns the versions applied by this call."""
    own = conn is None
    conn = conn or get_conn()
    applied = _applied_versions(conn)
    done: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.name.split("_", 1)[0]
        if version in applied:
            continue
        with conn:  # transaction: either the whole file lands or none of it
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, now_tokyo_iso()),
            )
        done.append(path.name)
    if own:
        pass
    return done


def reset_for_tests() -> None:
    """Drop the thread-local connection (used after swapping DB paths)."""
    close_conn()


def iter_rows(cursor: sqlite3.Cursor) -> Iterator[dict]:
    for row in cursor:
        yield dict(row)


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat()