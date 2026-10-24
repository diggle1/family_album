"""Permission matrix for growth journals. Non-members stay on the existing 403."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Journal, Membership
from app.services.clock import shanghai_today
from tests.test_acceptance import auth
from tests.test_journal import _family, _invite, _post

SECRET = "保密正文ZZ"
ACTIONS = (
    "list",
    "detail",
    "create",
    "edit_own",
    "edit_other",
    "delete_own",
    "delete_other",
    "picker",
    "page_new",
    "page_edit",
)


def _world(client: TestClient):
    child_id = _family(client, "perm-admin@example.com", "家长", "密家ZZ", child="果果密名")
    admin_journal = _post(client, child_id, body=SECRET)
    assert admin_journal.status_code == 201, admin_journal.text
    uploader = _invite(client, "perm-up@example.com", "uploader", "可传")
    up_journal = _post(uploader, child_id, body="可传写的")
    assert up_journal.status_code == 201, up_journal.text
    viewer = _invite(client, "perm-view@example.com", "viewer", "外婆")
    outsider = TestClient(app)
    auth(outsider, "perm-out@example.com", "外人")
    anon = TestClient(app)
    return {
        "child_id": child_id,
        "admin": client,
        "uploader": uploader,
        "viewer": viewer,
        "outsider": outsider,
        "anon": anon,
        "own": {
            "admin": admin_journal.json()["id"],
            "uploader": up_journal.json()["id"],
            "viewer": admin_journal.json()["id"],
            "outsider": admin_journal.json()["id"],
            "anon": admin_journal.json()["id"],
        },
        "other": {
            "admin": up_journal.json()["id"],
            "uploader": admin_journal.json()["id"],
            "viewer": up_journal.json()["id"],
            "outsider": up_journal.json()["id"],
            "anon": up_journal.json()["id"],
        },
    }


def _expect(role: str, action: str) -> tuple[int, str | None]:
    if role in {"outsider", "anon"}:
        return 403, None
    if role == "viewer":
        if action in {"list", "detail"}:
            return 200, None
        return 403, "journal_readonly" if action in {"create", "edit_own", "edit_other", "delete_own", "delete_other"} else None
    if action == "create":
        return 201, None
    if action in {"list", "detail", "picker", "page_new", "page_edit", "edit_own"}:
        return 200, None
    if action == "delete_own":
        return 204, None
    if action == "edit_other":
        return 403, "journal_not_author"
    if action == "delete_other":
        return (204, None) if role == "admin" else (403, "journal_not_author")
    raise AssertionError(action)


def _call(world: dict, role: str, action: str):
    actor: TestClient = world[role]
    child_id = world["child_id"]
    today = shanghai_today().isoformat()
    own = world["own"][role]
    other = world["other"][role]
    payload = {"child_id": child_id, "on_date": today, "body": "矩阵正文", "photo_ids": []}
    if action == "list":
        return actor.get(f"/api/journals?child_id={child_id}")
    if action == "detail":
        return actor.get(f"/api/journals/{own}")
    if action == "create":
        return actor.post("/api/journals", headers={"Idempotency-Key": str(uuid.uuid4())}, json=payload)
    if action == "edit_own":
        return actor.put(f"/api/journals/{own}", json=payload)
    if action == "edit_other":
        return actor.put(f"/api/journals/{other}", json=payload)
    if action == "delete_own":
        return actor.delete(f"/api/journals/{own}")
    if action == "delete_other":
        return actor.delete(f"/api/journals/{other}")
    if action == "picker":
        return actor.get("/fragments/journal/picker")
    if action == "page_new":
        return actor.get("/journals/new")
    if action == "page_edit":
        return actor.get(f"/journals/{own}/edit")
    raise AssertionError(action)


@pytest.mark.parametrize("role", ["admin", "uploader", "viewer", "outsider", "anon"])
@pytest.mark.parametrize("action", ACTIONS)
def test_perm_matrix(client: TestClient, role: str, action: str):
    world = _world(client)
    response = _call(world, role, action)
    status, code = _expect(role, action)
    assert response.status_code == status, response.text
    if response.headers.get("content-type", "").startswith("application/json"):
        body = response.json()
        if code is None:
            assert "code" not in body
        else:
            assert body["code"] == code
    elif code == "journal_readonly":
        assert response.status_code == 403
    assert SECRET not in response.text or status == 200
    if role in {"outsider", "anon"} or (role == "viewer" and action not in {"list", "detail"}):
        assert SECRET not in response.text
        assert "果果密名" not in response.text
        assert "密家ZZ" not in response.text


def test_admin_deletes_others_but_cannot_edit(client: TestClient):
    child_id = _family(client, "adm@example.com", "家长", "家长家")
    uploader = _invite(client, "adm-up@example.com", "uploader", "可传")
    created = _post(uploader, child_id, body="别人的日志")
    journal_id = created.json()["id"]
    edited = client.put(
        f"/api/journals/{journal_id}",
        json={"child_id": child_id, "on_date": shanghai_today().isoformat(), "body": "家长改", "photo_ids": []},
    )
    assert edited.status_code == 403
    assert edited.json()["code"] == "journal_not_author"
    page = client.get(f"/journals/{journal_id}/edit")
    assert page.status_code == 403
    assert client.delete(f"/api/journals/{journal_id}").status_code == 204


def test_uploader_cannot_edit_or_delete_others(client: TestClient):
    child_id = _family(client, "up-own@example.com", "家长", "上传家")
    created = _post(client, child_id, body="家长原文")
    journal_id = created.json()["id"]
    uploader = _invite(client, "up-other@example.com", "uploader", "可传")
    edited = uploader.put(
        f"/api/journals/{journal_id}",
        json={"child_id": child_id, "on_date": shanghai_today().isoformat(), "body": "改不成", "photo_ids": []},
    )
    assert edited.status_code == 403
    assert edited.json()["code"] == "journal_not_author"
    deleted = uploader.delete(f"/api/journals/{journal_id}")
    assert deleted.status_code == 403
    assert deleted.json()["code"] == "journal_not_author"
    assert client.get(f"/api/journals/{journal_id}").json()["body"] == "家长原文"


def test_demoted_author_post_put_delete_403_readonly(client: TestClient):
    child_id = _family(client, "demo-admin@example.com", "家长", "降级家")
    author = _invite(client, "demo-up@example.com", "uploader", "小姨")
    created = _post(author, child_id, body="降级前写的")
    journal_id = created.json()["id"]
    session = SessionLocal()
    try:
        membership = session.scalar(select_membership(author))
        assert membership is not None
        membership.role = "viewer"
        session.commit()
    finally:
        session.close()
    today = shanghai_today().isoformat()
    payload = {"child_id": child_id, "on_date": today, "body": "还想写", "photo_ids": []}
    posted = author.post("/api/journals", headers={"Idempotency-Key": str(uuid.uuid4())}, json=payload)
    edited = author.put(f"/api/journals/{journal_id}", json=payload)
    deleted = author.delete(f"/api/journals/{journal_id}")
    for response in (posted, edited, deleted):
        assert response.status_code == 403
        assert response.json()["code"] == "journal_readonly"
        assert "no-store" in response.headers["cache-control"]
    detail = author.get(f"/api/journals/{journal_id}")
    assert detail.status_code == 200
    assert detail.json()["body"] == "降级前写的"
    assert detail.json()["author"] == {"label": "小姨", "former": False}
    assert "前成员" not in client.get(f"/?child={child_id}").text


def select_membership(actor: TestClient):
    from sqlalchemy import select

    user_id = actor.get("/api/me").json()["id"]
    return select(Membership).where(Membership.user_id == user_id)


def test_removed_member_403_without_code_and_no_content(client: TestClient):
    child_id = _family(client, "rm-admin@example.com", "家长", "移出家")
    author = _invite(client, "rm-up@example.com", "uploader", "小姨")
    created = _post(author, child_id, body=SECRET)
    journal_id = created.json()["id"]
    session = SessionLocal()
    try:
        membership = session.scalar(select_membership(author))
        assert membership is not None
        session.delete(membership)
        session.commit()
    finally:
        session.close()
    denied = author.get(f"/api/journals/{journal_id}")
    assert denied.status_code == 403
    assert denied.json() == {"detail": "没有访问权限"}
    assert SECRET not in denied.text
    assert client.delete(f"/api/journals/{journal_id}").status_code == 204
    session = SessionLocal()
    try:
        row = session.get(Journal, journal_id)
        assert row is not None and row.body == ""
    finally:
        session.close()


def test_foreign_missing_deleted_ids_same_404(client: TestClient):
    child_id = _family(client, "same-a@example.com", "家长", "同404家")
    other = TestClient(app)
    foreign_child = _family(other, "same-b@example.com", "别人", "另一家")
    foreign = _post(other, foreign_child, body="别家正文")
    created = _post(client, child_id, body=SECRET)
    assert foreign.status_code == created.status_code == 201
    journal_id = created.json()["id"]
    assert client.delete(f"/api/journals/{journal_id}").status_code == 204
    deleted = client.get(f"/api/journals/{journal_id}")
    missing = client.get("/api/journals/999999")
    outside = client.get(f"/api/journals/{foreign.json()['id']}")
    assert deleted.status_code == 404
    assert deleted.content == missing.content == outside.content
    assert deleted.json() == {"detail": "找不到", "code": "journal_not_found"}
    assert "no-store" in deleted.headers["cache-control"]
    assert SECRET not in deleted.text


def test_403_body_has_no_journal_text(client: TestClient):
    child_id = _family(client, "leak-a@example.com", "家长", "密家ZZ", child="果果密名")
    created = _post(client, child_id, body=SECRET)
    journal_id = created.json()["id"]
    outsider = TestClient(app)
    auth(outsider, "leak-b@example.com", "外人")
    anon = TestClient(app)
    for actor in (outsider, anon):
        for response in (
            actor.get(f"/api/journals?child_id={child_id}"),
            actor.get(f"/api/journals/{journal_id}"),
            actor.post("/api/journals", json={"body": SECRET}),
            actor.get(f"/journals/{journal_id}"),
        ):
            assert response.status_code == 403
            assert SECRET not in response.text
            assert "果果密名" not in response.text
            assert "密家ZZ" not in response.text
            if response.headers.get("content-type", "").startswith("application/json"):
                assert response.json() == {"detail": "没有访问权限"}


def test_expired_session_is_403_without_content(client: TestClient):
    child_id = _family(client, "exp@example.com", "家长", "过期家", child="果果密名")
    created = _post(client, child_id, body=SECRET)
    journal_id = created.json()["id"]
    client.cookies.set("session", "expired-token")
    response = client.get(f"/api/journals/{journal_id}")
    assert response.status_code == 403
    assert response.json() == {"detail": "没有访问权限"}
    assert SECRET not in response.text
    assert "果果密名" not in response.text


def test_viewer_pages_have_no_write_entry(client: TestClient):
    child_id = _family(client, "view-a@example.com", "家长", "只读家")
    created = _post(client, child_id, body="给外婆看")
    journal_id = created.json()["id"]
    viewer = _invite(client, "view-b@example.com", "viewer", "外婆")
    timeline = viewer.get(f"/?child={child_id}")
    assert timeline.status_code == 200
    assert "write-pill" not in timeline.text
    assert "/journals/new" not in timeline.text
    assert "给外婆看" in timeline.text
    detail = viewer.get(f"/journals/{journal_id}")
    assert detail.status_code == 200
    assert "data-journal-more" not in detail.text
    assert "data-journal-delete" not in detail.text
    assert viewer.get("/journals/new").status_code == 403


def test_list_ignores_family_id_param(client: TestClient):
    child_id = _family(client, "famq@example.com", "家长", "参数家")
    created = _post(client, child_id, body="只看本家")
    assert created.status_code == 201
    plain = client.get(f"/api/journals?child_id={child_id}")
    tricked = client.get(f"/api/journals?child_id={child_id}&family_id=99999")
    assert plain.status_code == tricked.status_code == 200
    assert plain.json() == tricked.json()
    assert plain.json()["journals"][0]["id"] == created.json()["id"]
