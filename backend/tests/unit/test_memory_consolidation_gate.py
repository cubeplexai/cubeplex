import fakeredis.aioredis
import pytest

from cubeplex.services import memory_consolidation as mc


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


@pytest.mark.asyncio
async def test_note_run_increments_counter(redis):
    await mc.note_run(redis, "t", "conv1")
    await mc.note_run(redis, "t", "conv1")
    assert await mc._counter(redis, "t", "conv1") == 2


@pytest.mark.asyncio
async def test_should_consolidate_requires_both_gates(redis):
    import time as _t

    await mc.note_run(redis, "t", "conv1")
    assert await mc.should_consolidate(redis, "t", "conv1", min_hours=0, min_runs=5) is False
    for _ in range(4):
        await mc.note_run(redis, "t", "conv1")  # 5 total
    assert await mc.should_consolidate(redis, "t", "conv1", min_hours=0, min_runs=5) is True
    # Just consolidated (last~now) → too soon even with enough runs → False.
    await mc.mark_consolidated(redis, "t", "conv1", cutoff=_t.time(), consumed=0)
    for _ in range(5):
        await mc.note_run(redis, "t", "conv1")
    assert await mc.should_consolidate(redis, "t", "conv1", min_hours=999, min_runs=1) is False


@pytest.mark.asyncio
async def test_lock_excludes_concurrent_holder(redis):
    tok = await mc.acquire_lock(redis, "t", "conv1", ttl_s=30)
    assert tok is not None
    assert await mc.acquire_lock(redis, "t", "conv1", ttl_s=30) is None
    await mc.release_lock(redis, "t", "conv1", tok)
    assert await mc.acquire_lock(redis, "t", "conv1", ttl_s=30) is not None


@pytest.mark.asyncio
async def test_high_water_mark_keeps_runs_arriving_during_pass(redis):
    for _ in range(5):
        await mc.note_run(redis, "t", "conv1")
    n = await mc._counter(redis, "t", "conv1")  # capture N=5
    await mc.note_run(redis, "t", "conv1")  # a run arrives mid-pass → 6
    await mc.mark_consolidated(redis, "t", "conv1", cutoff=123.0, consumed=n)
    assert await mc._counter(redis, "t", "conv1") == 1
    assert await mc.get_last(redis, "t", "conv1") == 123.0
