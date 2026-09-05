from __future__ import annotations

import pytest

from cubeplex.im.runtime import (
    LEASE_TTL,
    clear_connection_suspension,
    publish_connection_heartbeat,
    read_connection_heartbeats,
    release_lease,
    remove_connection_heartbeat,
    renew_lease,
    suspend_connection,
    try_acquire_lease,
)


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

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self._store.get(key) for key in keys]

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self._store

    async def eval(self, script: str, numkeys: int, *values: object) -> int:
        keys = [str(value) for value in values[:numkeys]]
        args = [str(value) for value in values[numkeys:]]
        if "cubeplex-im-acquire" in script:
            owner_key, suspended_key = keys
            if suspended_key in self._store:
                return 0
            owner = self._store.get(owner_key)
            if owner is None or owner == args[0]:
                self._store[owner_key] = args[0]
                return 1
            return 0
        if "cubeplex-im-renew" in script:
            if self._store.get(keys[0]) == args[0]:
                return int(await self.expire(keys[0], int(args[1])))
            return 0
        if "cubeplex-im-release" in script:
            if self._store.get(keys[0]) == args[0]:
                return await self.delete(keys[0])
            return 0
        if "cubeplex-im-suspend" in script:
            owner_key, suspended_key, heartbeat_key = keys
            self._store[suspended_key] = "1"
            if self._store.get(owner_key) == args[0]:
                await self.delete(owner_key)
            if self._store.get(heartbeat_key) == args[0]:
                await self.delete(heartbeat_key)
            return 1
        raise AssertionError("unknown Lua script")


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


@pytest.mark.asyncio
async def test_renew_does_not_extend_a_lease_stolen_between_compare_and_expire() -> None:
    class LeaseStealRedis(FakeRedis):
        async def expire(self, key: str, seconds: int) -> bool:
            del seconds
            self._store[key] = "inst2"
            return True

        async def eval(self, script: str, numkeys: int, *values: object) -> int:
            if "cubeplex-im-renew" in script:
                self._store[str(values[0])] = "inst2"
                return 0
            return await super().eval(script, numkeys, *values)

    redis = LeaseStealRedis()
    await redis.set("test:im:gateway:a1:owner", "inst1")

    renewed = await renew_lease(
        redis,
        account_id="a1",
        instance_id="inst1",
        prefix="test",
    )

    assert renewed is False
    assert await redis.get("test:im:gateway:a1:owner") == "inst2"


@pytest.mark.asyncio
async def test_terminal_suspension_blocks_replacement_until_explicit_clear() -> None:
    redis = FakeRedis()
    assert await try_acquire_lease(redis, account_id="a1", instance_id="inst1", prefix="test")
    await suspend_connection(redis, account_id="a1", instance_id="inst1", prefix="test")

    assert not await try_acquire_lease(redis, account_id="a1", instance_id="inst2", prefix="test")
    await clear_connection_suspension(redis, account_id="a1", prefix="test")
    assert await try_acquire_lease(redis, account_id="a1", instance_id="inst2", prefix="test")


@pytest.mark.asyncio
async def test_shared_heartbeat_is_removed_only_by_its_owner() -> None:
    redis = FakeRedis()
    await publish_connection_heartbeat(
        redis,
        account_id="a1",
        instance_id="inst1",
        prefix="test",
    )

    assert await read_connection_heartbeats(redis, account_ids=["a1", "a2"], prefix="test") == {
        "a1"
    }
    await remove_connection_heartbeat(
        redis,
        account_id="a1",
        instance_id="inst2",
        prefix="test",
    )
    assert await read_connection_heartbeats(redis, account_ids=["a1"], prefix="test") == {"a1"}

    await remove_connection_heartbeat(
        redis,
        account_id="a1",
        instance_id="inst1",
        prefix="test",
    )
    assert await read_connection_heartbeats(redis, account_ids=["a1"], prefix="test") == set()
