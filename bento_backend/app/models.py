"""Request/response schemas (pydantic v2)."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["needed", "not_needed"]
Role = Literal["employee", "admin"]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    line_user_id: str = Field(min_length=8, max_length=100)
    id_token: str | None = Field(default=None, max_length=4096)
    nonce: str | None = Field(default=None, max_length=128)
    dev_token: str | None = Field(default=None, max_length=256)


class EmployeeOut(BaseModel):
    id: int
    employee_code: str
    name: str
    role: Role


class LoginResponse(BaseModel):
    success: bool = True
    token: str
    expires_at: str
    employee: EmployeeOut


class OrderUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    status: Status


class OrderOut(BaseModel):
    date: str
    status: Status
    updated_at: str | None = None


class OrderUpsertResponse(BaseModel):
    success: bool = True
    message: str
    order: OrderOut


class OrderListResponse(BaseModel):
    success: bool = True
    start_date: str
    end_date: str
    orders: list[OrderOut]


class AdminEmployeeStatus(BaseModel):
    employee_id: int
    employee_code: str
    name: str
    status: Literal["needed", "not_needed", "unregistered"]
    updated_at: str | None = None


class AdminSummary(BaseModel):
    total_employees: int
    needed_count: int
    not_needed_count: int
    unregistered_count: int


class AdminDayOut(BaseModel):
    date: str
    summary: AdminSummary
    employees_status: list[AdminEmployeeStatus]


class AdminRangeOut(BaseModel):
    start_date: str
    end_date: str
    total_employees: int
    days: list[AdminDayOut]


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    message: str


class AuditLogOut(BaseModel):
    id: int
    employee_id: int
    date: str
    old_status: Status | None
    new_status: Status
    action: str
    changed_at: str
    changed_by: str


def validate_date_range(start: str, end: str) -> tuple[date, date]:
    from .timeutil import parse_date

    s = parse_date(start)
    e = parse_date(end)
    if e < s:
        raise ValueError("end_date must be on or after start_date")
    return s, e