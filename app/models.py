"""Core tables. v1: one membership per user; photo kind checks child_id."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.services.clock import utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Family(Base):
    __tablename__ = "families"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Site root the admin gives elders. Empty means paste is blocked. Nullable, no default.
    family_access_url: Mapped[str | None] = mapped_column(String(512), nullable=True)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "family_id", name="uq_membership_user_family"),
        CheckConstraint(
            "role IN ('admin', 'uploader', 'viewer')",
            name="ck_membership_role",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique(user_id) is the v1 one-family rule. AC10 keeps it: many families
    # may share one instance, but a user still has at most one membership.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    relation_label: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(16))
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Child(Base):
    __tablename__ = "children"

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    birth_date: Mapped[date] = mapped_column(Date)
    avatar_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Photo(Base):
    __tablename__ = "photos"
    __table_args__ = (
        CheckConstraint("kind IN ('child', 'self')", name="ck_photo_kind"),
        CheckConstraint(
            "(kind = 'child' AND child_id IS NOT NULL) OR (kind = 'self' AND child_id IS NULL)",
            name="ck_photo_kind_child",
        ),
        Index("ix_photo_child_taken", "child_id", "taken_on"),
        Index("ix_photo_uploader_kind", "uploader_id", "kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    child_id: Mapped[int | None] = mapped_column(ForeignKey("children.id"), nullable=True)
    taken_on: Mapped[date] = mapped_column(Date)
    storage_path: Mapped[str] = mapped_column(String(80))
    # Written at upload from the current STORAGE_BACKEND. Reads use this column,
    # not the env at request time. create_all does not add it to an existing table.
    storage_backend: Mapped[str] = mapped_column(String(16), default="local", server_default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Journal(Base):
    """A family note for one child on one calendar day. Soft-delete keeps the idempotency key."""

    __tablename__ = "journals"
    __table_args__ = (
        UniqueConstraint("author_id", "client_request_id", name="uq_journal_author_key"),
        CheckConstraint(
            "deleted_at IS NOT NULL OR length(body) > 0",
            name="ck_journal_body",
        ),
        Index("ix_journal_family_child_date", "family_id", "child_id", "on_date"),
        Index("ix_journal_author", "author_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # No FK: deleting a child must not be blocked by a journal, and the row stays for export.
    child_id: Mapped[int] = mapped_column(index=True)
    on_date: Mapped[date] = mapped_column(Date)
    body: Mapped[str] = mapped_column(Text)
    client_request_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JournalChild(Base):
    """Ordered children on one journal. child_id has no FK, same as journals.child_id."""

    __tablename__ = "journal_children"
    __table_args__ = (
        UniqueConstraint("journal_id", "child_id", name="uq_journal_child"),
        Index("ix_journal_child_child", "child_id"),
        Index("ix_journal_child_journal", "journal_id"),
        CheckConstraint("position >= 0", name="ck_journal_child_pos"),
    )

    journal_id: Mapped[int] = mapped_column(
        ForeignKey("journals.id", ondelete="CASCADE"),
        primary_key=True,
    )
    child_id: Mapped[int] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column()


class JournalPhoto(Base):
    """Ordered photo references. photo_id has no FK so a deleted photo leaves the journal intact."""

    __tablename__ = "journal_photos"
    __table_args__ = (
        UniqueConstraint("journal_id", "photo_id", name="uq_journal_photo"),
        CheckConstraint("position BETWEEN 0 AND 8", name="ck_journal_photo_pos"),
    )

    journal_id: Mapped[int] = mapped_column(ForeignKey("journals.id"), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    photo_id: Mapped[int] = mapped_column(index=True)


class Video(Base):
    """One short video for exactly one child on one calendar day.

    Soft-delete clears the file names and keeps the row so the idempotency key
    cannot be reused. child_id has no foreign key, same as journals.
    """

    __tablename__ = "videos"
    __table_args__ = (
        UniqueConstraint("uploader_id", "client_request_id", name="uq_video_uploader_key"),
        CheckConstraint(
            "deleted_at IS NOT NULL OR (byte_size > 0 AND length(storage_path) > 0)",
            name="ck_video_blob",
        ),
        Index("ix_video_family_child_date", "family_id", "child_id", "on_date"),
        Index("ix_video_uploader", "uploader_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"))
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # No FK: a future child delete must not be blocked by a video row.
    child_id: Mapped[int] = mapped_column()
    on_date: Mapped[date] = mapped_column(Date)
    duration_ms: Mapped[int] = mapped_column()
    byte_size: Mapped[int] = mapped_column()
    container: Mapped[str] = mapped_column(String(8))
    storage_path: Mapped[str] = mapped_column(String(80))
    poster_path: Mapped[str | None] = mapped_column(String(80), nullable=True)
    storage_backend: Mapped[str] = mapped_column(String(16), default="local")
    client_request_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VideoChild(Base):
    """Ordered children on one video. child_id has no FK, same as videos.child_id."""

    __tablename__ = "video_children"
    __table_args__ = (
        UniqueConstraint("video_id", "child_id", name="uq_video_child"),
        Index("ix_video_child_child", "child_id"),
        Index("ix_video_child_video", "video_id"),
        CheckConstraint("position >= 0", name="ck_video_child_pos"),
    )

    video_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"),
        primary_key=True,
    )
    child_id: Mapped[int] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column()


class AuthCode(Base):
    __tablename__ = "auth_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    # HMAC-SHA256 hex digest. The 6-digit code is never stored.
    code: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthCodeRequest(Base):
    """Accepted request-code events. Email cooldown and IP caps read this table."""

    __tablename__ = "auth_code_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    ip: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthThrottle(Base):
    """Failed verify counts. kind is verify_email or verify_ip."""

    __tablename__ = "auth_throttles"
    __table_args__ = (UniqueConstraint("kind", "bucket_key", name="uq_auth_throttle_kind_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    bucket_key: Mapped[str] = mapped_column(String(255))
    failures: Mapped[int] = mapped_column(default=0)
    window_started: Mapped[datetime] = mapped_column(DateTime(timezone=True))
