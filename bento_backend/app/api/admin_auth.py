"""Admin page auth endpoints (setup / login / logout / me).

These back /admin.html — a plain browser page outside the LINE LIFF app.
The credential model is deliberately separate from the LIFF one
(username + pbkdf2 password in ``admin_users``, opaque session in
``admin_sessions``); see ``app/security/admin_auth.py``.

Session delivery is a cookie (``admin_session``) so the page JavaScript
never touches the token. The same token is also accepted as a
``Authorization: Bearer`` header for scripted/API use.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ..db import get_conn
from ..errors import bad_request, unauthorized
from ..security import admin_auth

router = APIRouter(prefix="/api/admin-auth", tags=["admin-auth"])

COOKIE_NAME = "admin_session"
_SESSION_MAX_AGE = 12 * 60 * 60  # 12h, matches the session TTL


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(default="", max_length=50)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


def _admin_token(request: Request) -> str | None:
    """Read the admin session token from the cookie, else the Bearer header."""
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        return cookie
    header = request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def current_admin_user(request: Request) -> dict:
    """FastAPI dependency: resolve the logged-in admin user or 401."""
    token = _admin_token(request)
    if not token:
        raise unauthorized("管理ページへのログインが必要です。")
    admin = admin_auth.resolve_admin_session(get_conn(), token)
    if admin is None:
        raise unauthorized("セッションが無効か期限切れです。再度ログインしてください。")
    return admin


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


@router.get("/status")
def auth_status() -> dict:
    """Whether the admin page still needs first-run setup."""
    conn = get_conn()
    return {"setup_required": admin_auth.count_admin_users(conn) == 0}


@router.post("/setup")
def setup(body: SetupRequest, response: Response) -> dict:
    """Create the FIRST admin user. Refuses once one exists — the setup
    endpoint is a one-shot bootstrap, not a registration endpoint."""
    conn = get_conn()
    if admin_auth.count_admin_users(conn) > 0:
        raise bad_request("already_setup", "管理者は既に設定されています。ログインしてください。")
    if not body.username.replace("_", "").replace("-", "").isalnum():
        raise bad_request("invalid_username", "ユーザー名は英数字・_・- のみ使用できます。")
    try:
        user = admin_auth.create_admin_user(
            conn, body.username, body.password, body.display_name or body.username
        )
        with conn:
            pass  # commit the insert
    except Exception as exc:  # noqa: BLE001 - unique username collision etc.
        raise bad_request("setup_failed", f"管理者の作成に失敗しました: {exc}") from exc
    session = admin_auth.create_admin_session(conn, user["id"])
    with conn:
        pass
    _set_session_cookie(response, session["token"])
    return {"success": True, "admin": user, "token": session["token"], "expires_at": session["expires_at"]}


@router.post("/login")
def login(body: LoginRequest, response: Response) -> dict:
    conn = get_conn()
    user = admin_auth.find_admin_user(conn, body.username)
    # Same message for "no such user" and "wrong password" so the endpoint
    # cannot be used to enumerate valid usernames.
    if user is None or not admin_auth.verify_password(body.password, user["password_hash"]):
        raise unauthorized("ユーザー名またはパスワードが違います。")
    session = admin_auth.create_admin_session(conn, user["id"])
    with conn:
        pass
    _set_session_cookie(response, session["token"])
    user.pop("password_hash", None)
    return {"success": True, "admin": user, "token": session["token"], "expires_at": session["expires_at"]}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    token = _admin_token(request)
    if token:
        conn = get_conn()
        admin_auth.revoke_admin_session(conn, token)
        with conn:
            pass
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"success": True}


@router.get("/me")
def me(admin: dict = Depends(current_admin_user)) -> dict:
    return {"success": True, "admin": admin}
