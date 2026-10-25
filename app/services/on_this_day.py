"""Last year's same calendar day for this family's timeline.

The match key is the record date already used by the day page: photo
taken_on, journal on_date, video on_date. Asia/Shanghai decides which
day "today" is. February 29 has no previous-year date, so the card is
absent. Reading does not move the member's seen mark.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.models import Child
from app.on_this_day_copy import ON_THIS_DAY
from app.services.children import list_children
from app.services.clock import shanghai_today
from app.services.journals import build_family_feed, build_feed

logger = logging.getLogger("family_album.on_this_day")

_CHIP_ORDER = (
    ("photo_count", "ph", "chip_photo"),
    ("journal_count", "jn", "chip_journal"),
    ("video_count", "vd", "chip_video"),
)


class OnThisDayUnavailable(Exception):
    """The year card could not be read. The timeline itself still opens."""


def _fill(template: str, **values: object) -> str:
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def previous_year_date(today: date) -> date | None:
    """Same month and day one year earlier. February 29 returns None."""
    try:
        return date(today.year - 1, today.month, today.day)
    except ValueError:
        return None


def blank_on_this_day(target: date | None = None) -> dict:
    return {
        "has_on_this_day": False,
        "target_date": None if target is None else target.isoformat(),
        "year": None if target is None else target.year,
        "photo_count": 0,
        "journal_count": 0,
        "video_count": 0,
        "total": 0,
        "thumb_urls": [],
        "day_path": None,
    }


def _child_ids(day: dict) -> list[int]:
    found: list[int] = []
    for photo in day.get("photos") or []:
        found.extend(photo.get("child_ids") or [])
    for journal in day.get("journals") or []:
        found.extend(child.get("id") for child in journal.get("children") or [] if child.get("id"))
    for video in day.get("videos") or []:
        found.extend(child.get("id") for child in video.get("children") or [] if child.get("id"))
    return found


def on_this_day_for_feed(
    feed: dict,
    *,
    prefer_child_id: int | None,
    fallback_child_id: int | None,
    today: date | None = None,
) -> dict:
    """Same counts the timeline already shows for that record day."""
    moment = today or shanghai_today()
    target = previous_year_date(moment)
    if target is None:
        return blank_on_this_day(None)
    key = target.isoformat()
    day = next((item for item in feed.get("days") or [] if item.get("taken_on") == key), None)
    if day is None:
        return blank_on_this_day(target)
    photos = list(day.get("photos") or [])
    journals = list(day.get("journals") or [])
    videos = list(day.get("videos") or [])
    photo_count = len(photos)
    journal_count = len(journals)
    video_count = len(videos)
    total = photo_count + journal_count + video_count
    if total <= 0:
        return blank_on_this_day(target)
    thumbs = [photo["media_url"] for photo in photos[:4] if photo.get("media_url")]
    anchor = prefer_child_id
    if anchor is None:
        ids = _child_ids(day)
        anchor = sorted(set(ids))[0] if ids else fallback_child_id
    day_path = f"/children/{anchor}/days/{key}" if anchor is not None else None
    return {
        "has_on_this_day": total > 0,
        "target_date": key,
        "year": target.year,
        "photo_count": photo_count,
        "journal_count": journal_count,
        "video_count": video_count,
        "total": total,
        "thumb_urls": thumbs,
        "day_path": day_path,
    }


def public_on_this_day(
    db: Session,
    *,
    family_id: int,
    child: Child | None,
    today: date | None = None,
) -> dict:
    """Read-only payload. child None is the whole family, matching the family feed."""
    if child is not None:
        feed = build_feed(db, child)
        return on_this_day_for_feed(
            feed,
            prefer_child_id=child.id,
            fallback_child_id=child.id,
            today=today,
        )
    feed = build_family_feed(db, family_id)
    kids = list_children(db, family_id)
    fallback = kids[0].id if kids else None
    return on_this_day_for_feed(
        feed,
        prefer_child_id=None,
        fallback_child_id=fallback,
        today=today,
    )


def card_view(payload: dict | None) -> dict | None:
    """Template card. None means the year card is not rendered."""
    if not payload or not payload.get("has_on_this_day") or not payload.get("day_path"):
        return None
    target = date.fromisoformat(payload["target_date"])
    chips = []
    for count_key, kind, copy_key in _CHIP_ORDER:
        count = int(payload.get(count_key) or 0)
        if count <= 0:
            continue
        label = ON_THIS_DAY[copy_key]
        chips.append(
            {
                "kind": kind,
                "text": _fill(ON_THIS_DAY["chip_count"], label=label, n=count),
            }
        )
    thumbs = list(payload.get("thumb_urls") or [])
    return {
        "year": payload["year"],
        "title": ON_THIS_DAY["amber_title"],
        "sub": _fill(ON_THIS_DAY["amber_sub"], mo=target.month, d=target.day, y=payload["year"]),
        "stamp_lab": ON_THIS_DAY["amber_stamp_lab"],
        "href": payload["day_path"],
        "chips": chips,
        "thumbs": thumbs,
        "thumb_count": len(thumbs),
    }


def year_card(
    feed: dict,
    *,
    prefer_child_id: int | None,
    fallback_child_id: int | None,
    today: date | None = None,
) -> dict | None:
    """Page card. A read failure leaves the timeline up and omits the card."""
    try:
        payload = on_this_day_for_feed(
            feed,
            prefer_child_id=prefer_child_id,
            fallback_child_id=fallback_child_id,
            today=today,
        )
    except OnThisDayUnavailable:
        logger.warning("on-this-day skipped")
        payload = blank_on_this_day()
    except Exception as exc:
        logger.warning("on-this-day skipped error=%s", type(exc).__name__)
        payload = blank_on_this_day()
    return card_view(payload)


def year_meta_for(day: date, today: date | None = None) -> str | None:
    """Subtitle for the existing day page when that page is last year's same day."""
    moment = today or shanghai_today()
    target = previous_year_date(moment)
    if target is None or day != target:
        return None
    return _fill(ON_THIS_DAY["day_year_meta"], y=target.year)
