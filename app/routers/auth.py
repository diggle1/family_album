from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import clear_session, set_session
from app.db import get_db
from app.http import is_form
from app.schemas import EmailIn, VerifyIn
from app.services.accounts import me_payload
from app.services.auth_codes import request_code, verify_code

router = APIRouter(tags=["auth"])


@router.post("/auth/request-code")
def post_request_code(body: EmailIn, db: Session = Depends(get_db)) -> dict:
    return request_code(db, body.email)


@router.post("/auth/verify")
def post_verify(body: VerifyIn, db: Session = Depends(get_db)) -> JSONResponse:
    user = verify_code(db, body.email, body.code)
    payload = me_payload(db, user)
    payload["needs_profile"] = not bool(user.name)
    response = JSONResponse(payload)
    set_session(response, user.id)
    return response


@router.post("/auth/logout")
def post_logout(request: Request):
    if is_form(request):
        response = RedirectResponse("/", status_code=303)
        clear_session(response)
        return response
    response = JSONResponse({"ok": True})
    clear_session(response)
    return response
