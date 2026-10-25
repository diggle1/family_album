"""Runtime settings. Read from the environment on first use."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

# Shipped placeholder. Startup refuses this value unless ALLOW_INSECURE_DEFAULTS is set.
DEFAULT_SECRET = "dev-secret-change-me"


class InsecureSettingsError(RuntimeError):
    """Process would start while signing cookies with the shipped development secret."""


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


class Settings:
    def __init__(self) -> None:
        self.database_url = os.environ.get("DATABASE_URL", "sqlite:///./data/app.db")
        self.secret_key = os.environ.get("SECRET_KEY", DEFAULT_SECRET)
        self.allow_insecure_defaults = _enabled("ALLOW_INSECURE_DEFAULTS")
        # Empty string disables the bypass. Unset stays empty; dev and pytest opt in.
        raw_code = os.environ.get("AUTH_TEST_CODE", "")
        self.auth_test_code = raw_code.strip() or None
        backend = os.environ.get("STORAGE_BACKEND", "local").strip().lower()
        self.storage_backend = backend or "local"
        # PHOTO_DIR is the local-dev alias. When set, it is the media root.
        photo_dir = os.environ.get("PHOTO_DIR")
        if photo_dir is not None and photo_dir.strip():
            self.media_root = Path(photo_dir).expanduser()
        else:
            self.media_root = Path(os.environ.get("MEDIA_ROOT", "/data/media")).expanduser()
        self.photo_dir = self.media_root
        self.s3_endpoint = os.environ.get("S3_ENDPOINT", "").strip() or None
        self.s3_bucket = os.environ.get("S3_BUCKET", "").strip()
        self.s3_access_key = os.environ.get("S3_ACCESS_KEY", "").strip()
        self.s3_secret_key = os.environ.get("S3_SECRET_KEY", "").strip()
        self.s3_region = os.environ.get("S3_REGION", "").strip() or "us-east-1"
        self.s3_force_path_style = _enabled("S3_FORCE_PATH_STYLE")
        self.cookie_secure = _enabled("COOKIE_SECURE")
        self.code_ttl_seconds = int(os.environ.get("AUTH_CODE_TTL_SECONDS", "600"))
        self.invite_ttl_days = int(os.environ.get("INVITE_TTL_DAYS", "7"))
        self.session_ttl_seconds = 14 * 24 * 3600
        self.max_upload_bytes = 20 * 1024 * 1024


def validate_settings(settings: Settings) -> None:
    """Refuse an empty key, and the shipped SECRET_KEY unless local dev opted in."""
    if not settings.secret_key.strip():
        raise InsecureSettingsError("SECRET_KEY 为空，拒绝启动。请换成高熵随机串。")
    if settings.secret_key == DEFAULT_SECRET and not settings.allow_insecure_defaults:
        raise InsecureSettingsError(
            "SECRET_KEY 仍是开发默认值 dev-secret-change-me，拒绝启动。"
            "请换成高熵随机串；本地开发可设 ALLOW_INSECURE_DEFAULTS=1。"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
