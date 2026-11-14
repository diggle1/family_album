"""Old-photo tray. One file per request, streamed to a temp file with a hard cap."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.constants import (
    DAY_PILES,
    LEGACY_IMPORT,
    LEGACY_IMPORT_BATCH_MAX_BYTES,
    LEGACY_IMPORT_CONCURRENCY,
    LEGACY_IMPORT_MAX_BYTES,
    LEGACY_IMPORT_MAX_EDGE,
    LEGACY_IMPORT_MAX_FILES,
    ROLE_RANK,
)
from app.db import get_db
from app.deps import MemberCtx, require_member
from app.routers.pages import _flags, gate_member, render
from app.services.children import child_in_family, list_children
from app.services.capture_day import read_capture_day
from app.services.legacy_import import (
    LegacyImportError,
    import_spooled_photo,
    note_session,
    parse_claimed_on_date,
    require_child_ids,
)

router = APIRouter(tags=["legacy-import"])

_CHUNK = 64 * 1024
_FILE_REQUIRED = "请上传照片"


def _json(payload: dict, status: int) -> JSONResponse:
    response = JSONResponse(payload, status_code=status)
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _writer(ctx: MemberCtx) -> MemberCtx:
    if ROLE_RANK[ctx.membership.role] < ROLE_RANK["uploader"]:
        raise LegacyImportError(403, "legacy_import_readonly", LEGACY_IMPORT["err_perm"])
    return ctx


async def _spool(upload: UploadFile, dest: Path, limit: int) -> int:
    """Copy one upload to disk. Stop once the single-file cap is crossed."""
    total = 0
    with dest.open("wb") as handle:
        while True:
            chunk = await upload.read(_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise LegacyImportError(400, "legacy_import_file_too_large", LEGACY_IMPORT["fail_why_mb"])
            handle.write(chunk)
    return total


@router.get("/legacy-import")
def legacy_import_page(
    request: Request,
    child: int | None = None,
    db: Session = Depends(get_db),
):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    if gate.membership.role not in {"admin", "uploader"}:
        raise LegacyImportError(403, "legacy_import_readonly", LEGACY_IMPORT["err_perm"])
    children = list_children(db, gate.family.id)
    explicit = None
    if child is not None:
        explicit = child_in_family(db, family_id=gate.family.id, child_id=child)
    if len(children) == 1:
        selected_ids = [children[0].id]
    elif explicit is not None:
        selected_ids = [explicit.id]
    else:
        selected_ids = []
    children_json = [
        {"id": item.id, "name": item.name, "birth": item.birth_date.isoformat()} for item in children
    ]
    return render(
        request,
        "legacy_import.html",
        {
            **_flags(gate.user, gate.membership),
            "family": gate.family,
            "children": children,
            "selected_ids": selected_ids,
            "single": len(children) == 1,
            "max_files": LEGACY_IMPORT_MAX_FILES,
            "max_file": LEGACY_IMPORT_MAX_BYTES,
            "max_batch": LEGACY_IMPORT_BATCH_MAX_BYTES,
            "max_edge": LEGACY_IMPORT_MAX_EDGE,
            "concurrency": LEGACY_IMPORT_CONCURRENCY,
            "who": gate.user.name or "",
            "show_nav": True,
            "children_json": json.dumps(children_json, ensure_ascii=True),
            "selected_json": json.dumps(selected_ids),
            "piles": DAY_PILES,
        },
    )


@router.post("/api/families/current/legacy-import/photos", status_code=201)
async def post_legacy_photo(
    request: Request,
    on_date: str | None = Form(None),
    child_ids: list[str] | None = Form(None),
    import_session: str | None = Form(None),
    client_name: str | None = Form(None),
    file: UploadFile | None = File(None),
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """One photo. ``family_id`` on the form is ignored; the session owns the family.

    A day read from the file wins. A missing day is not filled with today.
    """
    del client_name
    _writer(ctx)
    if file is None:
        raise LegacyImportError(422, "file_required", _FILE_REQUIRED)
    claimed = parse_claimed_on_date(on_date)
    chosen = require_child_ids(child_ids)
    note_session(import_session)
    header = request.headers.get("content-length")
    if header and header.isdigit() and int(header) > LEGACY_IMPORT_MAX_BYTES + 256 * 1024:
        raise LegacyImportError(400, "legacy_import_file_too_large", LEGACY_IMPORT["fail_why_mb"])
    from app.config import get_settings

    root = get_settings().media_root
    root.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=".li-", dir=root, delete=False)
    path = Path(handle.name)
    handle.close()
    try:
        total = await _spool(file, path, LEGACY_IMPORT_MAX_BYTES)
        payload = import_spooled_photo(
            db,
            family_id=ctx.membership.family_id,
            uploader_id=ctx.user.id,
            on_date=claimed,
            child_ids=chosen,
            path=path,
            byte_size=total,
        )
    finally:
        path.unlink(missing_ok=True)
    return _json(payload, 201)


@router.post("/api/families/current/legacy-import/peek-date")
async def peek_legacy_date(
    request: Request,
    file: UploadFile | None = File(None),
    ctx: MemberCtx = Depends(require_member()),
) -> JSONResponse:
    """Read one file's capture day. Nothing is stored and nothing is written."""
    _writer(ctx)
    if file is None:
        raise LegacyImportError(422, "file_required", _FILE_REQUIRED)
    header = request.headers.get("content-length")
    if header and header.isdigit() and int(header) > LEGACY_IMPORT_MAX_BYTES + 256 * 1024:
        raise LegacyImportError(400, "legacy_import_file_too_large", LEGACY_IMPORT["fail_why_mb"])
    from app.config import get_settings

    root = get_settings().media_root
    root.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=".pk-", dir=root, delete=False)
    path = Path(handle.name)
    handle.close()
    try:
        await _spool(file, path, LEGACY_IMPORT_MAX_BYTES)
        try:
            found = read_capture_day(path)
        except Exception:
            found = None
    finally:
        path.unlink(missing_ok=True)
    text = found.isoformat() if found is not None else None
    return _json({"on_date": text}, 200)
