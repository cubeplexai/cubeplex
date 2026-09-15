from __future__ import annotations

from cubeplex.errors import ErrorCode
from cubeplex.streams.execution_adapter import CubeloopAgentRunError
from cubeplex.streams.run_manager import _message_for_run_exception


def test_cubeloop_agent_error_message_is_user_visible() -> None:
    exc = CubeloopAgentRunError("provider rejected reasoning")

    message = _message_for_run_exception(
        exc,
        ErrorCode.internal_error,
        {"model": "glm-5.2"},
    )

    assert message == "provider rejected reasoning"
