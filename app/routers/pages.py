"""HTML screens aligned with the final IA: 时光 / 家人 / 设置."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import set_session
from app.constants import ELDERS_NO_APP, FORBIDDEN, JOURNAL, PHOTO_MULTI_CHILD, ROLE_LABEL, ROLE_NOTE, VIDEO
from app.db import get_db
from app.deps import MemberCtx, optional_user
from app.http import client_ip, safe_next, toast_text
from app.models import Photo, User
from app.services.accounts import get_family, get_membership, patch_me
from app.services.auth_codes import request_code, verify_code
from app.services.children import (
    build_timeline,
    child_in_family,
    create_child,
    list_children,
    require_child,
)
from app.services.day_notes import day_page_state
from app.services.journals import build_family_feed, build_feed
from app.services.on_this_day import year_card, year_meta_for
from app.services.timeline_child_filter import (
    TimelineFilterBusy,
    child_token_probes,
    raise_if_busy,
    resolve_timeline_child,
    view_copy,
)
from app.services.whats_new import build_slip
from app.services.clock import date_label, shanghai_today, weekday_zh
from app.services.elders import EldersError, greeting_for, invite_absolute, normalize_access_url
from app.services.families import (
    accept_invite,
    create_family,
    create_invite,
    invite_view,
    list_members,
    set_role,
)
from app.services.assoc import children_payload, linked_children, parse_form_child_ids, photo_child_ids
from app.services.photos import PhotoError, can_edit_photo, create_photo, self_album
from app.setup_gate import note_child_upload
from app.services.validators import parse_iso_date
from app.templating import templates
from app.config import get_settings
from app.routers.photos import _read_limited

router = APIRouter(tags=["pages"])
logger = logging.getLogger("family_album.pages")
_EMPTY_SLIP = {
    "has_updates": False,
    "window": "fallback_7d",
    "last_seen_at": None,
    "corner": "",
    "rows": [],
}


def render(request: Request, name: str, context: dict, status_code: int = 200):
    payload = {
        "show_nav": False,
        "show_fab": False,
        "fab_href": "/upload",
        "fab_family": False,
        "tab": None,
        "toast": None,
        "can_upload": False,
        "is_admin": False,
        "user": None,
        "membership": None,
    }
    payload.update(context)
    return templates.TemplateResponse(request, name, payload, status_code=status_code)


def _flags(user: User | None, membership) -> dict:
    role = membership.role if membership is not None else None
    return {
        "user": user,
        "membership": membership,
        "can_upload": role in {"admin", "uploader"},
        "is_admin": role == "admin",
        "role_label": ROLE_LABEL.get(role or "", ""),
        "role_note": ROLE_NOTE.get(role or "", ""),
    }


def gate_member(request: Request, db: Session) -> MemberCtx | RedirectResponse:
    user = optional_user(request, db)
    if user is None:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    if not user.name:
        response = RedirectResponse("/profile", status_code=303)
        if request.url.path.startswith("/fragments/"):
            response.headers["HX-Redirect"] = "/profile"
        return response
    membership = get_membership(db, user.id)
    if membership is None:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    family = get_family(db, membership.family_id)
    if family is None:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    return MemberCtx(user=user, membership=membership, family=family)


def _login_ctx(request: Request, *, error: str | None = None, email: str = "", nxt: str = "/") -> dict:
    return {
        "error": error,
        "email": email,
        "next": nxt,
        "test_code_enabled": bool(get_settings().auth_test_code),
        "toast": toast_text(request.query_params.get("toast")),
    }


def _who_choice(db, family_id: int, raw: str | None):
    """Busy becomes 503. A bad child id is the mixed feed, not a different status."""
    try:
        raise_if_busy()
    except TimelineFilterBusy as exc:
        raise HTTPException(status_code=503, detail="服务忙") from exc
    _scope, current = resolve_timeline_child(db, family_id, raw)
    return current is None, current, view_copy(current)


@router.get("/")
def home(
    request: Request,
    child: str | None = None,
    toast: str | None = None,
    db: Session = Depends(get_db),
):
    user = optional_user(request, db)
    nxt = safe_next(request.query_params.get("next"))
    if user is None:
        if child_token_probes(child):
            raise HTTPException(status_code=403, detail=FORBIDDEN)
        return render(request, "login.html", _login_ctx(request, nxt=nxt))
    if not user.name:
        return render(request, "profile.html", {**_flags(user, get_membership(db, user.id)), "error": None, "next": nxt})
    if nxt.startswith("/invites/"):
        return RedirectResponse(nxt, status_code=303)
    membership = get_membership(db, user.id)
    if membership is None:
        if child_token_probes(child):
            raise HTTPException(status_code=403, detail=FORBIDDEN)
        return render(
            request,
            "family_empty.html",
            {**_flags(user, None), "error": None, "toast": toast_text(toast)},
        )
    family = get_family(db, membership.family_id)
    if family is None:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    children = list_children(db, family.id)
    flags = _flags(user, membership)
    if not children:
        if membership.role == "admin":
            return render(
                request,
                "add_child.html",
                {**flags, "error": None, "toast": toast_text(toast), "show_nav": True, "tab": "timeline"},
            )
        return render(
            request,
            "no_child.html",
            {**flags, "toast": toast_text(toast), "show_nav": True, "tab": "timeline"},
        )
    if request.query_params.get("view") == "family" and children:
        _who_choice(db, family.id, None)
        return _timeline_response(request, db, user, membership, family, children, None, toast, family_view=True)
    family_view, current, who = _who_choice(db, family.id, child)
    return _timeline_response(
        request,
        db,
        user,
        membership,
        family,
        children,
        current,
        toast,
        family_view=family_view,
        who=who,
    )


def _video_toast(params) -> str | None:
    kind = params.get("vtoast")
    if kind == "deleted":
        return VIDEO["toast_deleted"]
    if kind == "posted":
        vdate = params.get("vdate") or ""
        if re.fullmatch(r"\d{1,2}月\d{1,2}日", vdate):
            return VIDEO["toast_posted"].format(date=vdate)
    return None


def _journal_toast(params) -> str | None:
    kind = params.get("jtoast")
    if kind == "edited":
        return JOURNAL["toast_edited"]
    if kind == "deleted":
        return JOURNAL["toast_deleted"]
    if kind == "posted":
        jdate = params.get("jdate") or ""
        if re.fullmatch(r"\d{1,2}月\d{1,2}日", jdate):
            return JOURNAL["toast_posted"].format(date=jdate)
    return None


def _photo_toast(params) -> str | None:
    kind = params.get("toast")
    if kind == "photo_edited":
        return PHOTO_MULTI_CHILD["toast_edited"]
    if kind == "photo_posted":
        pdate = params.get("pdate") or ""
        if re.fullmatch(r"\d{1,2}月\d{1,2}日", pdate):
            return PHOTO_MULTI_CHILD["toast_posted"].format(date=pdate)
    return None


def _slip(db, user, family, current, family_view: bool) -> dict:
    # Family view passes no selected child. The day link then follows whoever the new item belongs to.
    # A slip failure leaves the year card and the rest of the timeline in place.
    fallback = None if current is None else current.id
    prefer = None if family_view or current is None else current.id
    try:
        return build_slip(
            db,
            user_id=user.id,
            family_id=family.id,
            prefer_child_id=prefer,
            fallback_child_id=fallback,
        )
    except Exception as exc:
        logger.warning("whats-new slip skipped error=%s", type(exc).__name__)
        return dict(_EMPTY_SLIP)


def _year_card(timeline, current, children, family_view: bool):
    prefer = None if family_view or current is None else current.id
    if current is not None:
        fallback = current.id
    elif children:
        fallback = children[0].id
    else:
        fallback = None
    return year_card(timeline, prefer_child_id=prefer, fallback_child_id=fallback)


def _timeline_response(
    request,
    db,
    user,
    membership,
    family,
    children,
    current,
    toast,
    family_view=False,
    who=None,
):
    flags = _flags(user, membership)
    if who is None:
        who = view_copy(None if family_view else current)
    if family_view:
        timeline = build_family_feed(db, family.id)
        timeline["child"]["name"] = family.name
    else:
        timeline = build_feed(db, current)
    write_child = "" if family_view else str(current.id)
    return render(
        request,
        "timeline.html",
        {
            **flags,
            "family": family,
            "children": children,
            "child": current,
            "timeline": timeline,
            "family_view": family_view,
            "who": who,
            "write_child": write_child,
            "show_nav": True,
            "show_fab": flags["can_upload"],
            "fab_menu": flags["can_upload"],
            "fab_href": "/upload" if family_view else f"/upload?child={current.id}",
            "tab": "timeline",
            "highlight_video": request.query_params.get("vhl") or "",
            "toast": _photo_toast(request.query_params)
            or _video_toast(request.query_params)
            or _journal_toast(request.query_params)
            or toast_text(toast),
            "wn": _slip(
                db,
                user,
                family,
                current if current is not None else (children[0] if children else None),
                family_view,
            ),
            "otd_card": _year_card(timeline, current, children, family_view),
        },
    )


@router.get("/fragments/timeline")
def timeline_fragment(
    request: Request,
    child: str | None = None,
    db: Session = Depends(get_db),
):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    children = list_children(db, gate.family.id)
    flags = _flags(gate.user, gate.membership)
    raw_child = None if request.query_params.get("view") == "family" else child
    family_view, found, who = _who_choice(db, gate.family.id, raw_child)
    if family_view and not children:
        family_view = True
        found = None
        timeline = {
            "child": {"id": None, "name": "", "birth_date": "", "age_label": "", "photo_count": 0},
            "days": [],
            "video_count": 0,
            "family_view": True,
        }
    elif family_view:
        timeline = build_family_feed(db, gate.family.id)
        timeline["child"]["name"] = gate.family.name
    else:
        timeline = build_feed(db, found)
    return render(
        request,
        "fragments/timeline_swap.html",
        {
            **flags,
            "family": gate.family,
            "children": children,
            "child": found,
            "timeline": timeline,
            "family_view": family_view,
            "who": who,
            "write_child": "" if family_view else str(found.id),
            "highlight_video": request.query_params.get("vhl") or "",
            "wn": _slip(
                db,
                gate.user,
                gate.family,
                found if found is not None else (children[0] if children else None),
                family_view,
            ),
            "otd_card": _year_card(timeline, found, children, family_view),
        },
    )


@router.get("/login/code")
def login_code(request: Request, email: str = "", db: Session = Depends(get_db)):
    if optional_user(request, db) is not None:
        return RedirectResponse("/", status_code=303)
    nxt = safe_next(request.query_params.get("next"))
    return render(request, "login_code.html", _login_ctx(request, email=email, nxt=nxt))


@router.post("/login/request")
def login_request(
    request: Request,
    email: str = Form(""),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    nxt = safe_next(next)
    try:
        request_code(db, email, ip=client_ip(request))
    except HTTPException as exc:
        return render(
            request,
            "login.html",
            _login_ctx(request, error=str(exc.detail), email=email, nxt=nxt),
            status_code=exc.status_code,
        )
    from app.services.validators import normalize_email

    cleaned = normalize_email(email)
    location = "/login/code?email=" + quote(cleaned) + "&next=" + quote(nxt, safe="")
    return RedirectResponse(location, status_code=303)


@router.post("/login/verify")
def login_verify(
    request: Request,
    email: str = Form(""),
    code: str = Form(""),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    nxt = safe_next(next)
    try:
        user = verify_code(db, email, code, ip=client_ip(request))
    except HTTPException as exc:
        return render(
            request,
            "login_code.html",
            _login_ctx(request, error=str(exc.detail), email=email, nxt=nxt),
            status_code=exc.status_code,
        )
    dest = nxt
    if not user.name and not dest.startswith("/invites/"):
        dest = "/profile?next=" + quote(dest, safe="")
    response = RedirectResponse(dest, status_code=303)
    set_session(response, user.id)
    return response


@router.get("/profile")
def profile_page(request: Request, db: Session = Depends(get_db)):
    user = optional_user(request, db)
    if user is None:
        return render(request, "login.html", _login_ctx(request, nxt=safe_next(request.query_params.get("next"))))
    membership = get_membership(db, user.id)
    return render(
        request,
        "profile.html",
        {
            **_flags(user, membership),
            "error": None,
            "next": safe_next(request.query_params.get("next")),
            "toast": toast_text(request.query_params.get("toast")),
            "show_nav": bool(user.name and membership),
            "tab": "settings",
        },
    )


@router.post("/profile")
def profile_save(
    request: Request,
    name: str = Form(""),
    relation_label: str = Form(""),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    user = optional_user(request, db)
    if user is None:
        return render(request, "login.html", _login_ctx(request))
    membership = get_membership(db, user.id)
    fields = {"name"}
    if membership is not None:
        fields.add("relation_label")
    try:
        patch_me(db, user, fields, name=name, relation_label=relation_label)
    except HTTPException as exc:
        return render(
            request,
            "profile.html",
            {**_flags(user, membership), "error": str(exc.detail), "next": safe_next(next)},
            status_code=exc.status_code,
        )
    dest = safe_next(next)
    if "toast=" not in dest:
        join = "&" if "?" in dest else "?"
        dest = f"{dest}{join}toast=profile"
    return RedirectResponse(dest, status_code=303)


@router.post("/families")
def family_save(request: Request, name: str = Form(""), db: Session = Depends(get_db)):
    user = optional_user(request, db)
    if user is None:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    if not user.name:
        return RedirectResponse("/profile", status_code=303)
    try:
        create_family(db, user, name)
    except HTTPException as exc:
        if get_membership(db, user.id) is not None and exc.status_code == 400:
            return RedirectResponse("/", status_code=303)
        return render(
            request,
            "family_empty.html",
            {**_flags(user, None), "error": str(exc.detail)},
            status_code=exc.status_code,
        )
    return RedirectResponse("/", status_code=303)


@router.post("/children")
def child_save(
    request: Request,
    name: str = Form(""),
    birth_date: str = Form(""),
    relation_label: str = Form(""),
    db: Session = Depends(get_db),
):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    flags = _flags(gate.user, gate.membership)
    if gate.membership.role != "admin":
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    if len(relation_label.strip()) > 40:
        return render(
            request,
            "add_child.html",
            {**flags, "error": "关系需要不超过 40 个字", "show_nav": True, "tab": "settings"},
            status_code=400,
        )
    try:
        child = create_child(db, family_id=gate.family.id, name_raw=name, birth_raw=birth_date)
    except HTTPException as exc:
        return render(
            request,
            "add_child.html",
            {**flags, "error": str(exc.detail), "show_nav": True, "tab": "settings"},
            status_code=exc.status_code,
        )
    if relation_label.strip():
        patch_me(
            db,
            gate.user,
            {"relation_label"},
            name=None,
            relation_label=relation_label,
        )
    return RedirectResponse(f"/?child={child.id}", status_code=303)


@router.get("/children/new")
def new_child(request: Request, db: Session = Depends(get_db)):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    if gate.membership.role != "admin":
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    return render(
        request,
        "add_child.html",
        {**_flags(gate.user, gate.membership), "error": None, "show_nav": True, "tab": "settings"},
    )


@router.get("/family/members")
def members_page(request: Request, toast: str | None = None, db: Session = Depends(get_db)):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    return _members(request, db, gate, toast=toast_text(toast))


def _members(request, db, gate: MemberCtx, *, error: str | None = None, toast: str | None = None, status_code: int = 200):
    data = list_members(db, family_id=gate.family.id, include_invites=gate.membership.role == "admin")
    admin_count = sum(1 for item in data["members"] if item["role"] == "admin")
    entry_url = ""
    if gate.membership.role == "admin":
        entry_url = (gate.family.family_access_url or "").strip()
        for item in data["members"]:
            if item["role"] == "viewer":
                item["greeting"] = greeting_for(item.get("relation_label"))
        for item in data.get("invites", []):
            item["greeting"] = greeting_for(None)
            item["invite_url"] = invite_absolute(entry_url, item["token"]) if entry_url else ""
    return render(
        request,
        "members.html",
        {
            **_flags(gate.user, gate.membership),
            "family": gate.family,
            "members": data["members"],
            "invites": data.get("invites", []),
            "admin_count": admin_count,
            "role_labels": ROLE_LABEL,
            "role_notes": ROLE_NOTE,
            "entry_url": entry_url,
            "error": error,
            "show_nav": True,
            "tab": "settings",
            "toast": toast,
        },
        status_code=status_code,
    )


@router.post("/family/invites")
def members_invite(
    request: Request,
    email: str = Form(""),
    role: str = Form("viewer"),
    db: Session = Depends(get_db),
):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    if gate.membership.role != "admin":
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    try:
        create_invite(db, family_id=gate.family.id, created_by=gate.user.id, email_raw=email, role=role)
    except HTTPException as exc:
        return _members(request, db, gate, error=str(exc.detail), status_code=exc.status_code)
    return RedirectResponse("/family/members?toast=invite", status_code=303)


@router.post("/family/members/{user_id}/role")
def members_role(
    user_id: int,
    request: Request,
    role: str = Form(""),
    db: Session = Depends(get_db),
):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    if gate.membership.role != "admin":
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    try:
        set_role(db, family_id=gate.family.id, user_id=user_id, role=role)
    except HTTPException as exc:
        return _members(request, db, gate, error=str(exc.detail), status_code=exc.status_code)
    return RedirectResponse("/family/members", status_code=303)


def _settings(request, db, gate: MemberCtx, *, toast: str | None = None, access_error: str | None = None, status_code: int = 200):
    children = list_children(db, gate.family.id)
    member_count = len(list_members(db, family_id=gate.family.id, include_invites=False)["members"])
    cards = []
    for child in children:
        timeline = build_timeline(db, child)
        cards.append({"child": child, "age_label": timeline["child"]["age_label"], "photo_count": timeline["child"]["photo_count"]})
    shown = toast_text(toast)
    if request.query_params.get("saved") == "url":
        shown = ELDERS_NO_APP["url_saved"]
    return render(
        request,
        "settings.html",
        {
            **_flags(gate.user, gate.membership),
            "family": gate.family,
            "cards": cards,
            "member_count": member_count,
            "show_nav": True,
            "tab": "settings",
            "toast": shown,
            "error": None,
            "access_error": access_error,
        },
        status_code=status_code,
    )


@router.get("/family/settings")
def settings_page(request: Request, toast: str | None = None, db: Session = Depends(get_db)):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    return _settings(request, db, gate, toast=toast)


@router.post("/family/settings/access-url")
def settings_access_url(
    request: Request,
    family_access_url: str = Form(""),
    db: Session = Depends(get_db),
):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    if gate.membership.role != "admin":
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    try:
        cleaned = normalize_access_url(family_access_url)
    except EldersError:
        return _settings(request, db, gate, access_error=ELDERS_NO_APP["url_help"], status_code=400)
    gate.family.family_access_url = cleaned
    db.commit()
    return RedirectResponse("/family/settings?saved=url", status_code=303)


@router.get("/people")
def people_page(request: Request, db: Session = Depends(get_db)):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    data = list_members(db, family_id=gate.family.id, include_invites=False)
    flags = _flags(gate.user, gate.membership)
    return render(
        request,
        "people.html",
        {
            **flags,
            "members": data["members"],
            "role_notes": ROLE_NOTE,
            "show_nav": True,
            "show_fab": flags["can_upload"],
            "fab_href": "/upload",
            "tab": "people",
            "toast": toast_text(request.query_params.get("toast")),
        },
    )


@router.get("/people/{user_id}/album")
def album_page(user_id: int, request: Request, toast: str | None = None, db: Session = Depends(get_db)):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    album = self_album(db, family_id=gate.family.id, user_id=user_id)
    if album is None:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    children = list_children(db, gate.family.id)
    flags = _flags(gate.user, gate.membership)
    return render(
        request,
        "album.html",
        {
            **flags,
            "album": album,
            "owner_is_me": user_id == gate.user.id,
            "child_names": [child.name for child in children],
            "show_nav": True,
            "show_fab": flags["can_upload"],
            "fab_href": "/upload?kind=self" if user_id == gate.user.id else "/upload",
            "fab_family": True,
            "tab": "people",
            "toast": toast_text(toast),
        },
    )


@router.get("/upload")
def upload_page(
    request: Request,
    kind: str | None = None,
    child: int | None = None,
    db: Session = Depends(get_db),
):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    if gate.membership.role not in {"admin", "uploader"}:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    return _upload_form(request, db, gate, kind=kind, child_id=child)


def _upload_form(
    request,
    db,
    gate: MemberCtx,
    *,
    kind: str | None,
    child_id: int | None,
    error: str | None = None,
    status_code: int = 200,
    mode: str = "new",
    photo: Photo | None = None,
    selected_override: list[int] | None = None,
):
    children = list_children(db, gate.family.id)
    chosen = (kind or "").strip().lower()
    if chosen not in {"child", "self"}:
        chosen = ""
    explicit = None
    if child_id is not None:
        explicit = child_in_family(db, family_id=gate.family.id, child_id=child_id)
    selected_ids: list[int] = []
    pre_name = ""
    if selected_override is not None:
        known = {child.id for child in children}
        selected_ids = [item for item in selected_override if item in known]
    elif chosen == "child":
        if explicit is not None:
            selected_ids = [explicit.id]
        elif len(children) == 1:
            selected_ids = [children[0].id]
    if chosen == "child" and len(children) > 1 and selected_ids and (explicit is not None or mode == "edit" or selected_override is not None):
        first = next(child for child in children if child.id == selected_ids[0])
        pre_name = first.name
    if chosen == "self":
        back = f"/people/{gate.user.id}/album"
    elif selected_ids:
        back = f"/?child={selected_ids[0]}"
    elif len(children) > 1:
        back = "/?view=family"
    else:
        back = "/"
    children_json = [
        {"id": child.id, "name": child.name, "birth": child.birth_date.isoformat()} for child in children
    ]
    flags = _flags(gate.user, gate.membership)
    return render(
        request,
        "upload.html",
        {
            **flags,
            "family": gate.family,
            "children": children,
            "selected_ids": selected_ids,
            "pre_name": pre_name,
            "query_child": explicit.id if explicit is not None else None,
            "kind": chosen,
            "mode": mode,
            "photo": photo,
            "today": shanghai_today().isoformat(),
            "error": error,
            "show_red": chosen == "child" and not selected_ids,
            "show_nav": False,
            "back_href": back,
            "children_json": json.dumps(children_json, ensure_ascii=True),
            "selected_json": json.dumps(selected_ids),
            "edit_date": date_label(photo.taken_on) if photo is not None else "",
        },
        status_code=status_code,
    )


@router.post("/upload")
async def upload_save(
    request: Request,
    kind: str = Form(""),
    child_id: str | None = Form(None),
    child_ids: list[str] | None = Form(None),
    taken_on: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    if gate.membership.role not in {"admin", "uploader"}:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    parsed_child = None
    if child_id and str(child_id).isdigit():
        parsed_child = int(child_id)
    override = parse_form_child_ids(child_ids)
    try:
        if file is None:
            raise HTTPException(status_code=400, detail="请上传照片")
        data = await _read_limited(file, request)
        photo = create_photo(
            db,
            family_id=gate.membership.family_id,
            uploader_id=gate.user.id,
            kind_raw=kind,
            child_id_raw=child_id,
            child_ids_raw=child_ids,
            taken_on_raw=taken_on,
            data=data,
        )
    except PhotoError as exc:
        return _upload_form(
            request,
            db,
            gate,
            kind=kind,
            child_id=parsed_child,
            error="没传上去，点重试",
            status_code=exc.status,
            selected_override=override,
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            raise
        return _upload_form(
            request,
            db,
            gate,
            kind=kind,
            child_id=parsed_child,
            error="没传上去，点重试",
            status_code=exc.status_code,
            selected_override=override,
        )
    if photo.kind == "self":
        response = RedirectResponse(f"/people/{gate.user.id}/album?toast=album", status_code=303)
    else:
        response = RedirectResponse(f"/?child={photo.child_id}&toast=timeline", status_code=303)
    note_child_upload(request, response, role=gate.membership.role, kind=photo.kind)
    return response


def _invite_login_href(token: str) -> str:
    return "/?next=" + quote(f"/invites/{token}", safe="/")


def _invite_open_context(db: Session, token: str, invite) -> dict:
    family = get_family(db, invite.family_id)
    role_label = ELDERS_NO_APP["inv_role"] if invite.role == "viewer" else ROLE_NOTE.get(invite.role, "")
    return {
        "token": token,
        "family_name": family.name if family else "",
        "role_label": role_label,
        "login_href": _invite_login_href(token),
        "error": None,
    }


@router.get("/invites/{token}")
def invite_page(token: str, request: Request, db: Session = Depends(get_db)):
    state, invite = invite_view(db, token)
    user = optional_user(request, db)
    if state == "expired":
        return render(request, "invite.html", {"mode": "expired", "token": token}, status_code=400)
    if state != "open" or invite is None:
        return render(request, "invite.html", {"mode": "invalid", "token": token}, status_code=400)
    context = _invite_open_context(db, token, invite)
    if user is None:
        return render(request, "invite.html", {"mode": "login", **context})
    if user.email != invite.email:
        return render(
            request,
            "invite.html",
            {"mode": "mismatch", "token": token, "login_href": context["login_href"]},
            status_code=400,
        )
    return render(
        request,
        "invite.html",
        {"mode": "ready", **_flags(user, get_membership(db, user.id)), **context},
    )


@router.post("/invites/{token}/accept")
def invite_accept(token: str, request: Request, db: Session = Depends(get_db)):
    user = optional_user(request, db)
    if user is None:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    try:
        result = accept_invite(db, user, token)
    except HTTPException as exc:
        state, invite = invite_view(db, token)
        if state == "expired":
            return render(request, "invite.html", {"mode": "expired", "token": token}, status_code=exc.status_code)
        if state == "open" and invite is not None and user.email != invite.email:
            return render(
                request,
                "invite.html",
                {"mode": "mismatch", "token": token, "login_href": _invite_login_href(token)},
                status_code=exc.status_code,
            )
        if state == "open" and invite is not None and user.email == invite.email:
            context = _invite_open_context(db, token, invite)
            context["error"] = str(exc.detail)
            return render(
                request,
                "invite.html",
                {"mode": "ready", **_flags(user, get_membership(db, user.id)), **context},
                status_code=exc.status_code,
            )
        return render(request, "invite.html", {"mode": "invalid", "token": token}, status_code=exc.status_code)
    if result["needs_profile"]:
        return RedirectResponse("/profile?next=" + quote("/", safe=""), status_code=303)
    return RedirectResponse("/", status_code=303)


@router.get("/photos/{photo_id}")
def photo_page(photo_id: int, request: Request, toast: str | None = None, db: Session = Depends(get_db)):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    photo = db.get(Photo, photo_id)
    if photo is None or photo.family_id != gate.family.id:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    owner = db.get(User, photo.uploader_id)
    audience: list[dict] = []
    if photo.kind == "child":
        raw_ids = photo_child_ids(db, photo)
        live = linked_children(db, family_id=gate.family.id, child_ids=raw_ids)
        audience = children_payload(live, photo.taken_on)
    owner_name = owner.name if owner and owner.name else "未命名"
    return render(
        request,
        "photo.html",
        {
            **_flags(gate.user, gate.membership),
            "photo": photo,
            "owner_name": owner_name,
            "audience": audience,
            "can_edit": photo.kind == "child" and can_edit_photo(gate.membership, photo),
            "date_label": date_label(photo.taken_on),
            "weekday": weekday_zh(photo.taken_on),
            "suffix": Path(photo.storage_path or "").suffix.lower(),
            "show_nav": True,
            "tab": "people" if photo.kind == "self" else "timeline",
            "toast": _photo_toast(request.query_params) or toast_text(toast),
        },
    )


@router.get("/photos/{photo_id}/edit")
def photo_edit_page(photo_id: int, request: Request, db: Session = Depends(get_db)):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    if gate.membership.role not in {"admin", "uploader"}:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    photo = db.get(Photo, photo_id)
    if photo is None or photo.family_id != gate.family.id:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    if photo.kind != "child" or not can_edit_photo(gate.membership, photo):
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    return _upload_form(
        request,
        db,
        gate,
        kind="child",
        child_id=photo.child_id,
        mode="edit",
        photo=photo,
        selected_override=photo_child_ids(db, photo),
    )


def _render_day(request: Request, db: Session, gate: MemberCtx, day, *, child):
    if child is not None:
        timeline = build_feed(db, child)
        back_href = f"/?child={child.id}"
        child_id = child.id
    else:
        timeline = build_family_feed(db, gate.family.id)
        back_href = "/?view=family"
        child_id = None
    photos: list[dict] = []
    journals: list[dict] = []
    videos: list[dict] = []
    age = ""
    for group in timeline["days"]:
        if group["taken_on"] == day.isoformat():
            photos = group["photos"]
            journals = group.get("journals") or []
            videos = group.get("videos") or []
            age = group.get("age_label") or ""
            break
    state = day_page_state(db, gate, day, child_id=child_id)
    initial = {
        "body": state["body"],
        "childIds": state["selected_ids"],
        "noteId": state["own_id"],
        "mode": state["mode"],
    }
    return render(
        request,
        "day.html",
        {
            **_flags(gate.user, gate.membership),
            "family": gate.family,
            "child": child,
            "family_entry": child is None,
            "taken_on": day.isoformat(),
            "date_label": date_label(day),
            "weekday": weekday_zh(day),
            "age_label": age,
            "photos": photos,
            "journals": journals,
            "videos": videos,
            "notes": state["notes"],
            "state": state,
            "back_href": back_href,
            "otd_year_meta": year_meta_for(day) or "",
            "show_nav": True,
            "tab": "timeline",
            "initial_json": json.dumps(initial, ensure_ascii=True),
            "children_json": json.dumps(state["children"], ensure_ascii=True),
        },
    )


@router.get("/days/{taken_on}")
def family_day_page(taken_on: str, request: Request, db: Session = Depends(get_db)):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    day = parse_iso_date(taken_on, invalid="拍摄日期无效", missing="拍摄日期无效")
    assert day is not None
    return _render_day(request, db, gate, day, child=None)


@router.get("/children/{child_id}/days/{taken_on}")
def day_page(child_id: int, taken_on: str, request: Request, db: Session = Depends(get_db)):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    child = require_child(db, family_id=gate.family.id, child_id=child_id)
    day = parse_iso_date(taken_on, invalid="拍摄日期无效", missing="拍摄日期无效")
    assert day is not None
    return _render_day(request, db, gate, day, child=child)


@router.get("/403")
def forbidden_page(request: Request):
    return render(request, "403.html", {}, status_code=403)


@router.get("/error")
def error_page(request: Request):
    return render(
        request,
        "error.html",
        {"message": "没加载出来", "next": safe_next(request.query_params.get("next"))},
    )
