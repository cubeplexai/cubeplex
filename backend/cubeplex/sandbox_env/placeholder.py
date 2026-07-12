"""Opaque placeholder tokens injected into the sandbox in place of real secrets.

A tool reads the env var, sends the placeholder in a header; the egress addon
scans headers for this pattern and exchanges it for the real secret. The token
is high-entropy so header scanning cannot accidentally match real data.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets

_PREFIX = "cbxref_"
# 160 bits of randomness, base32 (no padding, uppercase A-Z2-7).
_RAND_BYTES = 20

# Recognizable, self-delimiting: prefix + fixed-length base32 body.
PLACEHOLDER_RE = re.compile(r"cbxref_[A-Z2-7]{32}")


def mint_placeholder() -> str:
    body = base64.b32encode(secrets.token_bytes(_RAND_BYTES)).decode("ascii").rstrip("=")
    return f"{_PREFIX}{body}"


def hash_placeholder(placeholder: str) -> str:
    """Stable hex SHA-256; only the hash is persisted in the ref record."""
    return hashlib.sha256(placeholder.encode("ascii")).hexdigest()
