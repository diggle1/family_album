"""Journal pages and JSON. Family id always comes from the session."""

from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.constants import FORBIDDEN, JOURNAL, JOURNAL_BODY_MAX, JOURNAL_BODY_WARN, ROLE_RANK
from app.db import get_db
from app.deps import MemberCtx, require_member
from app.routers.pages import _flags, gate_member, render
from app.services.children import list_children
from app.services.clock import shanghai_today
from app.services.journals import (
    JournalError,
    create_journal,
    delete_journal,
    detail_payload,
    display_who,
    list_journals,
    not_found,
    picker_page,
    rights,
    update_journal,
    visible_journal,
)

logger = logging.getLogger("family_album.journal")

router = APIRouter(tags=["journals"])

_KEY = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

_NEAR_FMT = "还能写 {left} 字 · {n}/2000"


def journal_writer(ctx: MemberCtx = Depends(require_member())) -> MemberCtx:
    if ROLE_RANK[ctx.membership.role] < ROLE_RANK["uploader"]:
        raise JournalError(403, "journal_readonly", FORBIDDEN)
    return ctx


def _nostore(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _json(payload: dict, status: int):
    return _nostore(JSONResponse(payload, status_code=status))


def _require_json(request: Request) -> None:
    ctype = request.headers.get("content-type", "")
    if "application/json" not in ctype:
        raise JournalError(415, "unsupported_media", "不支持的内容类型")


def _read_key(request: Request, *, required: bool) -> str | None:
    raw = request.headers.get("idempotency-key")
    if raw is None or raw.strip() == "":
        if required:
            raise JournalError(400, "idempotency_key_invalid", "幂等键无效")
        return None
    key = raw.strip()
    if _KEY.fullmatch(key) is None:
        if required:
            raise JournalError(400, "idempotency_key_invalid", "幂等键无效")
        logger.info("journal put ignored invalid idempotency key")
        return None
    return key


async def _payload(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception as exc:
        raise JournalError(400, "body_invalid", JOURNAL["err_empty"]) from exc
    if not isinstance(body, dict):
        raise JournalError(400, "body_invalid", JOURNAL["err_empty"])
    return body


def _gate(request: Request, db: Session):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return _nostore(gate)
    return gate


def _missing(request: Request, gate: MemberCtx, *, recover_id: int | None = None, status: int = 404):
    response = render(
        request,
        "journal_missing.html",
        {
            **_flags(gate.user, gate.membership),
            "family": gate.family,
            "recover_edit": recover_id is not None,
            "requested_id": recover_id or "",
            "show_nav": False,
        },
        status_code=status,
    )
    return _nostore(response)


def _write_page(
    request: Request,
    db: Session,
    gate: MemberCtx,
    *,
    mode: str,
    journal=None,
):
    children = list_children(db, gate.family.id)
    selected = None
    if journal is not None:
        selected = next((child for child in children if child.id == journal.child_id), None)
    else:
        raw = request.query_params.get("child")
        if raw and raw.isdigit():
            selected = next((child for child in children if child.id == int(raw)), None)
        if selected is None and len(children) == 1:
            selected = children[0]
    payload = detail_payload(db, journal, gate.membership) if journal is not None else None
    photos = payload["photos"] if payload else []
    initial = {
        "body": journal.body if journal is not None else "",
        "date": journal.on_date.isoformat() if journal is not None else shanghai_today().isoformat(),
        "childId": selected.id if selected is not None else None,
        "photoIds": [slot["id"] for slot in photos if slot.get("id")],
        "photos": [
            {"id": slot["id"], "url": slot["media_url"]} for slot in photos if slot.get("id")
        ],
        "updatedAt": payload["updated_at"] if payload else None,
    }
    children_json = [
        {"id": child.id, "name": child.name, "birth": child.birth_date.isoformat()} for child in children
    ]
    response = render(
        request,
        "journal_write.html",
        {
            **_flags(gate.user, gate.membership),
            "family": gate.family,
            "children": children,
            "selected": selected,
            "mode": mode,
            "journal": journal,
            "today": shanghai_today().isoformat(),
            "body_max": JOURNAL_BODY_MAX,
            "body_warn": JOURNAL_BODY_WARN,
            "near_fmt": _NEAR_FMT,
            "show_nav": False,
            "back_href": f"/?child={selected.id}" if selected is not None else "/",
            "children_json": json.dumps(children_json, ensure_ascii=True),
            "initial_json": json.dumps(initial, ensure_ascii=True),
            "submit_label": JOURNAL["w_submit_edit"] if mode == "edit" else JOURNAL["w_submit"],
        },
    )
    return _nostore(response)


@router.get("/api/journals")
def get_journals(
    child_id: int,
    before: str | None = None,
    limit: int = Query(30, ge=1, le=100),
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
):
    # family_id on the query string is intentionally unread.
    return _json(list_journals(db, ctx, child_id=child_id, before=before, limit=limit), 200)


@router.get("/api/journals/{journal_id}")
def get_journal(
    journal_id: int,
    ctx: MemberCtx = Depends(require_member()),
    db: Session = Depends(get_db),
):
    journal = visible_journal(db, family_id=ctx.family.id, journal_id=journal_id)
    if journal is None:
        raise not_found()
    return _json(detail_payload(db, journal, ctx.membership), 200)


@router.post("/api/journals")
async def post_journal(
    request: Request,
    ctx: MemberCtx = Depends(journal_writer),
    db: Session = Depends(get_db),
):
    _require_json(request)
    key = _read_key(request, required=True)
    assert key is not None
    payload = await _payload(request)
    body, status = create_journal(db, ctx, key, payload)
    return _json(body, status)


@router.put("/api/journals/{journal_id}")
async def put_journal(
    journal_id: int,
    request: Request,
    ctx: MemberCtx = Depends(journal_writer),
    db: Session = Depends(get_db),
):
    _require_json(request)
    key = _read_key(request, required=False)
    payload = await _payload(request)
    return _json(update_journal(db, ctx, journal_id, payload, key), 200)


@router.delete("/api/journals/{journal_id}")
def remove_journal(
    journal_id: int,
    ctx: MemberCtx = Depends(journal_writer),
    db: Session = Depends(get_db),
):
    delete_journal(db, ctx, journal_id)
    return _nostore(Response(status_code=204))


@router.get("/journals/new")
def page_new(request: Request, db: Session = Depends(get_db)):
    gate = _gate(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    if ROLE_RANK[gate.membership.role] < ROLE_RANK["uploader"]:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    return _write_page(request, db, gate, mode="new")


@router.get("/journals/{journal_id}")
def page_detail(journal_id: int, request: Request, db: Session = Depends(get_db)):
    gate = _gate(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    journal = visible_journal(db, family_id=gate.family.id, journal_id=journal_id)
    if journal is None:
        return _missing(request, gate)
    child = next(child for child in list_children(db, gate.family.id) if child.id == journal.child_id)
    payload = detail_payload(db, journal, gate.membership)
    can_edit, can_delete = rights(gate.membership, journal)
    kind = request.query_params.get("jtoast")
    toast = None
    if kind == "edited":
        toast = JOURNAL["toast_edited"]
    response = render(
        request,
        "journal_detail.html",
        {
            **_flags(gate.user, gate.membership),
            "family": gate.family,
            "journal": journal,
            "child": child,
            "payload": payload,
            "who": display_who(payload["author"]),
            "can_edit": can_edit,
            "can_delete": can_delete,
            "show_nav": False,
            "toast": toast,
        },
    )
    return _nostore(response)


@router.get("/journals/{journal_id}/edit")
def page_edit(journal_id: int, request: Request, db: Session = Depends(get_db)):
    gate = _gate(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    if ROLE_RANK[gate.membership.role] < ROLE_RANK["uploader"]:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    journal = visible_journal(db, family_id=gate.family.id, journal_id=journal_id)
    if journal is None:
        return _missing(request, gate, recover_id=journal_id)
    if journal.author_id != gate.user.id:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    return _write_page(request, db, gate, mode="edit", journal=journal)


@router.get("/fragments/journal/picker")
def page_picker(
    request: Request,
    before: str | None = None,
    ctx: MemberCtx = Depends(journal_writer),
    db: Session = Depends(get_db),
):
    page = picker_page(db, family_id=ctx.family.id, before=before)
    response = render(
        request,
        "fragments/journal_picker.html",
        {"page": page, "show_nav": False},
    )
    return _nostore(response)
