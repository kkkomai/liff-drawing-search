"""Employee-facing order endpoints (spec 4.2, 4.3, 5.1).

* ``GET  /api/orders``  — own orders in a period (no lower bound on history age).
* ``POST /api/orders`` / ``PUT /api/orders`` — upsert today or a future date.
* ``DELETE /api/orders/{date}`` — clear today's/future entry (audit logged).
* ``GET  /api/orders/audit`` — own change history (tamper-evidence trail).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..db import get_conn
from ..errors import bad_request
from ..models import OrderListResponse, OrderOut, OrderUpsertRequest, OrderUpsertResponse
from ..services.orders import delete_order, list_audit_logs, list_orders, upsert_order
from ..timeutil import add_days, parse_date, today_tokyo
from .deps import current_employee

router = APIRouter(prefix="/api/orders", tags=["orders"])

# Guard rails: history is unbounded in age (spec 1.1) but a single response is
# capped so one request cannot ask for 20 years of days.
MAX_RANGE_DAYS = 400
# Defaults used when the client sends no range: ~1 year back, ~2 months ahead.
DEFAULT_PAST_DAYS = 330
DEFAULT_FUTURE_DAYS = 60


@router.get("", response_model=OrderListResponse)
def get_orders(
    start_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    employee: dict = Depends(current_employee),
) -> OrderListResponse:
    today = today_tokyo()
    try:
        start = parse_date(start_date) if start_date else add_days(today, -DEFAULT_PAST_DAYS)
        end = parse_date(end_date) if end_date else add_days(today, DEFAULT_FUTURE_DAYS)
    except ValueError as exc:
        raise bad_request("invalid_date", str(exc)) from exc
    if end < start:
        raise bad_request("invalid_range", "end_date は start_date 以降を指定してください。")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise bad_request(
            "range_too_large", f"一度に取得できる期間は{MAX_RANGE_DAYS}日以内です。"
        )

    rows = list_orders(get_conn(), employee["employee_id"], start, end)
    return OrderListResponse(
        start_date=start.isoformat(), end_date=end.isoformat(), orders=[OrderOut(**r) for r in rows]
    )


@router.post("", response_model=OrderUpsertResponse, status_code=200)
@router.put("", response_model=OrderUpsertResponse, status_code=200)
def put_order(body: OrderUpsertRequest, employee: dict = Depends(current_employee)) -> OrderUpsertResponse:
    try:
        day = parse_date(body.date)
    except ValueError as exc:
        raise bad_request("invalid_date", str(exc)) from exc

    result = upsert_order(
        get_conn(),
        employee["employee_id"],
        day,
        body.status,
        actor=employee["employee_code"],
    )
    message = "注文を保存しました。" if result["changed"] else "すでに同じ内容で登録されています。"
    return OrderUpsertResponse(
        message=message,
        order=OrderOut(date=result["date"], status=result["status"], updated_at=result["updated_at"]),
    )


@router.delete("/{date_value}")
def delete_order_endpoint(
    date_value: str, employee: dict = Depends(current_employee)
) -> dict:
    try:
        day = parse_date(date_value)
    except ValueError as exc:
        raise bad_request("invalid_date", str(exc)) from exc
    delete_order(get_conn(), employee["employee_id"], day, actor=employee["employee_code"])
    return {"success": True, "message": "注文を取り消しました。"}


@router.get("/audit")
def audit(employee: dict = Depends(current_employee)) -> dict:
    logs = list_audit_logs(get_conn(), employee_id=employee["employee_id"])
    return {"success": True, "logs": logs}