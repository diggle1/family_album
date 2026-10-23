"""Login defaults, hashed codes, request limits, and verify lockout."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.services.auth_codes as auth_codes
from app.config import DEFAULT_SECRET, InsecureSettingsError, Settings, get_settings, validate_settings
from app.main import app
from app.models import AuthCode, AuthCodeRequest, AuthThrottle
from app.services.auth_codes import code_digest
from app.services.clock import utcnow


def test_auth_test_code_defaults_to_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AUTH_TEST_CODE", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_DEFAULTS", raising=False)
    fresh = Settings()
    assert fresh.auth_test_code is None
    assert fresh.secret_key == DEFAULT_SECRET
    assert fresh.allow_insecure_defaults is False
    with pytest.raises(InsecureSettingsError, match="dev-secret-change-me"):
        validate_settings(fresh)

    monkeypatch.setenv("AUTH_TEST_CODE", "   ")
    assert Settings().auth_test_code is None
    monkeypatch.setenv("AUTH_TEST_CODE", "000000")
    assert Settings().auth_test_code == "000000"
    monkeypatch.setenv("ALLOW_INSECURE_DEFAULTS", "1")
    monkeypatch.setenv("SECRET_KEY", DEFAULT_SECRET)
    opted = Settings()
    assert opted.allow_insecure_defaults is True
    validate_settings(opted)


def test_startup_refuses_default_secret_without_opt_in(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "secret_key", DEFAULT_SECRET)
    monkeypatch.setattr(settings, "allow_insecure_defaults", False)
    with pytest.raises(InsecureSettingsError, match="SECRET_KEY"):
        validate_settings(settings)
    with pytest.raises(InsecureSettingsError, match="SECRET_KEY"):
        with TestClient(app):
            pass


def test_allow_insecure_defaults_permits_startup(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "secret_key", DEFAULT_SECRET)
    monkeypatch.setattr(settings, "allow_insecure_defaults", True)
    validate_settings(settings)
    with TestClient(app) as client:
        assert client.get("/api/me").status_code == 401


def test_bypass_off_stores_hash_and_accepts_real_code(client: TestClient, db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "auth_test_code", None)
    monkeypatch.setattr(auth_codes.secrets, "randbelow", lambda _span: 246810)
    email = "plain@example.com"
    created = client.post("/auth/request-code", json={"email": email})
    assert created.status_code == 200, created.text
    assert created.json() == {"ok": True}
    assert "246810" not in created.text

    row = db.scalar(select(AuthCode).where(AuthCode.email == email))
    assert row is not None
    assert row.code == code_digest(email, "246810")
    assert row.code != "246810"
    db.rollback()

    rejected = client.post("/auth/verify", json={"email": email, "code": "000000"})
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "验证码无效或已过期"

    verified = client.post("/auth/verify", json={"email": email, "code": "246810"})
    assert verified.status_code == 200, verified.text
    assert verified.json()["email"] == email


def test_request_code_email_cooldown_returns_429(client: TestClient, db):
    email = "slow@example.com"
    first = client.post("/auth/request-code", json={"email": email})
    assert first.status_code == 200
    stored = db.scalar(select(AuthCode).where(AuthCode.email == email)).code
    db.rollback()

    second = client.post("/auth/request-code", json={"email": email})
    assert second.status_code == 429
    assert "请求过于频繁" in second.json()["detail"]
    assert "秒" in second.json()["detail"]
    again = db.scalar(select(AuthCode).where(AuthCode.email == email))
    assert again.code == stored
    db.rollback()

    row = db.scalar(select(AuthCodeRequest).where(AuthCodeRequest.email == email))
    row.created_at = utcnow() - timedelta(seconds=120)
    db.commit()
    assert client.post("/auth/request-code", json={"email": email}).status_code == 200


def test_request_code_ip_hourly_cap_returns_429(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth_codes, "IP_REQUEST_HOURLY_LIMIT", 2)
    assert client.post("/auth/request-code", json={"email": "one@example.com"}).status_code == 200
    assert client.post("/auth/request-code", json={"email": "two@example.com"}).status_code == 200
    blocked = client.post("/auth/request-code", json={"email": "three@example.com"})
    assert blocked.status_code == 429
    assert "本小时" in blocked.json()["detail"]


def test_request_code_ip_daily_cap_returns_429(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth_codes, "IP_REQUEST_HOURLY_LIMIT", 100)
    monkeypatch.setattr(auth_codes, "IP_REQUEST_DAILY_LIMIT", 1)
    assert client.post("/auth/request-code", json={"email": "day1@example.com"}).status_code == 200
    blocked = client.post("/auth/request-code", json={"email": "day2@example.com"})
    assert blocked.status_code == 429
    assert "今天" in blocked.json()["detail"]


def test_login_form_shows_test_code_hint_and_cooldown(client: TestClient):
    first = client.post(
        "/login/request",
        data={"email": "form@example.com", "next": "/"},
        follow_redirects=False,
    )
    assert first.status_code == 303
    page = client.get(first.headers["location"])
    assert page.status_code == 200
    assert "已开启测试码" in page.text
    again = client.post(
        "/login/request",
        data={"email": "form@example.com", "next": "/"},
        follow_redirects=False,
    )
    assert again.status_code == 429
    assert "请求过于频繁" in again.text


def test_verify_failure_lockout(client: TestClient, db):
    email = "lock@example.com"
    assert client.post("/auth/request-code", json={"email": email}).status_code == 200
    for _ in range(5):
        bad = client.post("/auth/verify", json={"email": email, "code": "111111"})
        assert bad.status_code == 400, bad.text
        assert bad.json()["detail"] == "验证码无效或已过期"

    blocked = client.post("/auth/verify", json={"email": email, "code": "000000"})
    assert blocked.status_code == 429
    assert "尝试次数过多" in blocked.json()["detail"]

    throttle = db.scalar(
        select(AuthThrottle).where(AuthThrottle.kind == "verify_email", AuthThrottle.bucket_key == email)
    )
    assert throttle is not None
    assert throttle.failures >= 5
    throttle.window_started = utcnow() - timedelta(seconds=get_settings().code_ttl_seconds + 5)
    db.commit()
    released = client.post("/auth/verify", json={"email": email, "code": "000000"})
    assert released.status_code == 200, released.text


def test_new_request_clears_verify_lockout(client: TestClient, db):
    email = "again@example.com"
    assert client.post("/auth/request-code", json={"email": email}).status_code == 200
    for _ in range(5):
        assert client.post("/auth/verify", json={"email": email, "code": "111111"}).status_code == 400
    assert client.post("/auth/verify", json={"email": email, "code": "000000"}).status_code == 429

    cooling = client.post("/auth/request-code", json={"email": email})
    assert cooling.status_code == 429
    assert "请求过于频繁" in cooling.json()["detail"]

    row = db.scalar(select(AuthCodeRequest).where(AuthCodeRequest.email == email))
    row.created_at = utcnow() - timedelta(seconds=120)
    db.commit()
    assert client.post("/auth/request-code", json={"email": email}).status_code == 200
    assert client.post("/auth/verify", json={"email": email, "code": "000000"}).status_code == 200


def test_verify_lockout_per_ip(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth_codes, "VERIFY_IP_LIMIT", 2)
    assert client.post("/auth/request-code", json={"email": "a@example.com"}).status_code == 200
    assert client.post("/auth/verify", json={"email": "a@example.com", "code": "111111"}).status_code == 400
    assert client.post("/auth/verify", json={"email": "a@example.com", "code": "111111"}).status_code == 400
    assert client.post("/auth/request-code", json={"email": "b@example.com"}).status_code == 200
    blocked = client.post("/auth/verify", json={"email": "b@example.com", "code": "000000"})
    assert blocked.status_code == 429
    assert "当前网络" in blocked.json()["detail"]
