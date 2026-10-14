"""管理ページ (admin page) のテスト: 認証 + 社員マスタCRUD。

LIFF とは別系統の認証（admin_users + admin_sessions, pbkdf2パスワード,
httpOnlyセッションCookie）の動作と、/api/admin/employees のCRUDを検証する。
"""
from __future__ import annotations

import pytest


def _setup(client, username="admin1", password="password123", name="管理者"):
    return client.post(
        "/api/admin-auth/setup",
        json={"username": username, "password": password, "display_name": name},
    )


@pytest.fixture()
def admin_client(env):
    """https シミュレーションの TestClient に管理者をセットアップした状態。

    ``secure=True`` のセッションCookieは https でしか送信されないため、
    本番と同じ https:// ベースURLのクライアントを使う（TestClient の
    デフォルト http:// だと cookie が飛ばず 401 になる）。
    """
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(), base_url="https://testserver") as c:
        resp = _setup(c)
        assert resp.status_code == 200, resp.text
        yield c


# --------------------------------------------------------------------------- #
# セットアップ / ログイン
# --------------------------------------------------------------------------- #

def test_status_reports_setup_required_initially(client):
    resp = client.get("/api/admin-auth/status")
    assert resp.status_code == 200
    assert resp.json()["setup_required"] is True


def test_setup_creates_first_admin_and_sets_cookie(client):
    resp = _setup(client)
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["success"] is True
    assert j["admin"]["username"] == "admin1"
    assert j["token"]  # Bearer 用の生トークンも返る
    assert "admin_session" in resp.cookies
    # セットアップ後は setup_required が false になる
    assert client.get("/api/admin-auth/status").json()["setup_required"] is False


def test_setup_refused_once_admin_exists(client):
    _setup(client)
    resp = _setup(client, username="admin2")
    assert resp.status_code == 400
    assert resp.json()["error"] == "already_setup"


def test_setup_requires_password_length(client):
    resp = _setup(client, password="short")
    assert resp.status_code == 422  # pydantic min_length


def test_login_success_and_failures(client):
    _setup(client)
    client.cookies.clear()  # セットアップのcookieを破棄して素の状態で試す

    good = client.post("/api/admin-auth/login", json={"username": "admin1", "password": "password123"})
    assert good.status_code == 200, good.text
    assert good.json()["admin"]["username"] == "admin1"

    bad = client.post("/api/admin-auth/login", json={"username": "admin1", "password": "wrong-password"})
    assert bad.status_code == 401

    # ユーザー名不正でも同じ401（ユーザー名列挙不可）
    unknown = client.post("/api/admin-auth/login", json={"username": "nobody", "password": "x"})
    assert unknown.status_code == 401
    assert unknown.json()["message"] == bad.json()["message"]


def test_me_requires_session(client):
    assert client.get("/api/admin-auth/me").status_code == 401


def test_me_with_cookie(admin_client):
    resp = admin_client.get("/api/admin-auth/me")
    assert resp.status_code == 200
    assert resp.json()["admin"]["username"] == "admin1"


def test_bearer_header_auth(client):
    resp = _setup(client)
    token = resp.json()["token"]
    client.cookies.clear()  # cookie を捨てて Bearer のみで認証できることを確認
    me = client.get("/api/admin-auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200


def test_logout_revokes_session(admin_client):
    assert admin_client.get("/api/admin-auth/me").status_code == 200
    out = admin_client.post("/api/admin-auth/logout")
    assert out.status_code == 200
    assert admin_client.get("/api/admin-auth/me").status_code == 401


# --------------------------------------------------------------------------- #
# 社員マスタCRUD
# --------------------------------------------------------------------------- #

def test_employee_list_requires_admin_auth(client, employees):
    resp = client.get("/api/admin/employees")
    assert resp.status_code == 401


def test_employee_list(admin_client, employees):
    resp = admin_client.get("/api/admin/employees")
    assert resp.status_code == 200
    assert len(resp.json()["employees"]) == 20
    # 社員コード昇順
    codes = [e["employee_code"] for e in resp.json()["employees"]]
    assert codes == sorted(codes)


def test_employee_create(admin_client, employees):
    resp = admin_client.post(
        "/api/admin/employees",
        json={
            "employee_code": "E021",
            "name": "新入社員",
            "line_user_id": "Unew0001aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "role": "employee",
        },
    )
    assert resp.status_code == 200, resp.text
    emp = resp.json()["employee"]
    assert emp["employee_code"] == "E021"
    assert emp["name"] == "新入社員"
    assert emp["is_active"] == 1  # 既定は有効


def test_employee_create_duplicate_code(admin_client, employees):
    admin_client.post(
        "/api/admin/employees",
        json={"employee_code": "E021", "name": "一人目", "line_user_id": "Unew0001aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    )
    dup = admin_client.post(
        "/api/admin/employees",
        json={"employee_code": "E021", "name": "二人目", "line_user_id": "Unew0002aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    )
    assert dup.status_code == 409
    assert dup.json()["error"] == "duplicate_employee_code"


def test_employee_create_duplicate_line_user_id(admin_client, employees):
    dup = admin_client.post(
        "/api/admin/employees",
        json={"employee_code": "E021", "name": "重複", "line_user_id": employees["E001"]},
    )
    assert dup.status_code == 409
    assert dup.json()["error"] == "duplicate_line_user_id"


def test_employee_update(admin_client, employees):
    created = admin_client.post(
        "/api/admin/employees",
        json={"employee_code": "E021", "name": "仮名", "line_user_id": "Unew0001aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    ).json()["employee"]
    resp = admin_client.put(
        f"/api/admin/employees/{created['id']}",
        json={"name": "本名", "is_active": 0},
    )
    assert resp.status_code == 200
    emp = resp.json()["employee"]
    assert emp["name"] == "本名"
    assert emp["is_active"] == 0


def test_employee_update_not_found(admin_client, employees):
    resp = admin_client.put("/api/admin/employees/99999", json={"name": "x"})
    assert resp.status_code == 404


def test_employee_delete(admin_client, employees):
    created = admin_client.post(
        "/api/admin/employees",
        json={"employee_code": "E021", "name": "一時的", "line_user_id": "Unew0001aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    ).json()["employee"]
    resp = admin_client.delete(f"/api/admin/employees/{created['id']}")
    assert resp.status_code == 200
    # 削除後は一覧から消え、更新は404
    codes = [e["employee_code"] for e in admin_client.get("/api/admin/employees").json()["employees"]]
    assert "E021" not in codes
    assert admin_client.put(f"/api/admin/employees/{created['id']}", json={"name": "x"}).status_code == 404


def test_employee_delete_not_found(admin_client, employees):
    assert admin_client.delete("/api/admin/employees/99999").status_code == 404
