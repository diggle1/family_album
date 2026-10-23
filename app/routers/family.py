from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import MemberCtx, require_member, require_user_for_family
from app.models import User
from app.schemas import FamilyIn, InviteIn, RoleIn
from app.services.families import (
    accept_invite,
    create_family,
    create_invite,
    current_family_payload,
    list_members,
    set_role,
)

router = APIRouter(tags=["family"])


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
) -> dict:
    return current_family_payload(db, user)


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
