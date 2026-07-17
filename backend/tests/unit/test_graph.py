"""graph_pi tests — create_cubeplex_agent (M1.4)."""

import pytest
from cubepi import Agent
from cubepi.providers.faux import FauxProvider, faux_assistant_message

from cubeplex.agents.graph import create_cubeplex_agent


def _faux_bound_model() -> object:
    """Build a single-leg FauxProvider-backed BoundModel for unit tests."""
    return FauxProvider().model("test-model")


def test_returns_cubepi_agent_instance() -> None:
    agent = create_cubeplex_agent(
        bound_model=_faux_bound_model(),
        system_prompt="You are helpful.",
    )
    assert isinstance(agent, Agent)


def test_agent_carries_system_prompt() -> None:
    agent = create_cubeplex_agent(
        bound_model=_faux_bound_model(),
        system_prompt="You are helpful.",
    )
    assert agent._state.system_prompt == "You are helpful."


def test_agent_accepts_checkpointer_and_thread_id() -> None:
    from cubepi.checkpointer import MemoryCheckpointer

    cp = MemoryCheckpointer()
    agent = create_cubeplex_agent(
        bound_model=_faux_bound_model(),
        system_prompt="",
        checkpointer=cp,
        thread_id="conv-123",
    )
    assert agent.checkpointer is cp
    assert agent.thread_id == "conv-123"


def test_agent_accepts_empty_tools() -> None:
    agent = create_cubeplex_agent(
        bound_model=_faux_bound_model(),
        system_prompt="",
    )
    # No tools provided → empty list
    assert agent._state.tools == []


@pytest.mark.asyncio
async def test_bare_agent_runs_a_turn() -> None:
    """Smoke: bare cubepi agent runs an LLM call against FauxProvider."""
    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("hello back")])
    agent = create_cubeplex_agent(
        bound_model=provider.model("test-model"),
        system_prompt="You are helpful.",
    )
    await agent.prompt("hi")
    assert len(agent.state.messages) == 2
    assert agent.state.messages[-1].content[0].text == "hello back"
