"""kids时光机. One process serves the API and the pages."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders

from app.config import get_settings, validate_settings
from app.constants import FORBIDDEN
from app.db import Base, engine
from app.http import safe_next, wants_json
from app.routers import auth, children, family, me, pages, photos
from app.templating import templates

logger = logging.getLogger("family_album")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    validate_settings(settings)
    settings.photo_dir.mkdir(parents=True, exist_ok=True)
    import app.models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    yield


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


app = FastAPI(title="kids时光机", version="2026.10.23.1", lifespan=lifespan)
app.add_middleware(SecurityMiddleware)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(auth.router)
app.include_router(me.router)
app.include_router(family.router)
app.include_router(children.router)
app.include_router(photos.router)
app.include_router(pages.router)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "请求无法完成"
    if wants_json(request):
        response = JSONResponse({"detail": detail}, status_code=exc.status_code)
        if exc.status_code == 403:
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

