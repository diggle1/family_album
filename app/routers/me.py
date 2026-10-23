from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_login
from app.models import User
from app.schemas import MePatch
from app.services.accounts import me_payload, patch_me

router = APIRouter(tags=["me"])


@router.get("/api/me")
def get_me(user: User = Depends(require_login), db: Session = Depends(get_db)) -> dict:
    return me_payload(db, user)


@router.patch("/api/me")
def update_me(
    body: MePatch,
    user: User = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    return patch_me(
        db,
        user,
        set(body.model_fields_set),
        name=body.name,
        relation_label=body.relation_label,
    )
