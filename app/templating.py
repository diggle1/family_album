from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.constants import EXPORT, JOURNAL

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.globals["export_copy"] = EXPORT
templates.env.globals["journal_copy"] = JOURNAL


def _initial(name: str | None) -> str:
    text = (name or "").strip()
    return text[:1] if text else "·"


templates.env.filters["initial"] = _initial
