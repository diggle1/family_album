"""kids时光机. One process serves the API and the pages."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from starlette.datastructures import Headers, MutableHeaders

from app.config import get_settings, validate_settings
from app.constants import FORBIDDEN
from app.db import engine
from app.gate import StorageGateMiddleware
from app.http import safe_next, wants_json
from app.migrate_schema import migrate_schema
from app.routers import auth, children, deploy, family, journals, me, pages, photos, export, videos
from app.services.journals import JournalError
from app.services.videos import VideoError, sweep_video_partials
from app.services.export import sweep_temp_dir
from app.storage.errors import StorageError
from app.storage.factory import probe_code
from app.storage.respond import storage_block_response
from app.templating import templates

logger = logging.getLogger("family_album")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Default SECRET_KEY still refuses to boot. Storage problems must not.
    validate_settings(get_settings())
    _app.state.storage_error = None
    _app.state.db_error = None
    _app.state.storage_probe_done = asyncio.Event()
    try:
        migrate_schema()
    except Exception as exc:
        logger.warning("database migrate failed error=%s", type(exc).__name__)
        _app.state.db_error = "DB_DOWN"
    try:
        sweep_temp_dir()
    except Exception:
        logger.warning("export temp sweep failed")
    try:
        sweep_video_partials()
    except Exception:
        logger.warning("video partial sweep failed")

    async def _probe() -> None:
        try:
            _app.state.storage_error = await asyncio.to_thread(probe_code)
        finally:
            _app.state.storage_probe_done.set()

    task = asyncio.create_task(_probe())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def ping_db() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("database ping failed error=%s", type(exc).__name__)
        return False


class SecurityMiddleware:
    """CSRF origin check plus response headers, without BaseHTTPMiddleware.

    BaseHTTPMiddleware swallows endpoint exception handlers.
    """

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            host = headers.get("host")
            if origin:
                parsed = urlparse(origin)
                if parsed.scheme != scope.get("scheme") or parsed.netloc != host:
                    response = JSONResponse({"detail": FORBIDDEN}, status_code=403)
                    response.headers["Cache-Control"] = "no-store"
                    await response(scope, receive, send)
                    return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                raw = MutableHeaders(scope=message)
                raw.setdefault("X-Content-Type-Options", "nosniff")
                raw.setdefault("Referrer-Policy", "same-origin")
                raw.setdefault("X-Frame-Options", "DENY")
            await send(message)

        await self.app(scope, receive, send_with_headers)


app = FastAPI(title="kids时光机", version="2026.10.25.1", lifespan=lifespan)
app.add_middleware(StorageGateMiddleware)
app.add_middleware(SecurityMiddleware)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(auth.router)
app.include_router(me.router)
app.include_router(family.router)
app.include_router(children.router)
app.include_router(photos.router)
app.include_router(export.router)
app.include_router(journals.router)
app.include_router(videos.router)
app.include_router(pages.router)
app.include_router(deploy.router)


@app.get("/healthz")
def healthz(request: Request):
    """Process and database only. A bad media volume or S3 config stays healthy."""
    if getattr(request.app.state, "db_error", None) or not ping_db():
        return JSONResponse({"ok": False}, status_code=503)
    return {"ok": True}


@app.exception_handler(StorageError)
async def storage_failed(request: Request, exc: StorageError):
    request.app.state.storage_error = exc.code
    return storage_block_response(request, exc.code)


@app.exception_handler(OperationalError)
async def database_failed(request: Request, exc: OperationalError):
    logger.warning("database request failed error=%s", type(exc).__name__)
    request.app.state.db_error = "DB_DOWN"
    return storage_block_response(request, "DB_DOWN")


@app.exception_handler(VideoError)
async def video_failed(request: Request, exc: VideoError):
    path = request.url.path
    if wants_json(request) or path.startswith("/api/") or path.startswith("/media/"):
        response = JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status)
    elif exc.status == 403:
        response = templates.TemplateResponse(
            request,
            "403.html",
            {"show_nav": False, "toast": None},
            status_code=403,
        )
    elif exc.status == 404:
        response = templates.TemplateResponse(
            request,
            "video_missing.html",
            {"show_nav": False, "toast": None, "family": None, "user": None},
            status_code=404,
        )
    else:
        response = templates.TemplateResponse(
            request,
            "error.html",
            {"show_nav": False, "toast": None, "message": exc.detail, "next": "/"},
            status_code=exc.status,
        )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.exception_handler(JournalError)
async def journal_failed(request: Request, exc: JournalError):
    if wants_json(request) or request.url.path.startswith("/api/"):
        payload = {"detail": exc.detail, "code": exc.code}
        payload.update(exc.extra)
        response = JSONResponse(payload, status_code=exc.status)
    elif exc.status == 403:
        response = templates.TemplateResponse(
            request,
            "403.html",
            {"show_nav": False, "toast": None},
            status_code=403,
        )
        if request.url.path.startswith("/fragments/"):
            response.headers["HX-Redirect"] = "/403"
    elif exc.status == 404:
        response = templates.TemplateResponse(
            request,
            "journal_missing.html",
            {
                "show_nav": False,
                "toast": None,
                "family": None,
                "user": None,
                "recover_edit": False,
                "requested_id": "",
            },
            status_code=404,
        )
    else:
        response = templates.TemplateResponse(
            request,
            "error.html",
            {"show_nav": False, "toast": None, "message": exc.detail, "next": "/"},
            status_code=exc.status,
        )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "请求无法完成"
    if wants_json(request):
        response = JSONResponse({"detail": detail}, status_code=exc.status_code)
        if exc.status_code in {403, 404}:
            response.headers["Cache-Control"] = "no-store"
        if exc.status_code == 403 and request.url.path == "/api/export/my-photos.zip":
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response
    if exc.status_code == 404:
        response = templates.TemplateResponse(
            request,
            "error.html",
            {"show_nav": False, "toast": None, "message": detail, "next": "/"},
            status_code=404,
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    if exc.status_code == 403:
        response = templates.TemplateResponse(
            request,
            "403.html",
            {"show_nav": False, "toast": None},
            status_code=403,
        )
        response.headers["Cache-Control"] = "no-store"
        if request.url.path.startswith("/fragments/"):
            response.headers["HX-Redirect"] = "/403"
        return response
    if exc.status_code == 401:
        nxt = quote(safe_next(request.url.path), safe="")
        return RedirectResponse(f"/?next={nxt}", status_code=303)
    return templates.TemplateResponse(
        request,
        "error.html",
        {"show_nav": False, "toast": None, "message": detail, "next": "/"},
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logger.exception("unhandled error")
    if wants_json(request):
        return JSONResponse({"detail": "没加载出来"}, status_code=500)
    return templates.TemplateResponse(
        request,
        "error.html",
        {"show_nav": False, "toast": None, "message": "没加载出来", "next": safe_next(request.url.path)},
        status_code=500,
    )

