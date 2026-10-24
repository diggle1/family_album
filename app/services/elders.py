"""给长辈的免装笺。paste_body 只在服务端拼。"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.elders_copy import ELDERS_NO_APP
from app.models import Family, Invite, Membership
from app.services.clock import as_utc, utcnow

TARGET_NOT_FOUND = "找不到这位长辈"
TARGET_INVALID = "请先选择要复制的人"
NOT_VIEWER = "只能复制给只读的家人"
BODY_INVALID = "请求无法完成"

_PASTE_LINES = (
    "家里给您留了一份网页相册，不用装任何东西。\n"
    "请用系统浏览器（Safari/Chrome）打开下面的链接。\n"
    "打开就能看家里的时光。"
)
_URL_KEYS = {"family_access_url", "family_id"}
_PASTE_KEYS = {"member_user_id", "invite_id", "family_id"}


class EldersError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        self.status = status
        self.code = code
        self.detail = detail
        super().__init__(detail)


def greeting_for(label: str | None) -> str:
    text = (label or "").strip()
    if text:
        return f"亲爱的{text}："
    return "亲爱的家人："


def invite_absolute(entry_url: str, token: str) -> str:
    return entry_url.rstrip("/") + "/invites/" + token


def build_paste_body(*, greeting: str, family_name: str, entry_url: str, invite_url: str | None) -> str:
    body = f"{greeting}\n\n{_PASTE_LINES}\n\n家庭：{family_name}\n入口：{entry_url}"
    if invite_url:
        line = ELDERS_NO_APP["paste_inv_line"].replace("{inv}", invite_url)
        body = f"{body}\n{line}"
    return body


def normalize_access_url(raw: object) -> str | None:
    """Empty becomes NULL. Anything else must be an http(s) URL without whitespace."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise EldersError(400, "access_url_invalid", ELDERS_NO_APP["url_help"])
    if raw.strip() == "":
        return None
    if any(char.isspace() for char in raw) or len(raw) > 512:
        raise EldersError(400, "access_url_invalid", ELDERS_NO_APP["url_help"])
    lowered = raw.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise EldersError(400, "access_url_invalid", ELDERS_NO_APP["url_help"])
    return raw


def parse_json_object(raw: bytes) -> dict:
    if not raw or not raw.strip():
        raise EldersError(422, "elders_body_invalid", BODY_INVALID)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise EldersError(422, "elders_body_invalid", BODY_INVALID) from None
    if not isinstance(payload, dict):
        raise EldersError(422, "elders_body_invalid", BODY_INVALID)
    return payload


def apply_access_url(db: Session, family: Family, payload: dict) -> None:
    if "family_access_url" not in payload or set(payload) - _URL_KEYS:
        raise EldersError(422, "elders_body_invalid", BODY_INVALID)
    family.family_access_url = normalize_access_url(payload["family_access_url"])
    db.commit()
    db.refresh(family)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _targets(payload: dict) -> tuple[int | None, int | None]:
    if set(payload) - _PASTE_KEYS:
        raise EldersError(422, "elders_body_invalid", BODY_INVALID)
    has_member = "member_user_id" in payload
    has_invite = "invite_id" in payload
    if has_member == has_invite:
        raise EldersError(400, "elders_target_invalid", TARGET_INVALID)
    if has_member:
        member_id = _as_int(payload.get("member_user_id"))
        if member_id is None:
            raise EldersError(400, "elders_target_invalid", TARGET_INVALID)
        return member_id, None
    invite_id = _as_int(payload.get("invite_id"))
    if invite_id is None:
        raise EldersError(400, "elders_target_invalid", TARGET_INVALID)
    return None, invite_id


def _require_entry(family: Family) -> str:
    entry = (family.family_access_url or "").strip()
    if not entry:
        raise EldersError(400, "access_url_required", ELDERS_NO_APP["empty_url_hint"])
    return entry


def paste_for_admin(db: Session, *, family: Family, payload: dict) -> dict:
    entry = _require_entry(family)
    member_id, invite_id = _targets(payload)
    if member_id is not None:
        return _paste_member(db, family, member_id, entry)
    assert invite_id is not None
    return _paste_invite(db, family, invite_id, entry)


def _not_found() -> EldersError:
    return EldersError(404, "elders_target_not_found", TARGET_NOT_FOUND)


def _paste_member(db: Session, family: Family, user_id: int, entry: str) -> dict:
    row = db.scalar(select(Membership).where(Membership.user_id == user_id))
    if row is None or row.family_id != family.id:
        raise _not_found()
    if row.role != "viewer":
        raise EldersError(400, "elders_not_viewer", NOT_VIEWER)
    body = build_paste_body(
        greeting=greeting_for(row.relation_label),
        family_name=family.name,
        entry_url=entry,
        invite_url=None,
    )
    return {
        "paste_body": body,
        "has_invite_line": False,
        "entry_url": entry,
        "invite_url": None,
    }


def _paste_invite(db: Session, family: Family, invite_id: int, entry: str) -> dict:
    invite = db.get(Invite, invite_id)
    if invite is None or invite.family_id != family.id:
        raise _not_found()
    if invite.accepted_at is not None or as_utc(invite.expires_at) <= utcnow():
        raise EldersError(400, "invite_expired", ELDERS_NO_APP["inv_expired_sub"])
    invite_url = invite_absolute(entry, invite.token)
    body = build_paste_body(
        greeting=greeting_for(None),
        family_name=family.name,
        entry_url=entry,
        invite_url=invite_url,
    )
    return {
        "paste_body": body,
        "has_invite_line": True,
        "entry_url": entry,
        "invite_url": invite_url,
    }
