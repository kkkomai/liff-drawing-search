"""LINE LIFF ID token verification.

Verifies the ``id_token`` issued by ``liff.getDecodedIDToken()`` against LINE's
JWKS endpoint (RS256), checking issuer, audience (channel id), expiry and
nonce. Results are cached briefly per ``kid`` to avoid a JWKS round-trip on
every request.

If ``BENTO_LINE_CHANNEL_ID`` / ``BENTO_LINE_CHANNEL_SECRET`` are not configured
the app refuses to trust ID tokens unless an explicit dev token is supplied
(``BENTO_DEV_AUTH_TOKEN``), so a misconfigured deployment fails closed.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx
import jwt
from jwt import PyJWK

from ..config import Settings


class TokenError(Exception):
    """Raised when an ID token cannot be trusted."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


class JwksCache:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._lock = threading.Lock()
        self._keys: dict[str, Any] = {}
        self._fetched_at: float = 0.0

    def _fresh(self) -> bool:
        return bool(self._keys) and (
            time.time() - self._fetched_at < self._settings.jwks_cache_seconds
        )

    def get(self, kid: str) -> Any:
        with self._lock:
            if not self._fresh():
                self._refresh()
            key = self._keys.get(kid)
            if key is None:
                # force one refresh: LINE rotates keys
                self._fetched_at = 0.0
                self._refresh()
                key = self._keys.get(kid)
            if key is None:
                raise TokenError("invalid_id_token", "ID tokenの署名を検証できませんでした。")
            return key

    def _refresh(self) -> None:
        try:
            resp = httpx.get(self._settings.liff_jwks_url, timeout=5.0)
            resp.raise_for_status()
            jwks = resp.json()
        except Exception as exc:  # network/HTTP/JSON failure
            if self._keys:  # serve stale rather than break every request
                return
            raise TokenError("jwks_unavailable", f"JWKSの取得に失敗しました: {exc}") from exc
        keys: dict[str, Any] = {}
        for jwk in jwks.get("keys", []):
            kid = jwk.get("kid")
            if not kid:
                continue
            try:
                keys[kid] = PyJWK.from_dict(jwk).key
            except Exception:
                continue
        if not keys:
            raise TokenError("jwks_unavailable", "JWKSに有効な鍵がありません。")
        self._keys = keys
        self._fetched_at = time.time()


_cache: dict[str, JwksCache] = {}
_cache_lock = threading.Lock()


def _cache_for(settings: Settings) -> JwksCache:
    with _cache_lock:
        cache = _cache.get(settings.liff_jwks_url)
        if cache is None:
            cache = JwksCache(settings)
            _cache[settings.liff_jwks_url] = cache
        return cache


def verify_id_token(
    settings: Settings, id_token: str, *, nonce: str | None = None
) -> dict:
    """Return the verified claims, or raise :class:`TokenError`."""
    if not settings.line_verification_enabled:
        raise TokenError(
            "id_token_verification_disabled",
            "IDトークン検証が設定されていません。",
        )
    try:
        header = jwt.get_unverified_header(id_token)
    except Exception as exc:
        raise TokenError("invalid_id_token", "ID tokenの形式が不正です。") from exc

    kid = header.get("kid")
    if not kid:
        raise TokenError("invalid_id_token", "ID tokenにkidがありません。")
    if header.get("alg") != "RS256":
        raise TokenError("invalid_id_token", "想定外の署名アルゴリズムです。")

    key = _cache_for(settings).get(kid)
    try:
        claims = jwt.decode(
            id_token,
            key=key,
            algorithms=["RS256"],
            audience=settings.line_channel_id,
            issuer=settings.liff_issuer,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("id_token_expired", "ID tokenの有効期限が切れています。") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid_id_token", "ID tokenを検証できませんでした。") from exc

    if nonce is not None and claims.get("nonce") not in (None, nonce):
        raise TokenError("nonce_mismatch", "nonceが一致しません。")
    return claims


def decode_unverified_sub(id_token: str) -> str | None:
    """Best-effort ``sub`` extraction, used only to cross-check the client claim."""
    try:
        claims = jwt.decode(id_token, options={"verify_signature": False})
    except Exception:
        return None
    sub = claims.get("sub")
    return sub if isinstance(sub, str) else None


def pretty(token: str) -> str:  # pragma: no cover - debug helper
    try:
        return json.dumps(json.loads(jwt.decode(token, options={"verify_signature": False})), ensure_ascii=False)
    except Exception:
        return "<undecodable>"