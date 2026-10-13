"""PostgreSQL household-expense store using psycopg3.

Mirrors bike_db.py: a single per-user table for household bookkeeping
expenses, schema bootstrapped on first import. Reuses the same DATABASE_URL
(Neon) already configured for schedules/bike/paid-leave, so no new env var is
required.

Dates are stored as TEXT (``YYYY-MM-DD``) for the same reason as bike_logs:
the values only ever come from the client as strings, are compared with a
LIKE prefix for the month filter, and we never do date arithmetic on them in
SQL — a TEXT column avoids per-row timezone/cast surprises and keeps the
bootstrap trivial. Validation of the format happens in the API layer.

Every row is scoped to a LINE user id, so update/delete always filter by both
id and line_user_id: without the user in the WHERE clause one logged-in user
could edit another user's expenses by guessing a UUID.
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
    """Open a fresh connection per call.

    A long-lived psycopg connection does not survive a Render cold-start;
    on the first request after a 15-minute sleep cycle the held socket is
    dead and the next execute() raises ``the connection is closed``. We
    already pay one connect per operation on the other stores without
    trouble, so the same pattern is used here.
    """
    import psycopg  # imported lazily so the import is optional

    conn = psycopg.connect(_parse_database_url(), autocommit=True)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_schema() -> None:
    """Create the household_expenses table if it does not exist yet."""
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
                    CREATE TABLE IF NOT EXISTS household_expenses (
                        id          TEXT PRIMARY KEY,
                        line_user_id TEXT NOT NULL,
                        spent_on    TEXT NOT NULL,
                        item        TEXT NOT NULL DEFAULT '',
                        category    TEXT NOT NULL DEFAULT 'その他',
                        amount      INTEGER NOT NULL DEFAULT 0,
                        note        TEXT NOT NULL DEFAULT '',
                        created     TEXT NOT NULL
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS household_expenses_user_date_idx "
                    "ON household_expenses (line_user_id, spent_on)"
                )
        _INITIALIZED = True
        logger.info("Postgres household_expenses schema ready")


def _row_to_dict(r) -> dict:
    return {
        "id": r[0],
        "spent_on": r[1],
        "item": r[2],
        "category": r[3],
        "amount": int(r[4]) if r[4] is not None else 0,
        "note": r[5],
        "created": r[6],
    }


_EXPENSE_COLUMNS = (
    "SELECT id, spent_on, item, category, amount, note, created "
    "FROM household_expenses"
)


def list_expenses(line_user_id: str, month: str | None = None, date: str | None = None) -> list[dict]:
    """List a user's expenses, newest first.

    ``month`` matches the ``YYYY-MM`` prefix of spent_on via LIKE; ``date``
    matches the full ``YYYY-MM-DD`` exactly. When both are given the month
    filter is applied first and the exact date narrows it further — the
    caller (API layer) only ever passes one of the two.
    """
    _ensure_schema()
    sql = _EXPENSE_COLUMNS + " WHERE line_user_id = %s"
    params: list = [line_user_id]
    if month:
        sql += " AND spent_on LIKE %s"
        params.append(month + "%")
    if date:
        sql += " AND spent_on = %s"
        params.append(date)
    sql += " ORDER BY spent_on DESC, created DESC"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def insert_expense(entry: dict) -> None:
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO household_expenses "
                "(id, line_user_id, spent_on, item, category, amount, note, created) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    entry["id"],
                    entry["line_user_id"],
                    entry["spent_on"],
                    entry.get("item", ""),
                    entry.get("category", "その他"),
                    int(entry.get("amount", 0)),
                    entry.get("note", ""),
                    entry.get("created", ""),
                ),
            )


def update_expense(expense_id: str, line_user_id: str, updates: dict) -> dict | None:
    """Partially update one expense, returning the updated row or None.

    Both ``expense_id`` AND ``line_user_id`` go in the WHERE clause so a
    logged-in user can never modify (or even probe the existence of)
    another user's rows — those simply come back as 404.
    """
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                _EXPENSE_COLUMNS + " WHERE id = %s AND line_user_id = %s",
                (expense_id, line_user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            current = _row_to_dict(row)
            for key in ("spent_on", "item", "category", "note"):
                if key in updates and updates[key] is not None:
                    current[key] = str(updates[key])
            if "amount" in updates and updates["amount"] is not None:
                try:
                    current["amount"] = int(updates["amount"])
                except (TypeError, ValueError):
                    pass
            cur.execute(
                "UPDATE household_expenses SET spent_on=%s, item=%s, category=%s, "
                "amount=%s, note=%s WHERE id=%s AND line_user_id=%s",
                (
                    current["spent_on"],
                    current["item"],
                    current["category"],
                    current["amount"],
                    current["note"],
                    expense_id,
                    line_user_id,
                ),
            )
    return current


def delete_expense(expense_id: str, line_user_id: str) -> bool:
    """Delete one expense; returns False when the row is not the caller's."""
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM household_expenses WHERE id = %s AND line_user_id = %s",
                (expense_id, line_user_id),
            )
            return cur.rowcount > 0


def _prev_month(year_month: str) -> str:
    year, month = int(year_month[:4]), int(year_month[5:7])
    month -= 1
    if month == 0:
        year -= 1
        month = 12
    return f"{year:04d}-{month:02d}"


def monthly_summary(line_user_id: str, year_month: str) -> dict:
    """Return totals for ``YYYY-MM`` (per user), plus the previous month.

    ``year_month`` is matched against the ``spent_on`` column's TEXT prefix
    via LIKE, same as the bike summary. ``prev_total`` is the previous
    calendar month's total so the UI can show a month-over-month change
    without a second request.
    """
    _ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), COALESCE(SUM(amount), 0) "
                "FROM household_expenses WHERE line_user_id = %s AND spent_on LIKE %s",
                (line_user_id, year_month + "%"),
            )
            count, total = cur.fetchone()

            cur.execute(
                "SELECT category, COALESCE(SUM(amount), 0) AS total, COUNT(*) AS n "
                "FROM household_expenses WHERE line_user_id = %s AND spent_on LIKE %s "
                "GROUP BY category ORDER BY total DESC, category ASC",
                (line_user_id, year_month + "%"),
            )
            by_category = [
                {"category": r[0], "amount": int(r[1] or 0), "count": int(r[2] or 0)}
                for r in cur.fetchall()
            ]

            prev = _prev_month(year_month)
            cur.execute(
                "SELECT COALESCE(SUM(amount), 0) "
                "FROM household_expenses WHERE line_user_id = %s AND spent_on LIKE %s",
                (line_user_id, prev + "%"),
            )
            prev_total = cur.fetchone()[0]
    return {
        "month": year_month,
        "total": int(total or 0),
        "count": int(count or 0),
        "by_category": by_category,
        "prev_month": prev,
        "prev_total": int(prev_total or 0),
    }
