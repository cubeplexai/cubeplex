"""Conftest for tests/diagnostic/.

Provides credential-checking fixtures that skip cleanly when provider
API keys are not configured locally. This keeps the diagnostic tests safe
to collect in CI without needing real credentials.

All tests here are marked @pytest.mark.real_llm (provider creds required)
and @pytest.mark.diagnostic (opt-in subset marker). Both markers are already
registered in pyproject.toml.
"""

from __future__ import annotations

import os

import pytest


def _read_env_or_skip(var: str, provider: str) -> str:
    """Return env var value or skip the test with a clear message."""
    val = os.environ.get(var, "").strip()
    if not val:
        pytest.skip(f"{provider} credentials not set ({var} is empty) — skipping diagnostic test")
    return val


@pytest.fixture
def deepseek_api_key() -> str:
    """Return the DeepSeek API key or skip."""
    return _read_env_or_skip("CUBEPLEX_LLM__PROVIDERS__DEEPSEEK__API_KEY", "DeepSeek")


def _load_dev_config_key(dotted_path: str) -> str:
    """Load a key from the development dynaconf config, bypassing the test env.

    Diagnostic tests read credentials from config.development.local.yaml (not
    config.test.yaml), so we instantiate a separate Dynaconf object scoped to
    the development environment instead of relying on the module-level `config`
    which is already bound to `test` by the time this fixture runs.
    """
    import pathlib

    from dynaconf import Dynaconf

    backend_root = pathlib.Path(__file__).parent.parent.parent
    dev_config = Dynaconf(
        settings_files=[
            str(backend_root / "config.yaml"),
            str(backend_root / "config.development.yaml"),
            str(backend_root / "config.development.local.yaml"),
        ],
        environments=True,
        env="development",
        # Prevent ENV_FOR_DYNACONF=test (set by the top-level conftest.py) from
        # overriding the explicit env="development" here. We use a dummy env_switcher
        # var name so the global pytest-session environment doesn't bleed in.
        env_switcher="DIAG_ENV_FOR_DYNACONF",
        env_prefix="CUBEPLEX",
        load_dotenv=True,
        dotenv_path=str(backend_root / ".env"),
    )
    return str(dev_config.get(dotted_path, "") or "")


@pytest.fixture
def alicode_api_key() -> str:
    """Return the alicode (DashScope coding) API key or skip.

    alicode stores the key directly in config.development.local.yaml (not via env var),
    so we load it from the development config. Accepts CUBEPLEX_ALICODE_API_KEY env
    override for CI injection.
    """
    env_val = os.environ.get("CUBEPLEX_ALICODE_API_KEY", "").strip()
    if env_val:
        return env_val
    key = _load_dev_config_key("llm.providers.alicode.api_key")
    if key and key != "key-in-env":
        return key
    pytest.skip(
        "alicode credentials not available — set CUBEPLEX_ALICODE_API_KEY or ensure "
        "config.development.local.yaml is present with alicode.api_key"
    )


@pytest.fixture
def arkcode_api_key() -> str:
    """Return the arkcode (Ark Coding) API key or skip.

    Like alicode, arkcode stores the key in local yaml config.
    """
    env_val = os.environ.get("CUBEPLEX_ARKCODE_API_KEY", "").strip()
    if env_val:
        return env_val
    key = _load_dev_config_key("llm.providers.arkcode.api_key")
    if key and key != "key-in-env":
        return key
    pytest.skip(
        "arkcode credentials not available — set CUBEPLEX_ARKCODE_API_KEY or ensure "
        "config.development.local.yaml is present with arkcode.api_key"
    )
