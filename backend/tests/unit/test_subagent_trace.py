"""Unit tests for SubAgentMiddleware tracer attach/detach wiring (Task 4).

The middleware spawns an inner cubepi.Agent and runs ``await inner.prompt``.
A process-level ``Tracer`` is threaded in via the ``tracer=`` kwarg; the
middleware must ``tracer.attach(inner)`` around the inner run and always
detach in ``finally`` — best-effort, so tracing can never break the run.

These tests use a ``_FakeTracer`` and reuse the construction/invocation
pattern from ``test_subagents.py``. They do NOT depend on the cubepi-side
run-nesting changes.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cubepi.providers.faux import FauxProvider, faux_assistant_message

from cubebox.middleware.subagents import SubAgent, SubAgentMiddleware


class _FakeTracer:
    def __init__(self) -> None:
        self.attached: list[Any] = []
        self.detached = 0

    def attach(self, agent: Any) -> Any:
        self.attached.append(agent)

        def _detach() -> None:  # this fake's detach is sync (returns None)
            self.detached += 1
            return None

        return _detach


class _AwaitableDetachTracer:
    """Fake whose ``detach()`` returns an awaitable — mirrors the real
    cubepi ``Tracer.attach`` contract, where ``detach()`` runs sync cleanup
    then returns the scheduled-flush ``asyncio.Task``. Exercises the
    ``if res is not None: await res`` branch in the middleware's finally."""

    def __init__(self) -> None:
        self.attached: list[Any] = []
        self.detached = 0
        self.awaited = False

    def attach(self, agent: Any) -> Any:
        self.attached.append(agent)

        def _detach() -> Any:
            self.detached += 1

            async def _flush() -> None:
                self.awaited = True

            return _flush()

        return _detach


def _make_mw(
    tracer: Any,
    *,
    provider: FauxProvider | None = None,
) -> SubAgentMiddleware:
    if provider is None:
        provider = FauxProvider()
    subagent_map = {
        "general-purpose": SubAgent(
            name="general-purpose",
            description="general",
            system_prompt="You are a sub.",
        )
    }
    return SubAgentMiddleware(
        subagent_map=subagent_map,
        default_provider=provider,
        default_model_id="test-model",
        default_provider_name="faux",
        tracer=tracer,
    )


def _args(sub_tool: Any, prompt: str) -> Any:
    return sub_tool.parameters(
        name="x",
        role="r",
        task="t",
        prompt=prompt,
        subagent_type="general-purpose",
    )


@pytest.mark.asyncio
async def test_subagent_attaches_and_detaches_tracer_on_success() -> None:
    """Inner run completes normally: tracer attached once, detached once."""
    tracer = _FakeTracer()
    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("subagent reply")])

    mw = _make_mw(tracer, provider=provider)
    [sub_tool] = mw.tools

    result = await sub_tool.execute(
        "tc-1", _args(sub_tool, "please reply"), signal=None, on_update=None
    )

    assert not result.is_error
    assert len(tracer.attached) == 1
    assert tracer.detached == 1


@pytest.mark.asyncio
async def test_subagent_awaits_awaitable_detach() -> None:
    """When detach() returns an awaitable (the real Tracer schedules a flush
    Task), the middleware awaits it in finally."""
    tracer = _AwaitableDetachTracer()
    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("subagent reply")])

    mw = _make_mw(tracer, provider=provider)
    [sub_tool] = mw.tools

    result = await sub_tool.execute(
        "tc-await", _args(sub_tool, "please reply"), signal=None, on_update=None
    )

    assert not result.is_error
    assert tracer.detached == 1
    assert tracer.awaited is True, "awaitable returned by detach() was not awaited"


@pytest.mark.asyncio
async def test_subagent_detaches_tracer_when_inner_run_fails() -> None:
    """Inner run raises a normal Exception: tool returns is_error, still detaches."""
    tracer = _FakeTracer()
    provider = FauxProvider()
    mw = _make_mw(tracer, provider=provider)
    [sub_tool] = mw.tools

    with patch("cubebox.agents.graph.create_cubebox_agent") as mock_factory:
        mock_agent = AsyncMock()
        mock_agent.subscribe = lambda listener: lambda: None
        mock_agent.prompt = AsyncMock(side_effect=RuntimeError("inner boom"))
        mock_factory.return_value = mock_agent

        result = await sub_tool.execute(
            "tc-2", _args(sub_tool, "break please"), signal=None, on_update=None
        )

    assert result.is_error is True
    assert "inner boom" in result.content[0].text
    assert len(tracer.attached) == 1
    assert tracer.detached == 1


@pytest.mark.asyncio
async def test_subagent_detaches_tracer_when_inner_run_cancelled() -> None:
    """Inner run raises CancelledError (BaseException): propagates but finally detaches."""
    tracer = _FakeTracer()
    provider = FauxProvider()
    mw = _make_mw(tracer, provider=provider)
    [sub_tool] = mw.tools

    with patch("cubebox.agents.graph.create_cubebox_agent") as mock_factory:
        mock_agent = AsyncMock()
        mock_agent.subscribe = lambda listener: lambda: None
        mock_agent.prompt = AsyncMock(side_effect=asyncio.CancelledError())
        mock_factory.return_value = mock_agent

        with pytest.raises(asyncio.CancelledError):
            await sub_tool.execute(
                "tc-3", _args(sub_tool, "cancel please"), signal=None, on_update=None
            )

    assert len(tracer.attached) == 1
    assert tracer.detached == 1


def test_run_manager_passes_process_tracer_to_subagent_middleware() -> None:
    """The process Tracer read off ``app.state.tracer`` reaches the middleware.

    Driving run_manager's real construction site is impractical because the
    surrounding setup (provider resolution, tool collection, conversation
    context) is heavy to stand up in a unit test — not because of the lazy
    import (that re-resolves the module-level class and is patchable). So we
    assert narrowly: a tracer read via the same
    ``getattr(app.state, "tracer", None)`` pattern run_manager uses flows
    through the ``tracer=`` kwarg into ``SubAgentMiddleware._tracer``. This
    guards the kwarg name and storage; the literal run_manager line is covered
    by inspection.
    """
    sentinel = object()

    class _State:
        tracer = sentinel

    class _App:
        state = _State()

    app = _App()

    mw = SubAgentMiddleware(
        subagent_map={},
        default_provider=FauxProvider(),
        default_model_id="test-model",
        default_provider_name="faux",
        shared_tools=[],
        inherited_middleware=[],
        tracer=getattr(app.state, "tracer", None),
    )

    assert mw._tracer is sentinel
