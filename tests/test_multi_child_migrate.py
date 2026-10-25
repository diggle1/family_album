"""Association tables are created once, backfilled, and left in place on rollback."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import MetaData, create_engine, event, inspect, text
from sqlalchemy.orm import Session

from app.db import Base
from app.main import app
from app.migrate_schema import migrate_schema
from app.models import Family, Journal, JournalChild, User, Video, VideoChild


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
    user = User(email="assoc@example.com", name="迁移", created_at=now)
    session.add(user)
    session.flush()
    family = Family(name="关联家", created_at=now)
    session.add(family)
    session.flush()
    return user, family


def _journal(session: Session, family_id: int, user_id: int, child_id: int, key: str) -> Journal:
    row = Journal(
        family_id=family_id,
        author_id=user_id,
        child_id=child_id,
        on_date=date(2026, 10, 1),
        body="存量一句",
        client_request_id=key,
        created_at=_stamp(),
        updated_at=_stamp(),
    )
    session.add(row)
    session.flush()
    return row


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
    session.flush()
    return row


def test_migrate_creates_assoc_tables_idempotently(tmp_path):
    engine = _engine(tmp_path / "app.db")
    assert migrate_schema(engine) is False
    assert migrate_schema(engine) is False
    insp = inspect(engine)
    assert insp.has_table("journal_children")
    assert insp.has_table("video_children")
    journal_cols = {column["name"] for column in insp.get_columns("journal_children")}
    video_cols = {column["name"] for column in insp.get_columns("video_children")}
    assert {"journal_id", "child_id", "position"} <= journal_cols
    assert {"video_id", "child_id", "position"} <= video_cols
    engine.dispose()


def test_backfill_single_child_rows(tmp_path):
    engine = _engine(tmp_path / "app.db")
    migrate_schema(engine)
    with Session(engine) as session:
        user, family = _people(session)
        journal = _journal(session, family.id, user.id, 7, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        video = _video(session, family.id, user.id, 8, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        session.commit()
        journal_id, video_id = journal.id, video.id
    migrate_schema(engine)
    migrate_schema(engine)
    with engine.connect() as conn:
        journal_rows = conn.execute(
            text("select child_id, position from journal_children where journal_id = :id"),
            {"id": journal_id},
        ).all()
        video_rows = conn.execute(
            text("select child_id, position from video_children where video_id = :id"),
            {"id": video_id},
        ).all()
    assert journal_rows == [(7, 0)]
    assert video_rows == [(8, 0)]
    engine.dispose()


def test_backfill_keeps_single_assoc(tmp_path):
    engine = _engine(tmp_path / "app.db")
    migrate_schema(engine)
    with Session(engine) as session:
        user, family = _people(session)
        journal = _journal(session, family.id, user.id, 3, "cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        session.add(JournalChild(journal_id=journal.id, child_id=9, position=0))
        video = _video(session, family.id, user.id, 4, "dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        session.add(VideoChild(video_id=video.id, child_id=11, position=0))
        session.add(VideoChild(video_id=video.id, child_id=12, position=1))
        session.commit()
        journal_id, video_id = journal.id, video.id
    migrate_schema(engine)
    with engine.connect() as conn:
        journal_rows = conn.execute(
            text("select child_id, position from journal_children where journal_id = :id order by position"),
            {"id": journal_id},
        ).all()
        video_rows = conn.execute(
            text("select child_id, position from video_children where video_id = :id order by position"),
            {"id": video_id},
        ).all()
        assert conn.execute(text("select child_id from journals")).scalar() == 3
    assert journal_rows == [(9, 0)]
    assert video_rows == [(11, 0), (12, 1)]
    engine.dispose()


def test_rollback_schema_ignores_assoc_tables(tmp_path):
    engine = _engine(tmp_path / "app.db")
    migrate_schema(engine)
    with Session(engine) as session:
        user, family = _people(session)
        journal = _journal(session, family.id, user.id, 5, "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
        session.add(JournalChild(journal_id=journal.id, child_id=5, position=0))
        video = _video(session, family.id, user.id, 6, "ffffffff-ffff-4fff-8fff-ffffffffffff")
        session.add(VideoChild(video_id=video.id, child_id=6, position=0))
        session.commit()
    old = MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name in {"journal_children", "video_children"}:
            continue
        table.to_metadata(old)
    old.create_all(engine)
    source = (
        Path("app/migrate_schema.py").read_text(encoding="utf-8")
        + Path("scripts/migrate.py").read_text(encoding="utf-8")
        + Path("releases/v2026.10.25.1.md").read_text(encoding="utf-8")
        + Path("CHANGELOG.md").read_text(encoding="utf-8")
    )
    assert "DROP TABLE journal_children" not in source
    assert "DROP TABLE video_children" not in source
    notes = Path("releases/v2026.10.25.1.md").read_text(encoding="utf-8")
    assert "down -v" in notes
    assert "v2026.10.24.4" in notes
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert "## v2026.10.25.1" in changelog.split("## v2026.10.24.4", 1)[0]
    assert app.version == "2026.10.25.4"
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from journal_children")).scalar() == 1
        assert conn.execute(text("select count(*) from video_children")).scalar() == 1
    engine.dispose()


def test_assoc_child_id_has_no_fk(tmp_path):
    engine = _engine(tmp_path / "app.db")
    migrate_schema(engine)
    for table in ("journal_children", "video_children"):
        for fk in inspect(engine).get_foreign_keys(table):
            assert "child_id" not in fk["constrained_columns"]
    with Session(engine) as session:
        user, family = _people(session)
        journal = _journal(session, family.id, user.id, 424242, "12121212-1212-4121-8121-121212121212")
        session.add(JournalChild(journal_id=journal.id, child_id=424242, position=0))
        video = _video(session, family.id, user.id, 434343, "13131313-1313-4131-8131-131313131313")
        session.add(VideoChild(video_id=video.id, child_id=434343, position=0))
        session.commit()
    with engine.connect() as conn:
        assert conn.execute(text("select child_id from journal_children")).scalar() == 424242
        assert conn.execute(text("select child_id from video_children")).scalar() == 434343
    engine.dispose()
