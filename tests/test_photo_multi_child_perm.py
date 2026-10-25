"""Who can read a photo, who can change its children, and who is turned away."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.constants import FORBIDDEN, PHOTO_MULTI_CHILD
from app.db import SessionLocal
from app.main import app
from app.models import Membership, Photo
from tests.test_acceptance import JPEG, auth
from tests.test_journal import _family, _invite
from tests.test_photo_multi_child import DAY, _add, _photo

pytestmark = pytest.mark.filterwarnings("ignore")


def test_removed_member_photo_403(client: TestClient):
    child_id = _family(client, "p-rm@example.com", "妈妈", "移出家", child="秘密果果")
    member = _invite(client, "p-rm-up@example.com", "uploader", "爸爸")
    created = _photo(client, [child_id])
    photo_id = created.json()["id"]
    session = SessionLocal()
    try:
        user_id = member.get("/api/me").json()["id"]
        row = session.scalar(select(Membership).where(Membership.user_id == user_id))
        assert row is not None
        session.delete(row)
        session.commit()
    finally:
        session.close()
    denied = member.post(
        "/api/photos",
        data={"kind": "child", "child_ids": str(child_id), "taken_on": DAY},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": FORBIDDEN}
    assert "code" not in denied.json()
    assert "秘密果果" not in denied.text
    hidden = member.patch(f"/api/photos/{photo_id}", json={"child_ids": [child_id]})
    assert hidden.status_code == 403
    assert "code" not in hidden.json()
    assert "秘密果果" not in hidden.text


def test_demoted_mid_photo_submit(client: TestClient):
    child_id = _family(client, "p-dem@example.com", "妈妈", "降级家", child="果果")
    member = _invite(client, "p-dem-up@example.com", "uploader", "爸爸")
    user_id = member.get("/api/me").json()["id"]
    assert client.patch(f"/api/members/{user_id}", json={"role": "viewer"}).status_code == 200
    denied = member.post(
        "/api/photos",
        data={"kind": "child", "child_ids": str(child_id), "taken_on": DAY},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "photo_forbidden_role"
    assert denied.json()["detail"] == FORBIDDEN
    session = SessionLocal()
    try:
        assert session.scalars(select(Photo).where(Photo.uploader_id == user_id)).all() == []
    finally:
        session.close()
    source_page = member.get("/")
    assert "/upload" not in source_page.text
    assert PHOTO_MULTI_CHILD["ro_meta"] in source_page.text or "只读 · 只能看" in source_page.text


def test_photo_not_found_same_for_missing_and_other_family(client: TestClient):
    child_id = _family(client, "p-404@example.com", "妈妈", "找不到家", child="果果")
    created = _photo(client, [child_id])
    photo_id = created.json()["id"]
    other = TestClient(app)
    foreign = _family(other, "p-404-b@example.com", "别人", "别家", child="小小")
    missing = client.patch("/api/photos/999999", json={"child_ids": [child_id]})
    foreign_patch = other.patch(f"/api/photos/{photo_id}", json={"child_ids": [foreign]})
    assert missing.status_code == foreign_patch.status_code == 404
    assert missing.json() == foreign_patch.json() == {"detail": "没有这张照片", "code": "photo_not_found"}
    assert "果果" not in missing.text
    assert "果果" not in foreign_patch.text
    viewer = _invite(client, "p-404-ro@example.com", "viewer", "外婆")
    viewer_missing = viewer.patch("/api/photos/999999", json={"child_ids": [child_id]})
    assert viewer_missing.status_code == 403
    assert viewer_missing.json()["code"] == "photo_forbidden_role"


def test_other_uploader_cannot_edit_assoc_admin_can(client: TestClient):
    child_id = _family(client, "p-auth@example.com", "妈妈", "作者家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    author = _invite(client, "p-auth-a@example.com", "uploader", "爸爸")
    other = _invite(client, "p-auth-b@example.com", "uploader", "姑姑")
    created = _photo(author, [child_id])
    photo_id = created.json()["id"]
    forbidden = other.patch(f"/api/photos/{photo_id}", json={"child_ids": [younger]})
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "photo_not_author"
    assert other.get(f"/photos/{photo_id}/edit").status_code == 403
    edited = client.patch(f"/api/photos/{photo_id}", json={"child_ids": [younger, child_id]})
    assert edited.status_code == 200, edited.text
    assert edited.json()["child_ids"] == [younger, child_id]
    own = author.get(f"/photos/{photo_id}/edit")
    assert own.status_code == 200
    assert PHOTO_MULTI_CHILD["u_submit_edit"] in own.text


def test_outsider_photo_pages_stay_403(client: TestClient):
    child_id = _family(client, "p-out@example.com", "妈妈", "外人家庭名", child="秘密果果")
    created = _photo(client, [child_id])
    photo_id = created.json()["id"]
    outsider = TestClient(app)
    auth(outsider, "p-out-x@example.com", "外人")
    for url in (f"/photos/{photo_id}", f"/photos/{photo_id}/edit", f"/?child={child_id}"):
        page = outsider.get(url)
        assert page.status_code == 403, url
        assert "秘密果果" not in page.text
        assert "外人家庭名" not in page.text
    api = outsider.get(f"/media/{photo_id}")
    assert api.status_code == 403
    assert api.json() == {"detail": FORBIDDEN}
