"""photo_children is created once, backfilled, and left in place on rollback."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import MetaData, create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.main import app
from app.migrate_schema import migrate_schema
from app.models import Child, Family, Photo, PhotoChild, User


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


def _people(session: Session):
    now = _stamp()
    user = User(email="photo-assoc@example.com", name="迁移", created_at=now)
    session.add(user)
    session.flush()
    family = Family(name="照片关联家", created_at=now)
    session.add(family)
    session.flush()
    child = Child(family_id=family.id, name="果果", birth_date=date(2024, 7, 12), created_at=now)
    session.add(child)
    session.flush()
    return user, family, child


def _photo(session: Session, family_id: int, user_id: int, child_id: int | None, kind: str) -> Photo:
    row = Photo(
        family_id=family_id,
        uploader_id=user_id,
        kind=kind,
        child_id=child_id,
        taken_on=date(2026, 10, 22),
        storage_path="abc.jpg",
        storage_backend="local",
        created_at=_stamp(),
    )
    session.add(row)
    session.flush()
    return row


def test_migrate_creates_photo_children_idempotently(tmp_path):
    engine = _engine(tmp_path / "app.db")
    assert migrate_schema(engine) is False
    assert migrate_schema(engine) is False
    insp = inspect(engine)
    assert insp.has_table("photo_children")
    columns = {column["name"] for column in insp.get_columns("photo_children")}
    assert {"photo_id", "child_id", "position"} <= columns
    engine.dispose()


def test_backfill_single_child_photo_rows(tmp_path):
    engine = _engine(tmp_path / "app.db")
    migrate_schema(engine)
    with Session(engine) as session:
        user, family, child = _people(session)
        photo = _photo(session, family.id, user.id, child.id, "child")
        session.commit()
        photo_id = photo.id
        child_id = child.id
    migrate_schema(engine)
    migrate_schema(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text("select child_id, position from photo_children where photo_id = :id"),
            {"id": photo_id},
        ).all()
    assert rows == [(child_id, 0)]
    engine.dispose()


def test_self_photos_have_no_assoc(tmp_path):
    engine = _engine(tmp_path / "app.db")
    migrate_schema(engine)
    with Session(engine) as session:
        user, family, child = _people(session)
        self_photo = _photo(session, family.id, user.id, None, "self")
        child_photo = _photo(session, family.id, user.id, child.id, "child")
        session.commit()
        self_id, child_photo_id = self_photo.id, child_photo.id
    migrate_schema(engine)
    with engine.connect() as conn:
        assert conn.execute(
            text("select count(*) from photo_children where photo_id = :id"),
            {"id": self_id},
        ).scalar() == 0
        assert conn.execute(
            text("select count(*) from photo_children where photo_id = :id"),
            {"id": child_photo_id},
        ).scalar() == 1
    engine.dispose()


def test_backfill_keeps_existing_photo_children(tmp_path):
    engine = _engine(tmp_path / "app.db")
    migrate_schema(engine)
    with Session(engine) as session:
        user, family, child = _people(session)
        photo = _photo(session, family.id, user.id, child.id, "child")
        session.add(PhotoChild(photo_id=photo.id, child_id=child.id, position=0))
        session.add(PhotoChild(photo_id=photo.id, child_id=424242, position=1))
        session.commit()
        photo_id = photo.id
        kept = child.id
    migrate_schema(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text("select child_id, position from photo_children where photo_id = :id order by position"),
            {"id": photo_id},
        ).all()
    assert rows == [(kept, 0), (424242, 1)]
    engine.dispose()


def test_rollback_schema_ignores_photo_children(tmp_path):
    engine = _engine(tmp_path / "app.db")
    migrate_schema(engine)
    with Session(engine) as session:
        user, family, child = _people(session)
        photo = _photo(session, family.id, user.id, child.id, "child")
        session.add(PhotoChild(photo_id=photo.id, child_id=child.id, position=0))
        session.commit()
    old = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name == "photo_children":
            continue
        table.to_metadata(old)
    old.create_all(engine)
    source = (
        Path("app/migrate_schema.py").read_text(encoding="utf-8")
        + Path("scripts/migrate.py").read_text(encoding="utf-8")
        + Path("releases/v2026.10.25.3.md").read_text(encoding="utf-8")
        + Path("CHANGELOG.md").read_text(encoding="utf-8")
    )
    assert "DROP TABLE photo_children" not in source
    notes = Path("releases/v2026.10.25.3.md").read_text(encoding="utf-8")
    assert "v2026.10.25.2" in notes
    assert "down -v" in notes
    assert "photo_children" in notes
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert "## v2026.10.25.3" in changelog.split("## v2026.10.25.2", 1)[0]
    assert app.version == "2026.10.25.4"
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from photo_children")).scalar() == 1
    engine.dispose()


def test_assoc_child_id_has_no_fk(tmp_path):
    engine = _engine(tmp_path / "app.db")
    migrate_schema(engine)
    for fk in inspect(engine).get_foreign_keys("photo_children"):
        assert "child_id" not in fk["constrained_columns"]
    photo_fks = inspect(engine).get_foreign_keys("photos")
    assert any("child_id" in fk["constrained_columns"] for fk in photo_fks)
    with Session(engine) as session:
        user, family, child = _people(session)
        photo = _photo(session, family.id, user.id, child.id, "child")
        session.add(PhotoChild(photo_id=photo.id, child_id=424242, position=0))
        session.commit()
        with pytest.raises(IntegrityError):
            _photo(session, family.id, user.id, 999999, "child")
            session.commit()
        session.rollback()
    with engine.connect() as conn:
        assert conn.execute(text("select child_id from photo_children")).scalar() == 424242
    engine.dispose()
