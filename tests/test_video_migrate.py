"""The videos table is created once and left in place when rolling back."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import MetaData, create_engine, event, inspect, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import Base
from app.main import app
from app.migrate_schema import migrate_schema
from app.models import Child, Family, User, Video


@pytest.fixture(autouse=True)
def video_media(tmp_path, monkeypatch):
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setenv("PHOTO_DIR", str(root))
    monkeypatch.setenv("MEDIA_ROOT", str(root))
    monkeypatch.setenv("EXPORT_TEMP_DIR", str(tmp_path / "export"))
    (tmp_path / "export").mkdir()
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


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


def _video(session: Session, family_id: int, user_id: int, child_id: int, key: str) -> Video:
    row = Video(
        family_id=family_id,
        uploader_id=user_id,
        child_id=child_id,
        on_date=date(2026, 10, 1),
        duration_ms=12500,
        byte_size=20,
        container="mp4",
        storage_path="abc123.mp4",
        storage_backend="local",
        client_request_id=key,
        created_at=_stamp(),
    )
    session.add(row)
    return row


def test_migrate_creates_video_table_idempotently(tmp_path):
    engine = _engine(tmp_path / "app.db")
    assert migrate_schema(engine) is False
    assert migrate_schema(engine) is False
    insp = inspect(engine)
    assert insp.has_table("videos")
    names = {column["name"] for column in insp.get_columns("videos")}
    assert {"storage_path", "poster_path", "client_request_id", "deleted_at", "child_id"} <= names
    now = _stamp()
    with Session(engine) as session:
        user = User(email="vmig@example.com", name="迁移", created_at=now)
        session.add(user)
        session.flush()
        family = Family(name="迁移家", created_at=now)
        session.add(family)
        session.flush()
        _video(session, family.id, user.id, 1, "11111111-1111-4111-8111-111111111111")
        session.commit()
    assert migrate_schema(engine) is False
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from videos")).scalar() == 1
        assert conn.execute(text("select storage_path from videos")).scalar() == "abc123.mp4"
    engine.dispose()


def test_rollback_schema_ignores_video_table(tmp_path):
    engine = _engine(tmp_path / "app.db")
    migrate_schema(engine)
    now = _stamp()
    with Session(engine) as session:
        user = User(email="vrb@example.com", name="回退", created_at=now)
        session.add(user)
        session.flush()
        family = Family(name="回退家", created_at=now)
        session.add(family)
        session.flush()
        _video(session, family.id, user.id, 7, "22222222-2222-4222-8222-222222222222")
        session.commit()
    old = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name == "videos":
            continue
        table.to_metadata(old)
    old.create_all(engine)
    source = (
        Path("app/migrate_schema.py").read_text(encoding="utf-8")
        + Path("scripts/migrate.py").read_text(encoding="utf-8")
        + Path("releases/v2026.10.24.4.md").read_text(encoding="utf-8")
    )
    assert "DROP TABLE videos" not in source
    assert "down -v" in Path("releases/v2026.10.24.4.md").read_text(encoding="utf-8")
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert "## v2026.10.24.4" in changelog.split("## v2026.10.24.3", 1)[0]
    assert app.version == "2026.10.25.8"
    with Session(engine) as session:
        session.add(User(email="after-v@example.com", name="旧表", created_at=_stamp()))
        session.commit()
        assert session.scalar(select(Video).where(Video.child_id == 7)) is not None
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from videos")).scalar() == 1
    engine.dispose()


def test_video_child_id_has_no_fk(tmp_path):
    engine = _engine(tmp_path / "fk.db")
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    pointed = {tuple(fk["constrained_columns"]) for fk in insp.get_foreign_keys("videos")}
    assert ("child_id",) not in pointed
    assert ("family_id",) in pointed
    assert ("uploader_id",) in pointed
    now = _stamp()
    with Session(engine) as session:
        user = User(email="vfk@example.com", name="作者", created_at=now)
        session.add(user)
        session.flush()
        family = Family(name="外键家", created_at=now)
        session.add(family)
        session.flush()
        child = Child(family_id=family.id, name="果果", birth_date=date(2024, 7, 12), created_at=now)
        session.add(child)
        session.flush()
        child_id = child.id
        dangling = _video(session, family.id, user.id, 424242, "33333333-3333-4333-8333-333333333333")
        linked = _video(session, family.id, user.id, child_id, "44444444-4444-4444-8444-444444444444")
        session.commit()
        session.delete(child)
        session.commit()
        assert session.get(Video, dangling.id) is not None
        assert session.get(Video, linked.id).child_id == child_id
    engine.dispose()
