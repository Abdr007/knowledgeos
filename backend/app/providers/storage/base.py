"""Storage protocol (§29.1).

Originals are kept so a document can be re-parsed with a better parser, and so a
citation can deep-link to the source file. The protocol keeps that behind two
methods, which is the whole cost of supporting both a local volume and S3.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Object storage. Keys are opaque and generated — never user input (§18)."""

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...
