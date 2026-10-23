"""Input parsing. Messages never include family or child names."""

from __future__ import annotations

import re
from datetime import date

from fastapi import HTTPException

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_email(raw: str | None) -> str:
    email = (raw or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 255:
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    return email


def clean_short_text(raw: str | None, *, message: str, max_len: int = 40) -> str:
    text = (raw or "").strip()
    if not text or len(text) > max_len:
        raise HTTPException(status_code=400, detail=message)
    return text


def parse_iso_date(raw: str | None, *, invalid: str, missing: str | None = None) -> date | None:
    if raw is None or str(raw).strip() == "":
        if missing:
            raise HTTPException(status_code=400, detail=missing)
        return None
    text = str(raw).strip()
    if not DATE_RE.match(text):
        raise HTTPException(status_code=400, detail=invalid)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise HTTPException(status_code=400, detail=invalid) from None


def parse_optional_int(raw: str | int | None) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if text == "":
        return None
    if not text.isdigit():
        raise HTTPException(status_code=400, detail="孩子不在当前家庭")
    return int(text)
