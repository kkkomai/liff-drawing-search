"""LIFF ID token verification tests.

A real RSA key pair is generated locally, a JWKS document is built from the
public key, and httpx.get is stubbed so no network call is made. This exercises
the production verification path (RS256, kid lookup, iss/aud/exp) rather than
the dev-token shortcut.
"""
from __future__ import annotations

import json
import time
from datetime import timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.security import line_token
from app.security.line_token import TokenError, verify_id_token

KID = "test-kid-1"


@pytest.fixture()
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk_for(rsa_key) -> dict:
    """Public JWK dict for an RSA private key (PyJWT's to_jwk wants a key object)."""
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key()))
    jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
    return jwk


@pytest.fixture()
def jwks_stub(rsa_key, monkeypatch):
    document = {"keys": [jwk_for(rsa_key)]}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return document

    calls = {"n": 0}

    def fake_get(url, timeout=None, **kwargs):
        calls["n"] += 1
        return FakeResponse()

    monkeypatch.setattr(line_token.httpx, "get", fake_get)
    return calls


@pytest.fixture()
def settings_env(monkeypatch):
    monkeypatch.setenv("BENTO_LINE_CHANNEL_ID", "1234567890")
    monkeypatch.setenv("BENTO_LINE_CHANNEL_SECRET", "secret-value")
    from app.config import get_settings

    get_settings.cache_clear()
    line_token._cache.clear()
    yield get_settings()
    get_settings.cache_clear()
    line_token._cache.clear()


def make_token(rsa_key, **overrides):
    now = int(time.time())
    claims = {
        "iss": "https://api.line.me",
        "aud": "1234567890",
        "sub": "Utest0002aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "iat": now,
        "exp": now + 3600,
        "nonce": "n-abc",
    }
    claims.update(overrides)
    headers = {"kid": KID, "alg": "RS256"}
    return jwt.encode(claims, rsa_key, algorithm="RS256", headers=headers)


def test_valid_token_is_accepted(settings_env, rsa_key, jwks_stub):
    claims = verify_id_token(settings_env, make_token(rsa_key))
    assert claims["sub"] == "Utest0002aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def test_expired_token_rejected(settings_env, rsa_key, jwks_stub):
    now = int(time.time())
    token = make_token(rsa_key, iat=now - 7200, exp=now - 3600)
    with pytest.raises(TokenError) as exc:
        verify_id_token(settings_env, token)
    assert exc.value.reason == "id_token_expired"


def test_wrong_audience_rejected(settings_env, rsa_key, jwks_stub):
    with pytest.raises(TokenError) as exc:
        verify_id_token(settings_env, make_token(rsa_key, aud="9999999999"))
    assert exc.value.reason == "invalid_id_token"


def test_wrong_issuer_rejected(settings_env, rsa_key, jwks_stub):
    with pytest.raises(TokenError):
        verify_id_token(settings_env, make_token(rsa_key, iss="https://evil.example"))


def test_token_signed_by_other_key_rejected(settings_env, rsa_key, jwks_stub):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(TokenError) as exc:
        verify_id_token(settings_env, make_token(other))
    assert exc.value.reason == "invalid_id_token"


def test_unknown_kid_rejected(settings_env, rsa_key, jwks_stub):
    token = jwt.encode(
        {"iss": "https://api.line.me", "aud": "1234567890", "sub": "x",
         "iat": int(time.time()), "exp": int(time.time()) + 60},
        rsa_key, algorithm="RS256", headers={"kid": "unknown", "alg": "RS256"},
    )
    with pytest.raises(TokenError):
        verify_id_token(settings_env, token)


def test_nonce_mismatch_rejected(settings_env, rsa_key, jwks_stub):
    with pytest.raises(TokenError) as exc:
        verify_id_token(settings_env, make_token(rsa_key), nonce="different")
    assert exc.value.reason == "nonce_mismatch"


def test_nonce_match_accepted(settings_env, rsa_key, jwks_stub):
    verify_id_token(settings_env, make_token(rsa_key), nonce="n-abc")


def test_jwks_is_cached(settings_env, rsa_key, jwks_stub):
    verify_id_token(settings_env, make_token(rsa_key))
    verify_id_token(settings_env, make_token(rsa_key))
    verify_id_token(settings_env, make_token(rsa_key))
    assert jwks_stub["n"] == 1, "JWKS should be fetched once and cached"


def test_garbage_token_rejected(settings_env, jwks_stub):
    with pytest.raises(TokenError):
        verify_id_token(settings_env, "not-a-jwt")


def test_none_algorithm_rejected(settings_env, rsa_key, jwks_stub):
    token = jwt.encode(
        {"iss": "https://api.line.me", "aud": "1234567890", "sub": "x",
         "iat": int(time.time()), "exp": int(time.time()) + 60},
        key="", algorithm="none", headers={"kid": KID},
    )
    with pytest.raises(TokenError) as exc:
        verify_id_token(settings_env, token)
    assert exc.value.reason == "invalid_id_token"


def test_login_with_real_id_token_succeeds(monkeypatch, rsa_key, jwks_stub, env):
    """End-to-end: /api/auth/login accepts a properly signed LINE ID token."""
    monkeypatch.setenv("BENTO_LINE_CHANNEL_ID", "1234567890")
    monkeypatch.setenv("BENTO_LINE_CHANNEL_SECRET", "secret-value")
    from app import config

    config.reset_settings_cache()
    line_token._cache.clear()

    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        line_id = "Utest0002aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        from app.db import get_conn
        from app.timeutil import now_tokyo_iso

        conn = get_conn()
        with conn:
            conn.execute(
                "INSERT INTO employees (employee_code, name, line_user_id, role, created_at)"
                " VALUES ('E002', '社員02', ?, 'employee', ?)",
                (line_id, now_tokyo_iso()),
            )
        token = make_token(rsa_key, sub=line_id)
        resp = client.post("/api/auth/login", json={"line_user_id": line_id, "id_token": token})
        check = resp.status_code == 200, resp.text
        assert check, resp.text
        assert resp.json()["employee"]["employee_code"] == "E002"

        # A token for a different LINE user must not authenticate this one.
        other = make_token(rsa_key, sub="Utest9999aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        resp = client.post("/api/auth/login", json={"line_user_id": line_id, "id_token": other})
        assert resp.status_code == 401

    config.reset_settings_cache()
    line_token._cache.clear()