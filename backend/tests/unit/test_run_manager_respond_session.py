from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from cubeloop.session.types import ExecutionResult

from cubeplex.streams.run_manager import ResumeConflict, RunContext, RunManager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim_matches", "finalize_matches", "expected_conflict"),
    [
        (True, True, None),
        (False, True, "resume claim was replaced before cleanup"),
        (True, False, "resume claim was replaced before finalization"),
    ],
)
async def test_respond_projects_and_clears_answered_pending_before_finalizing(
    monkeypatch: pytest.MonkeyPatch,
    claim_matches: bool,
    finalize_matches: bool,
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
        load_checkpoint=AsyncMock(return_value=None),
        state_context=object(),
    )
    agent = SimpleNamespace(session=session)
    manager = RunManager.__new__(RunManager)
    manager._app = SimpleNamespace(state=SimpleNamespace(tracer=None))
    manager._redis = MagicMock()
    manager._key_prefix = "test-respond"
    manager._run_event_ttl_seconds = 60
    manager._agents = {}
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
            outcome="completed",
            checkpoint_committed=True,
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

    if expected_conflict is None:
        assert await _run() == "completed"
        assert order.index("reserve-finalization") < order.index("clear-pending")
        assert order.index("clear-pending") < order.index("resolved")
        assert order.index("resolved") < order.index("flush")
        assert order.index("flush") < order.index("drain")
        assert order.index("drain") < order.index("finalize")
    else:
        with pytest.raises(ResumeConflict, match=expected_conflict):
            await _run()
    manager._steering_delivery.acknowledge_injected.assert_awaited_once_with("run-1", "steer-1")
    assert manager._agents == {}
