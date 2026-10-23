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
    # unique(user_id) is the v1 one-family rule. The pair unique is redundant and kept.
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthCode(Base):
    __tablename__ = "auth_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    code: Mapped[str] = mapped_column(String(12))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
