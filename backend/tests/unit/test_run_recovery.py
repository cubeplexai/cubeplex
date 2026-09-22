import asyncio
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest

from cubeplex.streams import recovery
from cubeplex.streams.run_events import create_run
from cubeplex.streams.run_manager import RunManager


async def test_stop_recovery_start_is_single_flight_and_shutdown_joins_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = 0
    ready, exited = asyncio.Event(), asyncio.Event()

    async def scan_until_shutdown(_self: recovery.StoppedRunRecovery) -> None:
        nonlocal started
        started += 1
        ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    monkeypatch.setattr(recovery.StoppedRunRecovery, "run", scan_until_shutdown)
    manager = RunManager(
        app=MagicMock(), redis=MagicMock(), key_prefix="stop", run_event_ttl_seconds=60
    )
    try:
        manager.start_stop_recovery()
        manager.start_stop_recovery()
        await asyncio.wait_for(ready.wait(), timeout=1)
        assert started == 1
    finally:
        await manager.stop_control_listeners()
    assert exited.is_set()


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
