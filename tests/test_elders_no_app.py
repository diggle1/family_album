"""长辈免装 v2026.10.25.2。复制、门口笺、收藏便签、访问地址。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.constants import ELDERS_DRAFT_KEYS, ELDERS_NO_APP
from app.elders_copy import ELDERS_NO_APP as COPY
from app.main import app
from app.models import Invite
from app.services.elders import (
    BODY_INVALID,
    NOT_VIEWER,
    TARGET_NOT_FOUND,
    build_paste_body,
    greeting_for,
    invite_absolute,
)
from tests.test_acceptance import auth

MOCKUP = Path("design/family_album/elders-no-app/final/mockup.html")
FINAL_MD5 = "3f41b99ba967c82e804dc80f29bd02be"
BANNED = ("会员", "开通", "商城", "发现", "广告", "下载 App", "应用商店", "去商店")
SURFACES = (
    Path("app/templates/members.html"),
    Path("app/templates/settings.html"),
    Path("app/templates/invite.html"),
    Path("app/templates/timeline.html"),
    Path("app/static/elders.js"),
    Path("app/services/elders.py"),
)


def _family(client: TestClient, email: str, name: str, family: str) -> None:
    auth(client, email, name)
    created = client.post("/api/families", json={"name": family})
    assert created.status_code == 201, created.text


def _person(admin: TestClient, email: str, role: str, name: str, relation: str | None = None) -> TestClient:
    token = admin.post("/api/families/invites", json={"email": email, "role": role}).json()["token"]
    member = TestClient(app)
    auth(member, email, name)
    assert member.post(f"/api/invites/{token}/accept").status_code == 200
    if relation is not None:
        renamed = member.patch("/api/me", json={"relation_label": relation})
        assert renamed.status_code == 200, renamed.text
    return member


def _set_url(client: TestClient, url: str | None) -> None:
    saved = client.patch("/api/families/current", json={"family_access_url": url})
    assert saved.status_code == 200, saved.text


def _assert_clean(text: str, where: str) -> None:
    for word in BANNED:
        assert word not in text, f"{word} in {where}"


def _mockup_copy() -> dict:
    node = shutil.which("node")
    assert node, "node is required to read the locked mockup"
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


def test_copy_keys_match_mockup_and_ban_words():
    assert hashlib.md5(MOCKUP.read_bytes()).hexdigest() == FINAL_MD5
    mockup = _mockup_copy()
    assert list(mockup) == list(COPY)
    assert mockup == COPY
    assert len(COPY) == 51
    assert ELDERS_DRAFT_KEYS <= set(COPY)
    assert len(ELDERS_DRAFT_KEYS) == 5
    surface = {key: value for key, value in COPY.items() if key not in ELDERS_DRAFT_KEYS}
    assert len(surface) == 46
    assert COPY is ELDERS_NO_APP
    _assert_clean("\n".join(COPY.values()), "constants")
    for path in SURFACES:
        text = path.read_text(encoding="utf-8")
        _assert_clean(text, str(path))
        for key in ELDERS_DRAFT_KEYS:
            assert COPY[key] not in text, key
    assert "/i/" not in Path("app/services/elders.py").read_text(encoding="utf-8")
    assert "/i/" not in Path("app/static/elders.js").read_text(encoding="utf-8")
    assert "/i/" not in Path("app/templates/invite.html").read_text(encoding="utf-8")
    elders_js = Path("app/static/elders.js").read_text(encoding="utf-8")
    assert not re.search(r"[\u4e00-\u9fff]", elders_js)
    copy_fn = elders_js.split("function copySlip", 1)[1].split("function bootSlip", 1)[0]
    assert copy_fn.index("data-has-url") < copy_fn.index("fetch(")
    assert "paste_body" in copy_fn
    assert "家里给您留了" not in elders_js
    css = Path("app/static/app.css").read_text(encoding="utf-8")
    sticky = css.split(".sticky {", 1)[1].split("}", 1)[0]
    assert "z-index: 4" in sticky
    assert "z-index: 6" in css.split(".sheet-mask {", 1)[1].split("}", 1)[0]


def test_paste_body_matches_contract_shape_without_demo_path():
    built = build_paste_body(
        greeting="亲爱的奶奶：",
        family_name="果果家",
        entry_url="https://album.guoguo.home",
        invite_url="https://album.guoguo.home/invites/a8f3",
    )
    assert built == COPY["paste_body"].replace("/i/a8f3", "/invites/a8f3")
    assert COPY["slip_l1"] in built
    assert COPY["slip_l2"] in built
    assert COPY["slip_l3"] in built
    _assert_clean(built, "sample")
    plain = build_paste_body(greeting=greeting_for(None), family_name="果果家", entry_url="https://album.example", invite_url=None)
    assert plain.startswith("亲爱的家人：\n\n")
    assert "邀请（还未进家）" not in plain
    assert invite_absolute("https://album.example/", "tok") == "https://album.example/invites/tok"


def test_admin_paste_body_has_no_app_store_words(client: TestClient):
    _family(client, "admin-paste@example.com", "妈妈", "果果家")
    _set_url(client, "https://album.example")
    viewer = _person(client, "nainai@example.com", "viewer", "奶奶", "奶奶")
    viewer_id = viewer.get("/api/me").json()["id"]
    pasted = client.post("/api/families/elders-paste", json={"member_user_id": viewer_id})
    assert pasted.status_code == 200, pasted.text
    assert "private, no-store" in pasted.headers["cache-control"]
    body = pasted.json()
    assert body["has_invite_line"] is False
    assert body["invite_url"] is None
    assert body["entry_url"] == "https://album.example"
    assert body["paste_body"].startswith("亲爱的奶奶：\n\n")
    assert "入口：https://album.example" in body["paste_body"]
    assert "邀请（还未进家）" not in body["paste_body"]
    assert COPY["slip_l1"] in body["paste_body"]
    assert COPY["slip_l2"] in body["paste_body"]
    _assert_clean(body["paste_body"], "member paste")

    pending = client.post("/api/families/invites", json={"email": "wait@example.com", "role": "viewer"})
    assert pending.status_code == 201
    invited = client.post("/api/families/elders-paste", json={"invite_id": pending.json()["id"]})
    assert invited.status_code == 200, invited.text
    invite_body = invited.json()
    assert invite_body["has_invite_line"] is True
    assert invite_body["paste_body"].startswith("亲爱的家人：\n\n")
    assert invite_body["invite_url"] == f"https://album.example/invites/{pending.json()['token']}"
    assert invite_body["invite_url"] in invite_body["paste_body"]
    assert "/i/" not in invite_body["paste_body"]
    _assert_clean(invite_body["paste_body"], "invite paste")


def test_empty_access_url_blocks_paste(client: TestClient):
    _family(client, "empty-url@example.com", "妈妈", "空地址家")
    viewer = _person(client, "empty-v@example.com", "viewer", "奶奶", "奶奶")
    viewer_id = viewer.get("/api/me").json()["id"]
    blocked = client.post("/api/families/elders-paste", content=b"[]", headers={"content-type": "application/json"})
    assert blocked.status_code == 400
    assert blocked.json()["code"] == "access_url_required"
    assert blocked.json()["detail"] == COPY["empty_url_hint"]
    page = client.get("/family/members")
    assert COPY["empty_url_hint"] in page.text
    assert COPY["empty_url_go"] in page.text
    assert 'data-has-url="0"' in page.text
    assert client.post("/api/families/elders-paste", json={"member_user_id": viewer_id}).json()["code"] == "access_url_required"
    current = client.get("/api/families/current")
    assert current.json()["family_access_url"] is None
    assert "private, no-store" in current.headers["cache-control"]


def test_access_url_null_means_block_paste(client: TestClient):
    _family(client, "null-url@example.com", "妈妈", "未填家")
    _person(client, "null-v@example.com", "viewer", "外婆")
    assert client.get("/api/families/current").json()["family_access_url"] is None
    denied = client.post("/api/families/elders-paste", json={"member_user_id": 1})
    assert denied.status_code == 400
    assert denied.json()["code"] == "access_url_required"


def test_access_url_rejects_bad_scheme_and_keeps_last_good_value(client: TestClient):
    _family(client, "scheme@example.com", "妈妈", "地址家")
    bad_values = [
        "ftp://album.example",
        "javascript:alert(1)",
        "album.example",
        "https://album.example/a b",
        "https://album.example\n",
        " https://album.example",
        "https://" + ("a" * 506),
        12,
    ]
    for value in bad_values:
        rejected = client.patch("/api/families/current", json={"family_access_url": value})
        assert rejected.status_code == 400, value
        assert rejected.json()["code"] == "access_url_invalid"
        assert rejected.json()["detail"] == COPY["url_help"]
    assert client.get("/api/families/current").json()["family_access_url"] is None
    kept = client.patch("/api/families/current", json={"family_access_url": "HTTPS://Album.Example/root"})
    assert kept.status_code == 200
    assert kept.json()["family_access_url"] == "HTTPS://Album.Example/root"
    still = client.patch("/api/families/current", json={"family_access_url": "notaurl"})
    assert still.status_code == 400
    assert client.get("/api/families/current").json()["family_access_url"] == "HTTPS://Album.Example/root"
    cleared = client.patch("/api/families/current", json={"family_access_url": "   "})
    assert cleared.status_code == 200
    assert cleared.json()["family_access_url"] is None
    again = client.patch("/api/families/current", json={"family_access_url": None})
    assert again.json()["family_access_url"] is None


def test_access_url_visible_only_to_admin(client: TestClient):
    _family(client, "vis@example.com", "妈妈", "可见家")
    _set_url(client, "https://family.example")
    uploader = _person(client, "vis-up@example.com", "uploader", "爸爸")
    viewer = _person(client, "vis-ro@example.com", "viewer", "奶奶")
    assert client.get("/api/families/current").json()["family_access_url"] == "https://family.example"
    assert "family_access_url" not in uploader.get("/api/families/current").json()
    assert "family_access_url" not in viewer.get("/api/families/current").json()
    admin_page = client.get("/family/settings")
    assert COPY["url_label"] in admin_page.text
    assert COPY["url_help"] in admin_page.text
    assert COPY["url_save"] in admin_page.text
    assert 'name="family_access_url"' in admin_page.text
    assert COPY["url_key_hint"] not in admin_page.text
    _assert_clean(admin_page.text, "admin settings")
    for person in (uploader, viewer):
        page = person.get("/family/settings")
        assert page.status_code == 200
        assert COPY["url_label"] not in page.text
        assert "family_access_url" not in page.text
        assert COPY["url_key_hint"] not in page.text
        assert COPY["uploader_no_url_note"] not in page.text
        members = person.get("/family/members")
        assert COPY["copy_entry"] not in members.text
        assert COPY["readonly_no_copy"] not in members.text
    admin_members = client.get("/family/members")
    assert admin_members.text.count(COPY["copy_entry"]) == 1
    assert 'data-kind="member"' in admin_members.text
    assert 'data-kind="invite"' not in admin_members.text
    assert COPY["copy_cta"] in admin_members.text
    assert COPY["toast_copied"] in admin_members.text


def test_settings_form_saves_and_bad_url_stays_on_the_row(client: TestClient):
    _family(client, "form-url@example.com", "妈妈", "表单家")
    bad = client.post("/family/settings/access-url", data={"family_access_url": "ftp://nope"}, follow_redirects=False)
    assert bad.status_code == 400
    assert COPY["url_help"] in bad.text
    assert client.get("/api/families/current").json()["family_access_url"] is None
    saved = client.post(
        "/family/settings/access-url",
        data={"family_access_url": "https://form.example"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"].endswith("/family/settings?saved=url")
    page = client.get("/family/settings?saved=url")
    assert COPY["url_saved"] in page.text
    assert 'value="https://form.example"' in page.text
    uploader = _person(client, "form-up@example.com", "uploader", "爸爸")
    forbidden = uploader.post("/family/settings/access-url", data={"family_access_url": "https://evil.example"})
    assert forbidden.status_code == 403
    assert client.get("/api/families/current").json()["family_access_url"] == "https://form.example"


def test_parallel_patch_access_url_last_write(client: TestClient):
    _family(client, "race-url@example.com", "妈妈", "并发家")
    first = client.patch("/api/families/current", json={"family_access_url": "https://one.example"})
    second = client.patch("/api/families/current", json={"family_access_url": "https://two.example"})
    assert first.status_code == second.status_code == 200
    assert client.get("/api/families/current").json()["family_access_url"] == "https://two.example"
    other = TestClient(app)
    other.cookies.update(client.cookies)
    barrier = threading.Barrier(2)
    results: list[int] = []

    def go(actor: TestClient, url: str) -> None:
        barrier.wait(5)
        results.append(actor.patch("/api/families/current", json={"family_access_url": url}).status_code)

    threads = [
        threading.Thread(target=go, args=(client, "https://left.example")),
        threading.Thread(target=go, args=(other, "https://right.example")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert results == [200, 200]
    assert client.get("/api/families/current").json()["family_access_url"] in {
        "https://left.example",
        "https://right.example",
    }


def test_family_id_on_the_request_is_ignored(client: TestClient):
    _family(client, "own-url@example.com", "妈妈", "本家")
    other = TestClient(app)
    _family(other, "other-url@example.com", "别人", "别家")
    other_id = other.get("/api/families/current").json()["id"]
    saved = client.patch(
        "/api/families/current",
        params={"family_id": other_id},
        json={"family_access_url": "https://mine.example", "family_id": other_id},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["id"] != other_id
    assert saved.json()["family_access_url"] == "https://mine.example"
    assert other.get("/api/families/current").json()["family_access_url"] is None
    _person(client, "own-v@example.com", "viewer", "奶奶")
    viewer_id = client.get("/api/members").json()["members"][-1]["user_id"]
    pasted = client.post(
        "/api/families/elders-paste",
        json={"member_user_id": viewer_id, "family_id": other_id},
    )
    assert pasted.status_code == 200
    assert "本家" in pasted.json()["paste_body"]
    assert "别家" not in pasted.json()["paste_body"]


def test_uploader_viewer_paste_403(client: TestClient):
    _family(client, "perm-url@example.com", "妈妈", "权限家")
    _set_url(client, "https://perm.example")
    uploader = _person(client, "perm-up@example.com", "uploader", "爸爸")
    viewer = _person(client, "perm-ro@example.com", "viewer", "奶奶")
    target = viewer.get("/api/me").json()["id"]
    for actor in (uploader, viewer):
        pasted = actor.post("/api/families/elders-paste", json={"member_user_id": target})
        edited = actor.patch("/api/families/current", json={"family_access_url": "https://nope.example"})
        assert pasted.status_code == edited.status_code == 403
        assert pasted.json()["code"] == "elders_paste_forbidden"
        assert edited.json()["code"] == "access_url_forbidden"
        assert pasted.json()["detail"] == edited.json()["detail"] == COPY["err_403"]
        assert "private, no-store" in pasted.headers["cache-control"]
    assert client.get("/api/families/current").json()["family_access_url"] == "https://perm.example"


def test_uploader_no_copy_entry_api_403(client: TestClient):
    _family(client, "no-entry@example.com", "妈妈", "无入口家")
    uploader = _person(client, "no-entry-up@example.com", "uploader", "爸爸")
    denied = uploader.post("/api/families/elders-paste", json={})
    assert denied.status_code == 403
    assert denied.json()["code"] == "elders_paste_forbidden"
    assert COPY["copy_entry"] not in uploader.get("/family/members").text


def test_outsider_elders_api_403(client: TestClient):
    _family(client, "gate-url@example.com", "妈妈", "门禁家")
    _set_url(client, "https://gate.example")
    outsider = TestClient(app)
    auth(outsider, "outsider-url@example.com", "路人")
    anonymous = TestClient(app)
    for actor in (outsider, anonymous):
        pasted = actor.post("/api/families/elders-paste", json={"member_user_id": 1})
        edited = actor.patch("/api/families/current", json={"family_access_url": "https://x.example"})
        assert pasted.status_code == edited.status_code == 403
        assert pasted.json() == edited.json() == {"detail": "没有访问权限"}
        assert "code" not in pasted.json()
    assert client.get("/api/families/current").json()["family_access_url"] == "https://gate.example"


def test_demoted_mid_paste_403(client: TestClient):
    _family(client, "demote-a@example.com", "妈妈", "降级家")
    _set_url(client, "https://demote.example")
    second = _person(client, "demote-b@example.com", "admin", "爸爸")
    viewer = _person(client, "demote-v@example.com", "viewer", "奶奶")
    admin_id = client.get("/api/me").json()["id"]
    assert second.patch(f"/api/members/{admin_id}", json={"role": "uploader"}).status_code == 200
    denied = client.post("/api/families/elders-paste", json={"member_user_id": viewer.get("/api/me").json()["id"]})
    assert denied.status_code == 403
    assert denied.json()["code"] == "elders_paste_forbidden"
    assert denied.json()["detail"] == COPY["err_403"]


def test_paste_rejects_non_viewer_member(client: TestClient):
    _family(client, "rank@example.com", "妈妈", "档位家")
    _set_url(client, "https://rank.example")
    uploader = _person(client, "rank-up@example.com", "uploader", "爸爸")
    rejected = client.post("/api/families/elders-paste", json={"member_user_id": uploader.get("/api/me").json()["id"]})
    myself = client.post("/api/families/elders-paste", json={"member_user_id": client.get("/api/me").json()["id"]})
    assert rejected.status_code == myself.status_code == 400
    assert rejected.json()["code"] == myself.json()["code"] == "elders_not_viewer"
    assert rejected.json()["detail"] == NOT_VIEWER
    assert uploader.get("/api/me").json()["email"] not in rejected.text


def test_foreign_member_invite_same_404(client: TestClient):
    _family(client, "home-a@example.com", "妈妈", "甲家哨兵")
    _set_url(client, "https://jia.example")
    other = TestClient(app)
    _family(other, "home-b@example.com", "别人", "乙家哨兵")
    _set_url(other, "https://yi.example")
    foreign_viewer = _person(other, "foreign-v@example.com", "viewer", "外公")
    foreign_invite = other.post("/api/families/invites", json={"email": "foreign-i@example.com", "role": "viewer"})
    missing_member = client.post("/api/families/elders-paste", json={"member_user_id": 987654})
    foreign_member = client.post(
        "/api/families/elders-paste",
        json={"member_user_id": foreign_viewer.get("/api/me").json()["id"]},
    )
    missing_invite = client.post("/api/families/elders-paste", json={"invite_id": 987654})
    foreign_row = client.post("/api/families/elders-paste", json={"invite_id": foreign_invite.json()["id"]})
    assert missing_member.status_code == foreign_member.status_code == 404
    assert missing_invite.status_code == foreign_row.status_code == 404
    expected = {"detail": TARGET_NOT_FOUND, "code": "elders_target_not_found"}
    assert missing_member.json() == foreign_member.json() == missing_invite.json() == foreign_row.json() == expected
    for response in (missing_member, foreign_member, missing_invite, foreign_row):
        assert "甲家哨兵" not in response.text
        assert "乙家哨兵" not in response.text
        assert "外公" not in response.text


def test_expired_invite_paste_400(client: TestClient, db):
    _family(client, "late-paste@example.com", "妈妈", "过期家")
    _set_url(client, "https://late.example")
    created = client.post("/api/families/invites", json={"email": "late-elder@example.com", "role": "viewer"})
    invite_id = created.json()["id"]
    token = created.json()["token"]
    invite = db.scalar(select(Invite).where(Invite.token == token))
    invite.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    expired = client.post("/api/families/elders-paste", json={"invite_id": invite_id})
    assert expired.status_code == 400
    assert expired.json()["code"] == "invite_expired"
    assert expired.json()["detail"] == COPY["inv_expired_sub"]
    fresh = client.post("/api/families/invites", json={"email": "fresh-elder@example.com", "role": "viewer"})
    member = TestClient(app)
    auth(member, "fresh-elder@example.com", "奶奶")
    assert member.post(f"/api/invites/{fresh.json()['token']}/accept").status_code == 200
    renamed = member.patch("/api/me", json={"relation_label": "奶奶"})
    assert renamed.status_code == 200, renamed.text
    accepted = client.post("/api/families/elders-paste", json={"invite_id": fresh.json()["id"]})
    assert accepted.status_code == 400
    assert accepted.json()["code"] == "invite_expired"
    living = client.post("/api/families/elders-paste", json={"member_user_id": member.get("/api/me").json()["id"]})
    assert living.status_code == 200
    assert living.json()["has_invite_line"] is False
    assert "入口：https://late.example" in living.json()["paste_body"]


def test_paste_target_and_body_errors(client: TestClient):
    _family(client, "body-url@example.com", "妈妈", "校验家")
    _set_url(client, "https://body.example")
    both = client.post("/api/families/elders-paste", json={"member_user_id": 1, "invite_id": 2})
    neither = client.post("/api/families/elders-paste", json={})
    text_id = client.post("/api/families/elders-paste", json={"member_user_id": "1"})
    flag = client.post("/api/families/elders-paste", json={"member_user_id": True})
    assert both.status_code == neither.status_code == text_id.status_code == flag.status_code == 400
    assert both.json()["code"] == "elders_target_invalid"
    empty = client.post("/api/families/elders-paste", content=b"", headers={"content-type": "application/json"})
    broken = client.post("/api/families/elders-paste", content=b"{", headers={"content-type": "application/json"})
    array = client.post("/api/families/elders-paste", content=b"[]", headers={"content-type": "application/json"})
    extra = client.post("/api/families/elders-paste", json={"member_user_id": 1, "note": "x"})
    for response in (empty, broken, array, extra):
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "elders_body_invalid"
        assert response.json()["detail"] == BODY_INVALID
    uploader = _person(client, "body-up@example.com", "uploader", "爸爸")
    forbidden = uploader.post("/api/families/elders-paste", content=b"[]")
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "elders_paste_forbidden"
    missing_key = client.patch("/api/families/current", json={})
    assert missing_key.status_code == 422
    assert missing_key.json()["code"] == "elders_body_invalid"


def test_elders_cross_origin_write_rejected(client: TestClient):
    _family(client, "csrf-url@example.com", "妈妈", "跨站家")
    _set_url(client, "https://keep.example")
    headers = {"Origin": "https://evil.example"}
    pasted = client.post("/api/families/elders-paste", headers=headers, json={"member_user_id": 1})
    edited = client.patch("/api/families/current", headers=headers, json={"family_access_url": "https://evil.example"})
    assert pasted.status_code == edited.status_code == 403
    assert pasted.json() == edited.json() == {"detail": "没有访问权限"}
    assert client.get("/api/families/current").json()["family_access_url"] == "https://keep.example"


def test_invite_page_copy_no_banned(client: TestClient):
    _family(client, "door@example.com", "妈妈", "门口家哨兵")
    created = client.post("/api/families/invites", json={"email": "door-v@example.com", "role": "viewer"})
    token = created.json()["token"]
    anonymous = TestClient(app)
    page = anonymous.get(f"/invites/{token}")
    assert page.status_code == 200
    assert "门口家哨兵" in page.text
    assert COPY["inv_title"] in page.text
    assert COPY["inv_role"] in page.text
    assert COPY["inv_accept"] in page.text
    assert COPY["inv_login"] in page.text
    assert COPY["inv_note"] in page.text
    _assert_clean(page.text, "invite")
    guest = TestClient(app)
    auth(guest, "door-v@example.com", "奶奶")
    ready = guest.get(f"/invites/{token}")
    assert COPY["inv_role"] in ready.text
    assert f'action="/invites/{token}/accept"' in ready.text
    accepted = guest.post(f"/invites/{token}/accept", follow_redirects=False)
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/"
    home = guest.get("/")
    assert home.status_code == 200
    _assert_clean(home.text, "after accept")
    up_token = client.post("/api/families/invites", json={"email": "door-up@example.com", "role": "uploader"}).json()["token"]
    up_page = anonymous.get(f"/invites/{up_token}")
    assert COPY["inv_role"] not in up_page.text
    assert "可上传" in up_page.text


def test_bad_token_no_family_leak(client: TestClient):
    _family(client, "leak-door@example.com", "妈妈", "泄漏哨兵家")
    missing = client.get("/invites/not-a-real-token")
    assert missing.status_code == 400
    assert COPY["bad_title"] in missing.text
    assert COPY["bad_sub"] in missing.text
    assert "泄漏哨兵家" not in missing.text
    assert COPY["inv_title"] not in missing.text
    assert "/media/" not in missing.text


def test_expired_invite_page(client: TestClient, db):
    _family(client, "exp-door@example.com", "妈妈", "过期哨兵家")
    created = client.post("/api/families/invites", json={"email": "exp-guest@example.com", "role": "viewer"})
    token = created.json()["token"]
    invite = db.scalar(select(Invite).where(Invite.token == token))
    invite.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    page = client.get(f"/invites/{token}")
    assert page.status_code == 400
    assert COPY["inv_expired_title"] in page.text
    assert COPY["inv_expired_sub"] in page.text
    assert "过期哨兵家" not in page.text
    assert COPY["inv_accept"] not in page.text
    assert client.post(f"/api/invites/{token}/accept").status_code == 400


def test_mismatch_invite_hides_family_name(client: TestClient):
    _family(client, "mis-door@example.com", "妈妈", "错箱哨兵家")
    token = client.post("/api/families/invites", json={"email": "right-door@example.com", "role": "viewer"}).json()["token"]
    wrong = TestClient(app)
    auth(wrong, "wrong-door@example.com", "错人")
    page = wrong.get(f"/invites/{token}")
    assert page.status_code == 400
    assert "错箱哨兵家" not in page.text
    assert "请使用受邀邮箱登录后再接受" in page.text


def test_bookmark_tip_ua_branch(client: TestClient):
    _family(client, "tip-admin@example.com", "妈妈", "便签家")
    client.post("/api/children", json={"name": "果果", "birth_date": "2024-07-12"})
    viewer = _person(client, "tip-v@example.com", "viewer", "奶奶")
    uploader = _person(client, "tip-up@example.com", "uploader", "爸爸")
    page = viewer.get("/")
    assert page.status_code == 200
    assert 'id="elders-bookmark"' in page.text
    assert COPY["tip_wechat"] in page.text
    assert COPY["tip_safari"] in page.text
    assert COPY["tip_ok"] in page.text
    assert 'hidden' in page.text
    me = viewer.get("/api/me").json()
    family_id = client.get("/api/families/current").json()["id"]
    assert f'data-family-id="{family_id}"' in page.text
    assert f'data-user-id="{me["id"]}"' in page.text
    assert 'id="elders-bookmark"' not in client.get("/").text
    assert 'id="elders-bookmark"' not in uploader.get("/").text
    node = shutil.which("node")
    assert node
    script = (
        "const m = require(process.argv[1]);"
        "const rows = ["
        "['Mozilla/5.0 MicroMessenger/8.0', 'tip_wechat'],"
        "['Mozilla/5.0 Safari/605', 'tip_safari'],"
        "['Chrome/120', 'tip_safari'],"
        "['', 'tip_safari']"
        "];"
        "if (m.bookmarkStorageKey(7, 9) !== 'elders.bookmark.dismissed.7.9') process.exit(2);"
        "if (m.bookmarkVisible('1', 'viewer')) process.exit(3);"
        "if (!m.bookmarkVisible('', 'viewer')) process.exit(4);"
        "if (m.bookmarkVisible('', 'admin')) process.exit(5);"
        "for (const row of rows) { if (m.bookmarkTipKey(row[0]) !== row[1]) process.exit(6); }"
        "const late = m.pasteOutcome(200, {paste_body:'x', code:'ok'}, true, true);"
        "if (late.toast) process.exit(7);"
        "const net = m.pasteOutcome(0, null, false, false);"
        "if (net.toast || !net.retry) process.exit(8);"
        "const busy = m.pasteOutcome(503, {detail:'busy'}, false, false);"
        "if (busy.toast || !busy.retry) process.exit(9);"
        "const invalid = m.pasteOutcome(422, {code:'elders_body_invalid'}, false, false);"
        "if (invalid.toast || !invalid.retry || invalid.kind !== 'validation') process.exit(10);"
        "const rolling = m.pasteOutcome(404, {detail:'nope'}, false, false);"
        "if (rolling.toast || !rolling.retry || rolling.kind !== 'rolling') process.exit(11);"
        "const missing = m.pasteOutcome(404, {detail:'找不到', code:'elders_target_not_found'}, false, false);"
        "if (missing.toast || missing.retry || missing.kind !== 'missing') process.exit(12);"
        "const wrote = m.pasteOutcome(200, {paste_body:'hello'}, false, true);"
        "if (!wrote.toast) process.exit(13);"
        "const denied = m.pasteOutcome(200, {paste_body:'hello'}, false, false);"
        "if (denied.toast) process.exit(14);"
    )
    completed = subprocess.run([node, "-e", script, str(Path("app/static/elders.js").resolve())], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_paste_network_error_no_toast():
    outcome = _outcome(0, None, False, False)
    assert outcome["toast"] is False
    assert outcome["retry"] is True
    assert outcome["kind"] == "network"


def test_paste_503_no_toast():
    outcome = _outcome(503, {"detail": "busy"}, False, False)
    assert outcome["toast"] is False
    assert outcome["retry"] is True


def test_late_paste_ignored_after_dismiss():
    outcome = _outcome(200, {"paste_body": "亲爱的家人："}, True, True)
    assert outcome["toast"] is False
    assert outcome["ignored"] is True


def test_js_422_mapped_paste():
    outcome = _outcome(422, {"code": "elders_body_invalid", "detail": BODY_INVALID}, False, False)
    assert outcome["toast"] is False
    assert outcome["kind"] == "validation"
    assert outcome["retry"] is True


def test_js_404_without_code_retryable_paste():
    rolling = _outcome(404, {"detail": "missing"}, False, False)
    coded = _outcome(404, {"detail": TARGET_NOT_FOUND, "code": "elders_target_not_found"}, False, False)
    assert rolling["toast"] is False and rolling["retry"] is True and rolling["kind"] == "rolling"
    assert coded["toast"] is False and coded["retry"] is False and coded["kind"] == "missing"


def _outcome(status, data, dismissed, wrote) -> dict:
    node = shutil.which("node")
    assert node
    script = (
        "const m = require(process.argv[1]);"
        "const payload = JSON.parse(process.argv[2]);"
        "process.stdout.write(JSON.stringify(m.pasteOutcome(payload.status, payload.data, payload.dismissed, payload.wrote)));"
    )
    raw = json.dumps({"status": status, "data": data, "dismissed": dismissed, "wrote": wrote})
    completed = subprocess.run(
        [node, "-e", script, str(Path("app/static/elders.js").resolve()), raw],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)
