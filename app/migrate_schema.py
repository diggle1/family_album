"""create_all plus an idempotent ALTER for photos.storage_backend.

SQLAlchemy create_all does not add columns to a table that already exists.
Upgrades from v2026.10.23.1 have to run this.

v2026.10.24.3 adds journals and journal_photos. create_all builds them once
the models are imported. It does not alter older tables. Rolling back to
v2026.10.24.2 does not drop them: that release never imports the models, so
its create_all leaves the rows where they are. Do not DROP TABLE.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db import Base, engine as default_engine

_ALTER = "ALTER TABLE photos ADD COLUMN storage_backend VARCHAR(16) NOT NULL DEFAULT 'local'"


def _column_names(bind: Engine, table: str) -> set[str]:
    insp = inspect(bind)
    if not insp.has_table(table):
        return set()
    return {col["name"] for col in insp.get_columns(table)}


def migrate_schema(bind: Engine | None = None) -> bool:
    """Create missing tables, then add storage_backend when it is absent.

    Returns True when the column was added on this call.
    """
    target = bind or default_engine
    import app.models  # noqa: F401

    Base.metadata.create_all(target)
    if "storage_backend" in _column_names(target, "photos"):
        return False
    if not _column_names(target, "photos") and not inspect(target).has_table("photos"):
        return False
    with target.begin() as conn:
        conn.execute(text(_ALTER))
    return True
