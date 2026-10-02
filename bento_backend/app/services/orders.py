"""Business logic for orders (spec sections 3, 4.2-4.4, 6).

Invariants enforced here, not in the route layer:

* one order per employee per day (``UNIQUE(employee_id, date)`` + upsert);
* past dates are read-only — rejected server-side regardless of what the client
  sends, so a tampered LIFF client cannot rewrite history;
* future dates are accepted with no upper bound;
* every create/update/delete appends an ``order_audit_logs`` row;
* writes take ``BEGIN IMMEDIATE`` so concurrent taps serialise safely and the
  last write wins deterministically.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from ..errors import ApiError, bad_request, not_found
from ..timeutil import now_tokyo_iso, today_tokyo

VALID_STATUSES = ("needed", "not_needed")


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #
def list_orders(conn: sqlite3.Connection, employee_id: int, start: date, end: date) -> list[dict]:
    rows = conn.execute(
        "SELECT date, status, created_at, updated_at FROM bento_orders"
        " WHERE employee_id = ? AND date BETWEEN ? AND ?"
        " ORDER BY date ASC",
        (employee_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [
        {"date": r["date"], "status": r["status"], "updated_at": r["updated_at"]}
        for r in rows
    ]


def get_order(conn: sqlite3.Connection, employee_id: int, day: date) -> dict | None:
    row = conn.execute(
        "SELECT date, status, updated_at FROM bento_orders"
        " WHERE employee_id = ? AND date = ?",
        (employee_id, day.isoformat()),
    ).fetchone()
    return dict(row) if row else None


def list_audit_logs(
    conn: sqlite3.Connection, employee_id: int | None = None, day: date | None = None, limit: int = 200
) -> list[dict]:
    sql = ["SELECT id, employee_id, date, old_status, new_status, action, changed_at, changed_by FROM order_audit_logs"]
    where, params = [], []
    if employee_id is not None:
        where.append("employee_id = ?")
        params.append(employee_id)
    if day is not None:
        where.append("date = ?")
        params.append(day.isoformat())
    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY id DESC LIMIT ?")
    params.append(limit)
    return [dict(r) for r in conn.execute(" ".join(sql), params)]


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #
def upsert_order(
    conn: sqlite3.Connection, employee_id: int, day: date, status: str, *, actor: str
) -> dict:
    """Create or update one order. Raises ApiError for past dates."""
    if status not in VALID_STATUSES:
        raise bad_request("invalid_status", "status は 'needed' または 'not_needed' です。")
    if day < today_tokyo():
        raise bad_request(
            "past_date_modification_not_allowed",
            "過去の日付の注文を変更することはできません。",
        )

    day_s = day.isoformat()
    now = now_tokyo_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT id, status FROM bento_orders WHERE employee_id = ? AND date = ?",
            (employee_id, day_s),
        ).fetchone()

        if existing is None:
            cur = conn.execute(
                "INSERT INTO bento_orders (employee_id, date, status, created_at, updated_at, updated_by)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (employee_id, day_s, status, now, now, actor),
            )
            order_id = cur.lastrowid
            old_status = None
            action = "create"
        else:
            order_id = existing["id"]
            old_status = existing["status"]
            if old_status == status:
                # Idempotent re-send of the same choice: no audit noise, no churn.
                conn.execute("COMMIT")
                return {
                    "date": day_s,
                    "status": status,
                    "updated_at": conn.execute(
                        "SELECT updated_at FROM bento_orders WHERE id = ?", (order_id,)
                    ).fetchone()["updated_at"],
                    "changed": False,
                }
            conn.execute(
                "UPDATE bento_orders SET status = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                (status, now, actor, order_id),
            )
            action = "update"

        conn.execute(
            "INSERT INTO order_audit_logs"
            " (order_id, employee_id, date, old_status, new_status, changed_at, changed_by, action)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, employee_id, day_s, old_status, status, now, actor, action),
        )
        conn.execute("COMMIT")
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        # Lost a race on UNIQUE(employee_id, date): retry once as an update.
        return upsert_order(conn, employee_id, day, status, actor=actor)
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {"date": day_s, "status": status, "updated_at": now, "changed": True}


def delete_order(conn: sqlite3.Connection, employee_id: int, day: date, *, actor: str) -> None:
    """Delete is only meaningful for today/future; past days are immutable."""
    if day < today_tokyo():
        raise bad_request(
            "past_date_modification_not_allowed",
            "過去の日付の注文を変更することはできません。",
        )
    day_s = day.isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT id, status FROM bento_orders WHERE employee_id = ? AND date = ?",
            (employee_id, day_s),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            raise not_found("その日の注文は登録されていません。")
        conn.execute("DELETE FROM bento_orders WHERE id = ?", (row["id"],))
        conn.execute(
            "INSERT INTO order_audit_logs"
            " (order_id, employee_id, date, old_status, new_status, changed_at, changed_by, action)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'delete')",
            (row["id"], employee_id, day_s, row["status"], row["status"], now_tokyo_iso(), actor),
        )
        conn.execute("COMMIT")
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        raise
    except ApiError:
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise


# --------------------------------------------------------------------------- #
# admin
# --------------------------------------------------------------------------- #
def admin_day(conn: sqlite3.Connection, day: date) -> dict:
    day_s = day.isoformat()
    employees = conn.execute(
        "SELECT id, employee_code, name FROM employees"
        " WHERE is_active = 1 ORDER BY employee_code ASC"
    ).fetchall()
    orders = {
        r["employee_id"]: r
        for r in conn.execute(
            "SELECT employee_id, status, updated_at FROM bento_orders WHERE date = ?", (day_s,)
        )
    }
    rows = []
    needed = not_needed = unregistered = 0
    for emp in employees:
        o = orders.get(emp["id"])
        status = o["status"] if o else "unregistered"
        if status == "needed":
            needed += 1
        elif status == "not_needed":
            not_needed += 1
        else:
            unregistered += 1
        rows.append(
            {
                "employee_id": emp["id"],
                "employee_code": emp["employee_code"],
                "name": emp["name"],
                "status": status,
                "updated_at": o["updated_at"] if o else None,
            }
        )
    return {
        "date": day_s,
        "summary": {
            "total_employees": len(employees),
            "needed_count": needed,
            "not_needed_count": not_needed,
            "unregistered_count": unregistered,
        },
        "employees_status": rows,
    }


def admin_range(conn: sqlite3.Connection, start: date, end: date, *, max_days: int = 62) -> dict:
    span = (end - start).days + 1
    if span > max_days:
        raise bad_request(
            "range_too_large",
            f"期間は{max_days}日以内で指定してください（指定日: {span}日）。",
        )
    total = conn.execute("SELECT COUNT(*) AS c FROM employees WHERE is_active = 1").fetchone()["c"]
    days = []
    from ..timeutil import add_days

    cur = start
    while cur <= end:
        days.append(admin_day(conn, cur))
        cur = add_days(cur, 1)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_employees": total,
        "days": days,
    }