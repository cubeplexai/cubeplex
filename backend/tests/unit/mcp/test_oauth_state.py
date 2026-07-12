"""Unit tests for cubeplex.mcp.oauth.state."""

from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest

from cubeplex.mcp.exceptions import OAuthStateExpired, OAuthStateInvalid
from cubeplex.mcp.oauth.state import OAuthStatePayload, OAuthStateStore

SECRET = b"unit-test-secret-key-32bytes!!!!"


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield fake
    finally:
        await fake.flushall()


async def test_issue_then_consume_round_trip(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    store = OAuthStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)
    state = await store.issue(connector_id="ins-1", actor_user_id="usr-7")

    payload = await store.consume(state)

    assert isinstance(payload, OAuthStatePayload)
    assert payload.connector_id == "ins-1"
    assert payload.actor_user_id == "usr-7"
    assert payload.issued_at.tzinfo is not None  # UTC


async def test_consume_twice_raises_expired(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    store = OAuthStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)
    state = await store.issue(connector_id="ins-1", actor_user_id="usr-7")
    await store.consume(state)

    with pytest.raises(OAuthStateExpired):
        await store.consume(state)


async def test_tampered_state_raises_invalid(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    store = OAuthStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)
    state = await store.issue(connector_id="ins-1", actor_user_id="usr-7")

    # Flip a byte after the dot (the HMAC signature half).
    payload_b64, sig_b64 = state.split(".", 1)
    flipped_char = "a" if sig_b64[0] != "a" else "b"
    tampered = f"{payload_b64}.{flipped_char}{sig_b64[1:]}"

    with pytest.raises(OAuthStateInvalid):
        await store.consume(tampered)


async def test_malformed_state_raises_invalid(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    store = OAuthStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)

    with pytest.raises(OAuthStateInvalid):
        await store.consume("no-dot-in-here")


async def test_ttl_expiry_raises_expired(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """If the redis key has been evicted (TTL elapsed), consume raises Expired."""
    store = OAuthStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)
    state = await store.issue(connector_id="ins-1", actor_user_id="usr-7")

    # Simulate TTL elapsed by deleting the underlying key directly.
    deleted = await fake_redis.delete(f"mcp_oauth_state:{state}")
    assert deleted == 1

    with pytest.raises(OAuthStateExpired):
        await store.consume(state)


async def test_hmac_uses_caller_provided_secret(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """A token issued under one secret must not validate under another."""
    issuer_store = OAuthStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)
    state = await issuer_store.issue(connector_id="ins-1", actor_user_id="usr-7")

    other_store = OAuthStateStore(
        redis=fake_redis,
        secret_key=b"different-secret-32-bytes-xx-yy!",
        ttl_seconds=300,
    )

    with pytest.raises(OAuthStateInvalid):
        await other_store.consume(state)


async def test_secret_key_must_be_non_empty(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    with pytest.raises(ValueError):
        OAuthStateStore(redis=fake_redis, secret_key=b"", ttl_seconds=300)


async def test_ttl_must_be_positive(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    with pytest.raises(ValueError):
        OAuthStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=0)
