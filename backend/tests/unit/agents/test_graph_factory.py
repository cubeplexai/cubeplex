"""create_cubeplex_agent — bound-model wiring smoke test (cubepi 0.7)."""

from __future__ import annotations

from cubepi.providers.faux import FauxProvider

from cubeplex.agents.graph import create_cubeplex_agent


def test_agent_uses_provided_bound_model() -> None:
    provider = FauxProvider(provider_id="faux")
    bound = provider.model("m1", max_tokens=1024, temperature=0.3)
    agent = create_cubeplex_agent(bound_model=bound)
    assert agent._state.model.provider_id == "faux"
    assert agent._state.model.id == "m1"
    assert agent._state.model.max_tokens == 1024
