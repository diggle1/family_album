"""Create tables and add photos.storage_backend when it is missing.

Idempotent. create_all alone does not alter an existing photos table, so
upgrades from v2026.10.23.1 must run this. The compose entrypoint runs it too.

``python scripts/migrate.py`` puts scripts/ on sys.path, not the repo root.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.migrate_schema import migrate_schema
from app.models import (  # noqa: F401
    AuthCode,
    AuthCodeRequest,
    AuthThrottle,
    Child,
    Family,
    Invite,
    DayNote,
    DayNoteChild,
    Journal,
    JournalChild,
    JournalPhoto,
    Membership,
    Photo,
    PhotoChild,
    User,
    TimelineLastSeen,
    Video,
    VideoChild,
)


def main() -> None:
    added = migrate_schema()
    if added:
        print("added photos.storage_backend default local")
    else:
        print("photos.storage_backend present")
    print("family_access_url ready")
    print("journals ready")
    print("journal_children ready")
    print("videos ready")
    print("video_children ready")
    print("photo_children ready")
    print("timeline_last_seen ready")
    print("day_notes ready")
    print("legacy_import ready (no new tables)")
    print("timeline_child_filter ready (no new tables)")
    print("exif_day_split ready (no new tables)")
    print("schema ready")


if __name__ == "__main__":
    main()
