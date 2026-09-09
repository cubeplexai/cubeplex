"""Startup contract for the required OpenSandbox provider."""

from __future__ import annotations

import pytest


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
    }
    monkeypatch.setattr(config, "get", lambda key, default=None: configured.get(key, default))

    from cubeplex.api.app import validate_sandbox_config

    validate_sandbox_config()
