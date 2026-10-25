"""day_notes tables are created idempotently and left in place on rollback."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import MetaData, create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.migrate_schema import migrate_schema
from app.models import Child, DayNote, DayNoteChild, Family, User


def _engine(path):
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _stamp() -> datetime:
    return datetime.now(timezone.utc)


def _seed(session: Session) -> tuple[User, Family, Child]:
    now = _stamp()
    user = User(email="day-note@example.com", name="迁移", created_at=now)
    session.add(user)
    session.flush()
    family = Family(name="句子家", created_at=now)
    session.add(family)
    session.flush()
    child = Child(family_id=family.id, name="果果", birth_date=date(2020, 1, 1), created_at=now)
    session.add(child)
    session.flush()
    return user, family, child


def test_migrate_creates_day_notes_idempotently(tmp_path):
    engine = _engine(tmp_path / "app.db")
    assert migrate_schema(engine) is False
    assert migrate_schema(engine) is False
    insp = inspect(engine)
    assert insp.has_table("day_notes")
    assert insp.has_table("day_note_children")
    with Session(engine) as session:
        user, family, child = _seed(session)
        now = _stamp()
        note = DayNote(
            family_id=family.id,
            author_id=user.id,
            on_date=date(2026, 10, 25),
            body="留着",
            created_at=now,
            updated_at=now,
        )
        session.add(note)
        session.flush()
        session.add(DayNoteChild(note_id=note.id, child_id=child.id, position=0))
        session.commit()
    assert migrate_schema(engine) is False
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from day_notes")).scalar() == 1
        assert conn.execute(text("select body from day_notes")).scalar() == "留着"
        assert conn.execute(text("select count(*) from day_note_children")).scalar() == 1
    engine.dispose()


def test_day_note_unique_active_partial_index(tmp_path):
    engine = _engine(tmp_path / "unique.db")
    migrate_schema(engine)
    with engine.connect() as conn:
        sql = conn.execute(
            text("select sql from sqlite_master where name = 'uq_day_note_active'")
        ).scalar()
        assert sql
        assert "UNIQUE" in sql.upper()
        assert "deleted_at IS NULL" in sql
    with Session(engine) as session:
        user, family, child = _seed(session)
        now = _stamp()
        session.add(
            DayNote(
                family_id=family.id,
                author_id=user.id,
                on_date=date(2026, 10, 25),
                body="第一句",
                created_at=now,
                updated_at=now,
                deleted_at=now,
            )
        )
        session.commit()
        active = DayNote(
            family_id=family.id,
            author_id=user.id,
            on_date=date(2026, 10, 25),
            body="还在的一句",
            created_at=now,
            updated_at=now,
        )
        session.add(active)
        session.commit()
        session.add(
            DayNote(
                family_id=family.id,
                author_id=user.id,
                on_date=date(2026, 10, 25),
                body="不该有第二句",
                created_at=now,
                updated_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert session.scalar(select(DayNote).where(DayNote.body == "还在的一句")) is not None
    engine.dispose()


def test_rollback_schema_ignores_day_notes(tmp_path):
    engine = _engine(tmp_path / "rollback.db")
    migrate_schema(engine)
    with Session(engine) as session:
        user, family, _child = _seed(session)
        now = _stamp()
        session.add(
            DayNote(
                family_id=family.id,
                author_id=user.id,
                on_date=date(2026, 10, 20),
                body="回退后还在",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    old = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name in {"day_notes", "day_note_children"}:
            continue
        table.to_metadata(old)
    old.create_all(engine)
    source = (
        Path("app/migrate_schema.py").read_text(encoding="utf-8")
        + Path("scripts/migrate.py").read_text(encoding="utf-8")
        + Path("releases/v2026.10.25.6.md").read_text(encoding="utf-8")
    )
    assert "DROP TABLE day_notes" not in source
    assert "DROP TABLE day_note_children" not in source
    assert "DROP COLUMN" not in Path("releases/v2026.10.25.6.md").read_text(encoding="utf-8")
    assert "day_notes ready" in Path("scripts/migrate.py").read_text(encoding="utf-8")
    with Session(engine) as session:
        session.add(User(email="after@example.com", name="旧表", created_at=_stamp()))
        session.commit()
        assert session.scalar(select(DayNote).where(DayNote.body == "回退后还在")) is not None
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from day_notes")).scalar() == 1
    engine.dispose()
