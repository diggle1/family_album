"""长辈「最近有更新」v2026.10.25.4。日签条、时间窗、已读推进。"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.constants import ELDERS_WHATS_NEW
from app.db import SessionLocal
from app.main import app
from app.models import Journal, Membership, Photo, TimelineLastSeen
from app.services.clock import shanghai_today, utcnow
from app.services.whats_new import TimelineSeenUnavailable
from app.whats_new_copy import ELDERS_WHATS_NEW as COPY
from app.whats_new_copy import ELDERS_WHATS_NEW_ANNO as ANNO
from tests.test_acceptance import JPEG, auth, upload
from tests.test_journal import _family, _invite

MOCKUP = Path("design/family_album/elders-whats-new/final/mockup.html")
FINAL_MD5 = "ac2ceba835f45d368d82c98660a7c32d"
BANNED = (
    "会员",
    "开通",
    "商城",
    "发现",
    "广告",
    "摘要",
    "Feed",
    "动态流",
    "下载 App",
    "应用商店",
    "去商店",
)
SURFACES = (
    Path("app/whats_new_copy.py"),
    Path("app/services/whats_new.py"),
    Path("app/static/whats_new.js"),
    Path("app/templates/fragments/whats_new_slip.html"),
    Path("app/templates/fragments/stage_inner.html"),
    Path("app/templates/day.html"),
    Path("app/templates/layout.html"),
)
VISIBLE_PREFIXES = ("slip_", "row_", "chip_", "day_", "err_", "deny_", "tab_", "ro_", "load_", "seen_meta", "desk_")


def _person(admin: TestClient, email: str, role: str, name: str) -> TestClient:
    return _invite(admin, email, role, name)


def _me(client: TestClient) -> dict:
    payload = client.get("/api/me").json()
    family = client.get("/api/families/current").json()
    return {"user_id": payload["id"], "family_id": family["id"]}


def _set_created(model, row_id: int, when: datetime) -> None:
    db = SessionLocal()
    try:
        row = db.get(model, row_id)
        assert row is not None
        row.created_at = when
        db.commit()
    finally:
        db.close()


def _stamp_seen(user_id: int, family_id: int, when: datetime) -> None:
    db = SessionLocal()
    try:
        row = db.scalar(
            select(TimelineLastSeen).where(
                TimelineLastSeen.user_id == user_id,
                TimelineLastSeen.family_id == family_id,
            )
        )
        if row is None:
            db.add(TimelineLastSeen(user_id=user_id, family_id=family_id, last_seen_at=when))
        else:
            row.last_seen_at = when
        db.commit()
    finally:
        db.close()


def _seen_count(user_id: int, family_id: int) -> int:
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(TimelineLastSeen).where(
                TimelineLastSeen.user_id == user_id,
                TimelineLastSeen.family_id == family_id,
            )
        ).all()
        return len(rows)
    finally:
        db.close()


def _journal(client: TestClient, child_id: int, body: str, on_date: str) -> dict:
    response = client.post(
        "/api/journals",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"child_id": child_id, "on_date": on_date, "body": body, "photo_ids": []},
    )
    assert response.status_code == 201, response.text
    return response.json()


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


def _js(expr: str) -> dict:
    node = shutil.which("node")
    assert node
    script = "const m = require(process.argv[1]); process.stdout.write(JSON.stringify(" + expr + "));"
    completed = subprocess.run(
        [node, "-e", script, str(Path("app/static/whats_new.js").resolve())],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_copy_no_banned_words_whats_new():
    assert hashlib.md5(MOCKUP.read_bytes()).hexdigest() == FINAL_MD5
    assert hashlib.md5(Path("design/family_album/elders-no-app/final/mockup.html").read_bytes()).hexdigest() == (
        "3f41b99ba967c82e804dc80f29bd02be"
    )
    assert hashlib.md5(Path("design/family_album/photo-multi-child/final/mockup.html").read_bytes()).hexdigest() == (
        "f700e4de1b74ac0923745d91c63a0e20"
    )
    mockup = _mockup_copy()
    assert len(mockup) == 41
    anno = {key: value for key, value in mockup.items() if key.startswith("anno_")}
    product = {key: value for key, value in mockup.items() if not key.startswith("anno_")}
    assert anno == ANNO
    assert len(anno) == 5
    assert len(product) == 36
    assert list(product) == list(COPY)
    assert product == COPY
    assert COPY is ELDERS_WHATS_NEW
    visible = {key: value for key, value in COPY.items() if key.startswith(VISIBLE_PREFIXES) or key in {"seen_meta", "desk_aside"}}
    assert "slip_title" in visible and "seen_meta" in visible and "desk_aside" in visible
    blob = "\n".join(visible.values())
    for word in BANNED:
        assert word not in blob, word
        assert word not in "\n".join(COPY.values())
    for path in SURFACES:
        text = path.read_text(encoding="utf-8")
        for word in BANNED:
            assert word not in text, f"{word} in {path}"
    joined = "\n".join(path.read_text(encoding="utf-8") for path in SURFACES)
    for needle in ("微信", "wechat", "miniprogram", "小程序"):
        assert needle.lower() not in joined.lower()
    assert COPY["seen_note"] not in Path("app/templates/fragments/stage_inner.html").read_text(encoding="utf-8")
    assert COPY["seen_note"] not in Path("app/templates/fragments/whats_new_slip.html").read_text(encoding="utf-8")
    assert COPY["role_note_admin"] not in Path("app/templates/timeline.html").read_text(encoding="utf-8")
    assert "最近有新内容" not in Path("app/static/whats_new.js").read_text(encoding="utf-8")


def test_no_new_bottom_tab_copy():
    layout = Path("app/templates/layout.html").read_text(encoding="utf-8")
    assert layout.count('class="tab{') == 3
    tabs = [key for key in COPY if key.startswith("tab_")]
    assert tabs == ["tab_time", "tab_family", "tab_settings"]
    css = Path("app/static/app.css").read_text(encoding="utf-8")
    assert "@media (min-width: 1024px)" in css
    assert "min(720px, 100%)" in css
    assert "wn-rail-side" not in css
    assert "最近栏" not in css


def test_whats_new_empty_no_slip(client: TestClient):
    child_id = _family(client, "wn-empty@example.com", "妈妈", "空条家")
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 200
    assert 'class="slipbar"' not in page.text
    assert "wn-slip" not in page.text
    assert "sliplist" not in page.text
    assert COPY["seen_meta"] not in page.text
    assert COPY["seen_note"] not in page.text
    fresh = client.get("/api/families/current/whats-new")
    assert fresh.status_code == 200
    assert fresh.json() == {
        "has_updates": False,
        "window": "fallback_7d",
        "last_seen_at": None,
        "days": [],
    }
    assert fresh.headers["cache-control"] == "private, no-store"
    ids = _me(client)
    assert _seen_count(ids["user_id"], ids["family_id"]) == 0


def test_whats_new_slip_and_day_navigation(client: TestClient):
    child_id = _family(client, "wn-nav@example.com", "妈妈", "日签家", child="果果")
    today = shanghai_today().isoformat()
    photo = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert photo.status_code == 201, photo.text
    note = _journal(client, child_id, "今天幼儿园画了大太阳", today)
    viewer = _person(client, "wn-nav-ro@example.com", "viewer", "奶奶")
    page = viewer.get(f"/?child={child_id}")
    assert page.status_code == 200
    assert 'class="slipbar"' in page.text
    assert COPY["slip_title"] in page.text
    assert "图" in page.text and "日志" in page.text
    href = f"/children/{child_id}/days/{today}"
    assert href in page.text
    family_page = viewer.get("/?view=family")
    assert family_page.status_code == 200
    assert href in family_page.text
    assert 'class="slipbar"' in family_page.text
    day = viewer.get(href)
    assert day.status_code == 200
    assert f"/media/{photo.json()['id']}" in day.text
    assert note["body"].split("\n")[0] in day.text or "今天幼儿园画了大太阳" in day.text
    payload = viewer.get("/api/families/current/whats-new").json()
    assert payload["has_updates"] is True
    assert payload["window"] == "fallback_7d"
    assert payload["days"][0]["date"] == today
    assert payload["days"][0]["photo_count"] == 1
    assert payload["days"][0]["journal_count"] == 1
    assert payload["days"][0]["total"] == 2
    assert set(payload["days"][0]) == {"date", "photo_count", "journal_count", "video_count", "total"}


def test_roles_see_whats_new(client: TestClient):
    child_id = _family(client, "wn-roles@example.com", "妈妈", "同路家")
    today = shanghai_today().isoformat()
    assert upload(client, kind="child", child_id=child_id, taken_on=today).status_code == 201
    uploader = _person(client, "wn-up@example.com", "uploader", "爸爸")
    viewer = _person(client, "wn-view@example.com", "viewer", "奶奶")
    for actor in (client, uploader, viewer):
        payload = actor.get("/api/families/current/whats-new?family_id=999999")
        assert payload.status_code == 200
        assert payload.json()["has_updates"] is True
        assert payload.json()["days"][0]["photo_count"] == 1
        page = actor.get(f"/?child={child_id}")
        assert 'class="slipbar"' in page.text
        assert COPY["slip_title"] in page.text


def test_outsider_whats_new_403_no_code(client: TestClient):
    _family(client, "wn-in@example.com", "妈妈", "SecretFamWN")
    outsider = TestClient(app)
    auth(outsider, "wn-out@example.com", "外人")
    denied = outsider.get("/api/families/current/whats-new")
    assert denied.status_code == 403
    assert denied.json() == {"detail": "没有访问权限"}
    assert "code" not in denied.json()
    assert "SecretFamWN" not in denied.text
    anonymous = TestClient(app)
    assert anonymous.get("/api/families/current/whats-new").status_code == 403
    assert anonymous.get("/api/families/current/whats-new").json() == {"detail": "没有访问权限"}


def test_outsider_whats_new_and_seen_403(client: TestClient):
    _family(client, "wn-gate@example.com", "妈妈", "门禁家")
    outsider = TestClient(app)
    auth(outsider, "wn-gate-out@example.com", "外人")
    for actor in (outsider, TestClient(app)):
        read = actor.get("/api/families/current/whats-new")
        write = actor.post("/api/families/current/timeline-seen", json={})
        assert read.status_code == write.status_code == 403
        assert read.json() == write.json() == {"detail": "没有访问权限"}
        assert "code" not in read.json()


def test_viewer_write_still_403(client: TestClient):
    child_id = _family(client, "wn-ro@example.com", "妈妈", "只读家")
    viewer = _person(client, "wn-ro-v@example.com", "viewer", "奶奶")
    denied = viewer.post(
        "/api/photos",
        data={"kind": "child", "child_id": str(child_id), "taken_on": shanghai_today().isoformat()},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "photo_forbidden_role"
    assert viewer.get("/api/families/current/whats-new").status_code == 200
    seen = viewer.post("/api/families/current/timeline-seen", json={})
    assert seen.status_code == 200
    assert "last_seen_at" in seen.json()


def test_no_row_fallback_7d(client: TestClient):
    child_id = _family(client, "wn-7d@example.com", "妈妈", "七天家")
    recent = upload(client, kind="child", child_id=child_id, taken_on=shanghai_today().isoformat())
    old = upload(client, kind="child", child_id=child_id, taken_on="2024-08-01")
    assert recent.status_code == old.status_code == 201
    _set_created(Photo, recent.json()["id"], utcnow() - timedelta(days=2))
    _set_created(Photo, old.json()["id"], utcnow() - timedelta(days=10))
    payload = client.get("/api/families/current/whats-new").json()
    assert payload["window"] == "fallback_7d"
    assert payload["last_seen_at"] is None
    assert [day["date"] for day in payload["days"]] == [shanghai_today().isoformat()]
    page = client.get(f"/?child={child_id}")
    assert COPY["slip_sub_7d"] in page.text


def test_since_seen_uses_created_at(client: TestClient):
    child_id = _family(client, "wn-since@example.com", "妈妈", "入库家")
    ids = _me(client)
    stale = upload(client, kind="child", child_id=child_id, taken_on=shanghai_today().isoformat())
    fresh = upload(client, kind="child", child_id=child_id, taken_on="2024-08-01")
    _set_created(Photo, stale.json()["id"], utcnow() - timedelta(days=3))
    _set_created(Photo, fresh.json()["id"], utcnow() - timedelta(hours=1))
    _stamp_seen(ids["user_id"], ids["family_id"], utcnow() - timedelta(days=1))
    payload = client.get("/api/families/current/whats-new").json()
    assert payload["window"] == "since_seen"
    assert payload["last_seen_at"]
    assert [day["date"] for day in payload["days"]] == ["2024-08-01"]
    assert payload["days"][0]["photo_count"] == 1


def test_aggregate_by_record_day_not_upload_day(client: TestClient):
    child_id = _family(client, "wn-day@example.com", "妈妈", "记录日家")
    created = upload(client, kind="child", child_id=child_id, taken_on="2024-08-01")
    assert created.status_code == 201
    payload = client.get("/api/families/current/whats-new").json()
    assert payload["days"][0]["date"] == "2024-08-01"
    assert payload["days"][0]["date"] != shanghai_today().isoformat()
    page = client.get(f"/?child={child_id}")
    assert "/children/" + str(child_id) + "/days/2024-08-01" in page.text


def test_clock_skew_fallback_7d(client: TestClient):
    child_id = _family(client, "wn-skew@example.com", "妈妈", "回拨家")
    ids = _me(client)
    created = upload(client, kind="child", child_id=child_id, taken_on=shanghai_today().isoformat())
    assert created.status_code == 201
    _stamp_seen(ids["user_id"], ids["family_id"], utcnow() + timedelta(days=2))
    payload = client.get("/api/families/current/whats-new").json()
    assert payload["window"] == "fallback_7d"
    assert payload["last_seen_at"] is None
    assert payload["has_updates"] is True
    page = client.get(f"/?child={child_id}")
    assert COPY["slip_sub_7d"] in page.text
    assert page.status_code == 200


def test_future_created_at_ignored(client: TestClient):
    child_id = _family(client, "wn-future@example.com", "妈妈", "未来家")
    created = upload(client, kind="child", child_id=child_id, taken_on=shanghai_today().isoformat())
    _set_created(Photo, created.json()["id"], utcnow() + timedelta(days=2))
    payload = client.get("/api/families/current/whats-new").json()
    assert payload["has_updates"] is False
    assert payload["days"] == []
    page = client.get(f"/?child={child_id}")
    assert 'class="slipbar"' not in page.text


def test_timeline_seen_clears_slip_on_reopen(client: TestClient):
    child_id = _family(client, "wn-reopen@example.com", "妈妈", "再进家")
    assert upload(client, kind="child", child_id=child_id, taken_on=shanghai_today().isoformat()).status_code == 201
    first = client.get("/api/families/current/whats-new")
    assert first.json()["has_updates"] is True
    assert 'class="slipbar"' in client.get(f"/?child={child_id}").text
    seen = client.post("/api/families/current/timeline-seen")
    assert seen.status_code == 200
    again = client.post("/api/families/current/timeline-seen", json={})
    assert again.status_code == 200
    assert again.json()["last_seen_at"] >= seen.json()["last_seen_at"]
    second = client.get("/api/families/current/whats-new")
    assert second.json()["has_updates"] is False
    assert second.json()["window"] == "since_seen"
    page = client.get(f"/?child={child_id}")
    assert 'class="slipbar"' not in page.text
    ids = _me(client)
    assert _seen_count(ids["user_id"], ids["family_id"]) == 1


def test_seen_before_get_eats_slip(client: TestClient):
    child_id = _family(client, "wn-order@example.com", "妈妈", "顺序家")
    assert upload(client, kind="child", child_id=child_id, taken_on=shanghai_today().isoformat()).status_code == 201
    early = client.post("/api/families/current/timeline-seen", json={"family_id": 999999})
    assert early.status_code == 200
    eaten = client.get("/api/families/current/whats-new")
    assert eaten.json()["has_updates"] is False
    assert 'class="slipbar"' not in client.get(f"/?child={child_id}").text


def test_day_page_empty_after_delete(client: TestClient):
    child_id = _family(client, "wn-empty-day@example.com", "妈妈", "空日家")
    today = shanghai_today().isoformat()
    created = _journal(client, child_id, "这一句会删掉", today)
    removed = client.delete(f"/api/journals/{created['id']}")
    assert removed.status_code == 204
    page = client.get(f"/children/{child_id}/days/{today}")
    assert page.status_code == 200
    assert COPY["day_empty_title"] in page.text
    assert COPY["day_empty_sub"] in page.text
    assert "<html" in page.text
    assert "这一句会删掉" not in page.text


def test_removed_mid_timeline_403(client: TestClient):
    child_id = _family(client, "wn-remove@example.com", "妈妈", "移出家")
    viewer = _person(client, "wn-remove-v@example.com", "viewer", "奶奶")
    assert upload(client, kind="child", child_id=child_id, taken_on=shanghai_today().isoformat()).status_code == 201
    assert viewer.get("/api/families/current/whats-new").status_code == 200
    ids = _me(viewer)
    db = SessionLocal()
    try:
        row = db.scalar(select(Membership).where(Membership.user_id == ids["user_id"]))
        assert row is not None
        db.delete(row)
        db.commit()
    finally:
        db.close()
    denied = viewer.get("/api/families/current/whats-new")
    assert denied.status_code == 403
    assert denied.json() == {"detail": "没有访问权限"}
    assert "code" not in denied.json()
    page = viewer.get(f"/?child={child_id}")
    assert page.status_code == 403
    decision = _js(
        "m.decideSlip(403, {detail:'没有访问权限'}, {left:false, generation:1, currentGeneration:1, settledEmpty:false})"
    )
    assert decision["render"] is False
    assert decision["postSeen"] is False


def test_whats_new_network_error_no_slip():
    decision = _js("m.decideSlip(0, null, {left:false, generation:1, currentGeneration:1, settledEmpty:false})")
    assert decision["render"] is False
    assert decision["ignore"] is False
    assert decision["kind"] == "network"
    source = Path("app/static/whats_new.js").read_text(encoding="utf-8")
    assert "xhr.abort" in source
    assert 'addEventListener("pagehide"' in source


def test_late_whats_new_ignored():
    late = _js(
        "m.decideSlip(200, {has_updates:true, days:[{date:'2026-10-24', total:1}]}, {left:true, generation:1, currentGeneration:1})"
    )
    settled = _js(
        "m.decideSlip(200, {has_updates:true, days:[{date:'2026-10-24', total:1}]}, {settledEmpty:true, generation:2, currentGeneration:2})"
    )
    stale = _js(
        "m.decideSlip(200, {has_updates:true, days:[{date:'2026-10-24', total:1}]}, {generation:1, currentGeneration:2})"
    )
    assert late["ignore"] and settled["ignore"] and stale["ignore"]
    assert late["render"] is settled["render"] is stale["render"] is False


def test_js_422_mapped_timeline_seen():
    outcome = _js("m.seenOutcome(422, {detail:'请求无法完成', code:'timeline_seen_body_invalid'})")
    assert outcome["ok"] is False
    assert outcome["retry"] is True
    assert outcome["crash"] is False
    source = Path("app/static/whats_new.js").read_text(encoding="utf-8")
    mapped = source.split("function seenOutcome", 1)[1].split("function wnFill", 1)[0]
    assert mapped.index("status === 422") < mapped.index("retry: true")


def test_js_404_without_code_retryable_seen():
    rolling = _js("m.seenOutcome(404, {detail:'missing'})")
    coded = _js("m.seenOutcome(404, {detail:'missing', code:'timeline_seen_missing'})")
    assert rolling["retry"] is True and rolling["ok"] is False and rolling["crash"] is False
    assert coded["retry"] is False
    source = Path("app/static/whats_new.js").read_text(encoding="utf-8")
    mapped = source.split("function seenOutcome", 1)[1].split("function wnFill", 1)[0]
    assert "status === 404 && !(data && data.code)" in mapped


def test_timeline_seen_503_no_crash(client: TestClient, monkeypatch):
    child_id = _family(client, "wn-503@example.com", "妈妈", "忙家")
    def boom(*_args, **_kwargs):
        raise TimelineSeenUnavailable()

    monkeypatch.setattr("app.routers.family.mark_timeline_seen", boom)
    busy = client.post("/api/families/current/timeline-seen", json={})
    assert busy.status_code == 503
    assert busy.json()["detail"] == "服务忙"
    assert "last_seen_at" not in busy.json()
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 200
    outcome = _js("m.seenOutcome(503, {detail:'服务忙'})")
    assert outcome["crash"] is False
    assert outcome["ok"] is False
    assert outcome["retry"] is True


def test_parallel_timeline_seen_idempotent(client: TestClient):
    _family(client, "wn-race@example.com", "妈妈", "并发家")
    other = TestClient(app)
    other.cookies.update(client.cookies)
    barrier = threading.Barrier(2)
    results: list[int] = []

    def go(actor: TestClient) -> None:
        barrier.wait(5)
        results.append(actor.post("/api/families/current/timeline-seen", json={}).status_code)

    threads = [threading.Thread(target=go, args=(client,)), threading.Thread(target=go, args=(other,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert results == [200, 200]
    ids = _me(client)
    assert _seen_count(ids["user_id"], ids["family_id"]) == 1


def test_csrf_origin_rejects_timeline_seen(client: TestClient):
    _family(client, "wn-csrf@example.com", "妈妈", "跨站家")
    denied = client.post(
        "/api/families/current/timeline-seen",
        json={},
        headers={"Origin": "https://evil.example"},
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "没有访问权限"}
    assert "code" not in denied.json()


def test_timeline_seen_rejects_garbage_body(client: TestClient):
    _family(client, "wn-body@example.com", "妈妈", "乱体家")
    garbage = client.post(
        "/api/families/current/timeline-seen",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert garbage.status_code == 422
    assert garbage.json()["code"] == "timeline_seen_body_invalid"
    listed = client.post("/api/families/current/timeline-seen", json=["nope"])
    assert listed.status_code == 422
    extra = client.post("/api/families/current/timeline-seen", json={"extra": 1})
    assert extra.status_code == 422
    page = client.get("/")
    assert page.status_code == 200


def test_self_photo_counts_and_deleted_journal_does_not(client: TestClient):
    child_id = _family(client, "wn-self@example.com", "妈妈", "本人条家")
    today = shanghai_today().isoformat()
    self_photo = upload(client, kind="self", taken_on=today, send_child=False)
    assert self_photo.status_code == 201
    note = _journal(client, child_id, "待删的一句", today)
    assert client.delete(f"/api/journals/{note['id']}").status_code == 204
    payload = client.get("/api/families/current/whats-new").json()
    assert payload["days"][0]["photo_count"] == 1
    assert payload["days"][0]["journal_count"] == 0
    assert payload["days"][0]["video_count"] == 0
    db = SessionLocal()
    try:
        assert db.scalar(select(Journal).where(Journal.id == note["id"])).deleted_at is not None
    finally:
        db.close()
