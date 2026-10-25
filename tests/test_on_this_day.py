"""往年今日（窄）琥珀年签 v2026.10.25.5。只读去年同月日，不新表，不推进 last_seen。"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import Session

from app.constants import FORBIDDEN, ON_THIS_DAY
from app.db import Base, SessionLocal
from app.main import app
from app.migrate_schema import migrate_schema
from app.models import Journal, Membership, Photo, TimelineLastSeen, Video
from app.on_this_day_copy import ON_THIS_DAY as COPY
from app.on_this_day_copy import ON_THIS_DAY_ANNO as ANNO
from app.services.clock import shanghai_today, utcnow
from app.services.on_this_day import OnThisDayUnavailable, previous_year_date
from tests.test_acceptance import JPEG, auth, upload

MOCKUP = Path("design/family_album/on-this-day/final/mockup.html")
FINAL_MD5 = "33ebfc7cbe4b5ed25dc503ad73386cf8"
BANNED = ("会员", "开通", "商城", "发现", "广告", "回忆 Feed", "On This Day")
SURFACES = (
    Path("app/on_this_day_copy.py"),
    Path("app/services/on_this_day.py"),
    Path("app/static/on_this_day.js"),
    Path("app/templates/fragments/amber_card.html"),
    Path("app/templates/fragments/stage_inner.html"),
    Path("app/templates/day.html"),
    Path("app/static/app.css"),
)
EXPECTED_TABLES = {
    "auth_code_requests",
    "auth_codes",
    "auth_throttles",
    "children",
    "families",
    "invites",
    "journal_children",
    "journal_photos",
    "journals",
    "memberships",
    "photo_children",
    "photos",
    "timeline_last_seen",
    "users",
    "video_children",
    "videos",
    "day_notes",
    "day_note_children",
}


def _anchor_date() -> date:
    today = shanghai_today()
    try:
        date(today.year - 1, today.month, today.day)
    except ValueError:
        return date(today.year, 2, 28)
    return today


def _target(today: date, years: int = 1) -> date:
    return date(today.year - years, today.month, today.day)


def _family(client: TestClient, email: str, name: str, family: str, child: str = "果果") -> int:
    auth(client, email, name)
    assert client.post("/api/families", json={"name": family}).status_code == 201
    created = client.post("/api/children", json={"name": child, "birth_date": "2018-01-01"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _child(client: TestClient, name: str) -> int:
    created = client.post("/api/children", json={"name": name, "birth_date": "2018-06-01"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _invite(admin: TestClient, email: str, role: str, name: str) -> TestClient:
    token = admin.post("/api/families/invites", json={"email": email, "role": role}).json()["token"]
    member = TestClient(app)
    auth(member, email, name)
    assert member.post(f"/api/invites/{token}/accept").status_code == 200
    return member


def _me(client: TestClient) -> dict:
    payload = client.get("/api/me").json()
    family = client.get("/api/families/current").json()
    return {"user_id": payload["id"], "family_id": family["id"], "family_name": family["name"]}


def _journal(client: TestClient, child_id: int, body: str, on_date: str, child_ids: list[int] | None = None) -> dict:
    payload: dict = {"on_date": on_date, "body": body, "photo_ids": []}
    if child_ids is None:
        payload["child_id"] = child_id
    else:
        payload["child_ids"] = child_ids
    response = client.post("/api/journals", headers={"Idempotency-Key": str(uuid.uuid4())}, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _photo(client: TestClient, child_ids: list[int], taken_on: str) -> dict:
    response = client.post(
        "/api/photos",
        data={"kind": "child", "taken_on": taken_on, "child_ids": [str(item) for item in child_ids]},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _box(kind: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def _mp4(duration_ms: int = 1000) -> bytes:
    ftyp = _box(b"ftyp", b"isom" + b"\x00\x00\x00\x00" + b"isom")
    mdat = _box(b"mdat", b"\x00" * 8)
    body = b"\x00\x00\x00\x00" * 3 + (1000).to_bytes(4, "big") + duration_ms.to_bytes(4, "big")
    return ftyp + mdat + _box(b"moov", _box(b"mvhd", body))


def _video(client: TestClient, child_id: int, on_date: str) -> dict:
    response = client.post(
        "/api/videos",
        data={"child_id": str(child_id), "on_date": on_date, "duration_ms": "1000"},
        files={"file": ("clip.mp4", _mp4(), "video/mp4")},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_created(model, row_id: int, when: datetime) -> None:
    db = SessionLocal()
    try:
        row = db.get(model, row_id)
        assert row is not None
        row.created_at = when
        db.commit()
    finally:
        db.close()


def _seen_stamp(user_id: int, family_id: int):
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(TimelineLastSeen).where(
                TimelineLastSeen.user_id == user_id,
                TimelineLastSeen.family_id == family_id,
            )
        ).all()
        return [(row.id, row.last_seen_at) for row in rows]
    finally:
        db.close()


def _pin(monkeypatch, today: date) -> date:
    monkeypatch.setattr("app.services.on_this_day.shanghai_today", lambda: today)
    return today


def _mockup_copy() -> dict:
    node = shutil.which("node")
    assert node
    script = r"""
const fs = require("fs");
const text = fs.readFileSync(process.argv[1], "utf8");
const start = text.indexOf("const COPY = ");
const end = text.indexOf("\n};", start);
const COPY = Function("return (" + text.slice(start + "const COPY = ".length, end + 2) + ")")();
process.stdout.write(JSON.stringify(COPY));
"""
    completed = subprocess.run([node, "-e", script, str(MOCKUP.resolve())], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _js(expr: str):
    node = shutil.which("node")
    assert node
    script = "const m = require(process.argv[1]); process.stdout.write(JSON.stringify(" + expr + "));"
    completed = subprocess.run(
        [node, "-e", script, str(Path("app/static/on_this_day.js").resolve())],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_copy_no_banned_words_on_this_day():
    assert hashlib.md5(MOCKUP.read_bytes()).hexdigest() == FINAL_MD5
    assert hashlib.md5(Path("design/family_album/elders-whats-new/final/mockup.html").read_bytes()).hexdigest() == (
        "ac2ceba835f45d368d82c98660a7c32d"
    )
    assert hashlib.md5(Path("design/family_album/elders-no-app/final/mockup.html").read_bytes()).hexdigest() == (
        "3f41b99ba967c82e804dc80f29bd02be"
    )
    assert hashlib.md5(Path("design/family_album/photo-multi-child/final/mockup.html").read_bytes()).hexdigest() == (
        "f700e4de1b74ac0923745d91c63a0e20"
    )
    mockup = _mockup_copy()
    assert len(mockup) == 45
    assert set(mockup) == set(COPY) | set(ANNO)
    assert len(COPY) == 40
    assert len(ANNO) == 5
    assert COPY is ON_THIS_DAY
    for key, value in COPY.items():
        assert mockup[key] == value
    for key, value in ANNO.items():
        assert mockup[key] == value
        assert key.startswith("anno_")
    blob = "\n".join(COPY.values())
    for word in BANNED:
        assert word not in blob, word
    for path in SURFACES:
        text = path.read_text(encoding="utf-8")
        for word in BANNED:
            assert word not in text, f"{word} in {path}"
    js = Path("app/static/on_this_day.js").read_text(encoding="utf-8")
    assert "去年今天" not in js
    joined = "\n".join(path.read_text(encoding="utf-8") for path in SURFACES)
    for needle in ("微信", "wechat", "miniprogram", "小程序"):
        assert needle.lower() not in joined.lower()
    layout = Path("app/templates/layout.html").read_text(encoding="utf-8")
    assert layout.count('class="tab{') == 3
    tabs = [key for key in COPY if key.startswith("tab_")]
    assert tabs == ["tab_time", "tab_family", "tab_settings"]
    css = Path("app/static/app.css").read_text(encoding="utf-8")
    assert "min(720px, 100%)" in css
    assert "amber-card" in css
    assert "wn-rail-side" not in css


def test_no_new_bottom_tab_copy():
    layout = Path("app/templates/layout.html").read_text(encoding="utf-8")
    assert layout.count('class="tab{') == 3
    assert "往年" not in layout
    assert [key for key in COPY if key.startswith("tab_")] == ["tab_time", "tab_family", "tab_settings"]


def test_on_this_day_no_schema_change(tmp_path):
    assert {table.name for table in Base.metadata.sorted_tables} == EXPECTED_TABLES
    engine = create_engine(f"sqlite:///{tmp_path / 'otd.db'}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    assert migrate_schema(engine) is False
    names = set(inspect(engine).get_table_names())
    names.discard("sqlite_sequence")
    assert names == EXPECTED_TABLES
    notes = Path("releases/v2026.10.25.5.md").read_text(encoding="utf-8")
    assert "v2026.10.25.4" in notes
    assert "零新表" in notes
    assert "down -v" in notes
    assert "DROP TABLE" not in notes
    assert "DROP COLUMN" not in notes
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert "## v2026.10.25.5" in changelog.split("## v2026.10.25.4", 1)[0]
    assert app.version == "2026.10.25.8"
    engine.dispose()


def test_on_this_day_empty_no_card(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    child_id = _family(client, "otd-empty@example.com", "妈妈", "空年签家")
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 200
    assert "amber-card" not in page.text
    assert "amber-wrap" not in page.text
    assert COPY["amber_title"] not in page.text
    assert COPY["none_note"] not in page.text
    assert COPY["leap_note"] not in page.text
    fresh = client.get("/api/families/current/on-this-day")
    assert fresh.status_code == 200
    target = _target(today).isoformat()
    assert fresh.json() == {
        "has_on_this_day": False,
        "target_date": target,
        "year": today.year - 1,
        "photo_count": 0,
        "journal_count": 0,
        "video_count": 0,
        "total": 0,
        "thumb_urls": [],
        "day_path": None,
    }
    assert fresh.headers["cache-control"] == "private, no-store"
    child_scope = client.get(f"/api/families/current/on-this-day?child_id={child_id}")
    assert child_scope.json()["has_on_this_day"] is False


def test_on_this_day_leap_day_empty(client: TestClient, monkeypatch):
    assert previous_year_date(date(2024, 2, 29)) is None
    assert previous_year_date(date(2028, 2, 29)) is None
    child_id = _family(client, "otd-leap@example.com", "妈妈", "闰年家")
    assert _photo(client, [child_id], "2024-02-29")["taken_on"] == "2024-02-29"
    _pin(monkeypatch, date(2028, 2, 29))
    payload = client.get("/api/families/current/on-this-day").json()
    assert payload["has_on_this_day"] is False
    assert payload["target_date"] is None
    assert payload["year"] is None
    assert payload["total"] == 0
    assert payload["day_path"] is None
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 200
    assert "amber-card" not in page.text
    assert COPY["amber_title"] not in page.text
    assert COPY["leap_note"] not in page.text
    _pin(monkeypatch, date(2025, 2, 28))
    _photo(client, [child_id], "2024-02-28")
    narrowed = client.get(f"/api/families/current/on-this-day?child_id={child_id}").json()
    assert narrowed["target_date"] == "2024-02-28"
    assert narrowed["has_on_this_day"] is True
    assert narrowed["photo_count"] == 1
    assert narrowed["day_path"] == f"/children/{child_id}/days/2024-02-28"


def test_on_this_day_matches_taken_on_and_on_date(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    target = _target(today)
    older = _target(today, 2)
    child_id = _family(client, "otd-match@example.com", "妈妈", "记录日家")
    photo = _photo(client, [child_id], target.isoformat())
    today_photo = _photo(client, [child_id], today.isoformat())
    _photo(client, [child_id], older.isoformat())
    self_photo = upload(client, kind="self", taken_on=target.isoformat(), send_child=False)
    assert self_photo.status_code == 201, self_photo.text
    kept = _journal(client, child_id, "去年留下的一句", target.isoformat())
    dropped = _journal(client, child_id, "这条会删掉", target.isoformat())
    _journal(client, child_id, "今年的一句", today.isoformat())
    _video(client, child_id, target.isoformat())
    _video(client, child_id, older.isoformat())
    _set_created(Photo, photo["id"], datetime(target.year, 1, 2, tzinfo=timezone.utc))
    _set_created(Photo, today_photo["id"], datetime(target.year, target.month, target.day, tzinfo=timezone.utc))
    assert client.delete(f"/api/journals/{dropped['id']}").status_code == 204
    payload = client.get(f"/api/families/current/on-this-day?child_id={child_id}").json()
    assert payload["target_date"] == target.isoformat()
    assert payload["photo_count"] == 1
    assert payload["journal_count"] == 1
    assert payload["video_count"] == 1
    assert payload["total"] == 3
    assert payload["thumb_urls"] == [photo["media_url"]]
    assert payload["has_on_this_day"] is True
    assert kept["id"] != dropped["id"]


def test_on_this_day_only_one_year_back(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    target = _target(today)
    older = _target(today, 2)
    child_id = _family(client, "otd-one@example.com", "妈妈", "只一年家")
    _photo(client, [child_id], older.isoformat())
    absent = client.get("/api/families/current/on-this-day").json()
    assert absent["has_on_this_day"] is False
    assert absent["target_date"] == target.isoformat()
    _photo(client, [child_id], target.isoformat())
    present = client.get("/api/families/current/on-this-day").json()
    assert present["photo_count"] == 1
    assert present["year"] == target.year


def test_on_this_day_server_shanghai_date(client: TestClient, monkeypatch):
    _pin(monkeypatch, date(2026, 4, 9))
    child_id = _family(client, "otd-clock@example.com", "妈妈", "服务端日家")
    _photo(client, [child_id], "2025-04-09")
    _photo(client, [child_id], "2026-04-09")
    payload = client.get("/api/families/current/on-this-day?today=1999-01-01&family_id=99999").json()
    assert payload["target_date"] == "2025-04-09"
    assert payload["year"] == 2025
    assert payload["photo_count"] == 1
    assert payload["has_on_this_day"] is True
    again = client.get("/api/families/current/on-this-day?family_id=1").json()
    assert again == payload


def test_on_this_day_card_and_day_navigation(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    target = _target(today)
    child_id = _family(client, "otd-nav@example.com", "妈妈", "年签家", child="果果")
    photos = [_photo(client, [child_id], target.isoformat()) for _ in range(5)]
    _journal(client, child_id, "公园第一次踩落叶", target.isoformat())
    _video(client, child_id, target.isoformat())
    viewer = _invite(client, "otd-nav-ro@example.com", "viewer", "奶奶")
    payload = viewer.get(f"/api/families/current/on-this-day?child_id={child_id}")
    assert payload.status_code == 200
    body = payload.json()
    assert body["photo_count"] == 5
    assert body["journal_count"] == 1
    assert body["video_count"] == 1
    assert body["total"] == 7
    assert body["thumb_urls"] == [item["media_url"] for item in photos[:4]]
    assert body["day_path"] == f"/children/{child_id}/days/{target.isoformat()}"
    page = viewer.get(f"/?child={child_id}")
    assert page.status_code == 200
    assert 'class="amber-card"' in page.text
    assert COPY["amber_title"] in page.text
    assert f"{target.month}月{target.day}日 · {target.year}" in page.text
    assert str(target.year) in page.text
    assert "图 5" in page.text
    assert "日志 1" in page.text
    assert "视频 1" in page.text
    assert body["day_path"] in page.text
    card = page.text[page.text.find('class="amber-card"') :]
    assert 0 <= card.find("amber-stamp") < card.find('class="ttl"')
    assert card.find('class="ttl"') < card.find('class="sub"')
    assert COPY["role_note_admin"] not in page.text
    assert COPY["url_demo"] not in page.text
    assert COPY["child_filter_note"] not in page.text
    assert "anno_" not in page.text
    day = viewer.get(body["day_path"])
    assert day.status_code == 200
    assert "公园第一次踩落叶" in day.text
    assert f"{target.year}年 · 去年今天" in day.text
    assert 'class="vcard' in day.text
    for item in photos:
        assert item["media_url"] in day.text
    fragment = viewer.get(f"/fragments/timeline?child={child_id}")
    assert fragment.status_code == 200
    assert 'class="amber-card"' in fragment.text
    assert body["day_path"] in fragment.text


def test_day_page_empty_after_delete(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    target = _target(today)
    child_id = _family(client, "otd-del@example.com", "妈妈", "删光家")
    created = _journal(client, child_id, "只剩这一句", target.isoformat())
    before = client.get(f"/api/families/current/on-this-day?child_id={child_id}").json()
    assert before["has_on_this_day"] is True
    assert before["journal_count"] == 1
    assert "amber-thumbs" not in client.get(f"/?child={child_id}").text
    assert client.delete(f"/api/journals/{created['id']}").status_code == 204
    after = client.get(f"/api/families/current/on-this-day?child_id={child_id}").json()
    assert after["has_on_this_day"] is False
    assert after["total"] == 0
    page = client.get(f"/?child={child_id}")
    assert "amber-card" not in page.text
    day = client.get(f"/children/{child_id}/days/{target.isoformat()}")
    assert day.status_code == 200
    assert COPY["day_empty_title"] in day.text
    assert COPY["day_empty_sub"] in day.text
    assert "只剩这一句" not in day.text


def test_roles_see_on_this_day(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    target = _target(today).isoformat()
    child_id = _family(client, "otd-role@example.com", "妈妈", "同路径家")
    _photo(client, [child_id], target)
    uploader = _invite(client, "otd-role-up@example.com", "uploader", "爸爸")
    viewer = _invite(client, "otd-role-ro@example.com", "viewer", "奶奶")
    seen = []
    for member in (client, uploader, viewer):
        payload = member.get("/api/families/current/on-this-day").json()
        page = member.get(f"/?child={child_id}")
        assert page.status_code == 200
        assert COPY["amber_title"] in page.text
        assert payload["day_path"] in page.text
        seen.append(payload)
    assert seen[0] == seen[1] == seen[2]
    assert seen[0]["has_on_this_day"] is True


def test_viewer_write_still_403(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    child_id = _family(client, "otd-ro@example.com", "妈妈", "只读写家")
    _photo(client, [child_id], _target(today).isoformat())
    viewer = _invite(client, "otd-ro-v@example.com", "viewer", "奶奶")
    denied = viewer.post(
        "/api/photos",
        data={"kind": "child", "child_id": str(child_id), "taken_on": today.isoformat()},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "photo_forbidden_role"
    journal = viewer.post(
        "/api/journals",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"child_id": child_id, "on_date": today.isoformat(), "body": "不能写", "photo_ids": []},
    )
    assert journal.status_code == 403
    assert journal.json()["code"] == "journal_readonly"
    video = viewer.post(
        "/api/videos",
        data={"child_id": str(child_id), "on_date": today.isoformat(), "duration_ms": "1000"},
        files={"file": ("clip.mp4", _mp4(), "video/mp4")},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert video.status_code == 403
    assert video.json()["code"] == "video_forbidden_role"
    read = viewer.get("/api/families/current/on-this-day")
    assert read.status_code == 200
    assert read.json()["has_on_this_day"] is True
    assert "code" not in read.json()


def test_outsider_on_this_day_403_no_code(client: TestClient):
    child_id = _family(client, "otd-out@example.com", "妈妈", "SecretOtdFam")
    _photo(client, [child_id], _target(_anchor_date()).isoformat())
    outsider = TestClient(app)
    auth(outsider, "otd-out-who@example.com", "外人")
    denied = outsider.get("/api/families/current/on-this-day")
    assert denied.status_code == 403
    assert denied.json() == {"detail": FORBIDDEN}
    assert "code" not in denied.json()
    assert "SecretOtdFam" not in denied.text
    anonymous = TestClient(app)
    anon = anonymous.get(f"/api/families/current/on-this-day?child_id={child_id}")
    assert anon.status_code == 403
    assert anon.json() == {"detail": FORBIDDEN}
    assert "code" not in anon.json()
    assert "SecretOtdFam" not in anon.text


def test_outsider_on_this_day_403(client: TestClient):
    _family(client, "otd-gate@example.com", "妈妈", "门禁年签家")
    outsider = TestClient(app)
    auth(outsider, "otd-gate-out@example.com", "路人")
    for actor in (outsider, TestClient(app)):
        read = actor.get("/api/families/current/on-this-day")
        assert read.status_code == 403
        assert read.json() == {"detail": "没有访问权限"}
        assert "code" not in read.json()


def test_child_filter_scope_on_this_day(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    target = _target(today).isoformat()
    elder = _family(client, "otd-kid@example.com", "妈妈", "两娃家", child="果果")
    younger = _child(client, "果果弟")
    other = TestClient(app)
    foreign = _family(other, "otd-kid-x@example.com", "别人", "别家", child="小外")
    _photo(client, [elder], target)
    _photo(client, [younger], target)
    _photo(client, [elder, younger], target)
    _journal(client, elder, "只写给哥哥", target)
    _video(client, younger, target)
    elder_body = client.get(f"/api/families/current/on-this-day?child_id={elder}").json()
    younger_body = client.get(f"/api/families/current/on-this-day?child_id={younger}").json()
    family_body = client.get("/api/families/current/on-this-day").json()
    assert elder_body["photo_count"] == 2
    assert elder_body["journal_count"] == 1
    assert elder_body["video_count"] == 0
    assert younger_body["photo_count"] == 2
    assert younger_body["journal_count"] == 0
    assert younger_body["video_count"] == 1
    assert family_body["photo_count"] == 3
    assert family_body["journal_count"] == 1
    assert family_body["video_count"] == 1
    assert family_body["total"] == 5
    assert elder_body["day_path"] == f"/children/{elder}/days/{target}"
    assert younger_body["day_path"] == f"/children/{younger}/days/{target}"
    assert family_body["day_path"] == f"/children/{elder}/days/{target}"
    elder_page = client.get(f"/?child={elder}")
    younger_page = client.get(f"/?child={younger}")
    family_page = client.get("/?view=family")
    assert "图 2" in elder_page.text
    assert "视频 1" not in elder_page.text
    assert "视频 1" in younger_page.text
    assert "日志 1" not in younger_page.text
    assert "图 3" in family_page.text
    assert family_body["day_path"] in family_page.text
    sneaky = client.get(f"/api/families/current/on-this-day?child_id={foreign}&family_id=1")
    assert sneaky.status_code == 403
    assert sneaky.json() == {"detail": FORBIDDEN}
    assert "code" not in sneaky.json()
    assert "别家" not in sneaky.text
    missing = client.get("/api/families/current/on-this-day?child_id=999999")
    assert missing.status_code == 403
    assert "code" not in missing.json()


def test_family_scope_aggregates(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    target = _target(today).isoformat()
    elder = _family(client, "otd-all@example.com", "妈妈", "合计家", child="姐姐")
    younger = _child(client, "弟弟")
    shared = _journal(client, elder, "写给两个", target, child_ids=[elder, younger])
    _photo(client, [younger], target)
    payload = client.get("/api/families/current/on-this-day").json()
    assert payload["journal_count"] == 1
    assert payload["photo_count"] == 1
    assert payload["total"] == 2
    assert client.get(f"/api/families/current/on-this-day?child_id={elder}").json()["journal_count"] == 1
    assert client.get(f"/api/families/current/on-this-day?child_id={younger}").json()["photo_count"] == 1
    assert shared["id"]


def test_whats_new_and_on_this_day_order(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    target = _target(today)
    child_id = _family(client, "otd-both@example.com", "妈妈", "两条家")
    _photo(client, [child_id], today.isoformat())
    last = _photo(client, [child_id], target.isoformat())
    _set_created(Photo, last["id"], utcnow() - timedelta(days=30))
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 200
    slip_at = page.text.find('class="wn-slip"')
    amber_at = page.text.find('class="amber-card"')
    assert 0 <= slip_at < amber_at
    between = page.text[slip_at:amber_at]
    assert "</details>" in between
    assert "amber-card" not in between
    assert COPY["slip_title"] in page.text or "最近有新内容" in page.text
    assert page.text.find("最近有新内容") < page.text.find(COPY["amber_title"])


def test_otd_slip_matrix_presence(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    target = _target(today)
    child_id = _family(client, "otd-matrix@example.com", "妈妈", "矩阵家")
    neither = client.get(f"/?child={child_id}")
    assert "wn-slip" not in neither.text
    assert "amber-card" not in neither.text
    assert COPY["amber_title"] not in neither.text

    only_year = _photo(client, [child_id], target.isoformat())
    _set_created(Photo, only_year["id"], utcnow() - timedelta(days=40))
    year_only = client.get(f"/?child={child_id}")
    assert "wn-slip" not in year_only.text
    assert "amber-card" in year_only.text

    _photo(client, [child_id], today.isoformat())
    both = client.get(f"/?child={child_id}")
    assert "wn-slip" in both.text
    assert "amber-card" in both.text
    assert both.text.find("wn-slip") < both.text.find("amber-card")


def test_otd_independent_of_whats_new_failure(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    child_id = _family(client, "otd-iso@example.com", "妈妈", "隔离家")
    _photo(client, [child_id], _target(today).isoformat())

    def boom(*_args, **_kwargs):
        raise RuntimeError("slip down")

    monkeypatch.setattr("app.routers.family.public_whats_new", boom)
    monkeypatch.setattr("app.routers.pages.build_slip", boom)
    with TestClient(app, raise_server_exceptions=False) as quiet:
        quiet.cookies.update(client.cookies)
        failed = quiet.get("/api/families/current/whats-new")
    assert failed.status_code == 500
    otd = client.get("/api/families/current/on-this-day")
    assert otd.status_code == 200
    assert otd.json()["has_on_this_day"] is True
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 200
    assert "amber-card" in page.text
    assert "wn-slip" not in page.text
    from tests.test_whats_new import _js as wn_js

    wn = wn_js("m.decideSlip(500, null, {left:false, generation:1, currentGeneration:1})")
    amber = _js(
        "m.decideOnThisDay(200, {has_on_this_day:true, total:1, day_path:'/children/1/days/2025-10-25'}, {left:false, generation:1, currentGeneration:1})"
    )
    pair = _js(
        "m.pairDecision({render:false}, {render:true})"
    )
    assert wn["render"] is False
    assert amber["render"] is True
    assert pair == {"slip": False, "amber": True, "merged": False}


def test_on_this_day_does_not_touch_last_seen(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    child_id = _family(client, "otd-seen@example.com", "妈妈", "不推进家")
    _photo(client, [child_id], _target(today).isoformat())
    ids = _me(client)
    before = _seen_stamp(ids["user_id"], ids["family_id"])
    assert client.get("/api/families/current/on-this-day").status_code == 200
    assert client.get(f"/?child={child_id}").status_code == 200
    assert client.get("/?view=family").status_code == 200
    assert _seen_stamp(ids["user_id"], ids["family_id"]) == before
    source = Path("app/services/on_this_day.py").read_text(encoding="utf-8")
    assert "mark_timeline_seen" not in source
    assert "TimelineLastSeen" not in source


def test_on_this_day_no_write_route(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    child_id = _family(client, "otd-write@example.com", "妈妈", "无写路由家")
    _photo(client, [child_id], _target(today).isoformat())
    ids = _me(client)
    before = _seen_stamp(ids["user_id"], ids["family_id"])
    posted = client.post("/api/families/current/on-this-day", json={"has_on_this_day": True})
    assert posted.status_code == 405
    put = client.put("/api/families/current/on-this-day", json={})
    assert put.status_code == 405
    assert _seen_stamp(ids["user_id"], ids["family_id"]) == before
    still = client.get("/api/families/current/on-this-day").json()
    assert still["has_on_this_day"] is True


def test_on_this_day_503_no_crash(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    child_id = _family(client, "otd-busy@example.com", "妈妈", "忙家")
    _photo(client, [child_id], today.isoformat())
    _photo(client, [child_id], _target(today).isoformat())

    def busy(*_args, **_kwargs):
        raise OnThisDayUnavailable()

    monkeypatch.setattr("app.services.on_this_day.on_this_day_for_feed", busy)
    failed = client.get("/api/families/current/on-this-day")
    assert failed.status_code == 503
    assert failed.json()["detail"] == "服务忙"
    assert failed.headers["cache-control"] == "private, no-store"
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 200
    assert "amber-card" not in page.text
    assert f"{today.month}月{today.day}日" in page.text
    assert client.get("/healthz").status_code == 200
    decision = _js("m.decideOnThisDay(503, null, {left:false, generation:1, currentGeneration:1})")
    assert decision["render"] is False
    assert decision["crash"] is False
    assert decision["retry"] is True
    assert decision["kind"] == "busy"


def test_on_this_day_network_error_no_card():
    decision = _js("m.decideOnThisDay(0, null, {left:false, generation:1, currentGeneration:1, settledEmpty:false})")
    assert decision == {
        "render": False,
        "ignore": False,
        "crash": False,
        "retry": True,
        "kind": "network",
    }
    html = _js("m.cardHtml({title:'去年今天'}, {has_on_this_day:false, total:0})")
    assert html == ""


def test_late_on_this_day_ignored():
    late = _js(
        "m.decideOnThisDay(200, {has_on_this_day:true, total:1, day_path:'/children/1/days/2025-10-25'}, {left:true, generation:1, currentGeneration:1})"
    )
    settled = _js(
        "m.decideOnThisDay(200, {has_on_this_day:true, total:1, day_path:'/children/1/days/2025-10-25'}, {settledEmpty:true, generation:2, currentGeneration:2})"
    )
    stale = _js(
        "m.decideOnThisDay(200, {has_on_this_day:true, total:1, day_path:'/children/1/days/2025-10-25'}, {generation:1, currentGeneration:2})"
    )
    assert late["ignore"] is True and late["render"] is False
    assert settled["ignore"] is True and settled["render"] is False
    assert stale["ignore"] is True and stale["render"] is False


def test_js_404_without_code_retryable_otd():
    rolling = _js("m.decideOnThisDay(404, {detail:'没找到'}, {left:false, generation:1, currentGeneration:1})")
    assert rolling["render"] is False
    assert rolling["retry"] is True
    assert rolling["kind"] == "rolling"
    assert rolling["crash"] is False
    coded = _js(
        "m.decideOnThisDay(404, {detail:'没有', code:'missing'}, {left:false, generation:1, currentGeneration:1})"
    )
    assert coded["kind"] != "rolling"
    assert coded["render"] is False


def test_removed_mid_timeline_403(client: TestClient, monkeypatch):
    today = _pin(monkeypatch, _anchor_date())
    child_id = _family(client, "otd-rm@example.com", "妈妈", "SecretRemovedFam")
    _photo(client, [child_id], _target(today).isoformat())
    ids = _me(client)
    db = SessionLocal()
    try:
        row = db.scalar(select(Membership).where(Membership.user_id == ids["user_id"]))
        assert row is not None
        db.delete(row)
        db.commit()
    finally:
        db.close()
    denied = client.get("/api/families/current/on-this-day")
    assert denied.status_code == 403
    assert denied.json() == {"detail": FORBIDDEN}
    assert "code" not in denied.json()
    assert "SecretRemovedFam" not in denied.text
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 403
    assert "amber-card" not in page.text
    assert "SecretRemovedFam" not in page.text
    home = client.get("/")
    assert "amber-card" not in home.text
    assert COPY["amber_title"] not in home.text
