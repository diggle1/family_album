"""Zip the current member's own uploads. No count cap.

The archive is written to a temp file on a worker thread. On timeout the
thread is asked to stop, its open read is closed, and an exception is raised
into the thread so the build does not keep running after the 503.
"""

from __future__ import annotations

import ctypes
import logging
import os
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import constants
from app.models import Photo
from app.storage import factory as storage_factory

logger = logging.getLogger("family_album.export")

# How long to wait after signalling the worker before giving up on join.
_STOP_GRACE_S = 2.0
READ_CHUNK = 64 * 1024

# Last worker, so tests can assert a timed-out build is no longer alive.
last_build_thread: threading.Thread | None = None


class ExportFailed(Exception):
    """Packaging did not finish. The route turns this into export_failed."""


class ExportCancelled(Exception):
    """Raised in the worker when the build must stop."""


@dataclass(frozen=True)
class ExportItem:
    photo_id: int
    taken_on: date
    storage_path: str
    storage_backend: str

    @property
    def entry_name(self) -> str:
        suffix = Path(self.storage_path).suffix.lower()
        return f"{self.taken_on.isoformat()}_{self.photo_id}{suffix}"


def temp_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "family-album-export"
    path.mkdir(parents=True, exist_ok=True)
    return path


def timeout_seconds() -> float:
    """Env wins when it parses. Otherwise the constants value, read each call."""
    raw = os.environ.get("EXPORT_BUILD_TIMEOUT_S", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            logger.warning("export timeout env ignored")
    return max(0.0, float(constants.EXPORT_BUILD_TIMEOUT_S))


def list_my_photos(db: Session, *, family_id: int, uploader_id: int) -> list[Photo]:
    """Photos this user uploaded in this family. Request parameters are not read."""
    return list(
        db.scalars(
            select(Photo)
            .where(Photo.family_id == family_id, Photo.uploader_id == uploader_id)
            .order_by(Photo.taken_on.asc(), Photo.id.asc())
        ).all()
    )


def count_my_photos(db: Session, *, family_id: int, uploader_id: int) -> int:
    return len(list_my_photos(db, family_id=family_id, uploader_id=uploader_id))


def to_item(photo: Photo) -> ExportItem:
    return ExportItem(
        photo_id=photo.id,
        taken_on=photo.taken_on,
        storage_path=photo.storage_path,
        storage_backend=photo.storage_backend or "local",
    )


class _StreamSlot:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream: object | None = None

    def set(self, stream: object) -> None:
        with self._lock:
            self._stream = stream

    def close(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is None:
            return
        close = getattr(stream, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception:
            logger.warning("export stream close failed")


def _unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("export temp delete failed")


def _async_raise(thread: threading.Thread, exc_type: type[BaseException]) -> None:
    ident = thread.ident
    if ident is None or not thread.is_alive():
        return
    setter = ctypes.pythonapi.PyThreadState_SetAsyncExc
    setter.argtypes = [ctypes.c_ulong, ctypes.py_object]
    setter.restype = ctypes.c_int
    count = setter(ctypes.c_ulong(ident), ctypes.py_object(exc_type))
    if count > 1:
        setter(ctypes.c_ulong(ident), None)


def _stop_worker(thread: threading.Thread, cancel: threading.Event, slot: _StreamSlot) -> None:
    cancel.set()
    slot.close()
    _async_raise(thread, ExportCancelled)
    thread.join(_STOP_GRACE_S)
    if thread.is_alive():
        _async_raise(thread, SystemExit)
        thread.join(_STOP_GRACE_S)


def _fill_zip(path: Path, items: list[ExportItem], cancel: threading.Event, slot: _StreamSlot) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for item in items:
            if cancel.is_set():
                raise ExportCancelled()
            stream, _media = storage_factory.store_for(item.storage_backend).open(item.storage_path)
            slot.set(stream)
            try:
                with archive.open(item.entry_name, "w") as dest:
                    while True:
                        if cancel.is_set():
                            raise ExportCancelled()
                        chunk = stream.read(READ_CHUNK)  # type: ignore[attr-defined]
                        if not chunk:
                            break
                        dest.write(chunk)
            finally:
                slot.close()


def build_zip(items: list[ExportItem], timeout_s: float | None = None) -> Path:
    """Write a complete zip. Raises ExportFailed without leaving the temp file."""
    if not items:
        raise ExportFailed("empty")
    limit = timeout_seconds() if timeout_s is None else max(0.0, float(timeout_s))
    directory = temp_dir()
    fd, name = tempfile.mkstemp(prefix="export-", suffix=".zip", dir=directory)
    os.close(fd)
    path = Path(name)
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.warning("export temp chmod failed")

    cancel = threading.Event()
    slot = _StreamSlot()
    outcome: dict[str, object] = {}

    def run() -> None:
        ok = False
        try:
            _fill_zip(path, items, cancel, slot)
            ok = True
            outcome["path"] = path
        except Exception as exc:
            outcome["error"] = exc
        finally:
            slot.close()
            if not ok:
                _unlink(path)

    global last_build_thread
    thread = threading.Thread(target=run, name="export-zip", daemon=True)
    last_build_thread = thread
    thread.start()
    thread.join(limit)
    if thread.is_alive():
        logger.warning("export build timeout")
        _stop_worker(thread, cancel, slot)
        _unlink(path)
        raise ExportFailed("timeout")
    if outcome.get("error") is not None or outcome.get("path") is None or not path.is_file():
        logger.warning(
            "export build failed error=%s",
            type(outcome.get("error")).__name__ if outcome.get("error") is not None else "missing",
        )
        _unlink(path)
        raise ExportFailed("read")
    return path
