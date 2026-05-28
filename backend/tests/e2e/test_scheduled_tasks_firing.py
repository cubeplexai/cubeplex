"""E2E: poller fires runs, applies missed-run policy, never double-fires."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from cubebox.db.engine import async_session_maker
from cubebox.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubebox.schedules.poller import ScheduledTaskPoller
from tests.e2e.conftest import DEFAULT_WS_ID

pytestmark = pytest.mark.e2e

BASE = f"/api/v1/ws/{DEFAULT_WS_ID}/scheduled-tasks"


def _run_manager(client: httpx.AsyncClient) -> object:
    app = client._transport.app  # type: ignore[attr-defined]
    return app.state.run_manager


async def _create_due_once(client: httpx.AsyncClient) -> str:
    """Create a 'once' task scheduled in the recent past so it is due now."""
    r = await client.post(
        BASE,
        json={
            "name": "fire-me",
            "prompt": "hi",
            "schedule_kind": "once",
            "run_at": (datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
            "target_mode": "new_each_run",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _runs_for(task_id: str) -> list[ScheduledTaskRun]:
    async with async_session_maker() as s:
        rows = (
            (
                await s.execute(
                    select(ScheduledTaskRun).where(
                        ScheduledTaskRun.scheduled_task_id == task_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


@pytest.mark.asyncio
async def test_once_task_fires_one_run(async_client: httpx.AsyncClient) -> None:
    tid = await _create_due_once(async_client)
    poller = ScheduledTaskPoller(run_manager=_run_manager(async_client), misfire_grace_seconds=300)
    await poller.poll_once()
    rows = await _runs_for(tid)
    assert len(rows) == 1
    assert rows[0].state in {"started", "succeeded", "failed"}
    assert rows[0].run_id is not None
    async with async_session_maker() as s:
        task = await s.get(ScheduledTask, tid)
        assert task is not None and task.next_fire_at is None


@pytest.mark.asyncio
async def test_missed_beyond_grace_skips_and_fast_forwards(
    async_client: httpx.AsyncClient,
) -> None:
    r = await async_client.post(
        BASE,
        json={
            "name": "hourly",
            "prompt": "hi",
            "schedule_kind": "interval",
            "interval_seconds": 3600,
            "target_mode": "new_each_run",
        },
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    async with async_session_maker() as s:
        task = await s.get(ScheduledTask, tid)
        assert task is not None
        task.next_fire_at = datetime.now(UTC) - timedelta(hours=3)
        await s.commit()
    poller = ScheduledTaskPoller(run_manager=_run_manager(async_client), misfire_grace_seconds=1)
    await poller.poll_once()
    rows = await _runs_for(tid)
    assert any(r.state == "skipped_missed" for r in rows)
    async with async_session_maker() as s:
        task = await s.get(ScheduledTask, tid)
        assert task is not None and task.next_fire_at is not None
        # next_fire_at must be in the recent past or near future, not 3h stale.
        assert task.next_fire_at > datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)


@pytest.mark.asyncio
async def test_stale_started_run_is_failed(async_client: httpx.AsyncClient) -> None:
    """A row stuck in 'started' past started_timeout must be marked failed."""
    tid = await _create_due_once(async_client)
    poller = ScheduledTaskPoller(run_manager=_run_manager(async_client), started_timeout_seconds=1)
    await poller.poll_once()  # creates + dispatches -> state 'started'
    async with async_session_maker() as s:
        rows = await _runs_for(tid)
        row = rows[0]
        db_row = await s.get(ScheduledTaskRun, row.id)
        assert db_row is not None
        db_row.state = "started"
        # Back-date well past the 1s timeout.
        db_row.started_at = datetime.now(UTC) - timedelta(minutes=5)
        await s.commit()
    await poller.poll_once()  # recovery sweep should fail it
    rows = await _runs_for(tid)
    assert rows[0].state == "failed"


@pytest.mark.asyncio
async def test_paused_stretch_records_skipped_missed_on_resume(
    async_client: httpx.AsyncClient,
) -> None:
    r = await async_client.post(
        BASE,
        json={
            "name": "hourly",
            "prompt": "hi",
            "schedule_kind": "interval",
            "interval_seconds": 3600,
            "target_mode": "new_each_run",
        },
    )
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    rp = await async_client.post(f"{BASE}/{tid}/pause")
    assert rp.status_code == 200, rp.text
    async with async_session_maker() as s:
        task = await s.get(ScheduledTask, tid)
        assert task is not None
        task.next_fire_at = datetime.now(UTC) - timedelta(hours=3)
        await s.commit()
    rr = await async_client.post(f"{BASE}/{tid}/resume")
    assert rr.status_code == 200, rr.text
    rows = await _runs_for(tid)
    assert sum(1 for r in rows if r.state == "skipped_missed") == 1
    async with async_session_maker() as s:
        task = await s.get(ScheduledTask, tid)
        assert task is not None and task.next_fire_at is not None
        assert task.next_fire_at > datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)


@pytest.mark.asyncio
async def test_concurrent_pollers_fire_once(async_client: httpx.AsyncClient) -> None:
    tid = await _create_due_once(async_client)
    rm = _run_manager(async_client)
    p1 = ScheduledTaskPoller(run_manager=rm)
    p2 = ScheduledTaskPoller(run_manager=rm)
    await asyncio.gather(p1.poll_once(), p2.poll_once())
    rows = await _runs_for(tid)
    assert len(rows) == 1
