"""Email codes. No SMTP. AUTH_TEST_CODE is an extra accepted value after a request."""

from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuthCode, User
from app.services.clock import as_utc, utcnow
from app.services.validators import normalize_email


def _same(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return secrets.compare_digest(left, right)


def request_code(db: Session, email_raw: str) -> dict:
    email = normalize_email(email_raw)
    settings = get_settings()
    db.execute(delete(AuthCode).where(AuthCode.email == email))
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(
        AuthCode(
            email=email,
            code=code,
            expires_at=utcnow() + timedelta(seconds=settings.code_ttl_seconds),
        )
    )
    db.commit()
    return {"ok": True}


def verify_code(db: Session, email_raw: str, code_raw: str) -> User:
    email = normalize_email(email_raw)
    submitted = (code_raw or "").strip()
    row = db.scalar(select(AuthCode).where(AuthCode.email == email).order_by(AuthCode.id.desc()))
    now = utcnow()
    if row is None or as_utc(row.expires_at) <= now:
        raise HTTPException(status_code=400, detail="验证码无效或已过期")
    settings = get_settings()
    ok = _same(row.code, submitted)
    if settings.auth_test_code and _same(settings.auth_test_code, submitted):
        ok = True
    if not ok:
        raise HTTPException(status_code=400, detail="验证码无效或已过期")
    db.execute(delete(AuthCode).where(AuthCode.email == email))
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email)
        db.add(user)
    db.commit()
    db.refresh(user)
    return user
