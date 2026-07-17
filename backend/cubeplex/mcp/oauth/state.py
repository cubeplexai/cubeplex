"""HMAC-signed, redis-backed one-shot OAuth state tokens.

The wire format is::

    state = base64url(payload_json) + "." + base64url(hmac_sha256)

where ``payload_json`` carries ``{connector_id, connector_id, actor_user_id, ts, nonce,
grant_scope, workspace_id, user_id}`` (the last three honor the four-layer
grant shape — workspace_id and user_id are nullable per grant_scope) and
``hmac_sha256`` is computed over the canonical payload bytes using the
caller-supplied ``secret_key``.

Lifecycle:

- ``issue`` generates a fresh nonce, signs the payload, writes
  ``mcp_oauth_state:{state} -> "1"`` to redis with TTL = ``ttl_seconds``,
  and returns the wire token.
- ``consume`` verifies the HMAC (constant-time), atomically deletes the
  redis key (single-shot), and returns the parsed payload. Missing key
  means TTL elapsed or the token was already consumed.

The module is pure: callers wire in the redis client and the secret key.
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

from cubeplex.mcp.exceptions import OAuthStateExpired, OAuthStateInvalid

_REDIS_KEY_PREFIX = "mcp_oauth_state:"
_NONCE_BYTES = 16


@dataclass(frozen=True)
class OAuthStatePayload:
    """Decoded OAuth state payload returned by ``OAuthStateStore.consume``.

    Four-layer grant fields: ``grant_scope`` is one of
    ``{"org", "workspace", "user"}``; ``workspace_id`` and ``user_id`` are
    populated per the grant scope.
    """

    connector_id: str
    actor_user_id: str
    issued_at: datetime  # UTC
    grant_scope: str | None = None
    workspace_id: str | None = None
    user_id: str | None = None
    frontend_origin: str | None = None


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


class OAuthStateStore:
    """Issues and consumes HMAC-signed, single-use OAuth state tokens."""

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
        actor_user_id: str,
        connector_id: str,
        grant_scope: str | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
        frontend_origin: str | None = None,
    ) -> str:
        """Mint a new state token and persist it for one-shot consumption."""
        ts_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
        nonce = secrets.token_hex(_NONCE_BYTES)
        payload: dict[str, object] = {
            "actor_user_id": actor_user_id,
            "ts": ts_ms,
            "nonce": nonce,
        }
        payload["connector_id"] = connector_id
        if grant_scope is not None:
            payload["grant_scope"] = grant_scope
        if workspace_id is not None:
            payload["workspace_id"] = workspace_id
        if user_id is not None:
            payload["user_id"] = user_id
        if frontend_origin is not None:
            payload["frontend_origin"] = frontend_origin
        # ``sort_keys`` keeps the canonical bytes deterministic across runs.
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(self._secret_key, payload_bytes, hashlib.sha256).digest()
        state = f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"
        await self._redis.set(_REDIS_KEY_PREFIX + state, "1", ex=self._ttl_seconds)
        return state

    async def consume(self, state: str) -> OAuthStatePayload:
        """Verify, atomically delete, and decode a state token.

        Raises:
            OAuthStateInvalid: format mismatch or HMAC verification failed.
            OAuthStateExpired: redis key missing (TTL elapsed or already consumed).
        """
        payload_bytes = self._verify(state)
        deleted = await self._redis.delete(_REDIS_KEY_PREFIX + state)
        if not deleted:
            raise OAuthStateExpired("OAuth state expired or already consumed")
        try:
            payload = json.loads(payload_bytes)
            connector_id = str(payload["connector_id"])
            actor_user_id = str(payload["actor_user_id"])
            ts_ms = int(payload["ts"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OAuthStateInvalid("OAuth state payload malformed") from exc
        issued_at = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
        raw_grant_scope = payload.get("grant_scope") if isinstance(payload, dict) else None
        raw_ws_id = payload.get("workspace_id") if isinstance(payload, dict) else None
        raw_user_id = payload.get("user_id") if isinstance(payload, dict) else None
        raw_fe_origin = payload.get("frontend_origin") if isinstance(payload, dict) else None
        return OAuthStatePayload(
            connector_id=connector_id,
            actor_user_id=actor_user_id,
            issued_at=issued_at,
            grant_scope=str(raw_grant_scope) if raw_grant_scope is not None else None,
            workspace_id=str(raw_ws_id) if raw_ws_id is not None else None,
            user_id=str(raw_user_id) if raw_user_id is not None else None,
            frontend_origin=str(raw_fe_origin) if raw_fe_origin is not None else None,
        )

    async def attach_pkce(self, *, state: str, verifier: str) -> None:
        """Persist the PKCE verifier under the same TTL as the state token.

        Stored under ``mcp_oauth_state:pkce:{state}`` so the callback can
        complete the token exchange without the verifier being passed
        through the user agent.
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

    def _verify(self, state: str) -> bytes:
        """Decode + HMAC-verify the wire token. Returns canonical payload bytes."""
        if not isinstance(state, str) or "." not in state:
            raise OAuthStateInvalid("OAuth state has invalid format")
        payload_b64, sig_b64 = state.split(".", 1)
        try:
            payload_bytes = _b64url_decode(payload_b64)
            sig = _b64url_decode(sig_b64)
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise OAuthStateInvalid("OAuth state base64 decode failed") from exc
        expected = hmac.new(self._secret_key, payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, sig):
            raise OAuthStateInvalid("OAuth state HMAC mismatch")
        return payload_bytes
