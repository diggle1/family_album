"""Auth dependencies. Family content without a membership is 403, not a name leak."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import read_user_id
from app.constants import FORBIDDEN, ROLE_RANK
from app.db import get_db
from app.models import Family, Membership, User
from app.services.accounts import get_family, get_membership


@dataclass
class MemberCtx:
    user: User
    membership: Membership
    family: Family


def _user(request: Request, db: Session, *, status: int) -> User:
    uid = read_user_id(request)
    detail = FORBIDDEN if status == 403 else "请先登录"
    if uid is None:
        raise HTTPException(status_code=status, detail=detail)
    user = db.get(User, uid)
    if user is None:
        raise HTTPException(status_code=status, detail=detail)
    return user


def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    """Logged-in user. Missing session is 401. Used by /api/me."""
    return _user(request, db, status=401)


def require_user_for_family(request: Request, db: Session = Depends(get_db)) -> User:
    """Logged-in user for family creation. Missing session is 403 (M4)."""
    return _user(request, db, status=403)


def optional_user(request: Request, db: Session) -> User | None:
    uid = read_user_id(request)
    if uid is None:
        return None
    return db.get(User, uid)


def require_member(min_role: str | None = None):
    def dependency(request: Request, db: Session = Depends(get_db)) -> MemberCtx:
        user = _user(request, db, status=403)
        membership = get_membership(db, user.id)
        if membership is None:
            raise HTTPException(status_code=403, detail=FORBIDDEN)
        family = get_family(db, membership.family_id)
        if family is None:
            raise HTTPException(status_code=403, detail=FORBIDDEN)
        if min_role is not None and ROLE_RANK[membership.role] < ROLE_RANK[min_role]:
            raise HTTPException(status_code=403, detail=FORBIDDEN)
        return MemberCtx(user=user, membership=membership, family=family)

    return dependency
