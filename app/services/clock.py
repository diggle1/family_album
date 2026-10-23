"""Dates, Shanghai calendar day, and 「几岁几个月」."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
_WEEK = "一二三四五六日"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat()


def shanghai_today() -> date:
    return datetime.now(SHANGHAI).date()


def age_parts(birth: date, on: date) -> tuple[int, int]:
    years = on.year - birth.year
    months = on.month - birth.month
    if on.day < birth.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12
    return years, months


def age_label(birth: date, on: date) -> str:
    """「N岁M个月」; under one month is 「不足1个月」."""
    years, months = age_parts(birth, on)
    if years < 0 or (years == 0 and months <= 0):
        return "不足1个月"
    if years == 0:
        return f"{months}个月"
    if months == 0:
        return f"{years}岁"
    return f"{years}岁{months}个月"


def date_label(value: date) -> str:
    return f"{value.month}月{value.day}日"


def weekday_zh(value: date) -> str:
    return "周" + _WEEK[value.weekday()]
