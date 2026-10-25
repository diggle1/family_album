"""One old photo at a time. The file is spooled to disk, then stored.

A readable capture day in the file wins over the day the client claimed.
When the file has no capture day, the hand-picked day is required.
This module never fills in today's date. A failed file does not delete
photos that already committed.
"""

from __future__ import annotations

import logging
import re
import uuid
import zlib
from datetime import date
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.constants import (
    DAY_PILES,
    LEGACY_IMPORT,
    LEGACY_IMPORT_MAX_BYTES,
    LEGACY_IMPORT_MAX_EDGE,
)
from app.services.capture_day import read_capture_day
from app.models import Photo
from app.services.assoc import ChildSetError, parse_form_child_ids, replace_photo_children, resolve_children
from app.services.photos import _EXT, photo_payload, sniff_image
from app.services.validators import DATE_RE
from app.storage.errors import StorageError
from app.storage.factory import current_store

logger = logging.getLogger("family_album.legacy_import")

_BIRTH = "那天{name}还没出生"
_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


class LegacyImportError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        self.status = status
        self.code = code
        self.detail = detail
        super().__init__(detail)


def parse_claimed_on_date(raw: str | None) -> date | None:
    """Client day, if one was sent. Blank is missing. A bad shape is invalid."""
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw).strip()
    if not DATE_RE.match(text):
        raise LegacyImportError(422, "on_date_invalid", DAY_PILES["err_nodate"])
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise LegacyImportError(422, "on_date_invalid", DAY_PILES["err_nodate"]) from None


def require_on_date(raw: str | None) -> date:
    """Hand-picked calendar day. Missing and invalid are different codes."""
    claimed = parse_claimed_on_date(raw)
    if claimed is None:
        raise LegacyImportError(422, "on_date_required", DAY_PILES["err_nodate"])
    return claimed


def choose_on_date(path: Path, claimed: date | None) -> date:
    """Prefer the day read from the file. Never invent today's date."""
    captured = read_capture_day(path)
    if captured is not None:
        return captured
    if claimed is None:
        raise LegacyImportError(422, "on_date_required", DAY_PILES["err_nodate"])
    return claimed


def require_child_ids(raw: list[str] | None) -> list[int]:
    if raw is None or not any(str(item).strip() for item in raw):
        raise LegacyImportError(422, "child_required", LEGACY_IMPORT["err_child"])
    parsed = parse_form_child_ids(raw)
    if not parsed:
        raise LegacyImportError(422, "child_invalid", LEGACY_IMPORT["err_child"])
    return parsed


def note_session(raw: str | None) -> None:
    """Optional client id for logs. It is not stored."""
    text = (raw or "").strip()
    if text and _SESSION.match(text):
        logger.info("legacy_import session=%s", text)


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 33 or data[12:16] != b"IHDR":
        return None
    length = int.from_bytes(data[8:12], "big")
    if length != 13:
        return None
    expect = zlib.crc32(data[12:29]) & 0xFFFFFFFF
    actual = int.from_bytes(data[29:33], "big")
    if expect != actual:
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return width, height


def _webp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[8:12] != b"WEBP":
        return None
    kind = data[12:16]
    if kind == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
    elif kind == b"VP8 ":
        if data[20:23] != b"\x9d\x01\x2a":
            return None
        width = int.from_bytes(data[23:25], "little") & 0x3FFF
        height = int.from_bytes(data[25:27], "little") & 0x3FFF
    elif kind == b"VP8L":
        if data[20] != 0x2F:
            return None
        b0, b1, b2, b3 = data[21:25]
        width = 1 + (((b1 & 0x3F) << 8) | b0)
        height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
    else:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _jpeg_size(handle) -> tuple[int, int] | None:
    if handle.read(2) != b"\xff\xd8":
        return None
    while True:
        marker = handle.read(1)
        if marker != b"\xff":
            return None
        while marker == b"\xff":
            marker = handle.read(1)
            if not marker:
                return None
        code = marker[0]
        if code in {0xD9, 0xDA}:
            return None
        if code == 0x01 or 0xD0 <= code <= 0xD7:
            continue
        length_bytes = handle.read(2)
        if len(length_bytes) < 2:
            return None
        segment = int.from_bytes(length_bytes, "big")
        if segment < 2:
            return None
        rest = segment - 2
        if code in _SOF:
            body = handle.read(rest)
            if len(body) < 5:
                return None
            height = int.from_bytes(body[1:3], "big")
            width = int.from_bytes(body[3:5], "big")
            if width <= 0 or height <= 0:
                return None
            return width, height
        handle.seek(rest, 1)


def probe_image(path: Path) -> str:
    """Return the media type, or raise a per-file skip error.

    Header bytes decide the type and the longest edge. The bitmap is not decoded.
    """
    with path.open("rb") as handle:
        head = handle.read(32)
        media = sniff_image(head)
        if media is None:
            raise LegacyImportError(400, "legacy_import_format", LEGACY_IMPORT["fail_why_fmt"])
        handle.seek(0)
        if media == "image/png":
            size = _png_size(handle.read(64))
        elif media == "image/webp":
            size = _webp_size(handle.read(64))
        else:
            size = _jpeg_size(handle)
    if size is None:
        raise LegacyImportError(400, "legacy_import_corrupt", LEGACY_IMPORT["fail_why_bad"])
    if max(size) > LEGACY_IMPORT_MAX_EDGE:
        raise LegacyImportError(400, "legacy_import_edge_too_large", LEGACY_IMPORT["fail_why_edge"])
    return media


def import_spooled_photo(
    db: Session,
    *,
    family_id: int,
    uploader_id: int,
    on_date,
    child_ids: list[int],
    path: Path,
    byte_size: int,
) -> dict:
    """Store one spooled file and one child-photo row.

    ``on_date`` is the client claim and may be missing. A day read from the
    file replaces it. Today's date is not a fallback.
    """
    if byte_size > LEGACY_IMPORT_MAX_BYTES:
        raise LegacyImportError(400, "legacy_import_file_too_large", LEGACY_IMPORT["fail_why_mb"])
    on_date = choose_on_date(path, on_date)
    try:
        resolved = resolve_children(
            db,
            family_id=family_id,
            child_ids=child_ids,
            legacy_child_id=None,
            used_list=True,
            required_detail=LEGACY_IMPORT["err_child"],
            missing_detail=LEGACY_IMPORT["err_child"],
            birth_detail=_BIRTH,
            on_date=on_date,
        )
    except ChildSetError as exc:
        if exc.code == "date_before_birth":
            raise LegacyImportError(400, exc.code, exc.detail) from exc
        if exc.code == "child_required":
            raise LegacyImportError(422, "child_required", LEGACY_IMPORT["err_child"]) from exc
        raise LegacyImportError(422, "child_invalid", LEGACY_IMPORT["err_child"]) from exc

    media = probe_image(path)
    settings = get_settings()
    filename = f"{uuid.uuid4().hex}{_EXT[media]}"
    backend = settings.storage_backend if settings.storage_backend in {"local", "s3"} else "local"
    store = current_store()
    stored = False
    try:
        store.put_file(filename, path, media)
        stored = True
        photo = Photo(
            family_id=family_id,
            uploader_id=uploader_id,
            kind="child",
            child_id=resolved.children[0].id,
            taken_on=on_date,
            storage_path=filename,
            storage_backend=backend,
        )
        db.add(photo)
        db.flush()
        replace_photo_children(db, photo, resolved.children)
        db.commit()
    except LegacyImportError:
        raise
    except StorageError:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        if stored:
            store.delete(filename)
        raise LegacyImportError(400, "legacy_import_corrupt", LEGACY_IMPORT["fail_why_bad"]) from None
    except Exception:
        db.rollback()
        if stored:
            store.delete(filename)
        raise
    db.refresh(photo)
    return photo_payload(db, photo)
