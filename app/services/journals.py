"""Growth journals: validation, idempotent writes, timeline cards, and export text.

Character count is Unicode code points with CR/LF removed. The browser uses
the same rule. Newlines are stored as ``\\n`` and are not trimmed.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.constants import FORBIDDEN, JOURNAL, JOURNAL_BODY_MAX, MULTI_CHILD, ROLE_RANK
from app.deps import MemberCtx
from app.models import Child, Journal, JournalChild, JournalPhoto, Membership, Photo, User
from app.services.assoc import (
    ChildSetError,
    children_payload,
    clear_journal_children,
    ids_of,
    journal_child_ids,
    journal_still_in_family,
    linked_children,
    parse_child_id_list,
    replace_journal_children,
    resolve_children,
)
from app.services.children import build_timeline, child_in_family, list_children
from app.services.clock import age_label, as_utc, date_label, isoformat, shanghai_today, utcnow, weekday_zh

logger = logging.getLogger("family_album.journal")

JOURNAL_PHOTO_MAX = 9
PICKER_PAGE = 60
NOT_FOUND = JOURNAL["not_found"]
CHILD_GONE = "（孩子已不在列表）"
PHOTO_GONE = "（已删除）"

_BLANK = re.compile(r"[\r\n\t \u3000]")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURSOR = re.compile(r"^(\d{4}-\d{2}-\d{2}):(\d+)$")


class JournalError(Exception):
    def __init__(self, status: int, code: str, detail: str, extra: dict | None = None) -> None:
        self.status = status
        self.code = code
        self.detail = detail
        self.extra = extra or {}
        super().__init__(detail)


def not_found() -> JournalError:
    return JournalError(404, "journal_not_found", NOT_FOUND)


def body_len(value: str) -> int:
    return len(value.replace("\r", "").replace("\n", ""))


def is_blank(value: str) -> bool:
    return _BLANK.sub("", value) == ""


def normalize_body(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _lone_surrogate(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _parse_day(value: object) -> date | None:
    if not isinstance(value, str) or _DAY.fullmatch(value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class ValidJournal:
    children: list[Child]
    on_date: date
    body: str
    photo_ids: list[int]

    @property
    def child(self) -> Child:
        return self.children[0]


def _child_messages(payload: dict) -> tuple[str, list[int] | None, int | None]:
    """Return (mode, ids, legacy_id). A present child_ids array wins over child_id."""
    if "child_ids" in payload:
        return "list", parse_child_id_list(payload.get("child_ids")), None
    if "child_id" not in payload or payload.get("child_id") is None:
        return "legacy", None, None
    parsed = _as_int(payload.get("child_id"))
    if parsed is None:
        return "bad_legacy", None, None
    return "legacy", None, parsed


def validate_payload(db: Session, *, family_id: int, payload: dict) -> ValidJournal:
    """Return the first failure only. Order matches the tech contract."""
    raw_body = payload.get("body", None)
    if not isinstance(raw_body, str) or _lone_surrogate(raw_body):
        raise JournalError(400, "body_invalid", JOURNAL["err_empty"])
    if is_blank(raw_body):
        raise JournalError(400, "body_empty", JOURNAL["err_empty"])
    count = body_len(raw_body)
    if count > JOURNAL_BODY_MAX:
        raise JournalError(
            400,
            "body_too_long",
            JOURNAL["err_too_long"].format(n=count - JOURNAL_BODY_MAX),
            {"count": count, "max": JOURNAL_BODY_MAX},
        )
    mode, child_ids, legacy_id = _child_messages(payload)
    used_list = mode == "list"
    if mode == "bad_legacy":
        raise JournalError(400, "child_not_found", JOURNAL["e_kid_gone"])
    if used_list:
        required = MULTI_CHILD["err_no_child"]
        missing = MULTI_CHILD["e_kid_gone"]
    else:
        required = JOURNAL["err_no_child"]
        missing = JOURNAL["e_kid_gone"]
    try:
        resolved = resolve_children(
            db,
            family_id=family_id,
            child_ids=child_ids,
            legacy_child_id=legacy_id,
            used_list=used_list,
            required_detail=required,
            missing_detail=missing,
            birth_detail=JOURNAL["err_date_birth"],
        )
    except ChildSetError as exc:
        raise JournalError(400, exc.code, exc.detail) from exc
    on_date = _parse_day(payload.get("on_date"))
    if on_date is None:
        raise JournalError(400, "date_invalid", JOURNAL["err_date_future"])
    if on_date > shanghai_today():
        raise JournalError(400, "date_future", JOURNAL["err_date_future"])
    try:
        resolved = resolve_children(
            db,
            family_id=family_id,
            child_ids=child_ids,
            legacy_child_id=legacy_id,
            used_list=used_list,
            required_detail=required,
            missing_detail=missing,
            birth_detail=JOURNAL["err_date_birth"],
            on_date=on_date,
        )
    except ChildSetError as exc:
        raise JournalError(400, exc.code, exc.detail) from exc
    raw_photos = payload.get("photo_ids", [])
    if raw_photos is None:
        raw_photos = []
    if not isinstance(raw_photos, list):
        raise JournalError(400, "photo_not_in_family", JOURNAL["err_photo_foreign"], {"invalid_ids": []})
    photo_ids: list[int] = []
    non_int = False
    for item in raw_photos:
        parsed = _as_int(item)
        if parsed is None:
            non_int = True
            continue
        photo_ids.append(parsed)
    if len(raw_photos) > JOURNAL_PHOTO_MAX:
        raise JournalError(400, "too_many_photos", JOURNAL["err_photo_max"])
    if len(photo_ids) != len(set(photo_ids)):
        raise JournalError(400, "photo_duplicate", "照片重复了")
    invalid = _invalid_photo_ids(db, family_id, photo_ids)
    if non_int or invalid:
        raise JournalError(
            400,
            "photo_not_in_family",
            JOURNAL["err_photo_foreign"],
            {"invalid_ids": invalid},
        )
    return ValidJournal(
        children=resolved.children,
        on_date=on_date,
        body=normalize_body(raw_body),
        photo_ids=photo_ids,
    )


def _invalid_photo_ids(db: Session, family_id: int, photo_ids: list[int]) -> list[int]:
    if not photo_ids:
        return []
    rows = db.scalars(select(Photo).where(Photo.id.in_(photo_ids))).all()
    found = {row.id: row for row in rows}
    return [pid for pid in photo_ids if pid not in found or found[pid].family_id != family_id]


def _set_photos(db: Session, journal: Journal, photo_ids: list[int]) -> None:
    db.execute(delete(JournalPhoto).where(JournalPhoto.journal_id == journal.id))
    db.flush()
    if os.environ.get("JOURNAL_FAIL_PHOTO_REPLACE") == "1":
        raise RuntimeError("journal photo replace failed")
    for index, photo_id in enumerate(photo_ids):
        db.add(JournalPhoto(journal_id=journal.id, position=index, photo_id=photo_id))


def _stamp(value: datetime | None) -> str | None:
    text = isoformat(value)
    if text and text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


def _author(db: Session, journal: Journal) -> dict:
    user = db.get(User, journal.author_id)
    name = user.name if user is not None and user.name else "未命名"
    membership = db.scalar(select(Membership).where(Membership.user_id == journal.author_id))
    former = membership is None or membership.family_id != journal.family_id
    return {"label": name, "former": former}


def display_who(author: dict) -> str:
    if author["former"]:
        return JOURNAL["former"].format(who=author["label"])
    return author["label"]


def rights(viewer: Membership, journal: Journal) -> tuple[bool, bool]:
    rank = ROLE_RANK[viewer.role]
    is_author = journal.author_id == viewer.user_id
    can_edit = rank >= ROLE_RANK["uploader"] and is_author
    can_delete = viewer.role == "admin" or (rank >= ROLE_RANK["uploader"] and is_author)
    return can_edit, can_delete


def _slots(db: Session, journal: Journal) -> list[dict]:
    links = db.scalars(
        select(JournalPhoto)
        .where(JournalPhoto.journal_id == journal.id)
        .order_by(JournalPhoto.position.asc())
    ).all()
    if not links:
        return []
    rows = db.scalars(select(Photo).where(Photo.id.in_([link.photo_id for link in links]))).all()
    found = {row.id: row for row in rows}
    slots: list[dict] = []
    for link in links:
        photo = found.get(link.photo_id)
        if photo is None or photo.family_id != journal.family_id:
            slots.append({"id": None, "deleted": True})
            continue
        slots.append(
            {
                "id": photo.id,
                "media_url": f"/media/{photo.id}",
                "page_url": f"/photos/{photo.id}",
                "deleted": False,
            }
        )
    return slots


def _child_brief(child: Child) -> dict:
    return {"id": child.id, "name": child.name}


def _audience(db: Session, journal: Journal) -> list[Child]:
    return linked_children(db, family_id=journal.family_id, child_ids=journal_child_ids(db, journal))


def _with_children(payload: dict, children: list[Child], on_date: date) -> dict:
    briefs = children_payload(children, on_date)
    payload["children"] = briefs
    payload["child_ids"] = ids_of(children)
    if children:
        payload["child"] = _child_brief(children[0])
        payload["age_label"] = age_label(children[0].birth_date, on_date)
    return payload


def detail_payload(
    db: Session,
    journal: Journal,
    viewer: Membership,
    *,
    replayed: bool = False,
) -> dict:
    children = _audience(db, journal)
    if not children:
        raise not_found()
    author = _author(db, journal)
    can_edit, can_delete = rights(viewer, journal)
    payload = {
        "id": journal.id,
        "child": _child_brief(children[0]),
        "on_date": journal.on_date.isoformat(),
        "date_label": date_label(journal.on_date),
        "age_label": age_label(children[0].birth_date, journal.on_date),
        "body": journal.body,
        "author": author,
        "photos": _slots(db, journal),
        "can_edit": can_edit,
        "can_delete": can_delete,
        "updated_at": _stamp(journal.updated_at),
        "replayed": replayed,
    }
    return _with_children(payload, children, journal.on_date)


def summary_payload(db: Session, journal: Journal, viewer: Membership, child: Child) -> dict:
    author = _author(db, journal)
    slots = _slots(db, journal)
    valid = [slot for slot in slots if not slot["deleted"]]
    can_edit, can_delete = rights(viewer, journal)
    children = _audience(db, journal) or [child]
    payload = {
        "id": journal.id,
        "child": _child_brief(children[0]),
        "on_date": journal.on_date.isoformat(),
        "date_label": date_label(journal.on_date),
        "age_label": age_label(children[0].birth_date, journal.on_date),
        "first_lines": journal.body.split("\n")[:3],
        "cover": valid[0] if valid else None,
        "photo_count_valid": len(valid),
        "author": author,
        "can_edit": can_edit,
        "can_delete": can_delete,
        "updated_at": _stamp(journal.updated_at),
    }
    return _with_children(payload, children, journal.on_date)


def visible_journal(db: Session, *, family_id: int, journal_id: int) -> Journal | None:
    journal = db.get(Journal, journal_id)
    if journal is None or journal.family_id != family_id or journal.deleted_at is not None:
        return None
    if not journal_still_in_family(db, journal):
        return None
    return journal


def _by_key(db: Session, *, author_id: int, key: str) -> Journal | None:
    return db.scalar(
        select(Journal).where(Journal.author_id == author_id, Journal.client_request_id == key)
    )


def _apply(db: Session, journal: Journal, draft: ValidJournal) -> None:
    journal.on_date = draft.on_date
    journal.body = draft.body
    journal.updated_at = utcnow()
    replace_journal_children(db, journal, draft.children)
    _set_photos(db, journal, draft.photo_ids)


def _replay_or_spent(db: Session, ctx: MemberCtx, existing: Journal, payload: dict) -> dict:
    if existing.deleted_at is not None or existing.family_id != ctx.family.id:
        raise JournalError(409, "journal_key_spent", JOURNAL["err_deleted"])
    draft = validate_payload(db, family_id=ctx.family.id, payload=payload)
    _apply(db, existing, draft)
    db.commit()
    db.refresh(existing)
    return detail_payload(db, existing, ctx.membership, replayed=True)


def create_journal(db: Session, ctx: MemberCtx, key: str, payload: dict) -> tuple[dict, int]:
    existing = _by_key(db, author_id=ctx.user.id, key=key)
    if existing is not None:
        return _replay_or_spent(db, ctx, existing, payload), 200
    draft = validate_payload(db, family_id=ctx.family.id, payload=payload)
    now = utcnow()
    journal = Journal(
        family_id=ctx.family.id,
        author_id=ctx.user.id,
        child_id=draft.child.id,
        on_date=draft.on_date,
        body=draft.body,
        client_request_id=key,
        created_at=now,
        updated_at=now,
    )
    try:
        db.add(journal)
        db.flush()
        replace_journal_children(db, journal, draft.children)
        _set_photos(db, journal, draft.photo_ids)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _by_key(db, author_id=ctx.user.id, key=key)
        if existing is None:
            raise
        return _replay_or_spent(db, ctx, existing, payload), 200
    db.refresh(journal)
    return detail_payload(db, journal, ctx.membership, replayed=False), 201


def update_journal(db: Session, ctx: MemberCtx, journal_id: int, payload: dict, key: str | None) -> dict:
    logger.info("journal put id=%s idempotency_key=%s", journal_id, key or "")
    journal = visible_journal(db, family_id=ctx.family.id, journal_id=journal_id)
    if journal is None:
        raise not_found()
    can_edit, _can_delete = rights(ctx.membership, journal)
    if not can_edit:
        raise JournalError(403, "journal_not_author", FORBIDDEN)
    draft = validate_payload(db, family_id=ctx.family.id, payload=payload)
    try:
        _apply(db, journal, draft)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(journal)
    return detail_payload(db, journal, ctx.membership, replayed=False)


def delete_journal(db: Session, ctx: MemberCtx, journal_id: int) -> None:
    journal = visible_journal(db, family_id=ctx.family.id, journal_id=journal_id)
    if journal is None:
        raise not_found()
    _can_edit, can_delete = rights(ctx.membership, journal)
    if not can_delete:
        raise JournalError(403, "journal_not_author", FORBIDDEN)
    now = utcnow()
    journal.body = ""
    journal.deleted_at = now
    journal.updated_at = now
    db.execute(delete(JournalPhoto).where(JournalPhoto.journal_id == journal.id))
    clear_journal_children(db, journal.id)
    db.commit()


def list_journals(
    db: Session,
    ctx: MemberCtx,
    *,
    child_id: int,
    before: str | None,
    limit: int,
) -> dict:
    child = child_in_family(db, family_id=ctx.family.id, child_id=child_id)
    if child is None:
        raise not_found()
    linked = select(JournalChild.journal_id).where(JournalChild.child_id == child.id)
    unlinked = ~Journal.id.in_(select(JournalChild.journal_id))
    stmt = (
        select(Journal)
        .where(
            Journal.family_id == ctx.family.id,
            Journal.deleted_at.is_(None),
            or_(Journal.id.in_(linked), and_(unlinked, Journal.child_id == child.id)),
        )
        .order_by(Journal.on_date.desc(), Journal.id.desc())
    )
    if before:
        matched = _CURSOR.fullmatch(before)
        parsed = _parse_day(matched.group(1)) if matched else None
        if matched is None or parsed is None:
            raise JournalError(400, "cursor_invalid", "翻页参数无效")
        cursor_id = int(matched.group(2))
        stmt = stmt.where(
            or_(
                Journal.on_date < parsed,
                and_(Journal.on_date == parsed, Journal.id < cursor_id),
            )
        )
    rows = list(db.scalars(stmt.limit(limit + 1)).all())
    extra = len(rows) > limit
    page = rows[:limit]
    payload = [summary_payload(db, row, ctx.membership, child) for row in page]
    nxt = None
    if extra and page:
        last = page[-1]
        nxt = f"{last.on_date.isoformat()}:{last.id}"
    return {"journals": payload, "next": nxt}


def _card(db: Session, journal: Journal, child: Child) -> dict:
    author = _author(db, journal)
    slots = _slots(db, journal)
    valid = [slot for slot in slots if not slot["deleted"]]
    lines = journal.body.split("\n")
    rest = " ".join(line for line in lines[1:3] if line != "")
    audience = _audience(db, journal) or [child]
    return {
        "id": journal.id,
        "day": journal.on_date.day,
        "month": journal.on_date.month,
        "age_label": age_label(audience[0].birth_date, journal.on_date),
        "quote": lines[0] if lines else "",
        "rest": rest,
        "film": valid[:3],
        "more": max(0, len(valid) - 3),
        "who": display_who(author),
        "children": children_payload(audience, journal.on_date),
    }


def build_feed(db: Session, child: Child) -> dict:
    """Page timeline. Journals of a day come before that day's photos.

    Journals are newest first. Photo order stays on the existing timeline.
    """
    from app.services.videos import videos_for_feed

    base = build_timeline(db, child)
    linked = select(JournalChild.journal_id).where(JournalChild.child_id == child.id)
    unlinked = ~Journal.id.in_(select(JournalChild.journal_id))
    rows = list(
        db.scalars(
            select(Journal).where(
                Journal.family_id == child.family_id,
                Journal.deleted_at.is_(None),
                or_(Journal.id.in_(linked), and_(unlinked, Journal.child_id == child.id)),
            )
        ).all()
    )
    rows.sort(key=lambda row: (as_utc(row.created_at), row.id), reverse=True)
    by_date = {day["taken_on"]: day for day in base["days"]}
    for day in base["days"]:
        day["journals"] = []
        day["videos"] = []
    for journal in rows:
        key = journal.on_date.isoformat()
        bucket = by_date.get(key)
        if bucket is None:
            bucket = {
                "taken_on": key,
                "date_label": date_label(journal.on_date),
                "weekday": weekday_zh(journal.on_date),
                "age_label": age_label(child.birth_date, journal.on_date),
                "photos": [],
                "byline": "",
                "journals": [],
                "videos": [],
            }
            by_date[key] = bucket
        bucket["journals"].append(_card(db, journal, child))
    video_count = 0
    for key, cards in videos_for_feed(db, child).items():
        bucket = by_date.get(key)
        if bucket is None:
            on_date = date.fromisoformat(key)
            bucket = {
                "taken_on": key,
                "date_label": date_label(on_date),
                "weekday": weekday_zh(on_date),
                "age_label": age_label(child.birth_date, on_date),
                "photos": [],
                "byline": "",
                "journals": [],
                "videos": [],
            }
            by_date[key] = bucket
        bucket["videos"] = cards
        video_count += len(cards)
    days = sorted(by_date.values(), key=lambda day: day["taken_on"], reverse=True)
    base["days"] = days
    base["video_count"] = video_count
    return base


def _empty_day(on_date: date, *, age: str) -> dict:
    return {
        "taken_on": on_date.isoformat(),
        "date_label": date_label(on_date),
        "weekday": weekday_zh(on_date),
        "age_label": age,
        "photos": [],
        "byline": "",
        "journals": [],
        "videos": [],
    }


def build_family_feed(db: Session, family_id: int) -> dict:
    """One card per journal or video. Photos stay one-child records and are merged by day."""
    from app.services.videos import family_videos_for_feed

    children = list_children(db, family_id)
    by_date: dict[str, dict] = {}
    photo_count = 0
    for child in children:
        base = build_timeline(db, child)
        photo_count += int(base["child"]["photo_count"])
        for day in base["days"]:
            bucket = by_date.get(day["taken_on"])
            if bucket is None:
                bucket = _empty_day(date.fromisoformat(day["taken_on"]), age="")
                by_date[day["taken_on"]] = bucket
            bucket["photos"].extend(day["photos"])
            if day.get("byline"):
                bucket["byline"] = day["byline"] if not bucket["byline"] else bucket["byline"]
    rows = [
        row
        for row in db.scalars(
            select(Journal).where(Journal.family_id == family_id, Journal.deleted_at.is_(None))
        ).all()
        if journal_still_in_family(db, row)
    ]
    rows.sort(key=lambda row: (as_utc(row.created_at), row.id), reverse=True)
    seen: set[int] = set()
    for journal in rows:
        if journal.id in seen:
            continue
        seen.add(journal.id)
        key = journal.on_date.isoformat()
        bucket = by_date.get(key)
        if bucket is None:
            bucket = _empty_day(journal.on_date, age="")
            by_date[key] = bucket
        anchor = _audience(db, journal)[0]
        bucket["journals"].append(_card(db, journal, anchor))
    video_count = 0
    for key, cards in family_videos_for_feed(db, family_id).items():
        bucket = by_date.get(key)
        if bucket is None:
            bucket = _empty_day(date.fromisoformat(key), age="")
            by_date[key] = bucket
        bucket["videos"] = cards
        video_count += len(cards)
    days = sorted(by_date.values(), key=lambda day: day["taken_on"], reverse=True)
    return {
        "child": {
            "id": None,
            "name": "",
            "birth_date": "",
            "age_label": "",
            "photo_count": photo_count,
        },
        "days": days,
        "video_count": video_count,
        "family_view": True,
    }


def count_my_journals(db: Session, *, family_id: int, author_id: int) -> int:
    value = db.scalar(
        select(func.count())
        .select_from(Journal)
        .where(
            Journal.family_id == family_id,
            Journal.author_id == author_id,
            Journal.deleted_at.is_(None),
        )
    )
    return int(value or 0)


def _entry_name(photo: Photo) -> str:
    suffix = Path(photo.storage_path).suffix.lower()
    return f"{photo.taken_on.isoformat()}_{photo.id}{suffix}"


def export_markdown(db: Session, *, family_id: int, author_id: int) -> str | None:
    """Markdown of this author's journals. None when there is nothing to write."""
    rows = list(
        db.scalars(
            select(Journal)
            .where(
                Journal.family_id == family_id,
                Journal.author_id == author_id,
                Journal.deleted_at.is_(None),
            )
            .order_by(Journal.on_date.asc(), Journal.id.asc())
        ).all()
    )
    if not rows:
        return None
    author = db.get(User, author_id)
    author_name = author.name if author is not None and author.name else "未命名"
    wanted: set[int] = set()
    linked_ids: dict[int, list[int]] = {}
    for row in rows:
        linked = journal_child_ids(db, row)
        linked_ids[row.id] = linked
        wanted.update(linked)
    children = {
        child.id: child
        for child in db.scalars(select(Child).where(Child.id.in_(wanted))).all()
        if child.family_id == family_id
    } if wanted else {}
    link_rows = db.scalars(
        select(JournalPhoto)
        .where(JournalPhoto.journal_id.in_([row.id for row in rows]))
        .order_by(JournalPhoto.position.asc())
    ).all()
    links: dict[int, list[JournalPhoto]] = {}
    photo_ids: set[int] = set()
    for link in link_rows:
        links.setdefault(link.journal_id, []).append(link)
        photo_ids.add(link.photo_id)
    photos = {}
    if photo_ids:
        photos = {photo.id: photo for photo in db.scalars(select(Photo).where(Photo.id.in_(photo_ids))).all()}
    parts = ["# 成长日志", ""]
    for row in rows:
        linked = linked_ids.get(row.id) or [row.child_id]
        child_names = [
            children[child_id].name if child_id in children else CHILD_GONE for child_id in linked
        ]
        child_line = child_names[0] if len(child_names) == 1 else "、".join(child_names)
        names: list[str] = []
        for link in links.get(row.id, []):
            photo = photos.get(link.photo_id)
            if photo is None or photo.family_id != family_id:
                names.append(PHOTO_GONE)
            else:
                names.append(_entry_name(photo))
        ref_line = "、".join(names) if names else "无"
        parts.append(f"## {row.on_date.isoformat()}")
        parts.append(f"作者：{author_name}")
        parts.append(f"日期：{row.on_date.isoformat()}")
        parts.append(f"孩子：{child_line}")
        if len(linked) > 1:
            parts.append("child_ids：" + ",".join(str(child_id) for child_id in linked))
        parts.append(f"引用：{ref_line}")
        parts.append("")
        parts.append(row.body)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def picker_page(db: Session, *, family_id: int, before: str | None, limit: int = PICKER_PAGE) -> dict:
    stmt = (
        select(Photo)
        .where(Photo.family_id == family_id)
        .order_by(Photo.taken_on.desc(), Photo.id.desc())
    )
    if before:
        matched = _CURSOR.fullmatch(before)
        parsed = _parse_day(matched.group(1)) if matched else None
        if matched is None or parsed is None:
            raise JournalError(400, "cursor_invalid", "翻页参数无效")
        cursor_id = int(matched.group(2))
        stmt = stmt.where(
            or_(
                Photo.taken_on < parsed,
                and_(Photo.taken_on == parsed, Photo.id < cursor_id),
            )
        )
    rows = list(db.scalars(stmt.limit(limit + 1)).all())
    extra = len(rows) > limit
    page = rows[:limit]
    days: list[dict] = []
    current: dict | None = None
    for photo in page:
        key = photo.taken_on.isoformat()
        if current is None or current["taken_on"] != key:
            current = {"taken_on": key, "date_label": date_label(photo.taken_on), "photos": []}
            days.append(current)
        current["photos"].append({"id": photo.id, "media_url": f"/media/{photo.id}"})
    nxt = None
    if extra and page:
        last = page[-1]
        nxt = f"{last.taken_on.isoformat()}:{last.id}"
    return {"days": days, "next": nxt}
