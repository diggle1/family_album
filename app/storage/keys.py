"""Object keys for photos are a single file name. Probe keys are not."""

from __future__ import annotations

from pathlib import Path

_MEDIA = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}


def safe_object_key(key: str) -> str:
    """Reject anything that is not a flat photo file name."""
    if not key or "/" in key or "\\" in key or key.startswith(".") or key != Path(key).name:
        raise FileNotFoundError(key)
    return key


def media_type_for(key: str) -> str:
    ext = Path(key).suffix.lower()
    media = _MEDIA.get(ext)
    if media is None:
        raise FileNotFoundError(key)
    return media
