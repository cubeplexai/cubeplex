"""Smoke test for ``RunManager._build_agent_for_conversation``.

T7 extracted the inline agent-build code from ``_run_cubepi_path`` into a
named factory so the prompt path (T8 respond, T10 cancel-paused) can reuse
it. The load-bearing invariant other tasks rely on is that the returned
HITL channel is a ``CheckpointedChannel`` wired with the same ``run_id``
the caller supplied — that channel writes ``pending_request`` and
``pending_run_id`` atomically to the cubepi_threads row, which is what
lets a worker on a different process pick up the answer.

Heavy DI (LLM factory, agent factory, MCP/skill/sandbox loaders) is
stubbed: this is a smoke test for the factory plumbing, not the
middleware stack. Integration coverage comes via T16 E2E.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from cubepi.hitl import CheckpointedChannel

from cubebox.streams.run_manager import RunContext, RunManager

pytestmark = pytest.mark.asyncio

_RUN_ID = "01H_T7_SMOKE_RUN"
_CONV_ID = "conv_t7_smoke"


def _stub_app() -> MagicMock:
    """A FastAPI app whose state exposes the bits LLMFactory + MCP loader touch."""
    app = MagicMock()
    app.state.encryption_backend = MagicMock()
    app.state.mcp_user_token_signer = MagicMock()
    app.state.tracer = None
    return app


def _stub_cp() -> MagicMock:
    """A checkpointer that ``create_cubebox_agent`` accepts and the caller can
    pass to ``CheckpointedChannel``."""
    cp = MagicMock()
    cp.load = AsyncMock(return_value=None)
    cp.save_pending_request = AsyncMock(return_value=None)
    return cp


async def _build(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sandbox: Any | None,
) -> tuple[MagicMock, list[Any], Any]:
    """Drive the factory with the minimum viable mock surface."""
    # The agent factory's actual implementation needs a real provider +
    # model + middleware. We never run the agent here, so a MagicMock with
    # the attributes the caller reads is enough.
    mock_agent = MagicMock()
    mock_agent._extra = {}
    monkeypatch.setattr(
        "cubebox.agents.graph.create_cubebox_agent",
        MagicMock(return_value=mock_agent),
    )

    # LLMFactory.resolve_default_provider_and_config: return a sentinel
    # tuple so the path through the try/commit branch succeeds without a
    # DB. build_cubepi_provider returns a MagicMock; the agent factory is
    # stubbed so it never inspects it.
    mock_factory_inst = MagicMock()
    mock_factory_inst.resolve_default_provider_and_config = AsyncMock(
        return_value=("anthropic", "claude-stub", MagicMock()),
    )
    mock_model_config = MagicMock()
    mock_model_config.max_tokens = 32000
    mock_model_config.reasoning = False
    mock_factory_inst.get_model_config = MagicMock(return_value=mock_model_config)
    mock_factory_inst.build_cubepi_provider = MagicMock(return_value=MagicMock())
    mock_factory_inst.llm_config = MagicMock()
    mock_factory_inst.llm_config.providers = {}
    monkeypatch.setattr(
        "cubebox.llm.factory.LLMFactory",
        MagicMock(return_value=mock_factory_inst),
    )

    rm = RunManager(
        app=_stub_app(),
        redis=MagicMock(),
        key_prefix="test_t7",
        run_event_ttl_seconds=60,
    )
    cp = _stub_cp()
    extra_ref_holder: dict[str, Any] = {"extra": None}

    agent, all_tools, channel = await rm._build_agent_for_conversation(
        ctx=RunContext(user_id="u1", org_id="o1", workspace_id="w1"),
        conversation_id=_CONV_ID,
        run_id=_RUN_ID,
        cp=cp,
        sandbox=sandbox,
        skill_catalog=None,
        catalog_session=None,
        effective_system_prompt="you are a test",
        extra_ref_holder=extra_ref_holder,
        sse_queue=MagicMock(),
        publish_stream_event=MagicMock(),
    )
    # The factory stashes provider_name / model_id / mem_repo_factory on
    # extra_ref_holder so the caller can wire its writeback + relevance
    # snapshot without re-resolving the LLM config.
    assert extra_ref_holder["provider_name"] == "anthropic"
    assert extra_ref_holder["model_id"] == "claude-stub"
    assert callable(extra_ref_holder["mem_repo_factory"])
    return agent, all_tools, channel


async def test_build_returns_tuple_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The factory's contract for T8/T10 is a 3-tuple of
    ``(agent, all_tools, sandbox_hitl_channel)``."""
    agent, all_tools, channel = await _build(monkeypatch, sandbox=MagicMock())
    assert agent is not None
    assert isinstance(all_tools, list)
    # Tools are a list of callable/registered objects; the smoke test just
    # cares that the return slot is a list (not None, not a tuple).
    assert isinstance(channel, CheckpointedChannel)


async def test_build_wires_run_id_into_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CheckpointedChannel must carry the supplied ``run_id`` — that's
    what makes pending_run_id atomic with pending_request, and lets a
    worker on a different process resume the right run."""
    _agent, _tools, channel = await _build(monkeypatch, sandbox=MagicMock())
    assert isinstance(channel, CheckpointedChannel)
    # CheckpointedChannel stores the run_id on a private attribute; assert
    # against whichever public-ish accessor exists.
    stored_run_id = getattr(channel, "run_id", None) or getattr(channel, "_run_id", None)
    assert stored_run_id == _RUN_ID


async def test_build_with_no_sandbox_returns_none_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When sandbox is None the factory MUST still build an agent (cancel
    path needs this). No sandbox → no SandboxMiddleware → no
    CheckpointedChannel. T10 depends on this graceful path."""
    agent, _tools, channel = await _build(monkeypatch, sandbox=None)
    assert agent is not None
    assert channel is None
