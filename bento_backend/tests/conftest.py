"""Pytest fixtures: isolated temp DB, migrated schema, seeded employees, client.

Every test uses a fresh SQLite file under tmp_path and a bearer token minted
through the real ``POST /api/auth/login`` path (dev-token auth), so tests
exercise authentication end-to-end.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEV_TOKEN = "test-dev-token"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("BENTO_DATABASE_PATH", str(db_file))
    monkeypatch.setenv("BENTO_DEV_AUTH_TOKEN", DEV_TOKEN)
    monkeypatch.setenv("BENTO_CORS_ORIGINS", "https://liff.line.me")
    monkeypatch.setenv("BENTO_SESSION_TTL_HOURS", "1")

    from app import config
    from app import db as db_module

    config.reset_settings_cache()
    db_module.reset_for_tests()
    yield {"db_path": str(db_file)}
    db_module.reset_for_tests()
    config.reset_settings_cache()


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def employees(client):
    """20 employees (E001 = admin) with deterministic fake LINE IDs."""
    from app.db import get_conn
    from app.timeutil import now_tokyo_iso

    db = get_conn()
    with db:
        for i in range(20):
            code = f"E{i + 1:03d}"
            db.execute(
                "INSERT INTO employees (employee_code, name, line_user_id, role, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    code,
                    f"社員{i + 1:02d}",
                    f"Utest{i + 1:04d}aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "admin" if code == "E001" else "employee",
                    now_tokyo_iso(),
                ),
            )
    return {f"E{i + 1:03d}": f"Utest{i + 1:04d}aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" for i in range(20)}


@pytest.fixture()
def auth(client, employees):
    """Helper: login as a given employee code, returning (headers, response)."""

    def _login(code: str):
        resp = client.post(
            "/api/auth/login",
            json={"line_user_id": employees[code], "dev_token": DEV_TOKEN},
        )
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['token']}"}, resp.json()

    return _login


@pytest.fixture()
def emp_headers(auth):
    headers, _ = auth("E002")
    return headers


@pytest.fixture()
def admin_headers(auth):
    headers, _ = auth("E001")
    return headers