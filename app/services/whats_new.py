"""What's new on this family's timeline.

The window filters on created_at. Days are grouped by the same record date
the day page already uses: photo taken_on, journal on_date, video on_date.
Asia/Shanghai is the calendar of those date columns. Reading does not move
last_seen.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Journal, Photo, TimelineLastSeen, Video
from app.services.assoc import (
    journal_child_ids,
    linked_children,
    photo_child_ids,
    video_child_ids,
)
from app.services.clock import as_utc, isoformat, utcnow
from app.whats_new_copy import ELDERS_WHATS_NEW

_WEEK = "一二三四五六日"
_FALLBACK_DAYS = 7
_CHIP_ORDER = (
    ("photo_count", "ph", "chip_photo"),
    ("journal_count", "jn", "chip_journal"),
    ("video_count", "vd", "chip_video"),
)


class TimelineSeenUnavailable(Exception):
    """The seen write could not be stored. The timeline itself still opens."""


def _fill(template: str, **values: object) -> str:
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def _row(db: Session, user_id: int, family_id: int) -> TimelineLastSeen | None:
    return db.scalar(
        select(TimelineLastSeen).where(
            TimelineLastSeen.user_id == user_id,
            TimelineLastSeen.family_id == family_id,
        )
    )


def _window(db: Session, *, user_id: int, family_id: int, now: datetime) -> tuple[datetime, str, str | None]:
    row = _row(db, user_id, family_id)
    seen = as_utc(row.last_seen_at) if row is not None and row.last_seen_at is not None else None
    if seen is not None and seen <= now:
        return seen, "since_seen", isoformat(seen)
    return now - timedelta(days=_FALLBACK_DAYS), "fallback_7d", None


def _in_window(created_at: datetime, since: datetime, now: datetime) -> bool:
    stamp = as_utc(created_at)
    return since < stamp <= now


def _live_child_ids(db: Session, family_id: int, child_ids: list[int]) -> list[int]:
    return [child.id for child in linked_children(db, family_id=family_id, child_ids=child_ids)]


def _collect(db: Session, *, family_id: int, since: datetime, now: datetime) -> list[dict]:
    buckets: dict[date, dict] = defaultdict(
        lambda: {"photo_count": 0, "journal_count": 0, "video_count": 0, "child_ids": set()}
    )

    photos = db.scalars(select(Photo).where(Photo.family_id == family_id)).all()
    for photo in photos:
        if not _in_window(photo.created_at, since, now):
            continue
        if photo.kind == "self":
            buckets[photo.taken_on]["photo_count"] += 1
            continue
        child_ids = _live_child_ids(db, family_id, photo_child_ids(db, photo))
        if not child_ids:
            continue
        bucket = buckets[photo.taken_on]
        bucket["photo_count"] += 1
        bucket["child_ids"].update(child_ids)

    journals = db.scalars(
        select(Journal).where(Journal.family_id == family_id, Journal.deleted_at.is_(None))
    ).all()
    for journal in journals:
        if not _in_window(journal.created_at, since, now):
            continue
        child_ids = _live_child_ids(db, family_id, journal_child_ids(db, journal))
        if not child_ids:
            continue
        bucket = buckets[journal.on_date]
        bucket["journal_count"] += 1
        bucket["child_ids"].update(child_ids)

    videos = db.scalars(
        select(Video).where(Video.family_id == family_id, Video.deleted_at.is_(None))
    ).all()
    for video in videos:
        if not _in_window(video.created_at, since, now):
            continue
        child_ids = _live_child_ids(db, family_id, video_child_ids(db, video))
        if not child_ids:
            continue
        bucket = buckets[video.on_date]
        bucket["video_count"] += 1
        bucket["child_ids"].update(child_ids)

    days: list[dict] = []
    for record_day in sorted(buckets, reverse=True):
        bucket = buckets[record_day]
        total = bucket["photo_count"] + bucket["journal_count"] + bucket["video_count"]
        if total <= 0:
            continue
        days.append(
            {
                "date": record_day,
                "photo_count": bucket["photo_count"],
                "journal_count": bucket["journal_count"],
                "video_count": bucket["video_count"],
                "total": total,
                "child_ids": sorted(bucket["child_ids"]),
            }
        )
    return days


def public_whats_new(db: Session, *, user_id: int, family_id: int, now: datetime | None = None) -> dict:
    moment = as_utc(now or utcnow())
    since, window, seen_at = _window(db, user_id=user_id, family_id=family_id, now=moment)
    days = _collect(db, family_id=family_id, since=since, now=moment)
    public_days = [
        {
            "date": item["date"].isoformat(),
            "photo_count": item["photo_count"],
            "journal_count": item["journal_count"],
            "video_count": item["video_count"],
            "total": item["total"],
        }
        for item in days
    ]
    return {
        "has_updates": bool(public_days),
        "window": window,
        "last_seen_at": seen_at,
        "days": public_days,
    }


def _corner(window: str, day_count: int) -> str:
    if window == "fallback_7d":
        return ELDERS_WHATS_NEW["slip_sub_7d"]
    if day_count > 0:
        return _fill(ELDERS_WHATS_NEW["slip_sub_days"], n=day_count)
    return ELDERS_WHATS_NEW["slip_sub_peek"]


def _href(day: date, child_ids: list[int], prefer_child_id: int | None, fallback_child_id: int | None) -> str:
    chosen = None
    if prefer_child_id is not None and prefer_child_id in child_ids:
        chosen = prefer_child_id
    elif child_ids:
        chosen = child_ids[0]
    else:
        chosen = fallback_child_id
    if chosen is None:
        return ""
    return f"/children/{chosen}/days/{day.isoformat()}"


def build_slip(
    db: Session,
    *,
    user_id: int,
    family_id: int,
    prefer_child_id: int | None,
    fallback_child_id: int | None,
    now: datetime | None = None,
) -> dict:
    """Template view. Empty days mean the strip is not rendered."""
    moment = as_utc(now or utcnow())
    since, window, seen_at = _window(db, user_id=user_id, family_id=family_id, now=moment)
    days = _collect(db, family_id=family_id, since=since, now=moment)
    rows = []
    for item in days:
        record_day = item["date"]
        weekday = _WEEK[record_day.weekday()]
        chips = []
        for count_key, kind, copy_key in _CHIP_ORDER:
            if item[count_key] > 0:
                chips.append({"kind": kind, "label": ELDERS_WHATS_NEW[copy_key]})
        rows.append(
            {
                "date": record_day.isoformat(),
                "day": record_day.day,
                "cal_m": _fill(ELDERS_WHATS_NEW["row_cal_m"], mo=record_day.month, w=weekday),
                "title": _fill(
                    ELDERS_WHATS_NEW["row_date_w"],
                    mo=record_day.month,
                    d=record_day.day,
                    w=weekday,
                ),
                "total": _fill(ELDERS_WHATS_NEW["row_total"], n=item["total"]),
                "href": _href(record_day, item["child_ids"], prefer_child_id, fallback_child_id),
                "chips": chips,
            }
        )
    return {
        "has_updates": bool(rows),
        "window": window,
        "last_seen_at": seen_at,
        "corner": _corner(window, len(rows)),
        "rows": rows,
    }


def _advance(row: TimelineLastSeen, moment: datetime) -> None:
    current = as_utc(row.last_seen_at) if row.last_seen_at is not None else None
    if current is None or moment >= current:
        row.last_seen_at = moment


def mark_timeline_seen(db: Session, *, user_id: int, family_id: int, now: datetime | None = None) -> datetime:
    """Upsert this member's row. Repeated calls stay at or after the previous value."""
    moment = as_utc(now or utcnow())
    row = _row(db, user_id, family_id)
    if row is None:
        row = TimelineLastSeen(user_id=user_id, family_id=family_id, last_seen_at=moment)
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = _row(db, user_id, family_id)
            if row is None:
                raise
            _advance(row, moment)
            db.commit()
    else:
        _advance(row, moment)
        db.commit()
    db.refresh(row)
    return as_utc(row.last_seen_at)
