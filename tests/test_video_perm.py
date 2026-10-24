"""Role matrix for growth videos. Existing permission assertions are not edited."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.constants import FORBIDDEN
from app.main import app
from tests.test_acceptance import JPEG
from tests.test_video import _family, _invite, _mp4, _post

ACTIONS = ("list", "detail", "media", "poster", "create", "delete_own", "delete_other", "page_new")


@pytest.fixture(autouse=True)
def video_media(tmp_path, monkeypatch):
    root = tmp_path / "media"
    export = tmp_path / "export"
    root.mkdir()
    export.mkdir()
    monkeypatch.setenv("PHOTO_DIR", str(root))
    monkeypatch.setenv("MEDIA_ROOT", str(root))
    monkeypatch.setenv("EXPORT_TEMP_DIR", str(export))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def client(video_media):
    del video_media
    with TestClient(app) as test_client:
        yield test_client


def _actors(client: TestClient):
    child_id = _family(client, "perm-admin@example.com", "家长", "权限家", child="权限果")
    uploader = _invite(client, "perm-up@example.com", "uploader", "小姨")
    viewer = _invite(client, "perm-view@example.com", "viewer", "爷爷")
    admin_video = _post(client, child_id, _mp4(), poster=("a.jpg", JPEG, "image/jpeg"))
    own_video = _post(uploader, child_id, _mp4(4000), duration="4000", poster=("b.jpg", JPEG, "image/jpeg"))
    assert admin_video.status_code == 201, admin_video.text
    assert own_video.status_code == 201, own_video.text
    return {
        "admin": client,
        "uploader": uploader,
        "viewer": viewer,
        "child_id": child_id,
        "admin_id": admin_video.json()["id"],
        "own_id": own_video.json()["id"],
    }


@pytest.mark.parametrize(
    "role,action",
    [pytest.param(role, action, id=f"{role}-{action}") for role in ("admin", "uploader", "viewer") for action in ACTIONS],
)
def test_perm_matrix(client: TestClient, role: str, action: str):
    cast = _actors(client)
    actor = cast[role]
    child_id = cast["child_id"]
    own_id = cast["own_id"] if role == "uploader" else cast["admin_id"]
    other_id = cast["admin_id"] if role != "admin" else cast["own_id"]
    if action == "list":
        page = actor.get(f"/?child={child_id}")
        assert page.status_code == 200
        assert f"/videos/{cast['admin_id']}" in page.text
        assert "权限果" in page.text
        if role == "viewer":
            assert 'class="fab"' not in page.text
            assert "/videos/new" not in page.text
            assert "/upload" not in page.text
        else:
            assert 'class="fab"' in page.text
            assert "/videos/new" in page.text
        return
    if action == "detail":
        response = actor.get(f"/api/videos/{cast['admin_id']}")
        assert response.status_code == 200
        assert response.json()["id"] == cast["admin_id"]
        assert "storage_path" not in response.json()
        assert "private, no-store" in response.headers["cache-control"]
        return
    if action == "media":
        response = actor.get(f"/media/videos/{cast['admin_id']}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("video/mp4")
        assert response.content.startswith(b"\x00\x00\x00")
        assert "max-age=3600" in response.headers["cache-control"]
        return
    if action == "poster":
        response = actor.get(f"/media/videos/{cast['admin_id']}/poster")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/jpeg")
        assert "no-store" in response.headers["cache-control"]
        return
    if action == "create":
        response = _post(actor, child_id, _mp4(2000), duration="2000")
        if role == "viewer":
            assert response.status_code == 403
            assert response.json()["code"] == "video_forbidden_role"
            assert response.json()["detail"] == FORBIDDEN
        else:
            assert response.status_code == 201, response.text
        return
    if action == "delete_own":
        response = actor.delete(f"/api/videos/{own_id}")
        if role == "viewer":
            assert response.status_code == 403
            assert response.json()["code"] == "video_forbidden_role"
        else:
            assert response.status_code == 204
            assert response.content == b""
        return
    if action == "delete_other":
        response = actor.delete(f"/api/videos/{other_id}")
        if role == "admin":
            assert response.status_code == 204
        elif role == "uploader":
            assert response.status_code == 403
            assert response.json()["code"] == "video_not_author"
        else:
            assert response.status_code == 403
            assert response.json()["code"] == "video_forbidden_role"
        return
    page = actor.get(f"/videos/new?child={child_id}")
    if role == "viewer":
        assert page.status_code == 403
        assert "挂到这一天" not in page.text
        assert "权限果" not in page.text
    else:
        assert page.status_code == 200
        assert "挂到这一天" in page.text
        assert "data-copy-u_submit" in page.text


def test_outsider_media_and_api_403(client: TestClient):
    child_id = _family(client, "out-admin@example.com", "家长", "外人家里", child="外人不可见")
    created = _post(client, child_id, _mp4(), poster=("a.jpg", JPEG, "image/jpeg"))
    video_id = created.json()["id"]
    outsider = TestClient(app)
    for url in (
        f"/api/videos/{video_id}",
        f"/media/videos/{video_id}",
        f"/media/videos/{video_id}/poster",
        f"/videos/{video_id}",
        "/videos/new",
        f"/?child={child_id}",
    ):
        response = outsider.get(url)
        assert response.status_code == 403, url
        assert "外人不可见" not in response.text
        assert b"ftyp" not in response.content
        if url.startswith("/api/") or url.startswith("/media/"):
            assert response.json() == {"detail": FORBIDDEN}
            assert "code" not in response.json()
    posted = outsider.post(
        "/api/videos",
        data={"child_id": str(child_id), "on_date": "2026-10-24", "duration_ms": "1000"},
        files={"file": ("clip.mp4", _mp4(), "video/mp4")},
        headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"},
    )
    assert posted.status_code == 403
    assert posted.json() == {"detail": FORBIDDEN}
    script = Path("app/static/video.js").read_text(encoding="utf-8")
    assert "video_forbidden_role" in script
    assert "err_demoted" in script
