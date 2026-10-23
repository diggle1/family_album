"""Photo storage. kind=self with a child_id is rejected (400), not cleared."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Child, Membership, Photo, User
from app.services.children import list_children
from app.services.clock import isoformat, shanghai_today
from app.services.validators import parse_iso_date, parse_optional_int

_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def photo_payload(photo: Photo) -> dict:
    return {
        "id": photo.id,
        "family_id": photo.family_id,
        "kind": photo.kind,
        "child_id": photo.child_id,
        "taken_on": photo.taken_on.isoformat(),
        "media_url": f"/media/{photo.id}",
        "created_at": isoformat(photo.created_at),
    }


def _resolve_child(db: Session, *, family_id: int, child_id: int | None) -> Child:
    children = list_children(db, family_id)
    if child_id is None:
        if len(children) == 1:
            return children[0]
        raise HTTPException(status_code=400, detail="孩子照片必须指定孩子")
    for child in children:
        if child.id == child_id:
            return child
    raise HTTPException(status_code=400, detail="孩子不在当前家庭")


def create_photo(
    db: Session,
    *,
    family_id: int,
    uploader_id: int,
    kind_raw: str,
    child_id_raw: str | int | None,
    taken_on_raw: str | None,
    data: bytes,
) -> Photo:
    kind = (kind_raw or "").strip().lower()
    if kind not in {"child", "self"}:
        raise HTTPException(status_code=400, detail="照片类型无效")
    child_id = parse_optional_int(child_id_raw)
    child: Child | None = None
    if kind == "self":
        # S6: a non-empty child_id is an illegal combination, not a silent clear.
        if child_id is not None:
            raise HTTPException(status_code=400, detail="本人照片不能挂到孩子")
        taken = parse_iso_date(taken_on_raw, invalid="拍摄日期无效")
        if taken is None:
            taken = shanghai_today()
    else:
        child = _resolve_child(db, family_id=family_id, child_id=child_id)
        taken = parse_iso_date(
            taken_on_raw,
            invalid="拍摄日期无效",
            missing="孩子照片必须填写拍摄日期",
        )
        assert taken is not None

    settings = get_settings()
    if not data:
        raise HTTPException(status_code=400, detail="请上传照片")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=400, detail="单张照片不能超过 10MB")
    media_type = sniff_image(data)
    if media_type is None:
        raise HTTPException(status_code=400, detail="只支持 jpeg、png、webp")

    settings.photo_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{_EXT[media_type]}"
    path: Path = settings.photo_dir / filename
    path.write_bytes(data)
    photo = Photo(
        family_id=family_id,
        uploader_id=uploader_id,
        kind=kind,
        child_id=child.id if child is not None else None,
        taken_on=taken,
        storage_path=filename,
    )
    db.add(photo)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="照片无法保存") from None
    db.refresh(photo)
    return photo


def self_album(db: Session, *, family_id: int, user_id: int) -> dict | None:
    membership = db.scalar(
        select(Membership).where(Membership.user_id == user_id, Membership.family_id == family_id)
    )
    if membership is None:
        return None
    owner = db.get(User, user_id)
    if owner is None:
        return None
    photos = list(
        db.scalars(
            select(Photo)
            .where(
                Photo.family_id == family_id,
                Photo.uploader_id == user_id,
                Photo.kind == "self",
            )
            .order_by(Photo.taken_on.desc(), Photo.id.desc())
        ).all()
    )
    return {
        "user_id": owner.id,
        "name": owner.name,
        "relation_label": membership.relation_label,
        "role": membership.role,
        "photos": [
            {
                "id": photo.id,
                "taken_on": photo.taken_on.isoformat(),
                "media_url": f"/media/{photo.id}",
                "created_at": isoformat(photo.created_at),
            }
            for photo in photos
        ],
    }


def photo_file(photo: Photo) -> tuple[Path, str]:
    settings = get_settings()
    name = photo.storage_path
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status_code=404, detail="没有这张照片")
    path = (settings.photo_dir / name).resolve()
    root = settings.photo_dir.resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="没有这张照片")
    media_type = _MEDIA.get(path.suffix.lower())
    if media_type is None:
        raise HTTPException(status_code=404, detail="没有这张照片")
    return path, media_type
