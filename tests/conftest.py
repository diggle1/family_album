"""Point the app at a throwaway SQLite file before it is imported."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_root = Path(tempfile.mkdtemp(prefix="family-album-"))
(_root / "photos").mkdir()
os.environ["DATABASE_URL"] = f"sqlite:///{_root / 'app.db'}"
os.environ["PHOTO_DIR"] = str(_root / "photos")
os.environ["STORAGE_BACKEND"] = "local"
os.environ["AUTH_TEST_CODE"] = "000000"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["COOKIE_SECURE"] = ""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import SessionLocal, engine
from app.main import app
from app.models import Base


@pytest.fixture(autouse=True)
def _reset_db():
    get_settings.cache_clear()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    photo_dir = get_settings().photo_dir
    photo_dir.mkdir(parents=True, exist_ok=True)
    for item in photo_dir.iterdir():
        if item.is_file():
            item.unlink()
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
