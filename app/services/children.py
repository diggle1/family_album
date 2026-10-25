"""Child profiles and the per-day timeline. Self photos never join this query."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import Child, Photo, PhotoChild, User
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


def load_uploaders(db: Session, photos: list[Photo]) -> dict[int, User]:
    uploader_ids = {photo.uploader_id for photo in photos}
    if not uploader_ids:
        return {}
    return {user.id: user for user in db.scalars(select(User).where(User.id.in_(uploader_ids))).all()}


def photo_feed_item(db: Session, photo: Photo, users: dict[int, User]) -> dict:
    """One timeline card. The stack lists every linked child, not a filtered subset."""
    from app.services.assoc import children_payload, linked_children, photo_child_ids

    owner = users.get(photo.uploader_id)
    raw_ids = photo_child_ids(db, photo)
    live = linked_children(db, family_id=photo.family_id, child_ids=raw_ids)
    return {
        "id": photo.id,
        "uploader_id": photo.uploader_id,
        "uploader_name": owner.name if owner and owner.name else "未命名",
        "media_url": f"/media/{photo.id}",
        "children": children_payload(live, photo.taken_on),
        "child_ids": [item.id for item in live],
    }


def child_photos(db: Session, child: Child) -> list[Photo]:
    """Child photos linked to this child. Empty association rows fall back to child_id."""
    linked = select(PhotoChild.photo_id).where(PhotoChild.child_id == child.id)
    unlinked = ~Photo.id.in_(select(PhotoChild.photo_id))
    return list(
        db.scalars(
            select(Photo)
            .where(
                Photo.family_id == child.family_id,
                Photo.kind == "child",
                or_(Photo.id.in_(linked), and_(unlinked, Photo.child_id == child.id)),
            )
            .order_by(Photo.taken_on.desc(), Photo.id.asc())
        ).all()
    )


def family_child_photos(db: Session, family_id: int) -> list[Photo]:
    """One row per child photo. Callers must not fan this out by linked child."""
    return list(
        db.scalars(
            select(Photo)
            .where(Photo.family_id == family_id, Photo.kind == "child")
            .order_by(Photo.taken_on.desc(), Photo.id.asc())
        ).all()
    )


def build_timeline(db: Session, child: Child) -> dict:
    photos = child_photos(db, child)
    users = load_uploaders(db, photos)
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
        current["photos"].append(photo_feed_item(db, photo, users))
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
