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

from cubeplex.llm.config import ProviderConfig
from cubeplex.llm.snapshot import LLMSnapshot, ModelPreset
from cubeplex.streams.run_manager import RunContext, RunManager

pytestmark = pytest.mark.asyncio

_RUN_ID = "01H_T7_SMOKE_RUN"
_CONV_ID = "conv_t7_smoke"


def _stub_app() -> MagicMock:
    """A FastAPI app whose state exposes the bits snapshot loader + MCP loader touch."""
    app = MagicMock()
    app.state.encryption_backend = MagicMock()
    app.state.mcp_user_token_signer = MagicMock()
    app.state.tracer = None
    return app


def _stub_cp() -> MagicMock:
    """A checkpointer that ``create_cubeplex_agent`` accepts and the caller can
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
        "cubeplex.agents.graph.create_cubeplex_agent",
        MagicMock(return_value=mock_agent),
    )

    # load_llm_snapshot returns a deterministic snapshot whose default preset
    # resolves to anthropic/claude-stub. build_chain_model is patched so we
    # don't need a real cubepi provider — the agent factory is stubbed and
    # never inspects the bound model.
    snap = LLMSnapshot(
        providers={
            "anthropic": ProviderConfig.model_validate(
                {
                    "base_url": "https://example.invalid",
                    "api_key": "k",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "claude-stub",
                            "name": "claude-stub",
                            "contextWindow": 128000,
                            "maxTokens": 32000,
                        }
                    ],
                }
            ),
        },
        model_presets=(
            ModelPreset(
                key="default",
                primary="anthropic/claude-stub",
                fallbacks=(),
                kind="tier",
                is_default=True,
            ),
        ),
        task_routing={},
    )

    async def _fake_load(*_a: Any, **_kw: Any) -> LLMSnapshot:
        return snap

    # _build_agent_for_conversation imports load_llm_snapshot / build_chain_model
    # locally inside the method, so we patch the source modules.
    monkeypatch.setattr(
        "cubeplex.llm.snapshot.load_llm_snapshot",
        _fake_load,
    )

    mock_bound_model = MagicMock()
    mock_bound_model.spec.provider_id = "anthropic"
    mock_bound_model.spec.id = "claude-stub"
    mock_bound_model.provider = MagicMock()
    monkeypatch.setattr(
        "cubeplex.llm.builder.build_chain_model",
        MagicMock(return_value=mock_bound_model),
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
        ctx=RunContext(user_id="u1", org_id="o1", workspace_id="w1", conversation_id=_CONV_ID),
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


async def test_build_with_no_sandbox_still_binds_hitl_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When sandbox is None the factory MUST still build an agent (cancel
    path needs this) AND it must still bind a CheckpointedChannel — without
    one, cubepi's ``agent.abort_pending`` short-circuits with HitlError
    before the DB pending row gets cleared, so cancel_paused_run leaves
    the pending row behind. The channel is needed by the ask_user tool
    binding and by abort_pending regardless of whether SandboxMiddleware
    is installed."""
    agent, _tools, channel = await _build(monkeypatch, sandbox=None)
    assert agent is not None
    assert isinstance(channel, CheckpointedChannel)
    stored_run_id = getattr(channel, "run_id", None) or getattr(channel, "_run_id", None)
    assert stored_run_id == _RUN_ID


async def test_build_registers_persona_tools_for_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interactive runs get persona_get + persona_update; tools bind the HITL channel."""
    _agent, tools, _channel = await _build(monkeypatch, sandbox=None)
    names = {getattr(t, "name", None) for t in tools}
    assert "persona_get" in names
    assert "persona_update" in names


async def test_build_keeps_persona_update_schema_for_non_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduled runs still register persona_update (stable tool schema) but
    gate writes at execute time via allow_write=False."""
    mock_agent = MagicMock()
    mock_agent._extra = {}
    monkeypatch.setattr(
        "cubeplex.agents.graph.create_cubeplex_agent",
        MagicMock(return_value=mock_agent),
    )
    from cubeplex.llm.config import ProviderConfig
    from cubeplex.llm.snapshot import LLMSnapshot, ModelPreset

    snap = LLMSnapshot(
        providers={
            "anthropic": ProviderConfig.model_validate(
                {
                    "base_url": "https://example.invalid",
                    "api_key": "k",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "claude-stub",
                            "name": "claude-stub",
                            "contextWindow": 128000,
                            "maxTokens": 32000,
                        }
                    ],
                }
            ),
        },
        model_presets=(
            ModelPreset(
                key="default",
                primary="anthropic/claude-stub",
                fallbacks=(),
                kind="tier",
                is_default=True,
            ),
        ),
        task_routing={},
    )

    async def _fake_load(*_a: Any, **_kw: Any) -> LLMSnapshot:
        return snap

    monkeypatch.setattr("cubeplex.llm.snapshot.load_llm_snapshot", _fake_load)
    mock_bound_model = MagicMock()
    mock_bound_model.spec.provider_id = "anthropic"
    mock_bound_model.spec.id = "claude-stub"
    mock_bound_model.provider = MagicMock()
    monkeypatch.setattr(
        "cubeplex.llm.builder.build_chain_model",
        MagicMock(return_value=mock_bound_model),
    )

    import cubeplex.tools.builtin.persona as persona_mod

    real_create = persona_mod.create_persona_tools
    captured: dict[str, Any] = {}

    def _capture_create_persona_tools(**kwargs: Any) -> list[Any]:
        captured.update(kwargs)
        return real_create(**kwargs)

    monkeypatch.setattr(persona_mod, "create_persona_tools", _capture_create_persona_tools)

    rm = RunManager(
        app=_stub_app(),
        redis=MagicMock(),
        key_prefix="test_t7",
        run_event_ttl_seconds=60,
    )
    extra_ref_holder: dict[str, Any] = {"extra": None}
    _agent, tools, _channel = await rm._build_agent_for_conversation(
        ctx=RunContext(
            user_id="u1",
            org_id="o1",
            workspace_id="w1",
            conversation_id=_CONV_ID,
            trigger="schedule",
        ),
        conversation_id=_CONV_ID,
        run_id=_RUN_ID,
        cp=_stub_cp(),
        sandbox=None,
        skill_catalog=None,
        catalog_session=None,
        effective_system_prompt="you are a test",
        extra_ref_holder=extra_ref_holder,
        sse_queue=MagicMock(),
        publish_stream_event=MagicMock(),
        trigger="schedule",
    )
    names = {getattr(t, "name", None) for t in tools}
    assert "persona_get" in names
    assert "persona_update" in names
    assert captured.get("allow_write") is False
