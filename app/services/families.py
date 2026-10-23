"""Family, invite, and role changes. One user, one membership."""

from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import ROLES
from app.models import Family, Invite, Membership, User
from app.services.accounts import get_membership
from app.services.clock import as_utc, isoformat, utcnow
from app.services.validators import clean_short_text, normalize_email


def create_family(db: Session, user: User, name_raw: str) -> dict:
    if get_membership(db, user.id) is not None:
        raise HTTPException(status_code=400, detail="已经加入家庭")
    name = clean_short_text(name_raw, message="家庭名称需要 1–40 个字")
    family = Family(name=name)
    db.add(family)
    db.flush()
    db.add(Membership(user_id=user.id, family_id=family.id, role="admin"))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="已经加入家庭") from None
    db.refresh(family)
    return {
        "id": family.id,
        "name": family.name,
        "created_at": isoformat(family.created_at),
        "role": "admin",
    }


def current_family_payload(db: Session, user: User) -> dict:
    membership = get_membership(db, user.id)
    if membership is None:
        raise HTTPException(status_code=404, detail="还没有家庭")
    family = db.get(Family, membership.family_id)
    if family is None:
        raise HTTPException(status_code=404, detail="还没有家庭")
    return {
        "id": family.id,
        "name": family.name,
        "created_at": isoformat(family.created_at),
        "role": membership.role,
        "relation_label": membership.relation_label,
    }


def _user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def create_invite(db: Session, *, family_id: int, created_by: int, email_raw: str, role: str) -> dict:
    email = normalize_email(email_raw)
    if role not in ROLES:
        raise HTTPException(status_code=400, detail="权限档位无效")
    existing = _user_by_email(db, email)
    if existing is not None and get_membership(db, existing.id) is not None:
        raise HTTPException(status_code=400, detail="已是家庭成员")
    settings = get_settings()
    invite = Invite(
        family_id=family_id,
        email=email,
        role=role,
        token=secrets.token_urlsafe(32),
        created_by=created_by,
        expires_at=utcnow() + timedelta(days=settings.invite_ttl_days),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite_payload(invite)


def invite_payload(invite: Invite) -> dict:
    return {
        "id": invite.id,
        "email": invite.email,
        "role": invite.role,
        "token": invite.token,
        "expires_at": isoformat(invite.expires_at),
        "accept_path": f"/invites/{invite.token}",
    }


def _open_invite(db: Session, token: str) -> Invite:
    invite = db.scalar(select(Invite).where(Invite.token == token))
    if invite is None or invite.accepted_at is not None or as_utc(invite.expires_at) <= utcnow():
        raise HTTPException(status_code=400, detail="邀请无效或已过期")
    return invite


def accept_invite(db: Session, user: User, token: str) -> dict:
    invite = _open_invite(db, token)
    if user.email != invite.email:
        raise HTTPException(status_code=400, detail="请使用受邀邮箱登录后再接受")
    if get_membership(db, user.id) is not None:
        raise HTTPException(status_code=400, detail="已经加入家庭")
    family = db.get(Family, invite.family_id)
    if family is None:
        raise HTTPException(status_code=400, detail="邀请无效或已过期")
    db.add(Membership(user_id=user.id, family_id=family.id, role=invite.role))
    invite.accepted_at = utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="已经加入家庭") from None
    return {
        "ok": True,
        "family_id": family.id,
        "family_name": family.name,
        "role": invite.role,
        "needs_profile": not bool(user.name),
    }


def list_members(db: Session, *, family_id: int, include_invites: bool) -> dict:
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.family_id == family_id)
        .order_by(Membership.id.asc())
    ).all()
    members = []
    for membership, user in rows:
        item = {
            "user_id": user.id,
            "name": user.name,
            "role": membership.role,
            "relation_label": membership.relation_label,
        }
        if include_invites:
            item["email"] = user.email
        members.append(item)
    payload: dict = {"members": members}
    if include_invites:
        now = utcnow()
        invites = db.scalars(
            select(Invite)
            .where(Invite.family_id == family_id, Invite.accepted_at.is_(None))
            .order_by(Invite.id.desc())
        ).all()
        payload["invites"] = [invite_payload(item) for item in invites if as_utc(item.expires_at) > now]
    return payload


def set_role(db: Session, *, family_id: int, user_id: int, role: str) -> dict:
    if role not in ROLES:
        raise HTTPException(status_code=400, detail="权限档位无效")
    row = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.family_id == family_id, Membership.user_id == user_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    membership, user = row
    if membership.role == "admin" and role != "admin":
        admins = db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.family_id == family_id, Membership.role == "admin")
        )
        if (admins or 0) <= 1:
            raise HTTPException(status_code=400, detail="不能降级家庭里最后一位家长管理员")
    membership.role = role
    db.commit()
    return {
        "user_id": user.id,
        "name": user.name,
        "role": membership.role,
        "relation_label": membership.relation_label,
    }


def preview_invite(db: Session, token: str) -> Invite | None:
    """Return an open invite, or None when it cannot be shown. Never raises a name."""
    invite = db.scalar(select(Invite).where(Invite.token == token))
    if invite is None or invite.accepted_at is not None or as_utc(invite.expires_at) <= utcnow():
        return None
    return invite
