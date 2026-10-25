import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.constants import ELDERS_NO_APP, ELDERS_WHATS_NEW, EXPORT, JOURNAL, MULTI_CHILD, PHOTO_MULTI_CHILD, VIDEO

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.globals["export_copy"] = EXPORT
templates.env.globals["journal_copy"] = JOURNAL
templates.env.globals["video_copy"] = VIDEO
templates.env.globals["multi_child"] = MULTI_CHILD
templates.env.globals["photo_copy"] = PHOTO_MULTI_CHILD
templates.env.globals["elders"] = ELDERS_NO_APP
templates.env.globals["wn_copy"] = ELDERS_WHATS_NEW


def _initial(name: str | None) -> str:
    text = (name or "").strip()
    return text[:1] if text else "·"


templates.env.filters["initial"] = _initial
templates.env.filters["tojson"] = lambda value: json.dumps(value, ensure_ascii=False)
