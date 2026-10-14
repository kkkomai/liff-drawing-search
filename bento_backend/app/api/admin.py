"""Admin endpoints (spec 4.4): all-employee status for a date or a period,
plus the 申請フォーム → LIFF app URL push (admin only).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..db import bind, get_conn
from ..errors import ApiError, bad_request, conflict, forbidden, not_found, unauthorized
from ..models import AdminDayOut, AdminRangeOut, AdminSummary
from ..services.line_form import LineFormError, get_liff_app_link, push_liff_app_url
from ..services.orders import admin_day, admin_range
from ..timeutil import parse_date, today_tokyo, now_tokyo_iso
from .admin_auth import current_admin_user
from .deps import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])

_LINE_USER_ID_MIN = 8
_LINE_USER_ID_MAX = 100


class LiffPushRequest(BaseModel):
    """Send the LIFF app URL to one employee (the 申請フォーム push)."""

    model_config = {"extra": "ignore"}

    line_user_id: str = Field(min_length=8, max_length=100)


class EmployeeCreateRequest(BaseModel):
    """Add one employee to the master (社員マスタ)."""

    model_config = {"extra": "ignore"}

    employee_code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=50)
    line_user_id: str | None = Field(default=None, min_length=_LINE_USER_ID_MIN, max_length=_LINE_USER_ID_MAX)
    role: str = Field(default="employee", pattern=r"^(employee|admin)$")
    is_active: int = Field(default=1)


class EmployeeUpdateRequest(BaseModel):
    """Edit one employee. Every field optional (partial update)."""

    model_config = {"extra": "ignore"}

    employee_code: str | None = Field(default=None, min_length=1, max_length=20)
    name: str | None = Field(default=None, min_length=1, max_length=50)
    line_user_id: str | None = Field(default=None, min_length=_LINE_USER_ID_MIN, max_length=_LINE_USER_ID_MAX)
    role: str | None = Field(default=None, pattern=r"^(employee|admin)$")
    is_active: int | None = None


def _employee_row(row) -> dict:
    return {
        "id": row["id"],
        "employee_code": row["employee_code"],
        "name": row["name"],
        "line_user_id": row["line_user_id"],
        "role": row["role"],
        "is_active": row["is_active"],
        "created_at": row["created_at"],
    }


def _fetch_employee(conn, employee_id: int):
    row = conn.execute(
        bind(
            "SELECT id, employee_code, name, line_user_id, role, is_active, created_at"
            " FROM employees WHERE id = ?"
        ),
        (employee_id,),
    ).fetchone()
    return dict(row) if row else None


@router.get("/employees")
def admin_list_employees(_: dict = Depends(current_admin_user)) -> dict:
    """社員マスタ一覧 (管理ページ用)。LIFF ではなく管理ページ認証で保護。"""
    conn = get_conn()
    rows = conn.execute(
        bind(
            "SELECT id, employee_code, name, line_user_id, role, is_active, created_at"
            " FROM employees ORDER BY employee_code"
        )
    ).fetchall()
    return {"success": True, "employees": [_employee_row(r) for r in rows]}


@router.post("/employees")
def admin_create_employee(body: EmployeeCreateRequest, _: dict = Depends(current_admin_user)) -> dict:
    conn = get_conn()
    line_id = (body.line_user_id or "").strip() or None
    try:
        with conn:
            row = conn.execute(
                bind(
                    "INSERT INTO employees (employee_code, name, line_user_id, role, is_active, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?) RETURNING id, employee_code, name, line_user_id, role, is_active, created_at"
                ),
                (
                    body.employee_code.strip(),
                    body.name.strip(),
                    line_id,
                    body.role,
                    1 if body.is_active else 0,
                    now_tokyo_iso(),
                ),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 - unique violation etc.
        # どの一意制約かをエラーメッセージのカラム名で判定する。
        # SQLite: "UNIQUE constraint failed: employees.line_user_id"
        # Postgres: "... employees_line_user_id_key ..."
        # 注: "unique" 単体では判定しない（どの制約かも全く同じ語を含むため）。
        msg_l = str(exc).lower()
        if "employee_code" in msg_l:
            raise conflict("duplicate_employee_code", f"社員コード「{body.employee_code}」は既に使用されています。") from exc
        if "line_user_id" in msg_l:
            raise conflict("duplicate_line_user_id", "このLINEユーザーIDは既に他の社員に登録されています。") from exc
        raise bad_request("create_failed", f"登録に失敗しました: {exc}") from exc
    return {"success": True, "employee": _employee_row(row)}


@router.put("/employees/{employee_id}")
def admin_update_employee(employee_id: int, body: EmployeeUpdateRequest, _: dict = Depends(current_admin_user)) -> dict:
    conn = get_conn()
    current = _fetch_employee(conn, employee_id)
    if current is None:
        raise not_found("指定された社員は存在しません。")
    updates: dict = {}
    if body.employee_code is not None:
        updates["employee_code"] = body.employee_code.strip()
    if body.name is not None:
        updates["name"] = body.name.strip()
    if body.line_user_id is not None:
        updates["line_user_id"] = body.line_user_id.strip() or None
    if body.role is not None:
        updates["role"] = body.role
    if body.is_active is not None:
        updates["is_active"] = 1 if body.is_active else 0
    if not updates:
        return {"success": True, "employee": _employee_row(current)}
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [employee_id]
    try:
        with conn:
            row = conn.execute(
                bind(
                    f"UPDATE employees SET {set_clause} WHERE id = ?"
                    " RETURNING id, employee_code, name, line_user_id, role, is_active, created_at"
                ),
                params,
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        # create と同じカラム名ベースの判定（"unique" 単体では判別不能）。
        msg_l = str(exc).lower()
        if "employee_code" in msg_l:
            raise conflict("duplicate_employee_code", "この社員コードは既に使用されています。") from exc
        if "line_user_id" in msg_l:
            raise conflict("duplicate_line_user_id", "このLINEユーザーIDは既に他の社員に登録されています。") from exc
        raise bad_request("update_failed", f"更新に失敗しました: {exc}") from exc
    return {"success": True, "employee": _employee_row(row)}


@router.delete("/employees/{employee_id}")
def admin_delete_employee(employee_id: int, _: dict = Depends(current_admin_user)) -> dict:
    conn = get_conn()
    current = _fetch_employee(conn, employee_id)
    if current is None:
        raise not_found("指定された社員は存在しません。")
    with conn:
        conn.execute(bind("DELETE FROM employees WHERE id = ?"), (employee_id,))
    return {"success": True, "deleted_id": employee_id}


@router.get("/orders", response_model=AdminDayOut | AdminRangeOut)
def admin_orders(
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    start_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    _: dict = Depends(require_admin),
) -> AdminDayOut | AdminRangeOut:
    conn = get_conn()
    try:
        if date:
            if start_date or end_date:
                raise bad_request(
                    "invalid_params", "date と start_date/end_date は同時に指定できません。"
                )
            return AdminDayOut(**admin_day(conn, parse_date(date)))
        if start_date and end_date:
            return AdminRangeOut(**admin_range(conn, parse_date(start_date), parse_date(end_date)))
        if start_date or end_date:
            raise bad_request(
                "invalid_params", "start_date と end_date は両方指定してください。"
            )
    except ValueError as exc:
        raise bad_request("invalid_date", str(exc)) from exc
    # default: today
    return AdminDayOut(**admin_day(conn, today_tokyo()))


@router.get("/audit")
def admin_audit(
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(default=200, ge=1, le=1000),
    _: dict = Depends(require_admin),
) -> dict:
    from ..services.orders import list_audit_logs

    try:
        day = parse_date(date) if date else None
    except ValueError as exc:
        raise bad_request("invalid_date", str(exc)) from exc
    return {"success": True, "logs": list_audit_logs(get_conn(), day=day, limit=limit)}


@router.get("/summary")
def admin_summary(_: dict = Depends(require_admin)) -> AdminSummary:
    conn = get_conn()
    total = conn.execute(
        bind("SELECT COUNT(*) AS c FROM employees WHERE is_active = 1")
    ).fetchone()["c"]
    row = conn.execute(
        bind(
            "SELECT"
            " SUM(CASE WHEN status = 'needed' THEN 1 ELSE 0 END) AS needed,"
            " SUM(CASE WHEN status = 'not_needed' THEN 1 ELSE 0 END) AS not_needed"
            " FROM bento_orders WHERE date = ?"
        ),
        (today_tokyo().isoformat(),),
    ).fetchone()
    needed = row["needed"] or 0
    not_needed = row["not_needed"] or 0
    return AdminSummary(
        total_employees=total,
        needed_count=needed,
        not_needed_count=not_needed,
        unregistered_count=max(total - needed - not_needed, 0),
    )


@router.get("/liff-link")
def admin_liff_link(_: dict = Depends(require_admin)) -> dict:
    """The exact LIFF app URL this deployment hands back to users.

    Admin-gated so an unauthenticated visitor cannot enumerate the app id.
    """
    from ..config import get_settings

    try:
        link = get_liff_app_link(get_settings())
    except LineFormError as exc:
        raise ApiError(503, exc.error, exc.message) from exc
    return {
        "success": True,
        "liff_app_url": link.liff_app_url,
        "liff_id": link.liff_id,
        "app_env": link.app_env,
        "message": link.as_message(),
    }


@router.post("/liff-push")
def admin_liff_push(
    body: LiffPushRequest, _: dict = Depends(require_admin)
) -> dict:
    """Push the LIFF app URL to one employee over the LINE Messaging API.

    Admin-only, and it fails loudly (503/502) when the channel access token is
    missing or LINE rejects the push — a silently dropped application form is
    the failure mode this endpoint exists to prevent.
    """
    from ..config import get_settings

    try:
        return {"success": True, **push_liff_app_url(get_settings(), body.line_user_id)}
    except LineFormError as exc:
        status = 503 if exc.error in ("liff_not_configured", "line_channel_access_token_missing") else 502
        raise ApiError(status, exc.error, exc.message) from exc
