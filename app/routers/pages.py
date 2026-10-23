"""HTML screens aligned with the final IA: 时光 / 家人 / 设置."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import set_session
from app.constants import FORBIDDEN, ROLE_LABEL, ROLE_NOTE
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
from app.services.clock import date_label, shanghai_today, weekday_zh
from app.services.families import (
    accept_invite,
    create_family,
    create_invite,
    list_members,
    preview_invite,
    set_role,
)
from app.services.photos import create_photo, self_album
from app.setup_gate import note_child_upload
from app.services.validators import parse_iso_date
from app.templating import templates
from app.config import get_settings
from app.routers.photos import _read_limited

router = APIRouter(tags=["pages"])


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


@router.get("/")
def home(
    request: Request,
    child: int | None = None,
    toast: str | None = None,
    db: Session = Depends(get_db),
):
    user = optional_user(request, db)
    nxt = safe_next(request.query_params.get("next"))
    if user is None:
        if child is not None:
            raise HTTPException(status_code=403, detail=FORBIDDEN)
        return render(request, "login.html", _login_ctx(request, nxt=nxt))
    if not user.name:
        return render(request, "profile.html", {**_flags(user, get_membership(db, user.id)), "error": None, "next": nxt})
    if nxt.startswith("/invites/"):
        return RedirectResponse(nxt, status_code=303)
    membership = get_membership(db, user.id)
    if membership is None:
        if child is not None:
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
    current = None
    if child is not None:
        current = child_in_family(db, family_id=family.id, child_id=child)
        if current is None:
            raise HTTPException(status_code=403, detail=FORBIDDEN)
    elif children:
        current = children[0]
    if current is None:
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
    return _timeline_response(request, db, user, membership, family, children, current, toast)


def _timeline_response(request, db, user, membership, family, children, current, toast):
    flags = _flags(user, membership)
    timeline = build_timeline(db, current)
    return render(
        request,
        "timeline.html",
        {
            **flags,
            "family": family,
            "children": children,
            "child": current,
            "timeline": timeline,
            "show_nav": True,
            "show_fab": flags["can_upload"],
            "fab_href": f"/upload?child={current.id}",
            "tab": "timeline",
            "toast": toast_text(toast),
        },
    )


@router.get("/fragments/timeline")
def timeline_fragment(request: Request, child: int, db: Session = Depends(get_db)):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    found = child_in_family(db, family_id=gate.family.id, child_id=child)
    if found is None:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    children = list_children(db, gate.family.id)
    flags = _flags(gate.user, gate.membership)
    return render(
        request,
        "fragments/timeline_swap.html",
        {
            **flags,
            "children": children,
            "child": found,
            "timeline": build_timeline(db, found),
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


@router.get("/family/settings")
def settings_page(request: Request, toast: str | None = None, db: Session = Depends(get_db)):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    children = list_children(db, gate.family.id)
    member_count = len(list_members(db, family_id=gate.family.id, include_invites=False)["members"])
    cards = []
    for child in children:
        timeline = build_timeline(db, child)
        cards.append({"child": child, "age_label": timeline["child"]["age_label"], "photo_count": timeline["child"]["photo_count"]})
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
            "toast": toast_text(toast),
            "error": None,
        },
    )


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


def _upload_form(request, db, gate: MemberCtx, *, kind: str | None, child_id: int | None, error: str | None = None, status_code: int = 200):
    children = list_children(db, gate.family.id)
    selected = None
    if child_id is not None:
        selected = child_in_family(db, family_id=gate.family.id, child_id=child_id)
    if selected is None and len(children) == 1:
        selected = children[0]
    chosen = (kind or "").strip().lower()
    if chosen not in {"child", "self"}:
        chosen = ""
    flags = _flags(gate.user, gate.membership)
    return render(
        request,
        "upload.html",
        {
            **flags,
            "children": children,
            "selected": selected,
            "kind": chosen,
            "today": shanghai_today().isoformat(),
            "error": error,
            "show_nav": True,
            "tab": "people" if chosen == "self" else "timeline",
        },
        status_code=status_code,
    )


@router.post("/upload")
async def upload_save(
    request: Request,
    kind: str = Form(""),
    child_id: str | None = Form(None),
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
            taken_on_raw=taken_on,
            data=data,
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
        )
    if photo.kind == "self":
        response = RedirectResponse(f"/people/{gate.user.id}/album?toast=album", status_code=303)
    else:
        response = RedirectResponse(f"/?child={photo.child_id}&toast=timeline", status_code=303)
    note_child_upload(request, response, role=gate.membership.role, kind=photo.kind)
    return response


@router.get("/invites/{token}")
def invite_page(token: str, request: Request, db: Session = Depends(get_db)):
    invite = preview_invite(db, token)
    user = optional_user(request, db)
    if invite is None:
        return render(request, "invite.html", {"mode": "invalid", "token": token}, status_code=400)
    if user is None:
        return render(request, "invite.html", {"mode": "login", "token": token, "email": invite.email})
    if user.email != invite.email:
        return render(request, "invite.html", {"mode": "mismatch", "token": token}, status_code=400)
    family = get_family(db, invite.family_id)
    return render(
        request,
        "invite.html",
        {
            "mode": "ready",
            "token": token,
            "family_name": family.name if family else "",
            "role_label": ROLE_LABEL.get(invite.role, ""),
            **_flags(user, get_membership(db, user.id)),
        },
    )


@router.post("/invites/{token}/accept")
def invite_accept(token: str, request: Request, db: Session = Depends(get_db)):
    user = optional_user(request, db)
    if user is None:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    try:
        result = accept_invite(db, user, token)
    except HTTPException as exc:
        return render(
            request,
            "invite.html",
            {"mode": "invalid", "token": token, "error": str(exc.detail)},
            status_code=exc.status_code,
        )
    if result["needs_profile"]:
        return RedirectResponse("/profile?next=" + quote("/", safe=""), status_code=303)
    return RedirectResponse("/", status_code=303)


@router.get("/photos/{photo_id}")
def photo_page(photo_id: int, request: Request, db: Session = Depends(get_db)):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    photo = db.get(Photo, photo_id)
    if photo is None or photo.family_id != gate.family.id:
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    owner = db.get(User, photo.uploader_id)
    child = None
    if photo.kind == "child" and photo.child_id is not None:
        child = child_in_family(db, family_id=gate.family.id, child_id=photo.child_id)
    return render(
        request,
        "photo.html",
        {
            **_flags(gate.user, gate.membership),
            "photo": photo,
            "owner_name": owner.name if owner and owner.name else "未命名",
            "child": child,
            "date_label": date_label(photo.taken_on),
            "weekday": weekday_zh(photo.taken_on),
            "show_nav": True,
            "tab": "people" if photo.kind == "self" else "timeline",
        },
    )


@router.get("/children/{child_id}/days/{taken_on}")
def day_page(child_id: int, taken_on: str, request: Request, db: Session = Depends(get_db)):
    gate = gate_member(request, db)
    if isinstance(gate, RedirectResponse):
        return gate
    child = require_child(db, family_id=gate.family.id, child_id=child_id)
    day = parse_iso_date(taken_on, invalid="拍摄日期无效", missing="拍摄日期无效")
    assert day is not None
    timeline = build_timeline(db, child)
    photos: list[dict] = []
    age = ""
    for group in timeline["days"]:
        if group["taken_on"] == day.isoformat():
            photos = group["photos"]
            age = group["age_label"]
            break
    return render(
        request,
        "day.html",
        {
            **_flags(gate.user, gate.membership),
            "child": child,
            "taken_on": day.isoformat(),
            "date_label": date_label(day),
            "weekday": weekday_zh(day),
            "age_label": age,
            "photos": photos,
            "show_nav": True,
            "tab": "timeline",
        },
    )


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
