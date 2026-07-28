"""UUIDv7 generation.

Version 7 UUIDs embed a millisecond Unix timestamp in their most significant
bits, so they sort chronologically while remaining globally unique. That matters
for primary keys: v4 keys are uniformly random, so every insert lands at a random
point in the B-tree, scattering writes across the index and destroying page
locality. v7 keys append at the right edge like a sequence, while staying safe to
expose publicly and safe to generate on multiple nodes without coordination.

Hand-rolled rather than taking a dependency: the layout is 36 lines of RFC 9562,
and a third-party package in the hot path of every insert is not worth the
supply-chain surface (§28.4).
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Return a time-ordered UUIDv7.

    Layout (RFC 9562):
        48 bits  unix_ts_ms      big-endian milliseconds since epoch
         4 bits  version         0b0111
        12 bits  rand_a          sub-millisecond ordering entropy
         2 bits  variant         0b10
        62 bits  rand_b          random
    """
    ts_ms = time.time_ns() // 1_000_000
    rand = os.urandom(10)

    b = bytearray(16)
    b[0:6] = ts_ms.to_bytes(6, "big")
    # Version nibble in the high half of byte 6, 12 bits of entropy follow.
    b[6] = 0x70 | (rand[0] & 0x0F)
    b[7] = rand[1]
    # Variant bits 10xx in the high half of byte 8.
    b[8] = 0x80 | (rand[2] & 0x3F)
    b[9:16] = rand[3:10]
    return uuid.UUID(bytes=bytes(b))


def uuid7_str() -> str:
    return str(uuid7())
