"""Calendar day written in a photo file, as a literal YYYY-MM-DD.

Only three recorded fields are consulted, in order: the original capture
field, the digitized field, then the generic date field. The first ten
characters decide the day. Hour, offset, and zone tags are ignored, so the
day is never shifted. A broken file, a non-image, or a missing field yields
None. This reader does not decode pixels and does not store anything.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from app.constants import LEGACY_IMPORT_MAX_BYTES

_DAY = re.compile(r"^(\d{4})[:\-](\d{2})[:\-](\d{2})")
_ORIGINAL = 0x9003
_DIGITIZED = 0x9004
_RECORDED = 0x0132
_SUB_IFD = 0x8769
_PRIORITY = (_ORIGINAL, _DIGITIZED, _RECORDED)
_ASCII = {2, 7}
_MAX_TEXT = 32
_MAX_ENTRIES = 128


def read_capture_day(path: Path) -> date | None:
    """Return the literal calendar day, or None when the file has none."""
    try:
        size = path.stat().st_size
        if size <= 0 or size > LEGACY_IMPORT_MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) != size:
        return None
    try:
        return _from_bytes(data)
    except Exception:
        return None


def _from_bytes(data: bytes) -> date | None:
    for blob in _tiff_blobs(data):
        found = _tiff_day(blob)
        if found is not None:
            return found
    return None


def _tiff_blobs(data: bytes):
    if data.startswith(b"\xff\xd8"):
        yield from _jpeg_blobs(data)
        return
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        yield from _png_blobs(data)
        return
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        yield from _webp_blobs(data)


def _unwrap(payload: bytes) -> bytes:
    if payload.startswith(b"Exif\x00\x00") and len(payload) > 6:
        return payload[6:]
    return payload


def _jpeg_blobs(data: bytes):
    limit = len(data)
    index = 2
    while index < limit:
        if data[index] != 0xFF:
            return
        index += 1
        while index < limit and data[index] == 0xFF:
            index += 1
        if index >= limit:
            return
        marker = data[index]
        index += 1
        if marker in {0xD9, 0xDA}:
            return
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > limit:
            return
        segment = int.from_bytes(data[index : index + 2], "big")
        if segment < 2 or index + segment > limit:
            return
        payload = data[index + 2 : index + segment]
        index += segment
        if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            yield _unwrap(payload)


def _png_blobs(data: bytes):
    index = 8
    limit = len(data)
    while index + 8 <= limit:
        length = int.from_bytes(data[index : index + 4], "big")
        tag = data[index + 4 : index + 8]
        if length < 0 or index + 12 + length > limit:
            return
        chunk = data[index + 8 : index + 8 + length]
        index += 12 + length
        if tag == b"eXIf":
            yield _unwrap(chunk)
        if tag == b"IEND":
            return


def _webp_blobs(data: bytes):
    index = 12
    limit = len(data)
    while index + 8 <= limit:
        tag = data[index : index + 4]
        size = int.from_bytes(data[index + 4 : index + 8], "little")
        if size < 0 or index + 8 + size > limit:
            return
        chunk = data[index + 8 : index + 8 + size]
        index += 8 + size
        if size % 2:
            index += 1
        if tag == b"EXIF":
            yield _unwrap(chunk)


def _tiff_day(blob: bytes) -> date | None:
    if len(blob) < 8:
        return None
    order = blob[:2]
    if order == b"II":
        little = True
    elif order == b"MM":
        little = False
    else:
        return None

    def u16(offset: int) -> int | None:
        if offset < 0 or offset + 2 > len(blob):
            return None
        return int.from_bytes(blob[offset : offset + 2], "little" if little else "big")

    def u32(offset: int) -> int | None:
        if offset < 0 or offset + 4 > len(blob):
            return None
        return int.from_bytes(blob[offset : offset + 4], "little" if little else "big")

    magic = u16(2)
    if magic != 42:
        return None
    ifd0 = u32(4)
    if ifd0 is None:
        return None
    found: dict[int, str] = {}
    _walk(blob, u16, u32, ifd0, found, seen=set(), depth=0)
    for tag in _PRIORITY:
        text = found.get(tag)
        if not text:
            continue
        parsed = _calendar_day(text)
        if parsed is not None:
            return parsed
    return None


def _walk(blob: bytes, u16, u32, offset: int, found: dict[int, str], seen: set[int], depth: int) -> None:
    if depth > 3 or offset in seen or offset <= 0:
        return
    count = u16(offset)
    if count is None or count <= 0 or count > _MAX_ENTRIES:
        return
    seen.add(offset)
    sub = None
    for slot in range(count):
        pos = offset + 2 + slot * 12
        tag = u16(pos)
        kind = u16(pos + 2)
        count_values = u32(pos + 4)
        if tag is None or kind is None or count_values is None:
            return
        if tag == _SUB_IFD and kind in {3, 4} and count_values >= 1:
            if kind == 3:
                sub = u16(pos + 8)
            else:
                sub = u32(pos + 8)
            continue
        if tag not in _PRIORITY or kind not in _ASCII:
            continue
        if tag in found:
            continue
        text = _ascii_at(blob, u32, pos + 8, count_values)
        if text:
            found[tag] = text
    if sub:
        _walk(blob, u16, u32, sub, found, seen, depth + 1)


def _ascii_at(blob: bytes, u32, field: int, count: int) -> str | None:
    if count <= 0 or count > _MAX_TEXT:
        return None
    if count <= 4:
        if field < 0 or field + count > len(blob):
            return None
        raw = blob[field : field + count]
    else:
        ptr = u32(field)
        if ptr is None or ptr < 0 or ptr + count > len(blob):
            return None
        raw = blob[ptr : ptr + count]
    text = raw.split(b"\x00", 1)[0].decode("ascii", "ignore").strip()
    return text or None


def _calendar_day(text: str) -> date | None:
    match = _DAY.match(text.strip())
    if match is None:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None
