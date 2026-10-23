"""Acceptance tests for v2026.10.23.1. Names in fixtures are deliberately unique."""

from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select
import pytest
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.main import app
from app.models import AuthCode, Invite, Membership, Photo
from app.services.clock import age_label, shanghai_today

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
WEBP = b"RIFF" + b"\x18\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x00" * 12
GIF = b"GIF89a" + b"\x00" * 16

FAM = "LeakFamZZ9"
CHILD = "LeakChildZZ9"
ADMIN_NAME = "LeakUserZZ9"


def auth(client: TestClient, email: str, name: str | None = None, relation: str | None = None):
    created = client.post("/auth/request-code", json={"email": email})
    assert created.status_code == 200, created.text
    assert created.json() == {"ok": True}
    verified = client.post("/auth/verify", json={"email": email, "code": "000000"})
    assert verified.status_code == 200, verified.text
    if name is not None:
        body: dict = {"name": name}
        if relation is not None:
            body["relation_label"] = relation
        renamed = client.patch("/api/me", json=body)
        assert renamed.status_code == 200, renamed.text
    return verified


def upload(client: TestClient, *, kind: str, content: bytes = JPEG, filename: str = "a.jpg", mime: str = "image/jpeg", child_id: int | None = None, taken_on: str | None = None, send_child: bool = True):
    data: dict[str, str] = {"kind": kind}
    if send_child and child_id is not None:
        data["child_id"] = str(child_id)
    if taken_on is not None:
        data["taken_on"] = taken_on
    return client.post("/api/photos", data=data, files={"file": (filename, content, mime)})


def photos() -> list[Photo]:
    session = SessionLocal()
    try:
        return list(session.scalars(select(Photo).order_by(Photo.id.asc())).all())
    finally:
        session.close()


def test_cross_origin_post_is_rejected(client: TestClient):
    response = client.post(
        "/auth/request-code",
        json={"email": "a@example.com"},
        headers={"origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "没有访问权限"}


def test_request_code_does_not_return_the_code(client: TestClient):
    response = client.post("/auth/request-code", json={"email": "a@example.com"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    bare = client.post("/auth/verify", json={"email": "nobody@example.com", "code": "000000"})
    assert bare.status_code == 400


def test_patch_me_name_and_relation(client: TestClient):
    auth(client, "me@example.com")
    missing = client.patch("/api/me", json={"relation_label": "妈妈"})
    assert missing.status_code == 400
    assert client.get("/api/me").json()["membership"] is None

    renamed = client.patch("/api/me", json={"name": "  小美  "})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "小美"
    assert "child_id" not in (renamed.json()["membership"] or {})

    blank = client.patch("/api/me", json={"name": "   "})
    assert blank.status_code == 400
    huge = client.patch("/api/me", json={"name": "名" * 41})
    assert huge.status_code == 400
    assert client.get("/api/me").json()["name"] == "小美"

    assert client.post("/api/families", json={"name": "小美家"}).status_code == 201
    labeled = client.patch("/api/me", json={"relation_label": "  果果妈妈  "})
    assert labeled.status_code == 200
    membership = labeled.json()["membership"]
    assert membership["relation_label"] == "果果妈妈"
    assert "child_id" not in membership
    cleared = client.patch("/api/me", json={"relation_label": "  "})
    assert cleared.json()["membership"]["relation_label"] is None
    nulled = client.patch("/api/me", json={"relation_label": None})
    assert nulled.json()["membership"]["relation_label"] is None


def test_me_requires_login_and_logout(client: TestClient):
    anonymous = client.get("/api/me")
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"] == "请先登录"
    auth(client, "me@example.com", "小美")
    assert client.get("/api/me").json()["email"] == "me@example.com"
    logged_out = client.post("/auth/logout")
    assert logged_out.status_code == 200
    assert client.get("/api/me").status_code == 401


def test_second_family_is_rejected(client: TestClient):
    auth(client, "a@example.com", "安安")
    assert client.post("/api/families", json={"name": "安安家"}).status_code == 201
    again = client.post("/api/families", json={"name": "第二家"})
    assert again.status_code == 400
    assert client.get("/api/families/current").json()["name"] == "安安家"

    guest = TestClient(app)
    auth(guest, "guest@example.com", "客人")
    token = client.post(
        "/api/families/invites",
        json={"email": "guest@example.com", "role": "uploader"},
    ).json()["token"]
    assert guest.post("/api/families", json={"name": "客人自己的家"}).status_code == 201
    rejected = guest.post(f"/api/invites/{token}/accept")
    assert rejected.status_code == 400
    assert guest.get("/api/families/current").json()["name"] == "客人自己的家"

    fresh = TestClient(app)
    auth(fresh, "fresh@example.com", "新人")
    fresh_token = client.post(
        "/api/families/invites",
        json={"email": "fresh@example.com", "role": "viewer"},
    ).json()["token"]
    assert fresh.post(f"/api/invites/{fresh_token}/accept").status_code == 200
    assert fresh.post("/api/families", json={"name": "不该建成"}).status_code == 400
    assert fresh.get("/api/families/current").json()["name"] == "安安家"

    session = SessionLocal()
    try:
        assert session.scalar(select(func.count()).select_from(Membership)) == 3
    finally:
        session.close()


def test_current_family_404_and_anonymous_403(client: TestClient):
    anonymous = client.get("/api/families/current")
    assert anonymous.status_code == 403
    assert anonymous.json() == {"detail": "没有访问权限"}
    auth(client, "solo@example.com", "独行")
    missing = client.get("/api/families/current")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "还没有家庭"


def test_invalid_invite_and_expiry(client: TestClient, db):
    auth(client, "a@example.com", "安安")
    client.post("/api/families", json={"name": "安安家"})
    bad_email = client.post("/api/families/invites", json={"email": "not-an-email", "role": "viewer"})
    assert bad_email.status_code == 400
    bad_role = client.post("/api/families/invites", json={"email": "x@example.com", "role": "owner"})
    assert bad_role.status_code == 400
    already = client.post("/api/families/invites", json={"email": "a@example.com", "role": "viewer"})
    assert already.status_code == 400

    created = client.post("/api/families/invites", json={"email": "late@example.com", "role": "viewer"})
    assert created.status_code == 201
    token = created.json()["token"]
    invite = db.scalar(select(Invite).where(Invite.token == token))
    invite.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    late = TestClient(app)
    auth(late, "late@example.com", "迟到")
    assert late.post(f"/api/invites/{token}/accept").status_code == 400

    mismatch = client.post("/api/families/invites", json={"email": "right@example.com", "role": "viewer"})
    wrong = TestClient(app)
    auth(wrong, "wrong@example.com", "错人")
    assert wrong.post(f"/api/invites/{mismatch.json()['token']}/accept").status_code == 400


def test_last_admin_cannot_be_demoted(client: TestClient):
    auth(client, "a@example.com", "安安")
    client.post("/api/families", json={"name": "安安家"})
    me = client.get("/api/me").json()
    demote = client.patch(f"/api/members/{me['id']}", json={"role": "uploader"})
    assert demote.status_code == 400
    assert demote.json()["detail"] == "不能降级家庭里最后一位家长管理员"
    assert client.get("/api/me").json()["membership"]["role"] == "admin"
    same = client.patch(f"/api/members/{me['id']}", json={"role": "admin"})
    assert same.status_code == 200

    token = client.post("/api/families/invites", json={"email": "b@example.com", "role": "admin"}).json()["token"]
    other = TestClient(app)
    auth(other, "b@example.com", "贝贝")
    assert other.post(f"/api/invites/{token}/accept").status_code == 200
    assert client.patch(f"/api/members/{other.get('/api/me').json()['id']}", json={"role": "viewer"}).status_code == 200
    assert client.patch(f"/api/members/{me['id']}", json={"role": "viewer"}).status_code == 400
    assert client.get("/api/me").json()["membership"]["role"] == "admin"

    uploader = TestClient(app)
    up_token = client.post("/api/families/invites", json={"email": "c@example.com", "role": "uploader"}).json()["token"]
    auth(uploader, "c@example.com", "上传")
    uploader.post(f"/api/invites/{up_token}/accept")
    forbidden = uploader.patch(f"/api/members/{me['id']}", json={"role": "viewer"})
    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": "没有访问权限"}
    assert client.get("/api/me").json()["membership"]["role"] == "admin"


def test_child_photo_requires_taken_on_and_child_id(client: TestClient, monkeypatch):
    auth(client, "a@example.com", "安安")
    client.post("/api/families", json={"name": "安安家"})
    only = client.post("/api/children", json={"name": "果果", "birth_date": "2024-07-12"})
    assert only.status_code == 201
    child_id = only.json()["id"]

    missing_date = upload(client, kind="child", child_id=child_id, send_child=True)
    assert missing_date.status_code == 400
    assert missing_date.json()["detail"] == "孩子照片必须填写拍摄日期"
    bad_date = upload(client, kind="child", child_id=child_id, taken_on="2024-13-40")
    assert bad_date.status_code == 400
    assert photos() == []

    implied = upload(client, kind="child", taken_on="2026-10-22", send_child=False)
    assert implied.status_code == 201, implied.text
    assert implied.json()["child_id"] == child_id
    assert implied.json()["taken_on"] == "2026-10-22"
    assert "storage_path" not in implied.text

    second = client.post("/api/children", json={"name": "豆豆", "birth_date": "2025-01-02"})
    assert second.status_code == 201
    ambiguous = upload(client, kind="child", taken_on="2026-10-22", send_child=False)
    assert ambiguous.status_code == 400
    assert ambiguous.json()["detail"] == "孩子照片必须指定孩子"

    other = TestClient(app)
    auth(other, "b@example.com", "贝贝")
    other.post("/api/families", json={"name": "贝贝家"})
    foreign = other.post("/api/children", json={"name": "小小", "birth_date": "2023-01-01"}).json()["id"]
    crossed = upload(client, kind="child", child_id=foreign, taken_on="2026-10-22")
    assert crossed.status_code == 400
    assert all(row.child_id != foreign for row in photos())

    monkeypatch.setattr("app.services.photos.shanghai_today", lambda: date(2026, 10, 23))
    smuggled = upload(client, kind="self", child_id=child_id, taken_on="2026-01-01")
    assert smuggled.status_code == 400
    assert smuggled.json()["detail"] == "本人照片不能挂到孩子"
    self_photo = upload(client, kind="self", send_child=False)
    assert self_photo.status_code == 201, self_photo.text
    assert self_photo.json()["child_id"] is None
    assert self_photo.json()["taken_on"] == "2026-10-23"
    override = upload(client, kind="self", taken_on="2026-02-02", send_child=False)
    assert override.status_code == 201
    assert override.json()["taken_on"] == "2026-02-02"

    assert upload(client, kind="note", taken_on="2026-10-22", child_id=child_id).status_code == 400
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22", content=GIF, filename="a.gif", mime="image/gif").status_code == 400
    png = upload(client, kind="child", child_id=child_id, taken_on="2026-10-01", content=PNG, filename="a.png", mime="image/png")
    assert png.status_code == 201
    webp = upload(client, kind="self", send_child=False, content=WEBP, filename="a.webp", mime="image/webp")
    assert webp.status_code == 201


def test_self_photo_never_joins_child_timeline(client: TestClient):
    auth(client, "a@example.com", "小美")
    client.post("/api/families", json={"name": "小美家"})
    child_id = client.post("/api/children", json={"name": "果果", "birth_date": "2024-07-12"}).json()["id"]
    child_photo = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    self_photo = upload(client, kind="self", taken_on="2026-10-22", send_child=False)
    assert child_photo.status_code == 201 and self_photo.status_code == 201
    timeline = client.get(f"/api/children/{child_id}/timeline")
    assert timeline.status_code == 200
    body = timeline.json()
    assert body["child"]["name"] == "果果"
    assert body["days"][0]["date_label"] == "10月22日"
    assert body["days"][0]["weekday"] == "周四"
    assert body["days"][0]["age_label"] == "2岁3个月"
    assert body["days"][0]["age_label"] == age_label(date(2024, 7, 12), date(2026, 10, 22))
    assert body["days"][0]["byline"] == "小美 上传"
    ids = [item["id"] for day in body["days"] for item in day["photos"]]
    assert ids == [child_photo.json()["id"]]
    assert self_photo.json()["id"] not in ids
    assert "storage_path" not in timeline.text
    album = client.get(f"/api/photos/self/{client.get('/api/me').json()['id']}")
    assert album.status_code == 200
    assert [item["id"] for item in album.json()["photos"]] == [self_photo.json()["id"]]
    assert album.json()["name"] == "小美"


def test_media_is_private(client: TestClient):
    auth(client, "a@example.com", ADMIN_NAME)
    client.post("/api/families", json={"name": FAM})
    child_id = client.post("/api/children", json={"name": CHILD, "birth_date": "2024-07-12"}).json()["id"]
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    photo_id = created.json()["id"]
    media = client.get(f"/media/{photo_id}")
    assert media.status_code == 200
    assert media.headers["content-type"].startswith("image/jpeg")
    assert media.content.startswith(b"\xff\xd8\xff")

    stored = photos()[0].storage_path
    assert client.get(f"/static/{stored}").status_code == 404
    assert client.get(f"/{stored}").status_code == 404
    assert stored not in created.text

    outsider = TestClient(app)
    auth(outsider, "out@example.com", "外人")
    hidden = outsider.get(f"/media/{photo_id}")
    assert hidden.status_code == 403
    assert hidden.json() == {"detail": "没有访问权限"}
    assert hidden.headers["content-type"].startswith("application/json")
    assert not hidden.content.startswith(b"\xff\xd8\xff")
    anonymous = TestClient(app)
    assert anonymous.get(f"/media/{photo_id}").status_code == 403


def test_permission_matrix_and_no_leak(client: TestClient):
    auth(client, "admin@example.com", ADMIN_NAME)
    assert client.post("/api/families", json={"name": FAM}).status_code == 201
    child_id = client.post("/api/children", json={"name": CHILD, "birth_date": "2024-07-12"}).json()["id"]
    photo = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert photo.status_code == 201
    self_photo = upload(client, kind="self", send_child=False, taken_on="2026-10-23")
    admin_id = client.get("/api/me").json()["id"]

    def invite(email: str, role: str) -> TestClient:
        token = client.post("/api/families/invites", json={"email": email, "role": role}).json()["token"]
        member = TestClient(app)
        auth(member, email, email.split("@")[0])
        assert member.post(f"/api/invites/{token}/accept").status_code == 200
        return member

    uploader = invite("up@example.com", "uploader")
    viewer = invite("view@example.com", "viewer")
    outsider = TestClient(app)
    auth(outsider, "out@example.com", "外人ZZ")
    other = TestClient(app)
    auth(other, "other@example.com", "别家ZZ")
    assert other.post("/api/families", json={"name": "OtherFamZZ9"}).status_code == 201

    for member in (client, uploader, viewer):
        assert member.get(f"/api/children/{child_id}/timeline").status_code == 200
        assert member.get(f"/api/photos/self/{admin_id}").status_code == 200
        assert member.get(f"/media/{photo.json()['id']}").status_code == 200
        assert member.patch("/api/me", json={"name": member.get("/api/me").json()["name"]}).status_code == 200

    assert uploader.post("/api/photos", data={"kind": "self", "taken_on": "2026-10-23"}, files={"file": ("a.jpg", JPEG, "image/jpeg")}).status_code == 201
    assert viewer.post("/api/photos", data={"kind": "child", "child_id": str(child_id), "taken_on": "2026-10-22"}, files={"file": ("a.jpg", JPEG, "image/jpeg")}).status_code == 403
    assert outsider.post("/api/photos", data={"kind": "self"}, files={"file": ("a.jpg", JPEG, "image/jpeg")}).status_code == 403

    for blocked in (uploader, viewer, outsider):
        assert blocked.post("/api/families/invites", json={"email": "n@example.com", "role": "viewer"}).status_code == 403
        assert blocked.post("/api/children", json={"name": "新", "birth_date": "2024-01-01"}).status_code == 403
        assert blocked.patch(f"/api/members/{admin_id}", json={"role": "viewer"}).status_code == 403

    secret_bits = (FAM, CHILD, ADMIN_NAME, "admin@example.com")
    foreign_api = [
        f"/api/children/{child_id}/timeline",
        f"/api/photos/self/{admin_id}",
        f"/media/{photo.json()['id']}",
        f"/media/{self_photo.json()['id']}",
    ]
    foreign_pages = [
        f"/people/{admin_id}/album",
        f"/?child={child_id}",
        f"/photos/{photo.json()['id']}",
        f"/children/{child_id}/days/2026-10-22",
    ]
    for actor in (outsider, other):
        for url in foreign_api:
            response = actor.get(url)
            assert response.status_code == 403, url
            assert response.json() == {"detail": "没有访问权限"}
            for bit in secret_bits:
                assert bit not in response.text
        for url in foreign_pages:
            page = actor.get(url)
            assert page.status_code == 403, url
            assert "没有访问权限" in page.text
            for bit in secret_bits:
                assert bit not in page.text

    for url in ("/api/children", "/api/members", "/family/members", "/family/settings", "/people"):
        denied = outsider.get(url)
        assert denied.status_code == 403, url
        assert "没有访问权限" in denied.text
        for bit in secret_bits:
            assert bit not in denied.text
        own = other.get(url)
        assert own.status_code == 200, url
        for bit in secret_bits:
            assert bit not in own.text

    missed = other.patch(f"/api/members/{admin_id}", json={"role": "viewer"})
    assert missed.status_code == 404
    for bit in secret_bits:
        assert bit not in missed.text
    assert client.get("/api/me").json()["membership"]["role"] == "admin"

    anonymous = TestClient(app)
    for url in ("/api/children", "/api/members", f"/api/children/{child_id}/timeline", f"/api/photos/self/{admin_id}"):
        response = anonymous.get(url)
        assert response.status_code == 403
        assert response.json() == {"detail": "没有访问权限"}
        for bit in secret_bits:
            assert bit not in response.text
    login = anonymous.get("/")
    assert login.status_code == 200
    assert "发送验证码" in login.text
    for url in (
        f"/?child={child_id}",
        "/family/members",
        "/family/settings",
        "/people",
        f"/people/{admin_id}/album",
        f"/photos/{photo.json()['id']}",
    ):
        page = anonymous.get(url)
        assert page.status_code == 403, url
        assert "没有访问权限" in page.text
        for bit in secret_bits:
            assert bit not in page.text


def test_viewer_page_hides_upload_and_uploader_cannot_edit_roles(client: TestClient):
    auth(client, "admin@example.com", "安安")
    client.post("/api/families", json={"name": "安安家"})
    client.post("/api/children", json={"name": "果果", "birth_date": "2024-07-12"})
    view_token = client.post("/api/families/invites", json={"email": "view@example.com", "role": "viewer"}).json()["token"]
    up_token = client.post("/api/families/invites", json={"email": "up@example.com", "role": "uploader"}).json()["token"]
    viewer = TestClient(app)
    auth(viewer, "view@example.com", "只读")
    viewer.post(f"/api/invites/{view_token}/accept")
    page = viewer.get("/")
    assert page.status_code == 200
    assert 'class="fab"' not in page.text
    assert "上传第一张" not in page.text
    assert "/upload" not in page.text
    assert "只读 · 只能看" in page.text
    assert viewer.get("/upload").status_code == 403

    uploader = TestClient(app)
    auth(uploader, "up@example.com", "可传")
    uploader.post(f"/api/invites/{up_token}/accept")
    members = uploader.get("/family/members")
    assert members.status_code == 200
    assert 'name="role"' not in members.text
    admin_members = client.get("/family/members")
    assert 'name="role"' in admin_members.text
    assert 'class="fab"' in client.get("/").text


def test_profile_gate_hides_timeline_until_named(client: TestClient):
    auth(client, "a@example.com")
    client.post("/api/families", json={"name": FAM})
    client.post("/api/children", json={"name": CHILD, "birth_date": "2024-07-12"})
    child_id = client.get("/api/children").json()["children"][0]["id"]
    assert client.get(f"/api/children/{child_id}/timeline").status_code == 200
    page = client.get("/")
    assert page.status_code == 200
    assert "完善资料" in page.text
    assert CHILD not in page.text
    assert FAM not in page.text
    client.patch("/api/me", json={"name": "小美", "relation_label": "果果妈妈"})
    opened = client.get("/")
    assert CHILD in opened.text
    assert "完善资料" not in opened.text


def test_html_login_family_child_and_age(client: TestClient):
    assert client.post("/login/request", data={"email": "a@example.com", "next": "/"}, follow_redirects=False).status_code == 303
    verified = client.post(
        "/login/verify",
        data={"email": "a@example.com", "code": "000000", "next": "/"},
        follow_redirects=True,
    )
    assert "完善资料" in verified.text
    named = client.post("/profile", data={"name": "小美", "next": "/"}, follow_redirects=True)
    assert "还没有家庭" in named.text
    assert "创建我们的家庭" in named.text
    created = client.post("/families", data={"name": "小美家"}, follow_redirects=True)
    assert "添加孩子" in created.text
    assert "家庭里至少保留 1 名家长管理员（创建者自动担任）" in created.text
    assert "不能降级家庭里最后一位家长管理员" in created.text
    born = client.post(
        "/children",
        data={"name": "果果", "birth_date": "2024-07-12", "relation_label": "果果妈妈"},
        follow_redirects=True,
    )
    assert "果果" in born.text
    assert "还没有照片" in born.text
    assert 'class="fab"' in born.text
    uploaded = client.post(
        "/upload?kind=child",
        data={"kind": "child", "taken_on": "2026-10-22"},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
        follow_redirects=True,
    )
    assert uploaded.status_code == 200
    assert "已传到时光线上" in uploaded.text
    assert "2岁3个月" in uploaded.text
    assert "10月22日" in uploaded.text
    assert "小美 上传" in uploaded.text
    me = client.get("/api/me").json()
    assert me["membership"]["relation_label"] == "果果妈妈"


def test_db_check_rejects_illegal_photo_shape(client: TestClient, db):
    auth(client, "a@example.com", "安安")
    client.post("/api/families", json={"name": "安安家"})
    child_id = client.post("/api/children", json={"name": "果果", "birth_date": "2024-07-12"}).json()["id"]
    user_id = client.get("/api/me").json()["id"]
    family_id = client.get("/api/families/current").json()["id"]
    db.add(
        Photo(
            family_id=family_id,
            uploader_id=user_id,
            kind="self",
            child_id=child_id,
            taken_on=date(2026, 10, 23),
            storage_path="nope.jpg",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(
        Photo(
            family_id=family_id,
            uploader_id=user_id,
            kind="child",
            child_id=None,
            taken_on=date(2026, 10, 23),
            storage_path="nope.jpg",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_ac7_no_paid_export_copy():
    banned = ("付费", "导出需付费", "推荐关注", "公开分享", "发现页")
    roots = [Path("app/templates"), Path("app/static/app.css"), Path("app/static/app.js")]
    for root in roots:
        files = [root] if root.is_file() else list(root.rglob("*"))
        for path in files:
            if not path.is_file() or path.suffix not in {".html", ".css", ".js"}:
                continue
            text = path.read_text(encoding="utf-8")
            for phrase in banned:
                assert phrase not in text, f"{phrase} in {path}"


def test_upload_size_limit(client: TestClient, monkeypatch):
    auth(client, "a@example.com", "安安")
    client.post("/api/families", json={"name": "安安家"})
    child_id = client.post("/api/children", json={"name": "果果", "birth_date": "2024-07-12"}).json()["id"]
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_bytes", 8)
    response = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22", content=JPEG)
    assert response.status_code == 400
    assert photos() == []


def test_code_expired(client: TestClient, db):
    client.post("/auth/request-code", json={"email": "a@example.com"})
    row = db.scalar(select(AuthCode).where(AuthCode.email == "a@example.com"))
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    assert client.post("/auth/verify", json={"email": "a@example.com", "code": "000000"}).status_code == 400


def test_header_age_uses_shanghai_today(client: TestClient):
    auth(client, "a@example.com", "安安")
    client.post("/api/families", json={"name": "安安家"})
    child = client.post("/api/children", json={"name": "果果", "birth_date": "2024-07-12"}).json()
    timeline = client.get(f"/api/children/{child['id']}/timeline").json()
    assert timeline["child"]["age_label"] == age_label(date(2024, 7, 12), shanghai_today())
    assert timeline["days"] == []
