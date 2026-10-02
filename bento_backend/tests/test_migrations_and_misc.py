"""Migration runner, health, CORS and error-shape tests."""
from __future__ import annotations

import sqlite3

from app.db import MIGRATIONS_DIR, migrate


def test_migrations_dir_is_ordered():
    files = sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql"))
    assert files, "no migration files found"
    assert files == sorted(files)


def test_migrate_is_idempotent(client, env):
    from app.db import get_conn

    conn = get_conn()
    assert migrate(conn) == []  # already applied at startup
    versions = [r["version"] for r in conn.execute("SELECT version FROM schema_migrations")]
    assert len(versions) == len(set(versions))


def test_fresh_db_gets_full_schema(client):
    from app.db import get_conn

    conn = get_conn()
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"employees", "bento_orders", "order_audit_logs", "sessions", "schema_migrations"} <= tables


def test_unique_constraint_exists_at_db_level(env):
    """The 1-employee-1-day rule is enforced by the DB, not only by app code."""
    import pytest

    from app import db
    from app.timeutil import now_tokyo_iso

    conn = db.get_conn()
    db.migrate(conn)
    now = now_tokyo_iso()
    with conn:
        conn.execute(
            "INSERT INTO employees (employee_code, name, created_at) VALUES ('E900', 'test', ?)", (now,)
        )
        emp_id = conn.execute(
            "SELECT id FROM employees WHERE employee_code='E900'"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO bento_orders (employee_id, date, status, created_at, updated_at, updated_by)"
            " VALUES (?, '2026-10-01', 'needed', ?, ?, 'test')",
            (emp_id, now, now),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO bento_orders (employee_id, date, status, created_at, updated_at, updated_by)"
                " VALUES (?, '2026-10-01', 'not_needed', ?, ?, 'test')",
                (emp_id, now, now),
            )


def test_foreign_keys_enabled(client):
    from app.db import get_conn

    assert get_conn().execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_foreign_key_rejects_unknown_employee(client):
    import pytest

    from app.db import get_conn

    conn = get_conn()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO bento_orders (employee_id, date, status, created_at, updated_at, updated_by)"
            " VALUES (999999, '2026-10-01', 'needed', 'x', 'x', 'x')"
        )
    conn.rollback()


def test_deleting_employee_cascades_orders(client, emp_headers):
    from datetime import timedelta

    from app.db import get_conn
    from app.timeutil import today_tokyo

    day = (today_tokyo() + timedelta(days=1)).isoformat()
    client.post("/api/orders", json={"date": day, "status": "needed"}, headers=emp_headers)
    conn = get_conn()
    emp_id = conn.execute("SELECT id FROM employees WHERE employee_code='E002'").fetchone()["id"]
    with conn:
        conn.execute("DELETE FROM employees WHERE id = ?", (emp_id,))
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM bento_orders WHERE employee_id = ?", (emp_id,)
    ).fetchone()["c"] == 0


def test_health_endpoint(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["line_verification_enabled"] is False


def test_cors_preflight_from_allowed_origin(client):
    resp = client.options(
        "/api/orders",
        headers={
            "Origin": "https://liff.line.me",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://liff.line.me"


def test_cors_preflight_from_unknown_origin(client):
    resp = client.options(
        "/api/orders",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert resp.headers.get("access-control-allow-origin") is None


def test_error_responses_have_stable_shape(client, employees):
    resp = client.post("/api/auth/login", json={"line_user_id": employees["E002"]})
    body = resp.json()
    assert set(body) == {"success", "error", "message"}
    assert body["success"] is False and isinstance(body["error"], str) and isinstance(body["message"], str)


def test_openapi_available(client):
    assert client.get("/openapi.json").status_code == 200