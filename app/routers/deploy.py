"""Public setup checklist and the storage failure page."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.constants import FORBIDDEN
from app.db import get_db
from app.deps import optional_user
from app.gate import recheck_storage
from app.services.accounts import get_membership
from app.setup_gate import setup_ready
from app.storage.respond import render_storage_error
from app.templating import templates

router = APIRouter(tags=["deploy"])


def _page(request: Request, name: str, context: dict, status_code: int = 200):
    payload = {
        "show_nav": False,
        "show_fab": False,
        "toast": None,
        "tab": None,
    }
    payload.update(context)
    return templates.TemplateResponse(request, name, payload, status_code=status_code)


@router.get("/setup")
def setup_page(request: Request):
    return _page(request, "setup.html", {"ready": setup_ready(request)})


@router.get("/setup/ok")
def setup_ok(request: Request, db: Session = Depends(get_db)):
    user = optional_user(request, db)
    if user is None:
        return RedirectResponse("/?next=/setup/ok", status_code=303)
    membership = get_membership(db, user.id)
    if membership is None or membership.role != "admin":
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    if not setup_ready(request):
        return RedirectResponse("/setup", status_code=303)
    return _page(request, "setup_ok.html", {})


@router.get("/errors/storage")
async def storage_error_page(request: Request):
    await recheck_storage(request.app)
    code = getattr(request.app.state, "db_error", None) or getattr(request.app.state, "storage_error", None)
    if not code:
        return RedirectResponse("/", status_code=303)
    return render_storage_error(request, code)
