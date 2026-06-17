"""HMAC-signed, Redis-backed one-shot SSO state tokens.

Same wire format as ``cubebox.mcp.oauth.state`` — base64url payload + dot +
base64url HMAC-SHA256 signature — but the payload carries SSO-specific
fields (``sso_connection_id``, ``protocol``, ``org_id``, optional OIDC
``nonce``) so the callback can resolve the right connection and reject
cross-protocol replays.

Lifecycle:

- ``issue`` mints a fresh nonce, signs the payload, writes
  ``sso_state:{state} -> "1"`` to redis with TTL = ``ttl_seconds``,
  returns the wire token.
- ``consume`` verifies the HMAC (constant-time), atomically deletes the
  redis key (single-shot), and returns the parsed payload.
- ``attach_pkce`` / ``consume_pkce`` sidecar-store the PKCE verifier so it
  doesn't ride through the user agent.
- ``attach_saml_request_id`` / ``consume_saml_request_id`` mirror the
  PKCE sidecar for the SAML AuthnRequest ID — we store it separately so
  the (potentially signed) AuthnRequest doesn't have to be rebuilt at
  callback time.

The module is pure: callers wire in the redis client and the HMAC key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis

_REDIS_KEY_PREFIX = "sso_state:"
_NONCE_BYTES = 16


class SSOStateInvalid(Exception):
    """Raised when the state token's format or HMAC signature is invalid."""


class SSOStateExpired(Exception):
    """Raised when the redis-backed state is missing (TTL elapsed or consumed)."""


@dataclass(frozen=True)
class SSOStatePayload:
    """Decoded SSO state payload returned by ``SSOStateStore.consume``.

    ``sso_connection_id`` is None for social login (e.g. Google) which has
    no per-org connection row. ``protocol`` is one of
    ``"oidc" | "saml" | "google"`` — every callback MUST assert this matches
    its own protocol before consuming the token, to block cross-protocol
    replay. ``org_id`` is None for social login (no org context until
    identity resolution). ``nonce`` is the OIDC nonce echoed back in the
    ID token; None for SAML. The SAML AuthnRequest ID lives in a sidecar
    (see ``attach_saml_request_id``) so the signed AuthnRequest's
    RelayState stays untouched.
    """

    sso_connection_id: str | None
    protocol: str
    org_id: str | None
    issued_at: datetime
    nonce: str | None = None


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class SSOStateStore:
    """Issues and consumes HMAC-signed, single-use SSO state tokens."""

    def __init__(
        self,
        *,
        redis: Redis,
        secret_key: bytes,
        ttl_seconds: int = 300,
    ) -> None:
        if not secret_key:
            raise ValueError("secret_key must be non-empty bytes")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._redis = redis
        self._secret_key = secret_key
        self._ttl_seconds = ttl_seconds

    async def issue(
        self,
        *,
        sso_connection_id: str | None,
        protocol: str,
        org_id: str | None,
        oidc_nonce: str | None = None,
    ) -> str:
        """Mint a new state token and persist it for one-shot consumption."""
        ts_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        rand = secrets.token_hex(_NONCE_BYTES)
        payload: dict[str, object] = {
            "sso_connection_id": sso_connection_id,
            "protocol": protocol,
            "org_id": org_id,
            "ts": ts_ms,
            "rand": rand,
            "oidc_nonce": oidc_nonce,
        }
        # ``sort_keys`` keeps the canonical bytes deterministic across runs.
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(self._secret_key, payload_bytes, hashlib.sha256).digest()
        state = f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"
        await self._redis.set(_REDIS_KEY_PREFIX + state, "1", ex=self._ttl_seconds)
        return state

    async def consume(self, state: str) -> SSOStatePayload:
        """Verify, atomically delete, and decode an SSO state token.

        Raises:
            SSOStateInvalid: format mismatch or HMAC verification failed.
            SSOStateExpired: redis key missing (TTL elapsed or already consumed).
        """
        payload_bytes = self._verify(state)
        deleted = await self._redis.delete(_REDIS_KEY_PREFIX + state)
        if not deleted:
            raise SSOStateExpired("SSO state expired or already consumed")
        try:
            payload = json.loads(payload_bytes)
            sso_connection_id = payload["sso_connection_id"]
            protocol = str(payload["protocol"])
            org_id = payload["org_id"]
            ts_ms = int(payload["ts"])
            oidc_nonce = payload.get("oidc_nonce")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SSOStateInvalid("SSO state payload malformed") from exc
        return SSOStatePayload(
            sso_connection_id=(str(sso_connection_id) if sso_connection_id is not None else None),
            protocol=protocol,
            org_id=str(org_id) if org_id is not None else None,
            issued_at=datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
            nonce=str(oidc_nonce) if oidc_nonce is not None else None,
        )

    async def attach_pkce(self, *, state: str, verifier: str) -> None:
        """Persist the PKCE verifier under the same TTL as the state token.

        Stored under ``sso_state:pkce:{state}`` so the callback can complete
        the token exchange without the verifier riding through the user
        agent.
        """
        await self._redis.set(
            _REDIS_KEY_PREFIX + "pkce:" + state,
            verifier,
            ex=self._ttl_seconds,
        )

    async def consume_pkce(self, state: str) -> str | None:
        """Atomically read-and-delete the PKCE verifier for this state."""
        key = _REDIS_KEY_PREFIX + "pkce:" + state
        raw = await self._redis.get(key)
        if raw is None:
            return None
        await self._redis.delete(key)
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return str(raw)

    async def attach_saml_request_id(self, *, state: str, request_id: str) -> None:
        """Sidecar-store the SAML AuthnRequest ID so the signed AuthnRequest
        is not rebuilt at callback time. Mirrors ``attach_pkce``."""
        await self._redis.set(
            _REDIS_KEY_PREFIX + "samlreq:" + state,
            request_id,
            ex=self._ttl_seconds,
        )

    async def consume_saml_request_id(self, state: str) -> str | None:
        """Atomically read-and-delete the SAML AuthnRequest ID for this state."""
        key = _REDIS_KEY_PREFIX + "samlreq:" + state
        raw = await self._redis.get(key)
        if raw is None:
            return None
        await self._redis.delete(key)
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return str(raw)

    def _verify(self, state: str) -> bytes:
        """Decode + HMAC-verify the wire token. Returns canonical payload bytes."""
        if not isinstance(state, str) or "." not in state:
            raise SSOStateInvalid("SSO state has invalid format")
        payload_b64, sig_b64 = state.split(".", 1)
        try:
            payload_bytes = _b64url_decode(payload_b64)
            sig = _b64url_decode(sig_b64)
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise SSOStateInvalid("SSO state base64 decode failed") from exc
        expected = hmac.new(self._secret_key, payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, sig):
            raise SSOStateInvalid("SSO state HMAC mismatch")
        return payload_bytes
