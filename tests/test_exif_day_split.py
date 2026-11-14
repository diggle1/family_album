"""Capture-day piles for old-photo import. v2026.10.25.9. No new tables."""

from __future__ import annotations

import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from app.exif_day_split_copy import DAY_PILE_NOTES, DAY_PILES
from app.main import app
from app.models import Base, Photo
from app.services.capture_day import read_capture_day
from app.services.clock import shanghai_today
from app.db import SessionLocal, engine
from tests.test_acceptance import auth, upload
from tests.test_journal import _family, _invite
from tests.test_legacy_import import _jpeg, _post
from tests.test_on_this_day import EXPECTED_TABLES

BANNED = ("会员", "开通", "商城", "发现", "广告")
JARGON = ("EXIF", "元数据", "exif")
SCRIPT = Path("app/static/legacy_import.js")
PAGE = Path("app/templates/legacy_import.html")
SURFACES = (
    PAGE,
    SCRIPT,
    Path("app/services/legacy_import.py"),
    Path("app/routers/legacy_import.py"),
)
DAY = "2024-08-20"
OTHER = "2024-09-03"
HAND = "2024-10-05"


def _ascii(text: str) -> bytes:
    raw = text.encode("ascii")
    if not raw.endswith(b"\x00"):
        raw += b"\x00"
    return raw


def build_tiff(
    *,
    original: str | None = None,
    digitized: str | None = None,
    recorded: str | None = None,
    offset: str | None = None,
    byte_order: str = "II",
) -> bytes:
    exif_items: list[tuple[int, bytes]] = []
    if original:
        exif_items.append((0x9003, _ascii(original)))
    if digitized:
        exif_items.append((0x9004, _ascii(digitized)))
    if offset:
        exif_items.append((0x9010, _ascii(offset)))
    ifd0_items: list[tuple[int, bytes]] = []
    if recorded:
        ifd0_items.append((0x0132, _ascii(recorded)))
    exif_items.sort()
    ifd0_items.sort()
    has_exif = bool(exif_items)
    n0 = len(ifd0_items) + (1 if has_exif else 0)
    ifd0_size = 2 + n0 * 12 + 4
    exif_size = (2 + len(exif_items) * 12 + 4) if has_exif else 0
    cursor = 8 + ifd0_size + exif_size
    placed: list[bytes] = []

    def place(blob: bytes) -> tuple[int, int]:
        nonlocal cursor
        offset_at = cursor
        placed.append(blob)
        cursor += len(blob)
        return offset_at, len(blob)

    ifd0_ready = [(*place(blob), tag) for tag, blob in ifd0_items]
    exif_ready = [(*place(blob), tag) for tag, blob in exif_items]
    little = byte_order == "II"

    def p16(number: int) -> bytes:
        return struct.pack("<H" if little else ">H", number)

    def p32(number: int) -> bytes:
        return struct.pack("<I" if little else ">I", number)

    out = bytearray()
    out += byte_order.encode("ascii")
    out += p16(42)
    out += p32(8)
    out += p16(n0)
    entries: list[tuple[int, int, int, int]] = []
    for offset_at, count, tag in ifd0_ready:
        entries.append((tag, 2, count, offset_at))
    if has_exif:
        entries.append((0x8769, 4, 1, 8 + ifd0_size))
    entries.sort()
    for tag, kind, count, value in entries:
        out += p16(tag) + p16(kind) + p32(count) + p32(value)
    out += p32(0)
    if has_exif:
        out += p16(len(exif_ready))
        for offset_at, count, tag in exif_ready:
            out += p16(tag) + p16(2) + p32(count) + p32(offset_at)
        out += p32(0)
    for blob in placed:
        out += blob
    return bytes(out)


def jpeg_day(**kwargs) -> bytes:
    order = kwargs.pop("byte_order", "II")
    tiff = build_tiff(byte_order=order, **kwargs)
    payload = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return _jpeg(24, 16, extra=app1)


def png_day(**kwargs) -> bytes:
    tiff = build_tiff(**kwargs)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes((120, 80, 40)) * 8 for _ in range(8))
    body = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"eXIf", tiff)
    body += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return body


def webp_day(**kwargs) -> bytes:
    tiff = build_tiff(**kwargs)
    vp8x = b"VP8X" + struct.pack("<I", 10) + bytes([0x08, 0, 0, 0, 7, 0, 0, 7, 0, 0])
    exif = b"EXIF" + struct.pack("<I", len(tiff)) + tiff
    if len(tiff) % 2:
        exif += b"\x00"
    blob = b"WEBP" + vp8x + exif
    return b"RIFF" + struct.pack("<I", len(blob)) + blob


def _peek(client: TestClient, content: bytes, *, name: str = "a.jpg", mime: str = "image/jpeg"):
    return client.post(
        "/api/families/current/legacy-import/peek-date",
        files={"file": (name, content, mime)},
    )


def _count() -> int:
    session = SessionLocal()
    try:
        return len(session.scalars(select(Photo)).all())
    finally:
        session.close()


def _node(source: str) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    return subprocess.run(
        [node, "-e", source, str(SCRIPT.resolve())],
        check=False,
        capture_output=True,
        text=True,
    )


def test_capture_day_priority_and_literal(tmp_path: Path):
    original = tmp_path / "a.jpg"
    original.write_bytes(
        jpeg_day(
            original="2024:08:20 23:30:00",
            digitized="2024:09:03 01:00:00",
            recorded="2024:10:05 08:00:00",
            offset="+14:00",
        )
    )
    assert read_capture_day(original) == __import__("datetime").date(2024, 8, 20)
    digitized = tmp_path / "b.jpg"
    digitized.write_bytes(jpeg_day(digitized="2024:09:03 00:10:00", recorded="2024:10:05 08:00:00"))
    assert read_capture_day(digitized).isoformat() == OTHER
    recorded = tmp_path / "c.jpg"
    recorded.write_bytes(jpeg_day(recorded="2024:10:05 08:00:00", byte_order="MM"))
    assert read_capture_day(recorded).isoformat() == HAND
    invalid = tmp_path / "d.jpg"
    invalid.write_bytes(jpeg_day(original="2024:13:40 00:00:00", digitized="2024:12:01 00:00:00"))
    assert read_capture_day(invalid).isoformat() == "2024-12-01"
    garbage = b"Exif\x00\x00" + b"2020:01:02 08:30:00"
    app1 = b"\xff\xe1" + struct.pack(">H", len(garbage) + 2) + garbage
    fake = tmp_path / "fake.jpg"
    fake.write_bytes(_jpeg(24, 16, extra=app1))
    assert read_capture_day(fake) is None
    broken = tmp_path / "bad.jpg"
    broken.write_bytes(b"\xff\xd8\xff" + b"\x00" * 24)
    assert read_capture_day(broken) is None
    png = tmp_path / "e.png"
    png.write_bytes(png_day(original="2024:08:20 12:00:00"))
    assert read_capture_day(png).isoformat() == DAY
    webp = tmp_path / "f.webp"
    webp.write_bytes(webp_day(original="2024:08:20 12:00:00"))
    assert read_capture_day(webp).isoformat() == DAY
    assert read_capture_day(tmp_path / "missing.jpg") is None


def test_peek_date_happy_path(client: TestClient):
    child_id = _family(client, "ex-peek@example.com", "妈妈", "探测家", child="果果")
    found = _peek(client, jpeg_day(original="2024:08:20 09:00:00"))
    assert found.status_code == 200, found.text
    assert found.json() == {"on_date": DAY}
    assert "private, no-store" in found.headers["cache-control"]
    assert _count() == 0
    from app.config import get_settings

    leftover = [item.name for item in get_settings().photo_dir.iterdir() if item.name != ".gitkeep"]
    assert leftover == []
    assert child_id


def test_peek_date_null_on_bad_or_missing_exif(client: TestClient):
    _family(client, "ex-null@example.com", "妈妈", "空日家", child="果果")
    garbage = b"Exif\x00\x00" + b"2020:01:02 08:30:00"
    app1 = b"\xff\xe1" + struct.pack(">H", len(garbage) + 2) + garbage
    samples = [
        (_jpeg(16, 16, extra=app1), "fake.jpg", "image/jpeg"),
        (b"\xff\xd8\xff" + b"\x00" * 24, "bad.jpg", "image/jpeg"),
        (b"GIF89a" + b"\x00" * 16, "a.gif", "image/gif"),
        (b"not-a-photo", "a.txt", "text/plain"),
        (b"", "empty.jpg", "image/jpeg"),
    ]
    for content, name, mime in samples:
        found = _peek(client, content, name=name, mime=mime)
        assert found.status_code == 200, found.text
        assert found.json()["on_date"] is None
    assert _count() == 0


def test_peek_date_does_not_store(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _family(client, "ex-store@example.com", "妈妈", "不落盘家", child="果果")

    def boom(*_args, **_kwargs):
        raise AssertionError("peek stored a file")

    monkeypatch.setattr("app.storage.local.LocalStore.put_file", boom)
    monkeypatch.setattr("app.storage.local.LocalStore.put", boom)
    found = _peek(client, jpeg_day(original="2024:08:20 09:00:00"))
    assert found.status_code == 200, found.text
    assert found.json()["on_date"] == DAY
    assert _count() == 0
    live = set(inspect(engine).get_table_names())
    live.discard("sqlite_sequence")
    assert live == EXPECTED_TABLES
    assert {table.name for table in Base.metadata.sorted_tables} == EXPECTED_TABLES


def test_viewer_legacy_import_peek_403(client: TestClient):
    _family(client, "ex-view@example.com", "妈妈", "只读家", child="果果")
    viewer = _invite(client, "ex-grandma@example.com", "viewer", "奶奶")
    denied = _peek(viewer, jpeg_day(original="2024:08:20 09:00:00"))
    assert denied.status_code == 403
    assert denied.json()["code"] == "legacy_import_readonly"
    assert denied.json()["detail"] == DAY_PILES["err_perm"]
    page = viewer.get("/legacy-import")
    assert page.status_code == 403
    assert DAY_PILES["cta"] not in page.text
    assert _count() == 0


def test_outsider_peek_photos_403_no_code(client: TestClient):
    child_id = _family(client, "ex-home@example.com", "妈妈", "本家", child="果果")
    outsider = TestClient(app)
    auth(outsider, "ex-out@example.com", "路人")
    denied = _peek(outsider, jpeg_day(original="2024:08:20 09:00:00"))
    assert denied.status_code == 403
    assert denied.json()["detail"]
    assert "code" not in denied.json()
    photo = _post(outsider, [child_id], jpeg_day(original="2024:08:20 09:00:00"), on_date=DAY)
    assert photo.status_code == 403
    assert "code" not in photo.json()
    stranger = TestClient(app)
    naked = _peek(stranger, jpeg_day(original="2024:08:20 09:00:00"))
    assert naked.status_code == 403
    assert "code" not in naked.json()
    assert _count() == 0


def test_photos_server_exif_overrides_client_on_date(client: TestClient):
    child_id = _family(client, "ex-win@example.com", "妈妈", "覆盖家", child="果果")
    created = _post(
        client,
        [child_id],
        jpeg_day(original="2024:08:20 23:30:00", offset="-12:00"),
        name="old.jpg",
        on_date="2025-03-03",
        mime="image/jpeg",
    )
    assert created.status_code == 201, created.text
    assert created.json()["taken_on"] == DAY
    assert created.json()["taken_on"] != "2025-03-03"
    assert created.json()["taken_on"] != shanghai_today().isoformat()
    omitted = _post(
        client,
        [child_id],
        jpeg_day(original="2024:09:03 00:05:00"),
        name="omit.jpg",
        on_date=None,
        mime="image/jpeg",
    )
    assert omitted.status_code == 201, omitted.text
    assert omitted.json()["taken_on"] == OTHER
    png = _post(client, [child_id], png_day(original="2024:10:05 08:00:00"), name="p.png", on_date="2025-01-01", mime="image/png")
    assert png.status_code == 201, png.text
    assert png.json()["taken_on"] == HAND
    day = client.get(f"/children/{child_id}/days/{DAY}")
    assert day.status_code == 200
    assert f"/media/{created.json()['id']}" in day.text


def test_exif_split_no_default_today(client: TestClient):
    child_id = _family(client, "ex-today@example.com", "妈妈", "不默认今天", child="果果")
    page = client.get("/legacy-import")
    assert page.status_code == 200
    tag = page.text.split('id="on-date"', 1)[1].split(">", 1)[0]
    assert 'value=""' in tag
    today = shanghai_today().isoformat()
    assert today not in tag
    assert today not in page.text
    assert DAY_PILES["no_today_hint"] in page.text
    assert DAY_PILES["split_oral"] in page.text
    blocked = _post(client, [child_id], b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, name="plain.png", on_date=None, mime="image/png")
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "on_date_required"
    assert blocked.json()["detail"] == DAY_PILES["err_nodate"]
    assert _count() == 0
    service = Path("app/services/legacy_import.py").read_text(encoding="utf-8")
    assert "shanghai_today" not in service


def test_exif_split_missing_date_or_child_422(client: TestClient):
    child_id = _family(client, "ex-miss@example.com", "妈妈", "缺字段家", child="果果")
    blank = _post(client, [child_id], jpeg_day(original="2024:08:20 01:00:00"), on_date="  ", mime="image/jpeg")
    assert blank.status_code == 201, blank.text
    assert blank.json()["taken_on"] == DAY
    bad = _post(client, [child_id], jpeg_day(original="2024:08:20 01:00:00"), on_date="2024-13-40", mime="image/jpeg")
    assert bad.status_code == 422
    assert bad.json()["code"] == "on_date_invalid"
    missing_child = _post(client, [], jpeg_day(original="2024:08:20 01:00:00"), on_date=None, mime="image/jpeg")
    assert missing_child.status_code == 422
    assert missing_child.json()["code"] == "child_required"
    no_file = client.post(
        "/api/families/current/legacy-import/peek-date",
        data={"on_date": DAY},
    )
    assert no_file.status_code == 422
    assert no_file.json()["code"] == "file_required"
    photo_file = client.post(
        "/api/families/current/legacy-import/photos",
        data={"on_date": DAY, "child_ids": str(child_id)},
    )
    assert photo_file.status_code == 422
    assert photo_file.json()["code"] == "file_required"


def test_exif_split_nodate_requires_pick(client: TestClient):
    child_id = _family(client, "ex-hand@example.com", "妈妈", "手选家", child="果果")
    plain = _jpeg(16, 16)
    missing = _post(client, [child_id], plain, name="plain.jpg", on_date=None, mime="image/jpeg")
    assert missing.status_code == 422
    assert missing.json()["code"] == "on_date_required"
    assert _count() == 0
    picked = _post(client, [child_id], plain, name="plain.jpg", on_date=HAND, mime="image/jpeg")
    assert picked.status_code == 201, picked.text
    assert picked.json()["taken_on"] == HAND
    assert picked.json()["taken_on"] != shanghai_today().isoformat()


def test_exif_split_mixed_batch_dates(client: TestClient):
    child_id = _family(client, "ex-mix@example.com", "妈妈", "混批家", child="果果")
    first = _post(client, [child_id], jpeg_day(original="2024:08:20 09:00:00"), name="a.jpg", on_date=OTHER, mime="image/jpeg")
    second = _post(client, [child_id], jpeg_day(original="2024:09:03 09:00:00"), name="b.jpg", on_date=DAY, mime="image/jpeg")
    third = _post(client, [child_id], _jpeg(16, 16), name="c.jpg", on_date=HAND, mime="image/jpeg")
    assert first.status_code == 201 and first.json()["taken_on"] == DAY
    assert second.status_code == 201 and second.json()["taken_on"] == OTHER
    assert third.status_code == 201 and third.json()["taken_on"] == HAND
    assert _count() == 3
    assert DAY in client.get(f"/children/{child_id}/days/{DAY}").text or f"/media/{first.json()['id']}" in client.get(
        f"/children/{child_id}/days/{DAY}"
    ).text


def test_exif_split_child_required(client: TestClient):
    elder = _family(client, "ex-kid@example.com", "妈妈", "孩子家", child="果果")
    younger = client.post("/api/children", json={"name": "豆豆", "birth_date": "2025-01-01"}).json()["id"]
    page = client.get("/legacy-import")
    assert 'class="sticker on"' not in page.text
    assert 'data-single="0"' in page.text
    missing = _post(client, [], jpeg_day(original="2024:08:20 09:00:00"), on_date=DAY, mime="image/jpeg")
    assert missing.status_code == 422
    assert missing.json()["code"] == "child_required"
    assert _count() == 0
    assert elder and younger


def test_exif_split_partial_success(client: TestClient):
    child_id = _family(client, "ex-part@example.com", "妈妈", "部分家", child="果果")
    good = _post(client, [child_id], jpeg_day(original="2024:08:20 09:00:00"), name="ok.jpg", on_date="2025-01-01", mime="image/jpeg")
    bad = _post(client, [child_id], b"GIF89a" + b"\x00" * 16, name="no.gif", on_date=HAND, mime="image/gif")
    assert good.status_code == 201 and good.json()["taken_on"] == DAY
    assert bad.status_code == 400 and bad.json()["code"] == "legacy_import_format"
    assert _count() == 1


def test_exif_split_cancel_keeps_successes(client: TestClient):
    child_id = _family(client, "ex-cancel@example.com", "妈妈", "取消家", child="果果")
    kept = _post(client, [child_id], jpeg_day(original="2024:08:20 09:00:00"), name="kept.jpg", mime="image/jpeg")
    assert kept.status_code == 201
    assert _count() == 1
    completed = _node(
        "const api=require(process.argv[1]);"
        "if (api.finishFrame(1,0,2,true)!=='done_partial') process.exit(1);"
        "if (api.finishFrame(2,0,2,false)!=='done_ok') process.exit(2);"
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert _count() == 1


def test_exif_split_late_response_ignored():
    completed = _node(
        "const api=require(process.argv[1]);"
        "if (api.acceptLate(4,4)!==true || api.acceptLate(4,5)!==false) process.exit(1);"
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_exif_split_storage_error_no_rollback(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from app.storage.errors import StorageError

    child_id = _family(client, "ex-disk@example.com", "妈妈", "磁盘家", child="果果")
    first = _post(client, [child_id], jpeg_day(original="2024:08:20 09:00:00"), name="one.jpg", mime="image/jpeg")
    assert first.status_code == 201

    def boom(self, key, source, content_type):
        raise StorageError("VOL_MISSING", "媒资卷没挂上")

    monkeypatch.setattr("app.storage.local.LocalStore.put_file", boom)
    second = _post(client, [child_id], jpeg_day(original="2024:09:03 09:00:00"), name="two.jpg", mime="image/jpeg")
    assert second.status_code == 503
    assert _count() == 1


def test_exif_split_offline_keeps_successes(client: TestClient):
    child_id = _family(client, "ex-off@example.com", "妈妈", "断网家", child="果果")
    kept = _post(client, [child_id], jpeg_day(recorded="2024:10:05 08:00:00"), name="kept.jpg", mime="image/jpeg")
    assert kept.status_code == 201
    assert kept.json()["taken_on"] == HAND
    assert _count() == 1


def test_js_404_without_code_retryable_exif_split():
    completed = _node(
        "const api=require(process.argv[1]);"
        "if (!api.retryable(404, {})) process.exit(1);"
        "if (api.retryable(404, {code:'legacy_import_format'})) process.exit(2);"
        "if (!api.retryable(503, {})) process.exit(3);"
        "if (api.retryable(400, {})) process.exit(4);"
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_exif_split_over_50_blocked_client():
    completed = _node(
        "const api=require(process.argv[1]);"
        "const limits={maxFiles:50,maxFile:20*1024*1024,maxBatch:200*1024*1024};"
        "if (api.batchBlock(51,10,limits)!=='over50') process.exit(1);"
        "if (api.batchBlock(50,10,limits)!=='') process.exit(2);"
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_exif_split_batch_over_200mib_blocked():
    completed = _node(
        "const api=require(process.argv[1]);"
        "const limits={maxFiles:50,maxFile:20*1024*1024,maxBatch:200*1024*1024};"
        "if (api.batchBlock(2,200*1024*1024+1,limits)!=='batch') process.exit(1);"
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_exif_split_file_over_20mib_skip():
    completed = _node(
        "const api=require(process.argv[1]);"
        "const limits={maxFiles:50,maxFile:20*1024*1024,maxBatch:200*1024*1024};"
        "if (api.fileSkip(20*1024*1024+1,limits)!=='mb') process.exit(1);"
        "if (api.fileSkip(20*1024*1024,limits)!=='') process.exit(2);"
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_exif_split_edge_over_8192_skip(client: TestClient):
    from tests.test_legacy_import import _png
    from app.constants import LEGACY_IMPORT_MAX_EDGE

    child_id = _family(client, "ex-edge@example.com", "妈妈", "边长家", child="果果")
    denied = _post(client, [child_id], _png(8193, 2, pixels=False), name="wide.png", on_date=HAND)
    assert denied.status_code == 400
    assert denied.json()["code"] == "legacy_import_edge_too_large"
    assert _count() == 0
    allowed = _post(client, [child_id], _png(LEGACY_IMPORT_MAX_EDGE, 1, pixels=False), name="edge.png", on_date=HAND)
    assert allowed.status_code == 201


def test_exif_split_bad_format_skip(client: TestClient):
    child_id = _family(client, "ex-gif@example.com", "妈妈", "格式家", child="果果")
    denied = _post(client, [child_id], b"GIF89a" + b"\x00" * 32, name="a.gif", on_date=HAND, mime="image/gif")
    assert denied.status_code == 400
    assert denied.json()["code"] == "legacy_import_format"
    assert _count() == 0


def test_exif_split_corrupt_skip(client: TestClient):
    child_id = _family(client, "ex-bad@example.com", "妈妈", "损坏家", child="果果")
    denied = _post(client, [child_id], b"\xff\xd8\xff" + b"\x00" * 24, name="bad.jpg", on_date=HAND, mime="image/jpeg")
    assert denied.status_code == 400
    assert denied.json()["code"] == "legacy_import_corrupt"
    peek = _peek(client, b"\xff\xd8\xff" + b"\x00" * 24, name="bad.jpg")
    assert peek.status_code == 200
    assert peek.json()["on_date"] is None
    assert _count() == 0


def test_exif_split_groups_and_blocks_nodate():
    completed = _node(
        "const api=require(process.argv[1]);"
        "const rows=["
        "{onDate:'2024-08-20',skip:''},{onDate:'2024-09-03',skip:''},{onDate:'2024-08-20',skip:''},"
        "{onDate:null,skip:''},{onDate:null,skip:'mb'}"
        "];"
        "const g=api.groupPiles(rows);"
        "if (g.order.join()!=='2024-08-20,2024-09-03') process.exit(1);"
        "if (g.nodate!==1 || g.skips!==1) process.exit(2);"
        "const head=api.splitHead(g);"
        "if (head.key!=='split_lab_mix' || head.map.a!=='2' || head.map.b!=='1') process.exit(3);"
        "if (api.canSubmit(g,'',1)!=='nodate') process.exit(4);"
        "if (api.canSubmit(g,'2024-10-05',1)!=='') process.exit(5);"
        "if (api.canSubmit(g,'2024-10-05',0)!=='child') process.exit(6);"
        "const all=api.groupPiles([{onDate:null,skip:''}]);"
        "if (api.splitHead(all).key!=='split_lab_all') process.exit(7);"
        "if (api.canSubmit(all,'',1)!=='nodate') process.exit(8);"
        "const ready=api.splitHead(g,'2024-10-05');"
        "if (ready.key!=='split_lab' || ready.map.n!=='3') process.exit(12);"
        "const readyAll=api.splitHead(all,'2024-10-05');"
        "if (readyAll.key!=='split_lab' || readyAll.map.n!=='1') process.exit(13);"
        "if (api.isHandDate('') || api.isHandDate('today')) process.exit(9);"
        "if (api.peekLimit(8)!==3 || api.peekLimit(2)!==2 || api.PEEK_CAP!==3) process.exit(10);"
        "let active=0, max=0;"
        "api.mapLimit([1,2,3,4,5], 8, ()=>new Promise((resolve)=>{"
        "active+=1; max=Math.max(max,active);"
        "setTimeout(()=>{active-=1; resolve(1);}, 15);"
        "})).then(()=>{ if(max!==3) process.exit(11); });"
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_exif_split_no_batch_body_buffer():
    router = Path("app/routers/legacy_import.py").read_text(encoding="utf-8")
    service = Path("app/services/legacy_import.py").read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    assert "request.body" not in router
    assert "request.body" not in service
    assert "b\"\".join" not in router
    assert "arrayBuffer" not in script
    assert "readAsArrayBuffer" not in script
    assert "Promise.all" not in script
    assert "peek-date" in router
    assert "read_capture_day" not in Path("app/services/photos.py").read_text(encoding="utf-8")
    assert "read_capture_day" not in Path("app/routers/photos.py").read_text(encoding="utf-8")


def test_daily_photo_upload_unchanged(client: TestClient):
    child_id = _family(client, "ex-daily@example.com", "妈妈", "日常家", child="果果")
    dated = jpeg_day(original="2024:08:20 11:00:00")
    child = upload(client, kind="child", child_id=child_id, taken_on="2025-01-02", content=dated, filename="c.jpg")
    assert child.status_code == 201, child.text
    assert child.json()["taken_on"] == "2025-01-02"
    self_photo = upload(client, kind="self", send_child=False, content=dated, filename="s.jpg")
    assert self_photo.status_code == 201, self_photo.text
    assert self_photo.json()["taken_on"] == shanghai_today().isoformat()
    assert self_photo.json()["taken_on"] != DAY
    missing = upload(client, kind="child", child_id=child_id, content=dated, filename="m.jpg")
    assert missing.status_code == 400


def test_copy_split_oral(client: TestClient):
    _family(client, "ex-copy@example.com", "妈妈", "文案家", child="果果")
    page = client.get("/legacy-import")
    assert page.status_code == 200
    assert DAY_PILES["split_oral"] == "按拍摄日分开"
    assert DAY_PILES["split_oral"] in page.text
    assert DAY_PILES["pile_need"] in page.text
    assert DAY_PILES["no_today_hint"] in page.text
    assert 'data-peek-limit="3"' in page.text
    assert 'data-concurrency="1"' in page.text
    assert 'id="submit-btn" disabled' in page.text or 'disabled' in page.text.split('id="submit-btn"', 1)[1].split(">", 1)[0]
    assert page.text.count('class="tab') >= 3
    assert "anno_" not in page.text


def test_copy_no_banned_words_exif_split(client: TestClient):
    _family(client, "ex-ban@example.com", "妈妈", "禁词家", child="果果")
    blob = "\n".join(DAY_PILES.values())
    for word in BANNED:
        assert word not in blob, word
    for word in JARGON:
        assert word not in blob, word
    assert len(DAY_PILES) == 54
    assert len(DAY_PILE_NOTES) == 10
    assert set(DAY_PILES).isdisjoint(DAY_PILE_NOTES)
    assert set(DAY_PILE_NOTES) == {
        "anno_pile",
        "anno_need",
        "anno_mix",
        "anno_film",
        "anno_bar",
        "anno_fail",
        "anno_desk",
        "anno_mb",
        "anno_empty",
        "anno_split",
    }
    assert DAY_PILES["split_lab"].format(n=3) == "按拍摄日分成 3 堆"
    assert DAY_PILES["split_lab_mix"].format(a=2, b=2) == "按拍摄日分成 2 堆 · 另有 2 张要选手选日"
    assert DAY_PILES["split_lab_all"] == "这些照片没有拍摄日，要选手选日"
    assert DAY_PILES["pile_cnt"].format(n=4) == "4 张"
    assert DAY_PILES["done_ok"].format(n=7) == "已放入 7 张"
    assert DAY_PILES["done_all"].format(n=9) == "这一批 9 张都放好了"
    assert DAY_PILES["done_fail"].format(n=2) == "2 张没放进去"
    assert DAY_PILES["done_split"].format(n=3) == "已按拍摄日放进 3 天"
    assert DAY_PILES["fail_item"].format(name="a.jpg", why="太大了，超过 20MB") == "跳过：a.jpg（太大了，超过 20MB）"
    assert DAY_PILES["by"].format(who="妈妈") == "妈妈传"
    assert DAY_PILES["err_nodate"] == "先给没有拍摄日的照片选一天"
    page = client.get("/legacy-import").text
    for key, value in DAY_PILES.items():
        assert value in page, key
    for key in DAY_PILE_NOTES:
        assert key not in page
    for path in SURFACES:
        text = path.read_text(encoding="utf-8")
        for word in BANNED:
            assert word not in text, f"{word} in {path}"
        assert "anno_" not in text


def test_copy_no_exif_jargon_in_ui():
    script = SCRIPT.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    css = Path("app/static/app.css").read_text(encoding="utf-8")
    for word in JARGON:
        assert word not in script
        assert word not in page
        assert word.lower() not in "\n".join(DAY_PILES.values()).lower()
    assert "元数据" not in css
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in script)
    assert ".li-root .pile" in css
    assert "font-size: 24px" in css
    assert "font-weight: 900" in css
    assert "@media (min-width: 1024px)" in css
    assert "min(720px, 100%)" in css


def test_release_notes_mention_body_size_and_buffering_off():
    notes = Path("releases/v2026.10.25.9.md").read_text(encoding="utf-8")
    assert "client_max_body_size" in notes
    assert "200m" in notes
    assert "proxy_request_buffering off" in notes
    assert "down -v" in notes
    assert "v2026.10.25.8" in notes
    assert "6b978e7ca5dd8d092b147148c14b60569469fedd" in notes
    assert "0923185135a108f60c78741b122e72f3" in notes
    assert "6d1fae4ef826b2928140fd6b9edaeaf1" in notes
    assert "5ee476e1910ce44d09b2bb9852e1007f" in notes
    assert "cd7116941fb8570e2b942c8add4dfc3a" in notes
    assert "exif_day_split ready (no new tables)" in notes
    assert "DROP TABLE" not in notes
    assert "DROP COLUMN" not in notes
    source = Path("app/migrate_schema.py").read_text(encoding="utf-8")
    script = Path("scripts/migrate.py").read_text(encoding="utf-8")
    assert "v2026.10.25.9 does not add a table or a column" in source
    assert "exif_day_split ready (no new tables)" in script
    assert "docker compose down -v" not in script
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.split("## v2026.10.25.8", 1)[0].startswith("# Changelog\n\n## v2026.10.25.9")
    assert "DROP TABLE" not in changelog.split("## v2026.10.25.8", 1)[0]
    assert app.version == "2026.10.25.9"
