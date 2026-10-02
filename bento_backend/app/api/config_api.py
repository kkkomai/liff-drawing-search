"""Unauthenticated deployment/config endpoint for the 申請フォーム (spec 6.7).

The application form that is pushed through LINE must hand the user the
*correct* LIFF app URL for the environment it is talking to.  Hard-coding that
URL in the form is how staging links end up in production, so the form asks
this endpoint instead:

    GET /api/config  ->  {liff_app_url, liff_id, app_env, api_base_url, ...}

No secrets are exposed: only values a browser legitimately needs.  The
channel secret, the dev token and the session TTL never appear here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..timeutil import now_tokyo_iso

router = APIRouter(tags=["config"])


@router.get("/api/config")
def public_config(settings: Settings = Depends(get_settings)) -> dict:
    """Deployment facts a client (LIFF page or application form) needs.

    ``liff_app_url`` is ``null`` until BENTO_LIFF_ID / BENTO_LIFF_APP_URL is
    set — the response says so explicitly (``liff_configured: false``) instead
    of handing back a placeholder URL that would 404 in the LINE app.
    """
    return {
        "success": True,
        "app_env": settings.app_env,
        "api_base_url": settings.public_api_base_url,
        "liff_id": settings.liff_id,
        "liff_app_url": settings.effective_liff_app_url,
        "liff_configured": settings.liff_configured,
        "server_time": now_tokyo_iso(),
    }
