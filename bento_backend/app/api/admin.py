"""Admin endpoints (spec 4.4): all-employee status for a date or a period,
plus the 申請フォーム → LIFF app URL push (admin only).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..db import get_conn
from ..errors import ApiError, bad_request
from ..models import AdminDayOut, AdminRangeOut, AdminSummary
from ..services.line_form import LineFormError, get_liff_app_link, push_liff_app_url
from ..services.orders import admin_day, admin_range
from ..timeutil import parse_date, today_tokyo
from .deps import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


class LiffPushRequest(BaseModel):
    """Send the LIFF app URL to one employee (the 申請フォーム push)."""

    model_config = {"extra": "ignore"}

    line_user_id: str = Field(min_length=8, max_length=100)


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
        "SELECT COUNT(*) AS c FROM employees WHERE is_active = 1"
    ).fetchone()["c"]
    row = conn.execute(
        "SELECT"
        " SUM(CASE WHEN status = 'needed' THEN 1 ELSE 0 END) AS needed,"
        " SUM(CASE WHEN status = 'not_needed' THEN 1 ELSE 0 END) AS not_needed"
        " FROM bento_orders WHERE date = ?",
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
