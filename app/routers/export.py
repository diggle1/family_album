"""Free export of the signed-in member's own photos, plus original download."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app import constants
from app.db import get_db
from app.deps import MemberCtx, require_member
from app.models import Photo
from app.services import export as export_service
from app.services.children import list_children
from app.services.clock import shanghai_today
from app.services.export import (
    CANCELLED,
    ExportAllMissing,
    ExportFailed,
    PackSlot,
    build_zip,
    count_my_photos,
    count_my_videos,
    export_filename_day,
    list_my_photos,
    list_my_videos,
    missing_header_value,
)
from app.services.journals import count_my_journals, export_markdown
from app.services.photos import photo_export_sidecars
from app.services.videos import video_export_sidecars
from app.services.photos import iter_stream, open_photo
from app.routers.pages import _flags, gate_member, render

logger = logging.getLogger("family_album.export")

router = APIRouter(tags=["export"])

_MISSING = "没有这张照片"
_SHEET_STATES = {
    "confirm",
    "packing",
    "success",
    "failure",
    "downloading",
    "dlfail",
    "allmissing",
    "big",
}
_DL_RE = re.compile(r"^[0-9a-f]{32}$")
_COPY_ATTRS = (
    ("busy", "busy"),
    ("toast_cancelled", "toast-cancelled"),
    ("toast_bg_done", "toast-bg-done"),
    ("stopping", "stopping"),
    ("where_mobile", "where-mobile"),
    ("where_desktop", "where-desktop"),
    ("ok_title", "ok-title"),
    ("ok_title_partial", "ok-title-partial"),
    ("ok_sub", "ok-sub"),
    ("ios_cancel", "ios-cancel"),
    ("filename", "filename"),
    ("dl_meta", "dl-meta"),
    ("row_sub_idle", "row-idle"),
    ("row_sub_downloading", "row-downloading"),
    ("row_sub_done", "row-done"),
    ("row_sub_failed", "row-failed"),
    ("row_sub_failed_pack", "row-failed-pack"),
    ("row_sub_failed_pack_retry", "row-failed-pack-retry"),
    ("row_sub_closed", "row-closed"),
    ("miss_head", "miss-head"),
    ("miss_body", "miss-body"),
    ("miss_more", "miss-more"),
    ("miss_txt", "miss-txt"),
    ("miss_item_sub", "miss-item-sub"),
    ("miss_toggle", "miss-toggle"),
    ("allmiss_sub", "allmiss-sub"),
    ("k_count", "k-count"),
    ("k_size", "k-size"),
)


def _private(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _frameable(response):
    """Same-origin iframe must be able to read a non-attachment JSON body."""
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return _private(response)


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("export temp delete failed")


async def _zip_chunks(path: Path, on_close, cancel) -> AsyncIterator[bytes]:
    try:
        with path.open("rb") as handle:
            while True:
                if cancel is not None and cancel.is_set():
                    break
                size = export_service.READ_CHUNK
                chunk = await anyio.to_thread.run_sync(lambda size=size: handle.read(size))
                if not chunk:
                    break
                if cancel is not None and cancel.is_set():
                    break
                yield chunk
    finally:
        # Delete the temp file before releasing the slot (R3).
        _unlink(path)
        if on_close is not None:
            on_close()


class ZipDownload(StreamingResponse):
    """Removes the temp file before releasing the pack slot.

    The body generator may never start (disconnect before the first byte).
    aclose() then skips the generator finally, and ASGI 2.4 skips BackgroundTask
    when send fails. This finally still unlinks, then releases.
    """

    def __init__(self, content, *, path: Path, on_close=None, **kwargs) -> None:
        super().__init__(content, **kwargs)
        self._path = path
        self._on_close = on_close

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
            _unlink(self._path)
            if self._on_close is not None:
                self._on_close()


def stream_zip(
    path: Path,
    on_close=None,
    cancel=None,
    packed: int | None = None,
    missing: list | None = None,
    day=None,
    dl: str | None = None,
) -> ZipDownload:
    size = path.stat().st_size
    when = day if day is not None else export_filename_day(None)
    headers = {
        "Cache-Control": "private, no-store, no-transform",
        "Content-Disposition": export_service.content_disposition(when),
        "Content-Length": str(size),
        "X-Frame-Options": "SAMEORIGIN",
    }
    if packed is not None:
        headers["X-Export-Packed"] = str(packed)
    if missing:
        headers["X-Export-Missing"] = missing_header_value(missing)
    if dl and _DL_RE.fullmatch(dl):
        headers["Set-Cookie"] = f"fa_dl={dl}; Path=/; Max-Age=120; SameSite=Strict"
    return ZipDownload(
        _zip_chunks(path, on_close, cancel),
        path=path,
        on_close=on_close,
        media_type="application/zip",
        headers=headers,
        background=BackgroundTask(_unlink, path),
    )


def _own(db: Session, ctx: MemberCtx):
    # family_id and uploader_id come from the session membership, never the query string.
    return list_my_photos(db, family_id=ctx.membership.family_id, uploader_id=ctx.user.id)


def _all_missing_body(missing: list) -> JSONResponse:
    return _frameable(
        JSONResponse(
            {
                "detail": "all_missing",
                "total": len(missing),
                "items": missing[:20],
            },
            status_code=422,
        )
    )


@router.get("/api/export/my-photos")
def export_count(
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
) -> JSONResponse:
    count = count_my_photos(db, family_id=ctx.membership.family_id, uploader_id=ctx.user.id)
    return _private(JSONResponse({"count": count}))


@router.post("/api/export/cancel")
async def export_cancel(ctx: MemberCtx = Depends(require_member())):
    key = (ctx.membership.family_id, ctx.user.id)
    export_service.request_cancel(key)
    stopped = await anyio.to_thread.run_sync(
        lambda: export_service.wait_slot_free(key, constants.EXPORT_CANCEL_WAIT_S)
    )
    return _private(JSONResponse({"stopped": bool(stopped)}))


@router.get("/api/export/my-photos.zip")
async def export_zip(
    request: Request,
    d: str | None = None,
    dl: str | None = None,
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
):
    family_id = ctx.membership.family_id
    user_id = ctx.user.id
    # `d` is only the download filename. It is not a filter.
    items = list_my_photos(db, family_id=family_id, uploader_id=user_id)
    items.extend(list_my_videos(db, family_id=family_id, uploader_id=user_id))
    markdown = export_markdown(db, family_id=family_id, author_id=user_id)
    sidecars = video_export_sidecars(db, family_id=family_id, uploader_id=user_id)
    sidecars.extend(photo_export_sidecars(db, family_id=family_id, uploader_id=user_id))
    db.rollback()
    extras: list[tuple[str, bytes]] = []
    if markdown:
        extras.append(("日志.md", markdown.encode("utf-8")))
    extras.extend(sidecars)
    if not items and not extras:
        return _frameable(JSONResponse({"detail": "no_photos", "count": 0}, status_code=404))
    key = (family_id, user_id)
    cancel = threading.Event()
    acquired = await anyio.to_thread.run_sync(
        lambda: export_service.acquire_pack_slot(key, constants.EXPORT_LOCK_WAIT_S, cancel)
    )
    if acquired is None:
        return _frameable(JSONResponse({"detail": "export_busy"}, status_code=409))
    if acquired is CANCELLED:
        return _frameable(JSONResponse({"detail": "export_failed"}, status_code=503))
    slot: PackSlot = acquired
    handed = False
    try:
        outcome: dict = {}
        stop_watch = anyio.Event()

        async def watch() -> None:
            try:
                while not stop_watch.is_set():
                    if await request.is_disconnected():
                        export_service.notify_cancel(cancel)
                        return
                    with anyio.move_on_after(export_service._DISCONNECT_POLL_S):
                        await stop_watch.wait()
            except anyio.get_cancelled_exc_class():
                return

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(watch)
                try:
                    outcome["result"] = await anyio.to_thread.run_sync(
                        lambda: build_zip(items, cancel=cancel, extras=extras)
                    )
                except Exception as exc:
                    outcome["error"] = exc
                finally:
                    stop_watch.set()
        except Exception as exc:
            outcome.setdefault("error", exc)

        error = outcome.get("error")
        if isinstance(error, ExportAllMissing):
            return _all_missing_body(error.missing)
        if error is not None:
            return _frameable(JSONResponse({"detail": "export_failed"}, status_code=503))
        result = outcome.get("result")
        if not isinstance(result, export_service.BuildResult):
            raise RuntimeError("export finished without a result")
        try:
            response = stream_zip(
                result.path,
                on_close=slot.release,
                cancel=cancel,
                packed=result.packed,
                missing=result.missing,
                day=export_filename_day(d),
                dl=dl,
            )
        except Exception:
            _unlink(result.path)
            raise
        handed = True
        return response
    finally:
        if not handed:
            slot.release()


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
        return gate, None, None, None
    family_id = gate.membership.family_id
    user_id = gate.user.id
    photos = count_my_photos(db, family_id=family_id, uploader_id=user_id)
    journals = count_my_journals(db, family_id=family_id, author_id=user_id)
    videos = count_my_videos(db, family_id=family_id, uploader_id=user_id)
    return gate, photos, journals, videos


def _upload_context(db: Session, gate) -> dict:
    children = list_children(db, gate.family.id)
    selected = children[0] if len(children) == 1 else None
    return {
        "children": children,
        "selected": selected,
        "kind": "",
        "today": shanghai_today().isoformat(),
        "error": None,
        "upload_inplace": True,
    }


@router.get("/family/export")
def export_page(request: Request, db: Session = Depends(get_db)):
    gate, count, journals, videos = _page(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    assert count is not None and journals is not None and videos is not None
    export_empty = count == 0 and journals == 0 and videos == 0
    context = {
        **_flags(gate.user, gate.membership),
        "family": gate.family,
        "count": count,
        "journal_count": journals,
        "export_empty": export_empty,
        "state": "confirm",
        "show_nav": True,
        "tab": "settings",
        "export_header_wait_ms": export_service.header_wait_ms(),
        "export_blob_max_bytes": export_service.blob_max_bytes(),
        "copy_attrs": _COPY_ATTRS,
    }
    if export_empty and gate.membership.role in {"admin", "uploader"}:
        context.update(_upload_context(db, gate))
    response = render(request, "export.html", context)
    return _private(response)


@router.get("/fragments/export/sheet")
def export_sheet(request: Request, state: str = "confirm", db: Session = Depends(get_db)):
    gate, count, journals, videos = _page(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    assert count is not None and journals is not None and videos is not None
    if count == 0 and journals == 0 and videos == 0:
        response = render(
            request,
            "fragments/export_empty.html",
            {
                **_flags(gate.user, gate.membership),
                "family": gate.family,
                "count": count,
                "show_nav": False,
            },
            status_code=404,
        )
        return _private(response)
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
