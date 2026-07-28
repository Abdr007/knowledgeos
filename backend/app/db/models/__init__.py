"""Model registry.

Importing this package registers every mapped class on ``Base.metadata``, which
is what Alembic autogenerate reads. A model that is not imported here is invisible
to migrations — it exists in Python and never in the database, and the failure
shows up as a confusing "relation does not exist" at runtime rather than at
migration time. Adding a model means adding it here.
"""

from app.db.base import Base
from app.db.models.content import Chunk, Document, IngestionJob
from app.db.models.conversation import Citation, Conversation, Feedback, Message
from app.db.models.enums import (
    DocumentStatus,
    FinishReason,
    JobStatus,
    MessageRole,
    Role,
    SourceType,
    UsageKind,
)
from app.db.models.identity import Membership, Organization, RefreshToken, User, Workspace
from app.db.models.ops import AuditEvent, UsageEvent
from app.db.models.storage import StoredObject

__all__ = [
    "AuditEvent",
    "Base",
    "Chunk",
    "Citation",
    "Conversation",
    "Document",
    "DocumentStatus",
    "Feedback",
    "FinishReason",
    "IngestionJob",
    "JobStatus",
    "Membership",
    "Message",
    "MessageRole",
    "Organization",
    "RefreshToken",
    "Role",
    "SourceType",
    "StoredObject",
    "UsageEvent",
    "UsageKind",
    "User",
    "Workspace",
]
