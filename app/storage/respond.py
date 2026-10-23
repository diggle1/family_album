"""HTML failure page or JSON. Secrets are not interpolated."""

from __future__ import annotations

from fastapi.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
from starlette.responses import Response

from app.storage.errors import MASKED_KEYS, failure_copy
from app.templating import templates


def wants_storage_json(request: Request) -> bool:
    if request.url.path.startswith("/api/"):
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


def render_storage_error(request: Request, code: str) -> Response:
    info = failure_copy(code)
    response = templates.TemplateResponse(
        request,
        "errors/storage.html",
        {
            "show_nav": False,
            "show_fab": False,
            "toast": None,
            "tab": None,
            "code": info["code"],
            "title": info["title"],
            "reason": info["reason"],
            "steps": info["steps"],
            "masked_keys": MASKED_KEYS,
        },
        status_code=503,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def storage_block_response(request: Request, code: str) -> Response:
    info = failure_copy(code)
    if wants_storage_json(request):
        response = JSONResponse(
            {"code": info["code"], "message": info["title"]},
            status_code=503,
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    if request.url.path.startswith("/errors/storage"):
        return render_storage_error(request, str(info["code"]))
    response = RedirectResponse(f"/errors/storage?code={info['code']}", status_code=303)
    response.headers["Cache-Control"] = "no-store"
    return response
