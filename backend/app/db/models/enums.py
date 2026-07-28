"""Domain enumerations.

Declared as Python ``StrEnum`` and stored as native Postgres enums. A CHECK
constraint on a text column would work too, but a real enum type gives the
database the same closed set the application has, so a bad value cannot be
written by a migration, a fixture or a psql session.

``values_callable`` is passed at every column site so Postgres stores the *value*
("PENDING") rather than the Python member *name*. Without it SQLAlchemy stores
names, and the two silently diverge the first time a member is renamed.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Organization-level role. Ordered least to most privileged."""

    VIEWER = "VIEWER"
    MEMBER = "MEMBER"
    ADMIN = "ADMIN"
    OWNER = "OWNER"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]

    def satisfies(self, required: Role) -> bool:
        """True if this role is at least as privileged as ``required``."""
        return self.rank >= required.rank


_ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 0,
    Role.MEMBER: 1,
    Role.ADMIN: 2,
    Role.OWNER: 3,
}


class SourceType(StrEnum):
    PDF = "PDF"
    DOCX = "DOCX"
    PPTX = "PPTX"
    MARKDOWN = "MARKDOWN"
    TXT = "TXT"
    URL = "URL"


class DocumentStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"  # admin removed it from retrieval without deleting (§27.5)
    DELETED = "DELETED"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"


class FinishReason(StrEnum):
    STOP = "STOP"
    LENGTH = "LENGTH"
    CLIENT_ABORT = "CLIENT_ABORT"
    REFUSED = "REFUSED"  # the §10 relevance gate fired; no provider call was made
    ERROR = "ERROR"


class UsageKind(StrEnum):
    CHAT = "CHAT"
    EMBED = "EMBED"
    TITLE = "TITLE"
    JUDGE = "JUDGE"
