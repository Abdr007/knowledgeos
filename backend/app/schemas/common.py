"""Shared DTO primitives.

DTOs are the API contract. ORM objects never cross the boundary: a model carries
lazy relationships, internal columns and a mutable identity map, and serialising
one directly is how `password_hash` ends up in a JSON response.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class Schema(BaseModel):
    """Base for every DTO."""

    model_config = ConfigDict(
        from_attributes=True,  # build from ORM objects explicitly, never implicitly
        extra="forbid",  # an unknown field is a client bug; say so rather than ignore it
        str_strip_whitespace=True,
    )


class Page[T](Schema):
    """Cursor-paginated envelope.

    Cursor rather than offset: offset pagination re-scans rows on every page and
    silently skips or repeats items when the underlying set changes between
    requests. UUIDv7 ids are time-ordered, so the id itself is the cursor.
    """

    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False


class ErrorResponse(Schema):
    """The one error shape the frontend parses (§17)."""

    error: str
    detail: str
    fields: dict[str, object] | None = None
    request_id: str | None = None


class Message(Schema):
    detail: str


Cursor = Annotated[str | None, Field(default=None, max_length=64)]
Limit = Annotated[int, Field(default=25, ge=1, le=100)]
