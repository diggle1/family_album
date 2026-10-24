"""Export experience fixes for v2026.10.24.2. Permission tests stay in test_export.py."""

from __future__ import annotations

import base64
import io
import json
import re
import threading
import time
import zipfile
from collections import deque
from pathlib import Path
from urllib.parse import unquote

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app import constants
from app.config import get_settings
from app.db import engine
from app.main import app
from app.services import export as export_service
from app.storage.local import LocalStore
from tests.test_acceptance import JPEG, auth, upload
from tests.test_export import BANNED, _family, _invite, _names, _stored

_SCRIPT = Path("app/static/app.js")
_EXPORT_PY = Path("app/services/export.py")
_MAIN = Path("app/main.py")


@pytest.fixture(autouse=True)
def _clear_pack_slots():
    export_service._active.clear()
    export_service._slots.clear()
    export_service._waiters.clear()
    yield
    export_service._active.clear()
    export_service._slots.clear()
    export_service._waiters.clear()


def _payloads(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _decode_missing(header: str) -> dict:
    padded = header + "=" * (-len(header) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def _session_pair(client: TestClient) -> TestClient:
    other = TestClient(app)
    other.cookies.set("session", client.cookies.get("session"))
    return other


def _delete_photo_file(photo_id: int) -> None:
    from app.db import SessionLocal
    from app.models import Photo

    session = SessionLocal()
    try:
        row = session.get(Photo, photo_id)
        assert row is not None
        (get_settings().photo_dir / row.storage_path).unlink()
    finally:
        session.close()


class SlowStore:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.reads = 0
        self.inner = LocalStore(get_settings().photo_dir)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.inner.put(key, data, content_type)

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    def probe(self) -> None:
        self.inner.probe()

    def open(self, key: str):
        stream, media = self.inner.open(key)
        outer = self

        class Wrapped:
            def read(self, n: int = -1) -> bytes:
                chunk = stream.read(n)
                if chunk:
                    outer.reads += 1
                    time.sleep(outer.delay)
                return chunk

            def close(self) -> None:
                stream.close()

        return Wrapped(), media


class BoomStore:
    def __init__(self) -> None:
        self.inner = LocalStore(get_settings().photo_dir)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.inner.put(key, data, content_type)

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    def probe(self) -> None:
        self.inner.probe()

    def open(self, _key: str):
        class Stream:
            def read(self, _n: int = -1) -> bytes:
                raise OSError("disk")

            def close(self) -> None:
                return None

        return Stream(), "image/jpeg"


class EndlessStore:
    def __init__(self) -> None:
        self.reads = 0
        self.inner = LocalStore(get_settings().photo_dir)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.inner.put(key, data, content_type)

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    def probe(self) -> None:
        self.inner.probe()

    def open(self, _key: str):
        outer = self

        class Stream:
            def read(self, _n: int = -1) -> bytes:
                outer.reads += 1
                time.sleep(0.05)
                return b"x"

        return Stream(), "image/jpeg"


def test_success_sheet_is_local_template(client: TestClient):
    child_id = _family(client, "sheet@example.com", "小满", "收据家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    page = client.get("/family/export")
    assert '<template id="export-success-tpl">' in page.text
    assert "已下载" in page.text
    assert "重新下载" in page.text
    script = _SCRIPT.read_text(encoding="utf-8")
    assert "state=success" not in script
    for word in BANNED:
        assert word not in script
        assert word not in page.text


def test_zero_photo_page_has_no_templates(client: TestClient):
    _family(client, "zero-tpl@example.com", "空空", "空家")
    page = client.get("/family/export")
    assert page.status_code == 200
    assert "export-success-tpl" not in page.text
    assert "export-packing-tpl" not in page.text
    assert "export-failure-tpl" not in page.text
    assert "开始导出" not in page.text


def test_zip_response_is_no_transform(client: TestClient):
    child_id = _family(client, "cache@example.com", "缓", "缓家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 200
    assert "no-transform" in packed.headers["cache-control"]
    assert packed.headers["x-frame-options"] == "SAMEORIGIN"


def test_js_skips_length_check_when_content_encoding():
    script = _SCRIPT.read_text(encoding="utf-8")
    assert "function skipLengthCheck" in script
    assert "content-encoding" in script
    assert "identity" in script
    body = script.split("function skipLengthCheck", 1)[1].split("function isBig", 1)[0]
    assert "identity" in body


def test_download_404_is_no_store(client: TestClient):
    child_a = _family(client, "dl404@example.com", "甲", "甲家")
    assert upload(client, kind="child", child_id=child_a, taken_on="2026-10-22").status_code == 201
    other = TestClient(app)
    child_b = _family(other, "dl404b@example.com", "乙", "乙家")
    foreign = upload(other, kind="child", child_id=child_b, taken_on="2026-10-22")
    missing = client.get("/media/1000000000/download")
    cross = client.get(f"/media/{foreign.json()['id']}/download")
    assert missing.status_code == cross.status_code == 404
    assert missing.content == cross.content
    assert "no-store" in missing.headers["cache-control"]
    assert "no-store" in cross.headers["cache-control"]


def test_header_wait_is_rendered_from_env_timeout(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    child_id = _family(client, "wait@example.com", "等", "等家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    monkeypatch.setenv("EXPORT_BUILD_TIMEOUT_S", "7")
    tuned = client.get("/family/export")
    assert 'data-header-wait-ms="17000"' in tuned.text
    monkeypatch.delenv("EXPORT_BUILD_TIMEOUT_S", raising=False)
    plain = client.get("/family/export")
    assert 'data-header-wait-ms="130000"' in plain.text


def test_header_margin_covers_lock_wait_and_stop_grace():
    assert constants.EXPORT_HEADER_MARGIN_S > (
        constants.EXPORT_LOCK_WAIT_S + export_service._STOP_GRACE_S + 1
    )


def test_blob_max_rendered_and_env_override(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    child_id = _family(client, "blob@example.com", "包", "包家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    page = client.get("/family/export")
    assert 'data-blob-max-bytes="209715200"' in page.text
    monkeypatch.setenv("EXPORT_BLOB_MAX_MB", "1")
    tuned = client.get("/family/export")
    assert 'data-blob-max-bytes="1048576"' in tuned.text
    script = _SCRIPT.read_text(encoding="utf-8")
    assert "209715200" in script
    assert "length > blobMaxBytes()" in script


def test_second_pack_same_user_gets_409(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(export_service, "READ_CHUNK", 8)
    child_id = _family(client, "busy@example.com", "忙", "忙家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    holder = _session_pair(client)
    with holder.stream("GET", "/api/export/my-photos.zip") as response:
        assert response.status_code == 200
        first = next(response.iter_bytes())
        assert first
        busy = client.get("/api/export/my-photos.zip?dl=" + "ab" * 16)
        assert busy.status_code == 409
        assert busy.json() == {"detail": "export_busy"}
        assert "no-store" in busy.headers["cache-control"]
        assert busy.headers["x-frame-options"] == "SAMEORIGIN"
        assert "set-cookie" not in busy.headers


def test_other_user_packs_in_parallel(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.storage.factory.store_for", lambda _backend: SlowStore(0.3))
    child_id = _family(client, "par-a@example.com", "甲", "甲家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    other = _invite(admin=client, email="par-b@example.com", role="uploader", name="乙")
    assert upload(other, kind="child", child_id=child_id, taken_on="2026-10-21").status_code == 201
    results: dict[str, object] = {}

    def run(name: str, actor: TestClient) -> None:
        results[name] = actor.get("/api/export/my-photos.zip")

    left = threading.Thread(target=run, args=("a", client))
    right = threading.Thread(target=run, args=("b", other))
    left.start()
    right.start()
    left.join(10)
    right.join(10)
    assert left.is_alive() is False and right.is_alive() is False
    assert results["a"].status_code == 200  # type: ignore[attr-defined]
    assert results["b"].status_code == 200  # type: ignore[attr-defined]


def test_pack_slot_released_after_503(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    monkeypatch.setattr("app.storage.factory.store_for", lambda _backend: BoomStore())
    child_id = _family(client, "slot503@example.com", "错", "错家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    failed = client.get("/api/export/my-photos.zip")
    assert failed.status_code == 503
    assert export_service._active == set()
    again = client.get("/api/export/my-photos.zip")
    assert again.status_code == 503


def test_pack_slot_released_when_client_disconnects_during_build(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    monkeypatch.setattr("app.storage.factory.store_for", lambda _backend: SlowStore(1.2))
    monkeypatch.setenv("EXPORT_BUILD_TIMEOUT_S", "8")
    child_id = _family(client, "disc@example.com", "断", "断家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    cookie = "session=" + client.cookies.get("session")
    pending: deque[dict] = deque([{"type": "http.request", "body": b"", "more_body": False}])
    started = time.monotonic()

    async def receive():
        while not pending:
            await anyio.sleep(0.01)
        return pending.popleft()

    async def later():
        await anyio.sleep(0.25)
        pending.append({"type": "http.disconnect"})

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/export/my-photos.zip",
        "raw_path": b"/api/export/my-photos.zip",
        "query_string": b"",
        "headers": [(b"cookie", cookie.encode())],
        "client": ("127.0.0.1", 9),
        "server": ("test", 80),
        "app": app,
    }

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(later)
            await app(scope, receive, send)

    anyio.run(run)
    elapsed = time.monotonic() - started
    statuses = [message["status"] for message in sent if message["type"] == "http.response.start"]
    assert statuses == [503]
    assert elapsed < 4
    assert export_service._active == set()
    assert list(tmp_path.iterdir()) == []
    thread = export_service.last_build_thread
    assert thread is not None and not thread.is_alive()


def test_disconnect_after_build_before_first_byte_releases(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    child_id = _family(client, "prebyte@example.com", "先", "先家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    cookie = "session=" + client.cookies.get("session")
    pending: deque[dict] = deque([{"type": "http.request", "body": b"", "more_body": False}])
    bodies: list[bytes] = []

    async def receive():
        while not pending:
            await anyio.sleep(0.01)
        return pending.popleft()

    async def send(message):
        if message["type"] == "http.response.start":
            pending.append({"type": "http.disconnect"})
            await anyio.sleep(0.05)
        if message["type"] == "http.response.body" and message.get("body"):
            bodies.append(message["body"])

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/export/my-photos.zip",
        "raw_path": b"/api/export/my-photos.zip",
        "query_string": b"",
        "headers": [(b"cookie", cookie.encode())],
        "client": ("127.0.0.1", 9),
        "server": ("test", 80),
        "app": app,
    }

    async def run():
        await app(scope, receive, send)

    anyio.run(run)
    assert bodies == []
    assert export_service._active == set()
    assert list(tmp_path.iterdir()) == []


def test_zero_photos_never_takes_pack_slot(client: TestClient):
    _family(client, "noslot@example.com", "无", "无家")
    missing = client.get("/api/export/my-photos.zip")
    assert missing.status_code == 404
    assert export_service._active == set()


def test_slow_pack_within_timeout_succeeds(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EXPORT_BUILD_TIMEOUT_S", "2")
    monkeypatch.setattr("app.storage.factory.store_for", lambda _backend: SlowStore(0.4))
    child_id = _family(client, "slowok@example.com", "慢成", "慢成家")
    first = upload(client, kind="child", child_id=child_id, taken_on="2026-10-20")
    second = upload(client, kind="self", taken_on="2026-10-21", send_child=False)
    third = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22", content=JPEG, filename="c.jpg")
    assert first.status_code == second.status_code == third.status_code == 201
    started = time.monotonic()
    packed = client.get("/api/export/my-photos.zip")
    elapsed = time.monotonic() - started
    assert packed.status_code == 200
    assert elapsed >= 1.2
    assert elapsed < 3
    assert _stored(packed.content, f"2026-10-22_{third.json()['id']}.jpg") == JPEG


def test_slow_pack_past_env_timeout_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    monkeypatch.setenv("EXPORT_BUILD_TIMEOUT_S", "2")
    monkeypatch.setattr("app.storage.factory.store_for", lambda _backend: SlowStore(1.0))
    child_id = _family(client, "slowfail@example.com", "慢败", "慢败家")
    for day in ("2026-10-20", "2026-10-21", "2026-10-22"):
        assert upload(client, kind="child", child_id=child_id, taken_on=day).status_code == 201
    started = time.monotonic()
    failed = client.get("/api/export/my-photos.zip")
    elapsed = time.monotonic() - started
    assert failed.status_code == 503
    assert failed.json() == {"detail": "export_failed"}
    assert 2 <= elapsed <= 2 + export_service._STOP_GRACE_S + 1
    thread = export_service.last_build_thread
    assert thread is not None and not thread.is_alive()
    assert list(tmp_path.iterdir()) == []
    assert export_service._active == set()


def test_worker_stops_by_flag_without_close(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    monkeypatch.setattr(constants, "EXPORT_BUILD_TIMEOUT_S", 0.4)
    store = EndlessStore()
    monkeypatch.setattr("app.storage.factory.store_for", lambda _backend: store)
    child_id = _family(client, "flag@example.com", "旗", "旗家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    failed = client.get("/api/export/my-photos.zip")
    assert failed.status_code == 503
    seen = store.reads
    time.sleep(0.5)
    assert store.reads == seen
    source = _EXPORT_PY.read_text(encoding="utf-8")
    assert "ctypes" not in source
    assert "PyThreadState_SetAsyncExc" not in source


def test_startup_sweeps_orphan_export_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    (tmp_path / "export-orphan.zip").write_bytes(b"PK")
    (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
    assert export_service.sweep_temp_dir() == 1
    assert not (tmp_path / "export-orphan.zip").exists()
    assert (tmp_path / "keep.txt").exists()
    assert "sweep_temp_dir()" in _MAIN.read_text(encoding="utf-8")


def test_count_uses_sql_count(client: TestClient):
    child_id = _family(client, "count@example.com", "数", "数家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    statements: list[str] = []

    def before(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        counted = client.get("/api/export/my-photos")
    finally:
        event.remove(engine, "before_cursor_execute", before)
    assert counted.json() == {"count": 1}
    blob = "\n".join(statements).lower()
    assert "count(" in blob
    assert "storage_path" not in blob


def test_cancel_stops_build_and_removes_temp(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    monkeypatch.setattr("app.storage.factory.store_for", lambda _backend: SlowStore(1.5))
    monkeypatch.setenv("EXPORT_BUILD_TIMEOUT_S", "8")
    child_id = _family(client, "cancel@example.com", "消", "消家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    box: dict[str, object] = {}

    def run() -> None:
        box["response"] = client.get("/api/export/my-photos.zip")

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not export_service._active:
        time.sleep(0.05)
    assert export_service._active
    caller = _session_pair(client)
    stopped = caller.post("/api/export/cancel")
    assert stopped.status_code == 200
    assert stopped.json() == {"stopped": True}
    assert "no-store" in stopped.headers["cache-control"]
    worker.join(8)
    assert box["response"].status_code == 503  # type: ignore[attr-defined]
    assert list(tmp_path.iterdir()) == []
    assert export_service._active == set()


def test_cancel_without_active_job_is_stopped_true(client: TestClient):
    _family(client, "idle-cancel@example.com", "闲", "闲家")
    stopped = client.post("/api/export/cancel")
    assert stopped.status_code == 200
    assert stopped.json() == {"stopped": True}
    assert "no-store" in stopped.headers["cache-control"]


def test_cancel_is_member_only_and_own_only(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.storage.factory.store_for", lambda _backend: SlowStore(1.5))
    monkeypatch.setenv("EXPORT_BUILD_TIMEOUT_S", "8")
    child_id = _family(client, "own@example.com", "主", "主家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    outsider = TestClient(app)
    auth(outsider, "out-cancel@example.com", "外人")
    assert outsider.post("/api/export/cancel").status_code == 403
    assert TestClient(app).post("/api/export/cancel").status_code == 403
    other = _invite(admin=client, email="other-cancel@example.com", role="uploader", name="旁")
    box: dict[str, object] = {}

    def run() -> None:
        box["response"] = client.get("/api/export/my-photos.zip")

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not export_service._active:
        time.sleep(0.05)
    assert export_service._active
    stopped = other.post("/api/export/cancel")
    assert stopped.json() == {"stopped": True}
    assert export_service._active
    owner = _session_pair(client)
    assert owner.post("/api/export/cancel").json() == {"stopped": True}
    worker.join(8)
    assert box["response"].status_code == 503  # type: ignore[attr-defined]


def test_cancel_while_waiting_for_slot(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    monkeypatch.setattr("app.storage.factory.store_for", lambda _backend: SlowStore(1.5))
    monkeypatch.setenv("EXPORT_BUILD_TIMEOUT_S", "8")
    child_id = _family(client, "wait-cancel@example.com", "等消", "等消家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    results: dict[str, object] = {}

    def run(name: str, actor: TestClient) -> None:
        results[name] = actor.get("/api/export/my-photos.zip")

    first = threading.Thread(target=run, args=("first", client))
    first.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not export_service._active:
        time.sleep(0.05)
    assert export_service._active
    waiter = _session_pair(client)
    second = threading.Thread(target=run, args=("second", waiter))
    second.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not export_service._waiters:
        time.sleep(0.05)
    assert export_service._waiters
    stopper = _session_pair(client)
    assert stopper.post("/api/export/cancel").json() == {"stopped": True}
    first.join(8)
    second.join(8)
    assert results["first"].status_code == 503  # type: ignore[attr-defined]
    assert results["second"].status_code == 503  # type: ignore[attr-defined]
    assert results["second"].json() == {"detail": "export_failed"}  # type: ignore[attr-defined]
    assert export_service._active == set()
    assert list(tmp_path.iterdir()) == []


def test_cancel_rejects_cross_origin_post(client: TestClient):
    _family(client, "origin@example.com", "源", "源家")
    denied = client.post("/api/export/cancel", headers={"origin": "https://evil.example"})
    assert denied.status_code == 403
    assert "no-store" in denied.headers["cache-control"]


def test_missing_list_txt_and_header_content(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    child_id = _family(client, "miss@example.com", "缺", "缺家")
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    kept = upload(client, kind="self", taken_on="2026-10-23", send_child=False)
    assert created.status_code == kept.status_code == 201
    _delete_photo_file(created.json()["id"])
    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 200
    decoded = _decode_missing(packed.headers["x-export-missing"])
    assert decoded == {"total": 1, "items": [{"date": "2026-10-22", "owner": "挂在 果果"}]}
    assert "photo_id" not in packed.headers["x-export-missing"]
    assert "@" not in packed.headers["x-export-missing"]
    text = _stored(packed.content, "缺失清单.txt")
    assert text.startswith("\ufeff".encode())
    assert text.decode("utf-8-sig") == "2026-10-22 · 挂在 果果\n"
    assert list(tmp_path.iterdir()) == []


def test_more_than_20_missing_caps_items_not_total(client: TestClient):
    child_id = _family(client, "cap@example.com", "帽", "帽家")
    kept = upload(client, kind="self", taken_on="2026-10-01", send_child=False)
    assert kept.status_code == 201
    missing_ids = []
    for index in range(25):
        day = f"2026-09-{index + 1:02d}"
        created = upload(client, kind="child", child_id=child_id, taken_on=day)
        assert created.status_code == 201
        missing_ids.append(created.json()["id"])
    for photo_id in missing_ids:
        _delete_photo_file(photo_id)
    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 200
    decoded = _decode_missing(packed.headers["x-export-missing"])
    assert decoded["total"] == 25
    assert len(decoded["items"]) == 20
    lines = _stored(packed.content, "缺失清单.txt").decode("utf-8-sig").strip().splitlines()
    assert len(lines) == 25
    assert all("photo" not in line for line in lines)


def test_all_missing_is_422_without_zip(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    child_id = _family(client, "allmiss@example.com", "全缺", "全缺家")
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert created.status_code == 201
    _delete_photo_file(created.json()["id"])
    failed = client.get("/api/export/my-photos.zip?dl=" + "cd" * 16)
    assert failed.status_code == 422
    body = failed.json()
    assert body["detail"] == "all_missing"
    assert body["total"] == 1
    assert body["items"] == [{"date": "2026-10-22", "owner": "挂在 果果"}]
    assert "no-store" in failed.headers["cache-control"]
    assert failed.headers["x-frame-options"] == "SAMEORIGIN"
    assert "set-cookie" not in failed.headers
    assert not failed.content.startswith(b"PK")
    assert list(tmp_path.iterdir()) == []


def test_non_missing_read_error_is_still_503(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    monkeypatch.setattr("app.storage.factory.store_for", lambda _backend: BoomStore())
    child_id = _family(client, "oserr@example.com", "盘", "盘家")
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert created.status_code == 201
    second = upload(client, kind="self", taken_on="2026-10-23", send_child=False)
    assert second.status_code == 201
    failed = client.get("/api/export/my-photos.zip")
    assert failed.status_code == 503
    assert failed.json() == {"detail": "export_failed"}
    assert not failed.content.startswith(b"PK")
    assert list(tmp_path.iterdir()) == []


def test_cancel_during_streaming_stops_body(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(export_service, "temp_dir", lambda: tmp_path)
    monkeypatch.setattr(export_service, "READ_CHUNK", 8)
    child_id = _family(client, "stream-stop@example.com", "流", "流家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    holder = _session_pair(client)
    box: dict[str, object] = {}
    started = threading.Event()
    release = threading.Event()

    def consume() -> None:
        with holder.stream("GET", "/api/export/my-photos.zip") as response:
            assert response.status_code == 200
            box["length"] = int(response.headers["content-length"])
            iterator = response.iter_bytes()
            first = next(iterator)
            box["first"] = first
            started.set()
            assert release.wait(5)
            rest = bytearray()
            for chunk in iterator:
                rest.extend(chunk)
                if len(first) + len(rest) >= box["length"]:  # type: ignore[operator]
                    break
                time.sleep(0.01)
            box["rest"] = bytes(rest)

    reader = threading.Thread(target=consume)
    reader.start()
    assert started.wait(5)
    release.set()
    stopped = client.post("/api/export/cancel")
    reader.join(8)
    assert stopped.json()["stopped"] is True
    received = len(box["first"]) + len(box["rest"])  # type: ignore[arg-type]
    assert received < box["length"]  # type: ignore[operator]
    assert list(tmp_path.iterdir()) == []
    again = client.get("/api/export/my-photos.zip")
    assert again.status_code == 200
    assert again.content.startswith(b"PK")


def test_dl_cookie_only_on_200_and_validated(client: TestClient):
    child_id = _family(client, "cookie@example.com", "饼", "饼家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    token = "ab" * 16
    packed = client.get("/api/export/my-photos.zip", params={"dl": token})
    assert packed.status_code == 200
    cookie = packed.headers["set-cookie"]
    assert f"fa_dl={token}" in cookie
    assert "HttpOnly" not in cookie
    assert "SameSite=Strict" in cookie
    assert "Path=/" in cookie
    assert "Max-Age=120" in cookie
    plain = client.get("/api/export/my-photos.zip")
    assert "set-cookie" not in plain.headers
    invalid = client.get("/api/export/my-photos.zip", params={"dl": "not-hex"})
    assert invalid.status_code == 200
    assert "set-cookie" not in invalid.headers
    empty = TestClient(app)
    _family(empty, "cookie0@example.com", "零", "零家")
    missing = empty.get("/api/export/my-photos.zip", params={"dl": token})
    assert missing.status_code == 404
    assert "set-cookie" not in missing.headers


def test_big_path_uses_iframe_not_navigation():
    script = _SCRIPT.read_text(encoding="utf-8")
    assert "iframe" in script
    assert "/api/export/my-photos.zip?dl=" in script
    assert "location.assign" not in script
    assert "fa_dl=" in script


def test_big_path_has_no_numbers_or_limit_words(client: TestClient):
    child_id = _family(client, "big@example.com", "大", "大家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    big = client.get("/fragments/export/sheet?state=big")
    assert big.status_code == 200
    assert "已开始下载" in big.text
    assert "已交给浏览器下载" in big.text
    visible = re.sub(r"<[^>]+>", "", big.text)
    assert not re.search(r"\d", visible)
    for word in ("最多", "上限", "配额"):
        assert word not in big.text


def test_uploader_zero_shows_go_upload(client: TestClient):
    _family(client, "goupload@example.com", "传", "传家")
    page = client.get("/family/export")
    assert "你还没传过照片，传几张再来导出" in page.text
    assert "去上传" in page.text
    assert 'href="/upload"' in page.text
    assert 'action="/api/photos"' in page.text
    assert 'id="choose"' in page.text
    assert "data-inplace" in page.text
    assert 'id="upload-file"' in page.text
    assert "location.reload" in _SCRIPT.read_text(encoding="utf-8")
    viewer = _invite(admin=client, email="ro-empty@example.com", role="viewer", name="外婆")
    empty = viewer.get("/family/export")
    assert "去上传" not in empty.text
    assert "upload-file" not in empty.text
    assert 'action="/api/photos"' not in empty.text
    denied = viewer.post(
        "/api/photos",
        data={"kind": "self", "taken_on": "2026-10-22"},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert denied.status_code == 403


def test_sheet_fragment_zero_is_empty_state_404(client: TestClient):
    _family(client, "frag0@example.com", "碎", "碎家")
    sheet = client.get("/fragments/export/sheet")
    assert sheet.status_code == 404
    assert "private, no-store" in sheet.headers["cache-control"]
    assert "你还没传过照片，传几张再来导出" in sheet.text
    assert "开始导出" not in sheet.text
    assert "没加载出来" not in sheet.text
    assert "error.html" not in sheet.text


def test_result_templates_are_preloaded(client: TestClient):
    child_id = _family(client, "preload@example.com", "预", "预家")
    assert upload(client, kind="child", child_id=child_id, taken_on="2026-10-22").status_code == 201
    page = client.get("/family/export")
    for name in (
        "export-confirm-tpl",
        "export-packing-tpl",
        "export-downloading-tpl",
        "export-success-tpl",
        "export-failure-tpl",
        "export-dlfail-tpl",
        "export-allmissing-tpl",
        "export-big-tpl",
    ):
        assert f'<template id="{name}">' in page.text
    assert 'data-copy-row-failed-pack-retry="没打包成 · 点这里重试"' in page.text
    assert 'data-copy-row-done="上次已下载 · 重新下载"' in page.text
    assert 'data-copy-toast-bg-done="已下载 · {where}"' in page.text
    script = _SCRIPT.read_text(encoding="utf-8")
    busy = script.split("function showBusy", 1)[1].split("function showAllMissing", 1)[0]
    assert "setRow" not in busy


def test_copy_matches_final_spec():
    assert len(constants.EXPORT) == 70
    assert constants.EXPORT["row_sub_failed_pack_retry"] == "没打包成 · 点这里重试"
    assert constants.EXPORT["row_sub_failed_pack"] == "没打包成 · 点这里看原因"
    assert constants.EXPORT["row_sub_failed"] == "没下载成 · 点这里重试"
    assert constants.EXPORT["row_sub_done"] == "上次已下载 · 重新下载"
    assert constants.EXPORT["toast_bg_done"] == "已下载 · {where}"
    assert constants.EXPORT["filename"] == "时光机-我的照片-{date}.zip"
    assert constants.EXPORT["empty_ro_p"].count("<br>") == 1
    script = _SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"[\u3400-\u9fff]", script) is None


def test_content_disposition_utf8_and_ascii_fallback(client: TestClient):
    child_id = _family(client, "name@example.com", "名", "名家")
    first = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    second = upload(client, kind="self", taken_on="2026-10-20", send_child=False)
    assert first.status_code == second.status_code == 201
    missing = client.get("/api/export/my-photos.zip")
    valid = client.get("/api/export/my-photos.zip", params={"d": "2024-05-06"})
    invalid = client.get("/api/export/my-photos.zip", params={"d": "2026-13-40"})
    injected = client.get("/api/export/my-photos.zip", params={"d": "2026-01-02\r\nSet-Cookie: a=b"})
    assert missing.status_code == valid.status_code == invalid.status_code == injected.status_code == 200
    same = _payloads(missing.content)
    assert same == _payloads(valid.content) == _payloads(invalid.content) == _payloads(injected.content)
    assert len(same) == 2
    for response in (missing, valid, invalid, injected):
        header = response.headers["content-disposition"]
        assert 'filename="kids-shiguangji-my-photos.zip"' in header
        assert "filename*=UTF-8''" in header
    decoded = unquote(valid.headers["content-disposition"])
    assert "时光机-我的照片-2024-05-06.zip" in decoded
    fallback = export_service.export_filename_day(None).isoformat()
    assert fallback in unquote(missing.headers["content-disposition"])
    assert fallback in unquote(invalid.headers["content-disposition"])
    assert fallback in unquote(injected.headers["content-disposition"])
    assert "2026-13-40" not in invalid.headers["content-disposition"]
    assert "Set-Cookie" not in injected.headers["content-disposition"]
    filtered = client.get("/api/export/my-photos.zip", params={"d": "2026-10-22"})
    assert _names(filtered.content) == _names(missing.content)


def test_viewer_tap_targets_and_toast_ms():
    css = Path("app/static/app.css").read_text(encoding="utf-8")
    assert "min-width: 44px" in css
    assert "min-height: 44px" in css
    assert "min-height: 52px" in css
    assert "font-size: 16px" in css
    script = _SCRIPT.read_text(encoding="utf-8")
    assert "showToast(original.getAttribute(\"data-save-original\"), 4000)" in script
    assert "showToast(fail, 4000)" in script
    assert "data-save-original" in script
