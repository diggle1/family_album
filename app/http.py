"""JSON vs HTML responses, and a closed set of toast keys."""

from __future__ import annotations

from starlette.requests import Request

from app.constants import TOASTS

_API_PREFIXES = ("/api/", "/auth/", "/media/")


def wants_json(request: Request) -> bool:
    path = request.url.path
    if any(path.startswith(prefix) for prefix in _API_PREFIXES):
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


def is_form(request: Request) -> bool:
    ctype = request.headers.get("content-type", "")
    return "application/x-www-form-urlencoded" in ctype or "multipart/form-data" in ctype


def safe_next(raw: str | None, default: str = "/") -> str:
    if not raw:
        return default
    if not raw.startswith("/") or raw.startswith("//") or raw.startswith("/\\"):
        return default
    if "\\" in raw or "\n" in raw or "\r" in raw:
        return default
    return raw


def toast_text(key: str | None) -> str | None:
    if not key:
        return None
    return TOASTS.get(key)
