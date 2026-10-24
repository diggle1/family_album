"""Shared child-set checks and association rows for journals and videos.

The primary ``child_id`` on the parent row is always ``child_ids[0]`` after
de-duplication, in request order. It is not the smallest id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Child, Journal, JournalChild, Video, VideoChild
from app.services.children import child_in_family, list_children
from app.services.clock import age_label


@dataclass(frozen=True)
class ResolvedChildren:
    children: list[Child]

    @property
    def primary(self) -> Child:
        return self.children[0]


class ChildSetError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _as_json_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def parse_child_id_list(raw: object) -> list[int] | None:
    """Ordered, de-duplicated ids. None means the value is not a usable list."""
    if not isinstance(raw, list):
        return None
    seen: set[int] = set()
    ordered: list[int] = []
    for item in raw:
        parsed = _as_json_int(item)
        if parsed is None:
            return None
        if parsed in seen:
            continue
        seen.add(parsed)
        ordered.append(parsed)
    if not ordered:
        return None
    return ordered


def parse_form_child_ids(raw: list[str] | None) -> list[int] | None:
    """Repeated form fields. None means missing or not a usable list of ints."""
    if raw is None:
        return None
    seen: set[int] = set()
    ordered: list[int] = []
    for item in raw:
        text = str(item).strip()
        if not text.isdigit():
            return None
        number = int(text)
        if number in seen:
            continue
        seen.add(number)
        ordered.append(number)
    if not ordered:
        return None
    return ordered


def resolve_children(
    db: Session,
    *,
    family_id: int,
    child_ids: list[int] | None,
    legacy_child_id: int | None,
    used_list: bool,
    required_detail: str,
    missing_detail: str,
    birth_detail: str,
    on_date: date | None = None,
) -> ResolvedChildren:
    """Validate a child set. ``on_date`` is checked against the latest birth.

    ``used_list`` selects the detail strings. The caller still owns date format
    checks that happen before or after this, and passes ``on_date`` only once
    the calendar day is known.
    """
    if used_list:
        ids = child_ids
    else:
        ids = [legacy_child_id] if legacy_child_id is not None else None
    if not ids:
        raise ChildSetError("child_required", required_detail)
    family = list_children(db, family_id)
    if len(ids) > len(family):
        raise ChildSetError("children_too_many", missing_detail)
    chosen: list[Child] = []
    for child_id in ids:
        child = child_in_family(db, family_id=family_id, child_id=child_id)
        if child is None:
            raise ChildSetError("child_not_found", missing_detail)
        chosen.append(child)
    if on_date is not None:
        latest = chosen[0]
        for child in chosen[1:]:
            if child.birth_date > latest.birth_date:
                latest = child
        if on_date < latest.birth_date:
            raise ChildSetError("date_before_birth", birth_detail.format(name=latest.name))
    return ResolvedChildren(children=chosen)


def _rows_for_journal(db: Session, journal_id: int) -> list[JournalChild]:
    return list(
        db.scalars(
            select(JournalChild)
            .where(JournalChild.journal_id == journal_id)
            .order_by(JournalChild.position.asc(), JournalChild.child_id.asc())
        ).all()
    )


def _rows_for_video(db: Session, video_id: int) -> list[VideoChild]:
    return list(
        db.scalars(
            select(VideoChild)
            .where(VideoChild.video_id == video_id)
            .order_by(VideoChild.position.asc(), VideoChild.child_id.asc())
        ).all()
    )


def journal_child_ids(db: Session, journal: Journal) -> list[int]:
    rows = _rows_for_journal(db, journal.id)
    if rows:
        return [row.child_id for row in rows]
    return [journal.child_id]


def video_child_ids(db: Session, video: Video) -> list[int]:
    rows = _rows_for_video(db, video.id)
    if rows:
        return [row.child_id for row in rows]
    return [video.child_id]


def linked_children(db: Session, *, family_id: int, child_ids: list[int]) -> list[Child]:
    """In-family children in association order. Missing rows are skipped."""
    found: list[Child] = []
    for child_id in child_ids:
        child = child_in_family(db, family_id=family_id, child_id=child_id)
        if child is not None:
            found.append(child)
    return found


def journal_has_child(db: Session, journal: Journal, child_id: int) -> bool:
    return child_id in journal_child_ids(db, journal)


def video_has_child(db: Session, video: Video, child_id: int) -> bool:
    return child_id in video_child_ids(db, video)


def journal_still_in_family(db: Session, journal: Journal) -> bool:
    return bool(linked_children(db, family_id=journal.family_id, child_ids=journal_child_ids(db, journal)))


def video_still_in_family(db: Session, video: Video) -> bool:
    return bool(linked_children(db, family_id=video.family_id, child_ids=video_child_ids(db, video)))


def replace_journal_children(db: Session, journal: Journal, children: list[Child]) -> None:
    db.execute(delete(JournalChild).where(JournalChild.journal_id == journal.id))
    db.flush()
    for index, child in enumerate(children):
        db.add(JournalChild(journal_id=journal.id, child_id=child.id, position=index))
    journal.child_id = children[0].id


def replace_video_children(db: Session, video: Video, children: list[Child]) -> None:
    db.execute(delete(VideoChild).where(VideoChild.video_id == video.id))
    db.flush()
    for index, child in enumerate(children):
        db.add(VideoChild(video_id=video.id, child_id=child.id, position=index))
    video.child_id = children[0].id


def clear_journal_children(db: Session, journal_id: int) -> None:
    db.execute(delete(JournalChild).where(JournalChild.journal_id == journal_id))


def clear_video_children(db: Session, video_id: int) -> None:
    db.execute(delete(VideoChild).where(VideoChild.video_id == video_id))


def children_payload(children: list[Child], on_date: date) -> list[dict]:
    return [
        {
            "id": child.id,
            "name": child.name,
            "age_label": age_label(child.birth_date, on_date),
        }
        for child in children
    ]


def ids_of(children: list[Child]) -> list[int]:
    return [child.id for child in children]
