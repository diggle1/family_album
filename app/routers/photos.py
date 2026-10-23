from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import FORBIDDEN
from app.db import get_db
from app.deps import MemberCtx, require_member
from app.models import Photo
from app.services.photos import create_photo, photo_file, photo_payload, self_album

router = APIRouter(tags=["photos"])


async def _read_limited(file: UploadFile, request: Request) -> bytes:
    settings = get_settings()
    limit = settings.max_upload_bytes
    header = request.headers.get("content-length")
    if header and header.isdigit() and int(header) > limit + 256 * 1024:
        raise HTTPException(status_code=400, detail="单张照片不能超过 10MB")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=400, detail="单张照片不能超过 10MB")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/api/photos", status_code=201)
async def post_photo(
    request: Request,
    kind: str = Form(...),
    child_id: str | None = Form(None),
    taken_on: str | None = Form(None),
    file: UploadFile | None = File(None),
    ctx: MemberCtx = Depends(require_member("uploader")),
    db: Session = Depends(get_db),
) -> dict:
    if file is None:
        raise HTTPException(status_code=400, detail="请上传照片")
    data = await _read_limited(file, request)
    photo = create_photo(
        db,
        family_id=ctx.family.id,
        uploader_id=ctx.user.id,
        kind_raw=kind,
        child_id_raw=child_id,
        taken_on_raw=taken_on,
        data=data,
    )
    return photo_payload(photo)


@router.get("/api/photos/self/{user_id}")
def get_self_album(
    user_id: int,
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
) -> dict:
    payload = self_album(db, family_id=ctx.family.id, user_id=user_id)
    if payload is None:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    return payload


@router.get("/media/{photo_id}")
def get_media(
    photo_id: int,
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
) -> FileResponse:
    photo = db.get(Photo, photo_id)
    if photo is None or photo.family_id != ctx.family.id:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    path, media_type = photo_file(photo)
    suffix = path.suffix.lower() or ""
    return FileResponse(
        path,
        media_type=media_type,
        filename=f"photo-{photo.id}{suffix}",
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )
