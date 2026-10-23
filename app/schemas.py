"""JSON bodies for the API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class EmailIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    code: str


class MePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    relation_label: str | None = None


class FamilyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    role: str


class RoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str


class ChildIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    birth_date: str
