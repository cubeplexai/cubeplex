import json

import fakeredis.aioredis
import pytest

from cubeplex.streams.run_events import (
    _run_events_key,
    iter_run_events_chunked,
)

PREFIX = "test-chunked"


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


async def _seed(redis, run_id: str, n: int) -> list[str]:
    key = _run_events_key(PREFIX, run_id)
    ids = []
    for i in range(n):
        payload = json.dumps(
            {
                "type": "text_delta",
                "timestamp": "",
                "data": {"content": str(i)},
                "agent_id": None,
                "agent_name": None,
            }
        )
        ids.append(await redis.xadd(key, {"payload": payload}))
    return ids


@pytest.mark.asyncio
async def test_chunked_reads_all_events_in_order(redis):
    ids = await _seed(redis, "run-chunk-1", 25)

    batches = []
    async for batch in iter_run_events_chunked(
        redis, prefix=PREFIX, run_id="run-chunk-1", start=None, stop="+", count=10
    ):
        batches.append(batch)

    # 25 events / count 10 -> 3 batches (10, 10, 5)
    assert [len(b) for b in batches] == [10, 10, 5]
    flat = [e.event_id for b in batches for e in b]
    assert flat == ids
    assert [e.payload["data"]["content"] for b in batches for e in b] == [str(i) for i in range(25)]


@pytest.mark.asyncio
async def test_chunked_honors_exclusive_start(redis):
    ids = await _seed(redis, "run-chunk-2", 5)

    flat = []
    async for batch in iter_run_events_chunked(
        redis, prefix=PREFIX, run_id="run-chunk-2", start=f"({ids[1]}", stop="+", count=10
    ):
        flat.extend(e.event_id for e in batch)

    # Exclusive of ids[1] -> starts at ids[2].
    assert flat == ids[2:]


@pytest.mark.asyncio
async def test_chunked_empty_stream(redis):
    batches = [
        b
        async for b in iter_run_events_chunked(
            redis, prefix=PREFIX, run_id="run-none", start=None, stop="+", count=10
        )
    ]
    assert batches == []
