"""Unit tests for force-compact service (no active agent / DB)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from cubepi.providers.base import TextContent, UserMessage

from cubeplex.services.conversation_compact import force_compact_conversation


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


class _FakeCp:
    def __init__(self, messages: list[Any] | None, extra: dict[str, Any] | None = None) -> None:
        self._messages = messages
        self._extra = extra or {}
        self.saved: dict[str, Any] | None = None

    async def load(self, _thread_id: str) -> SimpleNamespace | None:
        if self._messages is None:
            return None
        return SimpleNamespace(messages=self._messages, extra=dict(self._extra))

    async def save_extra(self, _thread_id: str, extra: dict[str, Any]) -> None:
        self.saved = extra


class _CpCtx:
    def __init__(self, cp: _FakeCp) -> None:
        self.cp = cp

    async def __aenter__(self) -> _FakeCp:
        return self.cp

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_force_compact_empty_history() -> None:
    cp = _FakeCp(messages=[])
    with patch(
        "cubeplex.services.conversation_compact.shared_checkpointer",
        return_value=_CpCtx(cp),
    ):
        result = await force_compact_conversation("conv-1")
    assert result.ok is True
    assert result.compacted is False
    assert result.reason == "empty"


@pytest.mark.asyncio
async def test_force_compact_too_short() -> None:
    cp = _FakeCp(messages=[_user("hi"), _user("there")])
    with patch(
        "cubeplex.services.conversation_compact.shared_checkpointer",
        return_value=_CpCtx(cp),
    ):
        result = await force_compact_conversation("conv-1")
    assert result.compacted is False
    assert result.reason == "too_short"


@pytest.mark.asyncio
async def test_force_compact_writes_extra_when_enough_messages() -> None:
    # Enough messages to clear min_compact (4) and leave a tail.
    msgs = [_user(f"message {i} " + ("x" * 200)) for i in range(12)]
    cp = _FakeCp(messages=msgs)
    with (
        patch(
            "cubeplex.services.conversation_compact.shared_checkpointer",
            return_value=_CpCtx(cp),
        ),
        patch(
            "cubeplex.services.conversation_compact.tail_start_by_tokens",
            return_value=8,
        ),
        patch(
            "cubeplex.services.conversation_compact.safe_boundary",
            return_value=8,
        ),
    ):
        result = await force_compact_conversation("conv-1")

    assert result.compacted is True
    assert result.boundary == 8
    assert cp.saved is not None
    assert "compaction" in cp.saved
    assert cp.saved["compaction_until_msg_index"] == 8
