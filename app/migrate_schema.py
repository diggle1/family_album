"""create_all plus an idempotent ALTER for photos.storage_backend.

SQLAlchemy create_all does not add columns to a table that already exists.
Upgrades from v2026.10.23.1 have to run this.

v2026.10.24.3 adds journals and journal_photos. create_all builds them once
the models are imported. It does not alter older tables. Rolling back to
v2026.10.24.2 does not drop them: that release never imports the models, so
its create_all leaves the rows where they are. Do not DROP TABLE.

v2026.10.24.4 adds videos the same way. Rolling back to v2026.10.24.3 does
not import Video, so create_all leaves that table and its rows in place.
Do not DROP TABLE.

v2026.10.25.1 adds journal_children and video_children, then projects each
existing journals.child_id / videos.child_id into one association row when
that record has none yet. It does not alter the old columns. Rolling back to
v2026.10.24.4 does not import the association models, so create_all leaves
those tables and their rows in place. Do not DROP TABLE.
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


_JOURNAL_BACKFILL = """
INSERT OR IGNORE INTO journal_children (journal_id, child_id, position)
SELECT id, child_id, 0 FROM journals
WHERE child_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM journal_children jc WHERE jc.journal_id = journals.id
  )
"""

_VIDEO_BACKFILL = """
INSERT OR IGNORE INTO video_children (video_id, child_id, position)
SELECT id, child_id, 0 FROM videos
WHERE child_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM video_children vc WHERE vc.video_id = videos.id
  )
"""


def _backfill_associations(bind: Engine) -> None:
    """Project the legacy single child into the association table. Idempotent."""
    insp = inspect(bind)
    with bind.begin() as conn:
        if insp.has_table("journals") and insp.has_table("journal_children"):
            conn.execute(text(_JOURNAL_BACKFILL))
        if insp.has_table("videos") and insp.has_table("video_children"):
            conn.execute(text(_VIDEO_BACKFILL))


def migrate_schema(bind: Engine | None = None) -> bool:
    """Create missing tables, then add storage_backend when it is absent.

    Returns True when the column was added on this call.
    Association tables are created and backfilled. They are never dropped.
    """
    target = bind or default_engine
    import app.models  # noqa: F401

    Base.metadata.create_all(target)
    _backfill_associations(target)
    if "storage_backend" in _column_names(target, "photos"):
        return False
    if not _column_names(target, "photos") and not inspect(target).has_table("photos"):
        return False
    with target.begin() as conn:
        conn.execute(text(_ALTER))
    return True
