"""
cubebox Configuration Management

Uses dynaconf for environment-based configuration with support for:
- YAML configuration files
- Environment variable overrides
- Development/production/testing environments
"""

import os
from pathlib import Path
from typing import Literal

import dynaconf
from dotenv import load_dotenv as _load_worktree_dotenv
from pydantic import BaseModel, Field

# Get the backend directory (where config files are located)
backend_dir = Path(__file__).parent.parent

# Load configuration based on environment
env = os.getenv("ENV_FOR_DYNACONF", "development")
settings_files = [
    str(backend_dir / "config.yaml"),  # Base configuration
    str(backend_dir / f"config.{env}.yaml"),
    str(backend_dir / f"config.{env}.local.yaml"),
]

# Load worktree-specific allocations (ports, DB schema, Redis prefix) from
# .worktree.env at the worktree root, BEFORE dynaconf reads. override=False
# means real shell exports still win. See
# docs/superpowers/specs/2026-04-28-worktree-parallel-dev-isolation-design.md
_worktree_env_path = backend_dir.parent / ".worktree.env"
if _worktree_env_path.exists():
    _load_worktree_dotenv(_worktree_env_path, override=False)

config = dynaconf.Dynaconf(
    environments=True,
    dotenv_path=str(backend_dir / ".env"),
    envvar_prefix="CUBEBOX",
    settings_files=settings_files,
    load_dotenv=True,
)

if config.langsmith.enabled:
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = config.langsmith.key


class AgentRuntimeConfig(BaseModel):
    """Which agent runtime to use.

    Set via env CUBEBOX_AGENTS__RUNTIME or config.<env>.yaml ``agents.runtime`` key.
    Default ``langgraph`` (current production path).
    ``cubepi`` enables the new cubepi-based runtime (Spec B, in progress).
    """

    runtime: Literal["langgraph", "cubepi"] = Field(default="langgraph")
