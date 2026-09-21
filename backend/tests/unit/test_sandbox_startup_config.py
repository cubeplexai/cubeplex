"""Startup contract for the required OpenSandbox provider."""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_sandbox_coordinator_and_cleanup_stop_as_one_shutdown_phase() -> None:
    from cubeplex.api.app import _stop_sandbox_background_tasks

    started = [asyncio.Event(), asyncio.Event()]

    async def _background(index: int) -> None:
        started[index].set()
        await asyncio.Event().wait()

    coordinator = asyncio.create_task(_background(0))
    cleanup = asyncio.create_task(_background(1))
    await asyncio.gather(*(event.wait() for event in started))

    await _stop_sandbox_background_tasks(coordinator, cleanup)

    assert coordinator.cancelled()
    assert cleanup.cancelled()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"sandbox.enabled": False}, "CUBEPLEX_SANDBOX__ENABLED must be true"),
        (
            {"sandbox.enabled": True, "sandbox.domain": ""},
            "CUBEPLEX_SANDBOX__DOMAIN is required",
        ),
        (
            {"sandbox.enabled": True, "sandbox.image": ""},
            "CUBEPLEX_SANDBOX__IMAGE is required",
        ),
        (
            {"sandbox.enabled": True, "sandbox.api_key": ""},
            "CUBEPLEX_SANDBOX__API_KEY is required",
        ),
        (
            {"sandbox.enabled": True, "sandbox.domain": "USE ENV"},
            "CUBEPLEX_SANDBOX__DOMAIN must not use a placeholder value",
        ),
        (
            {"sandbox.enabled": True, "sandbox.api_key": "REPLACE_ME"},
            "CUBEPLEX_SANDBOX__API_KEY must not use a placeholder value",
        ),
    ],
)
def test_validate_sandbox_config_rejects_incomplete_required_configuration(
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, object],
    message: str,
) -> None:
    from cubeplex.config import config

    configured = {
        "sandbox.enabled": True,
        "sandbox.domain": "opensandbox.example:8090",
        "sandbox.image": "registry.example/cubeplex-sandbox:latest",
        "sandbox.api_key": "test-key",
        "sandbox.command_default_timeout_seconds": 3600,
        **values,
    }
    monkeypatch.setattr(config, "get", lambda key, default=None: configured.get(key, default))

    from cubeplex.api.app import validate_sandbox_config

    with pytest.raises(RuntimeError, match=message):
        validate_sandbox_config()


def test_validate_sandbox_config_accepts_complete_required_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.config import config

    configured = {
        "sandbox.enabled": True,
        "sandbox.domain": "opensandbox.example:8090",
        "sandbox.image": "registry.example/cubeplex-sandbox:latest",
        "sandbox.api_key": "test-key",
        "sandbox.command_default_timeout_seconds": 3600,
    }
    monkeypatch.setattr(config, "get", lambda key, default=None: configured.get(key, default))

    from cubeplex.api.app import validate_sandbox_config

    validate_sandbox_config()


@pytest.mark.parametrize("timeout", [0, -1, 1.5, "3600", None, True, False])
def test_startup_rejects_invalid_managed_command_default(
    monkeypatch: pytest.MonkeyPatch, timeout: object
) -> None:
    from cubeplex.api.app import validate_sandbox_config
    from cubeplex.config import config

    configured = {
        "sandbox.enabled": True,
        "sandbox.domain": "opensandbox.example:8090",
        "sandbox.image": "registry.example/cubeplex-sandbox:latest",
        "sandbox.api_key": "test-key",
        "sandbox.command_default_timeout_seconds": timeout,
    }
    monkeypatch.setattr(config, "get", lambda key, default=None: configured.get(key, default))

    with pytest.raises(RuntimeError, match="COMMAND_DEFAULT_TIMEOUT_SECONDS.*positive integer"):
        validate_sandbox_config()
