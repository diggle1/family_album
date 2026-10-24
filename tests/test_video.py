"""Growth video upload, feed, export, and failure paths."""

from __future__ import annotations

import io
import threading
import uuid
import zipfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import get_settings
from app.constants import FORBIDDEN, VIDEO
from app.db import SessionLocal
from app.main import app
from app.models import Child, Membership, Video
from app.services.clock import age_label, shanghai_today
from app.services.videos import sweep_video_partials
from app.storage.errors import StorageError
from app.storage.local import LocalStore
from tests.test_acceptance import JPEG, auth, upload

BANNED = ("会员", "开通", "更高清", "加时长", "朋友圈", "发动态", "广告", "商城", "发现")
NOT_FOUND = {"detail": VIDEO["not_found"], "code": "video_not_found"}
EXPECTED = {
    "tab_time": "时光",
    "tab_family": "家人",
    "tab_settings": "设置",
    "tl_tab_child": "孩子时光",
    "tl_tab_family": "家人相册",
    "age_fmt": "{y}岁{m}个月",
    "date_fmt": "{y}年{mo}月{d}日 {wd}",
    "date_short": "{mo}月{d}日",
    "entry_video": "挂视频",
    "u_title": "挂一段视频",
    "u_pick": "选一段视频",
    "u_picked": "已选 · {s}s / {mb} MB",
    "meter_cap": "60 秒 · 100 MB",
    "meter_unpicked": "未选视频",
    "u_submit": "挂到这一天",
    "u_sending": "传着…",
    "u_cancel_up": "取消上传",
    "u_keep_open": "请保持页面开着传完",
    "u_limit_hint": "一次最多 60 秒 / 100 MB",
    "child_pick": "挂给哪个孩子？",
    "child_switch": "换孩子",
    "child_pick_tap": "点这里选",
    "child_sheet_title": "挂给谁",
    "child_sheet_sub": "一段只挂一个孩子；两个孩子就各挂一段。",
    "date_sheet_title": "这是哪一天的事？",
    "date_sheet_ok": "就这天",
    "date_age_follow": "年龄会跟着日期变",
    "cancel": "取消",
    "card_by": "{who}传",
    "card_dur": "{m}:{ss}",
    "toast_posted": "挂上了 · {date}",
    "d_sign": "{who}传 · 写给{name} · {date}",
    "former": "前成员 · {who}",
    "m_delete": "删除",
    "del_title": "删掉这段视频？",
    "del_sub": "文件和记录都会清掉，不能恢复。",
    "del_ok": "删掉",
    "del_keep": "再想想",
    "toast_deleted": "删掉了",
    "cover_ph": "视频",
    "err_retry": "请保持页面开着传完；没传上，点这里重试",
    "err_limit_dur": "这段超过 60 秒了，一次最多 60 秒",
    "err_limit_size": "文件超过 100 MB 了，一次最多 100 MB",
    "err_format": "换个常见格式再传",
    "err_demoted": "你现在不能挂视频了",
    "e_removed": "你已不在这个家庭里，这段没挂上",
    "err_no_child": "先选挂给哪个孩子",
    "err_date_future": "不能选今天以后的日子",
    "err_date_birth": "那天{name}还没出生",
    "not_found": "这段视频找不到了",
    "back_tl": "回时光线",
    "empty_v_title": "还没有视频",
    "empty_v_btn": "挂第一段",
    "empty_tl_title": "还没有照片",
    "empty_tl_btn": "上传第一张",
    "ro_meta": "只读 · 只能看",
    "u_cancel_note": "取消后不会留下半截",
    "fab_photo": "上传照片",
    "fab_video": "挂视频",
    "fab_sheet": "挂什么",
    "tl_hang": "给{kid}挂一条视频",
    "del_ing": "正在删除…",
    "del_fail": "没删掉，再点一次试试",
    "skel_note": "加载中",
}


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


def _box(kind: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def _mvhd(units: int, timescale: int = 1000) -> bytes:
    body = b"\x00\x00\x00\x00" * 3 + timescale.to_bytes(4, "big") + units.to_bytes(4, "big")
    return _box(b"mvhd", body)


def _mp4(duration_ms: int = 12500, brand: bytes = b"isom", *, moov: bool = True) -> bytes:
    ftyp = _box(b"ftyp", brand + b"\x00\x00\x00\x00" + brand)
    mdat = _box(b"mdat", b"\x00" * 8)
    if not moov:
        return ftyp + mdat
    return ftyp + mdat + _box(b"moov", _mvhd(duration_ms))


def _family(client: TestClient, email: str, name: str, family: str, child: str = "果果") -> int:
    auth(client, email, name)
    assert client.post("/api/families", json={"name": family}).status_code == 201
    created = client.post("/api/children", json={"name": child, "birth_date": "2024-07-12"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _invite(admin: TestClient, email: str, role: str, name: str) -> TestClient:
    token = admin.post("/api/families/invites", json={"email": email, "role": role}).json()["token"]
    member = TestClient(app)
    auth(member, email, name)
    assert member.post(f"/api/invites/{token}/accept").status_code == 200
    return member


def _count() -> int:
    session = SessionLocal()
    try:
        return int(session.scalar(select(func.count()).select_from(Video)) or 0)
    finally:
        session.close()


def _rows() -> list[Video]:
    session = SessionLocal()
    try:
        return list(session.scalars(select(Video).order_by(Video.id.asc())).all())
    finally:
        session.close()


def _post(
    client: TestClient,
    child_id: int | None,
    content: bytes | None = None,
    *,
    filename: str = "clip.mp4",
    duration: str | None = "12500",
    on_date: str | None = None,
    key: str | None = None,
    poster: tuple | None = None,
    include_child: bool = True,
    include_date: bool = True,
    extra_headers: dict | None = None,
):
    data: dict[str, str] = {}
    if include_child and child_id is not None:
        data["child_id"] = str(child_id)
    if include_date:
        data["on_date"] = on_date if on_date is not None else shanghai_today().isoformat()
    if duration is not None:
        data["duration_ms"] = duration
    files = {}
    if content is not None:
        mime = "video/quicktime" if filename.endswith(".mov") else "video/mp4"
        files["file"] = (filename, content, mime)
    if poster is not None:
        files["poster"] = poster
    headers = {"Idempotency-Key": key or str(uuid.uuid4())}
    if extra_headers:
        headers.update(extra_headers)
    return client.post("/api/videos", data=data, files=files or None, headers=headers)


def _names(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.namelist()


def test_copy_no_banned_words():
    assert len(EXPECTED) == 64
    assert VIDEO == EXPECTED
    blob = "\n".join(VIDEO.values())
    for word in BANNED:
        assert word not in blob
    surfaces = [
        Path("app/video_copy.py"),
        Path("app/static/video.js"),
        Path("app/services/videos.py"),
        Path("app/routers/videos.py"),
        Path("app/templates/video_upload.html"),
        Path("app/templates/video_detail.html"),
        Path("app/templates/video_missing.html"),
        Path("app/templates/fragments/video_card.html"),
    ]
    for path in surfaces:
        text = path.read_text(encoding="utf-8")
        for word in BANNED:
            assert word not in text, f"{word} in {path}"
    script = Path("app/static/video.js").read_text(encoding="utf-8")
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in script)
    constants = Path("app/constants.py").read_text(encoding="utf-8")
    assert "一次最多" not in constants
    assert "from app.video_copy import VIDEO as VIDEO" in constants


def test_cancel_pick_leaves_no_row(client: TestClient):
    child_id = _family(client, "cancel@example.com", "妈妈", "取消家")
    page = client.get(f"/videos/new?child={child_id}")
    assert page.status_code == 200
    assert VIDEO["u_cancel_note"] in page.text
    assert "data-age-under" in page.text
    assert _count() == 0
    source = Path("app/static/video.js").read_text(encoding="utf-8")
    assert "if (!file)" in source
    assert "XMLHttpRequest" in source
    assert 'setRequestHeader("Idempotency-Key"' in source


def test_abort_before_commit_no_row(client: TestClient, monkeypatch, video_media: Path):
    child_id = _family(client, "abort@example.com", "妈妈", "中止家")
    monkeypatch.setenv("VIDEO_FAIL_BEFORE_COMMIT", "1")
    with pytest.raises(RuntimeError, match="video fail before commit"):
        _post(client, child_id, _mp4())
    assert _count() == 0
    assert list(video_media.glob("*.mp4")) == []
    assert list(video_media.glob(".up-*")) == []


def test_partial_temp_swept_on_startup(video_media: Path, monkeypatch):
    old = video_media / ".up-old.partial"
    fresh = video_media / ".up-new.partial"
    other = video_media / "vup-old.partial"
    photo = video_media / "abc123.jpg"
    for path, payload in ((old, b"old"), (fresh, b"new"), (other, b"vup"), (photo, b"photo")):
        path.write_bytes(payload)
    import os
    import time

    past = time.time() - 7200
    os.utime(old, (past, past))
    os.utime(other, (past, past))
    monkeypatch.setenv("VIDEO_PARTIAL_MAX_AGE_S", "60")
    removed = sweep_video_partials()
    assert removed == 2
    assert not old.exists()
    assert not other.exists()
    assert fresh.read_bytes() == b"new"
    assert photo.read_bytes() == b"photo"
    assert "sweep_video_partials" in Path("app/main.py").read_text(encoding="utf-8")


def test_late_201_same_key_no_duplicate(client: TestClient, video_media: Path):
    child_id = _family(client, "late@example.com", "妈妈", "迟到家")
    key = str(uuid.uuid4())
    first = _post(client, child_id, _mp4(12500), key=key)
    assert first.status_code == 201, first.text
    stored = first.json()["byte_size"]
    second = _post(client, child_id, _mp4(4000) + b"\x11" * 40, key=key)
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["byte_size"] == stored
    assert _count() == 1
    files = list(video_media.glob("*.mp4"))
    assert len(files) == 1
    assert files[0].stat().st_size == stored


def test_over_duration_or_size_400(client: TestClient, monkeypatch):
    child_id = _family(client, "limit@example.com", "妈妈", "上限家")
    too_long = _post(client, child_id, _mp4(12500), duration="60001")
    assert too_long.status_code == 400
    assert too_long.json()["code"] == "video_duration"
    assert too_long.json()["detail"] == VIDEO["err_limit_dur"]
    parsed = _post(client, child_id, _mp4(61000), duration="1000")
    assert parsed.status_code == 400
    assert parsed.json()["code"] == "video_duration"
    monkeypatch.setattr("app.services.videos.VIDEO_MAX_BYTES", 32)
    huge = _post(client, child_id, _mp4(1000) + b"\x00" * 64, duration="1000")
    assert huge.status_code == 400
    assert huge.json()["code"] == "video_size"
    assert huge.json()["detail"] == VIDEO["err_limit_size"]
    for word in BANNED:
        assert word not in too_long.text
        assert word not in huge.text
    assert _count() == 0
    monkeypatch.setattr("app.services.videos.VIDEO_MAX_BYTES", 100 * 1024 * 1024)
    edge = _post(client, child_id, _mp4(60000), duration="60000")
    assert edge.status_code == 201, edge.text
    assert _count() == 1


def test_reject_webm_or_unknown_ftyp(client: TestClient):
    child_id = _family(client, "fmt@example.com", "妈妈", "格式家")
    webm = _post(client, child_id, b"\x1a\x45\xdf\xa3" + b"\x00" * 16, filename="clip.webm")
    assert webm.status_code == 400
    assert webm.json()["code"] == "video_format"
    assert webm.json()["detail"] == VIDEO["err_format"]
    unknown = _post(client, child_id, _mp4(brand=b"zzzz"))
    assert unknown.status_code == 400
    assert unknown.json()["code"] == "video_format"
    empty = _post(client, child_id, b"\x00\x00\x00\x08ftyp")
    assert empty.status_code == 400
    assert empty.json()["code"] == "video_format"
    assert _count() == 0
    mov = _post(client, child_id, _mp4(brand=b"qt  "), filename="clip.mov", duration="4000")
    assert mov.status_code == 201, mov.text
    assert mov.json()["duration_label"] == "0:04"
    classic = _box(b"moov", _mvhd(3000))
    accepted = _post(client, child_id, classic, filename="classic.mov", duration="3000")
    assert accepted.status_code == 201, accepted.text


def test_demoted_uploader_post_403(client: TestClient):
    child_id = _family(client, "demote-admin@example.com", "家长", "降级家")
    uploader = _invite(client, "demote-up@example.com", "uploader", "小姨")
    session = SessionLocal()
    try:
        user_id = uploader.get("/api/me").json()["id"]
        membership = session.scalar(select(Membership).where(Membership.user_id == user_id))
        assert membership is not None
        membership.role = "viewer"
        session.commit()
    finally:
        session.close()
    response = _post(uploader, child_id, _mp4())
    assert response.status_code == 403
    assert response.json()["code"] == "video_forbidden_role"
    assert response.json()["detail"] == FORBIDDEN
    assert "private, no-store" in response.headers["cache-control"]
    assert _count() == 0


def test_removed_member_403_no_content(client: TestClient):
    child_id = _family(client, "rm-admin@example.com", "家长", "移出家", child="秘密果果")
    member = _invite(client, "rm-up@example.com", "uploader", "小姨")
    created = _post(client, child_id, _mp4())
    assert created.status_code == 201
    video_id = created.json()["id"]
    session = SessionLocal()
    try:
        user_id = member.get("/api/me").json()["id"]
        membership = session.scalar(select(Membership).where(Membership.user_id == user_id))
        assert membership is not None
        session.delete(membership)
        session.commit()
    finally:
        session.close()
    denied = _post(member, child_id, _mp4())
    assert denied.status_code == 403
    assert "code" not in denied.json()
    assert denied.json()["detail"] == FORBIDDEN
    assert "秘密果果" not in denied.text
    media = member.get(f"/media/videos/{video_id}")
    assert media.status_code == 403
    assert b"ftyp" not in media.content
    assert "秘密果果" not in media.text
    assert member.get("/api/me").status_code == 200
    source = Path("app/static/video.js").read_text(encoding="utf-8")
    assert 'fetch("/api/me"' in source
    assert 'videoCopy("e_removed")' in source


def test_validation_codes_in_order(client: TestClient, monkeypatch):
    child_id = _family(client, "order-v@example.com", "妈妈", "顺序校验家")
    other = TestClient(app)
    other_child = _family(other, "order-b@example.com", "别人", "别家", child="豆豆")
    steps = [
        (_post(client, child_id, b"", key="not-a-uuid"), "idempotency_key_invalid", VIDEO["err_retry"]),
        (_post(client, child_id, b""), "video_empty", VIDEO["err_format"]),
    ]
    monkeypatch.setattr("app.services.videos.VIDEO_MAX_BYTES", 16)
    steps.append((_post(client, child_id, b"\x00" * 80), "video_size", VIDEO["err_limit_size"]))
    monkeypatch.setattr("app.services.videos.VIDEO_MAX_BYTES", 100 * 1024 * 1024)
    clip = _mp4(12500)
    steps.extend(
        [
            (_post(client, child_id, b"\x1a\x45\xdf\xa3" + b"\x00" * 24, filename="a.webm"), "video_format", VIDEO["err_format"]),
            (_post(client, child_id, clip, duration=None), "video_duration_invalid", VIDEO["err_limit_dur"]),
            (_post(client, child_id, clip, duration="0"), "video_duration_invalid", VIDEO["err_limit_dur"]),
            (_post(client, child_id, clip, duration="60001"), "video_duration", VIDEO["err_limit_dur"]),
            (_post(client, None, clip, include_child=False), "child_required", VIDEO["err_no_child"]),
            (_post(client, other_child, clip), "child_not_found", VIDEO["err_no_child"]),
            (_post(client, child_id, clip, on_date="2026/10/24"), "date_invalid", VIDEO["err_date_future"]),
            (_post(client, child_id, clip, on_date="2099-01-01"), "date_future", VIDEO["err_date_future"]),
            (_post(client, child_id, clip, on_date="2020-01-01"), "date_before_birth", "那天果果还没出生"),
        ]
    )
    monkeypatch.setattr("app.services.videos.POSTER_MAX_BYTES", 8)
    steps.append(
        (
            _post(client, child_id, clip, poster=("p.jpg", JPEG, "image/jpeg")),
            "poster_invalid",
            VIDEO["err_format"],
        )
    )
    seen = []
    for response, code, detail in steps:
        assert response.status_code == 400, response.text
        assert response.json()["code"] == code
        assert response.json()["detail"] == detail
        assert "private, no-store" in response.headers["cache-control"]
        seen.append(code)
    assert seen[0] == "idempotency_key_invalid"
    assert _count() == 0


def test_post_spent_key_409(client: TestClient):
    child_id = _family(client, "spent-v@example.com", "妈妈", "墓碑家")
    key = str(uuid.uuid4())
    created = _post(client, child_id, _mp4(), key=key)
    assert created.status_code == 201
    assert client.delete(f"/api/videos/{created.json()['id']}").status_code == 204
    again = _post(client, child_id, _mp4(), key=key)
    assert again.status_code == 409
    assert again.json()["code"] == "video_key_spent"
    assert again.json()["detail"] == VIDEO["err_retry"]
    assert _count() == 1
    tomb = _rows()[0]
    assert tomb.deleted_at is not None
    assert tomb.byte_size == 0
    assert tomb.storage_path == ""


def test_parallel_same_key_one_row(client: TestClient):
    child_id = _family(client, "par-v@example.com", "妈妈", "并发家")
    other = TestClient(app)
    other.cookies.update(client.cookies)
    key = str(uuid.uuid4())
    barrier = threading.Barrier(2)
    results: list = []

    def go(actor: TestClient) -> None:
        barrier.wait(5)
        results.append(_post(actor, child_id, _mp4(), key=key))

    threads = [threading.Thread(target=go, args=(actor,)) for actor in (client, other)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert sorted(item.status_code for item in results) == [200, 201]
    assert any(item.json().get("replayed") is True for item in results)
    assert _count() == 1


def test_uploader_cannot_delete_others(client: TestClient):
    child_id = _family(client, "own-admin@example.com", "家长", "删除家")
    created = _post(client, child_id, _mp4())
    video_id = created.json()["id"]
    uploader = _invite(client, "own-up@example.com", "uploader", "可传")
    denied = uploader.delete(f"/api/videos/{video_id}")
    assert denied.status_code == 403
    assert denied.json()["code"] == "video_not_author"
    assert denied.json()["detail"] == FORBIDDEN
    assert client.get(f"/api/videos/{video_id}").status_code == 200


def test_delete_twice_second_404(client: TestClient):
    child_id = _family(client, "twice@example.com", "妈妈", "再删家")
    created = _post(client, child_id, _mp4())
    video_id = created.json()["id"]
    assert client.delete(f"/api/videos/{video_id}").status_code == 204
    second = client.delete(f"/api/videos/{video_id}")
    assert second.status_code == 404
    assert second.json() == NOT_FOUND


def test_storage_error_no_row(client: TestClient, monkeypatch, video_media: Path):
    child_id = _family(client, "disk@example.com", "妈妈", "磁盘家")

    def boom(self, key, source, content_type):
        del self, key, source, content_type
        raise StorageError("VOL_MISSING", "媒资卷没挂上")

    monkeypatch.setattr(LocalStore, "put_file", boom)
    response = client.post(
        "/api/videos",
        data={
            "child_id": str(child_id),
            "on_date": shanghai_today().isoformat(),
            "duration_ms": "12500",
        },
        files={"file": ("clip.mp4", _mp4(), "video/mp4")},
        headers={"Idempotency-Key": str(uuid.uuid4())},
        follow_redirects=False,
    )
    assert response.status_code in {303, 503}
    assert _count() == 0
    assert list(video_media.glob("*.mp4")) == []
    assert list(video_media.glob(".up-*")) == []


def test_js_treats_422_as_retryable_or_field(client: TestClient):
    child_id = _family(client, "unprocess@example.com", "妈妈", "校验家")
    # Optional form fields do not 422 when the body is JSON. A valid key and no
    # file is the service's 400 video_empty. Framework 422 is mapped in video.js.
    response = client.post(
        "/api/videos",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"child_id": child_id},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "video_empty"
    source = Path("app/static/video.js").read_text(encoding="utf-8")
    mapped = source.split("function mapUpload", 1)[1].split("function afterForbidden", 1)[0]
    assert mapped.index("status === 422") < mapped.index('return videoCopy("err_retry")')
    assert 'return videoCopy("err_retry")' in mapped


def test_js_404_without_code_is_retryable():
    source = Path("app/static/video.js").read_text(encoding="utf-8")
    mapped = source.split("function mapUpload", 1)[1].split("function afterForbidden", 1)[0]
    assert 'status === 404 && !(data && data.code)' in mapped
    assert 'return videoCopy("err_retry")' in mapped
    detail = source.split("function bootDetail", 1)[1]
    assert 'data.code === "video_not_found"' in detail
    assert "status === 404 && !(data && data.code)" in detail
    assert 'copy("err_retry")' in detail
    assert 'visibilityState === "hidden"' in source
    assert 'addEventListener("pagehide"' in source
    assert 'addEventListener("beforeunload"' in source
    assert "xhr.abort" in source or ".abort()" in source


def test_idempotency_two_clients(client: TestClient):
    child_id = _family(client, "two@example.com", "妈妈", "两客户端家")
    other = TestClient(app)
    other.cookies.update(client.cookies)
    key = str(uuid.uuid4())
    first = _post(client, child_id, _mp4(), key=key)
    second = _post(other, child_id, _mp4(3000), key=key)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["replayed"] is True
    assert _count() == 1


def test_video_cross_origin_write_rejected(client: TestClient):
    child_id = _family(client, "csrf-v@example.com", "妈妈", "跨站家")
    response = _post(
        client,
        child_id,
        _mp4(),
        extra_headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": FORBIDDEN}
    assert "no-store" in response.headers["cache-control"]
    assert _count() == 0


def test_expired_session_403():
    anonymous = TestClient(app)
    response = anonymous.post(
        "/api/videos",
        data={"child_id": "1", "on_date": "2026-10-24", "duration_ms": "1000"},
        files={"file": ("clip.mp4", _mp4(), "video/mp4")},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": FORBIDDEN}
    assert "code" not in response.json()
    assert anonymous.get("/api/me").status_code == 401


def test_upload_mp4_under_limits_201_on_feed(client: TestClient):
    child_id = _family(client, "feed@example.com", "妈妈", "时光家")
    today = shanghai_today().isoformat()
    photo = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert photo.status_code == 201
    journal = client.post(
        "/api/journals",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"child_id": child_id, "on_date": today, "body": "日志在前", "photo_ids": []},
    )
    assert journal.status_code == 201, journal.text
    created = _post(
        client,
        child_id,
        _mp4(12500),
        poster=("cover.jpg", JPEG, "image/jpeg"),
        on_date=today,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["replayed"] is False
    assert body["duration_ms"] == 12500
    assert body["duration_label"] == "0:12"
    assert body["has_poster"] is True
    assert body["poster_url"] == f"/media/videos/{body['id']}/poster"
    assert body["media_url"] == f"/media/videos/{body['id']}"
    assert body["can_delete"] is True
    assert "uploader_id" not in body
    assert "storage_path" not in body
    assert "email" not in body
    assert "private, no-store" in created.headers["cache-control"]
    label = age_label(date(2024, 7, 12), shanghai_today())
    assert body["age_label"] == label
    page = client.get(f"/?child={child_id}&vtoast=posted&vdate={body['date_label']}&vhl={body['id']}")
    assert page.status_code == 200
    text = page.text
    assert text.index(f"/journals/{journal.json()['id']}") < text.index(f"/videos/{body['id']}")
    assert text.index(f"/videos/{body['id']}") < text.index(f"/photos/{photo.json()['id']}")
    assert "妈妈传" in text
    assert "0:12" in text
    assert label in text
    assert f"挂上了 · {body['date_label']}" in text
    assert "vcard hl" in text
    media = client.get(body["media_url"])
    assert media.status_code == 200
    assert media.headers["content-type"].startswith("video/mp4")
    assert "max-age=3600" in media.headers["cache-control"]
    assert media.headers["accept-ranges"] == "bytes"
    assert media.content.startswith(b"\x00\x00\x00")
    ranged = client.get(body["media_url"], headers={"Range": "bytes=0-3"})
    assert ranged.status_code == 206
    assert ranged.headers["content-range"].startswith("bytes 0-3/")
    assert len(ranged.content) == 4
    missing = client.get(body["media_url"], headers={"Range": "bytes=999999-999999"})
    assert missing.status_code == 416
    assert "video_not_found" not in missing.text
    poster = client.get(body["poster_url"])
    assert poster.status_code == 200
    assert poster.headers["content-type"].startswith("image/jpeg")
    assert "no-store" in poster.headers["cache-control"]
    detail = client.get(f"/videos/{body['id']}")
    assert detail.status_code == 200
    assert "<video" in detail.text
    assert 'src="/media/videos/' in detail.text
    assert "target=" not in detail.text
    assert "http://" not in detail.text
    assert "https://" not in detail.text
    router = Path("app/routers/videos.py").read_text(encoding="utf-8")
    assert "b\"\".join" not in router
    assert ".read()" not in router
    assert "read(64 * 1024)" in router


def test_viewer_can_play_member_cannot(client: TestClient):
    child_id = _family(client, "play-admin@example.com", "妈妈", "播放家", child="果果播放")
    created = _post(client, child_id, _mp4(), poster=("cover.jpg", JPEG, "image/jpeg"))
    assert created.status_code == 201
    video_id = created.json()["id"]
    viewer = _invite(client, "play-view@example.com", "viewer", "爷爷")
    page = viewer.get(f"/videos/{video_id}")
    assert page.status_code == 200
    assert "<video" in page.text
    assert f'src="/media/videos/{video_id}"' in page.text
    assert "http://" not in page.text
    assert "data-video-delete" not in page.text
    home = viewer.get(f"/?child={child_id}")
    assert 'class="fab"' not in home.text
    assert "/videos/new" not in home.text
    assert "/upload" not in home.text
    assert "只读 · 只能看" in home.text
    played = viewer.get(f"/media/videos/{video_id}")
    assert played.status_code == 200
    assert played.headers["content-type"].startswith("video/mp4")
    assert played.content.startswith(b"\x00\x00\x00")
    blocked = _post(viewer, child_id, _mp4())
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "video_forbidden_role"
    assert viewer.get("/videos/new").status_code == 403
    missing_child = _post(client, None, _mp4(), include_child=False)
    assert missing_child.status_code == 400
    assert missing_child.json()["code"] == "child_required"
    outsider = TestClient(app)
    for url in (f"/api/videos/{video_id}", f"/media/videos/{video_id}", f"/media/videos/{video_id}/poster", f"/videos/{video_id}"):
        denied = outsider.get(url)
        assert denied.status_code == 403, url
        assert "果果播放" not in denied.text
        assert b"ftyp" not in denied.content
        assert "0:12" not in denied.text


def test_delete_matrix_admin_uploader(client: TestClient):
    child_id = _family(client, "matrix-admin@example.com", "家长", "矩阵家")
    uploader = _invite(client, "matrix-up@example.com", "uploader", "小姨")
    viewer = _invite(client, "matrix-view@example.com", "viewer", "奶奶")
    mine = _post(uploader, child_id, _mp4())
    assert mine.status_code == 201
    mine_id = mine.json()["id"]
    admin_clip = _post(client, child_id, _mp4(4000), duration="4000")
    admin_id = admin_clip.json()["id"]
    assert viewer.delete(f"/api/videos/{mine_id}").json()["code"] == "video_forbidden_role"
    assert uploader.delete(f"/api/videos/{admin_id}").json()["code"] == "video_not_author"
    assert client.delete(f"/api/videos/{mine_id}").status_code == 204
    gone = client.get(f"/api/videos/{mine_id}")
    assert gone.status_code == 404
    assert gone.json() == NOT_FOUND
    assert uploader.delete(f"/api/videos/{admin_id}").status_code == 403
    own = _post(uploader, child_id, _mp4(2000), duration="2000")
    assert uploader.delete(f"/api/videos/{own.json()['id']}").status_code == 204
    page = client.get(f"/?child={child_id}")
    assert f"/videos/{mine_id}" not in page.text
    assert f"/videos/{admin_id}" in page.text


def test_export_zip_contains_own_videos_only(client: TestClient):
    child_id = _family(client, "zip-admin@example.com", "妈妈", "导出家")
    other = _invite(client, "zip-up@example.com", "uploader", "小姨")
    early = _post(client, child_id, _mp4(2000), duration="2000", on_date="2026-10-02")
    late = _post(client, child_id, _mp4(3000), duration="3000", on_date="2026-10-20")
    assert early.status_code == late.status_code == 201
    foreign = _post(other, child_id, _mp4(4000), duration="4000", on_date="2026-10-09")
    assert foreign.status_code == 201
    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 200, packed.text
    names = _names(packed.content)
    assert names == [
        f"2026-10-02_{early.json()['id']}.mp4",
        f"2026-10-20_{late.json()['id']}.mp4",
    ]
    assert all(str(foreign.json()["id"]) not in name for name in names)
    counted = client.get("/api/export/my-photos")
    assert counted.json() == {"count": 0}
    page = client.get("/family/export")
    assert "永久免费" in page.text
    assert "你还没传过照片" not in page.text


def test_export_videos_only_zip_ok(client: TestClient):
    child_id = _family(client, "onlyv@example.com", "妈妈", "仅视频家")
    created = _post(client, child_id, _mp4(), on_date="2026-10-18")
    assert created.status_code == 201
    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 200
    assert _names(packed.content) == [f"2026-10-18_{created.json()['id']}.mp4"]
    empty_user = TestClient(app)
    _family(empty_user, "novid@example.com", "爸爸", "空导出家")
    missing = empty_user.get("/api/export/my-photos.zip")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "no_photos", "count": 0}


def test_feed_order_journal_video_photo(client: TestClient):
    child_id = _family(client, "mixv@example.com", "妈妈", "混排视频家")
    today = shanghai_today().isoformat()
    photo = upload(client, kind="child", child_id=child_id, taken_on=today)
    journal = client.post(
        "/api/journals",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"child_id": child_id, "on_date": today, "body": "第一条甲", "photo_ids": [photo.json()["id"]]},
    )
    video = _post(client, child_id, _mp4(), on_date=today)
    assert journal.status_code == 201 and video.status_code == 201
    text = client.get(f"/?child={child_id}").text
    assert text.index("第一条甲") < text.index(f"/videos/{video.json()['id']}")
    assert text.index(f"/videos/{video.json()['id']}") < text.index('class="pics')
    timeline = client.get(f"/api/children/{child_id}/timeline").json()
    assert "journals" not in timeline["days"][0]
    assert "videos" not in timeline["days"][0]


def test_create_without_poster_ok(client: TestClient):
    child_id = _family(client, "noph@example.com", "妈妈", "无封面家")
    created = _post(client, child_id, _mp4())
    assert created.status_code == 201
    body = created.json()
    assert body["has_poster"] is False
    assert body["poster_url"] is None
    page = client.get(f"/?child={child_id}")
    assert VIDEO["cover_ph"] in page.text
    poster = client.get(f"/media/videos/{body['id']}/poster")
    assert poster.status_code == 404
    assert poster.json()["code"] == "poster_missing"
    media = client.get(body["media_url"])
    assert media.status_code == 200


def test_foreign_missing_deleted_same_404(client: TestClient):
    child_id = _family(client, "priv-a@example.com", "妈妈", "隐私家", child="隐私果")
    created = _post(client, child_id, _mp4(), poster=("cover.jpg", JPEG, "image/jpeg"))
    video_id = created.json()["id"]
    outsider_family = TestClient(app)
    _family(outsider_family, "priv-b@example.com", "别人", "别的家")
    missing_id = 999999
    samples = []

    def grab(actor: TestClient, vid: int):
        api = actor.get(f"/api/videos/{vid}")
        media = actor.get(f"/media/videos/{vid}")
        poster = actor.get(f"/media/videos/{vid}/poster")
        assert api.json() == media.json() == poster.json() == NOT_FOUND
        for response in (api, media, poster):
            assert response.status_code == 404
            assert "private, no-store" in response.headers["cache-control"]
            assert "隐私果" not in response.text
        samples.append(api.json())

    grab(client, missing_id)
    grab(outsider_family, video_id)
    assert client.delete(f"/api/videos/{video_id}").status_code == 204
    grab(client, video_id)
    kept = _post(client, child_id, _mp4())
    kept_id = kept.json()["id"]
    session = SessionLocal()
    try:
        child = session.get(Child, child_id)
        assert child is not None
        session.delete(child)
        session.commit()
    finally:
        session.close()
    grab(client, kept_id)
    assert samples == [NOT_FOUND, NOT_FOUND, NOT_FOUND, NOT_FOUND]


def test_missing_blob_detail_not_found(client: TestClient, video_media: Path):
    child_id = _family(client, "blob@example.com", "妈妈", "缺文件家")
    created = _post(client, child_id, _mp4())
    video_id = created.json()["id"]
    session = SessionLocal()
    try:
        video = session.get(Video, video_id)
        assert video is not None
        path = video_media / video.storage_path
    finally:
        session.close()
    path.unlink()
    page = client.get(f"/videos/{video_id}")
    assert page.status_code == 404
    assert VIDEO["not_found"] in page.text
    assert "<video" not in page.text
    media = client.get(f"/media/videos/{video_id}")
    assert media.status_code == 404
    assert media.json() == NOT_FOUND
