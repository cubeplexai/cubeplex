"""Process facts, not cancellation requests or timeouts, determine task results."""

import pytest

from cubeplex.sandbox.base import ProcessSnapshot
from cubeplex.services.background_task_lifecycle import command_result_readiness, command_state


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (ProcessSnapshot(status="running"), "running"),
        (ProcessSnapshot(status="running", exit_code=0), "running"),
        (ProcessSnapshot(status="exited", exit_code=0), "succeeded"),
        (ProcessSnapshot(status="exited", exit_code=2), "failed"),
        (ProcessSnapshot(status="exited", exit_code=-9), "failed"),
        (ProcessSnapshot(status="exited"), "unknown"),
        (ProcessSnapshot(status="killed", exit_code=-9), "cancelled"),
    ],
)
def test_mapping_requires_exit_evidence(snapshot: ProcessSnapshot, expected: str) -> None:
    assert command_state(snapshot) == expected


@pytest.mark.parametrize(
    "state", ["starting", "running", "unknown", "succeeded", "failed", "cancelled"]
)
@pytest.mark.parametrize("logs", ["pending", "retrying", "complete", "unavailable"])
def test_result_readiness_is_separate_from_process_state(state: str, logs: str) -> None:
    expected = "pending"
    if state in {"succeeded", "failed", "cancelled"}:
        expected = {"complete": "ready", "unavailable": "unavailable"}.get(logs, "pending")
    assert command_result_readiness(state=state, log_state=logs) == expected
