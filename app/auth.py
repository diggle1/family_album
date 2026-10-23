"""Signed httpOnly session cookie. SameSite=Lax is the CSRF control."""

from __future__ import annotations

from fastapi import Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.requests import Request

from app.config import get_settings

COOKIE = "session"


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.secret_key, salt="family-album-session")


def set_session(response: Response, user_id: int) -> None:
    settings = get_settings()
    token = _serializer().dumps({"uid": user_id})
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def read_user_id(request: Request) -> int | None:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    settings = get_settings()
    try:
        data = _serializer().loads(raw, max_age=settings.session_ttl_seconds)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict):
        return None
    uid = data.get("uid")
    if isinstance(uid, bool) or not isinstance(uid, int):
        return None
    return uid
