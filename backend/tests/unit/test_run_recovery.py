from unittest.mock import AsyncMock

import fakeredis.aioredis

from cubeplex.streams import recovery
from cubeplex.streams.run_events import create_run


async def test_recovery_skips_cleanup_when_stale_cas_loses(monkeypatch) -> None:  # noqa: ANN001
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    created = await create_run(
        redis,
        prefix="recovery-cas",
        run_id="run-1",
        conversation_id="conversation-1",
        status="running",
        started_at="2026-09-15T00:00:00+00:00",
        ttl_seconds=60,
    )
    assert created is not None

    mark_stale = AsyncMock(return_value=False)
    stamp_runs = AsyncMock()
    fail_schedules = AsyncMock()
    repair_threads = AsyncMock()
    monkeypatch.setattr(recovery, "mark_run_stale", mark_stale)
    monkeypatch.setattr(recovery, "_stamp_cubeloop_runs", stamp_runs)
    monkeypatch.setattr(recovery, "_fail_stranded_scheduled_runs", fail_schedules)
    monkeypatch.setattr(recovery, "_repair_stranded_threads", repair_threads)

    assert await recovery.recover_stranded_runs(redis, prefix="recovery-cas") == 0
    mark_stale.assert_awaited_once()
    stamp_runs.assert_not_awaited()
    fail_schedules.assert_not_awaited()
    repair_threads.assert_not_awaited()
