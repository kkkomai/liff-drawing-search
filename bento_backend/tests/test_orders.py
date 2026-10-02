"""Order write/read rules: uniqueness, past-date refusal, future horizon, audit."""
from __future__ import annotations

from datetime import timedelta

from app.timeutil import today_tokyo


def d(offset: int) -> str:
    return (today_tokyo() + timedelta(days=offset)).isoformat()


# --- registration ------------------------------------------------------------
def test_today_and_future_registration(client, emp_headers):
    for offset, status in ((0, "needed"), (1, "not_needed"), (7, "needed"), (400, "needed")):
        resp = client.post("/api/orders", json={"date": d(offset), "status": status}, headers=emp_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["order"]["status"] == status


def test_one_employee_one_day_upsert_not_duplicate(client, emp_headers):
    client.post("/api/orders", json={"date": d(2), "status": "needed"}, headers=emp_headers)
    resp = client.put("/api/orders", json={"date": d(2), "status": "not_needed"}, headers=emp_headers)
    assert resp.status_code == 200
    orders = client.get(f"/api/orders?start_date={d(2)}&end_date={d(2)}", headers=emp_headers).json()["orders"]
    assert len(orders) == 1
    assert orders[0]["status"] == "not_needed"


def test_identical_resend_is_idempotent(client, emp_headers):
    first = client.post("/api/orders", json={"date": d(3), "status": "needed"}, headers=emp_headers).json()
    second = client.post("/api/orders", json={"date": d(3), "status": "needed"}, headers=emp_headers).json()
    assert "同じ内容" in second["message"]
    logs = client.get("/api/orders/audit", headers=emp_headers).json()["logs"]
    creates = [l for l in logs if l["date"] == d(3) and l["action"] == "create"]
    assert len(creates) == 1
    assert first["order"]["updated_at"] == second["order"]["updated_at"]


def test_two_employees_can_order_same_day(client, auth):
    h_a, _ = auth("E002")
    h_b, _ = auth("E003")
    client.post("/api/orders", json={"date": d(1), "status": "needed"}, headers=h_a)
    client.post("/api/orders", json={"date": d(1), "status": "needed"}, headers=h_b)
    for h in (h_a, h_b):
        rows = client.get(f"/api/orders?start_date={d(1)}&end_date={d(1)}", headers=h).json()["orders"]
        assert len(rows) == 1


# --- past-date immutability (server side) -----------------------------------
def test_past_date_is_rejected(client, emp_headers):
    resp = client.post("/api/orders", json={"date": d(-1), "status": "needed"}, headers=emp_headers)
    assert resp.status_code == 400
    assert resp.json()["error"] == "past_date_modification_not_allowed"


def test_past_date_rejected_even_when_changing_existing_row(client, emp_headers):
    """Simulates a tampered client: the row exists (written via service), API still refuses."""
    from app.db import get_conn
    from app.services.orders import upsert_order

    conn = get_conn()
    emp_id = conn.execute("SELECT id FROM employees WHERE employee_code='E002'").fetchone()["id"]
    upsert_order(conn, emp_id, today_tokyo() + timedelta(days=1), "needed", actor="setup")
    day = d(1)
    # force the stored row to a past date (simulating legacy/edited data)
    with conn:
        conn.execute(
            "UPDATE bento_orders SET date = ? WHERE employee_id = ? AND date = ?",
            (d(-5), emp_id, day),
        )
    resp = client.put("/api/orders", json={"date": d(-5), "status": "not_needed"}, headers=emp_headers)
    assert resp.status_code == 400
    assert resp.json()["error"] == "past_date_modification_not_allowed"


def test_past_date_delete_rejected(client, emp_headers):
    resp = client.delete(f"/api/orders/{d(-1)}", headers=emp_headers)
    assert resp.status_code == 400


# --- validation --------------------------------------------------------------
def test_invalid_status_rejected(client, emp_headers):
    resp = client.post("/api/orders", json={"date": d(1), "status": "maybe"}, headers=emp_headers)
    assert resp.status_code == 422


def test_malformed_date_rejected(client, emp_headers):
    assert client.post("/api/orders", json={"date": "2026/10/01", "status": "needed"}, headers=emp_headers).status_code == 422
    assert client.post("/api/orders", json={"date": "2026-02-30", "status": "needed"}, headers=emp_headers).status_code == 400


def test_invalid_range_rejected(client, emp_headers):
    resp = client.get(f"/api/orders?start_date={d(5)}&end_date={d(1)}", headers=emp_headers)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_range"


def test_huge_range_rejected(client, emp_headers):
    resp = client.get("/api/orders?start_date=2000-01-01&end_date=2030-01-01", headers=emp_headers)
    assert resp.status_code == 400
    assert resp.json()["error"] == "range_too_large"


# --- history -----------------------------------------------------------------
def test_history_older_than_one_month_is_readable(client, emp_headers):
    old = (today_tokyo() - timedelta(days=400)).isoformat()
    from app.db import get_conn
    from app.services.orders import upsert_order

    conn = get_conn()
    emp_id = conn.execute("SELECT id FROM employees WHERE employee_code='E002'").fetchone()["id"]
    upsert_order(conn, emp_id, today_tokyo() + timedelta(days=1), "needed", actor="setup")
    with conn:  # backdate a stored row to represent 400-day-old history
        conn.execute("UPDATE bento_orders SET date=? WHERE employee_id=?", (old, emp_id))
    rows = client.get(f"/api/orders?start_date={old}&end_date={old}", headers=emp_headers).json()["orders"]
    assert len(rows) == 1 and rows[0]["status"] == "needed"


# --- audit -------------------------------------------------------------------
def test_audit_trail_records_create_and_update(client, emp_headers):
    client.post("/api/orders", json={"date": d(4), "status": "needed"}, headers=emp_headers)
    client.post("/api/orders", json={"date": d(4), "status": "not_needed"}, headers=emp_headers)
    logs = client.get("/api/orders/audit", headers=emp_headers).json()["logs"]
    day_logs = [l for l in logs if l["date"] == d(4)]
    assert [l["action"] for l in day_logs] == ["update", "create"]
    assert day_logs[0]["old_status"] == "needed" and day_logs[0]["new_status"] == "not_needed"
    assert day_logs[1]["old_status"] is None
    assert all(l["changed_by"] == "E002" for l in day_logs)
    assert all(l["changed_at"] for l in day_logs)


def test_delete_writes_audit_row(client, emp_headers):
    client.post("/api/orders", json={"date": d(5), "status": "needed"}, headers=emp_headers)
    assert client.delete(f"/api/orders/{d(5)}", headers=emp_headers).status_code == 200
    logs = client.get("/api/orders/audit", headers=emp_headers).json()["logs"]
    assert any(l["action"] == "delete" and l["date"] == d(5) for l in logs)
    rows = client.get(f"/api/orders?start_date={d(5)}&end_date={d(5)}", headers=emp_headers).json()["orders"]
    assert rows == []


def test_delete_unknown_returns_404(client, emp_headers):
    assert client.delete(f"/api/orders/{d(6)}", headers=emp_headers).status_code == 404


def test_audit_is_scoped_to_own_records(client, auth):
    h_a, _ = auth("E007")
    h_b, _ = auth("E008")
    client.post("/api/orders", json={"date": d(1), "status": "needed"}, headers=h_a)
    logs = client.get("/api/orders/audit", headers=h_b).json()["logs"]
    assert logs == []


# --- concurrency -------------------------------------------------------------
def test_concurrent_writes_keep_single_row(client, emp_headers):
    import threading

    day = d(8)
    errors: list[Exception] = []

    def worker(status: str) -> None:
        try:
            r = client.post("/api/orders", json={"date": day, "status": status}, headers=emp_headers)
            assert r.status_code == 200, r.text
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(s,)) for s in ("needed", "not_needed", "needed", "not_needed")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    rows = client.get(f"/api/orders?start_date={day}&end_date={day}", headers=emp_headers).json()["orders"]
    assert len(rows) == 1
    assert rows[0]["status"] in ("needed", "not_needed")