"""Auth dependencies: bearer session -> employee, plus role gate."""
from __future__ import annotations

from fastapi import Depends, Request

from ..db import get_conn
from ..errors import forbidden, unauthorized
from ..security.sessions import resolve_session


def _bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def current_employee(request: Request) -> dict:
    token = _bearer(request)
    if not token:
        raise unauthorized("認証トークンがありません。")
    conn = get_conn()
    employee = resolve_session(conn, token)
    if employee is None:
        raise unauthorized("認証が無効か、期限切れです。再度ログインしてください。")
    return employee


def require_admin(employee: dict = Depends(current_employee)) -> dict:
    if employee["role"] != "admin":
        raise forbidden("admin_required", "この操作は管理者のみ実行できます。")
    return employee