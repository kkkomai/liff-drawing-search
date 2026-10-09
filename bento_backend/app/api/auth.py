"""Authentication endpoints (spec 4.1).

POST /api/auth/login
    Body: {"line_user_id", "id_token"} (id_token verified against LINE JWKS)
    -> 200 session token + employee, or 403 when the LINE ID is not on the
       employee master, or 401 when the ID token itself cannot be trusted.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from ..config import Settings, get_settings
from ..db import bind, get_conn
from ..errors import ApiError, forbidden, unauthorized
from ..models import EmployeeOut, LoginRequest, LoginResponse
from ..security.line_token import TokenError, verify_id_token
from ..security.sessions import create_session, revoke_session
from ..timeutil import now_tokyo_iso
from .deps import current_employee

router = APIRouter(prefix="/api/auth", tags=["auth"])

logger = logging.getLogger("uvicorn.error")


def _verify_line_identity(body: LoginRequest, settings: Settings) -> None:
    if settings.dev_auth_enabled and body.dev_token == settings.dev_auth_token:
        return  # explicit local/dev escape hatch, off unless configured
    if not body.id_token:
        # Falling back to line_user_id alone is off by default: without the ID
        # token the value is client-supplied, so accepting it would let anyone
        # log in as any employee just by changing a string. Operators can opt in
        # explicitly for deployments where the LIFF app cannot issue tokens (a
        # LIFF app created without the openid scope never gets one).
        if not settings.allow_login_without_id_token:
            raise unauthorized(
                "id_token がありません。LINE コンソールの LIFF 設定で Scope に openid が含まれているか確認し、"
                "『更新』を押してから LINE アプリを完全に終了し、再度起動してください"
                "（LIFF の認証コンテキストは起動時にキャッシュされます）。"
                "IDトークンを必須としない運用にする場合は BENTO_ALLOW_LOGIN_WITHOUT_ID_TOKEN=true を設定してください。"
            )
        logger.warning(
            "login accepted without an ID token for line_user_id=%s "
            "because allow_login_without_id_token is enabled",
            body.line_user_id,
        )
        return
    if not settings.line_verification_enabled:
        raise ApiError(
            503,
            "id_token_verification_unavailable",
            "IDトークン検証が構成されていません（BENTO_LINE_CHANNEL_ID / BENTO_LINE_CHANNEL_SECRET を設定してください）。",
        )
    try:
        claims = verify_id_token(settings, body.id_token, nonce=body.nonce)
    except TokenError as exc:
        raise unauthorized(f"{exc.message}") from exc
    sub = claims.get("sub")
    if sub and sub != body.line_user_id:
        raise unauthorized("line_user_id が IDトークンの内容と一致しません。")


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, settings: Settings = Depends(get_settings)) -> LoginResponse:
    _verify_line_identity(body, settings)

    conn = get_conn()
    row = conn.execute(
        bind("SELECT id, employee_code, name, role, is_active FROM employees WHERE line_user_id = ?"),
        (body.line_user_id,),
    ).fetchone()
    # Any registered line_user_id is allowed in, regardless of the
    # ``is_active`` flag — the user-facing app is gated by membership in
    # the employees table, not by an admin-controlled per-row toggle.
    # The bento admin tooling can still flip ``is_active`` to hide a row
    # from the ordering screens without locking the user out of the rest
    # of the application. (Historical: the original code required
    # ``is_active = TRUE`` here, which silently excluded deactivated users
    # from the form site too.)
    if row is None:
        raise forbidden(
            "unauthorized_user",
            "このLINEアカウントは社員マスタに登録されていません。",
        )

    with conn:
        session = create_session(conn, row["id"], settings)
    return LoginResponse(
        token=session["token"],
        expires_at=session["expires_at"],
        employee=EmployeeOut(
            id=row["id"],
            employee_code=row["employee_code"],
            name=row["name"],
            role=row["role"],
        ),
    )


@router.post("/logout")
def logout(request: Request, employee: dict = Depends(current_employee)) -> dict:
    header = request.headers.get("Authorization") or ""
    token = header[7:].strip()
    conn = get_conn()
    with conn:
        revoke_session(conn, token)
    return {"success": True}


@router.get("/me")
def me(employee: dict = Depends(current_employee)) -> dict:
    return {
        "success": True,
        "employee": {
            "id": employee["employee_id"],
            "employee_code": employee["employee_code"],
            "name": employee["name"],
            "role": employee["role"],
        },
        "server_time": now_tokyo_iso(),
    }