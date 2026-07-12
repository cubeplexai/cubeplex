from __future__ import annotations

from types import SimpleNamespace

import pytest

from cubeplex.errors import ErrorCode
from cubeplex.streams.run_manager import (
    CubepiAgentRunError,
    _message_for_run_exception,
    _raise_if_cubepi_agent_failed,
)


def test_cubepi_agent_error_message_raises_terminal_run_error() -> None:
    agent = SimpleNamespace(
        state=SimpleNamespace(
            error_message=(
                "[litellm/glm-5.2 @ http://192.168.1.215:4000/v1/] "
                "TypeError: AsyncCompletions.create() got an unexpected keyword "
                "argument 'reasoning_effort'"
            )
        )
    )

    with pytest.raises(CubepiAgentRunError) as exc_info:
        _raise_if_cubepi_agent_failed(agent)

    assert "unexpected keyword argument 'reasoning_effort'" in str(exc_info.value)


def test_cubepi_agent_error_message_is_user_visible() -> None:
    exc = CubepiAgentRunError("provider rejected reasoning")

    message = _message_for_run_exception(
        exc,
        ErrorCode.internal_error,
        {"model": "glm-5.2"},
    )

    assert message == "provider rejected reasoning"


def test_missing_cubepi_agent_error_message_does_not_raise() -> None:
    agent = SimpleNamespace(state=SimpleNamespace(error_message=None))

    _raise_if_cubepi_agent_failed(agent)
