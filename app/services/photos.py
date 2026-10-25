"""Photo records. Bytes go through MediaStore, never Path.write_bytes.

kind=self with a child_id is rejected (400), not cleared.
Writes use current_store() and stamp Photo.storage_backend.
Reads use store_for(photo.storage_backend).

Child photos accept child_ids (or the legacy single child_id). The first
requested id is also written to photos.child_id. PATCH changes only that set.
"""

from __future__ import annotations

import json
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import FORBIDDEN, PHOTO_MULTI_CHILD, ROLE_RANK
from app.deps import MemberCtx
from app.models import Child, Membership, Photo, User
from app.services.assoc import (
    ChildSetError,
    children_payload,
    linked_children,
    parse_form_child_ids,
    photo_child_ids,
    replace_photo_children,
    resolve_children,
)
from app.services.children import child_in_family, list_children
from app.services.clock import isoformat, shanghai_today
from app.services.validators import parse_iso_date, parse_optional_int
from app.storage.factory import current_store, store_for

# Same sentence journals and videos already use for a day before birth.
_BIRTH = "那天{name}还没出生"
_LEGACY_REQUIRED = "孩子照片必须指定孩子"
_LEGACY_MISSING = "孩子不在当前家庭"
_SELF_NO_CHILD = "本人照片不能挂到孩子"


class PhotoError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        self.status = status
        self.code = code
        self.detail = detail
        super().__init__(detail)


def not_found() -> PhotoError:
    return PhotoError(404, "photo_not_found", "没有这张照片")

_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def photo_payload(db: Session, photo: Photo) -> dict:
    payload = {
        "id": photo.id,
        "family_id": photo.family_id,
        "kind": photo.kind,
        "child_id": photo.child_id,
        "taken_on": photo.taken_on.isoformat(),
        "media_url": f"/media/{photo.id}",
        "created_at": isoformat(photo.created_at),
    }
    if photo.kind != "child":
        return payload
    raw_ids = photo_child_ids(db, photo)
    live = linked_children(db, family_id=photo.family_id, child_ids=raw_ids)
    audience = children_payload(live, photo.taken_on)
    payload["children"] = audience
    payload["child_ids"] = raw_ids
    payload["child"] = audience[0] if audience else None
    return payload


def _self_has_child_field(child_id_raw: str | int | None, child_ids_raw: list[str] | None) -> bool:
    """True when a self photo was given a non-empty child field.

    A present child_ids list wins. An empty list ignores the legacy field.
    """
    if child_ids_raw is not None:
        return any(str(item).strip() for item in child_ids_raw)
    return parse_optional_int(child_id_raw) is not None


def _children_for_child_photo(
    db: Session,
    *,
    family_id: int,
    child_id_raw: str | int | None,
    child_ids_raw: list[str] | None,
    taken,
) -> list[Child]:
    if child_ids_raw is not None:
        try:
            resolved = resolve_children(
                db,
                family_id=family_id,
                child_ids=parse_form_child_ids(child_ids_raw),
                legacy_child_id=None,
                used_list=True,
                required_detail=PHOTO_MULTI_CHILD["err_no_child"],
                missing_detail=PHOTO_MULTI_CHILD["e_kid_gone"],
                birth_detail=_BIRTH,
                on_date=taken,
            )
        except ChildSetError as exc:
            raise PhotoError(400, exc.code, exc.detail) from exc
        return resolved.children
    child_id = parse_optional_int(child_id_raw)
    if child_id is None:
        family = list_children(db, family_id)
        if len(family) == 1:
            child_id = family[0].id
        else:
            raise HTTPException(status_code=400, detail=_LEGACY_REQUIRED)
    try:
        resolved = resolve_children(
            db,
            family_id=family_id,
            child_ids=None,
            legacy_child_id=child_id,
            used_list=False,
            required_detail=_LEGACY_REQUIRED,
            missing_detail=_LEGACY_MISSING,
            birth_detail=_BIRTH,
            on_date=taken,
        )
    except ChildSetError as exc:
        if exc.code == "date_before_birth":
            raise PhotoError(400, exc.code, exc.detail) from exc
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return resolved.children


def create_photo(
    db: Session,
    *,
    family_id: int,
    uploader_id: int,
    kind_raw: str,
    child_id_raw: str | int | None,
    taken_on_raw: str | None,
    data: bytes,
    child_ids_raw: list[str] | None = None,
) -> Photo:
    kind = (kind_raw or "").strip().lower()
    if kind not in {"child", "self"}:
        raise HTTPException(status_code=400, detail="照片类型无效")
    children: list[Child] = []
    if kind == "self":
        # A non-empty child field is an illegal combination, not a silent clear.
        if _self_has_child_field(child_id_raw, child_ids_raw):
            raise PhotoError(400, "photo_self_no_children", _SELF_NO_CHILD)
        taken = parse_iso_date(taken_on_raw, invalid="拍摄日期无效")
        if taken is None:
            taken = shanghai_today()
    else:
        taken = parse_iso_date(
            taken_on_raw,
            invalid="拍摄日期无效",
            missing="孩子照片必须填写拍摄日期",
        )
        assert taken is not None
        children = _children_for_child_photo(
            db,
            family_id=family_id,
            child_id_raw=child_id_raw,
            child_ids_raw=child_ids_raw,
            taken=taken,
        )

    settings = get_settings()
    if not data:
        raise HTTPException(status_code=400, detail="请上传照片")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=400, detail="单张照片不能超过 10MB")
    media_type = sniff_image(data)
    if media_type is None:
        raise HTTPException(status_code=400, detail="只支持 jpeg、png、webp")

    filename = f"{uuid.uuid4().hex}{_EXT[media_type]}"
    backend = settings.storage_backend if settings.storage_backend in {"local", "s3"} else "local"
    store = current_store()
    store.put(filename, data, media_type)
    photo = Photo(
        family_id=family_id,
        uploader_id=uploader_id,
        kind=kind,
        child_id=children[0].id if children else None,
        taken_on=taken,
        storage_path=filename,
        storage_backend=backend,
    )
    db.add(photo)
    try:
        db.flush()
        if children:
            replace_photo_children(db, photo, children)
        db.commit()
    except IntegrityError:
        db.rollback()
        store.delete(filename)
        raise HTTPException(status_code=400, detail="照片无法保存") from None
    except Exception:
        db.rollback()
        store.delete(filename)
        raise
    db.refresh(photo)
    return photo


def can_edit_photo(viewer: Membership, photo: Photo) -> bool:
    if viewer.role == "admin":
        return True
    return ROLE_RANK[viewer.role] >= ROLE_RANK["uploader"] and photo.uploader_id == viewer.user_id


def update_photo_assoc(
    db: Session,
    ctx: MemberCtx,
    photo_id: int,
    *,
    child_ids: list[int] | None,
) -> dict:
    """Replace the child set. The file, kind, and taken_on stay as they are."""
    photo = db.get(Photo, photo_id)
    if photo is None or photo.family_id != ctx.family.id:
        raise not_found()
    if not can_edit_photo(ctx.membership, photo):
        raise PhotoError(403, "photo_not_author", FORBIDDEN)
    if photo.kind != "child":
        raise PhotoError(400, "photo_self_no_children", _SELF_NO_CHILD)
    if not child_ids:
        raise PhotoError(400, "child_required", PHOTO_MULTI_CHILD["err_no_child"])
    try:
        resolved = resolve_children(
            db,
            family_id=ctx.family.id,
            child_ids=child_ids,
            legacy_child_id=None,
            used_list=True,
            required_detail=PHOTO_MULTI_CHILD["err_no_child"],
            missing_detail=PHOTO_MULTI_CHILD["e_kid_gone"],
            birth_detail=_BIRTH,
            on_date=photo.taken_on,
        )
    except ChildSetError as exc:
        raise PhotoError(400, exc.code, exc.detail) from exc
    replace_photo_children(db, photo, resolved.children)
    db.commit()
    db.refresh(photo)
    return photo_payload(db, photo)


def photo_export_sidecars(db: Session, *, family_id: int, uploader_id: int) -> list[tuple[str, bytes]]:
    """Sidecar JSON only when a photo is linked to more than one child."""
    rows = list(
        db.scalars(
            select(Photo).where(
                Photo.family_id == family_id,
                Photo.uploader_id == uploader_id,
                Photo.kind == "child",
            )
        ).all()
    )
    extras: list[tuple[str, bytes]] = []
    for photo in rows:
        linked = photo_child_ids(db, photo)
        if len(linked) <= 1:
            continue
        names: list[str] = []
        for child_id in linked:
            child = child_in_family(db, family_id=family_id, child_id=child_id)
            names.append(child.name if child is not None else "（孩子已不在列表）")
        payload = {"child_ids": linked, "names": names}
        blob = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        extras.append((f"{photo.taken_on.isoformat()}_{photo.id}.children.json", blob))
    return extras


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


def open_photo(photo: Photo) -> tuple[object, str]:
    backend = photo.storage_backend or "local"
    try:
        return store_for(backend).open(photo.storage_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="没有这张照片") from None


def iter_stream(stream: object):
    try:
        while True:
            chunk = stream.read(64 * 1024)  # type: ignore[attr-defined]
            if not chunk:
                break
            yield chunk
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()
