"""Short prefixed public ID generator.

Format: ``{prefix}-{14 base62 chars}``

The 14-char body encodes an 83-bit unsigned integer:
    high 41 bits  : milliseconds since 2024-01-01T00:00:00Z (UTC)
    low  42 bits  : cryptographically-random per ID

Sortable at millisecond granularity across processes; strictly increasing
within a single process via an in-memory monotonic factory (same approach
as ULID's monotonic mode).
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime

# Custom epoch: 2024-01-01T00:00:00Z
_EPOCH_MS: int = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)

_TS_BITS: int = 41
_RAND_BITS: int = 42
_RAND_MASK: int = (1 << _RAND_BITS) - 1

_BASE62_ALPHABET: str = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_BASE62_INDEX: dict[str, int] = {c: i for i, c in enumerate(_BASE62_ALPHABET)}
_BODY_LEN: int = 14


@dataclass
class _State:
    last_ms: int = 0
    last_rand: int = 0


_STATE: _State = _State()
_LOCK: threading.Lock = threading.Lock()


# Prefix constants — keep the table in sync with the spec.
PREFIX_ORGANIZATION: str = "org"
PREFIX_WORKSPACE: str = "ws"
PREFIX_USER: str = "usr"
PREFIX_CONVERSATION: str = "conv"
PREFIX_ATTACHMENT: str = "atch"
PREFIX_ARTIFACT: str = "art"
PREFIX_ARTIFACT_VERSION: str = "artv"
PREFIX_AGENT_CONFIG: str = "agt"
PREFIX_USER_SANDBOX: str = "sbx"
PREFIX_SKILL: str = "skl"
PREFIX_SKILL_VERSION: str = "sklv"
PREFIX_ORG_SKILL_INSTALL: str = "osi"
PREFIX_MCP_SERVER: str = "mcp"
PREFIX_WORKSPACE_MCP_CREDENTIAL: str = "wmc"
PREFIX_USER_MCP_CREDENTIAL: str = "umc"
PREFIX_PROVIDER: str = "prv"
PREFIX_MODEL: str = "mdl"
PREFIX_CREDENTIAL: str = "cred"
PREFIX_BILLING_EVENT: str = "bill"
PREFIX_BILLING_LLM_EVENT: str = "llmb"
PREFIX_ORG_PROVIDER_OVERRIDE: str = "opo"


def _now_ms() -> int:
    return int(time.time() * 1000) - _EPOCH_MS


def _base62_encode(n: int, length: int) -> str:
    if n < 0:
        raise ValueError("cannot encode negative integer")
    out: list[str] = []
    for _ in range(length):
        n, r = divmod(n, 62)
        out.append(_BASE62_ALPHABET[r])
    if n != 0:
        raise ValueError(f"value too large for {length} base62 chars")
    return "".join(reversed(out))


def _base62_decode(s: str) -> int:
    n = 0
    for c in s:
        n = n * 62 + _BASE62_INDEX[c]
    return n


def _next_int() -> int:
    """Return the next 83-bit ID integer, monotonically increasing within
    this process even across same-ms or backwards-clock conditions."""
    with _LOCK:
        now = _now_ms()
        if now <= _STATE.last_ms:
            new_rand = (_STATE.last_rand + 1) & _RAND_MASK
            if new_rand == 0:
                # 42-bit rand exhausted within one logical ms → spill forward.
                _STATE.last_ms += 1
                new_rand = secrets.randbits(_RAND_BITS)
            _STATE.last_rand = new_rand
        else:
            _STATE.last_ms = now
            _STATE.last_rand = secrets.randbits(_RAND_BITS)
        return (_STATE.last_ms << _RAND_BITS) | _STATE.last_rand


def generate_public_id(prefix: str) -> str:
    """Generate a new public ID with the given table prefix.

    The prefix must be 2–4 lowercase ASCII letters/digits. This is enforced
    once here so misuse from a model definition fails fast at import time.
    """
    if not (2 <= len(prefix) <= 4) or not prefix.isascii() or not prefix.islower():
        raise ValueError(f"invalid prefix: {prefix!r}")
    body = _base62_encode(_next_int(), _BODY_LEN)
    return f"{prefix}-{body}"
