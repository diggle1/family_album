"""Local media root. Compose mounts this even when new uploads go to S3."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from app.storage.errors import StorageError
from app.storage.keys import media_type_for, safe_object_key

logger = logging.getLogger("family_album.storage")


class LocalStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        name = safe_object_key(key)
        path = (self.root / name).resolve()
        root = self.root.resolve()
        if path.parent != root:
            raise FileNotFoundError(key)
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        del content_type  # inferred from the file name on read
        path = self._path(key)
        if not self.root.is_dir():
            raise StorageError("VOL_MISSING", "媒资卷没挂上")
        try:
            path.write_bytes(data)
        except OSError as exc:
            raise StorageError("VOL_MISSING", "媒资卷没挂上") from exc

    def put_file(self, key: str, source: Path, content_type: str) -> None:
        """Move a file already on disk into the media root. Does not read it into memory."""
        del content_type
        dest = self._path(key)
        if not self.root.is_dir():
            raise StorageError("VOL_MISSING", "媒资卷没挂上")
        try:
            os.replace(source, dest)
        except OSError as exc:
            raise StorageError("VOL_MISSING", "媒资卷没挂上") from exc

    def open(self, key: str) -> tuple[object, str]:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.open("rb"), media_type_for(key)

    def open_range(self, key: str, start: int, end: int | None = None) -> tuple[object, str]:
        del end  # the caller stops reading; the file handle is seeked
        handle, media = self.open(key)
        try:
            handle.seek(start)
        except Exception:
            close = getattr(handle, "close", None)
            if close is not None:
                close()
            raise
        return handle, media

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError:
            logger.warning("local delete failed key=%s", key)

    def probe(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError("VOL_MISSING", "媒资卷没挂上") from exc
        if not self.root.is_dir():
            raise StorageError("VOL_MISSING", "媒资卷没挂上")
        probe = self.root / f".probe-{uuid.uuid4().hex}"
        try:
            probe.write_bytes(b"ok")
        except OSError as exc:
            raise StorageError("VOL_MISSING", "媒资卷没挂上") from exc
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                logger.warning("local probe delete failed name=%s", probe.name)
