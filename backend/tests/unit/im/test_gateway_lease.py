from __future__ import annotations

import pytest

from cubebox.im.runtime import LEASE_TTL, release_lease, try_acquire_lease


class FakeRedis:
    """Minimal in-memory Redis stub for lease function tests."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int = 30) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def delete(self, key: str) -> int:
        if key in self._store:
            del self._store[key]
            return 1
        return 0

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self._store


@pytest.mark.asyncio
async def test_acquire_lease_success() -> None:
    redis = FakeRedis()
    acquired = await try_acquire_lease(redis, account_id="a1", instance_id="inst1", prefix="test")
    assert acquired is True
    assert await redis.get("test:im:gateway:a1:owner") == "inst1"


@pytest.mark.asyncio
async def test_acquire_lease_already_owned() -> None:
    redis = FakeRedis()
    await try_acquire_lease(redis, account_id="a1", instance_id="inst1", prefix="test")
    acquired = await try_acquire_lease(redis, account_id="a1", instance_id="inst2", prefix="test")
    assert acquired is False


@pytest.mark.asyncio
async def test_reacquire_lease_same_instance() -> None:
    """Same instance acquiring twice returns True (idempotent)."""
    redis = FakeRedis()
    await try_acquire_lease(redis, account_id="a1", instance_id="inst1", prefix="test")
    acquired = await try_acquire_lease(redis, account_id="a1", instance_id="inst1", prefix="test")
    assert acquired is True


@pytest.mark.asyncio
async def test_release_lease() -> None:
    redis = FakeRedis()
    await try_acquire_lease(redis, account_id="a1", instance_id="inst1", prefix="test")
    await release_lease(redis, account_id="a1", instance_id="inst1", prefix="test")
    assert await redis.get("test:im:gateway:a1:owner") is None


@pytest.mark.asyncio
async def test_release_lease_wrong_owner() -> None:
    """Release is a no-op if another instance owns the key."""
    redis = FakeRedis()
    await try_acquire_lease(redis, account_id="a1", instance_id="inst1", prefix="test")
    await release_lease(redis, account_id="a1", instance_id="inst2", prefix="test")
    # Key should still be owned by inst1
    assert await redis.get("test:im:gateway:a1:owner") == "inst1"


@pytest.mark.asyncio
async def test_lease_ttl_constant() -> None:
    assert LEASE_TTL == 30
