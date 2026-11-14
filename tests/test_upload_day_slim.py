"""Daily upload slim: Qige ticket, stamp grid, timeline row. No new tables."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.constants import (
    DAILY_BATCH_MAX_FILES,
    DAILY_CONCURRENCY,
    SILENT_FALLBACK_EDGE,
    SILENT_MAX_EDGE,
    SILENT_TARGET_BYTES,
    UPLOAD_DAY_SLIM,
)
from app.main import app
from app.migrate_schema import migrate_schema
from app.services.clock import shanghai_today
from app.upload_day_slim_copy import UPLOAD_DAY_SLIM_FINAL_KEYS
from tests.test_acceptance import JPEG, auth, upload
from tests.test_journal import _family, _invite

JS = Path("app/static/upload_day_slim.js")
DAY_JS = Path("app/static/day_grid.js")
BANNED = ("会员", "开通", "商城", "发现", "广告")
JARGON = ("压缩率", "画质", "20MB")
ABBREV = re.compile(r"(?i)(?<![A-Za-z])(kb|mb)(?![A-Za-z])")
SURFACES = (
    Path("app/upload_day_slim_copy.py"),
    Path("app/static/upload_day_slim.js"),
    Path("app/static/day_grid.js"),
    Path("app/templates/upload.html"),
    Path("app/templates/day.html"),
    Path("app/templates/fragments/stage_inner.html"),
)
FINAL_KEYS = {
    "confirm_title": "放到这一天",
    "date_lab": "哪一天",
    "date_today": "今天 · {mo}月{d}日",
    "submit": "放入",
    "submit_self": "放到我的相册",
    "pick_n": "已选 {n} 张",
    "progress": "正在放入 {i}/{n}",
    "fail_one": "{ok} 张已放入，有 {fail} 张没成功",
    "fail_go": "去看已放入的",
    "done_toast": "已放进今天",
    "err_size": "有一张太大了，先拿掉再放",
    "err_bad": "有一张不是照片，已跳过。其余可以继续放。",
    "self_banner": "这些照片记在你名下，不会出现在孩子时光线。",
    "self_album": "家人相册 · {name}",
    "count": "共 {n} 张",
    "empty_tl": "还没有照片",
    "empty_cta": "放入第一张",
    "swipe": "左右滑，看这一天",
    "lb_count": "{i} / {n}",
    "ro_meta": "只读 · 只能看",
    "byline": "{who}传",
    "strip_label": "贴上兄弟姐妹",
    "strip_hint_pre": "已贴{name}，可再贴兄弟姐妹",
}


def _node(body: str) -> subprocess.CompletedProcess[str]:
    script = f'const api=require({str(JS.resolve())!r});{body}'
    return subprocess.run(["node", "-e", script], check=False, capture_output=True, text=True)


def _require_node() -> None:
    if subprocess.run(["node", "-v"], check=False, capture_output=True).returncode != 0:
        pytest.skip("node not installed")


def test_copy_final_keys_match_and_err_batch():
    assert tuple(FINAL_KEYS) == UPLOAD_DAY_SLIM_FINAL_KEYS
    for key, value in FINAL_KEYS.items():
        assert UPLOAD_DAY_SLIM[key] == value
    assert UPLOAD_DAY_SLIM["err_batch"] == "一次先放五十张就好，多的下次再放"
    assert UPLOAD_DAY_SLIM["submit_other"] == "放进这一天"
    assert UPLOAD_DAY_SLIM["chip_today"] == "今天"
    assert UPLOAD_DAY_SLIM["chip_who"] == "{name}"
    assert UPLOAD_DAY_SLIM["chip_pick"] == "选一天"
    assert UPLOAD_DAY_SLIM["lab_day"] == "哪一天"
    assert UPLOAD_DAY_SLIM["lab_who"] == "再贴人"
    assert UPLOAD_DAY_SLIM["self_note"] == "记在我名下，不进孩子时光线"
    assert UPLOAD_DAY_SLIM["dismiss"] == "拿掉这些"
    assert UPLOAD_DAY_SLIM["n_zhang"] == "{n}张"
    assert " " not in UPLOAD_DAY_SLIM["n_zhang"]
    assert UPLOAD_DAY_SLIM["done_toast_other"] == "已放进这一天"
    assert UPLOAD_DAY_SLIM["date_only"] == "{mo}月{d}日"
    assert UPLOAD_DAY_SLIM["day_title"] == "{mo}月{d}日 {weekday}"
    assert "50" not in UPLOAD_DAY_SLIM["err_batch"]
    assert "anno_" not in UPLOAD_DAY_SLIM


def test_copy_forbidden_words_upload_day_slim():
    blob = "\n".join(path.read_text(encoding="utf-8") for path in SURFACES)
    for word in BANNED:
        assert word not in blob
        assert word not in "".join(UPLOAD_DAY_SLIM.values())
    for word in JARGON:
        assert word not in "".join(UPLOAD_DAY_SLIM.values())
        assert word not in blob
    assert ABBREV.search(blob) is None
    assert ABBREV.search("".join(UPLOAD_DAY_SLIM.values())) is None
    assert "刚放进" not in blob
    assert "anno_" not in Path("app/templates/day.html").read_text(encoding="utf-8")
    assert "20MB" in Path("app/routers/photos.py").read_text(encoding="utf-8")
    assert "20MB" not in Path("app/templates/upload.html").read_text(encoding="utf-8")


def test_copy_no_size_or_quality_jargon(client: TestClient):
    child_id = _family(client, "uds-copy@example.com", "妈妈", "文案家", child="果果")
    gone = client.get(f"/upload?kind=child&child={child_id}", follow_redirects=False)
    assert gone.status_code == 302
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 200
    for word in JARGON:
        assert word not in page.text
    assert "画质" not in page.text
    assert "压缩" not in page.text
    assert UPLOAD_DAY_SLIM["confirm_title"] not in page.text
    assert UPLOAD_DAY_SLIM["submit"] in page.text
    button = page.text.split('id="photo-submit"', 1)[1].split("</button>", 1)[0]
    assert ">放入<" in ">" + button.split(">", 1)[-1] + "<" or button.endswith("放入")
    assert "放进这一天" not in button
    assert "照片放到哪里" not in page.text
    wizard = client.get("/upload")
    assert "照片放到哪里" in wizard.text


def test_daily_upload_over_50_blocked_client():
    _require_node()
    completed = _node(
        "if (api.batchBlock(51)!=='err_batch') process.exit(1);"
        "if (api.batchBlock(50)!=='') process.exit(2);"
        "if (api.classifyFiles(Array.from({length:51},()=>({name:'a.jpg',type:'image/jpeg',size:10}))).canSubmit) process.exit(3);"
        "if (api.DAILY_BATCH_MAX_FILES!==50) process.exit(4);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert DAILY_BATCH_MAX_FILES == 50


def test_daily_upload_over_20mib_blocked_client(client: TestClient):
    _require_node()
    cap = 20 * 1024 * 1024
    completed = _node(
        f"if (api.fileSizeBlock({cap}+1)!=='err_size') process.exit(1);"
        f"if (api.fileSizeBlock({cap})!=='') process.exit(2);"
        f"const row=api.classifyFiles([{{name:'a.jpg',type:'image/jpeg',size:{cap}+1}}]);"
        "if (row.block!=='err_size'||row.canSubmit) process.exit(3);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    child_id = _family(client, "uds-big@example.com", "妈妈", "超帽家", child="果果")
    big = b"\xff\xd8\xff" + b"\x00" * (cap + 8)
    denied = upload(client, kind="child", child_id=child_id, content=big, taken_on="2026-10-22")
    assert denied.status_code == 400
    assert "20MB" in denied.json()["detail"]
    page = client.get(f"/upload?kind=child&child={child_id}")
    assert "20MB" not in page.text
    assert UPLOAD_DAY_SLIM["err_size"] in Path("app/upload_day_slim_copy.py").read_text(encoding="utf-8")


def test_daily_upload_bad_file_skipped():
    _require_node()
    completed = _node(
        "const row=api.classifyFiles(["
        "{name:'note.txt',type:'text/plain',size:12},"
        "{name:'a.jpg',type:'image/jpeg',size:20}"
        "]);"
        "if (row.bad!==1||row.canSubmit!==true||row.block!=='err_bad') process.exit(1);"
        "if (row.items.length!==1) process.exit(2);"
        "if (api.allowedType({name:'a.gif',type:'image/gif',size:10})) process.exit(3);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_daily_upload_compress_fallback_original():
    _require_node()
    completed = _node(
        "const cap=api.MAX_UPLOAD_BYTES;"
        "const small=api.choosePayload(1000,100,[]);"
        "if (small.action!=='original') process.exit(1);"
        "const failed=api.choosePayload(8000,4000,[]);"
        "if (failed.action!=='original') process.exit(2);"
        "const bigger=api.choosePayload(5000,4000,[{size:9000}]);"
        "if (bigger.action!=='original') process.exit(3);"
        "if (api.SILENT_MAX_EDGE!==2560||api.SILENT_TARGET_BYTES!==2*1024*1024) process.exit(4);"
        "const steps=api.encodeSteps();"
        "if (steps[0].quality!==0.82||steps[1].quality!==0.70||steps[2].edge!==1920) process.exit(5);"
        "if (!(cap>api.SILENT_TARGET_BYTES)) process.exit(6);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    source = JS.read_text(encoding="utf-8")
    assert "createImageBitmap" in source
    assert "imageOrientation" in source
    assert "from-image" in source
    assert SILENT_MAX_EDGE == 2560
    assert SILENT_TARGET_BYTES == 2 * 1024 * 1024
    assert SILENT_FALLBACK_EDGE == 1920


def test_daily_upload_compressed_still_over_cap():
    _require_node()
    cap = 20 * 1024 * 1024
    completed = _node(
        f"const over=api.choosePayload({cap}+100,4000,[{{size:{cap}+50}}]);"
        "if (over.action!=='fail') process.exit(1);"
        f"const kept=api.choosePayload({cap}-10,4000,[{{size:{cap}+50}}]);"
        "if (kept.action!=='original') process.exit(2);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_daily_upload_progress_line_is_current_file():
    _require_node()
    completed = _node(
        "const line=api.linePercent(2,5,62,100);"
        "const batch=api.batchFillPercent(2,5);"
        "if (Math.abs(batch-40)>0.001) process.exit(1);"
        "if (Math.abs(line-62)>0.001) process.exit(2);"
        "if (Math.abs(line-batch)<0.001) process.exit(3);"
        "if (api.progressWidth(0,100)!==0) process.exit(4);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    source = JS.read_text(encoding="utf-8")
    assert "linePercent(" in source
    assert 'line.style.width = String(width) + "%"' in source


def test_daily_upload_no_batch_buffer():
    _require_node()
    source = JS.read_text(encoding="utf-8")
    assert "arrayBuffer" not in source
    assert "readAsArrayBuffer" not in source
    assert "Promise.all" not in source
    assert DAILY_CONCURRENCY == 1
    completed = _node(
        "api.runSerial([1,2,3,4], function(){return Promise.resolve(1);}).then(function(max){"
        "if (max!==1||api.DAILY_CONCURRENCY!==1) process.exit(1);});"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_daily_upload_cancel_keeps_successes():
    _require_node()
    completed = _node(
        "const run=api.createRun();"
        "if (!api.noteSuccess(run,9,1)) process.exit(1);"
        "api.abortRun(run);"
        "const end=api.outcome(run,4);"
        "if (end.redirect||!end.failGo||end.ok!==1) process.exit(2);"
        "if (run.successes[0]!==9) process.exit(3);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_daily_upload_late_response_ignored():
    _require_node()
    completed = _node(
        "const run=api.createRun();"
        "api.noteSuccess(run,1,1);"
        "api.abortRun(run);"
        "if (api.noteSuccess(run,2,1)!==false) process.exit(1);"
        "if (run.successes.length!==1) process.exit(2);"
        "if (api.outcome(run,3).redirect) process.exit(3);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_daily_upload_offline_keeps_successes():
    _require_node()
    completed = _node(
        "const run=api.createRun();"
        "api.noteSuccess(run,4,1);"
        "const end=api.outcome(run,2);"
        "if (end.redirect||!end.failGo||end.ok!==1||end.fail!==1) process.exit(1);"
        "if (!api.retryable(0,{})) process.exit(2);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_js_404_without_code_retryable_daily_upload():
    _require_node()
    completed = _node(
        "if (!api.retryable(404,{})) process.exit(1);"
        "if (api.retryable(404,{code:'child_required'})) process.exit(2);"
        "if (!api.retryable(503,{})) process.exit(3);"
        "if (api.retryable(400,{})) process.exit(4);"
        "if (!api.retryable(0,{})) process.exit(5);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_daily_upload_partial_success(client: TestClient):
    _require_node()
    completed = _node(
        "const run=api.createRun();"
        "api.noteSuccess(run,1,1);api.noteSuccess(run,2,1);api.noteSuccess(run,3,1);api.noteSuccess(run,4,1);"
        "run.failures.push(5);"
        "const end=api.outcome(run,5);"
        "if (end.redirect||!end.failGo||end.ok!==4||end.fail!==1) process.exit(1);"
        "const url=api.landingUrl('child','8','3','2026-11-14',[1,2,3,4],'2026-11-14');"
        "if (url.indexOf('/children/8/days/2026-11-14?new=1,2,3,4&toast=placed_today')!==0) process.exit(2);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    child_id = _family(client, "uds-part@example.com", "妈妈", "部分家", child="果果")
    kept = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert kept.status_code == 201
    denied = client.post(
        "/api/photos",
        data={"kind": "child", "taken_on": "2026-10-22"},
        files={"file": ("a.gif", b"GIF89a", "image/gif")},
    )
    assert denied.status_code == 400
    page = client.get(f"/children/{child_id}/days/2026-10-22")
    assert f'data-photo-id="{kept.json()["id"]}"' in page.text


def test_daily_upload_child_required(client: TestClient):
    _family(client, "uds-child@example.com", "妈妈", "缺孩家", child="果果")
    _invite_unused = client.post("/api/children", json={"name": "豆豆", "birth_date": "2025-03-01"})
    assert _invite_unused.status_code == 201
    denied = client.post(
        "/api/photos",
        data={"kind": "child", "taken_on": "2026-10-22", "child_ids": ""},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert denied.status_code == 400
    assert denied.json()["code"] == "child_required"
    page = client.get("/upload?kind=child")
    assert "disabled" in page.text
    assert "至少选一个孩子" in page.text


def test_daily_upload_storage_error_no_rollback(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from app.storage.errors import StorageError

    child_id = _family(client, "uds-disk@example.com", "妈妈", "存储家", child="果果")
    first = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert first.status_code == 201

    def boom(*_args, **_kwargs):
        raise StorageError("VOL_MISSING", "missing")

    monkeypatch.setattr("app.services.photos.current_store", lambda: type("Boom", (), {"put": staticmethod(boom)})())
    try:
        second = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22", filename="b.jpg")
        assert second.status_code >= 500
    finally:
        app.state.storage_error = None
    page = client.get(f"/children/{child_id}/days/2026-10-22")
    assert page.text.count("data-photo-id=") == 1


def test_viewer_daily_upload_403(client: TestClient):
    child_id = _family(client, "uds-ro-a@example.com", "妈妈", "只读家", child="果果")
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-11-01")
    assert created.status_code == 201
    viewer = _invite(client, "uds-ro-v@example.com", "viewer", "外婆")
    page = viewer.get("/upload")
    assert page.status_code == 403
    assert "没有访问权限" in page.text
    denied = viewer.post(
        "/api/photos",
        data={"kind": "child", "child_id": str(child_id), "taken_on": "2026-11-01"},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "photo_forbidden_role"
    home = viewer.get(f"/?child={child_id}")
    assert 'class="fab"' not in home.text
    assert 'class="daycard"' in home.text
    assert 'href="/upload' not in home.text
    day = viewer.get(f"/children/{child_id}/days/2026-11-01")
    assert day.status_code == 200
    assert day.text.count('class="rochip"') == 1
    assert UPLOAD_DAY_SLIM["ro_meta"] in day.text
    assert "共 1 张" in day.text
    assert 'class="stamp' in day.text
    assert 'id="photo-submit"' not in day.text


def test_outsider_daily_upload_403_no_code(client: TestClient):
    child_id = _family(client, "uds-out-a@example.com", "妈妈", "外人家庭名ZZ", child="果果")
    photo = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert photo.status_code == 201
    me = client.get("/api/me").json()["id"]
    outsider = TestClient(app)
    auth(outsider, "uds-out-b@example.com", "路人")
    denied = outsider.post(
        "/api/photos",
        data={"kind": "self", "taken_on": "2026-10-22"},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "没有访问权限"}
    assert "code" not in denied.json()
    for url in (
        f"/children/{child_id}/days/2026-10-22",
        f"/people/{me}/album/days/2026-10-22",
        f"/upload?kind=child&child={child_id}",
    ):
        page = outsider.get(url)
        assert page.status_code == 403, url
        assert "没有访问权限" in page.text
        assert "外人家庭名ZZ" not in page.text
        assert "果果" not in page.text


def test_daily_upload_lands_on_day_grid(client: TestClient):
    child_id = _family(client, "uds-land@example.com", "妈妈", "落地家", child="果果")
    today = shanghai_today().isoformat()
    created = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert created.status_code == 201
    photo_id = created.json()["id"]
    page = client.get(f"/children/{child_id}/days/{today}?new={photo_id}&toast=placed_today")
    assert page.status_code == 200
    assert UPLOAD_DAY_SLIM["done_toast"] in page.text
    assert f'data-photo-id="{photo_id}"' in page.text
    assert "stamp new" in page.text
    assert "刚放进" not in page.text
    assert "共 1 张" in page.text
    assert 'id="day-lb"' in page.text
    assert UPLOAD_DAY_SLIM["swipe"] in page.text
    posted = client.post(
        "/upload?kind=child",
        data={"kind": "child", "child_id": str(child_id), "taken_on": "2026-10-02"},
        files={"file": ("b.jpg", JPEG, "image/jpeg")},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    assert f"/children/{child_id}/days/2026-10-02?" in posted.headers["location"]
    assert "toast=placed_day" in posted.headers["location"]
    assert "new=" in posted.headers["location"]


def test_self_upload_lands_on_self_day_grid(client: TestClient):
    child_id = _family(client, "uds-self@example.com", "妈妈", "本人家", child="果果")
    today = shanghai_today().isoformat()
    created = upload(client, kind="self", taken_on=today, send_child=False)
    assert created.status_code == 201
    photo_id = created.json()["id"]
    me = client.get("/api/me").json()["id"]
    day = client.get(f"/people/{me}/album/days/{today}?new={photo_id}&toast=placed_today")
    assert day.status_code == 200
    assert "家人相册 · 妈妈" in day.text
    assert "共 1 张" in day.text
    assert "stamp new" in day.text
    assert UPLOAD_DAY_SLIM["done_toast"] in day.text
    child_day = client.get(f"/children/{child_id}/days/{today}")
    assert f'data-photo-id="{photo_id}"' not in child_day.text
    album = client.get(f"/people/{me}/album")
    assert album.status_code == 200
    assert 'class="grid3"' in album.text
    _require_node()
    completed = _node(
        f"const url=api.landingUrl('self','{child_id}','{me}','{today}',[{photo_id}],'{today}');"
        f"if (url!=='/people/{me}/album/days/{today}?new={photo_id}&toast=placed_today') process.exit(1);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_day_grid_same_template_no_plusn(client: TestClient):
    child_id = _family(client, "uds-grid@example.com", "妈妈", "齐格家", child="果果")
    today = shanghai_today().isoformat()
    for count in (1, 2, 8):
        fresh = TestClient(app)
        cid = _family(fresh, f"uds-grid-{count}@example.com", "妈妈", f"齐格{count}", child="果果")
        for index in range(count):
            assert upload(fresh, kind="child", child_id=cid, taken_on=today, filename=f"{index}.jpg").status_code == 201
        page = fresh.get(f"/children/{cid}/days/{today}").text
        assert page.count("data-photo-id=") == count
        assert "qige-grid" in page
        assert 'id="qige-skel"' in page
        assert 'class="stamp sk"' in page
        assert f"共 {count} 张" in page
        assert "pics n1" not in page and "pics n2" not in page and "pics n3" not in page
        assert "photo-stack" not in page
        assert 'class="grid3"' not in page
        assert "刚放进" not in page
        assert 'id="day-lb"' in page
        assert 'id="lb-prev"' in page and 'id="lb-next"' in page
    assert child_id
    source = DAY_JS.read_text(encoding="utf-8")
    assert "touchend" in source
    completed = subprocess.run(
        ["node", "-e", f"const api=require({str(DAY_JS.resolve())!r});if(api.stepIndex(0,8,-1)!==0||api.stepIndex(1,8,1)!==2)process.exit(1);"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 127:
        pytest.skip("node not installed")
    assert completed.returncode == 0, completed.stderr


def test_timeline_day_card_row_not_stack(client: TestClient):
    child_id = _family(client, "uds-row@example.com", "妈妈", "排卡家", child="果果")
    today = shanghai_today().isoformat()
    for index in range(6):
        assert upload(client, kind="child", child_id=child_id, taken_on=today, filename=f"{index}.jpg").status_code == 201
    page = client.get(f"/?child={child_id}")
    assert page.text.count('class="daycard"') == 1
    assert page.text.count("data-photo-id=") == 4
    assert "共 6 张" in page.text
    assert "妈妈传" in page.text
    assert "photo-stack" not in page.text
    assert 'class="pcard"' not in page.text
    assert 'class="pics' not in page.text
    assert f'href="/children/{child_id}/days/{today}"' in page.text
    assert "+N" not in page.text


def test_migrate_upload_day_slim_noop(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    assert migrate_schema(engine) in {True, False}
    with engine.connect() as conn:
        before = [row[0] for row in conn.execute(text("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY sql"))]
    names = set(inspect(engine).get_table_names())
    migrate_schema(engine)
    with engine.connect() as conn:
        after = [row[0] for row in conn.execute(text("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY sql"))]
    assert before == after
    assert names == set(inspect(engine).get_table_names())
    assert not any("upload_day" in name for name in names)
    digest = hashlib.sha256("\n".join(before).encode("utf-8")).hexdigest()
    assert digest == hashlib.sha256("\n".join(after).encode("utf-8")).hexdigest()
    script = Path("scripts/migrate.py").read_text(encoding="utf-8")
    source = Path("app/migrate_schema.py").read_text(encoding="utf-8")
    assert "upload_day_slim ready (no new tables)" in script
    assert "upload_confirm_slim ready (no new tables)" in script
    assert "v2026.11.14.1 does not add a table or a column" in source
    assert "v2026.11.14.2 does not add a table or a column" in source
    assert "DROP TABLE" not in script
    assert "docker compose down -v" not in script
    assert app.version == "2026.11.14.2"


def test_four_roles_walk_upload_day(client: TestClient):
    child_id = _family(client, "uds-role-a@example.com", "管理员", "走查家", child="果果")
    uploader = _invite(client, "uds-role-u@example.com", "uploader", "叔叔")
    viewer = _invite(client, "uds-role-v@example.com", "viewer", "奶奶")
    outsider = TestClient(app)
    auth(outsider, "uds-role-o@example.com", "外人")
    today = shanghai_today().isoformat()
    admin_photo = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert admin_photo.status_code == 201
    up_photo = upload(uploader, kind="self", taken_on=today, send_child=False)
    assert up_photo.status_code == 201
    assert viewer.post(
        "/api/photos",
        data={"kind": "child", "child_id": str(child_id), "taken_on": today},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    ).status_code == 403
    assert outsider.get(f"/children/{child_id}/days/{today}").status_code == 403
    me = uploader.get("/api/me").json()["id"]
    seen = viewer.get(f"/people/{me}/album/days/{today}")
    assert seen.status_code == 200
    assert 'class="fab"' not in seen.text
    assert "共 1 张" in seen.text
    home = viewer.get("/")
    assert 'class="daycard"' in home.text or "还没有" in home.text
    confirm = uploader.get("/upload?kind=self", follow_redirects=False)
    assert confirm.status_code == 302
    album = uploader.get(confirm.headers["location"])
    assert album.status_code == 200
    assert UPLOAD_DAY_SLIM["self_note"] in album.text
    assert UPLOAD_DAY_SLIM["submit"] in album.text
    assert UPLOAD_DAY_SLIM["submit_self"] not in album.text
    assert "照片放到哪里" not in album.text
