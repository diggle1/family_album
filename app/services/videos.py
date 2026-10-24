"""Short family videos. Bytes are streamed to disk; nothing is held as one blob.

Validation returns the first failure only, in the tech order. Idempotency is
the unique (uploader, key) row, not a process lock. A replay returns the first
success and does not replace the file.
"""

from __future__ import annotations

import logging
import os
import stat
import time
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import FORBIDDEN, ROLE_RANK, VIDEO
from app.deps import MemberCtx
from app.models import Child, Membership, User, Video
from app.services.children import child_in_family
from app.services.clock import age_label, as_utc, date_label, isoformat, shanghai_today, utcnow
from app.services.photos import sniff_image
from app.storage.errors import StorageError
from app.storage.factory import current_store, store_for

logger = logging.getLogger("family_album.video")

VIDEO_MAX_BYTES = 100 * 1024 * 1024
VIDEO_MAX_DURATION_MS = 60_000
POSTER_MAX_BYTES = 2 * 1024 * 1024
PARTIAL_MAX_AGE_S = 30 * 60
PARTIAL_PREFIX = ".up-"
PARTIAL_SUFFIX = ".partial"

_MP4_BRANDS = {
    b"isom",
    b"iso2",
    b"mp41",
    b"mp42",
    b"avc1",
    b"dash",
    b"iso5",
    b"iso6",
    b"mp71",
    b"MSNV",
    b"M4V ",
    b"M4A ",
    b"ndas",
    b"ndsc",
    b"ndsh",
    b"ndsm",
    b"ndsp",
    b"ndss",
    b"ndxc",
    b"ndxh",
    b"ndxm",
    b"ndxp",
    b"ndxs",
}
_MOV_BRANDS = {b"qt  ", b"isom", b"iso2", b"mp41", b"mp42", b"avc1"}
_MOV_CLASSIC = {b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot", b"junk"}
_POSTER_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_CONTAINER_MIME = {"mp4": "video/mp4", "mov": "video/quicktime"}


class VideoError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        self.status = status
        self.code = code
        self.detail = detail
        super().__init__(detail)


def not_found() -> VideoError:
    return VideoError(404, "video_not_found", VIDEO["not_found"])


def partial_max_age_s() -> float:
    raw = os.environ.get("VIDEO_PARTIAL_MAX_AGE_S", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            logger.warning("video partial age env ignored")
    return float(PARTIAL_MAX_AGE_S)


def new_partial_path() -> Path:
    root = get_settings().media_root
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError("VOL_MISSING", "媒资卷没挂上") from exc
    return root / f"{PARTIAL_PREFIX}{uuid.uuid4().hex}{PARTIAL_SUFFIX}"


def unlink_path(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("video partial delete failed")


def sweep_video_partials() -> int:
    """Delete old video upload partials. Photos and finished objects are left alone."""
    root = get_settings().media_root
    if not root.is_dir():
        return 0
    cutoff = time.time() - partial_max_age_s()
    removed = 0
    try:
        names = list(root.iterdir())
    except OSError:
        logger.warning("video partial sweep failed")
        return 0
    for path in names:
        name = path.name
        if not (name.startswith(PARTIAL_PREFIX) and name.endswith(PARTIAL_SUFFIX)):
            continue
        try:
            info = path.lstat()
        except OSError:
            logger.warning("video partial sweep failed")
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            continue
        if info.st_mtime >= cutoff:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            logger.warning("video partial sweep failed")
    # Flat temp keys that a store may have published before the final name.
    for path in names:
        name = path.name
        if not (name.startswith("vup-") and name.endswith(PARTIAL_SUFFIX)):
            continue
        try:
            info = path.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            continue
        if info.st_mtime >= cutoff:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            logger.warning("video partial sweep failed")
    logger.info("video partial sweep removed=%s", removed)
    return removed


def _brands(header: bytes) -> set[bytes]:
    if len(header) < 16 or header[4:8] != b"ftyp":
        return set()
    size = int.from_bytes(header[0:4], "big")
    if size < 16:
        return set()
    end = min(len(header), size)
    found = {header[8:12]}
    pos = 16
    while pos + 4 <= end:
        found.add(header[pos : pos + 4])
        pos += 4
    return {item for item in found if item.strip(b"\x00")}


def sniff_container(header: bytes, suffix: str) -> str | None:
    """Return 'mp4' or 'mov', or None when the file is not on the whitelist."""
    suffix = (suffix or "").lower()
    if suffix not in {".mp4", ".mov"}:
        return None
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return None
    if len(header) >= 12 and header[0:4] == b"RIFF" and header[8:12] in {b"AVI ", b"WAVE"}:
        return None
    brands = _brands(header)
    if brands:
        major = header[8:12]
        if suffix == ".mp4" and major in _MP4_BRANDS and major != b"qt  ":
            return "mp4"
        if suffix == ".mov" and (major in _MOV_BRANDS or brands & _MOV_BRANDS):
            return "mov"
        return None
    if suffix == ".mov" and len(header) >= 8 and header[4:8] in _MOV_CLASSIC:
        return "mov"
    return None


def _read_mvhd(handle, start: int, end: int) -> int | None:
    handle.seek(start)
    data = handle.read(min(40, max(0, end - start)))
    if len(data) < 20:
        return None
    version = data[0]
    if version == 0:
        timescale = int.from_bytes(data[12:16], "big")
        duration = int.from_bytes(data[16:20], "big")
    elif version == 1:
        if len(data) < 32:
            return None
        timescale = int.from_bytes(data[20:24], "big")
        duration = int.from_bytes(data[24:32], "big")
    else:
        return None
    if timescale <= 0:
        return None
    return duration * 1000 // timescale


def _scan_mvhd(handle, start: int, end: int) -> int | None:
    pos = start
    while pos + 8 <= end:
        handle.seek(pos)
        header = handle.read(16)
        if len(header) < 8:
            return None
        size = int.from_bytes(header[0:4], "big")
        box_type = header[4:8]
        header_len = 8
        if size == 1:
            if len(header) < 16:
                return None
            size = int.from_bytes(header[8:16], "big")
            header_len = 16
        elif size == 0:
            size = end - pos
        if size < header_len:
            return None
        body_start = pos + header_len
        body_end = min(end, pos + size)
        if box_type == b"mvhd":
            return _read_mvhd(handle, body_start, body_end)
        if box_type == b"moov":
            found = _scan_mvhd(handle, body_start, body_end)
            if found is not None:
                return found
        pos += size
        if size == 0:
            break
    return None


def container_duration_ms(path: Path) -> int | None:
    """Movie header duration in milliseconds, or None when the box is absent."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            return _scan_mvhd(handle, 0, size)
    except OSError:
        logger.warning("video duration scan failed")
        return None


def _header(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(4096)


def duration_label(duration_ms: int) -> str:
    seconds = max(0, int(duration_ms) // 1000)
    return VIDEO["card_dur"].format(m=seconds // 60, ss=f"{seconds % 60:02d}")


def _stamp(value) -> str | None:
    text = isoformat(value)
    if text and text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


def _author(db: Session, video: Video) -> dict:
    user = db.get(User, video.uploader_id)
    name = user.name if user is not None and user.name else "未命名"
    membership = db.scalar(select(Membership).where(Membership.user_id == video.uploader_id))
    former = membership is None or membership.family_id != video.family_id
    return {"label": name, "former": former}


def display_who(author: dict) -> str:
    if author["former"]:
        return VIDEO["former"].format(who=author["label"])
    return author["label"]


def can_delete(viewer: Membership, video: Video) -> bool:
    if viewer.role == "admin":
        return True
    rank = ROLE_RANK[viewer.role]
    return rank >= ROLE_RANK["uploader"] and video.uploader_id == viewer.user_id


def visible_video(db: Session, *, family_id: int, video_id: int) -> Video | None:
    video = db.get(Video, video_id)
    if video is None or video.family_id != family_id or video.deleted_at is not None:
        return None
    if child_in_family(db, family_id=family_id, child_id=video.child_id) is None:
        return None
    return video


def _by_key(db: Session, *, uploader_id: int, key: str) -> Video | None:
    return db.scalar(
        select(Video).where(Video.uploader_id == uploader_id, Video.client_request_id == key)
    )


@dataclass(frozen=True)
class ValidVideo:
    child: Child
    on_date: date
    duration_ms: int
    container: str
    byte_size: int
    poster_media: str | None
    poster_ext: str | None


def _parse_duration(raw: object) -> int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    text = str(raw).strip()
    if text.startswith("-"):
        digits = text[1:]
        if not digits.isdigit():
            return None
        return -int(digits)
    if not text.isdigit():
        return None
    return int(text)


def _parse_day(raw: object) -> date | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def validate_upload(
    db: Session,
    *,
    family_id: int,
    child_raw: object,
    on_date_raw: object,
    duration_raw: object,
    video_path: Path,
    filename: str,
    poster_path: Path | None,
    poster_too_big: bool,
) -> ValidVideo:
    """First failure only. Size is enforced again in case the caller streamed a capped file."""
    try:
        byte_size = video_path.stat().st_size
    except OSError as exc:
        raise VideoError(400, "video_empty", VIDEO["err_format"]) from exc
    if byte_size <= 0:
        raise VideoError(400, "video_empty", VIDEO["err_format"])
    if byte_size > VIDEO_MAX_BYTES:
        raise VideoError(400, "video_size", VIDEO["err_limit_size"])
    suffix = Path(filename or "").suffix.lower()
    try:
        header = _header(video_path)
    except OSError as exc:
        raise VideoError(400, "video_format", VIDEO["err_format"]) from exc
    container = sniff_container(header, suffix)
    if container is None:
        raise VideoError(400, "video_format", VIDEO["err_format"])
    duration = _parse_duration(duration_raw)
    if duration is None or duration <= 0:
        raise VideoError(400, "video_duration_invalid", VIDEO["err_limit_dur"])
    parsed = container_duration_ms(video_path)
    if duration > VIDEO_MAX_DURATION_MS or (parsed is not None and parsed > VIDEO_MAX_DURATION_MS):
        raise VideoError(400, "video_duration", VIDEO["err_limit_dur"])
    if child_raw is None or (isinstance(child_raw, str) and child_raw.strip() == ""):
        raise VideoError(400, "child_required", VIDEO["err_no_child"])
    child_text = str(child_raw).strip()
    child_id = int(child_text) if child_text.isdigit() else None
    child = child_in_family(db, family_id=family_id, child_id=child_id) if child_id is not None else None
    if child is None:
        raise VideoError(400, "child_not_found", VIDEO["err_no_child"])
    on_date = _parse_day(on_date_raw if isinstance(on_date_raw, str) else None)
    if on_date is None:
        raise VideoError(400, "date_invalid", VIDEO["err_date_future"])
    if on_date > shanghai_today():
        raise VideoError(400, "date_future", VIDEO["err_date_future"])
    if on_date < child.birth_date:
        raise VideoError(400, "date_before_birth", VIDEO["err_date_birth"].format(name=child.name))
    poster_media = None
    poster_ext = None
    if poster_too_big:
        raise VideoError(400, "poster_invalid", VIDEO["err_format"])
    if poster_path is not None:
        try:
            with poster_path.open("rb") as handle:
                sniffed = sniff_image(handle.read(32))
        except OSError as exc:
            raise VideoError(400, "poster_invalid", VIDEO["err_format"]) from exc
        if sniffed is None or poster_path.stat().st_size > POSTER_MAX_BYTES:
            raise VideoError(400, "poster_invalid", VIDEO["err_format"])
        poster_media = sniffed
        poster_ext = _POSTER_EXT[sniffed]
    return ValidVideo(
        child=child,
        on_date=on_date,
        duration_ms=duration,
        container=container,
        byte_size=byte_size,
        poster_media=poster_media,
        poster_ext=poster_ext,
    )


def detail_payload(db: Session, video: Video, viewer: Membership, *, replayed: bool = False) -> dict:
    child = db.get(Child, video.child_id)
    if child is None or child.family_id != video.family_id:
        raise not_found()
    author = _author(db, video)
    has_poster = bool(video.poster_path)
    return {
        "id": video.id,
        "child": {"id": child.id, "name": child.name},
        "on_date": video.on_date.isoformat(),
        "date_label": date_label(video.on_date),
        "age_label": age_label(child.birth_date, video.on_date),
        "duration_ms": video.duration_ms,
        "duration_label": duration_label(video.duration_ms),
        "byte_size": video.byte_size,
        "author": author,
        "media_url": f"/media/videos/{video.id}",
        "poster_url": f"/media/videos/{video.id}/poster" if has_poster else None,
        "has_poster": has_poster,
        "can_delete": can_delete(viewer, video),
        "created_at": _stamp(video.created_at),
        "replayed": replayed,
    }


def peek_replay(db: Session, ctx: MemberCtx, key: str) -> tuple[dict, int] | None:
    existing = _by_key(db, uploader_id=ctx.user.id, key=key)
    if existing is None:
        return None
    if existing.deleted_at is not None or existing.family_id != ctx.family.id:
        raise VideoError(409, "video_key_spent", VIDEO["err_retry"])
    return detail_payload(db, existing, ctx.membership, replayed=True), 200


def _safe_delete(backend: str, key: str | None) -> None:
    if not key:
        return
    try:
        store_for(backend).delete(key)
    except StorageError:
        logger.warning("video object delete failed")
    except Exception:
        logger.warning("video object delete failed")


def create_video(
    db: Session,
    ctx: MemberCtx,
    key: str,
    *,
    child_raw: object,
    on_date_raw: object,
    duration_raw: object,
    video_path: Path,
    filename: str,
    poster_path: Path | None,
    poster_too_big: bool,
) -> tuple[dict, int]:
    replay = peek_replay(db, ctx, key)
    if replay is not None:
        return replay
    draft = validate_upload(
        db,
        family_id=ctx.family.id,
        child_raw=child_raw,
        on_date_raw=on_date_raw,
        duration_raw=duration_raw,
        video_path=video_path,
        filename=filename,
        poster_path=poster_path,
        poster_too_big=poster_too_big,
    )
    settings = get_settings()
    backend = settings.storage_backend if settings.storage_backend in {"local", "s3"} else "local"
    store = current_store()
    final_key = f"{uuid.uuid4().hex}.{draft.container}"
    poster_key = f"{uuid.uuid4().hex}{draft.poster_ext}" if draft.poster_ext else None
    stored_final = False
    stored_poster = False
    try:
        store.put_file(final_key, video_path, _CONTAINER_MIME[draft.container])
        stored_final = True
        if poster_key and poster_path is not None and draft.poster_media:
            store.put_file(poster_key, poster_path, draft.poster_media)
            stored_poster = True
        if os.environ.get("VIDEO_FAIL_BEFORE_COMMIT") == "1":
            raise RuntimeError("video fail before commit")
        video = Video(
            family_id=ctx.family.id,
            uploader_id=ctx.user.id,
            child_id=draft.child.id,
            on_date=draft.on_date,
            duration_ms=draft.duration_ms,
            byte_size=draft.byte_size,
            container=draft.container,
            storage_path=final_key,
            poster_path=poster_key if stored_poster else None,
            storage_backend=backend,
            client_request_id=key,
            created_at=utcnow(),
        )
        db.add(video)
        db.commit()
    except IntegrityError:
        db.rollback()
        if stored_final:
            _safe_delete(backend, final_key)
        if stored_poster:
            _safe_delete(backend, poster_key)
        replay = peek_replay(db, ctx, key)
        if replay is None:
            raise
        return replay
    except VideoError:
        raise
    except Exception:
        db.rollback()
        if stored_final:
            _safe_delete(backend, final_key)
        if stored_poster:
            _safe_delete(backend, poster_key)
        raise
    db.refresh(video)
    return detail_payload(db, video, ctx.membership, replayed=False), 201


def delete_video(db: Session, ctx: MemberCtx, video_id: int) -> None:
    video = visible_video(db, family_id=ctx.family.id, video_id=video_id)
    if video is None:
        raise not_found()
    if not can_delete(ctx.membership, video):
        raise VideoError(403, "video_not_author", FORBIDDEN)
    backend = video.storage_backend or "local"
    storage_path = video.storage_path
    poster_path = video.poster_path
    _safe_delete(backend, storage_path)
    _safe_delete(backend, poster_path)
    video.storage_path = ""
    video.poster_path = None
    video.byte_size = 0
    video.deleted_at = utcnow()
    db.commit()


def blob_exists(video: Video) -> bool:
    if not video.storage_path:
        return False
    try:
        handle, _media = store_for(video.storage_backend or "local").open(video.storage_path)
    except FileNotFoundError:
        return False
    except StorageError:
        raise
    close = getattr(handle, "close", None)
    if close is not None:
        close()
    return True


def open_named(backend: str, key: str, start: int | None = None, end: int | None = None):
    store = store_for(backend or "local")
    try:
        if start is None:
            return store.open(key)
        return store.open_range(key, start, end)
    except FileNotFoundError:
        raise not_found() from None


def open_media(video: Video, start: int | None = None, end: int | None = None):
    return open_named(video.storage_backend or "local", video.storage_path, start, end)


def iter_bounded(stream: object, limit: int | None):
    remaining = limit
    try:
        while True:
            if remaining is not None and remaining <= 0:
                break
            size = 64 * 1024
            if remaining is not None:
                size = min(size, remaining)
            chunk = stream.read(size)  # type: ignore[attr-defined]
            if not chunk:
                break
            if remaining is not None:
                remaining -= len(chunk)
            yield chunk
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()


def _card(db: Session, video: Video, child: Child) -> dict:
    author = _author(db, video)
    return {
        "id": video.id,
        "duration_label": duration_label(video.duration_ms),
        "who": display_who(author),
        "age_label": age_label(child.birth_date, video.on_date),
        "has_poster": bool(video.poster_path),
        "poster_url": f"/media/videos/{video.id}/poster" if video.poster_path else None,
    }


def videos_for_feed(db: Session, child: Child) -> dict[str, list[dict]]:
    rows = list(
        db.scalars(
            select(Video).where(
                Video.family_id == child.family_id,
                Video.child_id == child.id,
                Video.deleted_at.is_(None),
            )
        ).all()
    )
    rows.sort(key=lambda row: (as_utc(row.created_at), row.id), reverse=True)
    grouped: dict[str, list[dict]] = {}
    for video in rows:
        grouped.setdefault(video.on_date.isoformat(), []).append(_card(db, video, child))
    return grouped


def count_my_videos(db: Session, *, family_id: int, uploader_id: int) -> int:
    value = db.scalar(
        select(func.count())
        .select_from(Video)
        .where(
            Video.family_id == family_id,
            Video.uploader_id == uploader_id,
            Video.deleted_at.is_(None),
        )
    )
    return int(value or 0)
