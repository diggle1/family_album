"""Zip the current member's own uploads. No count cap.

The archive is written to a temp file on a worker thread. On timeout or
cancel the worker is asked to stop and its open read is closed. The temp
file is removed before the pack slot is released. One slot per
(family_id, user_id) in this process.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import stat
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import constants
from app.models import Child, Photo
from app.storage import factory as storage_factory

logger = logging.getLogger("family_album.export")

# How long to wait after signalling the worker before giving up on join.
_STOP_GRACE_S = 2.0
_DISCONNECT_POLL_S = 0.5
READ_CHUNK = 64 * 1024
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MISSING_NAME = "缺失清单.txt"
_HEADER_CAP = 20

# Last worker, so tests can assert a timed-out build is no longer alive.
last_build_thread: threading.Thread | None = None

_condition = threading.Condition()
_active: set[tuple[int, int]] = set()
_slots: dict[tuple[int, int], PackSlot] = {}
_waiters: dict[tuple[int, int], list[threading.Event]] = {}


class ExportFailed(Exception):
    """Packaging did not finish. The route turns this into export_failed."""

    def __init__(self, reason: str = "read") -> None:
        self.reason = reason
        super().__init__(reason)


class ExportAllMissing(Exception):
    """Every original was missing. The route turns this into 422 all_missing."""

    def __init__(self, missing: list[dict[str, str]]) -> None:
        self.missing = missing
        super().__init__("all_missing")


class ExportCancelled(Exception):
    """Raised in the worker when the build must stop."""


class _Cancelled:
    """acquire_pack_slot returns this when the waiter was cancelled."""


CANCELLED = _Cancelled()


@dataclass(frozen=True)
class ExportItem:
    photo_id: int
    taken_on: date
    storage_path: str
    storage_backend: str
    kind: str
    child_name: str | None

    @property
    def entry_name(self) -> str:
        suffix = Path(self.storage_path).suffix.lower()
        return f"{self.taken_on.isoformat()}_{self.photo_id}{suffix}"


@dataclass(frozen=True)
class BuildResult:
    path: Path
    packed: int
    missing: list[dict[str, str]]


class PackSlot:
    """One in-flight pack. release() is idempotent and runs after the temp file is gone."""

    def __init__(self, key: tuple[int, int], cancel: threading.Event) -> None:
        self.key = key
        self.cancel = cancel
        self._released = False
        self._gate = threading.Lock()

    def release(self) -> None:
        with self._gate:
            if self._released:
                return
            self._released = True
        with _condition:
            _active.discard(self.key)
            _slots.pop(self.key, None)
            _condition.notify_all()


def temp_dir() -> Path:
    """Directory for export zips.

    EXPORT_TEMP_DIR overrides the shared default. Under pytest each process
    gets its own subdirectory so one suite cannot see another's files.
    """
    override = os.environ.get("EXPORT_TEMP_DIR", "").strip()
    if override:
        path = Path(override)
    else:
        path = Path(tempfile.gettempdir()) / "family-album-export"
        if os.environ.get("PYTEST_CURRENT_TEST"):
            path = path / f"pytest-{os.getpid()}"
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


def header_wait_ms() -> int:
    """Re-reads the timeout env on every call, same as timeout_seconds()."""
    return int((timeout_seconds() + constants.EXPORT_HEADER_MARGIN_S) * 1000)


def blob_max_bytes() -> int:
    raw = os.environ.get("EXPORT_BLOB_MAX_MB", "").strip()
    mb = float(constants.EXPORT_BLOB_MAX_MB)
    if raw:
        try:
            mb = float(raw)
        except ValueError:
            logger.warning("export blob max env ignored")
    if mb < 0:
        mb = 0
    return int(mb * 1024 * 1024)


def export_filename_day(raw: str | None) -> date:
    """Valid ?d=YYYY-MM-DD wins. Anything else uses EXPORT_FILENAME_TZ (default Asia/Shanghai)."""
    if raw and _DATE_RE.fullmatch(raw):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    name = os.environ.get("EXPORT_FILENAME_TZ", "").strip() or constants.EXPORT_FILENAME_TZ
    try:
        zone = ZoneInfo(name)
    except Exception:
        logger.warning("export filename tz ignored")
        zone = ZoneInfo("Asia/Shanghai")
    return datetime.now(zone).date()


def content_disposition(day: date) -> str:
    """ASCII filename= is always present. filename* uses the chosen calendar day."""
    utf_name = constants.EXPORT["filename"].format(date=day.isoformat())
    from urllib.parse import quote

    encoded = quote(utf_name, safe="")
    ascii_name = constants.EXPORT_ZIP_FILENAME
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def list_my_photos(db: Session, *, family_id: int, uploader_id: int) -> list[ExportItem]:
    """Photos this user uploaded in this family. Request parameters are not read."""
    rows = db.execute(
        select(Photo, Child.name)
        .outerjoin(Child, (Child.id == Photo.child_id) & (Child.family_id == family_id))
        .where(Photo.family_id == family_id, Photo.uploader_id == uploader_id)
        .order_by(Photo.taken_on.asc(), Photo.id.asc())
    ).all()
    items: list[ExportItem] = []
    for photo, child_name in rows:
        items.append(
            ExportItem(
                photo_id=photo.id,
                taken_on=photo.taken_on,
                storage_path=photo.storage_path,
                storage_backend=photo.storage_backend or "local",
                kind=photo.kind,
                child_name=child_name if isinstance(child_name, str) and child_name else None,
            )
        )
    return items


def count_my_photos(db: Session, *, family_id: int, uploader_id: int) -> int:
    value = db.scalar(
        select(func.count())
        .select_from(Photo)
        .where(Photo.family_id == family_id, Photo.uploader_id == uploader_id)
    )
    return int(value or 0)


def owner_label(item: ExportItem) -> str:
    if item.kind == "child" and item.child_name:
        return f"挂在 {item.child_name}"
    return "记在我名下"


def missing_record(item: ExportItem) -> dict[str, str]:
    return {"date": item.taken_on.isoformat(), "owner": owner_label(item)}


def missing_header_value(rows: list[dict[str, str]]) -> str:
    payload = {"total": len(rows), "items": rows[:_HEADER_CAP]}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def acquire_pack_slot(
    key: tuple[int, int], wait_s: float, cancel: threading.Event
) -> PackSlot | _Cancelled | None:
    """Wait up to wait_s. None means still busy. CANCELLED means this waiter was cancelled."""
    deadline = time.monotonic() + max(0.0, wait_s)
    with _condition:
        bucket = _waiters.setdefault(key, [])
        bucket.append(cancel)
        try:
            while key in _active:
                if cancel.is_set():
                    return CANCELLED
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                _condition.wait(remaining)
            if cancel.is_set():
                return CANCELLED
            slot = PackSlot(key, cancel)
            _active.add(key)
            _slots[key] = slot
            return slot
        finally:
            current = _waiters.get(key, [])
            if cancel in current:
                current.remove(cancel)
            if not current:
                _waiters.pop(key, None)


def notify_cancel(cancel: threading.Event) -> None:
    cancel.set()
    with _condition:
        _condition.notify_all()


def request_cancel(key: tuple[int, int]) -> None:
    """Cancel the active pack and anyone waiting for that same key."""
    with _condition:
        slot = _slots.get(key)
        waiters = list(_waiters.get(key, []))
        if slot is not None:
            slot.cancel.set()
        for event in waiters:
            event.set()
        _condition.notify_all()


def wait_slot_free(key: tuple[int, int], wait_s: float) -> bool:
    deadline = time.monotonic() + max(0.0, wait_s)
    with _condition:
        while key in _active:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _condition.wait(remaining)
        return True


def sweep_max_age_s() -> float:
    """Keep a zip that may still be building or sending.

    Age is measured from mtime. A live build is younger than the build timeout.
    A finished zip can still be in the response body for EXPORT_TRANSFER_MAX_S.
    A new process must not delete the other one.
    """
    return timeout_seconds() + max(0.0, float(constants.EXPORT_TRANSFER_MAX_S))


def sweep_temp_dir() -> int:
    """Delete old regular export zips in this directory. Skip links and fresh files."""
    directory = temp_dir()
    cutoff = time.time() - sweep_max_age_s()
    removed = 0
    for path in directory.glob("export-*.zip"):
        try:
            info = path.lstat()
        except OSError:
            logger.warning("export temp sweep failed")
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            continue
        if info.st_mtime >= cutoff:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            logger.warning("export temp sweep failed")
    logger.info("export temp sweep removed=%s", removed)
    return removed


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


def _stop_worker(thread: threading.Thread, cancel: threading.Event, slot: _StreamSlot) -> None:
    cancel.set()
    slot.close()
    thread.join(_STOP_GRACE_S)
    if thread.is_alive():
        logger.warning("export worker still running")


def _zip_info(name: str, taken_on: date | None) -> zipfile.ZipInfo:
    if taken_on is None:
        stamp = (1980, 1, 1, 0, 0, 0)
    else:
        stamp = (max(1980, taken_on.year), taken_on.month, taken_on.day, 0, 0, 0)
    info = zipfile.ZipInfo(filename=name, date_time=stamp)
    info.compress_type = zipfile.ZIP_STORED
    return info


def _fill_zip(path: Path, items: list[ExportItem], cancel: threading.Event, slot: _StreamSlot) -> BuildResult:
    missing: list[dict[str, str]] = []
    packed = 0
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for item in items:
            if cancel.is_set():
                raise ExportCancelled()
            try:
                stream, _media = storage_factory.store_for(item.storage_backend).open(item.storage_path)
            except FileNotFoundError:
                missing.append(missing_record(item))
                continue
            slot.set(stream)
            try:
                info = _zip_info(item.entry_name, item.taken_on)
                with archive.open(info, "w") as dest:
                    while True:
                        if cancel.is_set():
                            raise ExportCancelled()
                        chunk = stream.read(READ_CHUNK)  # type: ignore[attr-defined]
                        if not chunk:
                            break
                        dest.write(chunk)
            finally:
                slot.close()
            packed += 1
        if packed == 0 and missing:
            raise ExportAllMissing(missing)
        if missing:
            lines = [f"{row['date']} · {row['owner']}" for row in missing]
            text = "\ufeff" + "\n".join(lines) + "\n"
            archive.writestr(_zip_info(_MISSING_NAME, None), text.encode("utf-8"))
    return BuildResult(path=path, packed=packed, missing=missing)


def build_zip(
    items: list[ExportItem],
    timeout_s: float | None = None,
    cancel: threading.Event | None = None,
) -> BuildResult:
    """Write a complete zip. Raises ExportFailed or ExportAllMissing without leaving the temp file."""
    if not items:
        raise ExportFailed("empty")
    limit = timeout_seconds() if timeout_s is None else max(0.0, float(timeout_s))
    if cancel is None:
        cancel = threading.Event()
    directory = temp_dir()
    fd, name = tempfile.mkstemp(prefix="export-", suffix=".zip", dir=directory)
    os.close(fd)
    path = Path(name)
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.warning("export temp chmod failed")

    slot = _StreamSlot()
    outcome: dict[str, object] = {}

    def run() -> None:
        ok = False
        try:
            outcome["result"] = _fill_zip(path, items, cancel, slot)
            ok = True
        except ExportAllMissing as exc:
            outcome["all_missing"] = exc
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
    deadline = time.monotonic() + limit
    while thread.is_alive():
        if cancel.is_set():
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(min(_DISCONNECT_POLL_S, remaining))
    if thread.is_alive():
        reason = "cancelled" if cancel.is_set() else "timeout"
        if reason == "timeout":
            logger.warning("export build timeout")
        _stop_worker(thread, cancel, slot)
        _unlink(path)
        raise ExportFailed(reason)
    all_missing = outcome.get("all_missing")
    if isinstance(all_missing, ExportAllMissing):
        _unlink(path)
        raise all_missing
    error = outcome.get("error")
    result = outcome.get("result")
    if isinstance(error, ExportCancelled) or (cancel.is_set() and not isinstance(result, BuildResult)):
        _unlink(path)
        raise ExportFailed("cancelled")
    if isinstance(result, BuildResult) and path.is_file():
        return result
    if error is None:
        if isinstance(result, BuildResult):
            error = FileNotFoundError(path)
        else:
            error = RuntimeError("export worker finished without a result")
    logger.warning("export build failed error=%s", type(error).__name__)
    _unlink(path)
    raise ExportFailed("read") from error
