"""E2E: poller fires runs, applies missed-run policy, never double-fires."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.db.engine import async_session_maker
from cubeplex.llm.snapshot import LLMSnapshot, load_llm_snapshot
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubeplex.schedules.poller import ScheduledTaskPoller
from cubeplex.streams.run_manager import RunContext
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID

pytestmark = pytest.mark.e2e

BASE = f"/api/v1/ws/{DEFAULT_WS_ID}/scheduled-tasks"


def _poller(
    client: httpx.AsyncClient,
    *,
    run_manager: object | None = None,
    **kwargs: object,
) -> ScheduledTaskPoller:
    app = client._transport.app  # type: ignore[attr-defined]

    async def load_snapshot(session: AsyncSession, org_id: str) -> LLMSnapshot:
        return await load_llm_snapshot(session, org_id, app.state.encryption_backend)

    return ScheduledTaskPoller(
        run_manager=run_manager or app.state.run_manager,
        load_execution_snapshot=load_snapshot,
        **kwargs,
    )


class _RecordingRunManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def start_run(
        self,
        *,
        conversation_id: str,
        content: str,
        attachments: list[str] | None,
        ctx: RunContext,
        run_id: str | None = None,
        model_key: str | None = None,
        reasoning: object | None = None,
        cancel_pending_hitl: bool = False,
        llm_snapshot: object | None = None,
        admission_id: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "content": content,
                "actor_user_id": ctx.user_id,
                "run_id": run_id,
                "model_key": model_key,
                "reasoning": reasoning,
                "admission_id": admission_id,
            }
        )
        assert run_id is not None
        return run_id


class _BusyRunManager(_RecordingRunManager):
    async def start_run(self, **kwargs: object) -> str:
        await super().start_run(**kwargs)  # type: ignore[arg-type]
        raise RuntimeError("Conversation already has an active run")


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
    poller = _poller(async_client, misfire_grace_seconds=300)
    await poller.poll_once()
    rows = await _runs_for(tid)
    assert len(rows) == 1
    assert rows[0].state in {"started", "succeeded", "failed"}
    assert rows[0].run_id is not None
    async with async_session_maker() as s:
        task = await s.get(ScheduledTask, tid)
        assert task is not None and task.next_fire_at is None


@pytest.mark.asyncio
async def test_claim_freezes_schedule_content_and_run_binding(
    async_client: httpx.AsyncClient,
) -> None:
    run_at = datetime.now(UTC) + timedelta(days=1)
    response = await async_client.post(
        BASE,
        json={
            "name": "original title",
            "prompt": "original prompt",
            "schedule_kind": "once",
            "run_at": run_at.isoformat(),
            "target_mode": "new_each_run",
        },
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["id"]
    run_manager = _RecordingRunManager()
    poller = _poller(async_client, run_manager=run_manager)

    async with async_session_maker() as session:
        task = await session.get(ScheduledTask, task_id)
        assert task is not None
        task.next_fire_at = datetime.now(UTC) - timedelta(seconds=1)
        row_id = await poller._claim_occurrence(session, task=task, now=datetime.now(UTC))
        assert row_id is not None
        await session.commit()

        task.prompt = "edited prompt"
        task.name = "edited title"
        await session.commit()

    await poller._dispatch_one(row_id)

    assert len(run_manager.calls) == 1
    call = run_manager.calls[0]
    assert call["content"] == "original prompt"
    assert call["run_id"] is not None
    assert call["admission_id"] is not None
    async with async_session_maker() as session:
        row = await session.get(ScheduledTaskRun, row_id)
        admission = await session.get(ConversationExecutionAdmission, call["admission_id"])
        conversation = await session.get(Conversation, call["conversation_id"])
        assert row is not None
        assert row.run_id == call["run_id"]
        assert row.execution_snapshot is not None
        assert row.execution_snapshot["content"] == "original prompt"
        assert admission is not None
        assert admission.source_kind == "schedule_occurrence"
        assert admission.source_id == row_id
        assert admission.actor_user_id == call["actor_user_id"]
        assert conversation is not None
        assert conversation.title == "original title"


@pytest.mark.asyncio
async def test_busy_retry_keeps_binding_and_delete_cancels_unstarted_run(
    async_client: httpx.AsyncClient,
) -> None:
    conversation_response = await async_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations",
        params={"title": "busy schedule target"},
    )
    assert conversation_response.status_code == 201, conversation_response.text
    conversation_id = conversation_response.json()["id"]
    response = await async_client.post(
        BASE,
        json={
            "name": "busy occurrence",
            "prompt": "original prompt",
            "schedule_kind": "once",
            "run_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "target_mode": "fixed",
            "target_conversation_id": conversation_id,
        },
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["id"]
    run_manager = _BusyRunManager()
    poller = _poller(
        async_client,
        run_manager=run_manager,
        busy_retry_delay_seconds=1,
    )

    async with async_session_maker() as session:
        task = await session.get(ScheduledTask, task_id)
        assert task is not None
        task.next_fire_at = datetime.now(UTC) - timedelta(seconds=1)
        row_id = await poller._claim_occurrence(session, task=task, now=datetime.now(UTC))
        assert row_id is not None
        await session.commit()

    await poller._dispatch_one(row_id)
    async with async_session_maker() as session:
        row = await session.get(ScheduledTaskRun, row_id)
        assert row is not None
        assert row.state == "claimed"
        assert row.run_id is not None
        stable_run_id = row.run_id
        row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    await poller.poll_once()
    assert [call["run_id"] for call in run_manager.calls] == [stable_run_id, stable_run_id]

    delete_response = await async_client.delete(f"{BASE}/{task_id}")
    assert delete_response.status_code == 204, delete_response.text
    async with async_session_maker() as session:
        row = await session.get(ScheduledTaskRun, row_id)
        admission = await session.scalar(
            select(ConversationExecutionAdmission).where(
                ConversationExecutionAdmission.source_kind == "schedule_occurrence",  # type: ignore[arg-type]
                ConversationExecutionAdmission.source_id == row_id,  # type: ignore[arg-type]
            )
        )
        assert row is not None
        assert row.state == "cancelled"
        assert admission is not None
        assert admission.run_id == stable_run_id
        assert admission.revoked_at is not None
        assert admission.run_finished_at is not None
        assert admission.run_terminal_status == "cancelled"


@pytest.mark.asyncio
async def test_claimed_fixed_occurrence_cannot_cross_stop_all(
    async_client: httpx.AsyncClient,
) -> None:
    conversation_response = await async_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations",
        params={"title": "generation-fenced schedule"},
    )
    assert conversation_response.status_code == 201, conversation_response.text
    conversation_id = conversation_response.json()["id"]
    response = await async_client.post(
        BASE,
        json={
            "name": "old occurrence",
            "prompt": "must not run after stop all",
            "schedule_kind": "once",
            "run_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "target_mode": "fixed",
            "target_conversation_id": conversation_id,
        },
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["id"]
    run_manager = _RecordingRunManager()
    poller = _poller(async_client, run_manager=run_manager)

    async with async_session_maker() as session:
        task = await session.get(ScheduledTask, task_id)
        conversation = await session.get(Conversation, conversation_id)
        assert task is not None
        assert conversation is not None
        generation = conversation.execution_generation
        task.next_fire_at = datetime.now(UTC) - timedelta(seconds=1)
        row_id = await poller._claim_occurrence(session, task=task, now=datetime.now(UTC))
        assert row_id is not None
        await session.commit()

    stop_response = await async_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}/stop-all",
        json={"execution_generation": generation},
    )
    assert stop_response.status_code == 202, stop_response.text

    await poller._dispatch_one(row_id)

    assert run_manager.calls == []
    async with async_session_maker() as session:
        row = await session.get(ScheduledTaskRun, row_id)
        assert row is not None
        assert row.state == "cancelled"
        assert row.detail is not None
        assert "predates the current conversation generation" in row.detail


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
    poller = _poller(async_client, misfire_grace_seconds=1)
    await poller.poll_once()
    rows = await _runs_for(tid)
    assert any(r.state == "skipped_missed" for r in rows)
    async with async_session_maker() as s:
        task = await s.get(ScheduledTask, tid)
        assert task is not None and task.next_fire_at is not None
        # next_fire_at must be in the recent past or near future, not 3h stale.
        assert task.next_fire_at > datetime.now(UTC) - timedelta(hours=1)


@pytest.mark.asyncio
async def test_stale_started_run_is_failed(async_client: httpx.AsyncClient) -> None:
    """A row stuck in 'started' past started_timeout must be marked failed."""
    tid = await _create_due_once(async_client)
    poller = _poller(async_client, started_timeout_seconds=1)
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
        assert task.next_fire_at > datetime.now(UTC) - timedelta(hours=1)


@pytest.mark.asyncio
async def test_concurrent_pollers_fire_once(async_client: httpx.AsyncClient) -> None:
    tid = await _create_due_once(async_client)
    p1 = _poller(async_client)
    p2 = _poller(async_client)
    await asyncio.gather(p1.poll_once(), p2.poll_once())
    rows = await _runs_for(tid)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_busy_postponed_row_skipped_by_stale_sweep(
    async_client: httpx.AsyncClient,
) -> None:
    """Regression for codex P1: a row postponed by busy-target retry has
    ``next_retry_at`` in the future but ``claimed_at`` may be older than
    ``claim_timeout``. The stale-claim sweep must NOT pick it up — that
    would collapse the 5-min busy retry cadence into the 2-min stale-claim
    cadence and exhaust the retry budget early.
    """
    from datetime import timedelta as _td

    from cubeplex.repositories.scheduled_task import claim_stale_runs

    tid = await _create_due_once(async_client)
    # Seed a busy-postponed row with its durable run identity. claimed_at is
    # past the stale timeout, while next_retry_at is still in the future.
    async with async_session_maker() as s:
        now = datetime.now(UTC)
        row = ScheduledTaskRun(
            scheduled_task_id=tid,
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            scheduled_for=now - _td(minutes=10),
            claimed_at=now - _td(minutes=5),
            state="claimed",
            run_id="0192abcd-busy-postponed",
            retry_count=1,
            next_retry_at=now + _td(minutes=4),
        )
        s.add(row)
        await s.commit()
        row_id = row.id
    # Run the stale sweep with the production default claim_timeout.
    async with async_session_maker() as s:
        stale = await claim_stale_runs(
            s, now=datetime.now(UTC), claim_timeout=_td(minutes=2), limit=50
        )
    assert all(r.id != row_id for r in stale), "busy-postponed row leaked into stale-claim sweep"


@pytest.mark.asyncio
async def test_stale_pre_stamped_row_is_reclaimed(
    async_client: httpx.AsyncClient,
) -> None:
    """A durable run binding remains recoverable after a dispatch crash.

    Recovery keeps this identity and uses the admission to decide whether
    the exact run can start; it never replaces it with a new run.
    """
    from datetime import timedelta as _td

    from cubeplex.repositories.scheduled_task import claim_stale_runs

    tid = await _create_due_once(async_client)
    async with async_session_maker() as s:
        now = datetime.now(UTC)
        row = ScheduledTaskRun(
            scheduled_task_id=tid,
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            scheduled_for=now - _td(minutes=10),
            claimed_at=now - _td(minutes=10),
            state="claimed",
            run_id="0192abcd-orphan-pre-stamp",  # pre-stamped but never dispatched
        )
        s.add(row)
        await s.commit()
        row_id = row.id
    async with async_session_maker() as s:
        stale = await claim_stale_runs(
            s, now=datetime.now(UTC), claim_timeout=_td(minutes=2), limit=50
        )
    assert any(r.id == row_id for r in stale), "pre-stamped stale row was not reclaimed"


@pytest.mark.asyncio
async def test_completion_hook_recovers_claimed_row_race(
    async_client: httpx.AsyncClient,
) -> None:
    """Regression for codex P2: a very short run can call the completion
    hook before the poller's post-dispatch UPDATE flips 'claimed' →
    'started'. The hook must still find the row by ``run_id`` (pre-stamped
    on the row while state was 'claimed') and mark it terminal.
    """
    from cubeplex.schedules.completion_hook import (
        record_scheduled_run_terminal_state,
    )

    tid = await _create_due_once(async_client)
    # Simulate the race window: row is 'claimed' with run_id pre-stamped
    # (post-dispatch UPDATE hasn't committed yet) and the run finishes.
    async with async_session_maker() as s:
        now = datetime.now(UTC)
        row = ScheduledTaskRun(
            scheduled_task_id=tid,
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            scheduled_for=now,
            claimed_at=now,
            state="claimed",
            run_id="0192abcd-race-window-pre-stamp",
        )
        s.add(row)
        await s.commit()
        row_id = row.id
    await record_scheduled_run_terminal_state(
        run_id="0192abcd-race-window-pre-stamp", run_status="completed"
    )
    async with async_session_maker() as s:
        refreshed = await s.get(ScheduledTaskRun, row_id)
        assert refreshed is not None
        assert refreshed.state == "succeeded"


@pytest.mark.asyncio
async def test_completion_hook_records_cancelled_queued_handoff(
    async_client: httpx.AsyncClient,
) -> None:
    from cubeplex.schedules.completion_hook import record_scheduled_run_terminal_state

    tid = await _create_due_once(async_client)
    async with async_session_maker() as session:
        now = datetime.now(UTC)
        row = ScheduledTaskRun(
            scheduled_task_id=tid,
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            scheduled_for=now,
            claimed_at=now,
            state="queued",
            run_id="0192abcd-queued-cancelled",
            detail="im_channel_queued",
        )
        session.add(row)
        await session.commit()
        row_id = row.id

    await record_scheduled_run_terminal_state(
        run_id="0192abcd-queued-cancelled",
        run_status="cancelled",
    )

    async with async_session_maker() as session:
        refreshed = await session.get(ScheduledTaskRun, row_id)
        assert refreshed is not None
        assert refreshed.state == "cancelled"
        assert refreshed.detail == "run cancelled"


@pytest.mark.asyncio
async def test_post_dispatch_backfills_conversation_id_when_hook_wins(
    async_client: httpx.AsyncClient,
) -> None:
    """Regression for codex round-3 P2: when the completion hook terminates
    the row before _dispatch_one's post-dispatch UPDATE lands, the UPDATE
    must still backfill conversation_id and started_at so the UI's "View
    conversation" link works. State must NOT be reverted from terminal.
    """
    from datetime import timedelta as _td

    from sqlalchemy import case, literal, update

    tid = await _create_due_once(async_client)
    # Set up the post-race state: hook already flipped 'claimed' → 'succeeded'
    # while conversation_id is still NULL. Then run the poller's UPDATE
    # logic directly (same SQL shape as _dispatch_one's tail) to verify
    # the backfill works and the terminal state is preserved.
    async with async_session_maker() as s:
        now = datetime.now(UTC)
        row = ScheduledTaskRun(
            scheduled_task_id=tid,
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            scheduled_for=now,
            claimed_at=now,
            state="succeeded",  # hook already terminated
            run_id="0192abcd-hook-won-race",
            conversation_id=None,
            started_at=None,
        )
        s.add(row)
        await s.commit()
        row_id = row.id
        backfill_started_at = now + _td(milliseconds=5)
        await s.execute(
            update(ScheduledTaskRun)
            .where(ScheduledTaskRun.id == row_id)
            .values(
                conversation_id="conv-backfilled",
                started_at=backfill_started_at,
                state=case(
                    (ScheduledTaskRun.state == "claimed", literal("started")),
                    else_=ScheduledTaskRun.state,
                ),
            )
        )
        await s.commit()
    async with async_session_maker() as s:
        refreshed = await s.get(ScheduledTaskRun, row_id)
        assert refreshed is not None
        # State preserved as the terminal value the hook set.
        assert refreshed.state == "succeeded"
        # conversation_id and started_at now backfilled.
        assert refreshed.conversation_id == "conv-backfilled"
        assert refreshed.started_at is not None


@pytest.mark.asyncio
async def test_busy_postponed_query_honors_above_default_retry_cap(
    async_client: httpx.AsyncClient,
) -> None:
    """Regression for codex round-3 P2: claim_busy_postponed_runs must not
    hard-code retry_count < 3. When max_busy_retries is configured higher,
    a row at retry_count > 3 must still be picked up; the cap is enforced
    in the poller's _dispatch_one (rows past the cap are flipped to terminal
    skipped_busy_max_retries, which the state='claimed' filter excludes).
    """
    from datetime import timedelta as _td

    from cubeplex.repositories.scheduled_task import claim_busy_postponed_runs

    tid = await _create_due_once(async_client)
    async with async_session_maker() as s:
        now = datetime.now(UTC)
        row = ScheduledTaskRun(
            scheduled_task_id=tid,
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            scheduled_for=now - _td(minutes=20),
            claimed_at=now - _td(minutes=15),
            state="claimed",
            run_id="0192abcd-configured-busy-retry",
            retry_count=4,  # above the historical literal cap
            next_retry_at=now - _td(seconds=1),
        )
        s.add(row)
        await s.commit()
        row_id = row.id
    async with async_session_maker() as s:
        rows = await claim_busy_postponed_runs(s, now=datetime.now(UTC), limit=50)
    assert any(r.id == row_id for r in rows), (
        "busy-postponed row with retry_count > 3 not picked up"
    )
