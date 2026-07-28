"""Password hashing, JWT handling, SSRF guard, and format sniffing (§18)."""

from __future__ import annotations

import time
import uuid

import pytest

from app.core.errors import UnsupportedMediaError, ValidationError
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.db.models.enums import Role, SourceType
from app.providers.parsers.formats import source_type_for
from app.services.url_fetcher import _assert_public

# ── passwords ────────────────────────────────────────────────────────────


def test_hash_is_salted_so_identical_passwords_differ():
    assert hash_password("correct horse battery") != hash_password("correct horse battery")


def test_verify_accepts_the_right_password_and_rejects_others():
    stored = hash_password("correct horse battery")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("wrong horse battery", stored)


def test_unknown_user_does_not_raise_and_still_costs_time():
    """The dummy-hash path must swallow its own mismatch.

    Letting it escape turns 'unknown email' into a 500 and hands back as a
    status-code oracle exactly the timing oracle the dummy hash exists to close.
    """
    started = time.perf_counter()
    assert verify_password("anything", None) is False
    assert time.perf_counter() - started > 0.005


# ── tokens ───────────────────────────────────────────────────────────────


def test_access_token_round_trips():
    subject = uuid.uuid4()
    token, jti, _expires = create_access_token(subject)
    payload = decode_access_token(token)
    assert payload["sub"] == str(subject)
    assert payload["jti"] == jti
    assert payload["typ"] == "access"


def test_tampered_token_is_rejected():
    token, _jti, _exp = create_access_token(uuid.uuid4())
    head, body, signature = token.split(".")
    with pytest.raises(Exception):
        decode_access_token(f"{head}.{body}.{signature[:-3]}xyz")


def test_refresh_tokens_are_high_entropy_and_stored_only_as_a_hash():
    a, b = generate_refresh_token(), generate_refresh_token()
    assert a != b
    assert len(a) >= 43
    digest = hash_refresh_token(a)
    assert len(digest) == 64 and digest != a
    assert hash_refresh_token(a) == digest  # deterministic


# ── roles ────────────────────────────────────────────────────────────────


def test_role_ladder_is_ordered():
    assert Role.OWNER.satisfies(Role.ADMIN)
    assert Role.ADMIN.satisfies(Role.MEMBER)
    assert Role.MEMBER.satisfies(Role.VIEWER)
    assert not Role.VIEWER.satisfies(Role.MEMBER)
    assert Role.MEMBER.satisfies(Role.MEMBER)


# ── SSRF guard ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # RFC1918
        "192.168.1.1",  # RFC1918
        "172.16.0.1",  # RFC1918
        "169.254.169.254",  # cloud instance metadata — the payoff target
        "0.0.0.0",
        "::1",
        "fd00::1",  # unique-local IPv6
    ],
)
def test_private_and_metadata_addresses_are_refused(address: str):
    with pytest.raises(ValidationError):
        _assert_public(address)


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "93.184.216.34"])
def test_public_addresses_are_allowed(address: str):
    _assert_public(address)


# ── format sniffing ──────────────────────────────────────────────────────


def test_content_decides_the_format_not_the_extension():
    assert source_type_for("report.pdf", b"%PDF-1.7\nrest") is SourceType.PDF
    # A .pdf that is really a zip is a mislabelled file or an attack. Both are
    # rejected rather than cleaned up and processed.
    with pytest.raises(UnsupportedMediaError):
        source_type_for("evil.pdf", b"PK\x03\x04rest-of-a-zip")


def test_ooxml_uses_the_extension_only_after_confirming_it_is_a_zip():
    assert source_type_for("deck.pptx", b"PK\x03\x04x") is SourceType.PPTX
    assert source_type_for("doc.docx", b"PK\x03\x04x") is SourceType.DOCX
    with pytest.raises(UnsupportedMediaError):
        source_type_for("archive.zip", b"PK\x03\x04x")


def test_binary_without_magic_or_extension_is_rejected():
    with pytest.raises(UnsupportedMediaError):
        source_type_for(None, b"\x00\x01\x02\xff\xfe")


def test_plain_text_is_accepted():
    assert source_type_for("notes.md", b"# Heading") is SourceType.MARKDOWN
    assert source_type_for(None, b"just some readable text") is SourceType.TXT
