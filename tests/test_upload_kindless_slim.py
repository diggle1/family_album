"""Kindless /upload is an in-place pick card plus the shipped slip. No new tables."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.constants import LEGACY_IMPORT, UPLOAD_DAY_SLIM
from app.main import app
from app.migrate_schema import migrate_schema
from app.services.clock import shanghai_today
from tests.test_acceptance import JPEG, auth, upload
from tests.test_journal import _family, _invite

JS = Path("app/static/upload_day_slim.js")
CSS = Path("app/static/app.css")
BANNED = ("会员", "开通", "商城", "发现", "广告")
JARGON = ("压缩率", "画质", "20MB", "KB", "MB")
ABBREV = re.compile(r"(?i)(?<![A-Za-z])(kb|mb)(?![A-Za-z])")
DROPPED = ("这些图还在，马上就能放", "把照片放这里", "选几张放到今天", "会挂在果果名下", "照片放到哪里")
SURFACES = (
    Path("app/upload_day_slim_copy.py"),
    Path("app/static/upload_day_slim.js"),
    Path("app/templates/upload.html"),
    Path("app/templates/album.html"),
    Path("app/templates/fragments/stage_inner.html"),
    Path("app/templates/fragments/upload_slip.html"),
    Path("app/static/app.css"),
)


def _node(body: str) -> subprocess.CompletedProcess[str]:
    script = f"const api=require({str(JS.resolve())!r});{body}"
    return subprocess.run(["node", "-e", script], check=False, capture_output=True, text=True)


def _require_node() -> None:
    if subprocess.run(["node", "-v"], check=False, capture_output=True).returncode != 0:
        pytest.skip("node not installed")


def _button(html: str, element_id: str) -> str:
    return html.split(f'id="{element_id}"', 1)[1].split("</button>", 1)[0]


def _tag(html: str, element_id: str) -> str:
    at = html.find(f'id="{element_id}"')
    start = html.rfind("<", 0, at)
    end = html.find(">", at)
    return html[start : end + 1]


def test_kindless_upload_no_choice_page(client: TestClient):
    _family(client, "uks-f1@example.com", "妈妈", "无三选家", child="果果")
    page = client.get("/upload")
    assert page.status_code == 200
    assert "照片放到哪里" not in page.text
    assert 'id="photo-entry"' not in page.text
    assert 'id="legacy-import-entry"' not in page.text
    assert "孩子照片" not in page.text
    assert "我的照片" not in page.text
    assert "选好后再选图" not in page.text
    child = client.get("/upload?child=1")
    assert child.status_code == 200
    assert 'id="photo-entry"' not in child.text
    assert "照片放到哪里" not in child.text


def test_kindless_empty_shows_pick_card(client: TestClient):
    _family(client, "uks-f2@example.com", "妈妈", "选图卡家", child="果果")
    page = client.get("/upload")
    assert page.status_code == 200
    assert page.url.path == "/upload" or page.request.url.path == "/upload"
    card = page.text.split('id="pick-card"', 1)[1].split('id="kept-grid"', 1)[0]
    assert UPLOAD_DAY_SLIM["pick_title"] in card
    assert UPLOAD_DAY_SLIM["pick_sub"] in card
    assert UPLOAD_DAY_SLIM["pick_cta"] in card
    cta = _tag(page.text, "pick-cta")
    assert "btn" in cta and "ghost" in cta
    assert "data-slip-pick" in cta
    assert 'id="kept-grid" hidden' in page.text or 'id="kept-grid"' in page.text
    assert "hidden" in page.text.split('id="kept-grid"', 1)[0][-40:] or 'id="kept-grid" hidden' in page.text
    assert 'id="upload-slip"' in page.text
    assert 'data-kind="child"' in page.text
    assert "把照片放这里" not in page.text
    file_tag = _tag(page.text, "photo-file")
    assert 'accept="image/jpeg,image/png,image/webp"' in file_tag
    assert "multiple" in file_tag


def test_kindless_select_shows_slip_no_auto():
    _require_node()
    completed = _node(
        "if (api.selectStartsUpload()) process.exit(1);"
        "const open=api.kindlessSurface({dockOpen:true,count:3});"
        "if (!open.pickHidden||open.keptHidden||open.thumbs!==3||open.leaveUpload) process.exit(2);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    source = JS.read_text(encoding="utf-8")
    change = source.split('fileInput.addEventListener("change"', 1)[1].split("});", 1)[0]
    assert "openWithFiles" in change
    assert "uploadAll" not in change
    assert "/api/photos" not in change
    opened = source.split("function openWithFiles", 1)[1].split("function paintProgress", 1)[0]
    assert "showDecision" in opened
    assert "location" not in opened
    assert "/api/photos" not in opened
    assert "indexedDB" not in source
    assert "sessionStorage" not in source


def test_kindless_keeps_files_in_session():
    _require_node()
    completed = _node(
        "const stay=api.kindlessSurface({dockOpen:true,count:4});"
        "if (!stay.pickHidden||stay.keptHidden||stay.thumbs!==4||stay.leaveUpload||stay.choicePage) process.exit(1);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    source = JS.read_text(encoding="utf-8")
    days = source.split('days.addEventListener("click"', 1)[1].split("var chip", 1)[0]
    chip = source.split('chip.addEventListener("click"', 1)[1].split("var nativeDate", 1)[0]
    assert "state.items = []" not in days
    assert "location" not in days
    assert "state.items = []" not in chip
    assert "location" not in chip
    assert 'state.takenOn = node.getAttribute("data-iso")' in days
    assert "paintKindless" in source.split("function paintSlip", 1)[1].split("function applyDefaults", 1)[0]


def test_kindless_dismiss_back_to_pick():
    _require_node()
    completed = _node(
        "if (api.dismissUploads()) process.exit(1);"
        "const back=api.kindlessSurface({dockOpen:false,count:0});"
        "if (back.pickHidden||!back.keptHidden||back.thumbs||back.leaveUpload) process.exit(2);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    source = JS.read_text(encoding="utf-8")
    abandon = source.split("function abandon", 1)[1].split("function toggleChild", 1)[0]
    assert "uploadAll" not in abandon
    assert "/api/photos" not in abandon
    assert "location" not in abandon
    assert "state.items = []" in abandon
    assert "dock.hidden = true" in abandon
    assert "paintKindless" in abandon
    assert UPLOAD_DAY_SLIM["dismiss"] == "拿掉这些"


def test_kindless_refresh_no_choice_page(client: TestClient):
    _family(client, "uks-f6@example.com", "妈妈", "刷新家", child="果果")
    first = client.get("/upload")
    second = client.get("/upload")
    for page in (first, second):
        assert page.status_code == 200
        assert "照片放到哪里" not in page.text
        assert 'id="photo-entry"' not in page.text
        assert 'id="pick-card"' in page.text
        assert 'id="kept-grid" hidden' in page.text
        dock = _tag(page.text, "upload-dock")
        assert "hidden" in dock
    assert "indexedDB" not in JS.read_text(encoding="utf-8")
    assert "这些图还在" not in first.text


def test_empty_cta_goes_to_kindless_upload(client: TestClient):
    child_id = _family(client, "uks-f7@example.com", "妈妈", "空态家", child="果果")
    page = client.get("/?view=family")
    assert page.status_code == 200
    block = page.text.split('class="center compact"', 1)[1].split('class="pad-bottom"', 1)[0]
    assert UPLOAD_DAY_SLIM["empty_tl"] in block
    assert UPLOAD_DAY_SLIM["empty_cta"] == "去上传"
    assert "去上传" in block
    assert "放入第一张" not in block
    assert "data-slip-pick" not in block
    at = block.find('class="go-upload"')
    anchor = block[block.rfind("<a", 0, at) : block.find(">", at) + 1]
    assert 'href="/upload"' in anchor
    assert "btn" not in anchor
    assert "data-slip-pick" not in anchor
    viewer = _invite(client, "uks-f7-v@example.com", "viewer", "外婆")
    home = viewer.get("/?view=family")
    assert 'class="go-upload"' not in home.text
    assert "去上传" not in home.text
    assert 'href="/upload' not in home.text
    assert 'class="fab"' not in home.text
    filled = upload(client, kind="child", child_id=child_id, taken_on=shanghai_today().isoformat())
    assert filled.status_code == 201
    lived = client.get(f"/?child={child_id}")
    assert 'class="go-upload"' not in lived.text
    assert "data-slip-pick" in _tag(lived.text, "fab-photo")


def test_self_entry_not_on_kindless(client: TestClient):
    _family(client, "uks-f8@example.com", "妈妈", "家人入口家", child="果果")
    kindless = client.get("/upload")
    assert UPLOAD_DAY_SLIM["self_note"] not in kindless.text
    assert "记在我名下" not in kindless.text
    assert 'id="slip-self-note"' not in kindless.text
    assert 'data-kind="child"' in kindless.text
    me = client.get("/api/me").json()["id"]
    album = client.get(f"/people/{me}/album")
    assert UPLOAD_DAY_SLIM["self_note"] in album.text
    assert 'id="slip-self-note"' in album.text
    assert "放入第一张" in album.text
    assert "data-slip-pick" in album.text


def test_legacy_stays_on_settings(client: TestClient):
    _family(client, "uks-f9@example.com", "妈妈", "旧库家", child="果果")
    settings = client.get("/family/settings")
    assert settings.status_code == 200
    assert 'id="legacy-import-entry"' in settings.text
    assert LEGACY_IMPORT["cta"] in settings.text
    assert LEGACY_IMPORT["entry_sub"] in settings.text
    assert 'id="access-url-form"' in settings.text
    assert 'href="/family/members"' in settings.text
    kindless = client.get("/upload")
    assert 'id="legacy-import-entry"' not in kindless.text
    assert "导入旧照片" not in kindless.text
    assert "从本机一次选以前的照片" not in kindless.text


def test_edit_mode_unchanged_on_kindless_knife(client: TestClient):
    child_id = _family(client, "uks-f10@example.com", "妈妈", "编辑仍在家", child="果果")
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert created.status_code == 201
    edit = client.get(f"/photos/{created.json()['id']}/edit")
    assert edit.status_code == 200
    assert "改这张照片" in edit.text
    assert "保存修改" in edit.text
    assert "/static/photo.js" in edit.text
    assert 'id="photo-app"' in edit.text
    assert 'id="film-strip"' in edit.text
    assert 'id="pick-card"' not in edit.text
    assert 'id="upload-slip"' not in edit.text
    assert 'class="slip"' not in edit.text


def test_kindless_defaults_today_child_and_solo(client: TestClient):
    _require_node()
    solo_id = _family(client, "uks-f11@example.com", "妈妈", "独孩深链家", child="果果")
    solo = client.get("/upload")
    assert 'data-solo="1"' in solo.text
    assert 'data-kind="child"' in solo.text
    assert f'data-uid="' in solo.text
    assert 'data-today="' in solo.text
    assert "再贴人" not in solo.text
    assert 'id="slip-faces"' not in solo.text
    chip = solo.text.split('id="slip-chip-lab"', 1)[1].split("</span>", 1)[0]
    assert chip.endswith("今天")
    assert "·" not in chip
    button = _button(solo.text, "photo-submit")
    assert "放入" in button
    assert "放进这一天" not in button
    assert 'data-copy-submit="放入"' in solo.text
    assert 'data-copy-submit_other="放进这一天"' in solo.text
    client.post("/api/children", json={"name": "豆豆", "birth_date": "2025-03-01"})
    many = client.get("/upload")
    assert "再贴人" in many.text
    assert 'id="slip-faces"' in many.text
    assert "果果" in many.text and "豆豆" in many.text
    completed = _node(
        "const today='2026-11-14';"
        "if (api.submitKey(today,today)!=='submit') process.exit(1);"
        "if (api.submitKey(today,'2026-11-10')!=='submit_other') process.exit(2);"
        "if (api.selectStartsUpload()) process.exit(3);"
        "const kids=[{id:1,name:'果果'}];"
        "if (api.chipLabel({today:today,takenOn:today,name:'果果',solo:true,self:false})!=='今天') process.exit(4);"
        "if (api.lastTaggedKey(7)!=='fa.last_tagged_child.7') process.exit(5);"
        "if (api.resolveDefaultChild({children:kids,pref:'',viewedId:'',singleView:true})!=='1') process.exit(6);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert solo_id


def test_people_fab_lands_on_child_pick(client: TestClient):
    _family(client, "uks-people@example.com", "妈妈", "家人列表家", child="果果")
    client.post("/api/children", json={"name": "豆豆", "birth_date": "2025-03-01"})
    people = client.get("/people")
    assert people.status_code == 200
    fab = _tag(people.text, "fab")
    assert 'href="/upload"' in fab
    assert "kind=" not in fab
    assert "data-slip-pick" not in fab
    dest = client.get("/upload")
    assert dest.status_code == 200
    assert 'id="pick-card"' in dest.text
    assert 'data-kind="child"' in dest.text
    assert UPLOAD_DAY_SLIM["pick_title"] in dest.text
    assert "记在我名下" not in dest.text
    assert 'id="slip-self-note"' not in dest.text
    assert 'data-copy-submit="放入"' in dest.text
    assert "fa.last_tagged_child." in JS.read_text(encoding="utf-8")


def test_kindless_success_lands_on_day_grid(client: TestClient):
    _require_node()
    child_id = _family(client, "uks-done@example.com", "妈妈", "落地家", child="果果")
    start = client.get("/upload")
    assert 'id="pick-card"' in start.text
    assert 'data-kind="child"' in start.text
    today = shanghai_today().isoformat()
    created = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert created.status_code == 201
    photo_id = created.json()["id"]
    day = client.get(f"/children/{child_id}/days/{today}?new={photo_id}&toast=placed_today")
    assert day.status_code == 200
    assert f'data-photo-id="{photo_id}"' in day.text
    assert 'class="stamp' in day.text
    assert "已放进今天" in day.text
    assert 'id="pick-card"' not in day.text
    completed = _node(
        f"const today={today!r};"
        "const url=api.landingUrl('child','4','9',today,['8'],today);"
        "if (url.indexOf('/children/4/days/'+today)!==0) process.exit(1);"
        "if (url.indexOf('/upload')>=0) process.exit(2);"
        "if (url.indexOf('toast=placed_today')<0) process.exit(3);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    source = JS.read_text(encoding="utf-8")
    settled = source.split("function showSettled", 1)[1].split("function abandon", 1)[0]
    assert "location.href = destination" in settled


def test_kindless_readonly_and_outsider(client: TestClient):
    child_id = _family(client, "uks-ro@example.com", "妈妈", "深链外露家", child="柚柚")
    today = shanghai_today().isoformat()
    created = upload(client, kind="child", child_id=child_id, taken_on=today)
    assert created.status_code == 201
    viewer = _invite(client, "uks-ro-v@example.com", "viewer", "奶奶")
    denied = viewer.get("/upload")
    assert denied.status_code == 403
    assert "没有访问权限" in denied.text
    assert "深链外露家" not in denied.text
    assert "柚柚" not in denied.text
    assert 'id="pick-card"' not in denied.text
    assert 'id="upload-slip"' not in denied.text
    kinded = viewer.get("/upload?kind=child", follow_redirects=False)
    assert kinded.status_code == 403
    home = viewer.get(f"/?child={child_id}")
    assert home.status_code == 200
    assert 'class="fab"' not in home.text
    assert 'class="go-upload"' not in home.text
    assert "data-slip-pick" not in home.text
    assert 'id="upload-slip"' not in home.text
    assert 'class="daycard"' in home.text
    day = viewer.get(f"/children/{child_id}/days/{today}")
    assert day.status_code == 200
    assert 'class="stamp' in day.text
    blocked = viewer.post(
        "/api/photos",
        data={"kind": "child", "child_id": str(child_id), "taken_on": today},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "photo_forbidden_role"
    outsider = TestClient(app)
    auth(outsider, "uks-out@example.com", "路人")
    page = outsider.get("/upload")
    assert page.status_code == 403
    assert "没有访问权限" in page.text
    assert "深链外露家" not in page.text
    assert "柚柚" not in page.text
    payload = outsider.get("/upload", headers={"accept": "application/json"})
    assert payload.status_code == 403
    assert "code" not in payload.json()
    assert "深链外露家" not in payload.text
    assert "柚柚" not in payload.text
    assert outsider.get(f"/children/{child_id}/days/{today}").status_code == 403


def test_kindless_no_pick_ticket_or_loading_line(client: TestClient):
    _family(client, "uks-f35@example.com", "妈妈", "不进笺家", child="果果")
    page = client.get("/upload")
    blob = "\n".join(path.read_text(encoding="utf-8") for path in SURFACES)
    rendered = page.text
    for phrase in DROPPED:
        assert phrase not in rendered
        assert phrase not in blob
    assert "选照片" not in page.text.split('id="pick-card"', 1)[1].split('id="kept-grid"', 1)[0] or UPLOAD_DAY_SLIM["pick_cta"] == "从相册选"


def test_copy_forbidden_words_upload_kindless_slim(client: TestClient):
    _family(client, "uks-copy@example.com", "妈妈", "文案家", child="果果")
    page = client.get("/upload")
    blob = "\n".join(path.read_text(encoding="utf-8") for path in SURFACES)
    product = "".join(UPLOAD_DAY_SLIM.values())
    for word in BANNED:
        assert word not in product
        assert word not in page.text
        assert word not in blob
    for word in JARGON:
        assert word not in product
        assert word not in page.text
    assert ABBREV.search(product) is None
    assert ABBREV.search(page.text) is None
    assert UPLOAD_DAY_SLIM["submit"] == "放入"
    assert UPLOAD_DAY_SLIM["submit_other"] == "放进这一天"
    assert UPLOAD_DAY_SLIM["empty_cta"] == "去上传"
    assert UPLOAD_DAY_SLIM["empty_self"] == "放入第一张"
    assert UPLOAD_DAY_SLIM["pick_title"] == "选几张放进来"
    assert UPLOAD_DAY_SLIM["pick_sub"] == "从本机选照片，一次可以选多张。"
    assert UPLOAD_DAY_SLIM["pick_cta"] == "从相册选"
    assert UPLOAD_DAY_SLIM["self_note"] == "记在我名下，不进孩子时光线"
    for needle in ("微信", "wechat", "miniprogram", "小程序"):
        assert needle.lower() not in Path("app/templates/upload.html").read_text(encoding="utf-8").lower()
        assert needle.lower() not in Path("app/static/upload_day_slim.js").read_text(encoding="utf-8").lower()


def test_kindless_slip_kernel_and_both_widths():
    css = CSS.read_text(encoding="utf-8")
    slip = css.split("/* upload-confirm-slip", 1)[1].split("/* /upload-confirm-slip */", 1)[0]
    rules = slip.split("*/", 1)[1]
    assert "circle at 8px 0" in rules
    assert "sprocket" not in rules
    assert ".pick-card" not in slip
    assert ".go-upload" not in slip
    assert ".kept" not in slip
    fresh = css.split("/* kindless /upload:", 1)[1]
    assert ".pick-card" in fresh
    assert ".go-upload" in fresh
    assert ".kept .kgrid" in fresh
    assert "@media (min-width: 1024px)" in fresh
    assert "min(720px, 100%)" in fresh
    assert "width: min(480px, 100%)" in css


def test_kinded_upload_still_redirects(client: TestClient):
    child_id = _family(client, "uks-302@example.com", "妈妈", "跳回家", child="果果")
    child = client.get(f"/upload?kind=child&child={child_id}", follow_redirects=False)
    assert child.status_code == 302
    assert child.headers["location"] == f"/?child={child_id}"
    me = client.get("/api/me").json()["id"]
    self_page = client.get("/upload?kind=self", follow_redirects=False)
    assert self_page.status_code == 302
    assert self_page.headers["location"] == f"/people/{me}/album"


def test_uploader_kindless_ok(client: TestClient):
    child_id = _family(client, "uks-up@example.com", "妈妈", "可上传家", child="果果")
    uploader = _invite(client, "uks-up-u@example.com", "uploader", "叔叔")
    page = uploader.get("/upload")
    assert page.status_code == 200
    assert 'id="pick-card"' in page.text
    assert 'data-kind="child"' in page.text
    assert "记在我名下" not in page.text
    assert child_id


def test_migrate_upload_kindless_slim_noop(tmp_path: Path):
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
    assert "upload_kindless" not in names
    digest = hashlib.sha256("\n".join(before).encode("utf-8")).hexdigest()
    assert digest == hashlib.sha256("\n".join(after).encode("utf-8")).hexdigest()
    script = Path("scripts/migrate.py").read_text(encoding="utf-8")
    source = Path("app/migrate_schema.py").read_text(encoding="utf-8")
    assert "upload_day_slim ready (no new tables)" in script
    assert "upload_confirm_slim ready (no new tables)" in script
    assert "upload_kindless_slim ready (no new tables)" in script
    assert "v2026.11.15.1 does not add a table or a column" in source
    assert "DROP TABLE" not in script
    assert "docker compose down -v" not in script
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'script.db'}"
    env["PHOTO_DIR"] = str(tmp_path / "photos")
    env["SECRET_KEY"] = "test-secret"
    env["STORAGE_BACKEND"] = "local"
    runs = [
        subprocess.run(
            [sys.executable, "scripts/migrate.py"],
            cwd=Path.cwd(),
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        for _ in range(2)
    ]
    for completed in runs:
        assert "upload_day_slim ready (no new tables)" in completed.stdout
        assert "upload_confirm_slim ready (no new tables)" in completed.stdout
        assert "upload_kindless_slim ready (no new tables)" in completed.stdout
    assert app.version == "2026.11.15.1"
    notes = Path("releases/v2026.11.15.1.md").read_text(encoding="utf-8")
    assert "v2026.11.14.2" in notes
    assert "零新表" in notes or "不新增表" in notes
    assert "down -v" in notes
    assert "DROP TABLE" not in notes
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.split("## v2026.11.14.2", 1)[0].startswith("# Changelog\n\n## v2026.11.15.1")


def test_kindless_decision_bar_has_no_sprocket(client: TestClient):
    _family(client, "uks-bar@example.com", "妈妈", "无齿孔家", child="果果")
    page = client.get("/upload")
    decision = page.text.split('id="upload-slip"', 1)[1].split('id="progress-dock"', 1)[0]
    assert 'class="tear"' in decision
    assert "stub" not in decision
    assert "sprocket" not in decision
    assert "teeth" not in decision
    progress = page.text.split('id="progress-dock"', 1)[1]
    assert 'class="stub"' in progress
