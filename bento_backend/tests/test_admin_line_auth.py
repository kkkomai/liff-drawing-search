"""管理ページのLINE認証経路（liff_admin）のテスト。

employee CRUD は2つの認証経路を持つ:
1. admin_users のパスワードセッション（cookie）
2. bento の LINE セッション（Bearer, role=admin）— liff_admin から開く経路

このファイルは (2) を検証する。(1) は test_admin_auth.py が担当。
"""
from __future__ import annotations

import pytest

DEV_TOKEN = "test-dev-token"


@pytest.fixture()
def env_line(tmp_path, monkeypatch):
    """LINE 検証あり・dev auth ありの環境（conftest の env に channel 設定を足す）。"""
    monkeypatch.setenv("BENTO_DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("BENTO_DEV_AUTH_TOKEN", DEV_TOKEN)
    monkeypatch.setenv("BENTO_CORS_ORIGINS", "https://liff.line.me")
    monkeypatch.setenv("BENTO_SESSION_TTL_HOURS", "1")
    from app import config
    from app import db as db_module

    config.reset_settings_cache()
    db_module.reset_for_tests()
    yield
    db_module.reset_for_tests()
    config.reset_settings_cache()


def _line_login(client, line_user_id: str):
    """dev_token で bento の LINE セッションを取得（実 id_token 不要のdev経路）。"""
    resp = client.post(
        "/api/auth/login",
        json={"line_user_id": line_user_id, "dev_token": DEV_TOKEN},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _seed_employee(client, code: str, name: str, role: str, line_id: str) -> None:
    """テスト用社員を直接INSERT（パスワード管理ページのCRUDを介さない）。"""
    from app.db import get_conn
    from app.timeutil import now_tokyo_iso

    conn = get_conn()
    with conn:
        conn.execute(
            "INSERT INTO employees (employee_code, name, line_user_id, role, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (code, name, line_id, role, now_tokyo_iso()),
        )


def test_line_admin_can_list_employees(env_line):
    from fastapi.testclient import TestClient

    from app.main import create_app

    admin_id = "Uadmin0001aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    with TestClient(create_app()) as client:
        _seed_employee(client, "E001", "管理者岩野", "admin", admin_id)
        token = _line_login(client, admin_id)
        resp = client.get(
            "/api/admin/employees", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200, resp.text
        codes = [e["employee_code"] for e in resp.json()["employees"]]
        assert "E001" in codes


def test_line_admin_can_create_employee(env_line):
    from fastapi.testclient import TestClient

    from app.main import create_app

    admin_id = "Uadmin0001aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    with TestClient(create_app()) as client:
        _seed_employee(client, "E001", "管理者岩野", "admin", admin_id)
        token = _line_login(client, admin_id)
        resp = client.post(
            "/api/admin/employees",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "employee_code": "E050",
                "name": "新入社員",
                "line_user_id": "Unew0500aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "role": "employee",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["employee"]["employee_code"] == "E050"


def test_line_employee_without_admin_role_is_rejected(env_line):
    """role=employee のLINEユーザーは管理ページAPIを叩けない。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    emp_id = "Uemp00001aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    with TestClient(create_app()) as client:
        _seed_employee(client, "E002", "一般社員", "employee", emp_id)
        token = _line_login(client, emp_id)
        resp = client.get(
            "/api/admin/employees", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "admin_required"


def test_no_auth_is_rejected(env_line):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        resp = client.get("/api/admin/employees")
        assert resp.status_code == 401


def test_invalid_bearer_is_rejected(env_line):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        resp = client.get(
            "/api/admin/employees", headers={"Authorization": "Bearer garbage-token"}
        )
        assert resp.status_code == 401
