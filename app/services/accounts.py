"""Current user payload and profile edits."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Family, Membership, User
from app.services.validators import clean_short_text


def get_membership(db: Session, user_id: int) -> Membership | None:
    return db.scalar(select(Membership).where(Membership.user_id == user_id))


def get_family(db: Session, family_id: int) -> Family | None:
    return db.get(Family, family_id)


def me_payload(db: Session, user: User) -> dict:
    membership = get_membership(db, user.id)
    payload: dict = {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "membership": None,
    }
    if membership is not None:
        payload["membership"] = {
            "family_id": membership.family_id,
            "role": membership.role,
            "relation_label": membership.relation_label,
        }
    return payload


def patch_me(db: Session, user: User, fields: set[str], *, name: str | None, relation_label: str | None) -> dict:
    if "name" in fields:
        user.name = clean_short_text(name, message="姓名需要 1–40 个字")
    if "relation_label" in fields:
        membership = get_membership(db, user.id)
        if membership is None:
            raise HTTPException(status_code=400, detail="还没有家庭，不能填写关系")
        if relation_label is None:
            membership.relation_label = None
        else:
            label = relation_label.strip()
            if len(label) > 40:
                raise HTTPException(status_code=400, detail="关系需要不超过 40 个字")
            membership.relation_label = label or None
    db.commit()
    db.refresh(user)
    return me_payload(db, user)
