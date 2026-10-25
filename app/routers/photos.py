from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import FORBIDDEN, PHOTO_MULTI_CHILD, ROLE_RANK
from app.db import get_db
from app.deps import MemberCtx, require_member
from app.models import Photo
from app.services.assoc import parse_child_id_list
from app.services.photos import PhotoError, create_photo, iter_stream, open_photo, photo_payload, self_album, update_photo_assoc
from app.setup_gate import note_child_media, note_child_upload

router = APIRouter(tags=["photos"])


def photo_writer(ctx: MemberCtx = Depends(require_member())) -> MemberCtx:
    if ROLE_RANK[ctx.membership.role] < ROLE_RANK["uploader"]:
        raise PhotoError(403, "photo_forbidden_role", FORBIDDEN)
    return ctx


def _json(payload: dict, status: int) -> JSONResponse:
    response = JSONResponse(payload, status_code=status)
    response.headers["Cache-Control"] = "private, no-store"
    return response


async def _read_limited(file: UploadFile, request: Request) -> bytes:
    settings = get_settings()
    limit = settings.max_upload_bytes
    header = request.headers.get("content-length")
    if header and header.isdigit() and int(header) > limit + 256 * 1024:
        raise HTTPException(status_code=400, detail="单张照片不能超过 20MB")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=400, detail="单张照片不能超过 20MB")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/api/photos", status_code=201)
async def post_photo(
    request: Request,
    kind: str = Form(...),
    child_id: str | None = Form(None),
    child_ids: list[str] | None = Form(None),
    taken_on: str | None = Form(None),
    file: UploadFile | None = File(None),
    ctx: MemberCtx = Depends(photo_writer),
    db: Session = Depends(get_db),
) -> JSONResponse:
    if file is None:
        raise HTTPException(status_code=400, detail="请上传照片")
    data = await _read_limited(file, request)
    photo = create_photo(
        db,
        family_id=ctx.membership.family_id,
        uploader_id=ctx.user.id,
        kind_raw=kind,
        child_id_raw=child_id,
        child_ids_raw=child_ids,
        taken_on_raw=taken_on,
        data=data,
    )
    response = _json(photo_payload(db, photo), 201)
    note_child_upload(request, response, role=ctx.membership.role, kind=photo.kind)
    return response


@router.patch("/api/photos/{photo_id}")
async def patch_photo(
    photo_id: int,
    request: Request,
    ctx: MemberCtx = Depends(photo_writer),
    db: Session = Depends(get_db),
):
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype or "application/octet-stream" in ctype:
        raise PhotoError(400, "photo_file_immutable", "不能换片")
    try:
        payload = await request.json()
    except Exception as exc:
        raise PhotoError(422, "child_required", PHOTO_MULTI_CHILD["err_no_child"]) from exc
    if not isinstance(payload, dict):
        raise PhotoError(422, "child_required", PHOTO_MULTI_CHILD["err_no_child"])
    if payload.get("file") or payload.get("storage_path") or payload.get("poster"):
        raise PhotoError(400, "photo_file_immutable", "不能换片")
    if "kind" in payload:
        raise PhotoError(400, "photo_kind_immutable", "不能改照片类型")
    if "child_ids" in payload:
        child_ids = parse_child_id_list(payload.get("child_ids"))
    elif "child_id" in payload and payload.get("child_id") is not None:
        one = payload.get("child_id")
        child_ids = [one] if isinstance(one, int) and not isinstance(one, bool) else None
        if child_ids is None:
            raise PhotoError(400, "child_required", PHOTO_MULTI_CHILD["err_no_child"])
    else:
        child_ids = None
    return _json(update_photo_assoc(db, ctx, photo_id, child_ids=child_ids), 200)


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
    request: Request,
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    photo = db.get(Photo, photo_id)
    if photo is None or photo.family_id != ctx.membership.family_id:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    stream, media_type = open_photo(photo)
    suffix = Path(photo.storage_path).suffix.lower()
    response = StreamingResponse(
        iter_stream(stream),
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f'inline; filename="photo-{photo.id}{suffix}"',
        },
    )
    note_child_media(request, response, role=ctx.membership.role, kind=photo.kind)
    return response
