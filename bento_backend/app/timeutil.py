"""Asia/Tokyo date/time helpers.

All business rules in this system are defined against Japan time, independent
of the server's local timezone.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TOKYO = ZoneInfo("Asia/Tokyo")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def now_tokyo() -> datetime:
    return datetime.now(TOKYO)


def now_tokyo_iso() -> str:
    return now_tokyo().isoformat(timespec="seconds")


def today_tokyo() -> date:
    return now_tokyo().date()


def parse_date(value: str) -> date:
    """Strict ``YYYY-MM-DD`` parser. Raises ValueError on anything else."""
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise ValueError("date must be formatted as YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:  # e.g. 2026-02-30
        raise ValueError("invalid calendar date") from exc


def to_iso(d: date) -> str:
    return d.isoformat()


def is_past(d: date, *, today: date | None = None) -> bool:
    return d < (today or today_tokyo())


def add_days(d: date, days: int) -> date:
    return d + timedelta(days=days)