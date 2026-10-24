from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import MemberCtx, require_member, require_user_for_family
from app.elders_copy import ELDERS_NO_APP
from app.models import User
from app.schemas import FamilyIn, InviteIn, RoleIn
from app.services.elders import EldersError, apply_access_url, parse_json_object, paste_for_admin
from app.services.families import (
    accept_invite,
    create_family,
    create_invite,
    current_family_payload,
    list_members,
    set_role,
)

router = APIRouter(tags=["family"])


def _json(payload: dict, status: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.post("/api/families", status_code=201)
def post_family(
    body: FamilyIn,
    user: User = Depends(require_user_for_family),
    db: Session = Depends(get_db),
) -> dict:
    return create_family(db, user, body.name)


@router.get("/api/families/current")
def get_current(
    user: User = Depends(require_user_for_family),
    db: Session = Depends(get_db),
) -> JSONResponse:
    return _json(current_family_payload(db, user))


@router.patch("/api/families/current")
async def patch_current(
    request: Request,
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
) -> JSONResponse:
    if ctx.membership.role != "admin":
        raise EldersError(403, "access_url_forbidden", ELDERS_NO_APP["err_403"])
    payload = parse_json_object(await request.body())
    apply_access_url(db, ctx.family, payload)
    return _json(current_family_payload(db, ctx.user))


@router.post("/api/families/elders-paste")
async def post_elders_paste(
    request: Request,
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
) -> JSONResponse:
    if ctx.membership.role != "admin":
        raise EldersError(403, "elders_paste_forbidden", ELDERS_NO_APP["err_403"])
    entry = (ctx.family.family_access_url or "").strip()
    if not entry:
        raise EldersError(400, "access_url_required", ELDERS_NO_APP["empty_url_hint"])
    payload = parse_json_object(await request.body())
    return _json(paste_for_admin(db, family=ctx.family, payload=payload))


@router.post("/api/families/invites", status_code=201)
def post_invite(
    body: InviteIn,
    ctx: MemberCtx = Depends(require_member("admin")),
    db: Session = Depends(get_db),
) -> dict:
    return create_invite(
        db,
        family_id=ctx.family.id,
        created_by=ctx.user.id,
        email_raw=body.email,
        role=body.role,
    )


@router.post("/api/invites/{token}/accept")
def post_accept(
    token: str,
    user: User = Depends(require_user_for_family),
    db: Session = Depends(get_db),
) -> dict:
    # Accepting requires a session. A missing session is 403 so the body stays generic.
    # A logged-in user who cannot accept gets 400 without other families' private lists.
    return accept_invite(db, user, token)


@router.get("/api/members")
def get_members(ctx: MemberCtx = Depends(require_member()), db: Session = Depends(get_db)) -> dict:
    return list_members(db, family_id=ctx.family.id, include_invites=ctx.membership.role == "admin")


@router.patch("/api/members/{user_id}")
def patch_member(
    user_id: int,
    body: RoleIn,
    ctx: MemberCtx = Depends(require_member("admin")),
    db: Session = Depends(get_db),
) -> dict:
    return set_role(db, family_id=ctx.family.id, user_id=user_id, role=body.role)
