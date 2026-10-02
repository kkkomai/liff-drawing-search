"""Tests for the 申請フォーム → LIFF app URL integration and the
environment-variable (dev/staging/prod) configuration.

These cover the contract the form depends on: the same code must hand back the
*correct* LIFF app URL per environment, refuse to hand back a placeholder, and
refuse to start a prod deployment that is still holding dev credentials.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import (  # noqa: E402
    ProductionNotReady,
    Settings,
    assert_production_ready,
    reset_settings_cache,
)
from app.services.line_form import (  # noqa: E402
    LineFormError,
    get_liff_app_link,
    push_liff_app_url,
)

LIFF_ID = "1234567890-AbcdEfgh"
LIFF_URL = f"https://liff.line.me/{LIFF_ID}"


# --------------------------------------------------------------- settings
def _settings(**kwargs) -> Settings:
    # dev_auth_token=None by default: the pytest env fixture exports
    # BENTO_DEV_AUTH_TOKEN, and a unit test that silently inherits it is not
    # testing what it thinks it is.
    base = {
        "app_env": "dev",
        "public_api_base_url": "https://api.example.jp",
        "cors_origins": "https://staging.example.jp",
        "dev_auth_token": None,
    }
    base.update(kwargs)
    return Settings(**base)


def test_liff_url_derived_from_id():
    s = _settings(liff_id=LIFF_ID)
    assert s.effective_liff_app_url == LIFF_URL
    assert s.liff_configured is True


def test_explicit_liff_app_url_wins_over_derived():
    s = _settings(liff_id=LIFF_ID, liff_app_url=LIFF_URL + "/")
    assert s.effective_liff_app_url == LIFF_URL  # trailing slash normalised


def test_no_liff_configured_is_explicit_not_a_placeholder():
    s = _settings()
    assert s.effective_liff_app_url is None
    assert s.liff_configured is False


def test_public_api_base_url_trailing_slash_stripped():
    assert _settings(public_api_base_url="https://api.example.jp/").public_api_base_url == (
        "https://api.example.jp"
    )


def test_cors_always_includes_liff_origin_plus_env_specific():
    s = _settings(cors_origins="https://staging.example.jp,http://localhost:5173")
    assert "https://liff.line.me" in s.effective_cors_origins
    assert "https://staging.example.jp" in s.effective_cors_origins
    assert "http://localhost:5173" in s.effective_cors_origins


def test_env_var_switches_url_per_environment(monkeypatch):
    """The whole point: same code, different env var, different LIFF URL."""
    for env, expected in [
        ("dev", "https://liff.line.me/1111111111-devdevdev"),
        ("staging", "https://liff.line.me/2222222222-stgstgstg"),
        ("prod", "https://liff.line.me/3333333333-prodprodpr"),
    ]:
        monkeypatch.setenv("BENTO_APP_ENV", env)
        monkeypatch.setenv("BENTO_LIFF_ID", expected.rsplit("/", 1)[1])
        reset_settings_cache()
        from app.config import get_settings

        assert get_settings().effective_liff_app_url == expected
    reset_settings_cache()


# ------------------------------------------------------ production guard
def test_prod_without_credentials_raises():
    with pytest.raises(ProductionNotReady) as exc:
        assert_production_ready(_settings(app_env="prod"))
    text = str(exc.value)
    assert "BENTO_LINE_CHANNEL_ID" in text
    assert "BENTO_LIFF_ID" in text
    assert "BENTO_PUBLIC_API_BASE_URL" not in text  # https is set, so that one is fine


def test_prod_reports_every_problem_at_once():
    """One deploy tells the operator everything that is wrong, not just #1."""
    with pytest.raises(ProductionNotReady) as exc:
        assert_production_ready(
            _settings(app_env="prod", public_api_base_url="http://api.example.jp", cors_origins="*")
        )
    text = str(exc.value)
    assert "BENTO_PUBLIC_API_BASE_URL" in text
    assert "'*'" in text


def test_prod_with_dev_token_even_if_everything_else_is_set():
    s = _settings(
        app_env="prod",
        dev_auth_token="oops",
        line_channel_id="cid",
        line_channel_secret="secret",
        liff_id=LIFF_ID,
    )
    with pytest.raises(ProductionNotReady, match="dev 抜け穴"):
        assert_production_ready(s)


def test_fully_configured_prod_passes():
    s = _settings(
        app_env="prod",
        line_channel_id="cid",
        line_channel_secret="secret",
        liff_id=LIFF_ID,
        public_api_base_url="https://api.example.jp",
    )
    checks = assert_production_ready(s)
    assert len(checks) == 6
    assert "LIFFアプリURLが設定済み" in checks


def test_dev_env_skips_the_guard():
    assert assert_production_ready(_settings(app_env="dev", dev_auth_token="x")) == []


# --------------------------------------------------------- form link
def test_form_link_contains_the_url():
    link = get_liff_app_link(_settings(liff_id=LIFF_ID))
    assert link.liff_app_url == LIFF_URL
    assert LIFF_URL in link.as_message()


def test_form_link_refuses_when_unconfigured():
    with pytest.raises(LineFormError) as exc:
        get_liff_app_link(_settings())
    assert exc.value.error == "liff_not_configured"


def test_form_link_rejects_a_non_liff_url():
    with pytest.raises(LineFormError) as exc:
        get_liff_app_link(_settings(liff_app_url="https://evil.example.jp/liff"))
    assert exc.value.error == "liff_app_url_invalid"


# ------------------------------------------------------------- push
class _RecordingClient:
    """Stands in for httpx.Client so no network is touched in tests."""

    def __init__(self, status: int = 200):
        self.status = status
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, **kw):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return httpx.Response(self.status, json={}, request=httpx.Request("POST", url))


def test_push_sends_the_configured_liff_url():
    client = _RecordingClient()
    s = _settings(liff_id=LIFF_ID, line_channel_access_token="tok")
    res = push_liff_app_url(s, "Uabcdef1234567890", client=client)
    assert res["sent"] is True
    assert res["liff_app_url"] == LIFF_URL
    call = client.calls[0]
    assert call["url"] == "https://api.line.me/v2/bot/message/push"
    assert call["headers"]["Authorization"] == "Bearer tok"
    text = call["json"]["messages"][0]["text"]
    assert LIFF_URL in text
    assert call["json"]["to"] == "Uabcdef1234567890"


def test_push_refuses_without_channel_token():
    client = _RecordingClient()
    with pytest.raises(LineFormError) as exc:
        push_liff_app_url(_settings(liff_id=LIFF_ID), "Uabcdef1234567890", client=client)
    assert exc.value.error == "line_channel_access_token_missing"
    assert client.calls == [], "トークンが無いのに送信してはいけない"


def test_push_rejects_a_bad_line_user_id_before_calling_line():
    client = _RecordingClient()
    s = _settings(liff_id=LIFF_ID, line_channel_access_token="tok")
    with pytest.raises(LineFormError) as exc:
        push_liff_app_url(s, "not-a-line-id", client=client)
    assert exc.value.error == "invalid_line_user_id"
    assert client.calls == []


def test_push_surfaces_line_api_errors():
    client = _RecordingClient(status=401)
    s = _settings(liff_id=LIFF_ID, line_channel_access_token="tok")
    with pytest.raises(LineFormError) as exc:
        push_liff_app_url(s, "Uabcdef1234567890", client=client)
    assert exc.value.error == "line_api_error"


def test_push_error_message_never_leaks_the_token():
    client = _RecordingClient(status=401)
    s = _settings(liff_id=LIFF_ID, line_channel_access_token="super-secret-token")
    with pytest.raises(LineFormError) as exc:
        push_liff_app_url(s, "Uabcdef1234567890", client=client)
    assert "super-secret-token" not in exc.value.message


# ------------------------------------------------------------ HTTP API
def test_public_config_returns_liff_url(client, monkeypatch):
    monkeypatch.setenv("BENTO_LIFF_ID", LIFF_ID)
    reset_settings_cache()
    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as c:
        body = c.get("/api/config").json()
    assert body["liff_app_url"] == LIFF_URL
    assert body["liff_id"] == LIFF_ID
    assert body["liff_configured"] is True
    assert body["app_env"] == "dev"
    reset_settings_cache()


def test_public_config_leaks_no_secrets(client):
    body = client.get("/api/config").json()
    blob = repr(body)
    assert "test-dev-token" not in blob
    assert "line_channel_secret" not in blob


def test_health_reports_liff_state(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "liff_configured" in body
    assert "app_env" in body


def test_admin_liff_link_returns_the_url(admin_headers, monkeypatch):
    monkeypatch.setenv("BENTO_LIFF_ID", LIFF_ID)
    reset_settings_cache()
    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as c:
        body = c.get("/api/admin/liff-link", headers=admin_headers).json()
    assert body["liff_app_url"] == LIFF_URL
    assert LIFF_URL in body["message"]
    reset_settings_cache()


def test_admin_liff_link_requires_admin(emp_headers):
    assert client_unauthorized(emp_headers)


def client_unauthorized(emp_headers) -> bool:
    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as c:
        return c.get("/api/admin/liff-link", headers=emp_headers).status_code == 403


def test_admin_liff_link_503_when_unconfigured(admin_headers):
    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as c:
        r = c.get("/api/admin/liff-link", headers=admin_headers)
    assert r.status_code == 503
    assert r.json()["error"] == "liff_not_configured"


def test_admin_liff_push_503_without_channel_token(admin_headers, monkeypatch):
    monkeypatch.setenv("BENTO_LIFF_ID", LIFF_ID)
    reset_settings_cache()
    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as c:
        r = c.post("/api/admin/liff-push", json={"line_user_id": "Uabcdef1234567890"},
                   headers=admin_headers)
    assert r.status_code == 503
    assert r.json()["error"] == "line_channel_access_token_missing"
    reset_settings_cache()


def test_employee_cannot_push(emp_headers, monkeypatch):
    monkeypatch.setenv("BENTO_LIFF_ID", LIFF_ID)
    reset_settings_cache()
    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as c:
        r = c.post("/api/admin/liff-push", json={"line_user_id": "Uabcdef1234567890"},
                   headers=emp_headers)
    assert r.status_code == 403
    reset_settings_cache()


# ------------------------------------------------- static frontend mount
def test_frontend_dir_serves_the_page_without_shadowing_api(tmp_path, monkeypatch):
    page = tmp_path / "index.html"
    page.write_text("<html><body>LIFF</body></html>", encoding="utf-8")
    monkeypatch.setenv("BENTO_FRONTEND_DIR", str(tmp_path))
    reset_settings_cache()
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        assert c.get("/health").status_code == 200
        assert "LIFF" in c.get("/").text
    reset_settings_cache()
