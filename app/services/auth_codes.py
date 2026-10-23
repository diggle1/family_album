"""Email login codes.

No SMTP. The 6-digit code is stored as HMAC-SHA256. When AUTH_TEST_CODE is
set, that value is accepted in addition to the real code after request-code.

Limits, counted from the direct client IP (forwarded headers are ignored):
- 60 seconds between request-code calls for the same email
- 20 request-code calls per IP per hour, 50 per IP per day
- 5 failed verifies per email, and 30 per IP, inside the code TTL window

A new request-code clears that email's failure count. The IP failure count
waits out the window, so asking for another code cannot reset a spray.
Success clears the email count as well.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuthCode, AuthCodeRequest, AuthThrottle, User
from app.services.clock import as_utc, utcnow
from app.services.validators import normalize_email

EMAIL_REQUEST_COOLDOWN_SECONDS = 60
IP_REQUEST_HOURLY_LIMIT = 20
IP_REQUEST_DAILY_LIMIT = 50
VERIFY_EMAIL_LIMIT = 5
VERIFY_IP_LIMIT = 30

_VERIFY_EMAIL = "verify_email"
_VERIFY_IP = "verify_ip"
_INVALID = "验证码无效或已过期"
_EMAIL_LOCKED = "这个邮箱的验证码尝试次数过多，请重新获取验证码或稍后再试"
_IP_LOCKED = "当前网络验证码尝试次数过多，请稍后再试"
_IP_HOURLY = "当前网络本小时获取验证码次数已达上限，请稍后再试"
_IP_DAILY = "当前网络今天获取验证码次数已达上限，请明天再试"


def code_digest(email: str, code: str) -> str:
    secret = get_settings().secret_key.encode()
    payload = email.encode() + b"\0" + code.encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _same(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return secrets.compare_digest(left, right)


def _throttle(db: Session, kind: str, key: str) -> AuthThrottle | None:
    return db.scalar(select(AuthThrottle).where(AuthThrottle.kind == kind, AuthThrottle.bucket_key == key))


def _window_active(row: AuthThrottle, now: datetime, ttl: int) -> bool:
    if ttl <= 0:
        return False
    return as_utc(row.window_started) + timedelta(seconds=ttl) > now


def _clear_email_failures(db: Session, email: str) -> None:
    db.execute(delete(AuthThrottle).where(AuthThrottle.kind == _VERIFY_EMAIL, AuthThrottle.bucket_key == email))


def _reject_if_verify_locked(db: Session, *, email: str, ip: str, now: datetime) -> None:
    ttl = get_settings().code_ttl_seconds
    checks = (
        (_VERIFY_EMAIL, email, VERIFY_EMAIL_LIMIT, _EMAIL_LOCKED),
        (_VERIFY_IP, ip, VERIFY_IP_LIMIT, _IP_LOCKED),
    )
    for kind, key, limit, detail in checks:
        row = _throttle(db, kind, key)
        if row is None or not _window_active(row, now, ttl):
            continue
        if row.failures >= limit:
            raise HTTPException(status_code=429, detail=detail)


def _note_verify_failure(db: Session, *, email: str, ip: str, now: datetime) -> None:
    ttl = get_settings().code_ttl_seconds
    for kind, key in ((_VERIFY_EMAIL, email), (_VERIFY_IP, ip)):
        row = _throttle(db, kind, key)
        if row is None:
            row = AuthThrottle(kind=kind, bucket_key=key, failures=0, window_started=now)
            db.add(row)
        elif not _window_active(row, now, ttl):
            row.failures = 0
            row.window_started = now
        row.failures += 1
    # Commit before raising. The request session rolls back on the way out.
    db.commit()


def _request_rows(db: Session) -> list[AuthCodeRequest]:
    return list(db.scalars(select(AuthCodeRequest)).all())


def _reject_if_request_limited(db: Session, *, email: str, ip: str, now: datetime) -> None:
    # Compare timestamps in Python. SQLite does not keep a stable datetime type.
    day_ago = now - timedelta(days=1)
    hour_ago = now - timedelta(hours=1)
    live = [row for row in _request_rows(db) if as_utc(row.created_at) >= day_ago]
    same_email = [row for row in live if row.email == email]
    if same_email:
        latest = max(same_email, key=lambda row: as_utc(row.created_at))
        elapsed = (now - as_utc(latest.created_at)).total_seconds()
        if elapsed < EMAIL_REQUEST_COOLDOWN_SECONDS:
            wait = max(1, math.ceil(EMAIL_REQUEST_COOLDOWN_SECONDS - elapsed))
            raise HTTPException(status_code=429, detail=f"请求过于频繁，请 {wait} 秒后再获取验证码")
    same_ip = [row for row in live if row.ip == ip]
    hourly = sum(1 for row in same_ip if as_utc(row.created_at) >= hour_ago)
    if hourly >= IP_REQUEST_HOURLY_LIMIT:
        raise HTTPException(status_code=429, detail=_IP_HOURLY)
    if len(same_ip) >= IP_REQUEST_DAILY_LIMIT:
        raise HTTPException(status_code=429, detail=_IP_DAILY)


def _drop_expired_requests(db: Session, now: datetime) -> None:
    cutoff = now - timedelta(days=1)
    for row in _request_rows(db):
        if as_utc(row.created_at) < cutoff:
            db.delete(row)


def request_code(db: Session, email_raw: str, *, ip: str) -> dict:
    email = normalize_email(email_raw)
    now = utcnow()
    _reject_if_request_limited(db, email=email, ip=ip, now=now)
    _drop_expired_requests(db, now)
    db.execute(delete(AuthCode).where(AuthCode.email == email))
    _clear_email_failures(db, email)
    code = f"{secrets.randbelow(1_000_000):06d}"
    settings = get_settings()
    db.add(
        AuthCode(
            email=email,
            code=code_digest(email, code),
            expires_at=now + timedelta(seconds=settings.code_ttl_seconds),
        )
    )
    db.add(AuthCodeRequest(email=email, ip=ip, created_at=now))
    db.commit()
    return {"ok": True}


def verify_code(db: Session, email_raw: str, code_raw: str, *, ip: str) -> User:
    email = normalize_email(email_raw)
    submitted = (code_raw or "").strip()
    now = utcnow()
    _reject_if_verify_locked(db, email=email, ip=ip, now=now)
    row = db.scalar(select(AuthCode).where(AuthCode.email == email).order_by(AuthCode.id.desc()))
    live = row is not None and as_utc(row.expires_at) > now
    ok = False
    if live:
        ok = _same(row.code, code_digest(email, submitted))
        settings = get_settings()
        if settings.auth_test_code and _same(settings.auth_test_code, submitted):
            ok = True
    if not ok:
        _note_verify_failure(db, email=email, ip=ip, now=now)
        raise HTTPException(status_code=400, detail=_INVALID)
    _clear_email_failures(db, email)
    db.execute(delete(AuthCode).where(AuthCode.email == email))
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email)
        db.add(user)
    db.commit()
    db.refresh(user)
    return user
