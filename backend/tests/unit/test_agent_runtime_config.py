"""Tests for AgentRuntimeConfig flag (M0.4)."""

import pytest


def test_default_runtime_is_langgraph() -> None:
    from cubebox.config import AgentRuntimeConfig

    cfg = AgentRuntimeConfig()
    assert cfg.runtime == "langgraph"


def test_runtime_accepts_cubepi() -> None:
    from cubebox.config import AgentRuntimeConfig

    cfg = AgentRuntimeConfig(runtime="cubepi")
    assert cfg.runtime == "cubepi"


def test_runtime_rejects_invalid() -> None:
    from pydantic import ValidationError

    from cubebox.config import AgentRuntimeConfig

    with pytest.raises(ValidationError):
        AgentRuntimeConfig(runtime="something-else")


def test_global_config_exposes_agents_runtime() -> None:
    """The global config object must have config.agents.runtime accessible."""
    from cubebox.config import config

    assert hasattr(config, "agents")
    assert hasattr(config.agents, "runtime")
    assert config.agents.runtime in ("langgraph", "cubepi")
