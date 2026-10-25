"""One sentence a day JSON. Family id always comes from the session."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.constants import ROLE_RANK
from app.db import get_db
from app.deps import MemberCtx, require_member
from app.services.day_notes import (
    DayNoteError,
    DayNoteUnavailable,
    create_day_note,
    delete_day_note,
    list_day_notes,
    parse_on_date,
    update_day_note,
    writer_denied,
)

router = APIRouter(tags=["day-notes"])


def day_note_writer(ctx: MemberCtx = Depends(require_member())) -> MemberCtx:
    """Viewer is refused before any sentence row is loaded."""
    if ROLE_RANK[ctx.membership.role] < ROLE_RANK["uploader"]:
        raise writer_denied()
    return ctx


def _nostore(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload: dict, status: int):
    return _nostore(JSONResponse(payload, status_code=status))


def _busy():
    return _json({"detail": "服务忙"}, 503)


async def _payload(request: Request) -> dict:
    ctype = request.headers.get("content-type", "")
    if "application/json" not in ctype:
        from app.day_note_copy import DAY_NOTE

        raise DayNoteError(422, "body_invalid", DAY_NOTE["err_empty"])
    try:
        body = await request.json()
    except Exception as exc:
        from app.day_note_copy import DAY_NOTE

        raise DayNoteError(422, "body_invalid", DAY_NOTE["err_empty"]) from exc
    if not isinstance(body, dict):
        from app.day_note_copy import DAY_NOTE

        raise DayNoteError(422, "body_invalid", DAY_NOTE["err_empty"])
    return body


@router.get("/api/families/current/day-notes")
def get_day_notes(
    request: Request,
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
):
    # family_id on the query string is intentionally unread.
    on_date = parse_on_date(request.query_params.get("on_date"))
    try:
        payload = list_day_notes(db, ctx, on_date)
    except DayNoteUnavailable:
        return _busy()
    return _json(payload, 200)


@router.post("/api/families/current/day-notes")
async def post_day_note(
    request: Request,
    ctx: MemberCtx = Depends(day_note_writer),
    db: Session = Depends(get_db),
):
    payload = await _payload(request)
    try:
        body, status = create_day_note(db, ctx, payload)
    except DayNoteUnavailable:
        return _busy()
    return _json(body, status)


@router.put("/api/day-notes/{note_id}")
async def put_day_note(
    note_id: int,
    request: Request,
    ctx: MemberCtx = Depends(day_note_writer),
    db: Session = Depends(get_db),
):
    payload = await _payload(request)
    try:
        body = update_day_note(db, ctx, note_id, payload)
    except DayNoteUnavailable:
        return _busy()
    return _json(body, 200)


@router.delete("/api/day-notes/{note_id}")
def remove_day_note(
    note_id: int,
    ctx: MemberCtx = Depends(day_note_writer),
    db: Session = Depends(get_db),
):
    try:
        delete_day_note(db, ctx, note_id)
    except DayNoteUnavailable:
        return _busy()
    return _nostore(Response(status_code=204))
