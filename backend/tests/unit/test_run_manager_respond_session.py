from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from cubeloop.session.types import DeliveryError, ExecutionResult

from cubeplex.streams.execution_adapter import EventProjectionError
from cubeplex.streams.run_manager import ResumeConflict, RunContext, RunManager
from cubeplex.streams.steering_delivery import SteeringRunScope


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "claim_matches",
        "finalize_matches",
        "checkpoint_error",
        "projection_failure",
        "expected_conflict",
    ),
    [
        (True, True, False, False, None),
        (False, True, False, False, "resume claim was replaced before cleanup"),
        (True, False, False, False, "resume claim was replaced before finalization"),
        (True, True, True, False, None),
        (True, True, False, True, None),
    ],
)
async def test_respond_projects_and_clears_answered_pending_before_finalizing(
    monkeypatch: pytest.MonkeyPatch,
    claim_matches: bool,
    finalize_matches: bool,
    checkpoint_error: bool,
    projection_failure: bool,
    expected_conflict: str | None,
) -> None:
    order: list[str] = []
    pending = SimpleNamespace(question_id="q-answered")
    checkpointer = MagicMock()
    checkpointer.load_pending = AsyncMock(return_value=(pending, "run-1"))

    async def _clear_pending(
        _conversation_id: str,
        *,
        question_id: str,
        run_id: str,
    ) -> bool:
        assert question_id == "q-answered"
        assert run_id == "run-1"
        order.append("clear-pending")
        return True

    checkpointer.clear_pending_request_if_matches = _clear_pending

    @asynccontextmanager
    async def _checkpointer_context() -> Any:
        yield checkpointer

    monkeypatch.setattr(
        "cubeplex.agents.checkpointer.shared_checkpointer",
        _checkpointer_context,
    )

    session = SimpleNamespace(
        load_checkpoint=AsyncMock(
            side_effect=RuntimeError("checkpoint restore failed") if checkpoint_error else None,
            return_value=None,
        ),
        state_context=object(),
    )
    agent = SimpleNamespace(session=session)
    manager = RunManager.__new__(RunManager)
    manager._app = SimpleNamespace(state=SimpleNamespace(tracer=None))
    manager._redis = MagicMock()
    manager._key_prefix = "test-respond"
    manager._run_event_ttl_seconds = 60
    manager._agents = {}
    manager._tasks = {}
    manager._cleanup_tasks = set()
    manager._hitl_channels = {}
    manager._steering_delivery = SimpleNamespace(
        register_and_drain=AsyncMock(),
        unregister=AsyncMock(),
        acknowledge_injected=AsyncMock(),
    )
    manager._build_agent_for_conversation = AsyncMock(  # type: ignore[method-assign]
        return_value=(agent, [], None)
    )

    class _AutoDetach:
        async def quiesce_then_schedule(self, _event: Any) -> None:
            return None

    monkeypatch.setattr(
        "cubeplex.streams.run_manager._build_auto_detach_listener",
        lambda *_args, **_kwargs: _AutoDetach(),
    )

    class _Heartbeat:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def stop(self) -> None:
            order.append("stop-heartbeat")

    monkeypatch.setattr("cubeplex.streams.run_manager._InFlightToolHeartbeat", _Heartbeat)

    @contextmanager
    def _tracing_context(**_kwargs: Any) -> Any:
        yield

    @asynccontextmanager
    async def _trace(*_args: Any, **_kwargs: Any) -> Any:
        yield

    monkeypatch.setattr("cubeloop.tracing.tracing_context", _tracing_context)
    monkeypatch.setattr("cubeloop.tracing.trace", _trace)
    monkeypatch.setattr(
        "cubeplex.llm.runtime_writeback.schedule_runtime_status_writeback",
        MagicMock(),
    )

    async def _execute_session(**kwargs: Any) -> ExecutionResult:
        await kwargs["on_checkpoint_input"]("steer-1")
        return ExecutionResult(
            run_id="run-1",
            attempt_id="attempt-1",
            outcome="suspended" if projection_failure else "completed",
            pending_request=pending if projection_failure else None,
            checkpoint_committed=True,
            delivery_errors=(
                DeliveryError(
                    consumer="cubeplex-runtime",
                    seq=7,
                    reason="timeout",
                    message="final notification timed out",
                ),
            )
            if projection_failure
            else (),
        )

    monkeypatch.setattr("cubeplex.streams.execution_adapter.execute_session", _execute_session)

    async def _begin_finalization(*_args: Any, **_kwargs: Any) -> bool:
        order.append("reserve-finalization")
        return claim_matches

    async def _finalize(*_args: Any, **_kwargs: Any) -> bool:
        order.append("finalize")
        return finalize_matches

    monkeypatch.setattr(
        "cubeplex.streams.hitl_resume.begin_resume_finalization",
        _begin_finalization,
    )
    monkeypatch.setattr(
        "cubeplex.streams.hitl_resume.finalize_run_meta_if_claim_matches",
        _finalize,
    )

    async def _emit_resolved(*_args: Any, **_kwargs: Any) -> None:
        order.append("resolved")

    monkeypatch.setattr("cubeplex.streams.run_manager._emit_synthetic_resolved", _emit_resolved)

    async def _flush(_source: str | None, _target: str | None) -> None:
        order.append("flush")

    async def _drain() -> None:
        order.append("drain")

    async def _run() -> str:
        return await manager._run_cubeloop_respond_path(
            ctx=RunContext(
                user_id="user-1",
                org_id="org-1",
                workspace_id="workspace-1",
                conversation_id="conversation-1",
            ),
            run_id="run-1",
            conversation_id="conversation-1",
            question_id="q-answered",
            answer="yes",
            claim_token="claim-1",
            effective_system_prompt="system",
            publish_stream_event=AsyncMock(),
            flush_citation_buffer=_flush,
            citation_buffers={"agent-1": "citation"},
            extra_ref_holder={"provider_name": "provider", "model_id": "model"},
            before_terminal_commit=_drain,
        )

    if checkpoint_error:
        with pytest.raises(RuntimeError, match="checkpoint restore failed"):
            await _run()
        assert order == ["finalize"]
    elif projection_failure:
        with pytest.raises(EventProjectionError, match="cubeplex-runtime.*timeout"):
            await _run()
        assert order.index("clear-pending") < order.index("finalize")
    elif expected_conflict is None:
        assert await _run() == "completed"
        assert order.index("reserve-finalization") < order.index("clear-pending")
        assert order.index("clear-pending") < order.index("resolved")
        assert order.index("resolved") < order.index("flush")
        assert order.index("flush") < order.index("drain")
        assert order.index("drain") < order.index("finalize")
    else:
        with pytest.raises(ResumeConflict, match=expected_conflict):
            await _run()
    if checkpoint_error:
        manager._steering_delivery.acknowledge_injected.assert_not_awaited()
    else:
        manager._steering_delivery.acknowledge_injected.assert_awaited_once_with(
            "run-1",
            "steer-1",
            scope=SteeringRunScope(
                org_id="org-1",
                workspace_id="workspace-1",
                conversation_id="conversation-1",
            ),
        )
    assert manager._agents == {}


async def test_execute_respond_run_chained_hitl_keeps_slot_and_avoids_post_pause_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chained HITL emits DoneEvent(paused=True) before releasing the finalization lease
    and keeps active_run occupied so a fast follow-up resume cannot be clobbered.
    """
    import json

    import fakeredis.aioredis

    from cubeplex.streams.hitl_resume import (
        ClaimResumeOutcome,
        begin_resume_finalization,
        claim_resume,
        finalize_run_meta_if_claim_matches,
    )
    from cubeplex.streams.run_events import create_run, get_active_run, get_run_meta

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    prefix = "test_respond_chained"
    cid, rid = "c-chained", "r-chained"
    await create_run(
        redis,
        prefix=prefix,
        run_id=rid,
        conversation_id=cid,
        status="running",
        started_at="2026-11-19T00:00:00+00:00",
        ttl_seconds=60,
    )
    await redis.hset(f"{prefix}:run_meta:v2:{rid}", "status", "paused_hitl")
    claim1 = await claim_resume(
        redis,
        prefix=prefix,
        conversation_id=cid,
        expected_run_id=rid,
        started_at="2026-11-19T00:00:00+00:00",
        ttl_seconds=60,
    )
    assert claim1.outcome == ClaimResumeOutcome.OK and claim1.claim_token is not None

    app = SimpleNamespace(state=SimpleNamespace())
    manager = RunManager(
        app=app,  # type: ignore[arg-type]
        redis=redis,
        key_prefix=prefix,
        run_event_ttl_seconds=60,
    )
    manager._steering_delivery = SimpleNamespace(  # type: ignore[assignment]
        finalize_run=AsyncMock(),
        unregister=AsyncMock(),
    )

    events_order: list[str] = []

    async def _fake_respond_path(**kwargs: Any) -> str:
        assert await begin_resume_finalization(
            redis,
            prefix=prefix,
            conversation_id=cid,
            run_id=rid,
            claim_token=claim1.claim_token,
            ttl_seconds=60,
            lease_seconds=60,
        )
        emit_done = kwargs["emit_done_before_terminal_commit"]
        assert emit_done is not None
        # While DoneEvent is emitted, meta is still running and finalization lease is held.
        meta_before = await get_run_meta(redis, prefix=prefix, run_id=rid)
        assert meta_before is not None and meta_before.status == "running"
        await emit_done("paused_hitl")
        events_order.append("done_emitted")
        assert await finalize_run_meta_if_claim_matches(
            redis,
            prefix=prefix,
            run_id=rid,
            claim_token=claim1.claim_token,
            status="paused_hitl",
        )
        events_order.append("paused_committed")
        return "paused_hitl"

    monkeypatch.setattr(manager, "_run_cubeloop_respond_path", _fake_respond_path)
    monkeypatch.setattr(manager, "_release_run_sandbox", AsyncMock())
    monkeypatch.setattr(manager, "_bump_topic_activity", AsyncMock())
    monkeypatch.setattr(manager, "_maybe_consolidate_memory", AsyncMock())
    monkeypatch.setattr(
        "cubeplex.api.routes.v1.conversations._update_conversation_timestamp",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "cubeplex.api.routes.v1.conversations._enqueue_search_index",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "cubeplex.schedules.completion_hook.record_scheduled_run_terminal_state",
        AsyncMock(),
    )

    await manager._execute_respond_run(
        run_id=rid,
        conversation_id=cid,
        question_id="q1",
        answer="yes",
        claim_token=claim1.claim_token,
        ctx=RunContext(user_id="u1", org_id="o1", workspace_id="w1", conversation_id=cid),
    )

    assert events_order == ["done_emitted", "paused_committed"]
    active = await get_active_run(redis, prefix=prefix, conversation_id=cid)
    assert active is not None and active.run_id == rid and active.status == "paused_hitl"
    raw_events = await redis.xrange(f"{prefix}:run_events:v2:{rid}")
    done_payloads = [
        json.loads(fields["payload"])
        for _eid, fields in raw_events
        if json.loads(fields["payload"]).get("type") == "done"
    ]
    assert len(done_payloads) == 1
    assert done_payloads[0]["data"]["paused"] is True
