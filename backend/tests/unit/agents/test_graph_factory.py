"""create_cubebox_agent — bound-model wiring smoke test (cubepi 0.7)."""

from __future__ import annotations

from cubepi.providers.faux import FauxProvider

from cubebox.agents.graph import create_cubebox_agent


def test_agent_uses_provider_bound_model() -> None:
    provider = FauxProvider(provider_id="faux")
    agent = create_cubebox_agent(
        provider=provider,
        model_id="m1",
        provider_name="faux",
        max_tokens=1024,
        temperature=0.3,
    )
    assert agent._state.model.provider_id == "faux"
    assert agent._state.model.id == "m1"
    assert agent._state.model.max_tokens == 1024
