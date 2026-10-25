"""Old-photo tray: one file per request, hand-picked day, no new tables."""

from __future__ import annotations

import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from app.config import get_settings
from app.constants import (
    LEGACY_IMPORT,
    LEGACY_IMPORT_BATCH_MAX_BYTES,
    LEGACY_IMPORT_CONCURRENCY,
    LEGACY_IMPORT_MAX_BYTES,
    LEGACY_IMPORT_MAX_EDGE,
    LEGACY_IMPORT_MAX_FILES,
)
from app.db import SessionLocal, engine
from app.main import app
from app.models import Photo, PhotoChild
from app.services.clock import shanghai_today
from app.services.legacy_import import probe_image
from app.storage.errors import StorageError
from tests.test_acceptance import PNG, auth
from tests.test_journal import _family, _invite
from tests.test_on_this_day import EXPECTED_TABLES

BANNED = ("会员", "开通", "商城", "发现", "广告")
ANNO = (
    "anno_tray",
    "anno_chip",
    "anno_film",
    "anno_bar",
    "anno_fail",
    "anno_desk",
    "anno_mb",
    "anno_empty",
)
SURFACES = (
    Path("app/legacy_import_copy.py"),
    Path("app/static/legacy_import.js"),
    Path("app/templates/legacy_import.html"),
    Path("app/services/legacy_import.py"),
    Path("app/routers/legacy_import.py"),
)
DAY = "2026-03-03"
SCRIPT = Path("app/static/legacy_import.js")
ROUTER = Path("app/routers/legacy_import.py")
SERVICE = Path("app/services/legacy_import.py")


def _png(width: int, height: int, *, pixels: bool = True) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    body = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
    if pixels:
        raw = b"".join(b"\x00" + bytes((120, 80, 40)) * width for _ in range(height))
        body += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return body


def _jpeg(width: int, height: int, extra: bytes = b"") -> bytes:
    body = bytes(
        [
            8,
            (height >> 8) & 255,
            height & 255,
            (width >> 8) & 255,
            width & 255,
            1,
            1,
            0x11,
            0,
        ]
    )
    sof = b"\xff\xc0" + struct.pack(">H", len(body) + 2) + body
    return b"\xff\xd8" + extra + sof + b"\xff\xd9"


def _post(client: TestClient, child_ids: list[int], content: bytes, *, name: str = "a.png", on_date: str | None = DAY, mime: str = "image/png", extra: dict | None = None):
    data: dict = {}
    if on_date is not None:
        data["on_date"] = on_date
    if child_ids:
        data["child_ids"] = [str(item) for item in child_ids]
    if extra:
        data.update(extra)
    return client.post(
        "/api/families/current/legacy-import/photos",
        data=data,
        files={"file": (name, content, mime)},
    )


def _count() -> int:
    session = SessionLocal()
    try:
        return len(session.scalars(select(Photo)).all())
    finally:
        session.close()


def _assoc(photo_id: int) -> list[int]:
    session = SessionLocal()
    try:
        rows = session.scalars(
            select(PhotoChild).where(PhotoChild.photo_id == photo_id).order_by(PhotoChild.position.asc())
        ).all()
        return [row.child_id for row in rows]
    finally:
        session.close()


def test_max_upload_bytes_is_20mib():
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.max_upload_bytes == 20 * 1024 * 1024
    assert LEGACY_IMPORT_MAX_BYTES == 20 * 1024 * 1024
    assert LEGACY_IMPORT_BATCH_MAX_BYTES == 200 * 1024 * 1024
    assert LEGACY_IMPORT_MAX_FILES == 50
    assert LEGACY_IMPORT_MAX_EDGE == 8192
    assert LEGACY_IMPORT_CONCURRENCY == 1


def test_legacy_import_happy_path(client: TestClient):
    elder = _family(client, "li-ok@example.com", "妈妈", "旧照家", child="果果")
    younger = client.post("/api/children", json={"name": "豆豆", "birth_date": "2024-08-01"}).json()["id"]
    created = _post(client, [elder, younger], PNG, on_date=DAY)
    assert created.status_code == 201, created.text
    assert "private, no-store" in created.headers["cache-control"]
    body = created.json()
    assert body["taken_on"] == DAY
    assert body["taken_on"] != shanghai_today().isoformat()
    assert body["kind"] == "child"
    assert body["child_ids"] == [elder, younger]
    assert "code" not in body or body.get("code") is None
    photo_id = body["id"]
    assert _assoc(photo_id) == [elder, younger]
    feed = client.get(f"/api/children/{elder}/timeline").json()
    assert feed["days"][0]["taken_on"] == DAY
    assert [item["id"] for item in feed["days"][0]["photos"]] == [photo_id]
    day = client.get(f"/children/{elder}/days/{DAY}")
    assert day.status_code == 200
    assert f"/media/{photo_id}" in day.text
    family_day = client.get(f"/days/{DAY}")
    assert f"/media/{photo_id}" in family_day.text
    media = client.get(f"/media/{photo_id}")
    assert media.status_code == 200
    assert media.content.startswith(b"\x89PNG")
    detail = client.get(f"/photos/{photo_id}")
    assert "妈妈传" in detail.text
    timeline = client.get(f"/?child={elder}")
    assert "妈妈传" in timeline.text
    names = {col["name"] for col in inspect(engine).get_columns("photos")}
    assert "source" not in names
    live = set(inspect(engine).get_table_names())
    live.discard("sqlite_sequence")
    assert live == EXPECTED_TABLES


def test_viewer_no_legacy_import_entry(client: TestClient):
    _family(client, "li-admin@example.com", "妈妈", "入口家", child="果果")
    viewer = _invite(client, "li-view@example.com", "viewer", "奶奶")
    uploader = _invite(client, "li-up@example.com", "uploader", "爸爸")
    assert LEGACY_IMPORT["cta"] in client.get("/family/settings").text
    assert LEGACY_IMPORT["cta"] in client.get("/upload").text
    assert 'id="fab-legacy"' in client.get("/").text
    assert LEGACY_IMPORT["cta"] not in viewer.get("/family/settings").text
    assert LEGACY_IMPORT["cta"] not in viewer.get("/").text
    assert viewer.get("/upload").status_code == 403
    assert viewer.get("/legacy-import").status_code == 403
    assert LEGACY_IMPORT["cta"] not in viewer.get("/legacy-import").text
    assert uploader.get("/legacy-import").status_code == 200
    assert LEGACY_IMPORT["cta"] in uploader.get("/family/settings").text


def test_viewer_legacy_import_403(client: TestClient):
    child_id = _family(client, "li-adm2@example.com", "妈妈", "拒绝家", child="果果")
    viewer = _invite(client, "li-view2@example.com", "viewer", "爷爷")
    denied = _post(viewer, [child_id], PNG)
    assert denied.status_code == 403
    assert denied.json()["code"] == "legacy_import_readonly"
    assert denied.json()["detail"] == LEGACY_IMPORT["err_perm"]
    assert _count() == 0


def test_outsider_legacy_import_403_no_code(client: TestClient):
    child_id = _family(client, "li-home@example.com", "妈妈", "本家", child="果果")
    outsider = TestClient(app)
    auth(outsider, "li-out@example.com", "路人")
    denied = _post(outsider, [child_id], PNG)
    assert denied.status_code == 403
    assert denied.json()["detail"]
    assert "code" not in denied.json()
    assert _count() == 0
    stranger = TestClient(app)
    naked = _post(stranger, [child_id], PNG)
    assert naked.status_code == 403
    assert "code" not in naked.json()


def test_legacy_import_missing_date_or_child_422(client: TestClient):
    child_id = _family(client, "li-miss@example.com", "妈妈", "缺字段家", child="果果")
    missing_date = _post(client, [child_id], PNG, on_date=None)
    assert missing_date.status_code == 422
    assert missing_date.json()["code"] == "on_date_required"
    blank = _post(client, [child_id], PNG, on_date="  ")
    assert blank.status_code == 422
    assert blank.json()["code"] == "on_date_required"
    bad = _post(client, [child_id], PNG, on_date="2026-13-40")
    assert bad.status_code == 422
    assert bad.json()["code"] == "on_date_invalid"
    missing_child = _post(client, [], PNG)
    assert missing_child.status_code == 422
    assert missing_child.json()["code"] == "child_required"
    invalid_child = _post(client, [99999], PNG)
    assert invalid_child.status_code == 422
    assert invalid_child.json()["code"] == "child_invalid"
    assert _count() == 0
    no_file = client.post(
        "/api/families/current/legacy-import/photos",
        data={"on_date": DAY, "child_ids": str(child_id)},
    )
    assert no_file.status_code == 422
    assert no_file.json()["code"] == "file_required"


def test_legacy_import_no_default_today(client: TestClient):
    child_id = _family(client, "li-day@example.com", "妈妈", "手选日家", child="果果")
    page = client.get("/legacy-import")
    assert page.status_code == 200
    tag = page.text.split('id="on-date"', 1)[1].split(">", 1)[0]
    assert 'value=""' in tag
    today = shanghai_today().isoformat()
    assert today not in tag
    assert today not in page.text
    blocked = _post(client, [child_id], PNG, on_date=None)
    assert blocked.status_code == 422
    assert _count() == 0
    service = SERVICE.read_text(encoding="utf-8")
    router = ROUTER.read_text(encoding="utf-8")
    blob = (service + router).lower()
    assert "exif" not in blob
    assert "datetimeoriginal" not in blob
    assert "shanghai_today" not in service
    extra = b"Exif\x00\x00" + b"2020:01:02 08:30:00"
    app1 = b"\xff\xe1" + struct.pack(">H", len(extra) + 2) + extra
    dated = _jpeg(24, 16, extra=app1)
    created = _post(client, [child_id], dated, name="old.jpg", on_date="2026-02-02", mime="image/jpeg")
    assert created.status_code == 201, created.text
    assert created.json()["taken_on"] == "2026-02-02"
    assert created.json()["taken_on"] != "2020-01-02"
    assert created.json()["taken_on"] != today


def test_legacy_import_child_required(client: TestClient):
    elder = _family(client, "li-kid@example.com", "妈妈", "孩子家", child="果果")
    younger = client.post("/api/children", json={"name": "豆豆", "birth_date": "2025-01-01"}).json()["id"]
    family = client.get("/legacy-import")
    assert 'class="sticker on"' not in family.text
    assert 'data-single="0"' in family.text
    child_page = client.get(f"/legacy-import?child={younger}")
    picked = [part for part in child_page.text.split("<button") if 'class="sticker on"' in part]
    assert len(picked) == 1 and f'data-child-id="{younger}"' in picked[0]
    solo = _family(client, "li-one@example.com", "爸爸", "一个孩子", child="安安")
    one = client.get("/legacy-import")
    assert 'data-single="1"' in one.text
    assert f'data-child-id="{solo}"' in one.text
    assert 'class="sticker on"' in one.text
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    completed = subprocess.run(
        [
            node,
            "-e",
            "const api=require(process.argv[1]);"
            "const keep=api.toggleChild(['7'],'7',false);"
            "const drop=api.toggleChild(['7','8'],'7',false);"
            "const add=api.toggleChild([],'9',false);"
            "const locked=api.toggleChild(['7'],'8',true);"
            "if (keep.join()!=='7'||drop.join()!=='8'||add.join()!=='9'||locked.join()!=='7') process.exit(1);",
            str(SCRIPT.resolve()),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert elder


def test_legacy_import_over_50_blocked_client():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    completed = subprocess.run(
        [
            node,
            "-e",
            "const api=require(process.argv[1]);"
            "const limits={maxFiles:50,maxFile:20*1024*1024,maxBatch:200*1024*1024};"
            "if (api.batchBlock(50,200*1024*1024,limits)!=='') process.exit(1);"
            "if (api.batchBlock(51,10,limits)!=='over50') process.exit(2);"
            "if (api.batchBlock(0,0,limits)!=='empty') process.exit(3);",
            str(SCRIPT.resolve()),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    page_source = Path("app/templates/legacy_import.html").read_text(encoding="utf-8")
    assert "multiple" in page_source
    assert 'accept="image/*"' in page_source


def test_legacy_import_batch_over_200mib_blocked():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    completed = subprocess.run(
        [
            node,
            "-e",
            "const api=require(process.argv[1]);"
            "const limits={maxFiles:50,maxFile:20*1024*1024,maxBatch:200*1024*1024};"
            "if (api.batchBlock(2,200*1024*1024,limits)!=='') process.exit(1);"
            "if (api.batchBlock(2,200*1024*1024+1,limits)!=='batch') process.exit(2);",
            str(SCRIPT.resolve()),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_legacy_import_file_over_20mib_skip(client: TestClient, tmp_path: Path):
    child_id = _family(client, "li-big@example.com", "妈妈", "体积家", child="果果")
    path = tmp_path / "big.png"
    with path.open("wb") as handle:
        handle.write(PNG)
        handle.write(b"\x00" * (LEGACY_IMPORT_MAX_BYTES + 1 - len(PNG)))
    with path.open("rb") as handle:
        denied = client.post(
            "/api/families/current/legacy-import/photos",
            data={"on_date": DAY, "child_ids": str(child_id)},
            files={"file": ("big.png", handle, "image/png")},
        )
    assert denied.status_code == 400
    assert denied.json()["code"] == "legacy_import_file_too_large"
    assert denied.json()["detail"] == LEGACY_IMPORT["fail_why_mb"]
    assert _count() == 0
    kept = _post(client, [child_id], PNG)
    assert kept.status_code == 201, kept.text
    assert _count() == 1


def test_legacy_import_edge_over_8192_skip(client: TestClient):
    child_id = _family(client, "li-edge@example.com", "妈妈", "边长家", child="果果")
    wide = _png(8193, 2, pixels=False)
    denied = _post(client, [child_id], wide, name="wide.png")
    assert denied.status_code == 400
    assert denied.json()["code"] == "legacy_import_edge_too_large"
    assert denied.json()["detail"] == LEGACY_IMPORT["fail_why_edge"]
    assert _count() == 0
    edge = _png(LEGACY_IMPORT_MAX_EDGE, 1, pixels=False)
    allowed = _post(client, [child_id], edge, name="edge.png")
    assert allowed.status_code == 201, allowed.text
    assert _count() == 1


def test_legacy_import_bad_format_skip(client: TestClient):
    child_id = _family(client, "li-gif@example.com", "妈妈", "格式家", child="果果")
    denied = _post(client, [child_id], b"GIF89a" + b"\x00" * 32, name="a.gif", mime="image/gif")
    assert denied.status_code == 400
    assert denied.json()["code"] == "legacy_import_format"
    assert _count() == 0


def test_legacy_import_corrupt_skip(client: TestClient):
    child_id = _family(client, "li-bad@example.com", "妈妈", "损坏家", child="果果")
    denied = _post(client, [child_id], b"\xff\xd8\xff" + b"\x00" * 24, name="bad.jpg", mime="image/jpeg")
    assert denied.status_code == 400
    assert denied.json()["code"] == "legacy_import_corrupt"
    assert _count() == 0


def test_legacy_import_partial_success(client: TestClient):
    child_id = _family(client, "li-part@example.com", "妈妈", "部分家", child="果果")
    good = _post(client, [child_id], PNG, name="ok.png")
    bad = _post(client, [child_id], b"GIF89a" + b"\x00" * 16, name="no.gif", mime="image/gif")
    assert good.status_code == 201, good.text
    assert bad.status_code == 400
    assert bad.json()["code"] == "legacy_import_format"
    assert _count() == 1
    assert _assoc(good.json()["id"]) == [child_id]


def test_legacy_import_storage_error_no_rollback(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    child_id = _family(client, "li-disk@example.com", "妈妈", "磁盘家", child="果果")
    first = _post(client, [child_id], PNG, name="one.png")
    assert first.status_code == 201, first.text

    def boom(self, key, source, content_type):
        raise StorageError("VOL_MISSING", "媒资卷没挂上")

    monkeypatch.setattr("app.storage.local.LocalStore.put_file", boom)
    second = _post(client, [child_id], PNG, name="two.png")
    assert second.status_code == 503
    assert _count() == 1
    assert _assoc(first.json()["id"]) == [child_id]


def test_legacy_import_no_batch_body_buffer():
    router = ROUTER.read_text(encoding="utf-8")
    service = SERVICE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    assert "request.body" not in router
    assert "request.body" not in service
    assert "await upload.read()" not in router
    assert "b\"\".join" not in router
    assert "arrayBuffer" not in script
    assert "readAsArrayBuffer" not in script
    assert "Promise.all" not in script
    assert "exif" not in script.lower()
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    completed = subprocess.run(
        [
            node,
            "-e",
            "const api=require(process.argv[1]);"
            "const order=[];"
            "api.runSerial([1,2], async (item) => { order.push('s'+item); await new Promise((r)=>setTimeout(r,15)); order.push('e'+item); }).then(()=>{"
            "if (order.join(',')!=='s1,e1,s2,e2') process.exit(1);"
            "if (!api.retryable(404, {})) process.exit(2);"
            "if (api.retryable(404, {code:'legacy_import_format'})) process.exit(3);"
            "if (!api.retryable(503, {})) process.exit(4);"
            "if (!api.retryable(0, {})) process.exit(5);"
            "if (api.retryable(400, {})) process.exit(6);"
            "if (api.acceptLate(3,3)!==true || api.acceptLate(3,4)!==false) process.exit(7);"
            "if (api.finishFrame(2,0,2,false)!=='done_ok') process.exit(8);"
            "if (api.finishFrame(1,0,2,true)!=='done_partial') process.exit(9);"
            "if (api.finishFrame(1,1,2,false)!=='done_partial') process.exit(10);"
            "});",
            str(SCRIPT.resolve()),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_release_notes_mention_body_size_and_buffering_off():
    notes = Path("releases/v2026.10.25.7.md").read_text(encoding="utf-8")
    assert "client_max_body_size" in notes
    assert "200m" in notes
    assert "proxy_request_buffering off" in notes
    assert "down -v" in notes
    assert "v2026.10.25.6" in notes
    assert "a27c4932" in notes
    assert "45252ce783ac9050ca31b7de888a14d0" in notes
    assert "29a8b08713dbdb2e2a3e28a367afd596" in notes
    assert "d0c08c0e390b4facf25a738f4cd20ca4" in notes
    assert "legacy_import ready (no new tables)" in notes
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.split("## v2026.10.25.6", 1)[0].startswith("# Changelog\n\n## v2026.10.25.8")
    assert app.version == "2026.10.25.8"


def test_copy_cta_wording():
    assert LEGACY_IMPORT["cta"] == "导入旧照片"
    assert LEGACY_IMPORT["err_over50"] == "一次最多 50 张，可再分批"
    assert LEGACY_IMPORT["by"] == "{who}传"
    assert len(LEGACY_IMPORT) == 45
    assert set(ANNO).isdisjoint(LEGACY_IMPORT)


def test_copy_no_banned_words_legacy_import():
    blob = "\n".join(LEGACY_IMPORT.values())
    for word in BANNED:
        assert word not in blob, word
    for key in ANNO:
        assert key not in LEGACY_IMPORT
    for path in SURFACES:
        text = path.read_text(encoding="utf-8")
        for word in BANNED:
            assert word not in text, f"{word} in {path}"
        assert "anno_" not in text
        for needle in ("微信", "wechat", "miniprogram", "小程序"):
            assert needle.lower() not in text.lower(), needle
    script = SCRIPT.read_text(encoding="utf-8")
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in script)
    css = Path("app/static/app.css").read_text(encoding="utf-8")
    assert ".app:has(.li-root)" in css
    assert "@media (min-width: 1024px)" in css
    assert "min(720px, 100%)" in css


def test_probe_png_and_jpeg_headers(tmp_path: Path):
    ok = tmp_path / "ok.png"
    ok.write_bytes(PNG)
    assert probe_image(ok) == "image/png"
    wide = tmp_path / "wide.png"
    wide.write_bytes(_png(9000, 1, pixels=False))
    with pytest.raises(Exception) as raised:
        probe_image(wide)
    assert raised.value.code == "legacy_import_edge_too_large"
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8\xff\x00\x00")
    with pytest.raises(Exception) as corrupt:
        probe_image(broken)
    assert corrupt.value.code == "legacy_import_corrupt"


def test_legacy_page_limits_and_steps(client: TestClient):
    _family(client, "li-page@example.com", "妈妈", "页面家", child="果果")
    page = client.get("/legacy-import?child=1")
    assert page.status_code == 200
    assert f'data-max-files="{LEGACY_IMPORT_MAX_FILES}"' in page.text
    assert f'data-max-file="{LEGACY_IMPORT_MAX_BYTES}"' in page.text
    assert f'data-max-batch="{LEGACY_IMPORT_BATCH_MAX_BYTES}"' in page.text
    assert f'data-concurrency="{LEGACY_IMPORT_CONCURRENCY}"' in page.text
    assert 'type="file"' in page.text and "multiple" in page.text
    for key in ("tray_empty_t", "date_pick_go", "submit", "progress", "done_title", "go_see", "desk_aside"):
        assert LEGACY_IMPORT[key] in page.text
    assert page.text.count('class="tab') >= 3
    assert "anno_" not in page.text
