"""Domain exception hierarchy and its HTTP mapping (§17).

Services raise these; they never construct ``HTTPException``. That is what keeps
the service layer framework-free (§6) and directly reusable by the ingestion
worker, which has no HTTP context at all. The translation to a status code
happens once, at the API boundary.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base for every expected failure.

    ``status_code`` and ``error`` are class attributes so a handler can map any
    subclass uniformly without a lookup table that drifts from the hierarchy.
    """

    status_code: int = 500
    error: str = "internal_error"

    def __init__(
        self,
        detail: str,
        *,
        fields: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.fields = fields
        self.retryable = retryable

    def to_payload(self, request_id: str | None = None) -> dict[str, Any]:
        return {
            "error": self.error,
            "detail": self.detail,
            "fields": self.fields,
            "request_id": request_id,
        }


class ValidationError(AppError):
    status_code = 422
    error = "validation_error"


class AuthenticationError(AppError):
    status_code = 401
    error = "authentication_error"


class AuthorizationError(AppError):
    status_code = 403
    error = "authorization_error"


class NotFoundError(AppError):
    """Absent — or present but invisible to this tenant.

    Cross-tenant lookups raise this rather than AuthorizationError on purpose: a
    403 confirms the resource exists, which is an enumeration oracle (§17).
    """

    status_code = 404
    error = "not_found"


class ConflictError(AppError):
    status_code = 409
    error = "conflict"


class PayloadTooLargeError(AppError):
    status_code = 413
    error = "payload_too_large"


class UnsupportedMediaError(AppError):
    status_code = 415
    error = "unsupported_media_type"


class RateLimitError(AppError):
    status_code = 429
    error = "rate_limited"

    def __init__(self, detail: str, *, retry_after: int = 60) -> None:
        super().__init__(detail, retryable=True)
        self.retry_after = retry_after


class ProviderError(AppError):
    """An upstream model provider failed."""

    status_code = 502
    error = "provider_error"

    def __init__(self, detail: str, *, retryable: bool = True) -> None:
        super().__init__(detail, retryable=retryable)


class ProviderTimeoutError(ProviderError):
    status_code = 504
    error = "provider_timeout"


class DependencyError(AppError):
    """Postgres, Qdrant or Redis is unavailable."""

    status_code = 503
    error = "dependency_unavailable"

    def __init__(self, detail: str) -> None:
        super().__init__(detail, retryable=True)


class TenantIsolationError(AppError):
    """A record crossed a workspace boundary.

    This must never happen. It is a distinct type so it can be alarmed on
    specifically (§26.3) rather than disappearing into a generic 500, and it is
    deliberately opaque to the client.
    """

    status_code = 500
    error = "internal_error"
