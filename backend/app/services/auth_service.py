"""Registration and authentication (§9).

Framework-free: takes a Session and plain values, raises domain errors.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AuthenticationError, ConflictError
from app.core.security import hash_password, needs_rehash, verify_password
from app.db.models.enums import Role
from app.db.models.identity import Membership, Organization, User, Workspace

logger = logging.getLogger(__name__)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def normalize_email(email: str) -> str:
    """The single place email casing is decided.

    Lowercased and NFKC-normalised. Without one canonical form,
    ``Ali@x.com`` and ``ali@x.com`` register as two accounts and the unique index
    does not stop it.
    """
    return unicodedata.normalize("NFKC", email).strip().lower()


def slugify(value: str, *, fallback: str = "workspace") -> str:
    slug = _SLUG_STRIP.sub("-", unicodedata.normalize("NFKD", value).lower()).strip("-")
    return (slug or fallback)[:60]


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    user: User
    organization: Organization
    workspace: Workspace


def register(
    db: Session,
    *,
    email: str,
    password: str,
    full_name: str,
    organization_name: str | None = None,
) -> RegistrationResult:
    """Create user, organization and default workspace as one unit.

    All three or none. A user with no organization has nowhere to put documents
    and no role to be authorized against — a half-registered account is a support
    ticket, not a state worth representing.

    The caller commits. This function only flushes (§ db/session.py).
    """
    normalized = normalize_email(email)

    if db.scalar(select(User.id).where(User.email == normalized)) is not None:
        raise ConflictError("An account with that email already exists.")

    user = User(
        email=normalized,
        password_hash=hash_password(password),
        full_name=full_name.strip(),
    )
    db.add(user)
    db.flush()  # assigns user.id, needed by the FKs below

    org_name = (organization_name or f"{full_name.strip()}'s Organization")[:200]
    organization = Organization(
        name=org_name,
        slug=_unique_org_slug(db, slugify(org_name, fallback="org")),
        created_by=user.id,
    )
    db.add(organization)
    db.flush()

    # The registrant owns the organization they created.
    db.add(Membership(user_id=user.id, org_id=organization.id, role=Role.OWNER))

    workspace = Workspace(
        org_id=organization.id,
        name="General",
        slug="general",
        description="Default workspace",
        created_by=user.id,
    )
    db.add(workspace)

    try:
        db.flush()
    except IntegrityError as exc:
        # Lost a race against a concurrent registration with the same email.
        db.rollback()
        raise ConflictError("An account with that email already exists.") from exc

    logger.info(
        "user registered",
        extra={
            "event": "auth.registered",
            "user_id": str(user.id),
            "org_id": str(organization.id),
        },
    )
    return RegistrationResult(user=user, organization=organization, workspace=workspace)


def authenticate(db: Session, *, email: str, password: str) -> User:
    """Verify credentials and return the user.

    One error message for every failure mode — unknown email, wrong password,
    disabled account. Distinguishing them tells an attacker which half of the
    credential pair to keep working on.
    """
    normalized = normalize_email(email)
    user = db.scalar(select(User).where(User.email == normalized))

    # Runs the hash even when the user is absent, so a missing account and a bad
    # password cost the same wall-clock time (§ core/security.verify_password).
    if not verify_password(password, user.password_hash if user else None):
        logger.info("failed login", extra={"event": "auth.login_failed"})
        raise AuthenticationError("Incorrect email or password.")

    assert user is not None  # verify_password returns False when user is None

    if not user.is_active:
        raise AuthenticationError("Incorrect email or password.")

    # Transparently upgrade the stored hash when cost parameters have changed.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    user.last_login_at = datetime.now(UTC)
    db.flush()
    return user


def load_memberships(db: Session, *, user_id: uuid.UUID) -> list[tuple[Membership, Organization]]:
    rows = db.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.org_id)
        .where(Membership.user_id == user_id)
        .order_by(Organization.created_at)
    ).all()
    return [(m, o) for m, o in rows]


def _unique_org_slug(db: Session, base: str) -> str:
    """Organization slugs are globally unique; append a counter on collision."""
    candidate = base
    for suffix in range(0, 100):
        if suffix:
            candidate = f"{base}-{suffix}"
        taken = db.scalar(
            select(func.count()).select_from(Organization).where(Organization.slug == candidate)
        )
        if not taken:
            return candidate
    return f"{base}-{uuid.uuid4().hex[:8]}"
