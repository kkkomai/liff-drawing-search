"""Authentication endpoints (spec 4.1).

POST /api/auth/login
    Body: {"line_user_id", "id_token"} (id_token verified against LINE JWKS)
    -> 200 session token + employee, or 403 when the LINE ID is not on the
       employee master, or 401 when the ID token itself cannot be trusted.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..config import Settings, get_settings
from ..db import get_conn
from ..errors import ApiError, forbidden, unauthorized
from ..models import EmployeeOut, LoginRequest, LoginResponse
from ..security.line_token import TokenError, verify_id_token
from ..security.sessions import create_session, revoke_session
from ..timeutil import now_tokyo_iso
from .deps import current_employee

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _verify_line_identity(body: LoginRequest, settings: Settings) -> None:
    if settings.dev_auth_enabled and body.dev_token == settings.dev_auth_token:
        return  # explicit local/dev escape hatch, off unless configured
    if not body.id_token:
        # Previously this bare message sent the user to debug the server. In
        # practice an empty jwt means the LIFF client context was minted before
        # the openid scope was saved in the LINE console, and the WebView keeps
        # serving that cached context — so name the actual cause and the fix.
        raise unauthorized(
            "id_token がありません。LINE コンソールの LIFF 設定で Scope に openid が含まれているか確認し、"
            "『更新』を押してから LINE アプリを完全に終了し、再度起動してください"
            "（LIFF の認証コンテキストは起動時にキャッシュされます）。"
        )
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
        "SELECT id, employee_code, name, role, is_active FROM employees WHERE line_user_id = ?",
        (body.line_user_id,),
    ).fetchone()
    if row is None or not row["is_active"]:
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