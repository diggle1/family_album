"""Video pages and JSON. Family id and uploader always come from the session."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.constants import FORBIDDEN, MULTI_CHILD, ROLE_RANK, VIDEO
from app.db import get_db
from app.deps import MemberCtx, require_member
from app.routers.pages import _flags, gate_member, render
from app.services.children import list_children
from app.services.clock import shanghai_today
from app.services import videos as video_service
from app.services.assoc import linked_children, parse_child_id_list, video_child_ids
from app.services.videos import (
    VideoError,
    blob_exists,
    can_delete,
    can_edit_assoc,
    create_video,
    delete_video,
    detail_payload,
    display_who,
    iter_bounded,
    new_partial_path,
    not_found,
    open_media,
    open_named,
    peek_replay,
    unlink_path,
    update_video_assoc,
    visible_video,
)

logger = logging.getLogger("family_album.video")

router = APIRouter(tags=["videos"])

_KEY = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_WEEK = "一二三四五六日"


def video_writer(ctx: MemberCtx = Depends(require_member())) -> MemberCtx:
    if ROLE_RANK[ctx.membership.role] < ROLE_RANK["uploader"]:
        raise VideoError(403, "video_forbidden_role", FORBIDDEN)
    return ctx


def _nostore(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload: dict, status: int):
    return _nostore(JSONResponse(payload, status_code=status))


def _read_key(request: Request) -> str:
    raw = request.headers.get("idempotency-key")
    if raw is None or raw.strip() == "" or _KEY.fullmatch(raw.strip()) is None:
        raise VideoError(400, "idempotency_key_invalid", VIDEO["err_retry"])
    return raw.strip()


def _parse_range(header: str | None, size: int):
    if not header or not header.startswith("bytes=") or "," in header:
        return None
    spec = header[6:].strip()
    if "-" not in spec:
        return None
    start_s, end_s = spec.split("-", 1)
    try:
        if start_s == "":
            length = int(end_s)
            if length <= 0:
                return None
            if size <= 0:
                return "unsat"
            start = max(size - length, 0)
            end = size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
    except ValueError:
        return None
    if size <= 0 or start < 0 or start >= size or end < start:
        return "unsat"
    return start, min(end, size - 1)


async def _stream_to(upload: UploadFile, dest: Path, limit: int) -> int:
    total = 0
    with dest.open("wb") as handle:
        while True:
            chunk = await upload.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise VideoError(400, "video_size", VIDEO["err_limit_size"])
            handle.write(chunk)
    return total


async def _stream_poster(upload: UploadFile, dest: Path, limit: int) -> int:
    """Stop once the poster cap is passed. The caller decides the error later."""
    total = 0
    with dest.open("wb") as handle:
        while True:
            chunk = await upload.read(64 * 1024)
            if not chunk:
                break
            if total >= limit:
                total = limit + 1
                break
            room = limit - total
            if len(chunk) > room:
                total = limit + 1
                break
            handle.write(chunk)
            total += len(chunk)
    return total


def _gate(request: Request, db: Session):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return _nostore(gate)
    return gate


def _missing(request: Request, gate: MemberCtx):
    response = render(
        request,
        "video_missing.html",
        {
            **_flags(gate.user, gate.membership),
            "family": gate.family,
            "show_nav": False,
        },
        status_code=404,
    )
    return _nostore(response)


@router.post("/api/videos")
async def post_video(
    request: Request,
    child_id: str | None = Form(None),
    child_ids: list[str] | None = Form(None),
    on_date: str | None = Form(None),
    duration_ms: str | None = Form(None),
    file: UploadFile | None = File(None),
    poster: UploadFile | None = File(None),
    ctx: MemberCtx = Depends(video_writer),
    db: Session = Depends(get_db),
):
    # Declared form names are the only ones read. family_id / uploader_id are ignored.
    key = _read_key(request)
    replay = peek_replay(db, ctx, key)
    if replay is not None:
        body, status = replay
        return _json(body, status)
    video_path = None
    poster_path = None
    try:
        if file is None:
            raise VideoError(400, "video_empty", VIDEO["err_format"])
        video_path = new_partial_path()
        total = await _stream_to(file, video_path, video_service.VIDEO_MAX_BYTES)
        if total <= 0:
            raise VideoError(400, "video_empty", VIDEO["err_format"])
        poster_too_big = False
        if poster is not None:
            poster_path = new_partial_path()
            poster_total = await _stream_poster(poster, poster_path, video_service.POSTER_MAX_BYTES)
            if poster_total <= 0:
                unlink_path(poster_path)
                poster_path = None
            elif poster_total > video_service.POSTER_MAX_BYTES:
                poster_too_big = True
        body, status = create_video(
            db,
            ctx,
            key,
            child_raw=child_id,
            on_date_raw=on_date,
            duration_raw=duration_ms,
            video_path=video_path,
            filename=file.filename or "",
            poster_path=None if poster_too_big else poster_path,
            poster_too_big=poster_too_big,
            child_ids_raw=child_ids,
        )
        return _json(body, status)
    finally:
        unlink_path(video_path)
        unlink_path(poster_path)


@router.get("/api/videos/{video_id}")
def get_video(
    video_id: int,
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
):
    video = visible_video(db, family_id=ctx.family.id, video_id=video_id)
    if video is None:
        raise not_found()
    return _json(detail_payload(db, video, ctx.membership), 200)


@router.patch("/api/videos/{video_id}")
async def patch_video(
    video_id: int,
    request: Request,
    ctx: MemberCtx = Depends(video_writer),
    db: Session = Depends(get_db),
):
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype or "application/octet-stream" in ctype:
        raise VideoError(400, "video_file_immutable", "不能换片")
    try:
        payload = await request.json()
    except Exception as exc:
        raise VideoError(400, "child_required", MULTI_CHILD["err_no_child"]) from exc
    if not isinstance(payload, dict):
        raise VideoError(400, "child_required", MULTI_CHILD["err_no_child"])
    if payload.get("file") or payload.get("poster") or payload.get("storage_path"):
        raise VideoError(400, "video_file_immutable", "不能换片")
    on_date = payload.get("on_date") if "on_date" in payload else None
    if "child_ids" in payload:
        child_ids = parse_child_id_list(payload.get("child_ids"))
    elif "child_id" in payload and payload.get("child_id") is not None:
        one = payload.get("child_id")
        child_ids = [one] if isinstance(one, int) and not isinstance(one, bool) else None
        if child_ids is None:
            raise VideoError(400, "child_required", MULTI_CHILD["err_no_child"])
    else:
        child_ids = None
    return _json(
        update_video_assoc(db, ctx, video_id, child_ids=child_ids, on_date_raw=on_date),
        200,
    )


@router.delete("/api/videos/{video_id}")
def remove_video(
    video_id: int,
    ctx: MemberCtx = Depends(video_writer),
    db: Session = Depends(get_db),
):
    delete_video(db, ctx, video_id)
    return _nostore(Response(status_code=204))


def _upload_page(
    request: Request,
    db: Session,
    gate: MemberCtx,
    *,
    mode: str,
    video=None,
    payload: dict | None = None,
):
    children = list_children(db, gate.family.id)
    preselect = False
    selected_ids: list[int] = []
    if payload is not None:
        selected_ids = list(payload.get("child_ids") or [])
    else:
        raw = request.query_params.get("child")
        if raw and raw.isdigit():
            chosen = next((child for child in children if child.id == int(raw)), None)
            if chosen is not None:
                selected_ids = [chosen.id]
                preselect = len(children) > 1
        if not selected_ids and len(children) == 1:
            selected_ids = [children[0].id]
    selected = next((child for child in children if selected_ids and child.id == selected_ids[0]), None)
    pre_name = ""
    if len(children) > 1 and selected is not None and (preselect or mode == "edit"):
        pre_name = selected.name
    children_json = [
        {"id": child.id, "name": child.name, "birth": child.birth_date.isoformat()} for child in children
    ]
    if selected_ids:
        back = f"/?child={selected_ids[0]}"
    elif len(children) > 1:
        back = "/?view=family"
    else:
        back = "/"
    response = render(
        request,
        "video_upload.html",
        {
            **_flags(gate.user, gate.membership),
            "family": gate.family,
            "children": children,
            "selected": selected,
            "selected_ids": selected_ids,
            "pre_name": pre_name,
            "mode": mode,
            "video": video,
            "payload": payload,
            "today": shanghai_today().isoformat(),
            "show_nav": False,
            "back_href": back,
            "children_json": json.dumps(children_json, ensure_ascii=True),
            "selected_json": json.dumps(selected_ids),
            "max_ms": video_service.VIDEO_MAX_DURATION_MS,
            "max_bytes": video_service.VIDEO_MAX_BYTES,
            "poster_max": video_service.POSTER_MAX_BYTES,
            "weekdays": _WEEK,
        },
    )
    return _nostore(response)


@router.get("/videos/new")
def page_new(request: Request, db: Session = Depends(get_db)):
    gate = _gate(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    if ROLE_RANK[gate.membership.role] < ROLE_RANK["uploader"]:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    return _upload_page(request, db, gate, mode="new")


@router.get("/videos/{video_id}")
def page_detail(video_id: int, request: Request, db: Session = Depends(get_db)):
    gate = _gate(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    video = visible_video(db, family_id=gate.family.id, video_id=video_id)
    if video is None or not blob_exists(video):
        return _missing(request, gate)
    audience = linked_children(db, family_id=gate.family.id, child_ids=video_child_ids(db, video))
    child = audience[0]
    payload = detail_payload(db, video, gate.membership)
    response = render(
        request,
        "video_detail.html",
        {
            **_flags(gate.user, gate.membership),
            "family": gate.family,
            "video": video,
            "child": child,
            "payload": payload,
            "who": display_who(payload["author"]),
            "can_delete": can_delete(gate.membership, video),
            "can_edit": can_edit_assoc(gate.membership, video),
            "audience": audience,
            "show_nav": False,
        },
    )
    return _nostore(response)


@router.get("/videos/{video_id}/edit")
def page_edit(video_id: int, request: Request, db: Session = Depends(get_db)):
    gate = _gate(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    if ROLE_RANK[gate.membership.role] < ROLE_RANK["uploader"]:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    video = visible_video(db, family_id=gate.family.id, video_id=video_id)
    if video is None or not blob_exists(video):
        return _missing(request, gate)
    if not can_edit_assoc(gate.membership, video):
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    payload = detail_payload(db, video, gate.membership)
    return _upload_page(request, db, gate, mode="edit", video=video, payload=payload)


def _media_headers(video, suffix: str, *, length: int, status_range: str | None) -> dict:
    headers = {
        "Cache-Control": "private, max-age=3600",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Disposition": f'inline; filename="video-{video.id}{suffix}"',
    }
    if status_range:
        headers["Content-Range"] = status_range
    return headers


@router.get("/media/videos/{video_id}")
def get_media(
    video_id: int,
    request: Request,
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
):
    video = visible_video(db, family_id=ctx.family.id, video_id=video_id)
    if video is None:
        raise not_found()
    suffix = Path(video.storage_path).suffix.lower()
    size = video.byte_size
    parsed = _parse_range(request.headers.get("range"), size)
    if parsed == "unsat":
        response = Response(status_code=416)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Content-Range"] = f"bytes */{size}"
        return response
    start = end = None
    length = size
    status = 200
    content_range = None
    if isinstance(parsed, tuple):
        start, end = parsed
        length = end - start + 1
        status = 206
        content_range = f"bytes {start}-{end}/{size}"
    stream, media_type = open_media(video, start, end)
    return StreamingResponse(
        iter_bounded(stream, length if start is not None else None),
        status_code=status,
        media_type=media_type,
        headers=_media_headers(video, suffix, length=length, status_range=content_range),
    )


@router.get("/media/videos/{video_id}/poster")
def get_poster(
    video_id: int,
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
):
    video = visible_video(db, family_id=ctx.family.id, video_id=video_id)
    if video is None:
        raise not_found()
    if not video.poster_path:
        raise VideoError(404, "poster_missing", VIDEO["not_found"])
    try:
        stream, media_type = open_named(video.storage_backend or "local", video.poster_path)
    except VideoError:
        raise VideoError(404, "poster_missing", VIDEO["not_found"]) from None
    suffix = Path(video.poster_path).suffix.lower()
    return StreamingResponse(
        iter_bounded(stream, None),
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="poster-{video.id}{suffix}"',
        },
    )
