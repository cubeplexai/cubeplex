"""Unit tests for cubeplex.sso.state."""

from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest

from cubeplex.sso.state import (
    SSOStateExpired,
    SSOStateInvalid,
    SSOStatePayload,
    SSOStateStore,
)

SECRET = b"sso-unit-test-secret-key-32by!!!"


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
    store = SSOStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)
    state = await store.issue(
        sso_connection_id="sso-test123",
        protocol="oidc",
        org_id="org-test",
        oidc_nonce="nonce-abc",
    )

    assert "." in state
    payload = await store.consume(state)

    assert isinstance(payload, SSOStatePayload)
    assert payload.sso_connection_id == "sso-test123"
    assert payload.protocol == "oidc"
    assert payload.org_id == "org-test"
    assert payload.nonce == "nonce-abc"
    assert payload.issued_at.tzinfo is not None  # UTC


async def test_consume_twice_raises_expired(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    store = SSOStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)
    state = await store.issue(sso_connection_id="sso-test", protocol="oidc", org_id="org-test")
    await store.consume(state)

    with pytest.raises(SSOStateExpired):
        await store.consume(state)


async def test_tampered_state_raises_invalid(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    store = SSOStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)
    state = await store.issue(sso_connection_id="sso-test", protocol="oidc", org_id="org-test")

    # Flip a byte after the dot (the HMAC signature half) so HMAC verify fails.
    payload_b64, sig_b64 = state.split(".", 1)
    flipped_char = "a" if sig_b64[0] != "a" else "b"
    tampered = f"{payload_b64}.{flipped_char}{sig_b64[1:]}"

    with pytest.raises(SSOStateInvalid):
        await store.consume(tampered)


async def test_malformed_state_raises_invalid(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    store = SSOStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)

    with pytest.raises(SSOStateInvalid):
        await store.consume("no-dot-in-here")


async def test_ttl_expiry_raises_expired(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """If the redis key has been evicted (TTL elapsed), consume raises Expired."""
    store = SSOStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)
    state = await store.issue(sso_connection_id="sso-test", protocol="oidc", org_id="org-test")

    # Simulate TTL elapsed by deleting the underlying key directly.
    deleted = await fake_redis.delete(f"sso_state:{state}")
    assert deleted == 1

    with pytest.raises(SSOStateExpired):
        await store.consume(state)


async def test_hmac_uses_caller_provided_secret(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """A token issued under one secret must not validate under another."""
    issuer_store = SSOStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)
    state = await issuer_store.issue(
        sso_connection_id="sso-test", protocol="oidc", org_id="org-test"
    )

    other_store = SSOStateStore(
        redis=fake_redis,
        secret_key=b"different-secret-32-bytes-xx-yy!",
        ttl_seconds=300,
    )

    with pytest.raises(SSOStateInvalid):
        await other_store.consume(state)


async def test_secret_key_must_be_non_empty(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    with pytest.raises(ValueError):
        SSOStateStore(redis=fake_redis, secret_key=b"", ttl_seconds=300)


async def test_ttl_must_be_positive(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    with pytest.raises(ValueError):
        SSOStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=0)


async def test_pkce_attach_and_consume_round_trip(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    store = SSOStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)
    state = await store.issue(sso_connection_id="sso-test", protocol="oidc", org_id="org-test")

    await store.attach_pkce(state=state, verifier="test-verifier")
    verifier = await store.consume_pkce(state)
    assert verifier == "test-verifier"

    # Second consume returns None (single-shot).
    assert await store.consume_pkce(state) is None


async def test_saml_request_id_attach_and_consume_round_trip(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    store = SSOStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)
    state = await store.issue(sso_connection_id="sso-saml", protocol="saml", org_id="org-test")

    await store.attach_saml_request_id(state=state, request_id="_req-id-abc-123")
    request_id = await store.consume_saml_request_id(state)
    assert request_id == "_req-id-abc-123"

    # Second consume returns None (single-shot).
    assert await store.consume_saml_request_id(state) is None


async def test_google_protocol_payload_round_trip(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Social login (Google) uses None for sso_connection_id and org_id."""
    store = SSOStateStore(redis=fake_redis, secret_key=SECRET, ttl_seconds=300)
    state = await store.issue(
        sso_connection_id=None,
        protocol="google",
        org_id=None,
        oidc_nonce="g-nonce",
    )

    payload = await store.consume(state)
    assert payload.sso_connection_id is None
    assert payload.protocol == "google"
    assert payload.org_id is None
    assert payload.nonce == "g-nonce"
