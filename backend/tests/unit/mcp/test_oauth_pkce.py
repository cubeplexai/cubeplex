"""Unit tests for cubeplex.mcp.oauth.pkce."""

from __future__ import annotations

import base64
import hashlib
import re

from cubeplex.mcp.oauth.pkce import (
    PKCE_VERIFIER_MAX_LENGTH,
    PKCE_VERIFIER_MIN_LENGTH,
    generate_pkce,
    verify_pkce_pair,
)

VALID_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~\-]+$")


def test_generate_pkce_verifier_length_and_charset() -> None:
    pair = generate_pkce()
    assert PKCE_VERIFIER_MIN_LENGTH <= len(pair.verifier) <= PKCE_VERIFIER_MAX_LENGTH
    assert VALID_VERIFIER_RE.match(pair.verifier) is not None
    assert pair.method == "S256"


def test_generate_pkce_challenge_matches_sha256_b64url_no_pad() -> None:
    pair = generate_pkce()
    expected_digest = hashlib.sha256(pair.verifier.encode("ascii")).digest()
    expected_challenge = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode("ascii")
    assert pair.challenge == expected_challenge


def test_verify_pkce_pair_returns_true_for_valid_pair() -> None:
    pair = generate_pkce()
    assert verify_pkce_pair(pair.verifier, pair.challenge) is True


def test_verify_pkce_pair_returns_false_for_tampered_verifier() -> None:
    pair = generate_pkce()
    tampered = pair.verifier[:-1] + ("A" if pair.verifier[-1] != "A" else "B")
    assert verify_pkce_pair(tampered, pair.challenge) is False


def test_verify_pkce_pair_returns_false_for_short_verifier() -> None:
    assert verify_pkce_pair("short", "anything") is False


def test_verify_pkce_pair_returns_false_for_invalid_charset() -> None:
    bad = "!" * (PKCE_VERIFIER_MIN_LENGTH + 1)
    assert verify_pkce_pair(bad, "anything") is False


def test_generate_pkce_returns_unique_verifiers() -> None:
    pair_a = generate_pkce()
    pair_b = generate_pkce()
    assert pair_a.verifier != pair_b.verifier
    assert pair_a.challenge != pair_b.challenge
