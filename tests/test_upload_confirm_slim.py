"""Upload confirm slip: stay on the page, torn paper, no auto-upload."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.constants import UPLOAD_DAY_SLIM
from app.main import app
from tests.test_acceptance import JPEG, auth, upload
from tests.test_journal import _family, _invite

JS = Path("app/static/upload_day_slim.js")
SLIP = Path("app/templates/fragments/upload_slip.html")
CSS = Path("app/static/app.css")
BANNED = ("会员", "开通", "商城", "发现", "广告")
JARGON = ("压缩率", "画质", "20MB", "KB", "MB")
SURFACES = (
    SLIP,
    Path("app/templates/timeline.html"),
    Path("app/templates/album.html"),
    Path("app/templates/fragments/stage_inner.html"),
    Path("app/static/upload_day_slim.js"),
)
OLD_SCREEN = ("confirm_title", "pick_n", "submit_self", "self_banner", "strip_label", "strip_hint_pre")


def _node(body: str) -> subprocess.CompletedProcess[str]:
    script = f"const api=require({str(JS.resolve())!r});{body}"
    return subprocess.run(["node", "-e", script], check=False, capture_output=True, text=True)


def _require_node() -> None:
    if subprocess.run(["node", "-v"], check=False, capture_output=True).returncode != 0:
        pytest.skip("node not installed")


def _slip_css() -> str:
    css = CSS.read_text(encoding="utf-8")
    return css.split("/* upload-confirm-slip", 1)[1].split("/* /upload-confirm-slip */", 1)[0]


def test_slip_dismiss_no_upload():
    _require_node()
    completed = _node(
        "if (api.dismissUploads()!==false) process.exit(1);"
        "if (api.selectStartsUpload()!==true && api.selectStartsUpload()!==false) process.exit(2);"
        "if (api.dismissUploads()) process.exit(3);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    source = JS.read_text(encoding="utf-8")
    abandon = source.split("function abandon", 1)[1].split("function toggleChild", 1)[0]
    assert "uploadAll" not in abandon
    assert "XMLHttpRequest" not in abandon
    assert "/api/photos" not in abandon


def test_select_does_not_auto_upload():
    _require_node()
    completed = _node("if (api.selectStartsUpload()) process.exit(1);")
    assert completed.returncode == 0, completed.stderr or completed.stdout
    source = JS.read_text(encoding="utf-8")
    change = source.split('fileInput.addEventListener("change"', 1)[1].split("});", 1)[0]
    assert "uploadAll" not in change
    assert "openWithFiles" in change
    assert "/api/photos" not in change


def test_slip_defaults_today_and_last_child():
    _require_node()
    completed = _node(
        "const kids=[{id:1,name:'果果'},{id:2,name:'豆豆'}];"
        "const store={m:{},getItem(k){return Object.prototype.hasOwnProperty.call(this.m,k)?this.m[k]:null},"
        "setItem(k,v){this.m[k]=String(v);}};"
        "if (api.lastTaggedKey(9)!=='fa.last_tagged_child.9') process.exit(1);"
        "if (!api.writeLastTagged(store,9,'2')) process.exit(2);"
        "if (api.readLastTagged(store,9)!=='2') process.exit(3);"
        "const fromA=api.resolveDefaultChild({children:kids,pref:api.readLastTagged(store,9),viewedId:'1',singleView:true});"
        "if (fromA!=='2') process.exit(4);"
        "if (api.resolveDefaultChild({children:kids,pref:'',viewedId:'1',singleView:true})!=='1') process.exit(5);"
        "if (api.resolveDefaultChild({children:kids,pref:'99',viewedId:'1',singleView:false})!=='1') process.exit(6);"
        "if (api.resolveDefaultChild({children:kids,pref:'99',viewedId:'2',singleView:true})!=='2') process.exit(7);"
        "const today='2026-11-14';"
        "if (api.chipLabel({today:today,takenOn:today,name:'果果',solo:false,self:false})!=='今天 · 果果') process.exit(8);"
        "if (api.chipLabel({today:today,takenOn:today,name:'果果',solo:true,self:false})!=='今天') process.exit(9);"
        "if (api.chipLabel({today:today,takenOn:'2026-11-10',name:'果果',solo:false,self:false})!=='11月10日 · 果果') process.exit(10);"
        "if (api.chipLabel({today:today,takenOn:today,name:'妈妈',solo:false,self:true})!=='今天') process.exit(11);"
        "if (api.chipLabel({today:today,takenOn:'2026-11-10',name:'',solo:false,self:true})!=='11月10日') process.exit(12);"
        "if (api.submitKey(today,today)!=='submit') process.exit(13);"
        "if (api.submitKey(today,'2026-11-10')!=='submit_other') process.exit(14);"
        "const days=api.recentDays(today,2);"
        "if (days.join(',')!=='2026-11-14,2026-11-13,2026-11-12') process.exit(15);"
        "if (api.monthDay('2026-11-03')!=='11月3日') process.exit(16);"
        "const fields=api.requestFieldNames('child');"
        "if (fields.indexOf('child_ids')<0) process.exit(17);"
        "if (fields.join(',').indexOf('last_tagged')>=0) process.exit(18);"
        "if (api.requestFieldNames('self').indexOf('child_ids')>=0) process.exit(19);"
        "if (api.submitBlocked({kind:'child',selected:[],over:false,ready:true,batch:false})!==true) process.exit(20);"
        "if (api.submitBlocked({kind:'child',selected:['1'],over:true,ready:true,batch:false})!==true) process.exit(21);"
        "if (api.submitBlocked({kind:'self',selected:[],over:false,ready:true,batch:false})!==false) process.exit(22);"
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    source = JS.read_text(encoding="utf-8")
    assert "localStorage" in source
    assert "document.cookie" not in source
    post = source.split("function postOne", 1)[1].split("function uploadAll", 1)[0]
    assert "last_tagged" not in post
    assert "localStorage" not in post
    assert 'form.append("child_ids"' in post


def test_confirm_stays_on_page_slip(client: TestClient):
    child_id = _family(client, "ucs-stay@example.com", "妈妈", "原处家", child="果果")
    _invite_unused = client.post("/api/children", json={"name": "豆豆", "birth_date": "2025-03-01"})
    assert _invite_unused.status_code == 201
    page = client.get(f"/?child={child_id}")
    assert page.status_code == 200
    assert 'id="upload-slip"' in page.text
    assert 'class="tear"' in page.text
    assert 'id="photo-file"' in page.text
    assert 'accept="image/jpeg,image/png,image/webp"' in page.text
    assert "multiple" in page.text.split('id="photo-file"', 1)[1].split(">", 1)[0]
    assert "data-slip-pick" in page.text
    assert UPLOAD_DAY_SLIM["submit"] in page.text
    assert "哪一天" in page.text
    assert "放到这一天" not in page.text
    assert "已选" not in page.text
    assert 'id="pick-row"' not in page.text
    assert 'id="film-strip"' not in page.text
    assert 'href="/upload?kind=child' not in page.text
    gone = client.get(f"/upload?kind=child&child={child_id}", follow_redirects=False)
    assert gone.status_code == 302


def test_solo_child_no_who_row(client: TestClient):
    child_id = _family(client, "ucs-solo@example.com", "妈妈", "独孩家", child="果果")
    page = client.get(f"/?child={child_id}")
    assert 'data-solo="1"' in page.text
    assert "再贴人" not in page.text
    assert 'id="slip-faces"' not in page.text
    chip = page.text.split('id="slip-chip-lab"', 1)[1].split("</span>", 1)[0]
    assert chip.endswith("今天")
    assert "·" not in chip
    assert ">放入<" in page.text


def test_self_slip_no_faces(client: TestClient):
    _family(client, "ucs-self@example.com", "妈妈", "家人家", child="果果")
    client.post("/api/children", json={"name": "豆豆", "birth_date": "2025-03-01"})
    me = client.get("/api/me").json()["id"]
    page = client.get(f"/people/{me}/album")
    assert page.status_code == 200
    assert 'id="slip-self-note"' in page.text
    assert UPLOAD_DAY_SLIM["self_note"] in page.text
    assert 'id="slip-faces"' not in page.text
    assert "再贴人" not in page.text
    assert 'class="go family' in page.text
    assert ">放入<" in page.text
    assert "放到我的相册" not in page.text
    chip = page.text.split('id="slip-chip-lab"', 1)[1].split("</span>", 1)[0]
    assert chip.endswith("今天")
    assert "记在我名下" not in chip
    assert "data-slip-pick" in page.text
    assert 'href="/upload?kind=self"' not in page.text


def test_lift_uses_paper_faces_not_film(client: TestClient):
    child_id = _family(client, "ucs-face@example.com", "妈妈", "小脸家", child="果果")
    client.post("/api/children", json={"name": "豆豆", "birth_date": "2025-03-01"})
    page = client.get(f"/?child={child_id}")
    assert 'class="faces"' in page.text
    assert 'class="facep"' in page.text
    assert UPLOAD_DAY_SLIM["lab_who"] in page.text
    slip = page.text.split('id="upload-slip"', 1)[1].split('id="progress-dock"', 1)[0]
    assert 'id="film-strip"' not in slip
    assert "teeth" not in slip
    assert "sprocket" not in slip
    assert 'class="stub"' not in slip
    css = _slip_css()
    assert "film-dark" not in css
    assert "--film" not in css
    assert "border-radius: 12px" in css


def test_pick_day_uses_native_date(client: TestClient):
    child_id = _family(client, "ucs-day@example.com", "妈妈", "选天家", child="果果")
    page = client.get(f"/?child={child_id}")
    slip = page.text.split('id="upload-dock"', 1)[1].split('id="slip-children"', 1)[0]
    assert 'id="slip-native-date"' in slip
    assert 'type="date"' in slip
    assert UPLOAD_DAY_SLIM["chip_pick"] in slip
    assert "calendar" not in slip.lower()
    source = JS.read_text(encoding="utf-8")
    assert "showPicker" in source
    picker = source.split("function openNativeDate", 1)[1].split("var faces", 1)[0]
    assert "showPicker" in picker
    assert "location" not in picker


def test_upload_kind_redirects_away_from_form(client: TestClient):
    child_id = _family(client, "ucs-302@example.com", "妈妈", "跳回家", child="果果")
    child = client.get(f"/upload?kind=child&child={child_id}", follow_redirects=False)
    assert child.status_code == 302
    assert child.headers["location"] == f"/?child={child_id}"
    assert "uds" not in child.text
    assert 'id="pick-row"' not in child.text
    bare = client.get("/upload?kind=child", follow_redirects=False)
    assert bare.status_code == 302
    assert bare.headers["location"] == "/"
    me = client.get("/api/me").json()["id"]
    self_page = client.get("/upload?kind=self", follow_redirects=False)
    assert self_page.status_code == 302
    assert self_page.headers["location"] == f"/people/{me}/album"
    stranger = client.get("/upload?kind=child&child=999999", follow_redirects=False)
    assert stranger.status_code == 302
    assert stranger.headers["location"] == "/"


def test_edit_and_kindless_upload_unchanged(client: TestClient):
    child_id = _family(client, "ucs-edit@example.com", "妈妈", "编辑家", child="果果")
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-10-22")
    assert created.status_code == 201
    edit = client.get(f"/photos/{created.json()['id']}/edit")
    assert edit.status_code == 200
    assert 'id="film-strip"' in edit.text
    assert 'id="photo-app"' in edit.text
    assert "/static/photo.js" in edit.text
    assert "改这张照片" in edit.text
    assert 'class="slip"' not in edit.text
    kindless = client.get("/upload")
    assert kindless.status_code == 200
    assert "照片放到哪里？" in kindless.text
    assert 'id="photo-entry"' in kindless.text
    assert 'id="legacy-import-entry"' in kindless.text
    assert 'id="upload-slip"' not in kindless.text
    assert 'class="uds"' not in kindless.text


def test_slip_has_no_sprocket():
    slip = SLIP.read_text(encoding="utf-8")
    decision = slip.split('id="upload-slip"', 1)[1].split('id="progress-dock"', 1)[0]
    assert "stub" not in decision
    assert "sprocket" not in decision
    assert "teeth" not in decision
    assert 'class="tear"' in decision
    progress = slip.split('id="progress-dock"', 1)[1]
    assert 'class="stub"' in progress
    css = _slip_css()
    assert ".stub" not in css
    assert "radial-gradient" in css
    assert "circle at 8px 0" in css


def test_copy_forbidden_words_upload_confirm_slim():
    blob = "\n".join(path.read_text(encoding="utf-8") for path in SURFACES)
    for word in BANNED:
        assert word not in blob
    for word in JARGON:
        assert word not in blob
    assert re.search(r"(?i)(?<![A-Za-z])(kb|mb)(?![A-Za-z])", blob) is None
    slip = SLIP.read_text(encoding="utf-8")
    for key in OLD_SCREEN:
        assert key not in slip
    assert "放到这一天" not in slip
    assert "放到我的相册" not in slip
    assert "已选" not in slip
    assert "贴上兄弟姐妹" not in slip
    assert UPLOAD_DAY_SLIM["submit"] == "放入"
    assert ">放入<" in slip or "{{ uds.submit }}" in slip
    assert "50" not in UPLOAD_DAY_SLIM["err_batch"]
    rendered_keys = (
        "submit",
        "submit_other",
        "n_zhang",
        "chip_today",
        "chip_who",
        "chip_pick",
        "lab_day",
        "lab_who",
        "self_note",
        "dismiss",
        "progress",
        "fail_one",
        "fail_go",
        "err_size",
        "err_bad",
        "err_batch",
    )
    for key in rendered_keys:
        for word in BANNED + JARGON:
            assert word not in UPLOAD_DAY_SLIM[key]


def test_viewer_has_no_slip(client: TestClient):
    child_id = _family(client, "ucs-ro@example.com", "妈妈", "只读底签", child="果果")
    created = upload(client, kind="child", child_id=child_id, taken_on="2026-11-02")
    assert created.status_code == 201
    viewer = _invite(client, "ucs-ro-v@example.com", "viewer", "外婆")
    home = viewer.get(f"/?child={child_id}")
    assert 'id="upload-slip"' not in home.text
    assert "data-slip-pick" not in home.text
    assert 'class="fab"' not in home.text
    assert 'class="daycard"' in home.text
    denied = viewer.get("/upload?kind=child", follow_redirects=False)
    assert denied.status_code == 403
    outsider = TestClient(app)
    auth(outsider, "ucs-out@example.com", "路人")
    blocked = outsider.get(f"/upload?kind=child&child={child_id}")
    assert blocked.status_code == 403
    assert "只读底签" not in blocked.text


def test_empty_state_picks_in_place(client: TestClient):
    _family(client, "ucs-empty@example.com", "妈妈", "空态家", child="果果")
    page = client.get("/?view=family")
    assert "放入第一张" in page.text
    assert "data-slip-pick" in page.text
    assert 'href="/upload' not in page.text
    assert JPEG
