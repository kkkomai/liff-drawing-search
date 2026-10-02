"""Admin listing tests (spec 4.4, 5.2): all 20 employees, day and period views."""
from __future__ import annotations

from datetime import timedelta

from app.timeutil import today_tokyo


def d(offset: int) -> str:
    return (today_tokyo() + timedelta(days=offset)).isoformat()


def test_default_view_is_today(client, admin_headers):
    body = client.get("/api/admin/orders", headers=admin_headers).json()
    assert body["date"] == d(0)
    assert body["summary"]["total_employees"] == 20
    assert body["summary"]["unregistered_count"] == 20


def test_day_view_counts(client, auth, admin_headers):
    h_a, _ = auth("E002")
    h_b, _ = auth("E003")
    h_c, _ = auth("E004")
    client.post("/api/orders", json={"date": d(1), "status": "needed"}, headers=h_a)
    client.post("/api/orders", json={"date": d(1), "status": "needed"}, headers=h_b)
    client.post("/api/orders", json={"date": d(1), "status": "not_needed"}, headers=h_c)
    body = client.get(f"/api/admin/orders?date={d(1)}", headers=admin_headers).json()
    assert body["summary"] == {
        "total_employees": 20,
        "needed_count": 2,
        "not_needed_count": 1,
        "unregistered_count": 17,
    }
    assert len(body["employees_status"]) == 20
    by_code = {e["employee_code"]: e for e in body["employees_status"]}
    assert by_code["E002"]["status"] == "needed"
    assert by_code["E005"]["status"] == "unregistered"
    assert by_code["E005"]["updated_at"] is None


def test_range_view(client, auth, admin_headers):
    h, _ = auth("E002")
    client.post("/api/orders", json={"date": d(1), "status": "needed"}, headers=h)
    body = client.get(f"/api/admin/orders?start_date={d(0)}&end_date={d(6)}", headers=admin_headers).json()
    assert body["start_date"] == d(0) and body["end_date"] == d(6)
    assert len(body["days"]) == 7
    assert body["total_employees"] == 20
    assert body["days"][1]["summary"]["needed_count"] == 1


def test_range_too_large_rejected(client, admin_headers):
    resp = client.get("/api/admin/orders?start_date=2020-01-01&end_date=2026-01-01", headers=admin_headers)
    assert resp.status_code == 400
    assert resp.json()["error"] == "range_too_large"


def test_date_and_range_together_rejected(client, admin_headers):
    resp = client.get(f"/api/admin/orders?date={d(0)}&start_date={d(0)}&end_date={d(1)}", headers=admin_headers)
    assert resp.status_code == 400


def test_half_open_range_params_rejected(client, admin_headers):
    resp = client.get(f"/api/admin/orders?start_date={d(0)}", headers=admin_headers)
    assert resp.status_code == 400


def test_summary_endpoint(client, auth, admin_headers):
    h, _ = auth("E002")
    client.post("/api/orders", json={"date": d(0), "status": "needed"}, headers=h)
    body = client.get("/api/admin/summary", headers=admin_headers).json()
    assert body["needed_count"] == 1
    assert body["unregistered_count"] == 19


def test_admin_audit_visible_across_employees(client, auth, admin_headers):
    h, _ = auth("E009")
    client.post("/api/orders", json={"date": d(2), "status": "needed"}, headers=h)
    logs = client.get(f"/api/admin/audit?date={d(2)}", headers=admin_headers).json()["logs"]
    assert any(l["employee_id"] is not None and l["changed_by"] == "E009" for l in logs)


def test_inactive_employee_excluded_from_admin_list(client, admin_headers):
    from app.db import get_conn

    conn = get_conn()
    with conn:
        conn.execute("UPDATE employees SET is_active = 0 WHERE employee_code = 'E020'")
    body = client.get(f"/api/admin/orders?date={d(0)}", headers=admin_headers).json()
    assert body["summary"]["total_employees"] == 19
    assert "E020" not in {e["employee_code"] for e in body["employees_status"]}