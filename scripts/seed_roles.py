"""Four-role fixture for a self-hosted Phase A box.

Not a production default. Create the rows, then temporarily set
AUTH_TEST_CODE=000000 to sign in. Clear that variable before real use.
One user still has at most one membership.
"""

import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import func, select

from app.db import SessionLocal
from app.migrate_schema import migrate_schema
from app.models import Child, Family, Membership, User

FAMILY_NAME = "示例家庭"
CHILD_NAME = "果果"
CHILD_BIRTH = date(2020, 1, 15)

ROLES = (
    ("admin@example.test", "管理员", "admin"),
    ("uploader@example.test", "可上传", "uploader"),
    ("viewer@example.test", "只读长辈", "viewer"),
)
OUTSIDER = ("outsider@example.test", "非成员")


def _user(db, email: str, name: str) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name=name)
        db.add(user)
        db.flush()
        return user
    if not user.name:
        user.name = name
    return user


def seed() -> None:
    migrate_schema()
    db = SessionLocal()
    try:
        family = db.scalar(select(Family).where(Family.name == FAMILY_NAME))
        if family is None:
            family = Family(name=FAMILY_NAME)
            db.add(family)
            db.flush()
        child = db.scalar(
            select(Child).where(Child.family_id == family.id, Child.name == CHILD_NAME)
        )
        if child is None:
            db.add(Child(family_id=family.id, name=CHILD_NAME, birth_date=CHILD_BIRTH))
        for email, name, role in ROLES:
            user = _user(db, email, name)
            membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
            if membership is None:
                db.add(Membership(user_id=user.id, family_id=family.id, role=role))
        outsider, outsider_name = OUTSIDER
        _user(db, outsider, outsider_name)
        db.commit()
        members = db.scalar(select(func.count()).select_from(Membership))
        print(f"seed ready family={family.id} memberships={members}")
        print("login with a temporary AUTH_TEST_CODE, then clear it")
    finally:
        db.close()


def main() -> None:
    seed()


if __name__ == "__main__":
    main()
