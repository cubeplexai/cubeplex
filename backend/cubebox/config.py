"""
cubebox Configuration Management

Uses dynaconf for environment-based configuration with support for:
- YAML configuration files
- Environment variable overrides
- Development/production/testing environments
"""

import os
from pathlib import Path

import dynaconf
from dotenv import load_dotenv as _load_worktree_dotenv

# Get the backend directory (where config files are located)
backend_dir = Path(__file__).parent.parent

# Load configuration based on environment
env = os.getenv("ENV_FOR_DYNACONF", "development")
settings_files = [
    str(backend_dir / "config.yaml"),  # Base configuration
    str(backend_dir / f"config.{env}.yaml"),
    # NOTE: do NOT list config.<env>.local.yaml here — dynaconf already
    # auto-loads each settings file's ".local." sibling. Listing it again
    # loads it twice, and with dynaconf_merge that doubles every list value
    # (e.g. llm.fallback_models accumulates each entry twice).
    # Helm deploys mount non-secret overrides via a ConfigMap (.local.yaml,
    # picked up by the auto-load) and credentials via a Secret (.secrets.yaml,
    # listed below). Both are dynaconf-merged so operators can split
    # safe-vs-sensitive without an init container.
    str(backend_dir / f"config.{env}.secrets.yaml"),
]

# Load worktree-specific allocations (ports, DB schema, Redis prefix) from
# .worktree.env at the worktree root, BEFORE dynaconf reads. override=False
# means real shell exports still win. See
# docs/dev/specs/2026-04-28-worktree-parallel-dev-isolation-design.md
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
