"""Authentication and authorisation tests (spec 2, 4.1, 6.1)."""
from __future__ import annotations

from conftest import DEV_TOKEN


def test_login_returns_employee_and_token(client, employees):
    resp = client.post(
        "/api/auth/login",
        json={"line_user_id": employees["E003"], "dev_token": DEV_TOKEN},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["employee"]["employee_code"] == "E003"
    assert body["employee"]["role"] == "employee"
    assert body["token"]


def test_login_rejects_unregistered_line_user(client, employees):
    resp = client.post(
        "/api/auth/login",
        json={"line_user_id": "Uunknown00000000000000000000000000", "dev_token": DEV_TOKEN},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["success"] is False
    assert body["error"] == "unauthorized_user"


def test_login_rejects_bad_dev_token(client, employees):
    resp = client.post(
        "/api/auth/login",
        json={"line_user_id": employees["E003"], "dev_token": "wrong"},
    )
    assert resp.status_code == 401


def test_login_without_id_token_is_rejected_when_line_unconfigured(client, employees):
    """Fails closed: no LINE credentials + no dev token => 401, never 200."""
    resp = client.post("/api/auth/login", json={"line_user_id": employees["E003"]})
    assert resp.status_code == 401


def test_orders_require_bearer_token(client, employees):
    assert client.get("/api/orders").status_code == 401


def test_garbage_token_rejected(client):
    resp = client.get("/api/orders", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_logout_revokes_session(client, auth, employees):
    headers, _ = auth("E004")
    assert client.get("/api/orders", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).json()["success"] is True
    assert client.get("/api/orders", headers=headers).status_code == 401


def test_me_returns_profile(client, emp_headers):
    body = client.get("/api/auth/me", headers=emp_headers).json()
    assert body["employee"]["employee_code"] == "E002"


def test_admin_route_rejects_employee(client, emp_headers):
    resp = client.get("/api/admin/orders", headers=emp_headers)
    assert resp.status_code == 403
    assert resp.json()["error"] == "admin_required"


def test_admin_route_allows_admin(client, admin_headers):
    resp = client.get("/api/admin/orders", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["summary"]["total_employees"] == 20


def test_inactive_employee_cannot_login(client, employees):
    from app.db import get_conn

    conn = get_conn()
    with conn:
        conn.execute("UPDATE employees SET is_active = 0 WHERE employee_code = 'E005'")
    resp = client.post(
        "/api/auth/login",
        json={"line_user_id": employees["E005"], "dev_token": DEV_TOKEN},
    )
    assert resp.status_code == 403


def test_session_token_is_hashed_at_rest(client, auth, employees):
    from app.db import get_conn

    headers, payload = auth("E006")
    row = get_conn().execute("SELECT token_hash FROM sessions").fetchall()
    stored = {r["token_hash"] for r in row}
    assert payload["token"] not in stored
    assert len(stored) == len(row)