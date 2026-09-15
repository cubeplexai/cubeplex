from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from cubeloop.agent.types import AgentStartEvent
from cubeloop.checkpointer import MemoryCheckpointer
from cubeloop.providers.faux import FauxProvider, faux_assistant_message
from cubeloop.session.events import (
    ExecutionEventEnvelope,
    ExecutionFinished,
    InputCommitted,
)
from cubeloop.session.types import (
    DeliveryError,
    ExecutionError,
    ExecutionResult,
    PromptExecutionRequest,
)

from cubeplex.agents.graph import create_cubeplex_agent
from cubeplex.streams.execution_adapter import (
    CubeloopAgentRunError,
    EventProjectionError,
    execute_session,
    host_projection_error,
    require_host_success,
)


class _FakeSession:
    def __init__(self, events: list[ExecutionEventEnvelope], result: ExecutionResult) -> None:
        self._events = events
        self._result = result
        self.subscription: dict[str, Any] | None = None

    def subscribe(
        self,
        listener: Callable[[ExecutionEventEnvelope], Any],
        **options: Any,
    ) -> Callable[[], None]:
        self.subscription = {"listener": listener, **options}
        return lambda: None

    async def execute(self, request: PromptExecutionRequest) -> ExecutionResult:
        assert request.run_id == self._result.run_id
        assert self.subscription is not None
        listener = self.subscription["listener"]
        for event in self._events:
            await listener(event)
        return self._result


def _envelope(seq: int, event: Any) -> ExecutionEventEnvelope:
    return ExecutionEventEnvelope(
        run_id="run-1",
        attempt_id="attempt-1",
        seq=seq,
        event=event,
    )


@pytest.mark.asyncio
async def test_real_session_executes_and_restores_messages_with_extra() -> None:
    checkpointer = MemoryCheckpointer()
    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("hello back")])
    agent = create_cubeplex_agent(
        bound_model=provider.model("test-model"),
        checkpointer=checkpointer,
        thread_id="conversation-1",
    )
    agent.session.state_context["todo"] = {"items": ["keep me"]}
    observed: list[Any] = []

    result = await execute_session(
        session=agent.session,
        request=PromptExecutionRequest(
            run_id="run-1",
            attempt_id="attempt-1",
            message="hello",
        ),
        on_agent_event=observed.append,
    )

    assert require_host_success(result) == "completed"
    assert observed

    restored = create_cubeplex_agent(
        bound_model=FauxProvider().model("test-model"),
        checkpointer=checkpointer,
        thread_id="conversation-1",
    )
    checkpoint = await restored.session.load_checkpoint()
    assert checkpoint is not None
    assert len(checkpoint.messages) == 2
    assert restored.session.state_context["todo"] == {"items": ["keep me"]}


@pytest.mark.asyncio
async def test_adapter_uses_required_bounded_consumer_and_returns_result() -> None:
    result = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-1",
        outcome="completed",
        checkpoint_committed=True,
    )
    session = _FakeSession(
        [
            _envelope(1, AgentStartEvent()),
            _envelope(2, ExecutionFinished(result=result)),
        ],
        result,
    )
    observed = []

    returned = await execute_session(
        session=session,
        request=PromptExecutionRequest(
            run_id="run-1",
            attempt_id="attempt-1",
            message="hello",
        ),
        on_agent_event=lambda event: observed.append(event),
    )

    assert returned is result
    assert [type(event) for event in observed] == [AgentStartEvent]
    assert session.subscription is not None
    assert session.subscription["required"] is True
    assert session.subscription["capacity"] == 256
    assert session.subscription["delivery_timeout"] == 6.0


@pytest.mark.asyncio
async def test_adapter_does_not_apply_host_limit_to_raw_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.streams import execution_adapter

    result = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-1",
        outcome="completed",
        checkpoint_committed=True,
    )
    session = _FakeSession([_envelope(1, AgentStartEvent())], result)
    monkeypatch.setattr(execution_adapter, "MAX_EVENT_BYTES", 1)

    returned = await execute_session(
        session=session,
        request=PromptExecutionRequest(
            run_id="run-1",
            attempt_id="attempt-1",
            message="hello",
        ),
        on_agent_event=lambda event: None,
    )

    assert returned is result


@pytest.mark.asyncio
async def test_result_mapping_rejects_required_consumer_delivery_error() -> None:
    result = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-1",
        outcome="completed",
        checkpoint_committed=True,
        delivery_errors=(
            DeliveryError(
                consumer="cubeplex-runtime",
                seq=7,
                reason="timeout",
                message="final notification timed out",
            ),
        ),
    )
    session = _FakeSession([_envelope(1, ExecutionFinished(result=result))], result)

    returned = await execute_session(
        session=session,
        request=PromptExecutionRequest(
            run_id="run-1",
            attempt_id="attempt-1",
            message="hello",
        ),
        on_agent_event=lambda event: None,
    )

    projection_error = host_projection_error(returned)
    assert isinstance(projection_error, EventProjectionError)
    assert "cubeplex-runtime delivery timeout" in str(projection_error)
    assert require_host_success(returned) == "completed"


@pytest.mark.asyncio
async def test_durable_suspension_survives_projection_delivery_error() -> None:
    result = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-1",
        outcome="suspended",
        pending_request=object(),  # type: ignore[arg-type]
        checkpoint_committed=True,
        delivery_errors=(
            DeliveryError(
                consumer="cubeplex-runtime",
                seq=7,
                reason="timeout",
                message="HITL notification timed out",
            ),
        ),
    )
    session = _FakeSession([_envelope(1, ExecutionFinished(result=result))], result)

    returned = await execute_session(
        session=session,
        request=PromptExecutionRequest(
            run_id="run-1",
            attempt_id="attempt-1",
            message="hello",
        ),
        on_agent_event=lambda event: None,
    )

    assert returned.delivery_errors == result.delivery_errors
    assert require_host_success(returned) == "paused_hitl"


def test_non_success_outcome_preserves_cause_and_projection_diagnostic() -> None:
    provider_error = RuntimeError("provider failed")
    delivery_errors = (
        DeliveryError(
            consumer="cubeplex-runtime",
            seq=7,
            reason="timeout",
            message="final notification timed out",
        ),
    )
    failed = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-1",
        outcome="failed",
        error=ExecutionError(
            kind="execution",
            message="provider failed",
            cause=provider_error,
        ),
        delivery_errors=delivery_errors,
    )
    cancelled = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-2",
        outcome="cancelled",
        delivery_errors=delivery_errors,
    )

    assert isinstance(host_projection_error(failed), EventProjectionError)
    with pytest.raises(RuntimeError, match="provider failed") as exc_info:
        require_host_success(failed)
    assert exc_info.value is provider_error

    assert isinstance(host_projection_error(cancelled), EventProjectionError)
    with pytest.raises(asyncio.CancelledError):
        require_host_success(cancelled)


@pytest.mark.asyncio
async def test_adapter_stops_when_host_publication_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.streams import execution_adapter

    result = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-1",
        outcome="completed",
        checkpoint_committed=True,
    )
    session = _FakeSession([_envelope(1, AgentStartEvent())], result)
    monkeypatch.setattr(execution_adapter, "HOST_EVENT_PUBLISH_TIMEOUT_SECONDS", 0.01)

    async def stalled_publish(_event: Any) -> None:
        await asyncio.Event().wait()

    with pytest.raises(EventProjectionError, match="timed out publishing"):
        await execute_session(
            session=session,
            request=PromptExecutionRequest(
                run_id="run-1",
                attempt_id="attempt-1",
                message="hello",
            ),
            on_agent_event=stalled_publish,
        )


@pytest.mark.asyncio
async def test_adapter_acknowledges_only_checkpoint_committed_input() -> None:
    result = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-1",
        outcome="completed",
    )
    session = _FakeSession(
        [
            _envelope(1, InputCommitted(input_id="memory", durability="memory")),
            _envelope(2, InputCommitted(input_id="durable", durability="checkpoint")),
        ],
        result,
    )
    committed: list[str] = []

    await execute_session(
        session=session,
        request=PromptExecutionRequest(
            run_id="run-1",
            attempt_id="attempt-1",
            message="hello",
        ),
        on_agent_event=lambda event: None,
        on_checkpoint_input=lambda input_id: committed.append(input_id),
    )

    assert committed == ["durable"]


def test_result_mapping_uses_public_outcome_and_durable_facts() -> None:
    completed = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-1",
        outcome="completed",
        checkpoint_committed=True,
    )
    suspended = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-2",
        outcome="suspended",
        pending_request=object(),  # type: ignore[arg-type]
        checkpoint_committed=True,
    )

    assert require_host_success(completed) == "completed"
    assert require_host_success(suspended) == "paused_hitl"


def test_result_mapping_preserves_failure_and_cancellation() -> None:
    provider_error = RuntimeError("provider failed")
    failed = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-1",
        outcome="failed",
        error=ExecutionError(
            kind="execution",
            message="provider failed",
            cause=provider_error,
        ),
    )
    cancelled = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-2",
        outcome="cancelled",
    )

    with pytest.raises(RuntimeError, match="provider failed") as exc_info:
        require_host_success(failed)
    assert exc_info.value is provider_error

    with pytest.raises(asyncio.CancelledError):
        require_host_success(cancelled)


def test_result_mapping_rejects_non_durable_suspension() -> None:
    result = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-1",
        outcome="suspended",
        checkpoint_committed=False,
    )

    with pytest.raises(CubeloopAgentRunError, match="not durably checkpointed"):
        require_host_success(result)


def test_respond_dangling_suspension_completes_but_follow_up_pauses() -> None:
    dangling = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-1",
        outcome="suspended",
        pending_request=type("Pending", (), {"question_id": "q-original"})(),
        checkpoint_committed=True,
    )
    follow_up = ExecutionResult(
        run_id="run-1",
        attempt_id="attempt-2",
        outcome="suspended",
        pending_request=type("Pending", (), {"question_id": "q-next"})(),
        checkpoint_committed=True,
    )

    assert require_host_success(dangling, answered_question_id="q-original") == "completed"
    assert require_host_success(follow_up, answered_question_id="q-original") == "paused_hitl"
