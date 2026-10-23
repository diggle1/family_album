"""AC8–AC10: migrate, local/S3 reads, media 403, healthz vs storage failure."""

from __future__ import annotations

import logging
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.db import SessionLocal, engine
from app.main import app
from app.migrate_schema import migrate_schema
from app.models import Base, Membership, Photo
from app.storage.errors import StorageError
from app.storage.local import LocalStore
from app.storage.s3 import S3Store
from scripts.seed_roles import seed
from tests.test_acceptance import JPEG, auth, upload

SECRET = "zoom-secret-SHOULD-NOT-LEAK"


def _photos() -> list[Photo]:
    session = SessionLocal()
    try:
        return list(session.scalars(select(Photo).order_by(Photo.id.asc())).all())
    finally:
        session.close()


def _family_with_child(client: TestClient, email: str, name: str, family: str) -> int:
    auth(client, email, name)
    assert client.post("/api/families", json={"name": family}).status_code == 201
    child = client.post("/api/children", json={"name": "果果", "birth_date": "2024-07-12"})
    assert child.status_code == 201
    return child.json()["id"]


def test_photo_dir_overrides_media_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    photo = tmp_path / "photos"
    media = tmp_path / "media"
    monkeypatch.setenv("PHOTO_DIR", str(photo))
    monkeypatch.setenv("MEDIA_ROOT", str(media))
    get_settings.cache_clear()
    root = get_settings().media_root
    assert root == photo
    store = LocalStore(root)
    store.probe()
    store.put("abc.jpg", JPEG, "image/jpeg")
    stream, media_type = store.open("abc.jpg")
    try:
        assert media_type == "image/jpeg"
        assert stream.read() == JPEG
    finally:
        stream.close()
    assert (photo / "abc.jpg").is_file()
    assert not media.exists()


def test_migrate_legacy_sqlite_defaults_local(tmp_path: Path):
    legacy = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with legacy.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE photos (
                    id INTEGER PRIMARY KEY,
                    family_id INTEGER NOT NULL,
                    uploader_id INTEGER NOT NULL,
                    kind VARCHAR(16) NOT NULL,
                    child_id INTEGER,
                    taken_on DATE NOT NULL,
                    storage_path VARCHAR(80) NOT NULL,
                    created_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO photos (family_id, uploader_id, kind, child_id, taken_on, storage_path)
                VALUES (1, 1, 'child', 1, '2026-10-22', 'legacy.jpg')
                """
            )
        )
    assert migrate_schema(legacy) is True
    assert migrate_schema(legacy) is False
    with legacy.connect() as conn:
        backend = conn.execute(text("SELECT storage_backend FROM photos")).scalar()
    assert backend == "local"


def test_create_all_does_not_add_column_but_migrate_keeps_media(client: TestClient):
    child_id = _family_with_child(client, "mig@example.com", "迁移", "迁移家")
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert created.status_code == 201
    photo_id = created.json()["id"]
    body = client.get(f"/media/{photo_id}").content

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE photos DROP COLUMN storage_backend"))
    names = {col["name"] for col in inspect(engine).get_columns("photos")}
    assert "storage_backend" not in names
    Base.metadata.create_all(engine)
    names = {col["name"] for col in inspect(engine).get_columns("photos")}
    assert "storage_backend" not in names

    assert migrate_schema() is True
    assert migrate_schema() is False
    row = _photos()[0]
    assert row.storage_backend == "local"
    media = client.get(f"/media/{photo_id}")
    assert media.status_code == 200
    assert media.content == body


def test_switch_to_s3_keeps_old_local_media(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    child_id = _family_with_child(client, "swap@example.com", "切换", "切换家")
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    photo_id = created.json()["id"]
    old = _photos()[0]
    assert old.storage_backend == "local"
    local_bytes = (get_settings().photo_dir / old.storage_path).read_bytes()
    assert created.json()["media_url"] == f"/media/{photo_id}"
    assert "http" not in created.json()["media_url"]

    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "family-album")
    monkeypatch.setenv("S3_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_SECRET_KEY", "testing")
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.delenv("S3_ENDPOINT", raising=False)
    get_settings.cache_clear()

    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="family-album")
        old_media = client.get(f"/media/{photo_id}")
        assert old_media.status_code == 200
        assert old_media.content == local_bytes
        assert "private" in old_media.headers["cache-control"]
        fresh = upload(client, kind="child", child_id=child_id, taken_on="2026-10-23")
        assert fresh.status_code == 201, fresh.text
        new = _photos()[-1]
        assert new.storage_backend == "s3"
        assert new.storage_path != old.storage_path
        s3 = boto3.client("s3", region_name="us-east-1")
        stored = s3.get_object(Bucket="family-album", Key=new.storage_path)["Body"].read()
        assert stored == JPEG
        listed = s3.list_objects_v2(Bucket="family-album").get("Contents", [])
        keys = {item["Key"] for item in listed}
        assert new.storage_path in keys
        assert old.storage_path not in keys
        assert client.get(f"/media/{fresh.json()['id']}").status_code == 200
        anonymous = TestClient(app)
        assert anonymous.get(f"/media/{fresh.json()['id']}").status_code == 403
        assert "generate_presigned" not in fresh.text


def test_ac10_two_families_media_403_keeps_one_membership(client: TestClient):
    assert Membership.__table__.c.user_id.unique is True
    child_a = _family_with_child(client, "a@example.com", "甲家", "甲家庭")
    photo = upload(client, kind="child", child_id=child_a, taken_on="2026-10-22")
    photo_id = photo.json()["id"]
    assert client.get(f"/media/{photo_id}").status_code == 200

    other = TestClient(app)
    child_b = _family_with_child(other, "b@example.com", "乙家", "乙家庭")
    assert child_b != child_a
    denied = other.get(f"/media/{photo_id}")
    assert denied.status_code == 403
    assert denied.json() == {"detail": "没有访问权限"}
    assert not denied.content.startswith(b"\xff\xd8\xff")
    own = upload(other, kind="child", child_id=child_b, taken_on="2026-10-22")
    assert client.get(f"/media/{own.json()['id']}").status_code == 403
    assert other.get(f"/media/{own.json()['id']}").status_code == 200

    anonymous = TestClient(app)
    assert anonymous.get(f"/media/{photo_id}").status_code == 403

    session = SessionLocal()
    try:
        rows = list(session.scalars(select(Membership)).all())
        assert len(rows) == 2
        assert {row.family_id for row in rows} == {rows[0].family_id, rows[1].family_id}
        assert len({row.family_id for row in rows}) == 2
        assert len({row.user_id for row in rows}) == 2
    finally:
        session.close()


def test_bad_volume_is_healthy_and_shows_failure_page(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    monkeypatch.setenv("PHOTO_DIR", str(blocker / "media"))
    monkeypatch.setenv("S3_SECRET_KEY", SECRET)
    get_settings.cache_clear()
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"ok": True}
        page = client.get("/", follow_redirects=True)
        assert page.status_code == 503
        assert "VOL_MISSING" in page.text
        assert "媒资卷没挂上" in page.text
        assert "我改好了，刷新重试" in page.text
        assert "打开部署说明 · 排查" in page.text
        assert "compose up -d" in page.text
        assert SECRET not in page.text
        assert "••••" in page.text
        api = client.get("/api/me")
        assert api.status_code == 503
        assert api.json() == {"code": "VOL_MISSING", "message": "媒资卷没挂上"}
        accepted = client.get("/", headers={"accept": "application/json"})
        assert accepted.status_code == 503
        assert accepted.json()["code"] == "VOL_MISSING"
        spoofed = client.get("/errors/storage?code=APP_BOOT", follow_redirects=True)
        assert "媒资卷没挂上" in spoofed.text
        assert "服务还没起来" not in spoofed.text
        setup = client.get("/setup")
        assert setup.status_code == 200
        assert "SECRET_KEY" in setup.text
        fixed = tmp_path / "media"
        monkeypatch.setenv("PHOTO_DIR", str(fixed))
        get_settings.cache_clear()
        recovered = client.get("/?recheck=1")
        assert recovered.status_code == 200
        assert "发送验证码" in recovered.text
        assert "媒资卷没挂上" not in recovered.text


def test_s3_unreachable_stays_healthy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("S3_BUCKET", "family-album")
    monkeypatch.setenv("S3_ACCESS_KEY", "testing")
    monkeypatch.setenv("S3_SECRET_KEY", SECRET)
    monkeypatch.setenv("S3_REGION", "us-east-1")
    get_settings.cache_clear()
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        page = client.get("/", follow_redirects=True)
        assert page.status_code == 503
        assert "S3_UNREACHABLE" in page.text
        assert "对象存储连不上" in page.text
        assert SECRET not in page.text


def test_s3_missing_key_is_forbidden(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "family-album")
    monkeypatch.setenv("S3_ACCESS_KEY", "")
    monkeypatch.setenv("S3_SECRET_KEY", "")
    monkeypatch.delenv("S3_ENDPOINT", raising=False)
    get_settings.cache_clear()
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        page = client.get("/", follow_redirects=True)
        assert page.status_code == 503
        assert "S3_FORBIDDEN" in page.text
        assert "对象存储权限不对" in page.text


def test_healthz_fails_when_database_ping_fails(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    def boom(*_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("down"))

    monkeypatch.setattr(engine, "connect", boom)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"ok": False}


def test_db_down_page(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    def fail(*_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("down"))

    monkeypatch.setattr("app.gate.migrate_schema", fail)
    client.app.state.db_error = "DB_DOWN"
    page = client.get("/", follow_redirects=True)
    assert page.status_code == 503
    assert "DB_DOWN" in page.text
    assert "应用数据起不来" in page.text
    client.app.state.db_error = None


def test_healthy_error_url_does_not_spoof(client: TestClient):
    response = client.get("/errors/storage?code=S3_FORBIDDEN", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_setup_checklist_and_success_gate(client: TestClient):
    setup = client.get("/setup")
    assert setup.status_code == 200
    assert "自部署检查清单" in setup.text
    assert "token_urlsafe(32)" in setup.text
    assert "AUTH_TEST_CODE=000000" in setup.text
    assert "ALLOW_INSECURE_DEFAULTS" in setup.text
    assert "docker compose up -d" in setup.text
    assert "打开站点检查" in setup.text
    assert "不要在网页里配" in setup.text
    assert "<input" not in setup.text

    missing = client.get("/setup/ok", follow_redirects=False)
    assert missing.status_code == 303
    assert missing.headers["location"].startswith("/?next=")

    child_id = _family_with_child(client, "admin-setup@example.com", "管理员", "确认家")
    not_yet = client.get("/setup/ok", follow_redirects=False)
    assert not_yet.status_code == 303
    assert not_yet.headers["location"] == "/setup"

    self_photo = upload(client, kind="self", send_child=False, taken_on="2026-10-23")
    assert self_photo.status_code == 201
    assert client.cookies.get("setup_gate") != "uploaded"

    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert created.status_code == 201
    assert client.cookies.get("setup_gate") == "uploaded"
    media = client.get(f"/media/{created.json()['id']}")
    assert media.status_code == 200
    assert media.content.startswith(b"\xff\xd8\xff")
    assert "private" in media.headers["cache-control"]
    assert "public" not in media.headers["cache-control"]
    assert client.cookies.get("setup_gate") == "ready"

    ok = client.get("/setup/ok")
    assert ok.status_code == 200
    assert "装好了，可以给家人用了" in ok.text
    assert "图片经登录鉴权查看，没有公开直链" in ok.text
    assert "family_id" in ok.text
    assert "进入时光" in ok.text

    guest = TestClient(app)
    token = client.post(
        "/api/families/invites",
        json={"email": "up-setup@example.com", "role": "uploader"},
    ).json()["token"]
    auth(guest, "up-setup@example.com", "可上传")
    assert guest.post(f"/api/invites/{token}/accept").status_code == 200
    guest_photo = upload(guest, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert guest_photo.status_code == 201
    assert guest.cookies.get("setup_gate") != "ready"
    assert guest.get(f"/media/{guest_photo.json()['id']}").status_code == 200
    forbidden = guest.get("/setup/ok")
    assert forbidden.status_code == 403
    assert "装好了，可以给家人用了" not in forbidden.text


def test_admin_settings_line_is_hidden_from_other_roles(client: TestClient):
    child_id = _family_with_child(client, "admin-line@example.com", "管理员", "灰字家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    admin_page = client.get("/family/settings")
    assert admin_page.status_code == 200
    assert "存储后端在服务器" in admin_page.text
    assert "部署说明" in admin_page.text
    assert "测通" not in admin_page.text
    assert 'name="s3' not in admin_page.text.lower()

    def member(email: str, role: str) -> TestClient:
        token = client.post("/api/families/invites", json={"email": email, "role": role}).json()["token"]
        person = TestClient(app)
        auth(person, email, email.split("@")[0])
        assert person.post(f"/api/invites/{token}/accept").status_code == 200
        return person

    for email, role in (("up-line@example.com", "uploader"), ("view-line@example.com", "viewer")):
        page = member(email, role).get("/family/settings")
        assert page.status_code == 200
        assert "存储后端在服务器" not in page.text
        assert "测通" not in page.text


def test_seed_roles_is_idempotent_and_outsider_is_blocked(client: TestClient):
    seed()
    seed()
    session = SessionLocal()
    try:
        memberships = list(session.scalars(select(Membership)).all())
        assert len(memberships) == 3
        assert {row.role for row in memberships} == {"admin", "uploader", "viewer"}
        assert len({row.user_id for row in memberships}) == 3
    finally:
        session.close()

    auth(client, "admin@example.test")
    assert client.get("/api/families/current").json()["name"] == "示例家庭"
    child_id = client.get("/api/children").json()["children"][0]["id"]
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert created.status_code == 201
    outsider = TestClient(app)
    auth(outsider, "outsider@example.test")
    # No membership yet: current-family is the empty state (404), not a leak of 示例家庭.
    assert outsider.get("/api/families/current").status_code == 404
    assert outsider.get("/family/members").status_code == 403
    hidden = outsider.get(f"/media/{created.json()['id']}")
    assert hidden.status_code == 403
    assert hidden.json() == {"detail": "没有访问权限"}
    assert "示例家庭" not in hidden.text


def test_s3_error_mapping_and_probe_delete_log(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "family-album")
    monkeypatch.setenv("S3_ACCESS_KEY", "AKIA_TEST")
    monkeypatch.setenv("S3_SECRET_KEY", SECRET)
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.delenv("S3_ENDPOINT", raising=False)
    get_settings.cache_clear()
    logging.getLogger("botocore").setLevel(logging.CRITICAL)
    logging.getLogger("boto3").setLevel(logging.CRITICAL)
    logging.getLogger("urllib3").setLevel(logging.CRITICAL)

    class Denied:
        def put_object(self, **_kwargs):
            raise ClientError(
                {
                    "Error": {"Code": "AccessDenied", "Message": "no"},
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                },
                "PutObject",
            )

    class Down:
        def put_object(self, **_kwargs):
            raise ConnectionError("failed to connect")

    settings = get_settings()
    with pytest.raises(StorageError) as denied:
        S3Store(settings, client=Denied()).probe()
    assert denied.value.code == "S3_FORBIDDEN"
    with pytest.raises(StorageError) as down:
        S3Store(settings, client=Down()).probe()
    assert down.value.code == "S3_UNREACHABLE"

    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="family-album")
        store = S3Store(settings)
        real = store._client()

        class Wrap:
            def put_object(self, **kwargs):
                return real.put_object(**kwargs)

            def delete_object(self, **_kwargs):
                raise ClientError(
                    {"Error": {"Code": "InternalError", "Message": SECRET}},
                    "DeleteObject",
                )

        store._client_override = Wrap()
        with caplog.at_level(logging.WARNING):
            store.probe()
        assert SECRET not in caplog.text
        assert "_probe/" in caplog.text
        session = SessionLocal()
        try:
            paths = list(session.scalars(select(Photo.storage_path)).all())
        finally:
            session.close()
        assert all("_probe/" not in path for path in paths)

    with mock_aws():
        missing = S3Store(get_settings())
        with pytest.raises(StorageError) as missing_bucket:
            missing.probe()
        assert missing_bucket.value.code == "S3_FORBIDDEN"


def test_deploy_files_and_no_public_media_path():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "app-data:/data" in compose
    assert "/healthz" in compose
    assert "env_file" in compose
    assert "minio/minio" not in compose
    assert "ALLOW_INSECURE_DEFAULTS" not in compose
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "ALLOW_INSECURE_DEFAULTS" not in dockerfile
    assert "SECRET_KEY" not in dockerfile
    assert "scripts/migrate.py" in dockerfile
    example = Path(".env.example").read_text(encoding="utf-8")
    assert "token_urlsafe(32)" in example
    assert "AUTH_TEST_CODE" in example
    assert "000000" in example
    assert "ALLOW_INSECURE_DEFAULTS" in example
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "token_urlsafe(32)" in readme
    assert "AUTH_TEST_CODE=000000" in readme
    assert "docker compose up -d" in readme
    assert "S3_FORCE_PATH_STYLE" in readme
    assert "storage_backend" in readme

    banned_bits = ("generate_presigned_url", "public-read", "family_id = 1", "family_id==1")
    for path in Path("app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for bit in banned_bits:
            assert bit not in text, f"{bit} in {path}"
