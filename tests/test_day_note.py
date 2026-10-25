"""一日一句 / 暖纸一句。某一天一句话，不新开底部标签，不推进 last_seen。"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.constants import DAY_NOTE, FORBIDDEN
from app.day_note_copy import DAY_NOTE as COPY
from app.day_note_copy import DAY_NOTE_ANNO as ANNO
from app.day_note_copy import DAY_NOTE_CHILD_GONE
from app.db import SessionLocal
from app.main import app
from app.models import Child, DayNote, DayNoteChild, Membership, TimelineLastSeen, User
from app.services.clock import shanghai_today
from app.services.day_notes import DayNoteUnavailable
from app.services.on_this_day import previous_year_date
from tests.test_acceptance import JPEG, auth, upload
from tests.test_journal import _clone, _family, _invite

BANNED = ("会员", "开通", "商城", "发现", "广告", "评论区", "动态")
SURFACES = (
    Path("app/templates/day.html"),
    Path("app/templates/fragments/day_note_card.html"),
    Path("app/templates/fragments/stage_inner.html"),
    Path("app/templates/layout.html"),
    Path("app/static/day_note.js"),
    Path("app/static/app.css"),
    Path("app/services/day_notes.py"),
)
SAMPLE_KEYS = (
    "empty_no_slot",
    "j_quote",
    "j_sub",
    "note_1",
    "note_2",
    "note_3",
    "url_demo",
    "name_gg",
    "name_dd",
    "name_mama",
    "name_baba",
    "name_yeye",
    "tl_family",
    "age_chip",
    "age_fmt",
    "wd_sun",
)


def _post(client: TestClient, child_ids: list[int] | None, body: str = "果果今天笑了", on_date: str | None = None, **extra):
    payload: dict = {"body": body}
    if on_date is not None:
        payload["on_date"] = on_date
    else:
        payload["on_date"] = shanghai_today().isoformat()
    if child_ids is not None:
        payload["child_ids"] = child_ids
    payload.update(extra)
    return client.post("/api/families/current/day-notes", json=payload)


def _day(client: TestClient, child_id: int, on_date: str | None = None):
    day = on_date or shanghai_today().isoformat()
    return client.get(f"/children/{child_id}/days/{day}")


def _initial(html: str) -> dict:
    marker = '<script type="application/json" id="day-note-initial">'
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    return json.loads(html[start:end])


def _js(expr: str):
    node = shutil.which("node")
    assert node
    script = "const m = require(process.argv[1]); process.stdout.write(JSON.stringify(" + expr + "));"
    completed = subprocess.run(
        [node, "-e", script, str(Path("app/static/day_note.js").resolve())],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_day_note_write_and_list(client: TestClient):
    child_id = _family(client, "dn-write@example.com", "妈妈", "一句家")
    sibling = client.post("/api/children", json={"name": "弟弟", "birth_date": "2022-03-01"})
    assert sibling.status_code == 201
    sibling_id = sibling.json()["id"]
    day = shanghai_today().isoformat()
    created = _post(client, [sibling_id, child_id, sibling_id], body="  弟弟先说  ", on_date=day)
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == {
        "id",
        "on_date",
        "body",
        "author",
        "children",
        "created_at",
        "updated_at",
        "can_edit",
        "can_delete",
    }
    assert body["body"] == "弟弟先说"
    assert body["on_date"] == day
    assert [item["id"] for item in body["children"]] == [sibling_id, child_id]
    assert body["children"][0]["avatar_url"] is None
    assert body["author"]["display_name"] == "妈妈"
    assert body["can_edit"] is True and body["can_delete"] is True
    assert body["created_at"].endswith("+00:00")
    assert not body["created_at"].endswith("Z")
    assert "no-store" in created.headers["cache-control"]
    listed = client.get(f"/api/families/current/day-notes?on_date={day}&family_id=999999")
    assert listed.status_code == 200
    assert listed.json()["notes"][0]["id"] == body["id"]
    assert "no-store" in listed.headers["cache-control"]
    page = _day(client, child_id, day)
    assert page.status_code == 200
    assert "弟弟先说" in page.text
    assert COPY["sec_notes"] in page.text
    assert 'class="warm-note"' in page.text
    assert COPY["empty_no_slot"] not in page.text


def test_day_note_upsert_same_author_same_day(client: TestClient):
    child_id = _family(client, "dn-up@example.com", "妈妈", "覆盖家")
    first = _post(client, [child_id], body="第一句")
    second = _post(client, [child_id], body="改成第二句")
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["body"] == "改成第二句"
    listed = client.get(f"/api/families/current/day-notes?on_date={shanghai_today().isoformat()}")
    assert len(listed.json()["notes"]) == 1


def test_two_authors_keep_two_notes(client: TestClient):
    child_id = _family(client, "dn-two-a@example.com", "妈妈", "两人家")
    dad = _invite(client, "dn-two-b@example.com", "uploader", "爸爸")
    assert _post(client, [child_id], body="妈妈的一句").status_code == 201
    assert _post(dad, [child_id], body="爸爸的一句").status_code == 201
    listed = client.get(f"/api/families/current/day-notes?on_date={shanghai_today().isoformat()}")
    bodies = [item["body"] for item in listed.json()["notes"]]
    assert bodies == ["妈妈的一句", "爸爸的一句"]


def test_day_note_empty_body_422(client: TestClient):
    child_id = _family(client, "dn-empty@example.com", "妈妈", "空句家")
    for body in ("", "   ", "\n\n"):
        response = _post(client, [child_id], body=body)
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "body_empty"
        assert response.json()["detail"] == COPY["err_empty"]


def test_day_note_body_over_200_422(client: TestClient):
    child_id = _family(client, "dn-long@example.com", "妈妈", "长句家")
    response = _post(client, [child_id], body="字" * 201)
    assert response.status_code == 422
    assert response.json()["code"] == "body_too_long"
    assert response.json()["detail"] == COPY["err_too_long"]
    assert _post(client, [child_id], body="字" * 200).status_code == 201


def test_day_note_newline_422(client: TestClient):
    child_id = _family(client, "dn-nl@example.com", "妈妈", "换行家")
    response = _post(client, [child_id], body="上一行\n下一行")
    assert response.status_code == 422
    assert response.json()["code"] == "body_has_newline"
    assert response.json()["detail"] == COPY["err_too_long"]


def test_day_note_no_child_422(client: TestClient):
    child_id = _family(client, "dn-kid@example.com", "妈妈", "孩子家")
    missing = client.post(
        "/api/families/current/day-notes",
        json={"on_date": shanghai_today().isoformat(), "body": "有字"},
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == "child_required"
    empty = _post(client, [], body="有字")
    assert empty.status_code == 422
    assert empty.json()["code"] == "child_required"
    foreign = _post(client, [child_id + 999], body="有字")
    assert foreign.status_code == 422
    assert foreign.json()["code"] == "child_invalid"
    assert empty.json()["detail"] == COPY["err_no_child"]


def test_day_note_malformed_422(client: TestClient):
    child_id = _family(client, "dn-bad@example.com", "妈妈", "坏包家")
    plain = client.post(
        "/api/families/current/day-notes",
        content=b"hello",
        headers={"content-type": "text/plain"},
    )
    assert plain.status_code == 422
    assert plain.json()["code"] == "body_invalid"
    array = client.post(
        "/api/families/current/day-notes",
        content=b"[1]",
        headers={"content-type": "application/json"},
    )
    assert array.status_code == 422
    assert array.json()["code"] == "body_invalid"
    bad_day = _post(client, [child_id], on_date="2026-13-40")
    assert bad_day.status_code == 422
    assert bad_day.json()["code"] == "on_date_invalid"
    missing_day = client.get("/api/families/current/day-notes")
    assert missing_day.status_code == 422
    assert missing_day.json()["code"] == "on_date_invalid"
    number = client.post(
        "/api/families/current/day-notes",
        json={"on_date": shanghai_today().isoformat(), "body": 12, "child_ids": [child_id]},
    )
    assert number.status_code == 422
    assert number.json()["code"] == "body_invalid"


def test_day_note_on_date_immutable(client: TestClient):
    child_id = _family(client, "dn-date@example.com", "妈妈", "日期家")
    created = _post(client, [child_id], body="原句", on_date="2026-10-01")
    note_id = created.json()["id"]
    moved = client.put(
        f"/api/day-notes/{note_id}",
        json={"body": "改句", "child_ids": [child_id], "on_date": "2026-10-02"},
    )
    assert moved.status_code == 422
    assert moved.json()["code"] == "on_date_immutable"
    kept = client.get("/api/families/current/day-notes?on_date=2026-10-01")
    assert kept.json()["notes"][0]["body"] == "原句"
    same = client.put(
        f"/api/day-notes/{note_id}",
        json={"body": "同日改", "child_ids": [child_id], "on_date": "2026-10-01"},
    )
    assert same.status_code == 200
    assert same.json()["on_date"] == "2026-10-01"
    assert same.json()["body"] == "同日改"


def test_day_note_display_over_80_under_hard_cap(client: TestClient):
    child_id = _family(client, "dn-80@example.com", "妈妈", "八十家")
    body = "字" * 90
    created = _post(client, [child_id], body=body)
    assert created.status_code == 201
    assert created.json()["body"] == body
    page = _day(client, child_id)
    assert body in page.text
    state = _js("m.countState(" + json.dumps(body) + ", 80, 200, 70)")
    assert state["over"] is True
    assert state["blocked"] is False
    assert state["n"] == 90
    warn = _js('m.countState("' + ("字" * 70) + '", 80, 200, 70)')
    assert warn["warn"] is True and warn["over"] is False and warn["blocked"] is False
    edge = _js('m.countState("' + ("字" * 200) + '", 80, 200, 70)')
    assert edge["blocked"] is False and edge["over"] is True
    hard = _js('m.countState("' + ("字" * 201) + '", 80, 200, 70)')
    assert hard["blocked"] is True


def test_viewer_can_read_day_notes(client: TestClient):
    child_id = _family(client, "dn-read-a@example.com", "妈妈", "只读可见家")
    secret = "长辈也能看见这句"
    assert _post(client, [child_id], body=secret).status_code == 201
    viewer = _invite(client, "dn-read-v@example.com", "viewer", "外婆")
    listed = viewer.get(f"/api/families/current/day-notes?on_date={shanghai_today().isoformat()}")
    assert listed.status_code == 200
    assert listed.json()["notes"][0]["body"] == secret
    assert listed.json()["notes"][0]["can_edit"] is False
    assert listed.json()["notes"][0]["can_delete"] is False
    page = _day(viewer, child_id)
    assert page.status_code == 200
    assert secret in page.text
    assert COPY["ro_meta"] in page.text
    assert COPY["entry_write"] not in page.text
    assert "day-note-form" not in page.text
    assert "day_note.js" not in page.text


def test_viewer_day_note_write_403(client: TestClient):
    child_id = _family(client, "dn-ro-a@example.com", "妈妈", "只读写家")
    secret = "不能被只读改掉"
    created = _post(client, [child_id], body=secret)
    note_id = created.json()["id"]
    viewer = _invite(client, "dn-ro-v@example.com", "viewer", "外婆")
    day = shanghai_today().isoformat()
    payload = {"on_date": day, "body": "偷看写", "child_ids": [child_id]}
    responses = [
        viewer.post("/api/families/current/day-notes", json=payload),
        viewer.put(f"/api/day-notes/{note_id}", json=payload),
        viewer.put("/api/day-notes/999999", json=payload),
        viewer.delete(f"/api/day-notes/{note_id}"),
        viewer.delete("/api/day-notes/999999"),
    ]
    for response in responses:
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "day_note_readonly"
        assert response.json()["detail"] == COPY["err_perm"]
        assert secret not in response.text
        assert "no-store" in response.headers["cache-control"]
    listed = client.get(f"/api/families/current/day-notes?on_date={day}")
    assert listed.json()["notes"][0]["body"] == secret


def test_outsider_day_note_403_no_code(client: TestClient):
    child_id = _family(client, "dn-out-a@example.com", "妈妈", "外人家")
    secret = "外人看不见的句子"
    created = _post(client, [child_id], body=secret)
    note_id = created.json()["id"]
    outsider = TestClient(app)
    auth(outsider, "dn-out-b@example.com", "外人")
    anon = TestClient(app)
    day = shanghai_today().isoformat()
    payload = {"on_date": day, "body": "不该写入", "child_ids": [child_id]}
    for actor in (outsider, anon):
        responses = [
            actor.get(f"/api/families/current/day-notes?on_date={day}"),
            actor.post("/api/families/current/day-notes", json=payload),
            actor.put(f"/api/day-notes/{note_id}", json=payload),
            actor.delete(f"/api/day-notes/{note_id}"),
        ]
        for response in responses:
            assert response.status_code == 403, response.text
            assert response.json() == {"detail": FORBIDDEN}
            assert "code" not in response.json()
            assert secret not in response.text
            assert "外人家" not in response.text


def test_day_note_404_uniform(client: TestClient):
    child_id = _family(client, "dn-404-a@example.com", "妈妈", "找不到家")
    created = _post(client, [child_id], body="还在")
    note_id = created.json()["id"]
    other = TestClient(app)
    other_child = _family(other, "dn-404-b@example.com", "别人", "另一家")
    foreign = _post(other, [other_child], body="另一家的句子")
    payload = {"body": "改", "child_ids": [child_id]}
    missing = client.put("/api/day-notes/999999", json=payload)
    cross = client.put(f"/api/day-notes/{foreign.json()['id']}", json=payload)
    assert client.delete(f"/api/day-notes/{note_id}").status_code == 204
    gone = client.put(f"/api/day-notes/{note_id}", json=payload)
    expected = {"detail": "找不到", "code": "day_note_not_found"}
    for response in (missing, cross, gone):
        assert response.status_code == 404, response.text
        assert response.json() == expected
        assert "另一家的句子" not in response.text


def test_uploader_cannot_delete_others_day_note(client: TestClient):
    child_id = _family(client, "dn-del-a@example.com", "妈妈", "删权家")
    created = _post(client, [child_id], body="妈妈的")
    note_id = created.json()["id"]
    uploader = _invite(client, "dn-del-u@example.com", "uploader", "爸爸")
    edited = uploader.put(
        f"/api/day-notes/{note_id}",
        json={"body": "爸爸改", "child_ids": [child_id]},
    )
    assert edited.status_code == 403
    assert edited.json()["code"] == "day_note_not_author"
    deleted = uploader.delete(f"/api/day-notes/{note_id}")
    assert deleted.status_code == 403
    assert deleted.json()["code"] == "day_note_not_author"
    listed = client.get(f"/api/families/current/day-notes?on_date={shanghai_today().isoformat()}")
    assert listed.json()["notes"][0]["body"] == "妈妈的"


def test_admin_deletes_any_day_note(client: TestClient):
    child_id = _family(client, "dn-adm@example.com", "妈妈", "家长删家")
    dad = _invite(client, "dn-adm-u@example.com", "uploader", "爸爸")
    created = _post(dad, [child_id], body="爸爸写的")
    note_id = created.json()["id"]
    edited = client.put(
        f"/api/day-notes/{note_id}",
        json={"body": "家长改", "child_ids": [child_id]},
    )
    assert edited.status_code == 403
    assert edited.json()["code"] == "day_note_not_author"
    assert client.delete(f"/api/day-notes/{note_id}").status_code == 204
    listed = client.get(f"/api/families/current/day-notes?on_date={shanghai_today().isoformat()}")
    assert listed.json()["notes"] == []


def test_admin_cannot_edit_before_validation(client: TestClient):
    child_id = _family(client, "dn-adm2@example.com", "妈妈", "先拒绝家")
    dad = _invite(client, "dn-adm2-u@example.com", "uploader", "爸爸")
    created = _post(dad, [child_id], body="原句")
    edited = client.put(f"/api/day-notes/{created.json()['id']}", json={"body": "", "child_ids": []})
    assert edited.status_code == 403
    assert edited.json()["code"] == "day_note_not_author"


def test_soft_deleted_day_note_hidden(client: TestClient):
    child_id = _family(client, "dn-soft@example.com", "妈妈", "软删家")
    created = _post(client, [child_id], body="要删的")
    note_id = created.json()["id"]
    assert client.delete(f"/api/day-notes/{note_id}").status_code == 204
    listed = client.get(f"/api/families/current/day-notes?on_date={shanghai_today().isoformat()}")
    assert listed.json()["notes"] == []
    page = _day(client, child_id)
    assert "要删的" not in page.text
    assert 'id="day-notes"' not in page.text
    with SessionLocal() as db:
        row = db.get(DayNote, note_id)
        assert row is not None and row.deleted_at is not None and row.body == ""
        links = db.scalar(select(func.count()).select_from(DayNoteChild).where(DayNoteChild.note_id == note_id))
        assert links == 0


def test_rewrite_after_soft_delete_creates_new(client: TestClient):
    child_id = _family(client, "dn-re@example.com", "妈妈", "再写家")
    first = _post(client, [child_id], body="旧句")
    old_id = first.json()["id"]
    assert client.delete(f"/api/day-notes/{old_id}").status_code == 204
    second = _post(client, [child_id], body="新句")
    assert second.status_code == 201
    assert second.json()["id"] != old_id
    listed = client.get(f"/api/families/current/day-notes?on_date={shanghai_today().isoformat()}")
    assert [item["id"] for item in listed.json()["notes"]] == [second.json()["id"]]
    with SessionLocal() as db:
        assert db.get(DayNote, old_id).deleted_at is not None
        assert db.get(DayNote, second.json()["id"]).deleted_at is None


def test_day_note_concurrent_upsert_one_row(client: TestClient):
    child_id = _family(client, "dn-race@example.com", "妈妈", "并发家")
    other = _clone(client)
    barrier = threading.Barrier(2)
    results: list = []

    def go(actor: TestClient, text: str) -> None:
        barrier.wait(5)
        results.append(_post(actor, [child_id], body=text))

    threads = [
        threading.Thread(target=go, args=(client, "同时甲")),
        threading.Thread(target=go, args=(other, "同时乙")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(item.status_code for item in results) == [200, 201]
    listed = client.get(f"/api/families/current/day-notes?on_date={shanghai_today().isoformat()}")
    assert len(listed.json()["notes"]) == 1
    with SessionLocal() as db:
        count = db.scalar(
            select(func.count()).select_from(DayNote).where(DayNote.deleted_at.is_(None))
        )
        assert count == 1


def test_day_note_visible_after_child_removed(client: TestClient):
    child_id = _family(client, "dn-gone@example.com", "妈妈", "移出家")
    sibling = client.post("/api/children", json={"name": "弟弟", "birth_date": "2022-02-02"})
    assert sibling.status_code == 201
    sibling_id = sibling.json()["id"]
    body = "孩子走了句子还在"
    created = _post(client, [child_id], body=body)
    assert created.status_code == 201
    with SessionLocal() as db:
        db.delete(db.get(Child, child_id))
        db.commit()
    listed = client.get(f"/api/families/current/day-notes?on_date={shanghai_today().isoformat()}")
    assert listed.status_code == 200
    note = listed.json()["notes"][0]
    assert note["body"] == body
    assert note["children"][0]["name"] == DAY_NOTE_CHILD_GONE
    assert note["children"][0]["id"] == child_id
    page = client.get(f"/days/{shanghai_today().isoformat()}")
    assert page.status_code == 200
    assert body in page.text
    assert DAY_NOTE_CHILD_GONE in page.text
    timeline = client.get("/?view=family")
    assert body in timeline.text
    assert "day-note-line" in timeline.text
    sibling_feed = client.get(f"/?child={sibling_id}")
    assert body not in sibling_feed.text
    hidden = client.get(f"/children/{child_id}/days/{shanghai_today().isoformat()}")
    assert hidden.status_code == 403
    assert body not in hidden.text


def test_day_note_edit_cannot_attach_removed_child(client: TestClient):
    first = _family(client, "dn-reattach@example.com", "妈妈", "不能挂回家")
    second = client.post("/api/children", json={"name": "弟弟", "birth_date": "2022-01-01"})
    second_id = second.json()["id"]
    created = _post(client, [first], body="只写给果果")
    note_id = created.json()["id"]
    with SessionLocal() as db:
        db.delete(db.get(Child, first))
        db.commit()
    denied = client.put(
        f"/api/day-notes/{note_id}",
        json={"body": "想挂回去", "child_ids": [first]},
    )
    assert denied.status_code == 422
    assert denied.json()["code"] == "child_invalid"
    listed = client.get(f"/api/families/current/day-notes?on_date={shanghai_today().isoformat()}")
    assert listed.json()["notes"][0]["body"] == "只写给果果"
    moved = client.put(
        f"/api/day-notes/{note_id}",
        json={"body": "改给弟弟", "child_ids": [second_id]},
    )
    assert moved.status_code == 200
    assert [item["id"] for item in moved.json()["children"]] == [second_id]


def test_child_view_preselects_family_entry_does_not(client: TestClient):
    child_id = _family(client, "dn-pre@example.com", "妈妈", "预选家")
    sibling = client.post("/api/children", json={"name": "弟弟", "birth_date": "2022-04-01"})
    sibling_id = sibling.json()["id"]
    day = shanghai_today().isoformat()
    family_page = client.get(f"/days/{day}")
    assert family_page.status_code == 200
    assert _initial(family_page.text)["childIds"] == []
    assert _initial(family_page.text)["mode"] == "new"
    child_page = _day(client, child_id, day)
    assert _initial(child_page.text)["childIds"] == [child_id]
    sibling_page = _day(client, sibling_id, day)
    assert _initial(sibling_page.text)["childIds"] == [sibling_id]
    created = _post(client, [child_id, sibling_id], body="写给两个")
    assert created.status_code == 201
    edited = _day(client, child_id, day)
    initial = _initial(edited.text)
    assert initial["mode"] == "edit"
    assert initial["noteId"] == created.json()["id"]
    assert initial["body"] == "写给两个"
    assert initial["childIds"] == [child_id, sibling_id]
    assert COPY["entry_edit"] in edited.text
    alone = TestClient(app)
    only = _family(alone, "dn-pre-one@example.com", "妈妈", "单孩全家")
    family_one = alone.get(f"/days/{day}")
    assert _initial(family_one.text)["childIds"] == []
    assert _initial(_day(alone, only, day).text)["childIds"] == [only]


def test_day_note_empty_no_slot(client: TestClient):
    child_id = _family(client, "dn-slot@example.com", "妈妈", "空天家")
    page = _day(client, child_id)
    assert page.status_code == 200
    assert 'id="day-notes"' not in page.text
    assert COPY["sec_notes"] not in page.text
    assert COPY["empty_no_slot"] not in page.text
    assert COPY["entry_write"] in page.text
    assert "这一天还没有内容" in page.text
    timeline = client.get(f"/?child={child_id}")
    assert "day-note-line" not in timeline.text
    assert COPY["empty_no_slot"] not in timeline.text


def test_timeline_no_snippet_when_empty(client: TestClient):
    child_id = _family(client, "dn-nosnip@example.com", "妈妈", "无句日卡家")
    today = shanghai_today().isoformat()
    photo = upload(client, kind="child", child_id=child_id, taken_on=today, content=JPEG)
    assert photo.status_code == 201
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 200
    assert "day-note-line" not in page.text
    assert COPY["empty_no_slot"] not in page.text
    assert f"/media/{photo.json()['id']}" in page.text


def test_timeline_day_card_snippet_earliest(client: TestClient):
    child_id = _family(client, "dn-snip-a@example.com", "妈妈", "摘要行家")
    dad = _invite(client, "dn-snip-b@example.com", "uploader", "爸爸")
    long_body = "早" + ("字" * 50)
    assert _post(client, [child_id], body=long_body).status_code == 201
    assert _post(dad, [child_id], body="爸爸后写的一句").status_code == 201
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 200
    assert "day-note-line" in page.text
    assert f"妈妈：早{'字' * 39}…" in page.text
    assert "爸爸后写的一句" not in page.text
    day = client.get(f"/children/{child_id}/days/{shanghai_today().isoformat()}")
    assert "爸爸后写的一句" in day.text
    assert long_body in day.text


def test_child_view_snippet_filters(client: TestClient):
    child_id = _family(client, "dn-filter@example.com", "妈妈", "过滤家")
    sibling = client.post("/api/children", json={"name": "弟弟", "birth_date": "2022-05-01"})
    sibling_id = sibling.json()["id"]
    assert _post(client, [sibling_id], body="只给弟弟").status_code == 201
    mine = client.get(f"/?child={child_id}")
    assert "只给弟弟" not in mine.text
    assert "day-note-line" not in mine.text
    theirs = client.get(f"/?child={sibling_id}")
    assert "只给弟弟" in theirs.text
    assert f'href="/children/{sibling_id}/days/{shanghai_today().isoformat()}"' in theirs.text
    family = client.get("/?view=family")
    assert "只给弟弟" in family.text
    assert f'href="/days/{shanghai_today().isoformat()}"' in family.text


def test_timeline_snippet_failure_isolated(client: TestClient, monkeypatch):
    child_id = _family(client, "dn-iso@example.com", "妈妈", "隔离家")
    today = shanghai_today().isoformat()
    photo = upload(client, kind="child", child_id=child_id, taken_on=today, content=JPEG)
    assert photo.status_code == 201
    assert _post(client, [child_id], body="不该拖垮时光线").status_code == 201

    def boom(*_args, **_kwargs):
        raise RuntimeError("snippet down")

    monkeypatch.setattr("app.services.day_notes.load_active_notes", boom)
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 200
    assert f"/media/{photo.json()['id']}" in page.text
    assert "day-note-line" not in page.text
    assert "不该拖垮时光线" not in page.text


def test_day_note_503_no_crash(client: TestClient, monkeypatch):
    child_id = _family(client, "dn-busy@example.com", "妈妈", "忙家")
    body = "忙的时候先别显示"
    assert _post(client, [child_id], body=body).status_code == 201

    def busy() -> None:
        raise DayNoteUnavailable()

    monkeypatch.setattr("app.services.day_notes.raise_if_busy", busy)
    listed = client.get(f"/api/families/current/day-notes?on_date={shanghai_today().isoformat()}")
    assert listed.status_code == 503
    assert listed.json() == {"detail": "服务忙"}
    assert "code" not in listed.json()
    page = _day(client, child_id)
    assert page.status_code == 200
    assert body not in page.text
    assert 'id="day-notes"' not in page.text
    assert COPY["entry_write"] in page.text
    timeline = client.get(f"/?child={child_id}")
    assert timeline.status_code == 200
    assert body in timeline.text


def test_demoted_mid_day_note_submit(client: TestClient):
    child_id = _family(client, "dn-dem-a@example.com", "妈妈", "降权家")
    uploader = _invite(client, "dn-dem-u@example.com", "uploader", "爸爸")
    page = _day(uploader, child_id)
    assert COPY["entry_write"] in page.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "dn-dem-u@example.com"))
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
        membership.role = "viewer"
        db.commit()
    response = _post(uploader, [child_id], body="降权后还想写")
    assert response.status_code == 403
    assert response.json()["code"] == "day_note_readonly"
    decision = _js('m.decideDayNote(403, {code: "day_note_readonly"}, {})')
    assert decision["hideWrite"] is True
    assert decision["ok"] is False
    assert decision["apply"] is False


def test_late_day_note_response_ignored():
    late = _js("m.decideDayNote(201, {id: 1}, {left: true, generation: 1, currentGeneration: 1})")
    assert late["ignore"] is True
    assert late["apply"] is False
    assert late["ok"] is False
    assert late["kind"] == "late"
    stale = _js("m.decideDayNote(200, {id: 1}, {generation: 1, currentGeneration: 2})")
    assert stale["ignore"] is True and stale["apply"] is False


def test_day_note_offline_no_fake_success():
    decision = _js("m.decideDayNote(0, null, {})")
    assert decision["ok"] is False
    assert decision["apply"] is False
    assert decision["retry"] is True
    assert decision["kind"] == "offline"


def test_js_404_without_code_retryable_day_note():
    bare = _js("m.decideDayNote(404, {}, {})")
    assert bare["retry"] is True
    assert bare["kind"] == "retryable"
    assert bare["clearLocal"] is False
    coded = _js('m.decideDayNote(404, {code: "day_note_not_found"}, {})')
    assert coded["retry"] is False
    assert coded["kind"] == "missing"
    busy = _js("m.decideDayNote(503, {detail: 'x'}, {})")
    assert busy["retry"] is True and busy["crash"] is False and busy["ok"] is False


def test_day_note_cross_origin_403(client: TestClient):
    child_id = _family(client, "dn-csrf@example.com", "妈妈", "跨站家")
    response = client.post(
        "/api/families/current/day-notes",
        json={"on_date": shanghai_today().isoformat(), "body": "不该写上", "child_ids": [child_id]},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": FORBIDDEN}
    listed = client.get(f"/api/families/current/day-notes?on_date={shanghai_today().isoformat()}")
    assert listed.json()["notes"] == []


def test_day_note_does_not_advance_last_seen(client: TestClient):
    child_id = _family(client, "dn-seen@example.com", "妈妈", "不推进家")
    assert _post(client, [child_id], body="不改查看时刻").status_code == 201
    assert _day(client, child_id).status_code == 200
    assert client.get(f"/api/families/current/day-notes?on_date={shanghai_today().isoformat()}").status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TimelineLastSeen)) == 0


def test_day_note_skipped_by_on_this_day_and_whats_new(client: TestClient):
    child_id = _family(client, "dn-otd@example.com", "妈妈", "不进年签家")
    target = previous_year_date(shanghai_today()) or date(2024, 6, 1)
    created = _post(client, [child_id], body="只有一句", on_date=target.isoformat())
    assert created.status_code == 201
    page = client.get(f"/?child={child_id}")
    assert "amber-card" not in page.text
    assert 'class="slipbar"' not in page.text
    fresh = client.get("/api/families/current/on-this-day", params={"child_id": child_id})
    assert fresh.status_code == 200
    assert fresh.json()["has_on_this_day"] is False
    assert fresh.json()["total"] == 0


def test_copy_entry_write_wording():
    assert COPY["entry_write"] == "写一句"
    assert COPY["entry_edit"] == "改一句"
    assert COPY["w_title_new"] == "写一句"
    assert COPY["w_submit"] == "留下这句"
    assert COPY["sec_notes"] == "这一天的一句"
    assert COPY["snip_line"] == "{who}：{text}"
    assert COPY is DAY_NOTE


def test_copy_no_banned_words_day_note():
    assert len(COPY) == 48
    assert len(ANNO) == 8
    assert set(ANNO) == {key for key in ANNO if key.startswith("anno_")}
    blob = "\n".join(COPY.values())
    for word in BANNED:
        assert word not in blob, word
    for key, value in ANNO.items():
        assert key.startswith("anno_")
    for path in SURFACES:
        text = path.read_text(encoding="utf-8")
        for word in BANNED:
            assert word not in text, f"{word} in {path}"
        assert "anno_" not in text
        assert "摘要" not in text
        for needle in ("微信", "wechat", "miniprogram", "小程序"):
            assert needle.lower() not in text.lower()
    for key in SAMPLE_KEYS:
        assert COPY[key] not in Path("app/templates/day.html").read_text(encoding="utf-8")
        assert COPY[key] not in Path("app/templates/fragments/stage_inner.html").read_text(encoding="utf-8")
    script = Path("app/static/day_note.js").read_text(encoding="utf-8")
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in script)
    layout = Path("app/templates/layout.html").read_text(encoding="utf-8")
    assert layout.count('class="tab{') == 3


def test_no_new_bottom_tab_copy_day_note():
    layout = Path("app/templates/layout.html").read_text(encoding="utf-8")
    assert layout.count('class="tab{') == 3
    assert [COPY[key] for key in ("tab_time", "tab_family", "tab_settings")] == ["时光", "家人", "设置"]
    assert "写一句" not in layout
    css = Path("app/static/app.css").read_text(encoding="utf-8")
    assert "min(720px, 100%)" in css
    assert "@media (min-width: 1024px)" in css
    assert ".app:has(.day-page)" in css
    assert "radial-gradient" not in css.split(".warm-note", 1)[1].split(".day-note-line", 1)[0]


def test_version_and_release_keep_tables():
    assert app.version == "2026.10.25.7"
    notes = Path("releases/v2026.10.25.6.md").read_text(encoding="utf-8")
    assert "v2026.10.25.5" in notes
    assert "day_notes" in notes and "day_note_children" in notes
    assert "down -v" in notes
    assert "不要删表" in notes
    assert "DROP TABLE" not in notes
    assert "DROP COLUMN" not in notes
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.split("## v2026.10.25.6", 1)[0].startswith("# Changelog\n\n## v2026.10.25.7")
    assert "DROP TABLE" not in changelog.split("## v2026.10.25.5", 1)[0]
