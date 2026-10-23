"""Block business routes when storage or the database is down.

/healthz, /setup, /static, and the failure page stay up. The probe result
lives on app.state and does not crash lifespan.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.migrate_schema import migrate_schema
from app.storage.factory import probe_code
from app.storage.respond import storage_block_response

logger = logging.getLogger("family_album.storage")


def _exempt(path: str) -> bool:
    if path == "/healthz" or path == "/setup":
        return True
    return path.startswith("/static") or path.startswith("/errors/storage")


def _recheck(scope: Scope) -> bool:
    raw = scope.get("query_string", b"")
    if isinstance(raw, str):
        raw = raw.encode()
    values = parse_qs(raw.decode("latin-1"))
    return values.get("recheck", ["0"])[0] == "1"


async def recheck_storage(app) -> None:
    """Run migrate and the storage probe again. Used by the retry button."""

    def _db() -> str | None:
        try:
            migrate_schema()
        except Exception as exc:
            logger.warning("database migrate failed error=%s", type(exc).__name__)
            return "DB_DOWN"
        return None

    app.state.db_error = await asyncio.to_thread(_db)
    app.state.storage_error = await asyncio.to_thread(probe_code)


class StorageGateMiddleware:
    def __init__(self, asgi_app: ASGIApp) -> None:
        self.app = asgi_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if _exempt(path):
            await self.app(scope, receive, send)
            return
        app = scope.get("app")
        if app is None:
            await self.app(scope, receive, send)
            return
        done = getattr(app.state, "storage_probe_done", None)
        if done is not None:
            await done.wait()
        if _recheck(scope):
            await recheck_storage(app)
        code = getattr(app.state, "db_error", None) or getattr(app.state, "storage_error", None)
        if not code:
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        response = storage_block_response(request, code)
        await response(scope, receive, send)
