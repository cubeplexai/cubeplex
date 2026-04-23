"""Minimal async Redis stub for streaming tests."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any


def _parse_stream_id(raw_id: str) -> tuple[int, int]:
    left, right = raw_id.split("-", 1)
    return int(left), int(right)


def _stream_id_gt(left: str, right: str) -> bool:
    return _parse_stream_id(left) > _parse_stream_id(right)


def _stream_id_gte(left: str, right: str) -> bool:
    return _parse_stream_id(left) >= _parse_stream_id(right)


class FakeRedisPipeline:
    """Very small subset of the redis pipeline API."""

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def set(self, *args: Any, **kwargs: Any) -> FakeRedisPipeline:
        self._ops.append(("set", args, kwargs))
        return self

    def delete(self, *args: Any, **kwargs: Any) -> FakeRedisPipeline:
        self._ops.append(("delete", args, kwargs))
        return self

    def expire(self, *args: Any, **kwargs: Any) -> FakeRedisPipeline:
        self._ops.append(("expire", args, kwargs))
        return self

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for op_name, args, kwargs in self._ops:
            method = getattr(self._redis, op_name)
            results.append(await method(*args, **kwargs))
        return results


class FakeRedis:
    """Enough of redis.asyncio.Redis for the streaming runtime tests."""

    def __init__(self) -> None:
        self._strings: dict[str, str] = {}
        self._streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self._conditions: dict[str, asyncio.Condition] = {}
        self._next_id = 1

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    def pipeline(self) -> FakeRedisPipeline:
        return FakeRedisPipeline(self)

    async def get(self, key: str) -> str | None:
        return self._strings.get(key)

    async def set(self, key: str, value: str) -> bool:
        self._strings[key] = value
        return True

    async def delete(self, key: str) -> int:
        deleted = 0
        if key in self._strings:
            del self._strings[key]
            deleted += 1
        if key in self._streams:
            del self._streams[key]
            deleted += 1
        return deleted

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        # Tests do not rely on TTL elapsing, only that the call succeeds.
        _ = (key, ttl_seconds)
        return True

    async def xadd(self, key: str, fields: dict[str, str]) -> str:
        event_id = f"{self._next_id}-0"
        self._next_id += 1
        entries = self._streams.setdefault(key, [])
        entries.append((event_id, dict(fields)))
        condition = self._conditions.setdefault(key, asyncio.Condition())
        async with condition:
            condition.notify_all()
        return event_id

    async def xrange(
        self,
        key: str,
        min: str = "-",
        max: str = "+",
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        entries = self._streams.get(key, [])
        exclusive_min = min.startswith("(")
        if exclusive_min:
            min = min[1:]
        matched: list[tuple[str, dict[str, str]]] = []
        for event_id, payload in entries:
            if min != "-":
                if exclusive_min and not _stream_id_gt(event_id, min):
                    continue
                if not exclusive_min and not _stream_id_gte(event_id, min):
                    continue
            if max != "+" and not _stream_id_gte(max, event_id):
                continue
            matched.append((event_id, payload))
            if count is not None and len(matched) >= count:
                break
        return matched

    async def xrevrange(
        self,
        key: str,
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        entries = list(reversed(self._streams.get(key, [])))
        if count is not None:
            entries = entries[:count]
        return entries

    async def xread(
        self,
        streams: dict[str, str],
        block: int | None = None,
        count: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        key, last_id = next(iter(streams.items()))
        baseline_last_id = last_id
        if last_id == "$":
            latest = self._streams.get(key, [])
            baseline_last_id = latest[-1][0] if latest else "0-0"

        async def read_available() -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
            entries = await self.xrange(key, min=f"({baseline_last_id}")
            if count is not None:
                entries = entries[:count]
            return [(key, entries)] if entries else []

        available = await read_available()
        if available:
            return available

        if block is None:
            return []

        deadline = monotonic() + (block / 1000)
        condition = self._conditions.setdefault(key, asyncio.Condition())
        while True:
            timeout = deadline - monotonic()
            if timeout <= 0:
                return []
            async with condition:
                try:
                    await asyncio.wait_for(condition.wait(), timeout=timeout)
                except TimeoutError:
                    return []
            available = await read_available()
            if available:
                return available
