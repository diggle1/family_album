"""Growth journal behavior, failure paths, and the D-J7 export empty state."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import threading
import uuid
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.constants import JOURNAL
from app.db import SessionLocal
from app.main import app
from app.models import Child, Journal, JournalPhoto, Membership, Photo
from app.services.clock import age_label, shanghai_today
from app.services.journals import body_len, is_blank
from tests.test_acceptance import JPEG, auth, upload

SAMPLE = "果果\U0001F476会走路了\n第一步\r\n\U0001F389e\u0301\U0001F468\u200d\U0001F469\u200d\U0001F467"
SAMPLES = (
    SAMPLE,
    "\U0001F476" * 2000,
    "\U0001F476" * 2000 + "\r\n",
    "字" * 2001,
    "a\r\rb",
    "\r\n\r\n",
)
EXPECTED_LENS = (18, 2000, 2000, 2001, 2, 0)
BANNED = ("会员", "广告", "发现", "发到时光线", "发动态", "朋友圈")
DEPRECATED = (
    "e_gone_pick_title",
    "e_gone_keep_edit",
    "e_gone_keep_new",
    "d_kid_gone",
    "d_gone_title",
    "draft_undiscard",
    "draft_banner",
    "del_body",
    "child_gone",
    "noaccess",
    "del_fail",
    "draft_discarded",
)


def _family(client: TestClient, email: str, name: str, family: str, child: str = "果果") -> int:
    auth(client, email, name)
    assert client.post("/api/families", json={"name": family}).status_code == 201
    created = client.post("/api/children", json={"name": child, "birth_date": "2024-07-12"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _invite(admin: TestClient, email: str, role: str, name: str) -> TestClient:
    token = admin.post("/api/families/invites", json={"email": email, "role": role}).json()["token"]
    member = TestClient(app)
    auth(member, email, name)
    assert member.post(f"/api/invites/{token}/accept").status_code == 200
    return member


def _clone(client: TestClient) -> TestClient:
    other = TestClient(app)
    other.cookies.update(client.cookies)
    return other


def _post(
    client: TestClient,
    child_id: int,
    body: str = "今天学会走路了",
    on_date: str | None = None,
    photos: list[int] | None = None,
    key: str | None = None,
    extra_headers: dict | None = None,
):
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if extra_headers:
        headers.update(extra_headers)
    return client.post(
        "/api/journals",
        headers=headers,
        json={
            "child_id": child_id,
            "on_date": on_date or shanghai_today().isoformat(),
            "body": body,
            "photo_ids": photos or [],
        },
    )


def _put(client: TestClient, journal_id: int, child_id: int, body: str, on_date: str, photos: list[int]):
    return client.put(
        f"/api/journals/{journal_id}",
        json={"child_id": child_id, "on_date": on_date, "body": body, "photo_ids": photos},
    )


def _names(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.namelist()


def _stored(content: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.read(name)


def test_body_len_server_cases():
    assert tuple(body_len(sample) for sample in SAMPLES) == EXPECTED_LENS
    assert is_blank(" \u3000\t\n") is True
    assert is_blank(SAMPLES[5]) is True
    assert is_blank(SAMPLE) is False


def test_js_body_len_matches_python():
    source = Path("app/static/journal.js").read_text(encoding="utf-8")
    assert 'Array.from(s.replace(/[\\r\\n]/g, "")).length' in source
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    script = (
        "const fs = require('fs');"
        "const m = require(process.argv[1]);"
        "const cases = JSON.parse(fs.readFileSync(0, 'utf8'));"
        "process.stdout.write(JSON.stringify(cases.map((s) => [m.bodyLen(s), m.isBlank(s)])));"
    )
    completed = subprocess.run(
        [node, "-e", script, str(Path("app/static/journal.js").resolve())],
        input=json.dumps(list(SAMPLES) + [" \u3000\t\n"]),
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(completed.stdout)
    for sample, (length, blank) in zip(SAMPLES, parsed[:-1], strict=True):
        assert length == body_len(sample)
        assert blank is is_blank(sample)
    assert parsed[-1] == [body_len(" \u3000\t\n"), True]


def test_js_never_uses_utf16_length_on_body():
    source = Path("app/static/journal.js").read_text(encoding="utf-8")
    page = Path("app/templates/journal_write.html").read_text(encoding="utf-8")
    counter = source.split("function bodyLen", 1)[1].split("function isBlank", 1)[0]
    assert 'Array.from(s.replace(/[\\r\\n]/g, "")).length' in counter
    assert "body.length" not in counter
    assert "value.length" not in counter
    assert "body.length" not in source
    assert "value.length" not in source
    assert "maxlength" not in page.lower()


def test_draft_undiscard_restores_within_4s():
    source = Path("app/static/journal.js").read_text(encoding="utf-8")
    assert "pendingDiscard" in source
    assert "4000" in source
    assert "localStorage.setItem(pendingDiscard.key, pendingDiscard.value)" in source
    assert "20000" in source


def test_js_404_without_code_is_retryable():
    source = Path("app/static/journal.js").read_text(encoding="utf-8")
    assert "status === 404 && !(data && data.code)" in source
    assert "journal_not_found" in source


def test_logout_hook_clears_journal_draft_prefix():
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    settings = Path("app/templates/settings.html").read_text(encoding="utf-8")
    assert "data-logout" in settings
    chunk = script.split('form.hasAttribute("data-logout")', 1)[1].split('if (form.id', 1)[0]
    assert "kids.journal.draft." in chunk
    assert "preventDefault" not in chunk
    assert "Clear-Site-Data" not in script


def test_draft_keys_scoped_by_family_user_and_id(client: TestClient):
    source = Path("app/static/journal.js").read_text(encoding="utf-8")
    assert 'DRAFT_PREFIX + fid + "." + uid + ".new"' in source
    assert 'DRAFT_PREFIX + fid + "." + uid + ".edit."' in source
    assert source.count("kids.journal.draft.") >= 1
    child_id = _family(client, "draft@example.com", "小美", "草稿家")
    me = client.get("/api/me").json()
    family = client.get("/api/families/current").json()
    page = client.get(f"/journals/new?child={child_id}&fid=999&uid=999")
    assert page.status_code == 200, page.text
    assert f'data-fid="{family["id"]}"' in page.text
    assert f'data-uid="{me["id"]}"' in page.text
    assert 'data-fid="999"' not in page.text
    assert 'data-uid="999"' not in page.text


def test_emoji_crlf_body_accepted_and_counted(client: TestClient):
    child_id = _family(client, "emoji@example.com", "小美", "表情家")
    created = _post(client, child_id, body=SAMPLE)
    assert created.status_code == 201, created.text
    body = created.json()["body"]
    assert "\r" not in body
    assert "\n" in body
    assert body_len(body) == 18


def test_body_over_2000_codepoints_400(client: TestClient):
    child_id = _family(client, "long@example.com", "小美", "长文家")
    ok = _post(client, child_id, body="\U0001F476" * 2000)
    assert ok.status_code == 201, ok.text
    assert body_len(ok.json()["body"]) == 2000
    over = _post(client, child_id, body="字" * 2001)
    assert over.status_code == 400
    assert over.json()["code"] == "body_too_long"
    assert over.json()["count"] == 2001
    assert over.json()["max"] == 2000
    assert "no-store" in over.headers["cache-control"]


def test_validation_codes_in_order(client: TestClient):
    child_id = _family(client, "valid@example.com", "小美", "校验家")
    today = shanghai_today()
    photo = upload(client, kind="child", child_id=child_id, taken_on=today.isoformat())
    assert photo.status_code == 201
    photo_id = photo.json()["id"]
    future = (today + timedelta(days=1)).isoformat()
    cases = [
        ({"body": 1, "child_id": child_id, "on_date": today.isoformat()}, "body_invalid"),
        ({"body": " \u3000\n", "on_date": "not-a-date"}, "body_empty"),
        ({"body": "字" * 2001}, "body_too_long"),
        ({"body": "有字", "on_date": today.isoformat()}, "child_required"),
        ({"body": "有字", "child_id": None, "on_date": today.isoformat()}, "child_required"),
        ({"body": "有字", "child_id": 999999, "on_date": today.isoformat()}, "child_not_found"),
        ({"body": "有字", "child_id": child_id, "on_date": "20261024"}, "date_invalid"),
        ({"body": "有字", "child_id": child_id, "on_date": future}, "date_future"),
        ({"body": "有字", "child_id": child_id, "on_date": "2024-07-11"}, "date_before_birth"),
        ({"body": "有字", "child_id": child_id, "on_date": today.isoformat(), "photo_ids": list(range(1, 11))}, "too_many_photos"),
        ({"body": "有字", "child_id": child_id, "on_date": today.isoformat(), "photo_ids": [photo_id, photo_id]}, "photo_duplicate"),
    ]
    for payload, code in cases:
        response = client.post(
            "/api/journals",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json=payload,
        )
        assert response.status_code == 400, payload
        assert response.json()["code"] == code, response.text


def test_create_with_two_photos_shows_on_timeline_with_age(client: TestClient):
    child_id = _family(client, "age@example.com", "妈妈", "果果家")
    today = shanghai_today().isoformat()
    first = upload(client, kind="child", child_id=child_id, taken_on=today)
    second = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert first.status_code == second.status_code == 201
    created = _post(client, child_id, body="第一句\n第二句", photos=[first.json()["id"], second.json()["id"]], on_date=today)
    assert created.status_code == 201, created.text
    label = age_label(date(2024, 7, 12), shanghai_today())
    assert created.json()["age_label"] == label
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 200
    assert "第一句" in page.text
    assert label in page.text
    assert f"/media/{first.json()['id']}" in page.text
    assert f"/media/{second.json()['id']}" in page.text
    assert "write-pill" in page.text


def test_feed_mixes_journal_before_photos_same_day(client: TestClient):
    child_id = _family(client, "mix@example.com", "妈妈", "混排家")
    today = shanghai_today().isoformat()
    photo = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert photo.status_code == 201
    created = _post(client, child_id, body="日志在照片前", on_date=today, photos=[photo.json()["id"]])
    assert created.status_code == 201
    page = client.get(f"/?child={child_id}")
    assert page.text.index(f"/journals/{created.json()['id']}") < page.text.index(f"/photos/{photo.json()['id']}")


def test_timeline_same_day_journals_desc(client: TestClient):
    child_id = _family(client, "order@example.com", "妈妈", "顺序家")
    today = shanghai_today().isoformat()
    photo = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert photo.status_code == 201
    first = _post(client, child_id, body="第一条甲", on_date=today)
    second = _post(client, child_id, body="第二条乙", on_date=today)
    assert first.status_code == second.status_code == 201
    page = client.get(f"/?child={child_id}").text
    assert page.index("第二条乙") < page.index("第一条甲")
    assert page.index("第一条甲") < page.index('class="pics')


def test_card_first_three_lines_and_cover(client: TestClient):
    child_id = _family(client, "card@example.com", "妈妈", "卡片家")
    today = shanghai_today().isoformat()
    photos = []
    for index in range(4):
        uploaded = upload(client, kind="child", child_id=child_id, taken_on=today, filename=f"{index}.jpg")
        assert uploaded.status_code == 201
        photos.append(uploaded.json()["id"])
    created = _post(client, child_id, body="第一句\n第二句\n第三句\n第四句不会上卡", photos=photos, on_date=today)
    assert created.status_code == 201
    listed = client.get(f"/api/journals?child_id={child_id}")
    assert listed.status_code == 200
    summary = listed.json()["journals"][0]
    assert summary["first_lines"] == ["第一句", "第二句", "第三句"]
    assert summary["cover"]["id"] == photos[0]
    assert summary["photo_count_valid"] == 4
    assert "email" not in summary["author"]
    page = client.get(f"/?child={child_id}").text
    assert "第一句" in page and "第二句" in page and "第三句" in page
    assert "第四句不会上卡" not in page
    assert "+1" in page


def test_detail_line_and_photo_links(client: TestClient):
    child_id = _family(client, "detail@example.com", "妈妈", "详情家")
    today = shanghai_today().isoformat()
    photo = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert photo.status_code == 201
    photo_id = photo.json()["id"]
    created = _post(client, child_id, body="全文在这里\n换行还在", photos=[photo_id], on_date=today)
    assert created.status_code == 201
    journal_id = created.json()["id"]
    page = client.get(f"/journals/{journal_id}")
    assert page.status_code == 200
    assert "全文在这里" in page.text
    assert f'href="/photos/{photo_id}"' in page.text
    assert "妈妈写 · 写给果果" in page.text
    assert "no-store" in page.headers["cache-control"]


def test_api_timeline_json_unchanged(client: TestClient):
    child_id = _family(client, "json@example.com", "妈妈", "接口家")
    today = shanghai_today().isoformat()
    photo = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert photo.status_code == 201
    assert _post(client, child_id, on_date=today).status_code == 201
    payload = client.get(f"/api/children/{child_id}/timeline").json()
    assert set(payload) == {"child", "days"}
    assert set(payload["child"]) == {"id", "name", "birth_date", "age_label", "photo_count"}
    assert payload["days"]
    assert set(payload["days"][0]) == {"taken_on", "date_label", "weekday", "age_label", "photos", "byline"}
    assert "journals" not in payload["days"][0]


def test_create_retry_same_key_creates_one(client: TestClient):
    child_id = _family(client, "retry@example.com", "妈妈", "重试家")
    key = str(uuid.uuid4())
    first = _post(client, child_id, body="第一次", key=key)
    second = _post(client, child_id, body="第一次", key=key)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert first.json()["id"] == second.json()["id"]
    listed = client.get(f"/api/journals?child_id={child_id}").json()
    assert len(listed["journals"]) == 1


def test_double_post_same_key_one_row(client: TestClient):
    child_id = _family(client, "double@example.com", "妈妈", "双击家")
    key = str(uuid.uuid4())
    statuses = [_post(client, child_id, key=key).status_code for _ in range(2)]
    assert statuses == [201, 200]
    assert len(client.get(f"/api/journals?child_id={child_id}").json()["journals"]) == 1


def test_parallel_same_key_one_row(client: TestClient):
    child_id = _family(client, "parj@example.com", "妈妈", "并发家")
    other = _clone(client)
    key = str(uuid.uuid4())
    barrier = threading.Barrier(2)
    results: list = []

    def go(actor: TestClient) -> None:
        barrier.wait(5)
        results.append(_post(actor, child_id, body="同时发出", key=key))

    threads = [threading.Thread(target=go, args=(actor,)) for actor in (client, other)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(item.status_code for item in results) == [200, 201]
    assert len(client.get(f"/api/journals?child_id={child_id}").json()["journals"]) == 1


def test_replay_with_new_body_updates_same_row(client: TestClient):
    child_id = _family(client, "replay@example.com", "妈妈", "重放家")
    key = str(uuid.uuid4())
    first = _post(client, child_id, body="旧正文", key=key)
    second = _post(client, child_id, body="新正文", key=key)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["body"] == "新正文"
    assert second.json()["replayed"] is True


def test_post_with_spent_key_is_409(client: TestClient):
    child_id = _family(client, "spent@example.com", "妈妈", "墓碑家")
    key = str(uuid.uuid4())
    created = _post(client, child_id, key=key)
    assert created.status_code == 201
    assert client.delete(f"/api/journals/{created.json()['id']}").status_code == 204
    again = _post(client, child_id, body="再用旧键", key=key)
    assert again.status_code == 409
    assert again.json()["code"] == "journal_key_spent"
    assert len(client.get(f"/api/journals?child_id={child_id}").json()["journals"]) == 0


def test_idempotency_across_two_clients(client: TestClient):
    child_id = _family(client, "two@example.com", "妈妈", "双端家")
    other = _clone(client)
    key = str(uuid.uuid4())
    first = _post(client, child_id, body="甲端", key=key)
    second = _post(other, child_id, body="乙端", key=key)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["body"] == "乙端"
    assert len(client.get(f"/api/journals?child_id={child_id}").json()["journals"]) == 1


def test_put_replaces_all_fields_atomically(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    child_id = _family(client, "atom@example.com", "妈妈", "原子家")
    today = shanghai_today()
    older = (today - timedelta(days=1)).isoformat()
    photo_a = upload(client, kind="child", child_id=child_id, taken_on=older)
    photo_b = upload(client, kind="child", child_id=child_id, taken_on=today.isoformat())
    assert photo_a.status_code == photo_b.status_code == 201
    created = _post(client, child_id, body="旧正文", on_date=older, photos=[photo_a.json()["id"]])
    assert created.status_code == 201
    journal_id = created.json()["id"]
    monkeypatch.setenv("JOURNAL_FAIL_PHOTO_REPLACE", "1")
    with pytest.raises(RuntimeError, match="journal photo replace failed"):
        _put(client, journal_id, child_id, "新正文", today.isoformat(), [photo_b.json()["id"]])
    monkeypatch.delenv("JOURNAL_FAIL_PHOTO_REPLACE", raising=False)
    current = client.get(f"/api/journals/{journal_id}")
    assert current.status_code == 200
    body = current.json()
    assert body["body"] == "旧正文"
    assert body["on_date"] == older
    assert [slot["id"] for slot in body["photos"]] == [photo_a.json()["id"]]


def test_two_puts_last_write_wins(client: TestClient):
    child_id = _family(client, "last@example.com", "妈妈", "后写家")
    today = shanghai_today().isoformat()
    created = _post(client, child_id, body="先写", on_date=today)
    journal_id = created.json()["id"]
    assert _put(client, journal_id, child_id, "中间", today, []).status_code == 200
    last = _put(client, journal_id, child_id, "后写覆盖", today, [])
    assert last.status_code == 200
    assert last.json()["body"] == "后写覆盖"
    assert client.get(f"/api/journals/{journal_id}").json()["body"] == "后写覆盖"


def test_put_deleted_journal_is_404_same_as_missing(client: TestClient):
    child_id = _family(client, "gone@example.com", "妈妈", "已删家")
    other = TestClient(app)
    foreign_child = _family(other, "gone-b@example.com", "别人", "别家")
    foreign = _post(other, foreign_child, body="别家日志")
    assert foreign.status_code == 201
    created = _post(client, child_id, body="将被删")
    assert created.status_code == 201
    journal_id = created.json()["id"]
    assert client.delete(f"/api/journals/{journal_id}").status_code == 204
    payload = {"child_id": child_id, "on_date": "not-a-date", "body": "", "photo_ids": []}
    deleted = client.put(f"/api/journals/{journal_id}", json=payload)
    missing = client.put("/api/journals/999999", json=payload)
    foreign_res = client.put(f"/api/journals/{foreign.json()['id']}", json=payload)
    assert deleted.status_code == missing.status_code == foreign_res.status_code == 404
    assert deleted.content == missing.content == foreign_res.content
    assert deleted.json()["code"] == "journal_not_found"
    assert "no-store" in deleted.headers["cache-control"]


def test_delete_twice_second_is_404(client: TestClient):
    child_id = _family(client, "twice@example.com", "妈妈", "再删家")
    created = _post(client, child_id)
    journal_id = created.json()["id"]
    assert client.delete(f"/api/journals/{journal_id}").status_code == 204
    second = client.delete(f"/api/journals/{journal_id}")
    missing = client.delete("/api/journals/999999")
    assert second.status_code == 404
    assert second.content == missing.content


def test_delete_keeps_photos_and_clears_text(client: TestClient):
    child_id = _family(client, "keep@example.com", "妈妈", "留照片家")
    today = shanghai_today().isoformat()
    uploaded = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert uploaded.status_code == 201
    photo_id = uploaded.json()["id"]
    created = _post(client, child_id, body="删字留图", photos=[photo_id], on_date=today)
    journal_id = created.json()["id"]
    session = SessionLocal()
    try:
        photo = session.get(Photo, photo_id)
        assert photo is not None
        path = Path(photo.storage_path)
        from app.config import get_settings

        full = get_settings().photo_dir / path
        before = full.read_bytes()
        photo_count = session.scalar(select(func.count()).select_from(Photo))
    finally:
        session.close()
    assert client.delete(f"/api/journals/{journal_id}").status_code == 204
    assert full.read_bytes() == before
    session = SessionLocal()
    try:
        assert session.scalar(select(func.count()).select_from(Photo)) == photo_count
        row = session.get(Journal, journal_id)
        assert row is not None
        assert row.body == ""
        assert row.deleted_at is not None
        links = session.scalar(
            select(func.count()).select_from(JournalPhoto).where(JournalPhoto.journal_id == journal_id)
        )
        assert links == 0
    finally:
        session.close()
    assert client.get(f"/media/{photo_id}").status_code == 200


def test_deleted_photo_placeholder_and_cover_falls_through(client: TestClient):
    child_id = _family(client, "ph@example.com", "妈妈", "占位家")
    today = shanghai_today().isoformat()
    first = upload(client, kind="child", child_id=child_id, taken_on=today)
    second = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert first.status_code == second.status_code == 201
    first_id = first.json()["id"]
    second_id = second.json()["id"]
    created = _post(client, child_id, body="首图会顺延", photos=[first_id, second_id], on_date=today)
    journal_id = created.json()["id"]
    session = SessionLocal()
    try:
        session.delete(session.get(Photo, first_id))
        session.commit()
    finally:
        session.close()
    detail = client.get(f"/api/journals/{journal_id}")
    assert detail.status_code == 200
    slots = detail.json()["photos"]
    assert slots[0] == {"id": None, "deleted": True}
    assert slots[1]["id"] == second_id
    assert str(first_id) not in json.dumps(slots[0])
    cover = client.get(f"/api/journals?child_id={child_id}").json()["journals"][0]["cover"]
    assert cover["id"] == second_id
    page = client.get(f"/?child={child_id}").text
    assert f"/media/{second_id}" in page
    assert f"/media/{first_id}" not in page


def test_child_removed_hides_from_feed_and_detail_404(client: TestClient):
    child_id = _family(client, "kid@example.com", "妈妈", "孩子家")
    other = client.post("/api/children", json={"name": "豆豆", "birth_date": "2025-03-01"})
    assert other.status_code == 201
    created = _post(client, child_id, body="孩子删了就不出现UNIQUEJ")
    journal_id = created.json()["id"]
    session = SessionLocal()
    try:
        session.delete(session.get(Child, child_id))
        session.commit()
    finally:
        session.close()
    page = client.get("/")
    assert page.status_code == 200
    assert "孩子删了就不出现UNIQUEJ" not in page.text
    detail = client.get(f"/api/journals/{journal_id}")
    missing = client.get("/api/journals/999999")
    assert detail.status_code == 404
    assert detail.content == missing.content
    html = client.get(f"/journals/{journal_id}")
    assert html.status_code == 404
    assert "找不到" in html.text
    assert "孩子删了就不出现UNIQUEJ" not in html.text


def test_ref_other_family_or_missing_photo_same_400(client: TestClient):
    child_id = _family(client, "ref@example.com", "妈妈", "引用家")
    today = shanghai_today().isoformat()
    mine = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert mine.status_code == 201
    other = TestClient(app)
    foreign_child = _family(other, "ref-b@example.com", "别人", "别家照片")
    foreign = upload(other, kind="child", child_id=foreign_child, taken_on=today)
    assert foreign.status_code == 201
    doomed = upload(client, kind="child", child_id=child_id, taken_on=today)
    doomed_id = doomed.json()["id"]
    session = SessionLocal()
    try:
        session.delete(session.get(Photo, doomed_id))
        session.commit()
    finally:
        session.close()
    missing_id = 999999
    foreign_id = foreign.json()["id"]
    missing = _post(client, child_id, photos=[missing_id], on_date=today)
    deleted = _post(client, child_id, photos=[doomed_id], on_date=today)
    outside = _post(client, child_id, photos=[foreign_id], on_date=today)
    assert missing.status_code == deleted.status_code == outside.status_code == 400
    assert missing.json()["code"] == deleted.json()["code"] == outside.json()["code"] == "photo_not_in_family"
    assert missing.json()["detail"] == outside.json()["detail"]
    assert missing.json()["invalid_ids"] == [missing_id]
    assert deleted.json()["invalid_ids"] == [doomed_id]
    assert outside.json()["invalid_ids"] == [foreign_id]
    mixed = _post(client, child_id, photos=[mine.json()["id"], foreign_id, missing_id], on_date=today)
    assert mixed.json()["invalid_ids"] == [foreign_id, missing_id]


def test_child_deleted_or_foreign_is_same_400(client: TestClient):
    child_id = _family(client, "cf@example.com", "妈妈", "孩子校验家")
    extra = client.post("/api/children", json={"name": "临时", "birth_date": "2025-01-01"})
    assert extra.status_code == 201
    extra_id = extra.json()["id"]
    session = SessionLocal()
    try:
        session.delete(session.get(Child, extra_id))
        session.commit()
    finally:
        session.close()
    other = TestClient(app)
    foreign_id = _family(other, "cf-b@example.com", "别人", "外孩家")
    today = shanghai_today().isoformat()
    deleted = _post(client, extra_id, on_date=today)
    foreign = _post(client, foreign_id, on_date=today)
    missing = _post(client, 888888, on_date=today)
    assert deleted.status_code == foreign.status_code == missing.status_code == 400
    assert deleted.content == foreign.content == missing.content
    assert deleted.json()["code"] == "child_not_found"
    assert child_id


def test_former_member_byline(client: TestClient):
    child_id = _family(client, "byline@example.com", "家长", "署名家")
    author = _invite(client, "aunt@example.com", "uploader", "小姨")
    created = _post(author, child_id, body="小姨写的")
    assert created.status_code == 201
    journal_id = created.json()["id"]
    admin_id = client.get("/api/me").json()["id"]
    session = SessionLocal()
    try:
        membership = session.scalar(select(Membership).where(Membership.user_id != admin_id))
        assert membership is not None
        session.delete(membership)
        session.commit()
    finally:
        session.close()
    detail = client.get(f"/api/journals/{journal_id}")
    assert detail.status_code == 200
    assert detail.json()["author"] == {"label": "小姨", "former": True}
    page = client.get(f"/?child={child_id}").text
    assert "前成员 · 小姨" in page


def test_age_js_port_matches_python():
    source = Path("app/static/journal.js").read_text(encoding="utf-8")
    assert "function ageLabel" in source
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    pairs = [
        ("2024-07-12", "2024-07-12"),
        ("2024-07-12", "2024-08-12"),
        ("2023-08-15", "2026-10-24"),
        ("2025-03-01", "2026-03-01"),
        ("2026-10-20", "2026-10-24"),
        ("2024-07-12", shanghai_today().isoformat()),
    ]
    script = (
        "const fs = require('fs');"
        "const m = require(process.argv[1]);"
        "const pairs = JSON.parse(fs.readFileSync(0, 'utf8'));"
        "process.stdout.write(JSON.stringify(pairs.map((pair) => m.ageLabel(pair[0], pair[1]))));"
    )
    completed = subprocess.run(
        [node, "-e", script, str(Path("app/static/journal.js").resolve())],
        input=json.dumps(pairs),
        check=True,
        capture_output=True,
        text=True,
    )
    labels = json.loads(completed.stdout)
    for (birth, on), label in zip(pairs, labels, strict=True):
        assert label == age_label(date.fromisoformat(birth), date.fromisoformat(on))


def test_all_journal_responses_no_store(client: TestClient):
    child_id = _family(client, "store@example.com", "妈妈", "缓存家")
    viewer = _invite(client, "store-v@example.com", "viewer", "外婆")
    created = _post(client, child_id)
    assert created.status_code == 201
    journal_id = created.json()["id"]
    responses = [
        created,
        client.get(f"/api/journals?child_id={child_id}"),
        client.get(f"/api/journals/{journal_id}"),
        client.get("/journals/new"),
        client.get(f"/journals/{journal_id}"),
        client.get("/fragments/journal/picker"),
        client.get("/api/journals/999999"),
        viewer.post("/api/journals", headers={"Idempotency-Key": str(uuid.uuid4())}, json={"body": "x"}),
        client.post("/api/journals", headers={"Idempotency-Key": "nope"}, json={"body": "有字", "child_id": child_id, "on_date": shanghai_today().isoformat()}),
    ]
    for response in responses:
        assert "no-store" in response.headers["cache-control"], response.request.url


def test_journal_cross_origin_write_rejected(client: TestClient):
    child_id = _family(client, "csrf@example.com", "妈妈", "跨站家")
    response = client.post(
        "/api/journals",
        headers={"Origin": "https://evil.example", "Idempotency-Key": str(uuid.uuid4())},
        json={"child_id": child_id, "on_date": shanghai_today().isoformat(), "body": "不该写上", "photo_ids": []},
    )
    assert response.status_code == 403
    assert "no-store" in response.headers["cache-control"]
    assert response.json() == {"detail": "没有访问权限"}
    assert client.get(f"/api/journals?child_id={child_id}").json()["journals"] == []


def test_form_post_is_415(client: TestClient):
    child_id = _family(client, "form@example.com", "妈妈", "表单家")
    response = client.post(
        "/api/journals",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        data={"body": "表单", "child_id": str(child_id)},
    )
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_media"
    assert "no-store" in response.headers["cache-control"]


def test_export_contains_only_my_journals_md(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("EXPORT_TEMP_DIR", str(tmp_path))
    child_id = _family(client, "md@example.com", "小美", "导出家")
    today = shanghai_today()
    mine = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    doomed = upload(client, kind="self", taken_on="2026-10-20", send_child=False)
    assert mine.status_code == doomed.status_code == 201
    other = _invite(client, "md-b@example.com", "uploader", "大伟")
    theirs = upload(other, kind="child", child_id=child_id, taken_on="2026-10-21")
    assert theirs.status_code == 201
    extra = client.post("/api/children", json={"name": "豆豆", "birth_date": "2025-03-01"})
    assert extra.status_code == 201
    bean_id = extra.json()["id"]
    early = _post(client, bean_id, body="豆豆那天", on_date="2026-10-01")
    kept = _post(
        client,
        child_id,
        body="果果那天",
        on_date="2026-10-22",
        photos=[mine.json()["id"], theirs.json()["id"], doomed.json()["id"]],
    )
    dropped = _post(client, child_id, body="这条会删掉", on_date="2026-10-02")
    theirs_journal = _post(other, child_id, body="大伟的日志", on_date="2026-10-03")
    assert early.status_code == kept.status_code == dropped.status_code == theirs_journal.status_code == 201
    assert client.delete(f"/api/journals/{dropped.json()['id']}").status_code == 204
    session = SessionLocal()
    try:
        session.delete(session.get(Photo, doomed.json()["id"]))
        session.delete(session.get(Child, bean_id))
        session.commit()
    finally:
        session.close()
    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 200, packed.text
    mine_name = f"2026-10-22_{mine.json()['id']}.jpg"
    their_name = f"2026-10-21_{theirs.json()['id']}.jpg"
    assert _names(packed.content) == [mine_name, "日志.md"]
    assert _stored(packed.content, mine_name) == JPEG
    text = _stored(packed.content, "日志.md").decode("utf-8")
    expected = (
        "# 成长日志\n"
        "\n"
        "## 2026-10-01\n"
        "作者：小美\n"
        "日期：2026-10-01\n"
        "孩子：（孩子已不在列表）\n"
        "引用：无\n"
        "\n"
        "豆豆那天\n"
        "\n"
        "## 2026-10-22\n"
        "作者：小美\n"
        "日期：2026-10-22\n"
        "孩子：果果\n"
        f"引用：{mine_name}、{their_name}、（已删除）\n"
        "\n"
        "果果那天\n"
    )
    assert text == expected
    assert "大伟的日志" not in text
    assert "这条会删掉" not in text
    assert today


def test_export_no_journal_md_when_zero(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("EXPORT_TEMP_DIR", str(tmp_path))
    child_id = _family(client, "nomd@example.com", "小美", "无日志家")
    uploaded = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert uploaded.status_code == 201
    created = _post(client, child_id, body="马上删")
    assert created.status_code == 201
    assert client.delete(f"/api/journals/{created.json()['id']}").status_code == 204
    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 200
    names = _names(packed.content)
    assert names == [f"2026-10-22_{uploaded.json()['id']}.jpg"]
    assert "日志.md" not in names


def test_export_journals_only_zip_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("EXPORT_TEMP_DIR", str(tmp_path))
    child_id = _family(client, "onlyj@example.com", "小美", "只有日志家")
    assert _post(client, child_id, body="没有照片也能带走").status_code == 201
    assert client.get("/api/export/my-photos").json() == {"count": 0}
    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 200, packed.text
    assert _names(packed.content) == ["日志.md"]
    assert "没有照片也能带走" in _stored(packed.content, "日志.md").decode("utf-8")
    page = client.get("/family/export")
    assert page.status_code == 200
    assert "你还没传过照片，传几张再来导出" not in page.text
    assert "开始导出" in page.text
    sheet = client.get("/fragments/export/sheet")
    assert sheet.status_code == 200
    assert "开始导出" in sheet.text


def test_journal_copy_no_banned_words():
    assert len(JOURNAL) == 75
    blob = "\n".join(JOURNAL.values())
    for word in BANNED:
        assert word not in blob
    assert "内容已帮你留着" not in JOURNAL["e_removed"]
    assert JOURNAL["err_demoted"] == "你现在不能写日志了，内容已帮你留着"
    assert JOURNAL["w_submit"] == "记下来"
    assert JOURNAL["w_submit_edit"] == "保存修改"
    assert JOURNAL["not_found"] == "找不到"
    assert JOURNAL["entry_write"] == "写日志"
    source = Path("app/journal_copy.py").read_text(encoding="utf-8")
    for key in DEPRECATED:
        assert key not in source
    assert "最多 2000 字" in JOURNAL["err_too_long"]
    for surface in (
        Path("app/templates/journal_write.html"),
        Path("app/templates/journal_detail.html"),
        Path("app/templates/fragments/journal_card.html"),
        Path("app/static/journal.js"),
    ):
        text = surface.read_text(encoding="utf-8")
        for word in ("发到时光线", "发动态", "朋友圈", "会员", "广告"):
            assert word not in text
