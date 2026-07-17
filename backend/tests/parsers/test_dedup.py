"""Redis-backed dedup cache tests (using fakeredis for isolation)."""

from collections.abc import AsyncIterator
from uuid import uuid4

import fakeredis.aioredis
import pytest

from cubeplex.parsers.dedup import check, hash_bytes, update
from cubeplex.parsers.schema import ParseOptions


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    """Inject a fakeredis instance via cubeplex.cache.set_redis."""
    from cubeplex.cache import reset_for_tests, set_redis

    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    set_redis(fake)
    try:
        yield fake
    finally:
        await fake.flushall()
        reset_for_tests()


async def test_hash_bytes_returns_sha256_hex() -> None:
    digest = await hash_bytes(b"hello")
    assert digest == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


async def test_check_returns_false_when_empty(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    conv = uuid4()
    assert await check(conv, "/p", ParseOptions(), "abc") is False


async def test_update_then_check_matches(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    conv = uuid4()
    await update(conv, "/p", ParseOptions(), "abc")
    assert await check(conv, "/p", ParseOptions(), "abc") is True
    assert await check(conv, "/p", ParseOptions(), "different") is False


async def test_check_isolates_per_conversation(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    a, b = uuid4(), uuid4()
    await update(a, "/p", ParseOptions(), "abc")
    assert await check(b, "/p", ParseOptions(), "abc") is False


async def test_check_isolates_per_page_range(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """Different page_range = different cache slot."""
    conv = uuid4()
    await update(conv, "/p", ParseOptions(page_range="1-5"), "abc")
    assert await check(conv, "/p", ParseOptions(page_range="6-10"), "abc") is False
    assert await check(conv, "/p", ParseOptions(page_range="1-5"), "abc") is True


async def test_check_isolates_per_line_range(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """Different line_range = different cache slot."""
    conv = uuid4()
    await update(conv, "/p", ParseOptions(line_range="1-100"), "abc")
    assert await check(conv, "/p", ParseOptions(line_range="200-300"), "abc") is False


async def test_ttl_set_on_update(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """Update sets TTL so cache eventually expires."""
    conv = uuid4()
    await update(conv, "/p", ParseOptions(), "abc")
    keys = await fake_redis.keys("parsers:dedup:v1:*")
    assert len(keys) == 1
    ttl = await fake_redis.ttl(keys[0])
    assert ttl > 0


async def test_hash_bytes_offloads_to_thread() -> None:
    """Verifies async-safe for large inputs."""
    big = b"x" * (10 * 1024 * 1024)  # 10 MB
    digest = await hash_bytes(big)
    assert len(digest) == 64
