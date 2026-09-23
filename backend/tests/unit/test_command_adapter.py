"""Provider failures are not cancellation or successful-exit evidence."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from opensandbox.exceptions import SandboxApiException

from cubeplex.sandbox.base import ProcessHandle, SandboxError
from cubeplex.sandbox.command_adapter import CommandAdapter
from cubeplex.sandbox.opensandbox import OpenSandbox


def provider() -> MagicMock:
    raw = MagicMock()
    raw.id = "original-instance"
    raw.commands.get_command_status = AsyncMock()
    raw.commands.interrupt = AsyncMock()
    raw.commands.get_background_command_logs = AsyncMock(
        side_effect=SandboxApiException("logs unavailable", status_code=503)
    )
    return raw


@pytest.mark.parametrize("exit_code", [0, 7])
async def test_natural_exit_ignores_log_outage_and_is_not_killed(exit_code: int) -> None:
    raw = provider()
    raw.commands.get_command_status.return_value = SimpleNamespace(
        running=False, exit_code=exit_code
    )
    adapter = CommandAdapter(OpenSandbox(sandbox=raw), sandbox_instance_id=raw.id)
    result = await adapter.observe_and_stop(
        ProcessHandle("cmd", "proc"), stop_requested=True, check_owner=AsyncMock()
    )
    assert result.snapshot is not None
    assert (result.snapshot.status, result.snapshot.exit_code) == ("exited", exit_code)
    assert result.logs_read is False
    raw.commands.interrupt.assert_not_awaited()
    raw.commands.get_background_command_logs.assert_awaited_once_with("proc", cursor=None)


async def test_poll_returns_output_with_a_candidate_cursor() -> None:
    raw = provider()
    raw.commands.get_command_status.return_value = SimpleNamespace(running=False, exit_code=0)
    raw.commands.get_background_command_logs.side_effect = None
    raw.commands.get_background_command_logs.return_value = SimpleNamespace(
        content="complete output\n", cursor=7
    )
    adapter = CommandAdapter(OpenSandbox(sandbox=raw), sandbox_instance_id=raw.id)

    result = await adapter.observe_and_stop(
        ProcessHandle("cmd", "proc", log_cursor="3"),
        stop_requested=False,
        check_owner=AsyncMock(),
    )

    assert result.logs_read is True
    assert result.snapshot is not None
    assert result.snapshot.new_output == "complete output\n"
    assert result.snapshot.log_cursor == "7"


async def test_cancel_error_followed_by_running_remains_running() -> None:
    raw = provider()
    raw.commands.get_command_status.return_value = SimpleNamespace(running=True, exit_code=None)
    raw.commands.interrupt.side_effect = SandboxApiException("not running", status_code=404)
    adapter = CommandAdapter(OpenSandbox(sandbox=raw), sandbox_instance_id=raw.id)
    result = await adapter.observe_and_stop(
        ProcessHandle("cmd", "proc"), stop_requested=True, check_owner=AsyncMock()
    )
    assert result.snapshot is not None and result.snapshot.status == "running"
    assert result.error is not None


async def test_cancel_error_can_still_have_a_real_late_exit() -> None:
    raw = provider()
    raw.commands.get_command_status.side_effect = [
        SimpleNamespace(running=True, exit_code=None),
        SimpleNamespace(running=False, exit_code=143),
    ]
    raw.commands.interrupt.side_effect = SandboxApiException("timeout", status_code=504)
    adapter = CommandAdapter(OpenSandbox(sandbox=raw), sandbox_instance_id=raw.id)
    result = await adapter.observe_and_stop(
        ProcessHandle("cmd", "proc"), stop_requested=True, check_owner=AsyncMock()
    )
    assert result.snapshot is not None
    assert (result.snapshot.status, result.snapshot.exit_code) == ("exited", 143)


async def test_command_404_and_successful_interrupt_are_not_terminal_proof() -> None:
    raw = provider()
    raw.commands.get_command_status.side_effect = SandboxApiException("not found", status_code=404)
    adapter = CommandAdapter(OpenSandbox(sandbox=raw), sandbox_instance_id=raw.id)
    result = await adapter.observe_and_stop(
        ProcessHandle("cmd", "proc"), stop_requested=True, check_owner=AsyncMock()
    )
    assert result.snapshot is None and result.error is not None
    raw.commands.interrupt.assert_awaited_once_with("proc")


async def test_stale_owner_cannot_interrupt_after_observation() -> None:
    raw = provider()
    raw.commands.get_command_status.return_value = SimpleNamespace(running=True, exit_code=None)
    adapter = CommandAdapter(OpenSandbox(sandbox=raw), sandbox_instance_id=raw.id)
    guard = AsyncMock(side_effect=[None, ValueError("owner lost")])
    with pytest.raises(ValueError, match="owner lost"):
        await adapter.observe_and_stop(
            ProcessHandle("cmd", "proc"), stop_requested=True, check_owner=guard
        )
    raw.commands.interrupt.assert_not_awaited()


def test_replacement_attachment_is_rejected() -> None:
    with pytest.raises(SandboxError, match="original"):
        CommandAdapter(OpenSandbox(sandbox=provider()), sandbox_instance_id="replacement")
