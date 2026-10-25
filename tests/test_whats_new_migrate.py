"""timeline_last_seen is created once and left in place on rollback."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import MetaData, create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.migrate_schema import migrate_schema
from app.models import Family, TimelineLastSeen, User


def _engine(path):
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def test_migrate_creates_timeline_last_seen_idempotently(tmp_path):
    engine = _engine(tmp_path / "app.db")
    assert migrate_schema(engine) is False
    assert migrate_schema(engine) is False
    insp = inspect(engine)
    assert insp.has_table("timeline_last_seen")
    columns = {column["name"] for column in insp.get_columns("timeline_last_seen")}
    assert {"id", "user_id", "family_id", "last_seen_at"} <= columns
    unique_names = {item["name"] for item in insp.get_unique_constraints("timeline_last_seen")}
    assert "uq_timeline_last_seen_user_family" in unique_names
    assert "timeline_last_seen ready" in Path("scripts/migrate.py").read_text(encoding="utf-8")
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        user = User(email="seen-mig@example.com", name="迁移", created_at=now)
        session.add(user)
        session.flush()
        family = Family(name="时刻家", created_at=now)
        session.add(family)
        session.flush()
        session.add(TimelineLastSeen(user_id=user.id, family_id=family.id, last_seen_at=now))
        session.commit()
        session.add(TimelineLastSeen(user_id=user.id, family_id=family.id, last_seen_at=now))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    engine.dispose()


def test_rollback_schema_ignores_timeline_last_seen(tmp_path):
    engine = _engine(tmp_path / "rollback.db")
    migrate_schema(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        user = User(email="seen-rb@example.com", name="回退", created_at=now)
        session.add(user)
        session.flush()
        family = Family(name="回退家", created_at=now)
        session.add(family)
        session.flush()
        session.add(TimelineLastSeen(user_id=user.id, family_id=family.id, last_seen_at=now))
        session.commit()
    old = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name == "timeline_last_seen":
            continue
        table.to_metadata(old)
    old.create_all(engine)
    source = (
        Path("app/migrate_schema.py").read_text(encoding="utf-8")
        + Path("scripts/migrate.py").read_text(encoding="utf-8")
        + Path("releases/v2026.10.25.4.md").read_text(encoding="utf-8")
        + Path("CHANGELOG.md").read_text(encoding="utf-8")
    )
    assert "DROP TABLE timeline_last_seen" not in source
    assert "DROP COLUMN" not in Path("releases/v2026.10.25.4.md").read_text(encoding="utf-8")
    notes = Path("releases/v2026.10.25.4.md").read_text(encoding="utf-8")
    assert "v2026.10.25.3" in notes
    assert "down -v" in notes
    assert "timeline_last_seen" in notes
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert "## v2026.10.25.4" in changelog.split("## v2026.10.25.3", 1)[0]
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from timeline_last_seen")).scalar() == 1
    engine.dispose()
