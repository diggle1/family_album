"""Resolve the timeline who-tag. Filter state stays in the URL, not the database.

A missing, blank, or unknown child id becomes the mixed family feed. The
lookup never leaves this family, so another household's child id does not
change the status code and does not appear in the response.
"""

from __future__ import annotations

from app.models import Child
from app.services.children import child_in_family
from app.timeline_child_filter_copy import TIMELINE_CHILD_FILTER

_COPY = TIMELINE_CHILD_FILTER


class TimelineFilterBusy(Exception):
    """The timeline could not be read. The process stays up."""


def raise_if_busy() -> None:
    """Tests replace this. Production leaves it as a no-op."""
    return None


def parse_child_token(raw: str | None) -> tuple[str, int | None]:
    """Classify a query value.

    family: absent, blank, or the literal all.
    child: a non-negative integer token.
    invalid: anything else. Pages fall back to family. JSON returns 422.
    """
    if raw is None:
        return "family", None
    text = str(raw).strip()
    if text == "" or text.lower() == "all":
        return "family", None
    if text.isdigit():
        return "child", int(text)
    return "invalid", None


def child_token_probes(raw: str | None) -> bool:
    """True when the query is trying to name a child, including a junk token."""
    kind, _child_id = parse_child_token(raw)
    return kind != "family"


def resolve_timeline_child(db, family_id: int, raw: str | None) -> tuple[str, Child | None]:
    """Member-side resolution. Unknown and junk tokens are the family feed."""
    kind, child_id = parse_child_token(raw)
    if kind != "child" or child_id is None:
        return "family", None
    child = child_in_family(db, family_id=family_id, child_id=child_id)
    if child is None:
        return "family", None
    return "child", child


def _swap_sample(template: str, sample: str, name: str) -> str:
    if sample and sample in template:
        return template.replace(sample, name, 1)
    return template


def kid_title(name: str) -> str:
    sample = _COPY["tl_title_kid"]
    prefix = _COPY["kid_a"]
    if sample.startswith(prefix):
        return name + sample[len(prefix) :]
    return name


def empty_title_for(name: str) -> str:
    return _swap_sample(_COPY["empty_title"], _COPY["kid_a"], name)


def empty_sub_for(name: str) -> str:
    return _swap_sample(_COPY["empty_sub"], _COPY["kid_a"], name)


def view_copy(child: Child | None) -> dict:
    if child is None:
        return {
            "scope": "family",
            "title": _COPY["tl_title_all"],
            "empty_title": "",
            "empty_sub": "",
        }
    return {
        "scope": "child",
        "title": kid_title(child.name),
        "empty_title": empty_title_for(child.name),
        "empty_sub": empty_sub_for(child.name),
    }
