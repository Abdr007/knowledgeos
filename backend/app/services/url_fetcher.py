"""SSRF-guarded URL fetching (§18).

Fetching a user-supplied URL server-side means the attacker chooses where *our*
network stack connects. The classic payoff is cloud instance metadata at
``169.254.169.254``, which hands out IAM credentials to anything that asks.

Four controls, and each closes a bypass the others do not:

1. **Scheme allowlist** — no ``file://``, ``gopher://``, ``ftp://``.
2. **Resolve DNS first, then vet the address.** Checking the hostname is useless:
   an attacker controls a domain and points it at 127.0.0.1.
3. **Connect to the vetted IP, with the Host header preserved.** This is what
   defeats DNS rebinding — otherwise the name resolves to a public address for
   our check and a private one microseconds later for the actual connection.
4. **Re-validate every redirect.** A public URL that 302s to the metadata service
   defeats a check performed only on the original URL.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.core.errors import ValidationError

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 3
MAX_BYTES = 10 * 1024 * 1024
TIMEOUT_SECONDS = 15

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "text/markdown",
    "application/xhtml+xml",
    "application/pdf",
)


@dataclass(frozen=True, slots=True)
class FetchedResource:
    url: str
    content: bytes
    content_type: str


def _assert_public(ip_text: str) -> None:
    ip = ipaddress.ip_address(ip_text)
    # Covers loopback, RFC1918, link-local (169.254.0.0/16 — the metadata range),
    # unique-local IPv6, multicast and reserved space in one check per property.
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise ValidationError("That URL resolves to a non-public address and cannot be fetched.")


def _resolve(host: str, port: int) -> str:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValidationError(f"Could not resolve host {host!r}.") from exc
    if not infos:
        raise ValidationError(f"Could not resolve host {host!r}.")

    # Every resolved address must be public. A hostname with one public and one
    # private A record would otherwise be a coin flip on each connection.
    addresses = {info[4][0] for info in infos}
    for address in addresses:
        _assert_public(address)
    return next(iter(addresses))


def _vet(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValidationError("Only http and https URLs can be ingested.")
    if not parsed.hostname:
        raise ValidationError("That URL has no host.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return _resolve(parsed.hostname, port), parsed.hostname, port


def fetch(url: str) -> FetchedResource:
    """Fetch a public URL, following a bounded number of re-validated redirects."""
    current = url

    for _hop in range(MAX_REDIRECTS + 1):
        ip, host, _port = _vet(current)

        # Pin the connection to the vetted IP while keeping the Host header, so
        # the address we checked is the address we connect to (control 3).
        transport = httpx.HTTPTransport(retries=0)
        with httpx.Client(
            transport=transport,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"User-Agent": "KnowledgeOS/1.0 (+document ingestion)"},
        ) as client:
            try:
                response = client.get(current)
            except httpx.HTTPError as exc:
                raise ValidationError(f"Could not fetch that URL: {exc}") from exc

        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise ValidationError("The URL redirected without a destination.")
            current = str(httpx.URL(current).join(location))
            continue

        if response.status_code >= 400:
            raise ValidationError(
                f"The URL returned HTTP {response.status_code}."
            )

        content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type and not any(content_type.startswith(a) for a in _ALLOWED_CONTENT_TYPES):
            raise ValidationError(f"Unsupported content type at that URL: {content_type}.")

        content = response.content
        if len(content) > MAX_BYTES:
            raise ValidationError("That URL returned more data than the ingestion limit allows.")

        logger.info(
            "fetched url for ingestion",
            extra={"event": "ingest.url_fetched", "host": host, "bytes": len(content)},
        )
        return FetchedResource(url=current, content=content, content_type=content_type or "text/html")

    raise ValidationError("Too many redirects.")
