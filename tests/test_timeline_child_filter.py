"""时光线按孩子筛选（窄）看谁签 v2026.10.25.8。不新表，筛选只留在地址上。"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, inspect, select

from app.constants import FORBIDDEN, TIMELINE_CHILD_FILTER
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Child, PhotoChild, TimelineLastSeen
from app.services.clock import shanghai_today
from app.services.on_this_day import previous_year_date
from app.services.timeline_child_filter import TimelineFilterBusy
from app.timeline_child_filter_copy import TIMELINE_CHILD_FILTER as COPY
from app.timeline_child_filter_copy import TIMELINE_CHILD_FILTER_ANNO as ANNO
from tests.test_acceptance import JPEG, auth, upload
from tests.test_journal import _family, _invite
from tests.test_multi_child import _video
from tests.test_on_this_day import EXPECTED_TABLES

BANNED = ("会员", "开通", "商城", "发现", "广告", "筛选器", "过滤器")
SURFACES = (
    Path("app/timeline_child_filter_copy.py"),
    Path("app/services/timeline_child_filter.py"),
    Path("app/static/timeline_child_filter.js"),
    Path("app/templates/fragments/stage_inner.html"),
    Path("app/templates/no_child.html"),
    Path("app/templates/add_child.html"),
    Path("app/static/app.css"),
    Path("app/routers/pages.py"),
    Path("app/routers/family.py"),
)
HIDDEN_KEYS = (
    "url_demo",
    "role_note_ro",
    "role_note_admin",
    "top_otd_gone",
    "j_quote_a",
    "j_quote_b",
    "j_quote_both",
    "kid_c",
    "tl_family",
)
PRODUCT = {
    "tab_time": "时光",
    "tab_family": "家人",
    "tab_settings": "设置",
    "ro_meta": "只读 · 只能看",
    "who_label": "看谁的",
    "who_all": "全家",
    "kid_a": "果果",
    "kid_b": "豆豆",
    "kid_c": "安安",
    "tl_family": "果果家",
    "tl_title_all": "时光",
    "tl_title_kid": "果果的时光",
    "age_a": "3岁2个月",
    "age_b": "1岁5个月",
    "j_quote_a": "「今天幼儿园画了大太阳」",
    "j_sub_a": "妈妈写 · 写给果果",
    "j_quote_b": "「豆豆学会叫爸爸了」",
    "j_sub_b": "爸爸写 · 写给豆豆",
    "j_quote_both": "「周末公园野餐」",
    "j_sub_both": "妈妈写 · 写给果果、豆豆",
    "v_title": "公园滑梯 · 爸爸传",
    "snip_line": "妈妈：今天幼儿园画了大太阳",
    "empty_title": "还没有果果的记录",
    "empty_sub": "家里给果果传了照片、日志或视频，会出现在这里。",
    "empty_back": "看全家",
    "empty_title_b": "还没有安安的记录",
    "empty_sub_b": "先给安安挂上照片或日志，再回来看。",
    "top_slip": "最近有新内容",
    "top_slip_sub": "3 天有新的",
    "top_otd": "去年今天",
    "top_otd_sub": "2025 · 果果 2 岁",
    "top_otd_gone": "筛到豆豆时，与豆豆无关的年签随主列表隐藏（示意）",
    "no_kids_hint": "先添加孩子，再来看谁的",
    "no_kids_sub": "家里还没有孩子时，时光线照常看；加上孩子后会出现名签。",
    "role_note_ro": "只读长辈：同路径可见名签条，无新增写入口",
    "role_note_admin": "管理员与可上传：同路径可筛；本刀不另开入口",
    "err_perm": "你没有权限做这件事",
    "err_gone": "进不了这家",
    "err_gone_sub": "家里的内容不会在这里显示",
    "desk_aside": "电脑和手机看到的是同一本相册",
    "url_demo": "album.guoguo.home / 时光",
}
ANNO_EXPECTED = {
    "anno_strip": "看谁签 · 浅纸名签条 · 主视觉=名字",
    "anno_vs": "异于写页 Ø56 兄弟姐妹条",
    "anno_filter": "点名签 → 主列表即时只留该孩",
    "anno_empty": "暖空纸口语 · 不假卡不白屏",
    "anno_top": "顶卡随筛：无关内容隐藏",
    "anno_desk": "桌面内容柱顶 · 同控件",
    "anno_nokids": "无孩子：无条或仅全家",
    "anno_ro": "只读同路径 · 无写入口",
}


def _add(client: TestClient, name: str, birth: str = "2025-03-01") -> int:
    created = client.post("/api/children", json={"name": name, "birth_date": birth})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _journal(client: TestClient, child_ids: list[int], body: str):
    return client.post(
        "/api/journals",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "child_ids": child_ids,
            "on_date": shanghai_today().isoformat(),
            "body": body,
            "photo_ids": [],
        },
    )


def _note(client: TestClient, child_ids: list[int], body: str):
    return client.post(
        "/api/families/current/day-notes",
        json={"on_date": shanghai_today().isoformat(), "body": body, "child_ids": child_ids},
    )


def _js(expr: str):
    node = shutil.which("node")
    assert node
    script = "const m = require(process.argv[1]); process.stdout.write(JSON.stringify(" + expr + "));"
    completed = subprocess.run(
        [node, "-e", script, str(Path("app/static/timeline_child_filter.js").resolve())],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _selected(html: str) -> str:
    for chunk in html.split("<a "):
        tag = chunk.split(">", 1)[0]
        if "ntag" not in tag or " on" not in tag or 'data-who="' not in tag:
            continue
        return tag.split('data-who="', 1)[1].split('"', 1)[0]
    raise AssertionError("selected name tag missing")


def _seen_count() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(TimelineLastSeen)) or 0)


def test_copy_banned_words_zero():
    assert COPY == PRODUCT
    assert COPY is TIMELINE_CHILD_FILTER
    assert ANNO == ANNO_EXPECTED
    assert len(COPY) == 41
    assert len(ANNO) == 8
    assert len(COPY) + len(ANNO) == 49
    assert set(ANNO).isdisjoint(COPY)
    blob = "\n".join(COPY.values())
    for word in BANNED:
        assert word not in blob, word
    for key, value in ANNO.items():
        assert key.startswith("anno_")
        assert value
    for path in SURFACES:
        text = path.read_text(encoding="utf-8")
        for word in BANNED:
            assert word not in text, f"{word} in {path}"
        if path.name != "timeline_child_filter_copy.py":
            assert "anno_" not in text
    templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            Path("app/templates/fragments/stage_inner.html"),
            Path("app/templates/timeline.html"),
            Path("app/templates/no_child.html"),
            Path("app/templates/add_child.html"),
        )
    )
    for key in HIDDEN_KEYS:
        assert COPY[key] not in templates
    for key in ANNO:
        assert key not in templates
        assert ANNO[key] not in templates
    script = Path("app/static/timeline_child_filter.js").read_text(encoding="utf-8")
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in script)
    for needle in ("微信", "wechat", "miniprogram", "小程序"):
        assert needle.lower() not in script.lower()
        for path in (
            Path("app/timeline_child_filter_copy.py"),
            Path("app/services/timeline_child_filter.py"),
            Path("app/templates/fragments/stage_inner.html"),
            Path("app/templates/no_child.html"),
        ):
            assert needle.lower() not in path.read_text(encoding="utf-8").lower()
    layout = Path("app/templates/layout.html").read_text(encoding="utf-8")
    assert layout.count('class="tab{') == 3
    assert 'src="/static/timeline_child_filter.js"' in layout


def test_copy_no_filter_jargon():
    blob = "\n".join(path.read_text(encoding="utf-8") for path in SURFACES)
    assert "筛选器" not in blob
    assert "过滤器" not in blob
    assert "筛选器" not in "\n".join(COPY.values())
    assert "过滤器" not in "\n".join(COPY.values())


def test_no_new_tab_or_second_timeline():
    layout = Path("app/templates/layout.html").read_text(encoding="utf-8")
    assert layout.count('class="tab{') == 3
    assert [COPY[key] for key in ("tab_time", "tab_family", "tab_settings")] == ["时光", "家人", "设置"]
    pages = Path("app/routers/pages.py").read_text(encoding="utf-8")
    assert pages.count('@router.get("/")') == 1
    assert '@router.get("/timeline")' not in pages
    assert "wechat" not in pages.lower()
    assert "miniprogram" not in pages.lower()
    css = Path("app/static/app.css").read_text(encoding="utf-8")
    assert "@media (min-width: 1024px)" in css
    assert ".app:has(.who-strip)" in css
    assert ".app:has(.feed)" in css
    assert "min(720px, 100%)" in css
    assert "child-sidebar" not in css
    assert "wn-rail-side" not in css
    stage = Path("app/templates/fragments/stage_inner.html").read_text(encoding="utf-8")
    assert 'class="who-strip"' in stage
    assert "child-sidebar" not in stage


def test_who_strip_renders_family_and_kids(client: TestClient):
    elder = _family(client, "tcf-strip@example.com", "妈妈", "名签家", child="果果")
    younger = _add(client, "豆豆")
    page = client.get("/")
    assert page.status_code == 200
    assert COPY["who_label"] in page.text
    assert COPY["who_all"] in page.text
    assert "果果" in page.text and "豆豆" in page.text
    assert 'class="who-strip"' in page.text
    assert _selected(page.text) == "all"
    assert 'data-family-view="1"' in page.text
    assert f'data-who="{elder}"' in page.text
    assert f'data-who="{younger}"' in page.text
    assert 'href="/?child=' in page.text
    one = client.get(f"/?child={younger}")
    assert _selected(one.text) == str(younger)
    assert "豆豆的时光" in one.text


def test_child_filter_select_kid_filters_feed(client: TestClient):
    elder = _family(client, "tcf-feed@example.com", "妈妈", "过滤家", child="果果")
    younger = _add(client, "豆豆")
    photo = upload(client, kind="child", child_id=elder, taken_on="2026-10-22")
    assert photo.status_code == 201, photo.text
    photo_id = photo.json()["id"]
    only_elder = _journal(client, [elder], "只写给果果的句子ZZ")
    only_younger = _journal(client, [younger], "只写给豆豆的句子ZZ")
    assert only_elder.status_code == only_younger.status_code == 201
    note = _note(client, [elder], "果果专属一句ZZ")
    assert note.status_code == 201, note.text
    shared = _video(client, [elder, younger])
    assert shared.status_code == 201, shared.text
    self_photo = upload(client, kind="self", send_child=False, taken_on="2026-10-23")
    assert self_photo.status_code == 201, self_photo.text
    self_id = self_photo.json()["id"]

    elder_page = client.get(f"/?child={elder}")
    younger_page = client.get(f"/?child={younger}")
    assert f"/media/{photo_id}" in elder_page.text
    assert "只写给果果的句子ZZ" in elder_page.text
    assert "果果专属一句ZZ" in elder_page.text
    assert elder_page.text.count('class="vcard') == 1
    assert "只写给豆豆的句子ZZ" not in elder_page.text
    assert f"/media/{self_id}" not in elder_page.text
    assert "只写给豆豆的句子ZZ" in younger_page.text
    assert younger_page.text.count('class="vcard') == 1
    assert f"/media/{photo_id}" not in younger_page.text
    assert "只写给果果的句子ZZ" not in younger_page.text
    assert "果果专属一句ZZ" not in younger_page.text
    assert f"/media/{self_id}" not in younger_page.text

    fallback = upload(client, kind="child", child_id=elder, taken_on="2026-10-21")
    assert fallback.status_code == 201
    fallback_id = fallback.json()["id"]
    with SessionLocal() as session:
        session.execute(delete(PhotoChild).where(PhotoChild.photo_id == fallback_id))
        session.commit()
    kept = client.get(f"/?child={elder}")
    assert f"/media/{fallback_id}" in kept.text
    assert f"/media/{fallback_id}" not in client.get(f"/?child={younger}").text


def test_child_filter_family_restores_mixed(client: TestClient):
    elder = _family(client, "tcf-mix@example.com", "妈妈", "混线家", child="果果")
    younger = _add(client, "豆豆")
    assert _journal(client, [elder], "果果单独一句").status_code == 201
    assert _journal(client, [younger], "豆豆单独一句").status_code == 201
    assert _video(client, [elder, younger]).status_code == 201
    self_photo = upload(client, kind="self", send_child=False, taken_on=shanghai_today().isoformat())
    assert self_photo.status_code == 201
    family = client.get("/")
    assert family.status_code == 200
    assert _selected(family.text) == "all"
    assert "果果单独一句" in family.text
    assert "豆豆单独一句" in family.text
    assert family.text.count('class="vcard') == 1
    assert f"/media/{self_photo.json()['id']}" not in family.text
    assert f"/media/{self_photo.json()['id']}" not in client.get(f"/?child={elder}").text
    again = client.get("/?view=family")
    assert _selected(again.text) == "all"
    assert again.text.count('class="jcard"') == 2
    fragment = client.get("/fragments/timeline?view=family")
    assert 'data-family-view="1"' in fragment.text
    assert "果果单独一句" in fragment.text and "豆豆单独一句" in fragment.text


def test_child_filter_empty_state_copy(client: TestClient):
    _family(client, "tcf-empty@example.com", "妈妈", "空态家", child="果果")
    quiet = _add(client, "安安", "2023-01-02")
    assert _journal(client, [client.get("/api/children").json()["children"][0]["id"]], "果果有一句").status_code == 201
    page = client.get(f"/?child={quiet}")
    assert page.status_code == 200
    assert "还没有安安的记录" in page.text
    assert "家里给安安传了照片、日志或视频，会出现在这里。" in page.text
    assert COPY["empty_back"] in page.text
    assert 'data-who-empty="1"' in page.text
    assert 'class="pcard"' not in page.text
    assert 'class="jcard"' not in page.text
    assert 'class="vcard' not in page.text
    assert "果果有一句" not in page.text
    back = client.get("/")
    assert "果果有一句" in back.text
    assert "还没有安安的记录" not in back.text


def test_child_filter_no_kids(client: TestClient):
    auth(client, "tcf-nokid-admin@example.com", "妈妈")
    assert client.post("/api/families", json={"name": "无孩家"}).status_code == 201
    viewer = _invite(client, "tcf-nokid-v@example.com", "viewer", "奶奶")
    admin = client.get("/")
    assert admin.status_code == 200
    assert "添加孩子" in admin.text
    assert COPY["no_kids_hint"] in admin.text
    assert COPY["no_kids_sub"] in admin.text
    assert 'class="who-strip"' not in admin.text
    guest = viewer.get("/")
    assert guest.status_code == 200
    assert COPY["no_kids_hint"] in guest.text
    assert 'class="who-strip"' not in guest.text
    assert 'class="fab"' not in guest.text
    assert 'class="write-pill"' not in guest.text
    assert "/upload" not in guest.text


def test_removed_child_not_in_who_strip(client: TestClient):
    elder = _family(client, "tcf-gone@example.com", "妈妈", "移出孩子家", child="果果")
    gone = _add(client, "安安移出")
    session = SessionLocal()
    try:
        row = session.get(Child, gone)
        assert row is not None
        session.delete(row)
        session.commit()
    finally:
        session.close()
    page = client.get("/")
    assert page.status_code == 200
    assert "安安移出" not in page.text
    assert f'data-who="{gone}"' not in page.text
    assert f'data-who="{elder}"' in page.text
    stale = client.get(f"/?child={gone}")
    assert stale.status_code == 200
    assert _selected(stale.text) == "all"
    assert "安安移出" not in stale.text
    resolved = client.get(f"/api/families/current/timeline-filter?child={gone}")
    assert resolved.status_code == 200
    assert resolved.json() == {"scope": "family", "child_id": None}


def test_viewer_can_filter_no_write_cta(client: TestClient):
    elder = _family(client, "tcf-ro@example.com", "妈妈", "只读筛家", child="果果")
    younger = _add(client, "豆豆")
    assert _journal(client, [elder], "长辈也能看见").status_code == 201
    viewer = _invite(client, "tcf-ro-v@example.com", "viewer", "奶奶")
    uploader = _invite(client, "tcf-ro-u@example.com", "uploader", "爸爸")
    page = viewer.get(f"/?child={elder}")
    assert page.status_code == 200
    assert _selected(page.text) == str(elder)
    assert "长辈也能看见" in page.text
    assert COPY["who_label"] in page.text
    assert COPY["ro_meta"] in page.text
    assert 'class="write-pill"' not in page.text
    assert 'class="fab"' not in page.text
    assert "/upload" not in page.text
    assert "/journals/new" not in page.text
    assert "/videos/new" not in page.text
    assert "/legacy-import" not in page.text
    switched = viewer.get(f"/fragments/timeline?child={younger}")
    assert switched.status_code == 200
    assert _selected(switched.text) == str(younger)
    assert 'class="write-pill"' not in switched.text
    assert uploader.get(f"/?child={elder}").status_code == 200
    assert "长辈也能看见" in uploader.get(f"/?child={elder}").text
    assert 'class="write-pill"' in client.get(f"/?child={elder}").text


def test_topcards_hidden_when_child_empty(client: TestClient):
    elder = _family(client, "tcf-top@example.com", "妈妈", "顶卡家", child="果果")
    younger = _add(client, "豆豆")
    quiet = _add(client, "安安", "2023-04-04")
    target = previous_year_date(shanghai_today())
    assert target is not None
    last_year = upload(client, kind="child", child_id=elder, taken_on=target.isoformat())
    assert last_year.status_code == 201, last_year.text
    assert _journal(client, [younger], "豆豆今天的句子").status_code == 201
    elder_page = client.get(f"/?child={elder}")
    younger_page = client.get(f"/?child={younger}")
    quiet_page = client.get(f"/?child={quiet}")
    family = client.get("/")
    assert 'class="amber-card"' in elder_page.text
    assert 'class="wn-slip"' in elder_page.text
    assert "豆豆今天的句子" not in elder_page.text
    assert 'class="amber-card"' not in younger_page.text
    assert "豆豆今天的句子" in younger_page.text
    assert 'class="wn-slip"' in younger_page.text
    assert 'class="amber-card"' not in quiet_page.text
    assert 'class="wn-slip"' not in quiet_page.text
    assert 'class="pcard"' not in quiet_page.text
    assert 'class="jcard"' not in quiet_page.text
    assert 'data-who-empty="1"' in quiet_page.text
    assert 'class="amber-card"' in family.text
    assert 'class="wn-slip"' in family.text
    assert "豆豆今天的句子" in family.text
    public = client.get("/api/families/current/whats-new")
    assert public.status_code == 200
    assert public.json()["has_updates"] is True
    assert _seen_count() == 0


def test_child_filter_query_persists_on_refresh(client: TestClient):
    elder = _family(client, "tcf-url@example.com", "妈妈", "深链家", child="果果")
    assert _journal(client, [elder], "刷新还在").status_code == 201
    first = client.get(f"/?child={elder}")
    second = client.get(f"/?child={elder}")
    assert first.status_code == second.status_code == 200
    assert _selected(first.text) == _selected(second.text) == str(elder)
    assert "刷新还在" in second.text
    assert "果果的时光" in second.text
    assert _seen_count() == 0


def test_child_filter_unknown_id_falls_back_family(client: TestClient):
    elder = _family(client, "tcf-unk@example.com", "妈妈", "本家筛", child="果果")
    secret = "别家秘密孩子"
    other = TestClient(app)
    foreign = _family(other, "tcf-unk-b@example.com", "别人", "别家筛", child=secret)
    page = client.get(f"/?child={foreign}")
    assert page.status_code == 200
    assert _selected(page.text) == "all"
    assert secret not in page.text
    assert "别家筛" not in page.text
    missing = client.get("/?child=999999")
    assert missing.status_code == 200
    assert _selected(missing.text) == "all"
    probed = client.get(f"/api/families/current/timeline-filter?child={foreign}")
    assert probed.status_code == 200
    assert probed.json() == {"scope": "family", "child_id": None}
    assert secret not in probed.text
    own = client.get(f"/api/families/current/timeline-filter?child={elder}")
    assert own.json() == {"scope": "child", "child_id": elder}
    assert elder


def test_child_filter_invalid_param_422_or_fallback(client: TestClient):
    elder = _family(client, "tcf-bad@example.com", "妈妈", "乱参家", child="果果")
    page = client.get("/?child=not-a-number")
    assert page.status_code == 200
    assert _selected(page.text) == "all"
    assert "not-a-number" not in page.text
    assert "invalid_child" not in page.text
    blank = client.get("/?child=")
    assert blank.status_code == 200
    assert _selected(blank.text) == "all"
    zero = client.get("/?child=0")
    assert zero.status_code == 200
    assert _selected(zero.text) == "all"
    api = client.get("/api/families/current/timeline-filter?child=not-a-number")
    assert api.status_code == 422
    assert api.json() == {"detail": "孩子参数无效", "code": "invalid_child"}
    assert "not-a-number" not in api.text
    assert "果果" not in api.text
    whole = client.get("/api/families/current/timeline-filter?child=all")
    assert whole.status_code == 200
    assert whole.json() == {"scope": "family", "child_id": None}
    absent = client.get("/api/families/current/timeline-filter")
    assert absent.json() == {"scope": "family", "child_id": None}
    assert elder


def test_outsider_timeline_child_filter_403_no_code(client: TestClient):
    child_id = _family(client, "tcf-home@example.com", "妈妈", "哨兵家庭名", child="哨兵孩子名")
    outsider = TestClient(app)
    auth(outsider, "tcf-out@example.com", "路人")
    page = outsider.get(f"/?child={child_id}")
    assert page.status_code == 403
    assert "没有访问权限" in page.text
    assert "code" not in page.text
    assert "哨兵家庭名" not in page.text
    assert "哨兵孩子名" not in page.text
    api = outsider.get(f"/api/families/current/timeline-filter?child={child_id}")
    assert api.status_code == 403
    assert api.json() == {"detail": FORBIDDEN}
    assert "code" not in api.json()
    assert "哨兵孩子名" not in api.text
    naked = TestClient(app)
    anon = naked.get(f"/?child={child_id}")
    assert anon.status_code == 403
    assert "没有访问权限" in anon.text
    assert "哨兵孩子名" not in anon.text
    anon_api = naked.get("/api/families/current/timeline-filter?child=1")
    assert anon_api.status_code == 403
    assert "code" not in anon_api.json()


def test_child_filter_503_no_crash(client: TestClient, monkeypatch):
    _family(client, "tcf-busy@example.com", "妈妈", "忙家", child="果果")

    def busy() -> None:
        raise TimelineFilterBusy()

    monkeypatch.setattr("app.routers.pages.raise_if_busy", busy)
    monkeypatch.setattr("app.routers.family.raise_if_busy", busy)
    with TestClient(app, raise_server_exceptions=False) as quiet:
        quiet.cookies.update(client.cookies)
        page = quiet.get("/")
        fragment = quiet.get("/fragments/timeline?child=1")
        api = quiet.get("/api/families/current/timeline-filter")
        health = quiet.get("/healthz")
    assert page.status_code == 503
    assert "服务忙" in page.text
    assert "重新加载" in page.text
    assert "Traceback" not in page.text
    assert fragment.status_code == 503
    assert "Traceback" not in fragment.text
    assert api.status_code == 503
    assert api.json() == {"detail": "服务忙"}
    assert health.status_code == 200
    assert health.json()["ok"] is True


def test_child_filter_offline_no_fake_success():
    offline = _js("m.decideFilter(0, null, {})")
    missing = _js("m.decideFilter(null, null, {})")
    assert offline["pretend"] is False
    assert offline["apply"] is False
    assert offline["retry"] is True
    assert offline["crash"] is False
    assert offline["kind"] == "offline"
    assert missing["kind"] == "offline"
    assert missing["pretend"] is False
    busy = _js("m.decideFilter(503, {detail:'busy'}, {})")
    assert busy["kind"] == "busy" and busy["retry"] is True and busy["crash"] is False and busy["apply"] is False


def test_child_filter_cancel_switch_ignores_stale():
    stale = _js("m.decideFilter(200, {scope:'child'}, {generation:1, currentGeneration:2})")
    fresh = _js("m.decideFilter(200, {scope:'child'}, {generation:4, currentGeneration:4})")
    assert stale["ignore"] is True
    assert stale["apply"] is False
    assert stale["kind"] == "late"
    assert fresh["apply"] is True
    assert fresh["ignore"] is False
    assert fresh["kind"] == "ok"


def test_child_filter_late_response_ignored():
    late = _js("m.decideFilter(200, {scope:'family'}, {left:true, generation:3, currentGeneration:3})")
    assert late["ignore"] is True
    assert late["apply"] is False
    assert late["pretend"] is False
    assert _js("m.acceptLate(3, 3)") is True
    assert _js("m.acceptLate(3, 4)") is False


def test_js_404_without_code_retryable_child_filter():
    rolling = _js("m.decideFilter(404, {detail:'missing'}, {})")
    assert rolling["kind"] == "rolling"
    assert rolling["retry"] is True
    assert rolling["crash"] is False
    assert rolling["apply"] is False
    coded = _js("m.decideFilter(404, {detail:'missing', code:'gone'}, {})")
    assert coded["kind"] == "missing"
    assert coded["retry"] is False
    invalid = _js("m.decideFilter(422, {code:'invalid_child'}, {})")
    assert invalid["kind"] == "invalid"
    assert invalid["fallbackFamily"] is True
    assert invalid["apply"] is False
    denied = _js("m.decideFilter(403, {detail:'no'}, {})")
    assert denied["kind"] == "forbidden"
    assert denied["apply"] is False
    assert denied["retry"] is False


def test_child_filter_multi_tab_independent(client: TestClient):
    elder = _family(client, "tcf-tabs@example.com", "妈妈", "多页家", child="果果")
    younger = _add(client, "豆豆")
    assert _journal(client, [elder], "左页果果").status_code == 201
    other = TestClient(app)
    other.cookies.update(client.cookies)
    left = client.get(f"/?child={elder}")
    right = other.get(f"/?child={younger}")
    assert "左页果果" in left.text
    assert _selected(left.text) == str(elder)
    assert "左页果果" not in right.text
    assert _selected(right.text) == str(younger)
    assert 'data-who-empty="1"' in right.text
    assert _seen_count() == 0
    columns = {col["name"] for col in inspect(engine).get_columns("timeline_last_seen")}
    assert columns == {"id", "user_id", "family_id", "last_seen_at"}
    assert {table.name for table in Base.metadata.sorted_tables} == EXPECTED_TABLES


def test_child_filter_no_new_tables():
    assert {table.name for table in Base.metadata.sorted_tables} == EXPECTED_TABLES
    source = Path("app/migrate_schema.py").read_text(encoding="utf-8")
    script = Path("scripts/migrate.py").read_text(encoding="utf-8")
    assert "v2026.10.25.8 does not add a table or a column" in source
    assert "timeline_child_filter ready (no new tables)" in script
    assert "DROP TABLE timeline" not in source
    assert "DROP TABLE timeline" not in script
    notes = Path("releases/v2026.10.25.8.md").read_text(encoding="utf-8")
    assert "121d31e51022586a5edc4b003bc07445" in notes
    assert "4fa54c784102fa0dee94a44e7f4e60cc" in notes
    assert "1220fe5a999e3650761f2ef2f001711e" in notes
    assert "e2944c5e" in notes
    assert "v2026.10.25.7" in notes
    assert "down -v" in notes
    assert app.version == "2026.10.25.9"
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.split("## v2026.10.25.7", 1)[0].startswith("# Changelog\n\n## v2026.10.25.9")
