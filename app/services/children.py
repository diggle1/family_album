"""Child profiles and the per-day timeline. Self photos never join this query."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Child, Photo, User
from app.services.clock import age_label, date_label, shanghai_today, weekday_zh
from app.services.validators import clean_short_text, parse_iso_date


def create_child(db: Session, *, family_id: int, name_raw: str, birth_raw: str) -> Child:
    name = clean_short_text(name_raw, message="小名需要 1–40 个字")
    birth = parse_iso_date(birth_raw, invalid="生日无效", missing="生日无效")
    assert birth is not None
    child = Child(family_id=family_id, name=name, birth_date=birth)
    db.add(child)
    db.commit()
    db.refresh(child)
    return child


def list_children(db: Session, family_id: int) -> list[Child]:
    return list(
        db.scalars(select(Child).where(Child.family_id == family_id).order_by(Child.id.asc())).all()
    )


def child_in_family(db: Session, *, family_id: int, child_id: int) -> Child | None:
    child = db.get(Child, child_id)
    if child is None or child.family_id != family_id:
        return None
    return child


def child_payload(child: Child) -> dict:
    return {
        "id": child.id,
        "family_id": child.family_id,
        "name": child.name,
        "birth_date": child.birth_date.isoformat(),
        "age_label": age_label(child.birth_date, shanghai_today()),
    }


def build_timeline(db: Session, child: Child) -> dict:
    photos = list(
        db.scalars(
            select(Photo)
            .where(Photo.child_id == child.id, Photo.kind == "child", Photo.family_id == child.family_id)
            .order_by(Photo.taken_on.desc(), Photo.id.asc())
        ).all()
    )
    uploader_ids = {photo.uploader_id for photo in photos}
    users: dict[int, User] = {}
    if uploader_ids:
        users = {
            user.id: user
            for user in db.scalars(select(User).where(User.id.in_(uploader_ids))).all()
        }
    days: list[dict] = []
    current: dict | None = None
    for photo in photos:
        key = photo.taken_on.isoformat()
        if current is None or current["taken_on"] != key:
            current = {
                "taken_on": key,
                "date_label": date_label(photo.taken_on),
                "weekday": weekday_zh(photo.taken_on),
                "age_label": age_label(child.birth_date, photo.taken_on),
                "photos": [],
            }
            days.append(current)
        owner = users.get(photo.uploader_id)
        current["photos"].append(
            {
                "id": photo.id,
                "uploader_id": photo.uploader_id,
                "uploader_name": owner.name if owner and owner.name else "未命名",
                "media_url": f"/media/{photo.id}",
            }
        )
    for day in days:
        names: list[str] = []
        for photo in day["photos"]:
            if photo["uploader_name"] not in names:
                names.append(photo["uploader_name"])
        day["byline"] = "、".join(names) + " 上传"
    return {
        "child": {
            "id": child.id,
            "name": child.name,
            "birth_date": child.birth_date.isoformat(),
            "age_label": age_label(child.birth_date, shanghai_today()),
            "photo_count": len(photos),
        },
        "days": days,
    }


def require_child(db: Session, *, family_id: int, child_id: int) -> Child:
    child = child_in_family(db, family_id=family_id, child_id=child_id)
    if child is None:
        from app.constants import FORBIDDEN

        raise HTTPException(status_code=403, detail=FORBIDDEN)
    return child
