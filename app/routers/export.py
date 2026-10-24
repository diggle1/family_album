"""Free export of the signed-in member's own photos, plus original download."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.constants import EXPORT_ZIP_FILENAME
from app.db import get_db
from app.deps import MemberCtx, require_member
from app.models import Photo
from app.services import export as export_service
from app.services.export import ExportFailed, build_zip, count_my_photos, list_my_photos, to_item
from app.services.photos import iter_stream, open_photo
from app.routers.pages import _flags, gate_member, render

logger = logging.getLogger("family_album.export")

router = APIRouter(tags=["export"])

_MISSING = "没有这张照片"
_SHEET_STATES = {"confirm", "packing", "success", "failure"}


def _private(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("export temp delete failed")


async def _zip_chunks(path: Path) -> AsyncIterator[bytes]:
    try:
        with path.open("rb") as handle:
            while True:
                size = export_service.READ_CHUNK
                chunk = await anyio.to_thread.run_sync(lambda size=size: handle.read(size))
                if not chunk:
                    break
                yield chunk
    finally:
        _unlink(path)


class ZipDownload(StreamingResponse):
    """Closes the body iterator on disconnect so the temp file is removed."""

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            closer = getattr(self.body_iterator, "aclose", None)
            if closer is not None:
                try:
                    await closer()
                except Exception:
                    logger.warning("export stream close failed")


def stream_zip(path: Path) -> ZipDownload:
    size = path.stat().st_size
    return ZipDownload(
        _zip_chunks(path),
        media_type="application/zip",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{EXPORT_ZIP_FILENAME}"',
            "Content-Length": str(size),
        },
        background=BackgroundTask(_unlink, path),
    )


def _own(db: Session, ctx: MemberCtx):
    # family_id and uploader_id come from the session membership, never the query string.
    return list_my_photos(db, family_id=ctx.membership.family_id, uploader_id=ctx.user.id)


@router.get("/api/export/my-photos")
def export_count(
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
) -> JSONResponse:
    count = count_my_photos(db, family_id=ctx.membership.family_id, uploader_id=ctx.user.id)
    return _private(JSONResponse({"count": count}))


@router.get("/api/export/my-photos.zip")
def export_zip(
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
):
    photos = _own(db, ctx)
    if not photos:
        return _private(JSONResponse({"detail": "no_photos", "count": 0}, status_code=404))
    try:
        path = build_zip([to_item(photo) for photo in photos])
    except ExportFailed:
        return _private(JSONResponse({"detail": "export_failed"}, status_code=503))
    return stream_zip(path)


@router.get("/media/{photo_id}/download")
def download_original(
    photo_id: int,
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    photo = db.get(Photo, photo_id)
    # Missing ids and other families share one 404. Non-members never reach this:
    # require_member() already returned 403.
    if photo is None or photo.family_id != ctx.membership.family_id:
        raise HTTPException(status_code=404, detail=_MISSING)
    stream, media_type = open_photo(photo)
    suffix = Path(photo.storage_path).suffix.lower()
    return StreamingResponse(
        iter_stream(stream),
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="photo-{photo.id}{suffix}"',
        },
    )


def _page(request: Request, db: Session):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate, None
    return gate, count_my_photos(db, family_id=gate.membership.family_id, uploader_id=gate.user.id)


@router.get("/family/export")
def export_page(request: Request, db: Session = Depends(get_db)):
    gate, count = _page(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    assert count is not None
    response = render(
        request,
        "export.html",
        {
            **_flags(gate.user, gate.membership),
            "family": gate.family,
            "count": count,
            "state": "confirm",
            "show_nav": True,
            "tab": "settings",
        },
    )
    return _private(response)


@router.get("/fragments/export/sheet")
def export_sheet(request: Request, state: str = "confirm", db: Session = Depends(get_db)):
    gate, count = _page(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    assert count is not None
    chosen = state if state in _SHEET_STATES else "confirm"
    response = render(
        request,
        "fragments/export_sheet.html",
        {
            **_flags(gate.user, gate.membership),
            "family": gate.family,
            "count": count,
            "state": chosen,
            "show_nav": False,
        },
    )
    return _private(response)


