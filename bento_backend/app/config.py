"""Application settings, loaded from environment variables (prefix ``BENTO_``).

Every deployment-sensitive value (DB path, LINE credentials, CORS origins,
LIFF app URL, dev-auth token) lives here so nothing is hard-coded in business
logic.  ``BENTO_APP_ENV`` (``dev`` / ``staging`` / ``prod``) selects the
environment: the same build is pointed at a dev backend, a staging backend or
production purely by environment variables, and the production guard rails
(:func:`assert_production_ready`) refuse to start a prod deployment that still
holds dev credentials.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated, List, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger("uvicorn.error")

AppEnv = Literal["dev", "staging", "prod"]

# Every LIFF app is served from this origin, so it is always allowed in
# addition to whatever BENTO_CORS_ORIGINS lists (staging/dev hostnames).
_BASE_ALLOWED = {"https://liff.line.me"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BENTO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- deployment identity ----------------------------------------------
    app_env: AppEnv = Field(default="dev")
    # Public base URL of *this* backend, handed to clients so a LIFF page or an
    # application form never has to hard-code an API host.
    public_api_base_url: str = Field(default="http://localhost:8000")

    # --- storage -----------------------------------------------------------
    database_path: str = Field(default="data/bento.db")

    # --- LINE LIFF / ID token verification --------------------------------
    line_channel_id: str | None = Field(default=None)
    line_channel_secret: str | None = Field(default=None)
    liff_issuer: str = Field(default="https://api.line.me")
    liff_jwks_url: str = Field(default="https://api.line.me/oauth2/v3/keys")
    jwks_cache_seconds: int = Field(default=3600)

    # --- LINE Messaging API (pushing the LIFF app URL to users) -----------
    # Long-lived channel access token from the LINE Developers console.
    line_channel_access_token: str | None = Field(default=None)
    line_messaging_api_base: str = Field(default="https://api.line.me")

    # --- static frontend ---------------------------------------------------
    # When set, the API also serves the LIFF page from this directory, so one
    # HTTPS origin hosts both the page and /api (no CORS at all in prod).
    frontend_dir: str | None = Field(default=None)

    # --- LIFF app identity (what the 申請フォーム hands back to users) -----
    # App id as issued by the LINE Developers console, e.g. 1234567890-AbcdEfgh
    liff_id: str | None = Field(default=None)
    # Full LIFF app URL, e.g. https://liff.line.me/1234567890-AbcdEfgh
    liff_app_url: str | None = Field(default=None)

    # --- sessions ----------------------------------------------------------
    session_ttl_hours: int = Field(default=24 * 7)

    # --- dev escape hatch --------------------------------------------------
    # When set, POST /api/auth/login accepts {"line_user_id": ..., "dev_token": ...}
    # instead of a real LINE ID token. MUST be empty in production.
    dev_auth_token: str | None = Field(default=None)

    # --- tokenless login ---------------------------------------------------
    # A LIFF app created without the openid scope never receives an ID token, so
    # the token cannot be verified. When this is true, /api/auth/login falls back
    # to trusting the client-supplied line_user_id.
    #
    # SECURITY: this removes the only thing proving the LINE user behind that id.
    # Anyone can POST any line_user_id and impersonate any employee on the master.
    # Keep it off unless the deployment genuinely cannot issue tokens, and prefer
    # creating a new LIFF app with openid instead — see the skill notes.
    allow_login_without_id_token: bool = Field(default=False)

    # --- http --------------------------------------------------------------
    # Comma-separated or JSON list; NoDecode stops pydantic-settings from
    # trying to JSON-parse before our validator runs.
    cors_origins: Annotated[List[str], NoDecode] = Field(default_factory=list)
    # Additional per-environment origins (staging/dev hostnames).
    extra_cors_origins: Annotated[List[str], NoDecode] = Field(default_factory=list)

    @field_validator("cors_origins", "extra_cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                import json

                return json.loads(v)
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("public_api_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return (v or "").rstrip("/")

    @property
    def line_verification_enabled(self) -> bool:
        return bool(self.line_channel_id and self.line_channel_secret)

    @property
    def dev_auth_enabled(self) -> bool:
        return bool(self.dev_auth_token)

    @property
    def is_production(self) -> bool:
        return self.app_env == "prod"

    @property
    def effective_liff_app_url(self) -> str | None:
        """The URL an application form must hand back to a user.

        Prefers an explicit ``BENTO_LIFF_APP_URL``; otherwise derives the
        canonical LIFF URL from the app id, so the two can never drift.
        """
        if self.liff_app_url:
            return self.liff_app_url.rstrip("/")
        if self.liff_id:
            return f"https://liff.line.me/{self.liff_id.strip()}"
        return None

    @property
    def effective_cors_origins(self) -> List[str]:
        return sorted(_BASE_ALLOWED | set(self.cors_origins) | set(self.extra_cors_origins))

    @property
    def liff_configured(self) -> bool:
        return bool(self.effective_liff_app_url)


class ProductionNotReady(RuntimeError):
    """Raised when a prod deployment is missing a hard requirement."""


def assert_production_ready(settings: Settings) -> list[str]:
    """Fail fast when a prod deployment is misconfigured.

    Returns the list of satisfied checks on success.  Raises
    :class:`ProductionNotReady` listing *every* problem at once, so a release
    engineer fixes them in one pass instead of one deploy at a time.
    """
    if not settings.is_production:
        return []

    problems: list[str] = []
    if settings.dev_auth_enabled:
        problems.append("BENTO_DEV_AUTH_TOKEN は本番では必ず空にすること（dev 抜け穴が残っています）")
    if not settings.line_verification_enabled:
        problems.append(
            "BENTO_LINE_CHANNEL_ID / BENTO_LINE_CHANNEL_SECRET が未設定（IDトークン検証ができません）"
        )
    if not settings.liff_configured:
        problems.append("BENTO_LIFF_ID または BENTO_LIFF_APP_URL が未設定（申請フォームのURLを返せません）")
    if not settings.public_api_base_url.lower().startswith("https://"):
        problems.append(
            "BENTO_PUBLIC_API_BASE_URL は https で始まる必要があります（現在: {settings.public_api_base_url}）".format(
                settings=settings
            )
        )
    if any(o == "*" for o in settings.cors_origins):
        problems.append("BENTO_CORS_ORIGINS に '*' は本番で使えません（LIFFオリジンを列挙してください）")
    if problems:
        raise ProductionNotReady("本番設定が未完了です:\n- " + "\n- ".join(problems))
    notes = [
        "BENTO_APP_ENV=prod",
        "dev escape hatch が無効",
        "LIFFアプリURLが設定済み",
        "公開API URLが https",
        "CORS Origins が明示許可リスト",
    ]
    if settings.allow_login_without_id_token:
        # Not a startup problem — the deployment can legitimately run this way —
        # but it removes the only proof of who the LINE user is, so make it
        # visible in the startup log rather than leaving it as a silent
        # downgrade of the auth path.
        notes.append("注意: IDトークン検証が実質無効（line_user_id の信頼のみ）")
        logger.warning(
            "allow_login_without_id_token=true: line_user_id is trusted from the client "
            "with no token verification, so any caller can impersonate any employee"
        )
    else:
        notes.append("LINE IDトークン検証が有効")
    return notes


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test helper: drop the cached Settings so env changes take effect."""
    get_settings.cache_clear()