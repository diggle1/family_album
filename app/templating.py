from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _initial(name: str | None) -> str:
    text = (name or "").strip()
    return text[:1] if text else "·"


templates.env.filters["initial"] = _initial
