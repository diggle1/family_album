"""Session cookie for /setup/ok. Not a database column.

The admin's browser may open the success page after this session has
uploaded a child photo and then loaded it through GET /media (200).
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings

COOKIE = "setup_gate"


def _set(response: Response, value: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE,
        value,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def note_child_upload(request: Request, response: Response, *, role: str, kind: str) -> None:
    if role == "admin" and kind == "child" and request.cookies.get(COOKIE) != "ready":
        _set(response, "uploaded")


def note_child_media(request: Request, response: Response, *, role: str, kind: str) -> None:
    if role == "admin" and kind == "child" and request.cookies.get(COOKIE) in {"uploaded", "ready"}:
        _set(response, "ready")


def setup_ready(request: Request) -> bool:
    return request.cookies.get(COOKIE) == "ready"
