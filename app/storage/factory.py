"""Write with the current env. Read with the backend stored on the photo row."""

from __future__ import annotations

import logging

from app.config import get_settings
from app.storage.errors import StorageError
from app.storage.local import LocalStore
from app.storage.s3 import S3Store

logger = logging.getLogger("family_album.storage")


def store_for(backend: str):
    name = (backend or "").strip().lower()
    settings = get_settings()
    if name == "local":
        return LocalStore(settings.media_root)
    if name == "s3":
        return S3Store(settings)
    raise StorageError("APP_BOOT", "服务还没起来")


def current_store():
    settings = get_settings()
    if settings.storage_backend not in {"local", "s3"}:
        raise StorageError("APP_BOOT", "服务还没起来")
    return store_for(settings.storage_backend)


def probe_code() -> str | None:
    """None when the current backend can be written. Never raises."""
    try:
        current_store().probe()
    except StorageError as exc:
        logger.warning("storage probe failed code=%s", exc.code)
        return exc.code
    except Exception as exc:
        logger.warning("storage probe failed code=APP_BOOT error=%s", type(exc).__name__)
        return "APP_BOOT"
    return None
