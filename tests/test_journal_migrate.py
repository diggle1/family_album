"""Journal tables are created idempotently and left in place on rollback."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import MetaData, create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.migrate_schema import _column_names, migrate_schema
from app.models import Child, Family, Journal, JournalPhoto, Photo, User


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


def test_migrate_creates_journal_tables_idempotently(tmp_path):
    engine = _engine(tmp_path / "app.db")
    assert migrate_schema(engine) is False
    assert migrate_schema(engine) is False
    insp = inspect(engine)
    assert insp.has_table("journals")
    assert insp.has_table("journal_photos")
    assert "storage_backend" in _column_names(engine, "photos")
    now = _stamp()
    with Session(engine) as session:
        user = User(email="mig@example.com", name="迁移", created_at=now)
        session.add(user)
        session.flush()
        family = Family(name="迁移家", created_at=now)
        session.add(family)
        session.flush()
        session.add(
            Journal(
                family_id=family.id,
                author_id=user.id,
                child_id=1,
                on_date=date(2026, 10, 1),
                body="留着",
                client_request_id="11111111-1111-4111-8111-111111111111",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    assert migrate_schema(engine) is False
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from journals")).scalar() == 1
        assert conn.execute(text("select body from journals")).scalar() == "留着"
    engine.dispose()


def test_rollback_schema_ignores_journal_tables(tmp_path):
    engine = _engine(tmp_path / "app.db")
    migrate_schema(engine)
    now = _stamp()
    with Session(engine) as session:
        user = User(email="rb@example.com", name="回退", created_at=now)
        session.add(user)
        session.flush()
        family = Family(name="回退家", created_at=now)
        session.add(family)
        session.flush()
        session.add(
            Journal(
                family_id=family.id,
                author_id=user.id,
                child_id=1,
                on_date=date(2026, 10, 2),
                body="回退后还在",
                client_request_id="22222222-2222-4222-8222-222222222222",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    old = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name in {"journals", "journal_photos"}:
            continue
        table.to_metadata(old)
    old.create_all(engine)
    assert "storage_backend" in _column_names(engine, "photos")
    source = open("app/migrate_schema.py", encoding="utf-8").read() + open("scripts/migrate.py", encoding="utf-8").read()
    assert "DROP TABLE journals" not in source
    assert "DROP TABLE journal_photos" not in source
    with Session(engine) as session:
        session.add(User(email="after@example.com", name="旧表", created_at=_stamp()))
        session.commit()
        assert session.scalar(select(Journal).where(Journal.body == "回退后还在")) is not None
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from journals")).scalar() == 1
    engine.dispose()


def test_journal_tables_have_no_fk_to_photos_children(tmp_path):
    engine = _engine(tmp_path / "fk.db")
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    now = _stamp()
    with Session(engine) as session:
        user = User(email="fk@example.com", name="作者", created_at=now)
        session.add(user)
        session.flush()
        family = Family(name="外键家", created_at=now)
        session.add(family)
        session.flush()
        child = Child(family_id=family.id, name="果果", birth_date=date(2024, 7, 12), created_at=now)
        session.add(child)
        session.flush()
        photo = Photo(
            family_id=family.id,
            uploader_id=user.id,
            kind="child",
            child_id=child.id,
            taken_on=date(2026, 10, 22),
            storage_path="x.jpg",
            storage_backend="local",
            created_at=now,
        )
        session.add(photo)
        session.flush()
        journal = Journal(
            family_id=family.id,
            author_id=user.id,
            child_id=child.id,
            on_date=date(2026, 10, 22),
            body="还在",
            client_request_id="33333333-3333-4333-8333-333333333333",
            created_at=now,
            updated_at=now,
        )
        session.add(journal)
        session.flush()
        session.add(JournalPhoto(journal_id=journal.id, position=0, photo_id=photo.id))
        session.commit()
        photo_id, child_id, journal_id = photo.id, child.id, journal.id
        session.delete(photo)
        session.commit()
        assert session.get(Photo, photo_id) is None
        session.delete(session.get(Child, child_id))
        session.commit()
        assert session.get(Child, child_id) is None
        left = session.get(Journal, journal_id)
        assert left is not None and left.body == "还在"
        session.delete(user)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    engine.dispose()
