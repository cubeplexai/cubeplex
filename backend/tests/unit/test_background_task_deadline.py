"""The managed command deadline is independent of foreground and monitor budgets."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import dynaconf
import pytest

from cubeplex.models.background_task import INFLIGHT_TASK_STATES, TERMINAL_TASK_STATES
from cubeplex.models.sandbox_command import SandboxCommandKind
from cubeplex.services.background_tasks import CommandExecutionDetails, command_deadline

NOW = datetime(2026, 9, 21, tzinfo=UTC)
DETAILS = CommandExecutionDetails("sbx-test", "instance-a", "local", "build", "/tmp/log")


def test_unknown_execution_is_not_terminal() -> None:
    assert "unknown" in INFLIGHT_TASK_STATES
    assert "unknown" not in TERMINAL_TASK_STATES


@pytest.mark.parametrize("seconds", [1, 3600, 7200, 2**31 - 1])
def test_explicit_timeout_overrides_default(seconds: int) -> None:
    assert command_deadline(now=NOW, details=replace(DETAILS, timeout_seconds=seconds)) == (
        NOW + timedelta(seconds=seconds)
    )


@pytest.mark.parametrize("seconds", [0, -1, 2**31, 10**100, True])
def test_unusable_explicit_timeout_is_rejected(seconds: int) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        command_deadline(now=NOW, details=replace(DETAILS, timeout_seconds=seconds))


def test_monitor_retains_its_own_absolute_deadline() -> None:
    deadline = NOW + timedelta(hours=10)
    details = replace(DETAILS, kind=SandboxCommandKind.monitor, monitor_deadline_at=deadline)
    assert command_deadline(now=NOW, details=details) == deadline
    assert command_deadline(now=NOW, details=replace(details, monitor_deadline_at=None)) is None


def test_naive_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        command_deadline(now=NOW.replace(tzinfo=None), details=DETAILS)


@pytest.mark.parametrize("seconds", [2**31, 10**100])
def test_unrepresentable_deployment_default_is_rejected_before_admission(
    monkeypatch: pytest.MonkeyPatch, seconds: int
) -> None:
    import cubeplex.config as config_module

    monkeypatch.setattr(config_module.config, "get", lambda key: seconds)
    with pytest.raises(RuntimeError, match="COMMAND_DEFAULT_TIMEOUT_SECONDS"):
        config_module.get_command_default_timeout_seconds()


def test_default_and_explicit_timeout_share_the_upper_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cubeplex.config as config_module

    monkeypatch.setattr(config_module.config, "get", lambda key: 2**31 - 1)
    assert command_deadline(now=NOW, details=DETAILS) == NOW + timedelta(seconds=2**31 - 1)
    with pytest.raises(ValueError, match="representable"):
        command_deadline(
            now=datetime.max.replace(tzinfo=UTC), details=replace(DETAILS, timeout_seconds=1)
        )


def test_base_default_and_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    base = Path(__file__).parents[2] / "config.yaml"
    monkeypatch.delenv("LIFECYCLE_TEST_SANDBOX__COMMAND_DEFAULT_TIMEOUT_SECONDS", raising=False)
    settings = dynaconf.Dynaconf(
        environments=True,
        env="default",
        envvar_prefix="LIFECYCLE_TEST",
        settings_files=[str(base)],
        load_dotenv=False,
    )
    assert settings.get("sandbox.command_default_timeout_seconds") == 3600
    monkeypatch.setenv("LIFECYCLE_TEST_SANDBOX__COMMAND_DEFAULT_TIMEOUT_SECONDS", "7200")
    settings.reload()
    assert settings.get("sandbox.command_default_timeout_seconds") == 7200


def test_yaml_default_override_and_real_environment_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import cubeplex.config as config_module

    override = tmp_path / "config.yaml"
    override.write_text("default:\n  sandbox:\n    command_default_timeout_seconds: 9000\n")
    key = "CUBEPLEX_SANDBOX__COMMAND_DEFAULT_TIMEOUT_SECONDS"
    monkeypatch.delenv(key, raising=False)
    settings = dynaconf.Dynaconf(
        environments=True,
        env="default",
        envvar_prefix="CUBEPLEX",
        settings_files=[str(override)],
        load_dotenv=False,
    )
    monkeypatch.setattr(config_module, "config", settings)
    assert config_module.get_command_default_timeout_seconds() == 9000
    monkeypatch.setenv(key, "7200")
    settings.reload()
    assert config_module.get_command_default_timeout_seconds() == 7200
    monkeypatch.setenv(key, "@none None")
    settings.reload()
    with pytest.raises(RuntimeError, match="positive integer"):
        config_module.get_command_default_timeout_seconds()
