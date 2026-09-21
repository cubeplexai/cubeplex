"""Process facts, not cancellation requests or timeouts, determine task results."""

import pytest

from cubeplex.sandbox.base import ProcessSnapshot
from cubeplex.services.background_task_lifecycle import command_state


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
