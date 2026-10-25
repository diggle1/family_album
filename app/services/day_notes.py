"""One sentence a day: upsert, soft-delete, and the optional timeline line.

Family id always comes from the session. A same-named request field is ignored.
Character count is Unicode code points after trim. A newline inside the
sentence is rejected. The partial unique index keeps one active row per
family, author, and calendar day.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import JOURNAL, ROLE_RANK
from app.day_note_copy import (
    DAY_NOTE,
    DAY_NOTE_BODY_MAX,
    DAY_NOTE_CHILD_GONE,
    DAY_NOTE_SNIP,
)
from app.deps import MemberCtx
from app.models import Child, DayNote, DayNoteChild, Membership, User
from app.services.assoc import ChildSetError, parse_child_id_list, resolve_children
from app.services.children import child_in_family, list_children
from app.services.clock import age_label, as_utc, date_label, isoformat, weekday_zh, utcnow

logger = logging.getLogger("family_album.day_note")

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NOT_FOUND = "找不到"


class DayNoteError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        self.status = status
        self.code = code
        self.detail = detail
        super().__init__(detail)


class DayNoteUnavailable(Exception):
    """The sentence store could not be used. The day page still opens."""


def raise_if_busy() -> None:
    """Tests replace this. Production leaves it as a no-op."""
    return None


def not_found() -> DayNoteError:
    return DayNoteError(404, "day_note_not_found", NOT_FOUND)


def _fill(template: str, **values: object) -> str:
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def _parse_day(value: object) -> date | None:
    if not isinstance(value, str) or _DAY.fullmatch(value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def clip_sentence(body: str, limit: int = DAY_NOTE_SNIP) -> str:
    points = list(body)
    if len(points) <= limit:
        return body
    return "".join(points[:limit]) + "…"


def normalize_sentence(raw: object) -> str:
    if not isinstance(raw, str):
        raise DayNoteError(422, "body_invalid", DAY_NOTE["err_empty"])
    text = raw.strip()
    if text == "":
        raise DayNoteError(422, "body_empty", DAY_NOTE["err_empty"])
    if "\n" in text or "\r" in text:
        raise DayNoteError(422, "body_has_newline", DAY_NOTE["err_too_long"])
    if len(text) > DAY_NOTE_BODY_MAX:
        raise DayNoteError(422, "body_too_long", DAY_NOTE["err_too_long"])
    return text


def _child_ids_from_payload(payload: dict) -> list[int]:
    if "child_ids" not in payload:
        raise DayNoteError(422, "child_required", DAY_NOTE["err_no_child"])
    raw = payload.get("child_ids")
    if isinstance(raw, list) and len(raw) == 0:
        raise DayNoteError(422, "child_required", DAY_NOTE["err_no_child"])
    parsed = parse_child_id_list(raw)
    if not parsed:
        raise DayNoteError(422, "child_invalid", DAY_NOTE["err_no_child"])
    return parsed


def _resolve(db: Session, *, family_id: int, child_ids: list[int]) -> list[Child]:
    try:
        resolved = resolve_children(
            db,
            family_id=family_id,
            child_ids=child_ids,
            legacy_child_id=None,
            used_list=True,
            required_detail=DAY_NOTE["err_no_child"],
            missing_detail=DAY_NOTE["err_no_child"],
            birth_detail=DAY_NOTE["err_no_child"],
        )
    except ChildSetError as exc:
        code = "child_required" if exc.code == "child_required" else "child_invalid"
        raise DayNoteError(422, code, DAY_NOTE["err_no_child"]) from exc
    return list(resolved.children)


def _links(db: Session, note_id: int) -> list[DayNoteChild]:
    return list(
        db.scalars(
            select(DayNoteChild)
            .where(DayNoteChild.note_id == note_id)
            .order_by(DayNoteChild.position.asc(), DayNoteChild.child_id.asc())
        ).all()
    )


def _link_ids(db: Session, note_id: int) -> list[int]:
    return [row.child_id for row in _links(db, note_id)]


def _replace_children(db: Session, note: DayNote, children: list[Child]) -> None:
    db.execute(delete(DayNoteChild).where(DayNoteChild.note_id == note.id))
    db.flush()
    for index, child in enumerate(children):
        db.add(DayNoteChild(note_id=note.id, child_id=child.id, position=index))


def _clear_children(db: Session, note_id: int) -> None:
    db.execute(delete(DayNoteChild).where(DayNoteChild.note_id == note_id))


def load_active_notes(db: Session, family_id: int, on_date: date | None = None) -> list[DayNote]:
    stmt = select(DayNote).where(DayNote.family_id == family_id, DayNote.deleted_at.is_(None))
    if on_date is not None:
        stmt = stmt.where(DayNote.on_date == on_date)
    stmt = stmt.order_by(DayNote.created_at.asc(), DayNote.id.asc())
    return list(db.scalars(stmt).all())


def _active(db: Session, *, family_id: int, author_id: int, on_date: date) -> DayNote | None:
    return db.scalar(
        select(DayNote).where(
            DayNote.family_id == family_id,
            DayNote.author_id == author_id,
            DayNote.on_date == on_date,
            DayNote.deleted_at.is_(None),
        )
    )


def visible_note(db: Session, *, family_id: int, note_id: int) -> DayNote | None:
    note = db.get(DayNote, note_id)
    if note is None or note.family_id != family_id or note.deleted_at is not None:
        return None
    return note


def _display_name(db: Session, note: DayNote) -> str:
    user = db.get(User, note.author_id)
    name = user.name if user is not None and user.name else "未命名"
    membership = db.scalar(select(Membership).where(Membership.user_id == note.author_id))
    if membership is None or membership.family_id != note.family_id:
        return _fill(JOURNAL["former"], who=name)
    return name


def rights(viewer: Membership, note: DayNote) -> tuple[bool, bool]:
    rank = ROLE_RANK[viewer.role]
    is_author = note.author_id == viewer.user_id
    can_edit = rank >= ROLE_RANK["uploader"] and is_author
    can_delete = viewer.role == "admin" or (rank >= ROLE_RANK["uploader"] and is_author)
    return can_edit, can_delete


def _children_view(db: Session, note: DayNote) -> list[dict]:
    items: list[dict] = []
    for link in _links(db, note.id):
        child = db.get(Child, link.child_id)
        if child is not None and child.family_id == note.family_id:
            items.append({"id": child.id, "name": child.name, "avatar_url": None})
        else:
            items.append({"id": link.child_id, "name": DAY_NOTE_CHILD_GONE, "avatar_url": None})
    return items


def _living_ids(db: Session, note: DayNote) -> list[int]:
    found: list[int] = []
    for link in _links(db, note.id):
        child = child_in_family(db, family_id=note.family_id, child_id=link.child_id)
        if child is not None:
            found.append(child.id)
    return found


def detail_payload(db: Session, note: DayNote, viewer: Membership) -> dict:
    can_edit, can_delete = rights(viewer, note)
    return {
        "id": note.id,
        "on_date": note.on_date.isoformat(),
        "body": note.body,
        "author": {"id": note.author_id, "display_name": _display_name(db, note)},
        "children": _children_view(db, note),
        "created_at": isoformat(note.created_at),
        "updated_at": isoformat(note.updated_at),
        "can_edit": can_edit,
        "can_delete": can_delete,
    }


def _apply(db: Session, note: DayNote, body: str, children: list[Child]) -> None:
    note.body = body
    note.updated_at = utcnow()
    _replace_children(db, note, children)


def _parse_write(db: Session, *, family_id: int, payload: dict, on_date: date | None) -> tuple[date, str, list[Child]]:
    body = normalize_sentence(payload.get("body", None) if "body" in payload else None)
    child_ids = _child_ids_from_payload(payload)
    children = _resolve(db, family_id=family_id, child_ids=child_ids)
    if on_date is None:
        parsed = _parse_day(payload.get("on_date"))
        if parsed is None:
            raise DayNoteError(422, "on_date_invalid", "日期无效")
        on_date = parsed
    return on_date, body, children


def list_day_notes(db: Session, ctx: MemberCtx, on_date: date) -> dict:
    raise_if_busy()
    rows = load_active_notes(db, ctx.family.id, on_date)
    return {"notes": [detail_payload(db, row, ctx.membership) for row in rows]}


def create_day_note(db: Session, ctx: MemberCtx, payload: dict) -> tuple[dict, int]:
    """Insert, or overwrite the author's active sentence for that day."""
    raise_if_busy()
    on_date, body, children = _parse_write(db, family_id=ctx.family.id, payload=payload, on_date=None)
    existing = _active(db, family_id=ctx.family.id, author_id=ctx.user.id, on_date=on_date)
    if existing is not None:
        _apply(db, existing, body, children)
        db.commit()
        db.refresh(existing)
        return detail_payload(db, existing, ctx.membership), 200
    now = utcnow()
    note = DayNote(
        family_id=ctx.family.id,
        author_id=ctx.user.id,
        on_date=on_date,
        body=body,
        created_at=now,
        updated_at=now,
    )
    try:
        db.add(note)
        db.flush()
        _replace_children(db, note, children)
        db.commit()
    except IntegrityError:
        db.rollback()
        raced = _active(db, family_id=ctx.family.id, author_id=ctx.user.id, on_date=on_date)
        if raced is None:
            raise
        _apply(db, raced, body, children)
        db.commit()
        db.refresh(raced)
        return detail_payload(db, raced, ctx.membership), 200
    db.refresh(note)
    return detail_payload(db, note, ctx.membership), 201


def update_day_note(db: Session, ctx: MemberCtx, note_id: int, payload: dict) -> dict:
    raise_if_busy()
    note = visible_note(db, family_id=ctx.family.id, note_id=note_id)
    if note is None:
        raise not_found()
    can_edit, _can_delete = rights(ctx.membership, note)
    if not can_edit:
        raise DayNoteError(403, "day_note_not_author", DAY_NOTE["err_perm"])
    if "on_date" in payload:
        parsed = _parse_day(payload.get("on_date"))
        if parsed is None:
            raise DayNoteError(422, "on_date_invalid", "日期无效")
        if parsed != note.on_date:
            raise DayNoteError(422, "on_date_immutable", "日期不能改")
    _on_date, body, children = _parse_write(db, family_id=ctx.family.id, payload=payload, on_date=note.on_date)
    _apply(db, note, body, children)
    db.commit()
    db.refresh(note)
    return detail_payload(db, note, ctx.membership)


def delete_day_note(db: Session, ctx: MemberCtx, note_id: int) -> None:
    raise_if_busy()
    note = visible_note(db, family_id=ctx.family.id, note_id=note_id)
    if note is None:
        raise not_found()
    _can_edit, can_delete = rights(ctx.membership, note)
    if not can_delete:
        raise DayNoteError(403, "day_note_not_author", DAY_NOTE["err_perm"])
    now = utcnow()
    note.body = ""
    note.deleted_at = now
    note.updated_at = now
    _clear_children(db, note.id)
    db.commit()


def _enrolled_briefs(children: list[Child]) -> list[dict]:
    return [{"id": child.id, "name": child.name, "birth": child.birth_date.isoformat()} for child in children]


def _selected_ids(enrolled: list[Child], child_id: int | None, own: DayNote | None, living: list[int]) -> list[int]:
    enrolled_ids = {child.id for child in enrolled}
    if own is not None:
        return [item for item in living if item in enrolled_ids]
    if child_id is not None and child_id in enrolled_ids:
        return [child_id]
    return []


def day_page_state(db: Session, ctx: MemberCtx, on_date: date, *, child_id: int | None) -> dict:
    """Template state. A busy store leaves the write entry and omits cards."""
    enrolled = list_children(db, ctx.family.id)
    briefs = _enrolled_briefs(enrolled)
    try:
        raise_if_busy()
        rows = load_active_notes(db, ctx.family.id, on_date)
        if child_id is not None:
            rows = [row for row in rows if child_id in _link_ids(db, row.id)]
        own = _active(db, family_id=ctx.family.id, author_id=ctx.user.id, on_date=on_date)
        unavailable = False
    except DayNoteUnavailable:
        logger.warning("day note page read skipped")
        return {
            "notes": [],
            "mode": "new",
            "own_id": None,
            "selected_ids": _selected_ids(enrolled, child_id, None, []),
            "body": "",
            "children": briefs,
            "unavailable": True,
        }
    living = _living_ids(db, own) if own is not None else []
    notes = [detail_payload(db, row, ctx.membership) for row in rows]
    return {
        "notes": notes,
        "mode": "edit" if own is not None else "new",
        "own_id": own.id if own is not None else None,
        "selected_ids": _selected_ids(enrolled, child_id, own, living),
        "body": own.body if own is not None else "",
        "children": briefs,
        "unavailable": unavailable,
    }


def _snippet_for(db: Session, note: DayNote, *, href: str) -> dict:
    who = _display_name(db, note)
    text = clip_sentence(note.body)
    return {
        "who": who,
        "text": text,
        "note_id": note.id,
        "line": _fill(DAY_NOTE["snip_line"], who=who, text=text),
        "href": href,
    }


def _blank_day(on_date: date, birth: date | None) -> dict:
    age = age_label(birth, on_date) if birth is not None else ""
    return {
        "taken_on": on_date.isoformat(),
        "date_label": date_label(on_date),
        "weekday": weekday_zh(on_date),
        "age_label": age,
        "photos": [],
        "byline": "",
        "journals": [],
        "videos": [],
        "day_note_snippet": None,
    }


def attach_day_note_snippets(
    db: Session,
    by_date: dict,
    *,
    family_id: int,
    child_id: int | None,
    birth: date | None,
) -> None:
    """Add the earliest sentence line. A read failure leaves every line empty."""
    for bucket in by_date.values():
        bucket["day_note_snippet"] = None
    try:
        rows = load_active_notes(db, family_id)
        grouped: dict[date, list[DayNote]] = {}
        for note in rows:
            if child_id is not None and child_id not in _link_ids(db, note.id):
                continue
            grouped.setdefault(note.on_date, []).append(note)
        pending: dict[str, dict] = {}
        for on_date, group in grouped.items():
            group.sort(key=lambda row: (as_utc(row.created_at), row.id))
            earliest = group[0]
            key = on_date.isoformat()
            href = f"/children/{child_id}/days/{key}" if child_id is not None else f"/days/{key}"
            snippet = _snippet_for(db, earliest, href=href)
            bucket = by_date.get(key)
            if bucket is None:
                bucket = _blank_day(on_date, birth)
            else:
                bucket = dict(bucket)
            bucket["day_note_snippet"] = snippet
            pending[key] = bucket
    except Exception:
        logger.warning("day note line skipped")
        for bucket in by_date.values():
            bucket["day_note_snippet"] = None
        return
    by_date.update(pending)


def parse_on_date(raw: object) -> date:
    parsed = _parse_day(raw)
    if parsed is None:
        raise DayNoteError(422, "on_date_invalid", "日期无效")
    return parsed


def writer_denied() -> DayNoteError:
    return DayNoteError(403, "day_note_readonly", DAY_NOTE["err_perm"])
