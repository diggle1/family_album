from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import MemberCtx, require_member
from app.schemas import ChildIn
from app.services.children import build_timeline, child_payload, create_child, list_children, require_child

router = APIRouter(tags=["children"])


@router.post("/api/children", status_code=201)
def post_child(
    body: ChildIn,
    ctx: MemberCtx = Depends(require_member("admin")),
    db: Session = Depends(get_db),
) -> dict:
    child = create_child(db, family_id=ctx.family.id, name_raw=body.name, birth_raw=body.birth_date)
    return child_payload(child)


@router.get("/api/children")
def get_children(ctx: MemberCtx = Depends(require_member()), db: Session = Depends(get_db)) -> dict:
    return {"children": [child_payload(child) for child in list_children(db, ctx.family.id)]}


@router.get("/api/children/{child_id}/timeline")
def get_timeline(
    child_id: int,
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
) -> dict:
    child = require_child(db, family_id=ctx.family.id, child_id=child_id)
    return build_timeline(db, child)
