"""申請フォーム integration: hand the correct LIFF app URL to the user.

Two paths, both environment-driven so the same code serves dev/staging/prod:

1. :func:`get_liff_app_link` — a pure lookup of the configured LIFF app URL,
   used by ``GET /api/config`` and by the push below.  Fails loudly rather
   than returning a placeholder that would 404 inside the LINE app.
2. :func:`push_liff_app_url` — pushes a LINE text message containing that URL
   to a user via the LINE Messaging API.  This is the automated stand-in for
   the 申請フォーム push: with the real channel access token configured it
   sends for real, and without it the function refuses (no silent no-op).

Nothing here ever logs or returns the channel access token.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ..config import Settings

logger = logging.getLogger("bento.line_form")


class LineFormError(RuntimeError):
    """Raised when the LIFF app URL cannot be produced or pushed."""

    def __init__(self, error: str, message: str):
        super().__init__(message)
        self.error = error
        self.message = message


@dataclass(frozen=True)
class LiffAppLink:
    """What the application form must return to the user."""

    liff_app_url: str
    liff_id: str | None
    app_env: str

    def as_message(self) -> str:
        return (
            "お弁当注文の申し込みフォームです。\n"
            "下のリンクからお弁当の注文・確認をお願いします。\n"
            f"{self.liff_app_url}"
        )


def get_liff_app_link(settings: Settings) -> LiffAppLink:
    """Return the environment's LIFF app link, or raise.

    Refusing (rather than returning ``None``/a dummy URL) is deliberate: a
    form that hands back a broken link is worse than a form that reports
    "not configured", because the user cannot tell the difference.
    """
    url = settings.effective_liff_app_url
    if not url:
        raise LineFormError(
            "liff_not_configured",
            "LIFFアプリURLが未設定です。環境変数 BENTO_LIFF_ID（または BENTO_LIFF_APP_URL）を設定してください。",
        )
    if not url.startswith("https://liff.line.me/"):
        raise LineFormError(
            "liff_app_url_invalid",
            f"LIFFアプリURLが不正です: {url}（https://liff.line.me/<LIFF ID> 形式である必要があります）",
        )
    return LiffAppLink(liff_app_url=url, liff_id=settings.liff_id, app_env=settings.app_env)


def push_liff_app_url(
    settings: Settings,
    to: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 10.0,
) -> dict:
    """Push the LIFF app URL to one LINE user id.

    ``to`` is a LINE user id (``U...``).  Returns the Messaging API response
    body on success; raises :class:`LineFormError` otherwise (missing
    credential, non-2xx, or a malformed user id) so the caller can retry or
    alert instead of believing the user got the link.
    """
    link = get_liff_app_link(settings)

    token = (settings.line_channel_access_token or "").strip()
    if not token:
        raise LineFormError(
            "line_channel_access_token_missing",
            "BENTO_LINE_CHANNEL_ACCESS_TOKEN が未設定のため送信できません（LINE Developers コンソールで発行してください）。",
        )
    if not to or not to.startswith("U"):
        raise LineFormError("invalid_line_user_id", f"LINEユーザーIDが不正です: {to!r}")

    url = f"{settings.line_messaging_api_base.rstrip('/')}/v2/bot/message/push"
    payload = {"to": to, "messages": [{"type": "text", "text": link.as_message()}]}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    owned = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        resp = http.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise LineFormError("line_api_unreachable", f"LINE Messaging API に接続できません: {exc}") from exc
    finally:
        if owned:
            http.close()

    if resp.status_code >= 300:
        # The body can echo request details; log only the status and a short
        # body prefix, never the token.
        logger.warning("LINE push failed: %s %s", resp.status_code, resp.text[:200])
        raise LineFormError(
            "line_api_error",
            f"LINE Messaging API がエラーを返しました（HTTP {resp.status_code}）。",
        )
    return {"sent": True, "liff_app_url": link.liff_app_url, "app_env": link.app_env}
