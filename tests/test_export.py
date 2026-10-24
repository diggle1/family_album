"""AC-E1–E5 and the export-ui review checks. ZIP is the caller's own uploads."""

from __future__ import annotations

import io
import threading
import time
import zipfile
from pathlib import Path

import anyio
import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from app import constants
from app.config import get_settings
from app.main import app
from app.services import export as export_service
from app.routers.export import stream_zip
from tests.test_acceptance import JPEG, PNG, auth, upload

BANNED = (
    "会员",
    "付费",
    "开通",
    "升级",
    "限免",
    "次数",
    "积分",
    "商城",
    "发现",
    "广告",
    "最多",
    "配额",
    "上限",
    "剩余",
)

COPY_SURFACES = (
    Path("app/constants.py"),
    Path("app/templates/settings.html"),
    Path("app/templates/export.html"),
    Path("app/templates/fragments/export_sheet.html"),
    Path("app/templates/photo.html"),
    Path("app/static/app.js"),
)


def _assert_clean(text: str, where: str) -> None:
    for word in BANNED:
        assert word not in text, f"{word} in {where}"
    assert text.count("会员") == 0


def _family(client: TestClient, email: str, name: str, family: str) -> int:
    auth(client, email, name)
    assert client.post("/api/families", json={"name": family}).status_code == 201
    child = client.post("/api/children", json={"name": "果果", "birth_date": "2024-07-12"})
    assert child.status_code == 201
    return child.json()["id"]


def _invite(admin: TestClient, email: str, role: str, name: str) -> TestClient:
    token = admin.post("/api/families/invites", json={"email": email, "role": role}).json()["token"]
    member = TestClient(app)
    auth(member, email, name)
    assert member.post(f"/api/invites/{token}/accept").status_code == 200
    return member


def _names(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.namelist()


def _stored(content: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.read(name)


def test_t1_zip_contains_only_my_uploads(client: TestClient):
    child_id = _family(client, "me@example.com", "小美", "果果家")
    other = _invite(admin=client, email="other@example.com", role="uploader", name="大伟")
    first = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    second = upload(client, kind="self", taken_on="2026-10-20", send_child=False, content=PNG, filename="b.png", mime="image/png")
    foreign = upload(other, kind="child", child_id=child_id, taken_on="2026-10-21")
    assert first.status_code == second.status_code == foreign.status_code == 201
    me_id = client.get("/api/me").json()["id"]
    count = client.get("/api/export/my-photos")
    assert count.status_code == 200
    assert count.json() == {"count": 2}
    assert "private, no-store" in count.headers["cache-control"]

    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 200, packed.text
    assert packed.headers["content-type"].startswith("application/zip")
    assert "private, no-store" in packed.headers["cache-control"]
    assert 'filename="kids-shiguangji-my-photos.zip"' in packed.headers["content-disposition"]
    assert packed.content.startswith(b"PK")
    first_id = first.json()["id"]
    second_id = second.json()["id"]
    names = _names(packed.content)
    assert names == [f"2026-10-20_{second_id}.png", f"2026-10-22_{first_id}.jpg"]
    assert _stored(packed.content, names[0]) == PNG
    assert _stored(packed.content, names[1]) == JPEG
    assert all(not name.endswith(f"_{foreign.json()['id']}.jpg") for name in names)
    assert me_id


def test_zip_ignores_family_id_query(client: TestClient):
    child_a = _family(client, "a@example.com", "甲", "甲家")
    mine = upload(client, kind="child", child_id=child_a, taken_on="2026-10-22")
    assert mine.status_code == 201
    other = TestClient(app)
    child_b = _family(other, "b@example.com", "乙", "乙家")
    theirs = upload(other, kind="child", child_id=child_b, taken_on="2026-10-22")
    assert theirs.status_code == 201
    other_family = other.get("/api/families/current").json()["id"]
    plain = client.get("/api/export/my-photos.zip")
    spoofed = client.get(
        "/api/export/my-photos.zip",
        params={"family_id": other_family, "uploader_id": other.get("/api/me").json()["id"]},
    )
    assert plain.status_code == spoofed.status_code == 200
    own_name = f"2026-10-22_{mine.json()['id']}.jpg"
    other_name = f"2026-10-22_{theirs.json()['id']}.jpg"
    assert _names(plain.content) == _names(spoofed.content) == [own_name]
    assert other_name not in _names(spoofed.content)
    counted = client.get("/api/export/my-photos", params={"family_id": other_family})
    assert counted.json() == {"count": 1}


def test_t2_copy_has_free_line_and_no_banned_words(client: TestClient):
    for path in COPY_SURFACES:
        _assert_clean(path.read_text(encoding="utf-8"), str(path))
    child_id = _family(client, "copy@example.com", "小美", "果果家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    settings = client.get("/family/settings")
    assert settings.status_code == 200
    assert "我的数据" in settings.text
    assert "导出我的照片" in settings.text
    assert "只含你上传的照片" in settings.text
    assert "永久免费" in settings.text
    _assert_clean(settings.text, "settings")

    page = client.get("/family/export")
    assert "将打包你上传的 1 张照片" in page.text
    assert "不会包含其他家人上传的照片" in page.text
    assert "永久免费" in page.text
    assert "开始导出" in page.text
    _assert_clean(page.text, "confirm")

    for state, needle in (
        ("packing", "正在打包你的照片…"),
        ("success", "已下载"),
        ("failure", "没打包成"),
        ("confirm", "开始导出"),
    ):
        sheet = client.get(f"/fragments/export/sheet?state={state}")
        assert sheet.status_code == 200
        assert needle in sheet.text
        assert "永久免费" in sheet.text or state == "failure"
        _assert_clean(sheet.text, state)
    success = client.get("/fragments/export/sheet?state=success")
    assert "永久免费" in success.text
    assert "重新下载" in success.text
    failure = client.get("/fragments/export/sheet?state=failure")
    assert "网络不稳或服务忙，稍后再试。" in failure.text
    assert "重试" in failure.text
    assert "张" not in failure.text
    photo_id = client.get("/api/export/my-photos").json()["count"]
    assert photo_id == 1
    listed = client.get("/api/photos/self/" + str(client.get("/api/me").json()["id"]))
    # The child photo is not in the self album. Open it from the timeline id via media list.
    packed = client.get("/api/export/my-photos.zip")
    name = _names(packed.content)[0]
    photo = name.rsplit("_", 1)[1].split(".")[0]
    viewer = client.get(f"/photos/{photo}")
    assert "保存原图" in viewer.text
    assert "原图已下载" in viewer.text
    _assert_clean(viewer.text, "photo")


def test_failure_and_packing_sheets_are_local_templates(client: TestClient):
    child_id = _family(client, "local-sheet@example.com", "小满", "本地家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    page = client.get("/family/export")
    assert page.status_code == 200
    assert '<template id="export-failure-tpl">' in page.text
    assert '<template id="export-packing-tpl">' in page.text
    assert "没打包成" in page.text
    assert "网络不稳或服务忙，稍后再试。" in page.text
    assert "重试" in page.text
    assert "正在打包你的照片…" in page.text
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "export-failure-tpl" in script
    assert "HEADER_WAIT_MS = 130000" in script
    assert "READ_STALL_MS = 15000" in script
    assert "state=failure" not in script
    assert "state=packing" not in script
    _assert_clean(page.text, "local-templates")


def test_t3_non_member_is_403_before_empty_count(client: TestClient):
    child_id = _family(client, "owner@example.com", "家长", "家长家")
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    photo_id = created.json()["id"]
    outsider = TestClient(app)
    auth(outsider, "out@example.com", "外人")
    anonymous = TestClient(app)
    for actor in (outsider, anonymous):
        for url in (
            "/api/export/my-photos.zip",
            "/api/export/my-photos.zip?family_id=1",
            "/api/export/my-photos",
            f"/media/{photo_id}/download",
            "/media/1000000000/download",
        ):
            response = actor.get(url)
            assert response.status_code == 403, url
            assert response.json()["detail"] == "没有访问权限"
            assert response.json() != {"detail": "no_photos", "count": 0}
            assert not response.content.startswith(b"PK")
        assert actor.get("/family/export").status_code == 403
        assert actor.get("/fragments/export/sheet").status_code == 403


def test_member_with_zero_photos_gets_404_not_an_empty_zip(client: TestClient):
    _family(client, "empty@example.com", "空空", "空家")
    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 404
    assert packed.json() == {"detail": "no_photos", "count": 0}
    assert not packed.content.startswith(b"PK")
    assert "private, no-store" in packed.headers["cache-control"]
    assert client.get("/api/export/my-photos").json() == {"count": 0}


def test_t4_viewer_without_uploads_sees_empty_state_and_can_save_original(client: TestClient):
    child_id = _family(client, "admin@example.com", "家长", "果果家")
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    photo_id = created.json()["id"]
    viewer = _invite(admin=client, email="view@example.com", role="viewer", name="外婆")
    settings = viewer.get("/family/settings")
    assert "导出我的照片" in settings.text
    assert "永久免费" in settings.text
    _assert_clean(settings.text, "viewer settings")
    empty = viewer.get("/family/export")
    assert empty.status_code == 200
    assert "还没有你上传的照片" in empty.text
    assert "去时光线保存单张" in empty.text
    assert 'href="/"' in empty.text
    assert "开始导出" not in empty.text
    assert "/api/export/my-photos.zip" not in empty.text
    assert "0 张" not in empty.text
    _assert_clean(empty.text, "empty")
    saved = viewer.get(f"/media/{photo_id}/download")
    assert saved.status_code == 200
    assert saved.content == JPEG
    assert saved.headers["content-disposition"].startswith("attachment;")
    assert f'filename="photo-{photo_id}.jpg"' in saved.headers["content-disposition"]
    assert "private, no-store" in saved.headers["cache-control"]
    inline = viewer.get(f"/media/{photo_id}")
    assert inline.status_code == 200
    assert inline.headers["content-disposition"].startswith("inline;")
    page = viewer.get(f"/photos/{photo_id}")
    assert "保存原图" in page.text
    assert f'href="/media/{photo_id}/download"' in page.text


def test_t5_demoted_viewer_can_still_zip_history(client: TestClient):
    child_id = _family(client, "admin@example.com", "家长", "果果家")
    uploader = _invite(admin=client, email="up@example.com", role="uploader", name="小美")
    created = upload(uploader, kind="child", child_id=child_id, taken_on="2026-10-22")
    photo_id = created.json()["id"]
    user_id = uploader.get("/api/me").json()["id"]
    demoted = client.patch(f"/api/members/{user_id}", json={"role": "viewer"})
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "viewer"
    assert uploader.post(
        "/api/photos",
        data={"kind": "self", "taken_on": "2026-10-23"},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    ).status_code == 403
    packed = uploader.get("/api/export/my-photos.zip")
    assert packed.status_code == 200
    assert _names(packed.content) == [f"2026-10-22_{photo_id}.jpg"]
    assert _stored(packed.content, f"2026-10-22_{photo_id}.jpg") == JPEG
    assert "永久免费" in uploader.get("/family/settings").text


def test_t6_download_missing_and_other_family_are_the_same_404(client: TestClient):
    child_a = _family(client, "a@example.com", "甲", "甲家")
    own = upload(client, kind="child", child_id=child_a, taken_on="2026-10-22")
    other = TestClient(app)
    child_b = _family(other, "b@example.com", "乙", "乙家")
    foreign = upload(other, kind="child", child_id=child_b, taken_on="2026-10-22")
    foreign_id = foreign.json()["id"]
    missing = client.get("/media/1000000000/download")
    cross = client.get(f"/media/{foreign_id}/download")
    assert missing.status_code == cross.status_code == 404
    assert missing.content == cross.content
    assert missing.json() == cross.json() == {"detail": "没有这张照片"}
    assert not cross.content.startswith(b"\xff\xd8\xff")
    # Existing inline route stays 403 for another family's id. Not part of this change.
    assert client.get(f"/media/{foreign_id}").status_code == 403
    assert client.get(f"/media/{own.json()['id']}/download").status_code == 200
    outsider = TestClient(app)
    auth(outsider, "out@example.com", "外人")
    denied_missing = outsider.get("/media/1000000000/download")
    denied_cross = outsider.get(f"/media/{foreign_id}/download")
    assert denied_missing.status_code == denied_cross.status_code == 403
    assert denied_missing.content == denied_cross.content


def test_t7_zip_reads_storage_backend_after_switch_to_s3(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    child_id = _family(client, "swap@example.com", "切换", "切换家")
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    photo_id = created.json()["id"]
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "family-album")
    monkeypatch.setenv("S3_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_SECRET_KEY", "testing")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.delenv("S3_ENDPOINT", raising=False)
    get_settings.cache_clear()
    assert get_settings().storage_backend == "s3"
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="family-album")
        packed = client.get("/api/export/my-photos.zip")
        assert packed.status_code == 200, packed.text
        assert _names(packed.content) == [f"2026-10-22_{photo_id}.jpg"]
        assert _stored(packed.content, f"2026-10-22_{photo_id}.jpg") == JPEG


def test_one_missing_original_is_partial_zip_with_list(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    child_id = _family(client, "gone@example.com", "丢失", "丢失家")
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert created.status_code == 201
    second = upload(client, kind="self", taken_on="2026-10-23", send_child=False)
    assert second.status_code == 201
    from app.db import SessionLocal
    from app.models import Photo

    session = SessionLocal()
    try:
        row = session.get(Photo, created.json()["id"])
        assert row is not None
        (get_settings().photo_dir / row.storage_path).unlink()
    finally:
        session.close()
    import base64
    import json

    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 200
    assert packed.content.startswith(b"PK")
    names = _names(packed.content)
    assert "缺失清单.txt" in names
    assert f"2026-10-23_{second.json()['id']}.jpg" in names
    assert f"2026-10-22_{created.json()['id']}.jpg" not in names
    raw = packed.headers["x-export-missing"]
    decoded = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    assert decoded["total"] == 1
    assert decoded["items"] == [{"date": "2026-10-22", "owner": "挂在 果果"}]
    assert packed.headers["x-export-packed"] == "1"
    text = _stored(packed.content, "缺失清单.txt").decode("utf-8-sig")
    assert text == "2026-10-22 · 挂在 果果\n"
    assert list(tmp_path.iterdir()) == []


def test_timeout_stops_the_worker_and_deletes_the_temp_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    monkeypatch.setattr(constants, "EXPORT_BUILD_TIMEOUT_S", 0.3)
    child_id = _family(client, "slow@example.com", "慢", "慢家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201

    class Blocking:
        def __init__(self) -> None:
            self.closed = threading.Event()

        def read(self, _n: int = -1) -> bytes:
            self.closed.wait(15)
            raise OSError("closed")

        def close(self) -> None:
            self.closed.set()

    opened: list[Blocking] = []

    class Store:
        def open(self, _key: str):
            stream = Blocking()
            opened.append(stream)
            return stream, "image/jpeg"

    monkeypatch.setattr("app.storage.factory.store_for", lambda _backend: Store())
    started = time.monotonic()
    failed = client.get("/api/export/my-photos.zip")
    elapsed = time.monotonic() - started
    assert failed.status_code == 503
    assert failed.json() == {"detail": "export_failed"}
    assert not failed.content.startswith(b"PK")
    assert elapsed < 5
    assert opened and opened[0].closed.is_set()
    thread = export_service.last_build_thread
    assert thread is not None and not thread.is_alive()
    assert list(tmp_path.iterdir()) == []


def test_temp_file_removed_after_full_response(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    child_id = _family(client, "full@example.com", "完整", "完整家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 200
    assert packed.content.startswith(b"PK")
    assert list(tmp_path.iterdir()) == []


def test_temp_file_removed_when_client_cancels_mid_transfer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(export_service, "READ_CHUNK", 8)
    path = tmp_path / "export-partial.zip"
    path.write_bytes(b"PK" + b"x" * 200)
    full_size = path.stat().st_size
    response = stream_zip(path)
    sent: list[bytes] = []

    async def receive():
        await anyio.sleep(0.2)
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            sent.append(message["body"])
            await anyio.sleep(30)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/export/my-photos.zip",
        "raw_path": b"/api/export/my-photos.zip",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 9),
        "server": ("test", 80),
    }

    async def run():
        await response(scope, receive, send)

    anyio.run(run)
    assert sent
    assert sum(len(part) for part in sent) < full_size
    assert sum(len(part) for part in sent) < 208
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
