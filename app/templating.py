from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.constants import EXPORT

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.globals["export_copy"] = EXPORT


def _initial(name: str | None) -> str:
    text = (name or "").strip()
    return text[:1] if text else "·"


templates.env.filters["initial"] = _initial
