"""Shared Postgres connection reuse for the *_db modules.

Measured on Neon's pooler endpoint: a fresh psycopg connection costs
~0.6s (TCP + TLS handshake) while a reused one answers in ~0.08s — a
7.5x difference on every request. schedules_db / bike_db / paid_db
each used to open a fresh connection per call, so every list/edit
round-trip paid the handshake twice (once for the read, once for the
write-back).

This module keeps ONE live connection per process and transparently
reconnects when Render's cold-start kills the socket — the failure
mode the per-call-connect design was working around. The pre-use
``closed`` / ``broken`` check catches the dead socket BEFORE the next
execute() raises "the connection is closed", so callers never see the
error. With autocommit=True each execute is its own transaction, so a
failed query does not poison the shared connection.

Usage (drop-in for the old ``_connect()`` contextmanager)::

    @contextmanager
    def _connect():
        yield pg_pool.get_conn()
"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CONN = None  # module-level shared connection; None until first use


def _parse_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def get_conn():
    """Return a live connection, reconnecting when the held one is dead.

    psycopg3 connections are thread-safe (they serialize internally),
    so the same connection can serve the FastAPI threadpool.
    """
    global _CONN
    with _LOCK:
        conn = _CONN
        if conn is not None and not conn.closed and not conn.broken:
            return conn
        # Replace a dead connection (Render cold-start) or open the first one.
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            logger.info("pg_pool: reconnecting after dead connection")
        import psycopg  # lazy so the import stays optional

        conn = psycopg.connect(_parse_database_url(), autocommit=True)
        _CONN = conn
        return conn


def close_conn() -> None:
    """Close the shared connection (used by tests)."""
    global _CONN
    with _LOCK:
        if _CONN is not None:
            try:
                _CONN.close()
            except Exception:
                pass
            _CONN = None
