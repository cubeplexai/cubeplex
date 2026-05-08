"""PKCE (RFC 7636) verifier + S256 challenge generation.

Pure logic — no I/O. The verifier is a 64-byte random buffer encoded as
base64url-without-padding (~86 chars), comfortably within the RFC 7636
[43, 128] character range. The challenge is ``base64url(sha256(verifier))``
without padding.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Literal

PKCE_VERIFIER_MIN_LENGTH = 43
PKCE_VERIFIER_MAX_LENGTH = 128
PKCE_VERIFIER_CHARSET = re.compile(r"^[A-Za-z0-9._~\-]+$")
_VERIFIER_RANDOM_BYTES = 64


@dataclass(frozen=True)
class PKCEChallenge:
    """A PKCE verifier/challenge pair using the S256 method."""

    verifier: str
    challenge: str
    method: Literal["S256"] = "S256"


def _b64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url_no_pad(digest)


def generate_pkce() -> PKCEChallenge:
    """Generate a fresh PKCE verifier + S256 challenge pair."""
    verifier = _b64url_no_pad(secrets.token_bytes(_VERIFIER_RANDOM_BYTES))
    # Defensive sanity checks — base64url output is guaranteed within range,
    # but keep the assertions as documentation of the invariant.
    if not (PKCE_VERIFIER_MIN_LENGTH <= len(verifier) <= PKCE_VERIFIER_MAX_LENGTH):
        raise RuntimeError("PKCE verifier length out of RFC 7636 range")
    if not PKCE_VERIFIER_CHARSET.match(verifier):
        raise RuntimeError("PKCE verifier contains invalid characters")
    return PKCEChallenge(verifier=verifier, challenge=_challenge_for(verifier))


def verify_pkce_pair(verifier: str, challenge: str) -> bool:
    """Return True iff ``challenge == base64url(sha256(verifier))`` (S256)."""
    if not isinstance(verifier, str) or not isinstance(challenge, str):
        return False
    if not (PKCE_VERIFIER_MIN_LENGTH <= len(verifier) <= PKCE_VERIFIER_MAX_LENGTH):
        return False
    if not PKCE_VERIFIER_CHARSET.match(verifier):
        return False
    expected = _challenge_for(verifier)
    return hmac.compare_digest(expected, challenge)
