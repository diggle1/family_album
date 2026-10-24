"""Multi-child association: API, feeds, export metadata, and copy."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import uuid
import zipfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.constants import JOURNAL, MULTI_CHILD, VIDEO
from app.db import SessionLocal
from app.main import app
from app.models import Journal, JournalChild, Video
from app.services.clock import age_label, shanghai_today
from tests.test_acceptance import auth
from tests.test_journal import _family, _invite
from tests.test_video import _mp4

BANNED = ("会员", "开通", "更高清", "加时长", "朋友圈", "发动态", "广告", "商城", "发现")
MOCKUP = Path("design/family_album/multi-child/final/mockup.html")
FINAL_MD5 = "2bdab7983095c7e7b81a7c7715b73211"


def _post(client: TestClient, child_ids: list[int], body: str = "今天姐妹俩", on_date: str | None = None, **extra):
    payload = {
        "child_ids": child_ids,
        "on_date": on_date or shanghai_today().isoformat(),
        "body": body,
        "photo_ids": [],
    }
    payload.update(extra)
    return client.post(
        "/api/journals",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json=payload,
    )


def _put(client: TestClient, journal_id: int, child_ids: list[int], body: str, on_date: str):
    return client.put(
        f"/api/journals/{journal_id}",
        json={"child_ids": child_ids, "on_date": on_date, "body": body, "photo_ids": []},
    )


def _video(
    client: TestClient,
    child_ids: list[int],
    on_date: str | None = None,
    child_id: int | None = None,
    *,
    empty_ids: bool = False,
):
    data: dict = {
        "on_date": on_date or shanghai_today().isoformat(),
        "duration_ms": "12500",
    }
    if empty_ids:
        data["child_ids"] = ""
    elif child_ids:
        data["child_ids"] = [str(item) for item in child_ids]
    if child_id is not None:
        data["child_id"] = str(child_id)
    return client.post(
        "/api/videos",
        data=data,
        files={"file": ("clip.mp4", _mp4(), "video/mp4")},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )


def _add(client: TestClient, name: str, birth: str) -> int:
    created = client.post("/api/children", json={"name": name, "birth_date": birth})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _names(content: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.namelist()


def _stored(content: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.read(name)


def test_journal_two_children_both_feeds_family_one_card(client: TestClient):
    elder = _family(client, "two-j@example.com", "妈妈", "双孩日志家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    created = _post(client, [elder, younger], "一起穿鞋")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["child_ids"] == [elder, younger]
    assert body["child"]["id"] == elder
    assert [kid["id"] for kid in body["children"]] == [elder, younger]
    for child_id in (elder, younger):
        page = client.get(f"/?child={child_id}")
        assert page.status_code == 200
        assert page.text.count('class="jcard"') == 1
        assert "一起穿鞋" in page.text
        assert "果果" in page.text and "豆豆" in page.text
    family = client.get("/?view=family")
    assert family.status_code == 200
    assert family.text.count('class="jcard"') == 1
    assert "一起穿鞋" in family.text
    assert 'href="/videos/new"' in family.text


def test_video_two_children_viewer_can_play(client: TestClient):
    elder = _family(client, "two-v@example.com", "妈妈", "双孩视频家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    created = _video(client, [younger, elder], child_id=elder)
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["child_ids"] == [younger, elder]
    assert payload["child"]["id"] == younger
    session = SessionLocal()
    try:
        row = session.get(Video, payload["id"])
        assert row is not None
        assert row.child_id == younger
    finally:
        session.close()
    viewer = _invite(client, "two-vv@example.com", "viewer", "奶奶")
    page = viewer.get(f"/videos/{payload['id']}")
    assert page.status_code == 200
    assert "果果" in page.text and "豆豆" in page.text
    media = viewer.get(payload["media_url"])
    assert media.status_code == 200
    assert media.content[:4] == _mp4()[:4]
    family = client.get("/?view=family")
    assert family.text.count('class="vcard') == 1


def test_empty_child_ids_400(client: TestClient):
    child_id = _family(client, "empty-j@example.com", "妈妈", "空选家")
    missing = _post(client, [], "没选")
    assert missing.status_code == 400
    assert missing.json()["code"] == "child_required"
    assert missing.json()["detail"] == MULTI_CHILD["err_no_child"]
    legacy = client.post(
        "/api/journals",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={"body": "旧字段也没给", "on_date": shanghai_today().isoformat(), "photo_ids": []},
    )
    assert legacy.status_code == 400
    assert legacy.json()["code"] == "child_required"
    assert legacy.json()["detail"] == JOURNAL["err_no_child"]
    clip = _video(client, [], empty_ids=True)
    assert clip.status_code == 400
    assert clip.json()["code"] == "child_required"
    assert clip.json()["detail"] == MULTI_CHILD["err_no_child"]
    assert child_id


def test_api_empty_array_400(client: TestClient):
    _family(client, "arr@example.com", "妈妈", "空数组家")
    response = _post(client, [], "空数组")
    assert response.status_code == 400
    assert response.json() == {
        "detail": "至少选一个孩子",
        "code": "child_required",
    }


def test_primary_is_request_order_not_min_id(client: TestClient):
    first = _family(client, "ord@example.com", "妈妈", "顺序家", child="果果")
    second = _add(client, "豆豆", "2025-03-01")
    assert second > first
    created = _post(client, [second, first], "弟弟在前", child_id=first)
    assert created.status_code == 201, created.text
    assert created.json()["child"]["id"] == second
    assert created.json()["child_ids"] == [second, first]
    session = SessionLocal()
    try:
        row = session.get(Journal, created.json()["id"])
        assert row is not None
        assert row.child_id == second
        links = session.scalars(
            select(JournalChild).where(JournalChild.journal_id == row.id).order_by(JournalChild.position)
        ).all()
        assert [link.child_id for link in links] == [second, first]
    finally:
        session.close()


def test_gone_child_rejected(client: TestClient):
    child_id = _family(client, "gone-c@example.com", "妈妈", "不在列表家")
    sibling = _add(client, "豆豆", "2025-03-01")
    denied = _post(client, [child_id, 999999], "有人不在")
    assert sibling
    assert denied.status_code == 400
    assert denied.json()["code"] == "child_not_found"
    assert denied.json()["detail"] == MULTI_CHILD["e_kid_gone"]


def test_dup_ids_collapsed_or_400(client: TestClient):
    first = _family(client, "dup@example.com", "妈妈", "去重家", child="果果")
    second = _add(client, "豆豆", "2025-03-01")
    collapsed = _post(client, [second, second, first, second], "收成两位")
    assert collapsed.status_code == 201, collapsed.text
    assert collapsed.json()["child_ids"] == [second, first]
    too_many = _post(client, [first, second, first + second + 10], "三位")
    assert too_many.status_code == 400
    assert too_many.json()["code"] == "children_too_many"
    assert too_many.json()["detail"] == MULTI_CHILD["e_kid_gone"]


def test_date_before_max_birth(client: TestClient):
    today = shanghai_today()
    elder_birth = date(today.year - 3, 1, 10)
    younger_birth = date(today.year - 1, 6, 1)
    elder = _family(client, "birth@example.com", "妈妈", "生日家", child="果果")
    younger = _add(client, "豆豆", younger_birth.isoformat())
    session = SessionLocal()
    try:
        from app.models import Child

        row = session.get(Child, elder)
        assert row is not None
        row.birth_date = elder_birth
        session.commit()
    finally:
        session.close()
    too_early = date(today.year - 1, 3, 1)
    denied = _post(client, [elder, younger], "早于弟弟", on_date=too_early.isoformat())
    assert denied.status_code == 400
    assert denied.json()["code"] == "date_before_birth"
    assert "豆豆" in denied.json()["detail"]
    same = date(today.year - 3, 1, 10)
    tied = _add(client, "安安", same.isoformat())
    denied_tie = _post(client, [tied, elder], "同一天生日", on_date=date(today.year - 3, 1, 9).isoformat())
    assert denied_tie.status_code == 400
    assert denied_tie.json()["code"] == "date_before_birth"
    assert "安安" in denied_tie.json()["detail"]


def test_edit_assoc_updates_both_feeds_age_on_date(client: TestClient):
    today = shanghai_today()
    birth_a = date(today.year - 2, 1, 15)
    birth_b = date(today.year - 2, 3, 1)
    on_date = date(today.year - 2, 6, 20)
    later = date(today.year - 1, 4, 1)
    elder = _family(client, "age-m@example.com", "妈妈", "年龄家", child="果果")
    younger = _add(client, "豆豆", birth_b.isoformat())
    session = SessionLocal()
    try:
        from app.models import Child

        row = session.get(Child, elder)
        assert row is not None
        row.birth_date = birth_a
        session.commit()
    finally:
        session.close()
    created = _post(client, [elder], "先记一个", on_date=on_date.isoformat())
    assert created.status_code == 201, created.text
    journal_id = created.json()["id"]
    updated = _put(client, journal_id, [elder, younger], "改成两个", later.isoformat())
    assert updated.status_code == 200, updated.text
    assert updated.json()["child_ids"] == [elder, younger]
    age_a = age_label(birth_a, later)
    age_b = age_label(birth_b, later)
    for child_id in (elder, younger):
        page = client.get(f"/?child={child_id}")
        assert f"果果{age_a}" in page.text
        assert f"豆豆{age_b}" in page.text
    family = client.get("/?view=family")
    assert family.text.count('class="jcard"') == 1
    assert f"果果{age_a}" in family.text
    assert f"豆豆{age_b}" in family.text


def test_preselect_from_child_a_add_sibling(client: TestClient):
    elder = _family(client, "pre@example.com", "妈妈", "预选家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    page = client.get(f"/journals/new?child={elder}")
    assert page.status_code == 200
    assert f'class="chip on c0" data-child-id="{elder}"' in page.text
    assert f'class="chip c1" data-child-id="{younger}"' in page.text
    assert "已选果果，可再点兄弟姐妹" in page.text
    initial = json.loads(page.text.split('id="journal-initial">', 1)[1].split("</script>", 1)[0])
    assert initial["childIds"] == [elder]
    created = _post(client, [elder, younger], "预选后再加上弟弟")
    assert created.status_code == 201, created.text
    assert created.json()["child_ids"] == [elder, younger]
    blank = client.get("/journals/new")
    assert "点头像选择，至少一位" in blank.text
    assert 'class="chip on"' not in blank.text


def test_child_feed_filter_not_trim_stack(client: TestClient):
    elder = _family(client, "filt@example.com", "妈妈", "过滤家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    both = _post(client, [elder, younger], "两个都在")
    only = _post(client, [younger], "只有豆豆")
    assert both.status_code == only.status_code == 201
    elder_page = client.get(f"/?child={elder}")
    younger_page = client.get(f"/?child={younger}")
    assert "两个都在" in elder_page.text
    assert "只有豆豆" not in elder_page.text
    start = elder_page.text.index('class="jcard"')
    card = elder_page.text[start : elder_page.text.index("</a>", start)]
    assert "豆豆" in card
    assert "果果" in card
    assert "只有豆豆" in younger_page.text
    assert younger_page.text.count('class="jcard"') == 2


def test_family_feed_dedupe_one_card(client: TestClient):
    elder = _family(client, "dedupe@example.com", "妈妈", "去重卡家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    note = _post(client, [elder, younger], "全家只一张")
    clip = _video(client, [elder, younger])
    assert note.status_code == clip.status_code == 201
    family = client.get("/?view=family")
    assert family.text.count('class="jcard"') == 1
    assert family.text.count('class="vcard') == 1
    assert family.text.count("全家只一张") == 1


def test_cannot_clear_all_children(client: TestClient):
    child_id = _family(client, "clear@example.com", "妈妈", "清空家")
    created = _post(client, [child_id], "留着")
    assert created.status_code == 201, created.text
    cleared = _put(client, created.json()["id"], [], "清空", shanghai_today().isoformat())
    assert cleared.status_code == 400
    assert cleared.json()["code"] == "child_required"
    assert cleared.json()["detail"] == MULTI_CHILD["err_no_child"]
    source = Path("app/static/journal.js").read_text(encoding="utf-8")
    assert 'copy("sib_hint_last")' in source
    video = Path("app/static/video.js").read_text(encoding="utf-8")
    assert 'videoCopy("sib_hint_last")' in video


def test_toggle_last_child_noop():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    script = r"""
const fs = require("fs");
const vm = require("vm");
const code = fs.readFileSync(process.argv[1], "utf8");

function el(id) {
  const node = {
    id: id || "",
    hidden: false,
    disabled: false,
    readOnly: false,
    value: "",
    type: "button",
    parentNode: null,
    children: [],
    attrs: {},
    listeners: {},
  };
  let text = "";
  const names = new Set();
  node.classList = {
    add() {},
    remove() {},
    toggle(name, force) {
      const on = force === undefined ? !names.has(name) : !!force;
      if (on) names.add(name); else names.delete(name);
    },
    contains(name) { return names.has(name); },
  };
  Object.defineProperty(node, "textContent", {
    get() { return text; },
    set(value) { text = String(value); node.children = []; },
  });
  Object.defineProperty(node, "className", {
    get() { return Array.from(names).join(" "); },
    set(value) {
      names.clear();
      String(value).split(/\s+/).filter(Boolean).forEach((name) => names.add(name));
    },
  });
  node.setAttribute = (k, v) => { node.attrs[k] = String(v); };
  node.getAttribute = (k) => (Object.prototype.hasOwnProperty.call(node.attrs, k) ? node.attrs[k] : null);
  node.removeAttribute = (k) => { delete node.attrs[k]; };
  node.addEventListener = (type, fn) => { (node.listeners[type] = node.listeners[type] || []).push(fn); };
  node.appendChild = (child) => { child.parentNode = node; node.children.push(child); return child; };
  node.remove = () => {};
  node.querySelector = () => null;
  node.querySelectorAll = () => [];
  node.focus = () => {};
  return node;
}

const ids = {};
function add(id) { const node = el(id); ids[id] = node; return node; }
const app = add("journal-app");
Object.assign(app.attrs, {
  "data-mode": "new",
  "data-fid": "1",
  "data-uid": "2",
  "data-today": "2026-10-24",
  "data-body-max": "2000",
  "data-body-warn": "1900",
  "data-copy-w_submit": "记下来",
  "data-copy-w_count": "{n}/2000",
  "data-copy-date_fmt": "{y}年{mo}月{d}日 {wd}",
  "data-copy-err_no_child": "先选写给哪个孩子",
});
add("journal-initial").textContent = JSON.stringify({
  body: "有字", date: "2026-10-24", childId: 1, childIds: [1], photoIds: [], photos: [],
});
add("journal-children-data").textContent = JSON.stringify([
  { id: 1, name: "果果", birth: "2024-01-15" },
  { id: 2, name: "豆豆", birth: "2025-03-01" },
]);
add("multi-child-copy").textContent = JSON.stringify({
  sib_hint_last: "至少留一位，不能全取消",
  sib_hint_pre: "已选{name}，可再点兄弟姐妹",
  sib_hint_pick: "点头像选择，至少一位",
  err_no_child: "至少选一个孩子",
});
add("journal-body");
add("journal-count");
add("film-row");
add("journal-submit");
add("draft-bar");
add("draft-tag");
const row = add("sib-row");
const hint = add("sib-hint");
const docListeners = {};
const document = {
  visibilityState: "visible",
  body: el("body"),
  getElementById(id) { return ids[id] || null; },
  createElement() { return el(""); },
  createTextNode(text) { const node = el(""); node.textContent = text; return node; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
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
const sandbox = {
  console, setTimeout, clearTimeout, AbortController,
  fetch: () => Promise.resolve({ status: 201, text: () => Promise.resolve("{}") }),
  document, localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
  location: { pathname: "/journals/new", search: "", href: "" },
  crypto: { randomUUID: () => "11111111-1111-4111-8111-111111111111" },
  module: { exports: {} }, exports: {},
  addEventListener() {},
};
sandbox.window = sandbox;
vm.runInNewContext(code, sandbox, { filename: "journal.js" });
(docListeners.DOMContentLoaded || []).forEach((fn) => fn());
const first = row.children[0];
click(first);
const stillOn = row.children.filter((node) => node.className.indexOf("on") !== -1).length;
const afterLast = hint.textContent;
click(row.children[1]);
const both = row.children.filter((node) => node.className.indexOf("on") !== -1).length;
process.stdout.write(JSON.stringify({ stillOn, afterLast, both, chips: row.children.length }));
"""
    completed = subprocess.run(
        [node, "-e", script, str(Path("app/static/journal.js").resolve())],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    report = json.loads(completed.stdout)
    assert report["chips"] == 2
    assert report["stillOn"] == 1
    assert report["afterLast"] == "至少留一位，不能全取消"
    assert report["both"] == 2


def test_write_bar_has_no_age(client: TestClient):
    elder = _family(client, "bar@example.com", "妈妈", "写条家", child="果果")
    _add(client, "豆豆", "2025-03-01")
    page = client.get(f"/journals/new?child={elder}")
    sib = page.text.split('id="sib"', 1)[1].split('id="draft-bar"', 1)[0]
    assert "岁" not in sib
    assert "个月" not in sib
    video = client.get(f"/videos/new?child={elder}")
    block = video.text.split('id="sib"', 1)[1].split('id="film-preview"', 1)[0]
    assert "岁" not in block
    assert "个月" not in block
    single = TestClient(app)
    only = _family(single, "solo@example.com", "妈妈", "单孩家", child="果果")
    solo = single.get(f"/journals/new?child={only}")
    assert 'class="sib single"' in solo.text
    assert "已选" not in solo.text.split('id="sib"', 1)[1].split('id="draft-bar"', 1)[0]


def test_video_patch_rejects_file(client: TestClient):
    elder = _family(client, "patch@example.com", "妈妈", "换片家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    created = _video(client, [elder])
    assert created.status_code == 201, created.text
    video_id = created.json()["id"]
    rejected = client.patch(
        f"/api/videos/{video_id}",
        data={"child_ids": str(elder)},
        files={"file": ("clip.mp4", _mp4(), "video/mp4")},
    )
    assert rejected.status_code == 400
    assert rejected.json()["code"] == "video_file_immutable"
    assert rejected.json()["detail"] == "不能换片"
    sneaky = client.patch(
        f"/api/videos/{video_id}",
        json={"child_ids": [elder, younger], "file": "clip.mp4"},
    )
    assert sneaky.status_code == 400
    assert sneaky.json()["code"] == "video_file_immutable"
    edited = client.patch(
        f"/api/videos/{video_id}",
        json={"child_ids": [younger, elder], "on_date": shanghai_today().isoformat()},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["child_ids"] == [younger, elder]
    assert edited.json()["child"]["id"] == younger
    other = _invite(client, "patch-u@example.com", "uploader", "爸爸")
    forbidden = other.patch(f"/api/videos/{video_id}", json={"child_ids": [elder]})
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "video_not_author"


def test_export_metadata_has_child_ids(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("EXPORT_TEMP_DIR", str(tmp_path))
    elder = _family(client, "meta@example.com", "妈妈", "元数据家", child="果果")
    younger = _add(client, "豆豆", "2025-03-01")
    day = shanghai_today().isoformat()
    single = _post(client, [elder], "只有果果", on_date=day)
    multi = _post(client, [elder, younger], "两个孩子", on_date=day)
    one_clip = _video(client, [elder], on_date=day)
    two_clip = _video(client, [younger, elder], on_date=day)
    assert single.status_code == multi.status_code == one_clip.status_code == two_clip.status_code == 201
    packed = client.get("/api/export/my-photos.zip")
    assert packed.status_code == 200, packed.text
    names = _names(packed.content)
    one_name = f"{day}_{one_clip.json()['id']}.mp4"
    two_name = f"{day}_{two_clip.json()['id']}.mp4"
    side = f"{day}_{two_clip.json()['id']}.children.json"
    assert one_name in names
    assert two_name in names
    assert side in names
    assert f"{day}_{one_clip.json()['id']}.children.json" not in names
    text = _stored(packed.content, "日志.md").decode("utf-8")
    assert "孩子：果果\n引用：无\n" in text
    assert "孩子：果果、豆豆\n" in text
    assert f"child_ids：{elder},{younger}\n" in text
    sidecar = json.loads(_stored(packed.content, side).decode("utf-8"))
    assert sidecar["child_ids"] == [younger, elder]
    assert sidecar["names"] == ["豆豆", "果果"]
    page = Path("app/templates/export.html").read_text(encoding="utf-8")
    assert "child_ids" not in page


def test_js_422_mapped():
    source = Path("app/static/journal.js").read_text(encoding="utf-8")
    mapped = source.split("function mapError", 1)[1].split("function finishSuccess", 1)[0]
    assert "status === 422" in mapped
    assert mapped.index("status === 422") < mapped.index("isRetryable")
    branch = mapped.split("status === 422", 1)[1].split("if (isRetryable", 1)[0]
    assert 'copy("err_no_child")' in branch
    assert "retry: true" in branch
    video = Path("app/static/video.js").read_text(encoding="utf-8")
    upload = video.split("function mapUpload", 1)[1].split("function afterForbidden", 1)[0]
    assert upload.index("status === 422") < upload.index('return videoCopy("err_retry")')


def test_copy_no_banned_words_multi_child():
    assert hashlib.md5(MOCKUP.read_bytes()).hexdigest() == FINAL_MD5
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
    assert len(mockup) == 53
    anno = {key: value for key, value in mockup.items() if key.startswith("anno_")}
    product = {key: value for key, value in mockup.items() if not key.startswith("anno_")}
    assert len(anno) == 4
    assert len(product) == 49
    assert product == MULTI_CHILD
    assert MULTI_CHILD["err_no_child"] == "至少选一个孩子"
    assert MULTI_CHILD["w_placeholder"] == "这一天" + "\u2026" * 2
    assert MULTI_CHILD["w_sending"] == "正在发" + "\u2026"
    assert MULTI_CHILD["u_sending"] == "传着" + "\u2026"
    product_files = [
        Path("app/multi_child_copy.py"),
        Path("app/constants.py"),
        Path("app/static/journal.js"),
        Path("app/static/video.js"),
        Path("app/services/assoc.py"),
        Path("app/services/journals.py"),
        Path("app/services/videos.py"),
        Path("app/templates/journal_write.html"),
        Path("app/templates/video_upload.html"),
        Path("app/templates/journal_detail.html"),
        Path("app/templates/video_detail.html"),
        Path("app/templates/fragments/journal_card.html"),
        Path("app/templates/fragments/video_card.html"),
        Path("app/templates/fragments/stage_inner.html"),
    ]
    for path in product_files:
        text = path.read_text(encoding="utf-8")
        for word in BANNED:
            assert word not in text, f"{word} in {path}"
        assert "anno_" not in text
    video_js = Path("app/static/video.js").read_text(encoding="utf-8")
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in video_js)
    assert "一次最多" not in Path("app/constants.py").read_text(encoding="utf-8")
