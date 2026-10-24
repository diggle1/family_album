"""Who may change a child association."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.constants import FORBIDDEN, MULTI_CHILD
from app.db import SessionLocal
from app.main import app
from app.models import Journal, Membership
from app.services.clock import shanghai_today
from tests.test_acceptance import auth
from tests.test_journal import _family, _invite


def _post(client: TestClient, child_id: int, body: str = "还有一句"):
    return client.post(
        "/api/journals",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "child_ids": [child_id],
            "on_date": shanghai_today().isoformat(),
            "body": body,
            "photo_ids": [],
        },
    )


def test_viewer_no_write_entry_api_403(client: TestClient):
    child_id = _family(client, "view-m@example.com", "妈妈", "只读家")
    viewer = _invite(client, "view-r@example.com", "viewer", "奶奶")
    page = viewer.get("/")
    assert page.status_code == 200
    assert 'class="write-pill"' not in page.text
    assert viewer.get("/journals/new").status_code == 403
    assert viewer.get("/videos/new").status_code == 403
    denied = _post(viewer, child_id)
    assert denied.status_code == 403
    assert denied.json()["code"] == "journal_readonly"
    assert denied.json()["detail"] == FORBIDDEN


def test_viewer_assoc_403(client: TestClient):
    child_id = _family(client, "assoc-a@example.com", "妈妈", "关联权限家")
    created = _post(client, child_id, "家长写的")
    assert created.status_code == 201, created.text
    journal_id = created.json()["id"]
    viewer = _invite(client, "assoc-v@example.com", "viewer", "奶奶")
    edited = viewer.put(
        f"/api/journals/{journal_id}",
        json={
            "child_ids": [child_id],
            "on_date": shanghai_today().isoformat(),
            "body": "想改",
            "photo_ids": [],
        },
    )
    assert edited.status_code == 403
    assert edited.json()["code"] == "journal_readonly"
    source = Path("app/static/journal.js").read_text(encoding="utf-8")
    assert 'copy("err_demoted")' in source
    assert MULTI_CHILD["err_demoted_j"]
    assert MULTI_CHILD["ro_meta"]


def test_removed_member_403(client: TestClient):
    child_id = _family(client, "gone-m@example.com", "妈妈", "移出家")
    member = _invite(client, "gone-u@example.com", "uploader", "爸爸")
    session = SessionLocal()
    try:
        row = session.scalar(select(Membership).where(Membership.role == "uploader"))
        assert row is not None
        session.delete(row)
        session.commit()
    finally:
        session.close()
    denied = _post(member, child_id)
    assert denied.status_code == 403
    assert "code" not in denied.json()
    assert denied.json()["detail"] == FORBIDDEN


def test_demoted_mid_submit(client: TestClient):
    child_id = _family(client, "demo-m@example.com", "妈妈", "降级家")
    member = _invite(client, "demo-u@example.com", "uploader", "爸爸")
    user_id = member.get("/api/me").json()["id"]
    assert client.patch(f"/api/members/{user_id}", json={"role": "viewer"}).status_code == 200
    denied = _post(member, child_id, "降级后这一句")
    assert denied.status_code == 403
    assert denied.json()["code"] == "journal_readonly"
    session = SessionLocal()
    try:
        assert session.scalar(select(Journal).where(Journal.body == "降级后这一句")) is None
    finally:
        session.close()
