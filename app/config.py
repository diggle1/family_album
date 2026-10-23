"""Runtime settings. Read from the environment on first use."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.database_url = os.environ.get("DATABASE_URL", "sqlite:///./data/app.db")
        self.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
        # Empty string disables the bypass. Default is the documented test code
        # because this version does not send real email.
        raw_code = os.environ.get("AUTH_TEST_CODE", "000000")
        self.auth_test_code = raw_code.strip() or None
        self.photo_dir = Path(os.environ.get("PHOTO_DIR", "./data/photos")).expanduser()
        self.cookie_secure = os.environ.get("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
        self.code_ttl_seconds = int(os.environ.get("AUTH_CODE_TTL_SECONDS", "600"))
        self.invite_ttl_days = int(os.environ.get("INVITE_TTL_DAYS", "7"))
        self.session_ttl_seconds = 14 * 24 * 3600
        self.max_upload_bytes = 10 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
