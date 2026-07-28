"""Organization and workspace DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.db.models.enums import Role
from app.schemas.common import Schema


class OrganizationOut(Schema):
    id: uuid.UUID
    name: str
    slug: str
    plan: str
    created_at: datetime
    role: Role = Field(description="The requesting user's role in this organization.")
    workspace_count: int = 0
    member_count: int = 0


class MemberOut(Schema):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    joined_at: datetime


class WorkspaceCreate(Schema):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class WorkspaceUpdate(Schema):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class WorkspaceOut(Schema):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    slug: str
    description: str | None
    created_at: datetime
    #: The caller's role, so the UI can hide actions it would be refused anyway.
    role: Role | None = None
    document_count: int = 0
    ready_document_count: int = 0
    chunk_count: int = 0
