"""Photo multi-child association: feeds, film strip, export sidecar, and copy."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import threading
import zipfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.constants import PHOTO_MULTI_CHILD
from app.db import SessionLocal
from app.main import app
from app.models import Child, Photo, PhotoChild
from app.services.clock import age_label
from app.storage.errors import StorageError
from tests.test_acceptance import JPEG, auth, upload
from tests.test_journal import _clone, _family, _invite
from tests.test_multi_child import _post as _journal
from tests.test_multi_child import _video

BANNED = (
    "会员",
    "开通",
    "商城",
    "发现",
    "广告",
    "下载 App",
    "应用商店",
    "去商店",
    "更高清",
    "加时长",
    "朋友圈",
    "发动态",
    "发到时光线",
)
MOCKUP = Path("design/family_album/photo-multi-child/final/mockup.html")
FINAL_MD5 = "f700e4de1b74ac0923745d91c63a0e20"
MULTI_MD5 = "2bdab7983095c7e7b81a7c7715b73211"
DAY = "2026-10-22"


def _film(html: str) -> str:
    return html.split('id="film-strip"', 1)[1].split('class="caprow"', 1)[0]


def _tag_href(html: str, element_id: str) -> str:
    for chunk in html.split("<a "):
        tag = chunk.split(">", 1)[0]
        if f'id="{element_id}"' not in tag or 'href="' not in tag:
            continue
        return tag.split('href="', 1)[1].split('"', 1)[0]
    raise AssertionError(element_id)


def _add(client: TestClient, name: str, birth: str) -> int:
    created = client.post("/api/children", json={"name": name, "birth_date": birth})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _photo(client: TestClient, child_ids: list[int], taken_on: str = DAY, **extra):
    data: dict = {"kind": "child", "taken_on": taken_on}
    if child_ids:
        data["child_ids"] = [str(item) for item in child_ids]
    data.update(extra)
    return client.post(
        "/api/photos",
        data=data,
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )


def _names(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.namelist()


def _stored(content: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.read(name)


def _assoc(photo_id: int) -> list[tuple[int, int]]:
    session = SessionLocal()
    try:
        rows = session.scalars(
            select(PhotoChild).where(PhotoChild.photo_id == photo_id).order_by(PhotoChild.position.asc())
        ).all()
        return [(row.child_id, row.position) for row in rows]
    finally:
        session.close()


def test_photo_two_children_both_feeds_family_one_card(client: TestClient):
    elder = _family(client, "p-two@example.com", "妈妈", "两孩家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    created = _photo(client, [elder, younger])
    assert created.status_code == 201, created.text
    body = created.json()
    photo_id = body["id"]
    assert body["child_ids"] == [elder, younger]
    assert body["child_id"] == elder
    assert body["child"]["id"] == elder
    assert [item["name"] for item in body["children"]] == ["果果", "豆豆"]
    assert body["children"][0]["age_label"] == age_label(date(2024, 7, 12), date(2026, 10, 22))
    assert "private, no-store" in created.headers["cache-control"]
    elder_feed = client.get(f"/api/children/{elder}/timeline").json()
    younger_feed = client.get(f"/api/children/{younger}/timeline").json()
    assert [item["id"] for item in elder_feed["days"][0]["photos"]] == [photo_id]
    assert [item["id"] for item in younger_feed["days"][0]["photos"]] == [photo_id]
    assert [item["name"] for item in elder_feed["days"][0]["photos"][0]["children"]] == ["果果", "豆豆"]
    family = client.get("/?view=family")
    assert family.status_code == 200
    assert family.text.count('class="pcard"') == 1
    assert family.text.count(f'href="/photos/{photo_id}"') == 1
    assert "果果" in family.text and "豆豆" in family.text


def test_uploader_photo_multi_viewer_can_see(client: TestClient):
    elder = _family(client, "p-see@example.com", "妈妈", "可见家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    created = _photo(client, [younger, elder])
    assert created.status_code == 201, created.text
    photo_id = created.json()["id"]
    viewer = _invite(client, "p-see-ro@example.com", "viewer", "外婆")
    page = viewer.get(f"/?child={elder}")
    assert page.status_code == 200
    assert f'href="/photos/{photo_id}"' in page.text
    assert "豆豆" in page.text and "果果" in page.text
    assert 'class="fab"' not in page.text
    assert "/upload" not in page.text
    detail = viewer.get(f"/photos/{photo_id}")
    assert detail.status_code == 200
    assert PHOTO_MULTI_CHILD["d_kids_label"] in detail.text
    assert "/edit" not in detail.text
    assert viewer.get(f"/media/{photo_id}").status_code == 200


def test_empty_child_ids_400(client: TestClient):
    elder = _family(client, "p-empty@example.com", "妈妈", "空选家", child="果果")
    _add(client, "豆豆", "2025-03-01")
    denied = client.post(
        "/api/photos",
        data={"kind": "child", "taken_on": DAY, "child_ids": ""},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert denied.status_code == 400
    assert denied.json()["code"] == "child_required"
    assert denied.json()["detail"] == PHOTO_MULTI_CHILD["err_no_child"]
    page = client.get("/upload?kind=child")
    assert page.text.index('id="photo-date"') < page.text.index('id="photo-preview"') < page.text.index('id="film-strip"')
    assert page.text.index('id="film-strip"') < page.text.index('class="caprow"') < page.text.index('id="redbar"')
    assert page.text.index('id="redbar"') < page.text.index('class="wfoot"') < page.text.index('id="photo-submit"')
    block = _film(page.text)
    assert 'id="redbar"' not in block
    assert PHOTO_MULTI_CHILD["err_no_child"] in page.text.split('id="redbar"', 1)[1].split('id="photo-submit"', 1)[0]
    assert PHOTO_MULTI_CHILD["strip_hint_pick"] in block
    assert PHOTO_MULTI_CHILD["u_cap_ph"] in page.text.split('class="caprow"', 1)[1].split('class="up-spacer"', 1)[0]
    assert "sticker on" not in block
    assert "btn dis" in page.text
    assert "disabled" in page.text.split('id="photo-submit"', 1)[1].split(">", 1)[0]
    assert 'class="mid"' in page.text
    assert _photo(client, [elder]).status_code == 201


def test_viewer_no_write_entry_api_403(client: TestClient):
    child_id = _family(client, "p-ro@example.com", "妈妈", "只读家", child="果果")
    viewer = _invite(client, "p-ro-v@example.com", "viewer", "外婆")
    assert viewer.get("/upload").status_code == 403
    assert viewer.get("/upload?kind=child").status_code == 403
    denied = viewer.post(
        "/api/photos",
        data={"kind": "child", "child_id": str(child_id), "taken_on": DAY},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "photo_forbidden_role"
    assert "private, no-store" in denied.headers["cache-control"]
    home = viewer.get("/")
    assert 'class="fab"' not in home.text
    assert "/upload" not in home.text


def test_edit_assoc_updates_both_feeds_age_on_taken_on(client: TestClient):
    elder = _family(client, "p-age@example.com", "妈妈", "年龄家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    created = _photo(client, [elder])
    assert created.status_code == 201, created.text
    photo_id = created.json()["id"]
    edited = client.patch(f"/api/photos/{photo_id}", json={"child_ids": [younger, elder]})
    assert edited.status_code == 200, edited.text
    assert edited.json()["child_ids"] == [younger, elder]
    assert edited.json()["child_id"] == younger
    elder_age = age_label(date(2024, 7, 12), date(2026, 10, 22))
    younger_age = age_label(date(2025, 3, 1), date(2026, 10, 22))
    assert edited.json()["children"][0]["age_label"] == younger_age
    assert edited.json()["children"][1]["age_label"] == elder_age
    elder_page = client.get(f"/?child={elder}")
    younger_page = client.get(f"/?child={younger}")
    assert f"{'豆豆'}{younger_age}" in elder_page.text
    assert f"{'果果'}{elder_age}" in younger_page.text
    assert elder_page.text.count('class="pcard"') == 1
    session = SessionLocal()
    try:
        row = session.get(Photo, photo_id)
        assert row is not None and row.child_id == younger
        assert row.taken_on.isoformat() == DAY
    finally:
        session.close()


def test_preselect_from_child_a_add_sibling(client: TestClient):
    elder = _family(client, "p-pre@example.com", "妈妈", "预选家", child="果果")
    _add(client, "豆豆", "2025-03-01")
    page = client.get(f"/upload?kind=child&child={elder}")
    assert page.status_code == 200
    assert "已贴果果，可再贴兄弟姐妹" in page.text
    assert page.text.index('id="photo-date"') < page.text.index('id="photo-preview"') < page.text.index('id="film-strip"')
    block = _film(page.text)
    assert block.count('class="sticker on"') == 1
    assert "豆豆" in block and "果果" in block
    entry = client.get(f"/upload?child={elder}")
    assert 'id="photo-entry"' in entry.text
    assert 'id="film-strip"' not in entry.text
    assert f"/upload?kind=child&amp;child={elder}" in entry.text or f"/upload?kind=child&child={elder}" in entry.text
    bare = client.get("/upload?kind=child")
    assert PHOTO_MULTI_CHILD["strip_hint_pick"] in bare.text
    assert 'class="sticker on"' not in bare.text


def test_family_entry_does_not_preselect_child_page_does(client: TestClient):
    elder = _family(client, "p-fab@example.com", "妈妈", "入口家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    child_page = client.get(f"/?child={younger}")
    assert _tag_href(child_page.text, "fab-photo") == f"/upload?child={younger}"
    assert _tag_href(child_page.text, "fab-video") == f"/videos/new?child={younger}"
    feed = child_page.text.split('class="feed"', 1)[1].split(">", 1)[0]
    assert f'data-child-id="{younger}"' in feed
    assert "data-family-view" not in feed
    photo_entry = client.get(f"/upload?child={younger}")
    assert f"/upload?kind=child&amp;child={younger}" in photo_entry.text
    photo_form = client.get(f"/upload?kind=child&child={younger}")
    picked = [part for part in photo_form.text.split("<button") if 'class="sticker on"' in part]
    assert len(picked) == 1 and f'data-child-id="{younger}"' in picked[0]
    video_form = client.get(f"/videos/new?child={younger}")
    video_picked = [part for part in video_form.text.split("<button") if "chip on" in part]
    assert len(video_picked) == 1 and f'data-child-id="{younger}"' in video_picked[0]
    assert _tag_href(client.get(f"/?child={elder}").text, "fab-photo") == f"/upload?child={elder}"
    assert _tag_href(client.get(f"/?child={elder}").text, "fab-video") == f"/videos/new?child={elder}"

    family = client.get("/?view=family")
    assert _tag_href(family.text, "fab-photo") == "/upload"
    assert _tag_href(family.text, "fab-video") == "/videos/new"
    assert "/upload?child=" not in family.text
    assert "/videos/new?child=" not in family.text
    family_feed = family.text.split('class="feed"', 1)[1].split(">", 1)[0]
    assert "data-child-id" not in family_feed
    assert 'data-family-view="1"' in family_feed
    assert 'class="sticker on"' not in client.get("/upload?kind=child").text
    video_bare = client.get("/videos/new").text.split('id="sib"', 1)[1].split('id="film-preview"', 1)[0]
    assert "chip on" not in video_bare

    switched = client.get("/fragments/timeline?view=family")
    assert _tag_href(switched.text, "fab-photo") == "/upload"
    assert _tag_href(switched.text, "fab-video") == "/videos/new"
    assert "/upload?child=" not in switched.text
    assert "/videos/new?child=" not in switched.text
    switched_feed = switched.text.split('class="feed"', 1)[1].split(">", 1)[0]
    assert "data-child-id" not in switched_feed
    assert 'data-family-view="1"' in switched_feed

    pages = Path("app/routers/pages.py").read_text(encoding="utf-8")
    assert "children, None, toast, family_view=True" in pages
    assert "found = None" in pages
    script = Path("app/static/app.js").read_text(encoding="utf-8")
    assert 'querySelector("[data-child-id]")' not in script
    assert "data-family-view" in script
    stage = Path("app/templates/fragments/stage_inner.html").read_text(encoding="utf-8")
    assert 'class="feed" data-child-id=' not in stage


def test_self_photo_path_no_film_strip(client: TestClient):
    child_id = _family(client, "p-self@example.com", "妈妈", "本人家", child="果果")
    _add(client, "豆豆", "2025-03-01")
    page = client.get("/upload?kind=self")
    assert page.status_code == 200
    assert page.text.index('id="photo-date"') < page.text.index('id="photo-preview"') < page.text.index('id="self-banner"')
    assert 'id="film-strip"' not in page.text
    assert 'class="caprow"' not in page.text
    assert 'id="redbar"' not in page.text
    assert "sticker" not in page.text
    assert PHOTO_MULTI_CHILD["self_banner"] in page.text
    assert PHOTO_MULTI_CHILD["u_submit_self"] in page.text
    assert "btn family" in page.text
    created = upload(client, kind="self", taken_on=DAY, send_child=False)
    assert created.status_code == 201, created.text
    assert created.json()["child_id"] is None
    assert "children" not in created.json()
    photo_id = created.json()["id"]
    assert _assoc(photo_id) == []
    detail = client.get(f"/photos/{photo_id}")
    assert PHOTO_MULTI_CHILD["self_banner"] in detail.text
    assert PHOTO_MULTI_CHILD["d_kids_label"] not in detail.text
    assert "/edit" not in detail.text
    me = client.get("/api/me").json()["id"]
    album = client.get(f"/people/{me}/album")
    assert PHOTO_MULTI_CHILD["self_banner"] in album.text
    assert client.get(f"/photos/{photo_id}/edit").status_code == 403
    smuggled = client.post(
        "/api/photos",
        data={"kind": "self", "taken_on": DAY, "child_ids": str(child_id)},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert smuggled.status_code == 400
    assert smuggled.json()["detail"] == "本人照片不能挂到孩子"
    assert smuggled.json()["code"] == "photo_self_no_children"


def test_export_metadata_has_child_list(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("EXPORT_TEMP_DIR", str(tmp_path))
    elder = _family(client, "p-zip@example.com", "妈妈", "导出家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    one = _photo(client, [elder])
    two = _photo(client, [younger, elder])
    assert one.status_code == two.status_code == 201
    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 200, packed.text
    names = _names(packed.content)
    one_name = f"{DAY}_{one.json()['id']}.jpg"
    two_name = f"{DAY}_{two.json()['id']}.jpg"
    side = f"{DAY}_{two.json()['id']}.children.json"
    assert one_name in names
    assert two_name in names
    assert side in names
    assert f"{DAY}_{one.json()['id']}.children.json" not in names
    sidecar = json.loads(_stored(packed.content, side).decode("utf-8"))
    assert sidecar["child_ids"] == [younger, elder]
    assert sidecar["names"] == ["豆豆", "果果"]
    export_page = Path("app/templates/export.html").read_text(encoding="utf-8")
    sheet = Path("app/templates/fragments/upload_sheet.html").read_text(encoding="utf-8")
    assert "贴上这一天" not in export_page
    assert "贴上这一天" not in sheet
    assert "child_ids" not in export_page


def test_backfill_keeps_single_assoc(client: TestClient):
    child_id = _family(client, "p-one@example.com", "妈妈", "存量家", child="果果")
    created = upload(client, kind="child", child_id=child_id, taken_on=DAY)
    assert created.status_code == 201, created.text
    photo_id = created.json()["id"]
    assert created.json()["child_ids"] == [child_id]
    assert _assoc(photo_id) == [(child_id, 0)]
    session = SessionLocal()
    try:
        row = session.get(Photo, photo_id)
        assert row is not None and row.child_id == child_id
    finally:
        session.close()


def test_copy_no_banned_words_photo_multi_child():
    assert hashlib.md5(MOCKUP.read_bytes()).hexdigest() == FINAL_MD5
    assert hashlib.md5(Path("design/family_album/multi-child/final/mockup.html").read_bytes()).hexdigest() == MULTI_MD5
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    script = r"""
const fs = require("fs");
const text = fs.readFileSync(process.argv[1], "utf8");
const start = text.indexOf("const COPY = ");
const end = text.indexOf("\n};", start);
const COPY = Function("return (" + text.slice(start + "const COPY = ".length, end + 2) + ")")();
process.stdout.write(JSON.stringify(COPY));
"""
    completed = subprocess.run(
        [node, "-e", script, str(MOCKUP.resolve())],
        check=True,
        capture_output=True,
        text=True,
    )
    mockup = json.loads(completed.stdout)
    assert len(mockup) == 56
    anno = {key: value for key, value in mockup.items() if key.startswith("anno_")}
    product = {key: value for key, value in mockup.items() if not key.startswith("anno_")}
    assert len(anno) == 6
    assert len(product) == 50
    assert product == PHOTO_MULTI_CHILD
    assert PHOTO_MULTI_CHILD["err_no_child"] == "至少选一个孩子"
    assert PHOTO_MULTI_CHILD["u_sending"] == "贴着" + "\u2026"
    assert "\u2026" in PHOTO_MULTI_CHILD["u_cap_ph"]
    surfaces = [
        Path("app/photo_copy.py"),
        Path("app/static/photo.js"),
        Path("app/templates/upload.html"),
        Path("app/templates/photo.html"),
        Path("app/templates/album.html"),
        Path("app/templates/fragments/photo_card.html"),
        Path("app/templates/fragments/stage_inner.html"),
    ]
    for path in surfaces:
        text = path.read_text(encoding="utf-8")
        for word in BANNED:
            assert word not in text, f"{word} in {path}"
        assert "anno_" not in text
    script_text = Path("app/static/photo.js").read_text(encoding="utf-8")
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in script_text)
    assert "u_cap_ph" not in Path("app/templates/upload.html").read_text(encoding="utf-8") or "data-copy-" in Path(
        "app/templates/upload.html"
    ).read_text(encoding="utf-8")
    assert 'id="photo-caption"' not in Path("app/templates/upload.html").read_text(encoding="utf-8")
    assert "<textarea" not in Path("app/templates/upload.html").read_text(encoding="utf-8")


def test_child_feed_filter_not_trim_stack_photo(client: TestClient):
    elder = _family(client, "p-filter@example.com", "妈妈", "过滤家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    third = _add(client, "安安", "2021-01-10")
    fourth = _add(client, "米米", "2020-02-02")
    both = _photo(client, [elder, younger, third, fourth])
    only = _photo(client, [younger])
    assert both.status_code == only.status_code == 201
    elder_page = client.get(f"/?child={elder}")
    younger_page = client.get(f"/?child={younger}")
    assert f"/photos/{both.json()['id']}" in elder_page.text
    assert f"/photos/{only.json()['id']}" not in elder_page.text
    assert f"/photos/{only.json()['id']}" in younger_page.text
    card = elder_page.text.split(f'data-photo-id="{both.json()["id"]}"', 1)[1].split("</a>", 1)[0]
    assert "果果" in card and "豆豆" in card and "安安" in card and "米米" in card
    assert card.count('class="sa ') == 3
    assert "+1" in card


def test_toggle_last_child_noop_photo():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    source = Path("app/static/photo.js").read_text(encoding="utf-8")
    assert 'copy("strip_hint_last")' in source
    assert "selected.length <= 1" in source
    script = r"""
const fs = require("fs");
const vm = require("vm");
const code = fs.readFileSync(process.argv[1], "utf8");
function el(id) {
  const node = { id: id || "", hidden: false, disabled: false, value: "", children: [], attrs: {}, listeners: {} };
  let text = "";
  const names = new Set();
  node.classList = {
    add() {}, remove() {},
    toggle(name, force) {
      const on = force === undefined ? !names.has(name) : !!force;
      if (on) names.add(name); else names.delete(name);
    },
    contains(name) { return names.has(name); },
  };
  Object.defineProperty(node, "textContent", { get() { return text; }, set(v) { text = String(v); } });
  Object.defineProperty(node, "className", {
    get() { return Array.from(names).join(" "); },
    set(value) { names.clear(); String(value).split(/\s+/).filter(Boolean).forEach((name) => names.add(name)); },
  });
  node.setAttribute = (k, v) => { node.attrs[k] = String(v); };
  node.getAttribute = (k) => (Object.prototype.hasOwnProperty.call(node.attrs, k) ? node.attrs[k] : null);
  node.addEventListener = (type, fn) => { (node.listeners[type] = node.listeners[type] || []).push(fn); };
  node.appendChild = (child) => { child.parentNode = node; node.children.push(child); return child; };
  return node;
}
const ids = {};
function add(id) { const node = el(id); ids[id] = node; return node; }
const appNode = add("photo-app");
Object.assign(appNode.attrs, {
  "data-mode": "edit", "data-kind": "child", "data-single": "0", "data-pre-name": "果果",
  "data-copy-strip_hint_last": "至少留一位，不能全撕掉",
  "data-copy-strip_hint_pre": "已贴{name}，可再贴兄弟姐妹",
  "data-copy-strip_hint_pick": "从胶片上点头像贴上，至少一位",
  "data-copy-strip_hint_single": "家里这一位，已自动贴上",
  "data-copy-err_no_child": "至少选一个孩子",
  "data-copy-u_submit_edit": "保存修改",
});
add("photo-selected").textContent = "[1]";
add("photo-children").textContent = JSON.stringify([{id:1,name:"果果",birth:"2024-07-12"},{id:2,name:"豆豆",birth:"2025-03-01"}]);
add("strip-hint");
add("redbar");
add("photo-submit");
add("photo-error");
const row = add("sticker-row");
[1, 2].forEach((id) => {
  const button = el("sticker-" + id);
  button.attrs["data-child-id"] = String(id);
  row.appendChild(button);
});
const docListeners = {};
const document = {
  readyState: "loading",
  visibilityState: "visible",
  getElementById(id) { return ids[id] || null; },
  createElement() { return el(""); },
  addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
};
function click(node) {
  const event = { target: node };
  let cur = node;
  while (cur) {
    (cur.listeners.click || []).forEach((fn) => fn(event));
    cur = cur.parentNode;
  }
}
const sandbox = { console, document, location: { href: "" }, addEventListener() {} };
sandbox.window = sandbox;
vm.runInNewContext(code, sandbox, { filename: "photo.js" });
(docListeners.DOMContentLoaded || []).forEach((fn) => fn());
click(row.children[0]);
const stillOn = row.children.filter((node) => node.className.indexOf("on") !== -1).length;
const afterLast = ids["strip-hint"].textContent;
click(row.children[1]);
const both = row.children.filter((node) => node.className.indexOf("on") !== -1).length;
process.stdout.write(JSON.stringify({ stillOn, afterLast, both }));
"""
    completed = subprocess.run(
        [node, "-e", script, str(Path("app/static/photo.js").resolve())],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    report = json.loads(completed.stdout)
    assert report["stillOn"] == 1
    assert report["afterLast"] == "至少留一位，不能全撕掉"
    assert report["both"] == 2


def test_film_strip_has_no_age(client: TestClient):
    elder = _family(client, "p-strip@example.com", "妈妈", "胶片家", child="果果")
    _add(client, "豆豆", "2025-03-01")
    page = client.get(f"/upload?kind=child&child={elder}")
    block = _film(page.text)
    assert "岁" not in block
    assert "个月" not in block
    assert PHOTO_MULTI_CHILD["strip_label"] in block
    single = TestClient(app)
    only = _family(single, "p-solo@example.com", "妈妈", "单孩胶片", child="果果")
    solo = single.get(f"/upload?kind=child&child={only}")
    hint = _film(solo.text)
    assert PHOTO_MULTI_CHILD["strip_hint_single"] in hint
    assert "岁" not in hint
    entry = client.get("/upload")
    assert PHOTO_MULTI_CHILD["entry_title"] in entry.text
    assert 'id="film-strip"' not in entry.text
    assert "sticker" not in entry.text


def test_family_feed_dedupe_one_photo_card(client: TestClient):
    elder = _family(client, "p-dedupe@example.com", "妈妈", "去重家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    created = _photo(client, [elder, younger])
    assert created.status_code == 201
    family = client.get("/?view=family")
    assert family.text.count('class="pcard"') == 1
    assert client.get(f"/?child={elder}").text.count('class="pcard"') == 1
    assert client.get(f"/?child={younger}").text.count('class="pcard"') == 1


def test_same_day_order_journal_video_photo(client: TestClient):
    elder = _family(client, "p-order@example.com", "妈妈", "排序家", child="果果")
    assert _journal(client, [elder], "同一天的句子", on_date=DAY).status_code == 201
    assert _video(client, [elder], on_date=DAY).status_code == 201
    assert _photo(client, [elder]).status_code == 201
    page = client.get(f"/?child={elder}")
    assert page.text.index('class="jcard"') < page.text.index('class="vcard') < page.text.index('class="pcard"')
    family = client.get("/?view=family")
    assert family.text.index('class="jcard"') < family.text.index('class="vcard') < family.text.index('class="pcard"')


def test_api_empty_array_400_photo(client: TestClient):
    child_id = _family(client, "p-arr@example.com", "妈妈", "空数组家", child="果果")
    created = _photo(client, [child_id])
    assert created.status_code == 201
    cleared = client.patch(f"/api/photos/{created.json()['id']}", json={"child_ids": []})
    assert cleared.status_code == 400
    assert cleared.json()["code"] == "child_required"
    assert cleared.json()["detail"] == PHOTO_MULTI_CHILD["err_no_child"]
    assert _assoc(created.json()["id"]) == [(child_id, 0)]


def test_cannot_clear_all_photo_children(client: TestClient):
    child_id = _family(client, "p-clear@example.com", "妈妈", "撕光家", child="果果")
    created = _photo(client, [child_id])
    missing = client.patch(
        f"/api/photos/{created.json()['id']}",
        content=b"{",
        headers={"content-type": "application/json"},
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == "child_required"
    assert missing.json()["detail"] == PHOTO_MULTI_CHILD["err_no_child"]
    assert _assoc(created.json()["id"]) == [(child_id, 0)]


def test_gone_child_rejected_photo(client: TestClient):
    elder = _family(client, "p-gone@example.com", "妈妈", "移孩家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    session = SessionLocal()
    try:
        row = session.get(Child, younger)
        assert row is not None
        session.delete(row)
        session.commit()
    finally:
        session.close()
    denied = _photo(client, [younger])
    assert denied.status_code == 400
    assert denied.json()["code"] == "child_not_found"
    assert denied.json()["detail"] == PHOTO_MULTI_CHILD["e_kid_gone"]
    assert elder


def test_dup_ids_collapsed_or_400_photo(client: TestClient):
    elder = _family(client, "p-dup@example.com", "妈妈", "重复家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    created = _photo(client, [elder, elder])
    assert created.status_code == 201, created.text
    assert created.json()["child_ids"] == [elder]
    assert created.json()["child_id"] == elder
    too_many = _photo(client, [elder, younger, 999999])
    assert too_many.status_code == 400
    assert too_many.json()["code"] == "children_too_many"
    assert too_many.json()["detail"] == PHOTO_MULTI_CHILD["e_kid_gone"]


def test_photo_date_before_max_birth(client: TestClient):
    elder = _family(client, "p-birth@example.com", "妈妈", "生日家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    denied = _photo(client, [elder, younger], taken_on="2024-01-01")
    assert denied.status_code == 400
    assert denied.json()["code"] == "date_before_birth"
    assert denied.json()["detail"] == "那天豆豆还没出生"


def test_legacy_child_id_and_child_ids_win(client: TestClient):
    elder = _family(client, "p-legacy@example.com", "妈妈", "兼容家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    legacy = upload(client, kind="child", child_id=elder, taken_on=DAY)
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["child_id"] == elder
    assert _assoc(legacy.json()["id"]) == [(elder, 0)]
    both = client.post(
        "/api/photos",
        data={
            "kind": "child",
            "taken_on": DAY,
            "child_id": str(elder),
            "child_ids": [str(younger), str(elder)],
        },
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert both.status_code == 201, both.text
    assert both.json()["child_ids"] == [younger, elder]
    assert both.json()["child_id"] == younger
    session = SessionLocal()
    try:
        row = session.get(Photo, both.json()["id"])
        assert row is not None and row.child_id == younger
    finally:
        session.close()


def test_photo_patch_rejects_file(client: TestClient):
    elder = _family(client, "p-file@example.com", "妈妈", "换片家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    created = _photo(client, [elder])
    photo_id = created.json()["id"]
    rejected = client.patch(
        f"/api/photos/{photo_id}",
        data={"child_ids": str(younger)},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "photo_file_immutable"
    assert rejected.json()["detail"] == "不能换片"
    sneaky = client.patch(f"/api/photos/{photo_id}", json={"child_ids": [younger], "file": "a.jpg"})
    assert sneaky.status_code == 400
    assert sneaky.json()["code"] == "photo_file_immutable"
    kind = client.patch(f"/api/photos/{photo_id}", json={"child_ids": [younger], "kind": "self"})
    assert kind.status_code == 400
    assert kind.json()["code"] == "photo_kind_immutable"
    session = SessionLocal()
    try:
        row = session.get(Photo, photo_id)
        assert row is not None and row.kind == "child" and row.child_id == elder
    finally:
        session.close()


def test_edit_page_no_file_input(client: TestClient):
    child_id = _family(client, "p-edit@example.com", "妈妈", "编辑家", child="果果")
    created = _photo(client, [child_id])
    photo_id = created.json()["id"]
    page = client.get(f"/photos/{photo_id}/edit")
    assert page.status_code == 200
    assert PHOTO_MULTI_CHILD["u_title_edit"] in page.text
    assert PHOTO_MULTI_CHILD["u_submit_edit"] in page.text
    assert 'type="file"' not in page.text
    assert 'id="film-strip"' in page.text
    assert page.text.index('id="photo-date-label"') < page.text.index('id="photo-preview"') < page.text.index('id="film-strip"')
    assert page.text.index('id="film-strip"') < page.text.index('class="caprow"') < page.text.index('id="redbar"')
    assert page.text.index('id="redbar"') < page.text.index('id="photo-submit"')
    detail = client.get(f"/photos/{photo_id}")
    top, menu = detail.text.split('id="photo-menu"', 1)
    assert f'href="/photos/{photo_id}/edit"' in top
    assert "✎" in top
    assert "/edit" not in menu.split("</div>", 1)[0]


def test_storage_503_writes_no_photo(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    child_id = _family(client, "p-503@example.com", "妈妈", "存储家", child="果果")

    class Boom:
        def put(self, *_args, **_kwargs):
            raise StorageError("VOL_MISSING", "missing")

    monkeypatch.setattr("app.services.photos.current_store", lambda: Boom())
    try:
        denied = _photo(client, [child_id])
        assert denied.status_code == 503
        assert denied.json()["code"] == "VOL_MISSING"
    finally:
        app.state.storage_error = None
    session = SessionLocal()
    try:
        assert session.scalars(select(Photo)).all() == []
    finally:
        session.close()


def test_parallel_patch_photo_assoc(client: TestClient):
    elder = _family(client, "p-race@example.com", "妈妈", "并发家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    created = _photo(client, [elder])
    photo_id = created.json()["id"]
    other = _clone(client)
    barrier = threading.Barrier(2)
    results: list[int] = []

    def go(actor: TestClient, ids: list[int]) -> None:
        barrier.wait(5)
        results.append(actor.patch(f"/api/photos/{photo_id}", json={"child_ids": ids}).status_code)

    threads = [
        threading.Thread(target=go, args=(client, [elder])),
        threading.Thread(target=go, args=(other, [younger, elder])),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert results == [200, 200]
    current = client.get(f"/api/children/{elder}/timeline").json()["days"][0]["photos"][0]["child_ids"]
    assert current in ([elder], [younger, elder])


def test_csrf_origin_rejects_photo_write(client: TestClient):
    child_id = _family(client, "p-csrf@example.com", "妈妈", "跨站家", child="果果")
    denied = client.post(
        "/api/photos",
        data={"kind": "child", "child_id": str(child_id), "taken_on": DAY},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
        headers={"Origin": "https://evil.example"},
    )
    assert denied.status_code == 403
    assert denied.json() == {"detail": "没有访问权限"}
    assert "code" not in denied.json()


def test_js_422_mapped_photo():
    source = Path("app/static/photo.js").read_text(encoding="utf-8")
    mapped = source.split("function mapError", 1)[1].split("function afterForbidden", 1)[0]
    assert "status === 422" in mapped
    assert 'copy("err_no_child")' in mapped
    assert "retry: true" in mapped
    assert mapped.index("status === 422") < mapped.index("retry: true")


def test_js_404_without_code_retryable_photo():
    source = Path("app/static/photo.js").read_text(encoding="utf-8")
    mapped = source.split("function mapError", 1)[1].split("function afterForbidden", 1)[0]
    assert "status === 404 && !(data && data.code)" in mapped
    assert "retryText()" in mapped
    assert 'status === 403 && !(data && data.code)' in mapped
    assert 'copy("e_removed")' in mapped
    assert "/api/me" in source
    assert "state.aborted" in source
    assert "xhr.abort" in source
    assert 'visibilityState === "hidden"' in source
    assert 'addEventListener("pagehide"' in source
    assert 'addEventListener("beforeunload"' in source
    assert "data-back" in source
    onload = source.split("xhr.onload = function", 1)[1].split("xhr.onerror", 1)[0]
    assert onload.index("state.aborted") < onload.index("finish(")


def test_self_empty_child_ids_ignores_legacy_child_id(client: TestClient):
    child_id = _family(client, "p-blank@example.com", "妈妈", "空列表家", child="果果")
    created = client.post(
        "/api/photos",
        data={"kind": "self", "taken_on": DAY, "child_id": str(child_id), "child_ids": ""},
        files={"file": ("a.jpg", JPEG, "image/jpeg")},
    )
    assert created.status_code == 201, created.text
    assert created.json()["child_id"] is None
    assert _assoc(created.json()["id"]) == []
