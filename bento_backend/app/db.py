"""Database connection: SQLite (default) or Postgres (when DATABASE_URL is set).

Operators can flip between backends purely by setting ``DATABASE_URL`` —
nothing in business code needs to change. The two backends share the
``row["col"]`` access pattern (psycopg uses ``dict_row``; sqlite3 uses
``sqlite3.Row``), and the psycopg connection auto-commits on `with conn:`
just like sqlite3.

Postgres compatibility notes
----------------------------
* SQL placeholders use ``%s`` (psycopg style) instead of SQLite's ``?``.
  ``bind()`` rewrites ``?`` → ``%s`` at call time so business code can keep
  using ``?`` for the SQLite path without manual translation per query.
* Transactional writes use the standard ``with conn:`` / ``conn.commit()`` /
  ``conn.rollback()`` API. The Postgres connection is created with
  ``autocommit=False`` so ``with conn:`` actually opens a transaction.
* ``executescript()`` is SQLite-only; the migration runner uses
  ``conn.execute(sql)`` per statement instead.
* The bento schema differs only in primary keys: SQLite uses
  ``INTEGER PRIMARY KEY AUTOINCREMENT``, Postgres uses ``SERIAL PRIMARY
  KEY``. Migrations are paired files (``001_initial.sql`` /
  ``001_initial_postgres.sql``); the runner picks the right one based on
  the active backend.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .config import get_settings
from .timeutil import now_tokyo_iso

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_local = threading.local()


def _is_postgres() -> bool:
    """True when the operator wants the Postgres backend (DATABASE_URL set)."""
    if os.environ.get("DATABASE_URL", "").strip():
        return True
    return bool(get_settings().database_url)


def _connect_sqlite(path: str) -> sqlite3.Connection:
    db_path = Path(path)
    if db_path.parent and str(db_path.parent) not in ("", "."):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _connect_postgres(url: str):
    import psycopg
    import psycopg.rows

    conn = psycopg.connect(url, autocommit=False)
    conn.row_factory = psycopg.rows.dict_row
    return conn


def get_conn():
    """Per-thread connection (SQLite or Postgres) chosen from the environment."""
    conn = getattr(_local, "conn", None)
    key = getattr(_local, "db_key", None)
    settings = get_settings()
    if _is_postgres():
        url = os.environ.get("DATABASE_URL", "").strip() or settings.database_url
        if conn is None or key != ("pg", url):
            if conn is not None:
                conn.close()
            conn = _connect_postgres(url)
            _local.conn = conn
            _local.db_key = ("pg", url)
            _local.kind = "pg"
    else:
        path = settings.database_path
        if conn is None or key != ("sqlite", path):
            if conn is not None:
                conn.close()
            conn = _connect_sqlite(path)
            _local.conn = conn
            _local.db_key = ("sqlite", path)
            _local.kind = "sqlite"
    return conn


def close_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _local.conn = None
    _local.db_key = None
    _local.kind = None


def kind() -> str:
    return getattr(_local, "kind", "sqlite")


def bind(sql: str) -> str:
    """Translate SQLite '?' placeholders to Postgres '%s' for the Postgres backend.

    Use at every ``conn.execute(sql, params)`` call site so the same SQL
    string works on both backends without manual rewriting.
    """
    if kind() == "pg":
        out = sql.replace("?", "%s")
        # Postgres has no implicit cast between TEXT and DATE. SQLite happily
        # compared `text_col >= CURRENT_DATE` because the TEXT gets coerced,
        # but psycopg raises `operator does not exist: text >= date`. Wrap
        # CURRENT_DATE / CURRENT_TIMESTAMP in to_char() so both sides are
        # TEXT, mirroring the `YYYY-MM-DD` shape that the bento schema uses.
        out = out.replace("CURRENT_DATE", "to_char(CURRENT_DATE, 'YYYY-MM-DD')")
        out = out.replace("CURRENT_TIMESTAMP", "to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD\"T\"HH24:MI:SS')")
        return out
    return sql


def lastrowid(cur) -> int | None:
    """Return the rowid of the last INSERT.

    On psycopg 3, ``cur.lastrowid`` is supported and ``INSERT ... RETURNING id``
    is the idiomatic way. On sqlite3, ``cur.lastrowid`` is the same as
    ``Cursor.lastrowid``. This helper hides the difference: callers should
    use ``RETURNING id`` and ``cur.fetchone()["id"]`` (preferred) or this
    helper as a fallback.
    """
    return getattr(cur, "lastrowid", None)


def _applied_versions(conn) -> set[str]:
    conn.execute(
        bind(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY,"
            " applied_at TEXT NOT NULL)"
        )
    )
    conn.commit()
    return {r["version"] for r in conn.execute(bind("SELECT version FROM schema_migrations"))}


def _resolve_migration_file(version: str) -> Path | None:
    """Pick the right SQL file for the active backend, falling back to the
    shared name when only one variant exists.
    """
    suffix = "_postgres" if kind() == "pg" else ""
    primary = MIGRATIONS_DIR / f"{version}_initial{suffix}.sql"
    if primary.exists():
        return primary
    alt = MIGRATIONS_DIR / f"{version}_initial.sql"
    if alt.exists():
        return alt
    return None


def migrate(conn=None) -> list[str]:
    """Apply pending migrations; returns the versions applied by this call."""
    own = conn is None
    conn = conn or get_conn()
    applied = _applied_versions(conn)
    done: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.name.split("_", 1)[0]
        if version in applied:
            continue
        is_pg_file = path.name.endswith("_postgres.sql")
        if is_pg_file and kind() != "pg":
            continue
        if not is_pg_file and kind() == "pg":
            pg_path = MIGRATIONS_DIR / f"{version}_initial_postgres.sql"
            if pg_path.exists():
                continue
        sql_text = path.read_text(encoding="utf-8")
        try:
            with conn:  # opens a transaction; auto-rollback on raise
                # exec a multi-statement SQL string one statement at a time
                # (executescript() is SQLite-only).
                for stmt in [s.strip() for s in sql_text.split(";") if s.strip()]:
                    conn.execute(stmt)
                conn.execute(
                    bind("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)"),
                    (version, now_tokyo_iso()),
                )
        except Exception:
            logger.exception("migration %s failed", path.name)
            raise
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