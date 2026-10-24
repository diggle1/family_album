"""family_access_url is added once and left in place on rollback."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session

from app.migrate_schema import migrate_schema
from app.models import Family, User


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


def test_migrate_adds_family_access_url_idempotently(tmp_path):
    engine = _engine(tmp_path / "legacy.db")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE families (id INTEGER PRIMARY KEY, name VARCHAR(40), created_at DATETIME)"))
        conn.execute(text("INSERT INTO families (name, created_at) VALUES ('旧家', :now)"), {"now": _stamp()})
    assert "family_access_url" not in {col["name"] for col in inspect(engine).get_columns("families")}
    migrate_schema(engine)
    migrate_schema(engine)
    columns = {col["name"]: col for col in inspect(engine).get_columns("families")}
    assert "family_access_url" in columns
    assert columns["family_access_url"]["nullable"] is True
    assert "VARCHAR" in str(columns["family_access_url"]["type"]).upper()
    with engine.connect() as conn:
        assert conn.execute(text("select family_access_url from families")).scalar() is None
        assert conn.execute(text("select name from families")).scalar() == "旧家"
    engine.dispose()


def test_migrate_script_prints_family_access_url_ready(tmp_path):
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'script.db'}"
    env["PHOTO_DIR"] = str(tmp_path / "photos")
    env["SECRET_KEY"] = "test-secret"
    env["STORAGE_BACKEND"] = "local"
    runs = [
        subprocess.run([sys.executable, "scripts/migrate.py"], cwd=Path.cwd(), env=env, capture_output=True, text=True, check=True)
        for _ in range(2)
    ]
    for completed in runs:
        assert "family_access_url ready" in completed.stdout
    engine = _engine(tmp_path / "script.db")
    names = {col["name"] for col in inspect(engine).get_columns("families")}
    assert "family_access_url" in names
    engine.dispose()


def test_rollback_schema_ignores_family_access_url(tmp_path):
    engine = _engine(tmp_path / "rollback.db")
    migrate_schema(engine)
    now = _stamp()
    with Session(engine) as session:
        user = User(email="rb-url@example.com", name="回退", created_at=now)
        session.add(user)
        session.flush()
        session.add(Family(name="回退家", created_at=now, family_access_url="https://album.example"))
        session.commit()
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS families_old_view (id INTEGER PRIMARY KEY, name VARCHAR(40), created_at DATETIME)"))
    with engine.connect() as conn:
        assert conn.execute(text("select family_access_url from families")).scalar() == "https://album.example"
        assert conn.execute(text("select name from families")).scalar() == "回退家"
    source = (
        Path("app/migrate_schema.py").read_text(encoding="utf-8")
        + Path("scripts/migrate.py").read_text(encoding="utf-8")
        + Path("releases/v2026.10.25.2.md").read_text(encoding="utf-8")
        + Path("CHANGELOG.md").read_text(encoding="utf-8")
    )
    assert "DROP COLUMN family_access_url" not in source
    assert "DROP TABLE families" not in source
    notes = Path("releases/v2026.10.25.2.md").read_text(encoding="utf-8")
    assert "v2026.10.25.1" in notes
    assert "down -v" in notes
    assert "family_access_url" in notes
    engine.dispose()
