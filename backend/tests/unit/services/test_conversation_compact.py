"""Unit tests for force-compact service (no active agent / DB)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from cubepi.middleware.compaction.state import CompactionState
from cubepi.providers.base import TextContent, UserMessage

from cubeplex.services import conversation_compact as compact_mod
from cubeplex.services.conversation_compact import (
    _load_state,
    force_compact_conversation,
)


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


class _FakeCp:
    def __init__(
        self,
        messages: list[Any] | None,
        extra: dict[str, Any] | None = None,
        *,
        second_messages: list[Any] | None = None,
        null_on_second_load: bool = False,
    ) -> None:
        self._messages = messages
        self._second_messages = second_messages
        self._null_on_second_load = null_on_second_load
        self._extra = extra or {}
        self.saved: dict[str, Any] | None = None
        self.appended: list[Any] | None = None
        self._loads = 0

    async def load(self, _thread_id: str) -> SimpleNamespace | None:
        self._loads += 1
        if self._messages is None:
            return None
        if self._loads > 1 and self._null_on_second_load:
            return None
        msgs = self._messages
        if self._loads > 1 and self._second_messages is not None:
            msgs = self._second_messages
        return SimpleNamespace(messages=msgs, extra=dict(self._extra))

    async def save_extra(self, _thread_id: str, extra: dict[str, Any]) -> None:
        self.saved = extra

    async def append(self, _thread_id: str, messages: list[Any]) -> None:
        self.appended = list(messages)


class _CpCtx:
    def __init__(self, cp: _FakeCp) -> None:
        self.cp = cp

    async def __aenter__(self) -> _FakeCp:
        return self.cp

    async def __aexit__(self, *args: object) -> None:
        return None


def test_load_state_variants() -> None:
    assert _load_state(None) is None
    state = CompactionState(summary="hi")
    assert _load_state(state) is state
    loaded = _load_state({"summary": "from-dict", "summarized_message_ids": []})
    assert loaded is not None
    assert loaded.summary == "from-dict"
    assert _load_state({"summary": 123}) is None  # invalid payload
    assert _load_state("not-a-state") is None


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
async def test_force_compact_none_checkpoint() -> None:
    cp = _FakeCp(messages=None)
    with patch(
        "cubeplex.services.conversation_compact.shared_checkpointer",
        return_value=_CpCtx(cp),
    ):
        result = await force_compact_conversation("conv-1")
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
async def test_force_compact_busy_at_start() -> None:
    async def busy() -> bool:
        return True

    result = await force_compact_conversation("conv-1", is_busy=busy)
    assert result.ok is False
    assert result.reason == "busy"


@pytest.mark.asyncio
async def test_force_compact_writes_extra_when_enough_messages() -> None:
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
    assert result.marker is not None
    assert result.marker["metadata"]["synthetic"] is True
    assert result.marker["metadata"]["synthetic_source"] == "compaction"
    assert cp.appended is not None
    assert len(cp.appended) == 1


@pytest.mark.asyncio
async def test_force_compact_keep_tail_zero_uses_default() -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
    cp = _FakeCp(messages=msgs)

    class _Cfg:
        def get(self, key: str, default: object = None) -> object:
            if key == "compaction.keep_tail_tokens":
                return 0
            if key == "compaction.min_compact_messages":
                return 4
            return default

    with (
        patch("cubeplex.services.conversation_compact._config", _Cfg()),
        patch(
            "cubeplex.services.conversation_compact.shared_checkpointer",
            return_value=_CpCtx(cp),
        ),
        patch(
            "cubeplex.services.conversation_compact.tail_start_by_tokens",
            return_value=8,
        ) as tail,
        patch(
            "cubeplex.services.conversation_compact.safe_boundary",
            return_value=8,
        ),
    ):
        result = await force_compact_conversation("conv-1")
    assert result.compacted is True
    # Default keep-tail (8000) applied when config returns 0
    tail.assert_called()
    assert tail.call_args.args[1] == compact_mod._DEFAULT_KEEP_TAIL


@pytest.mark.asyncio
async def test_force_compact_no_boundary() -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
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
            return_value=None,
        ),
    ):
        result = await force_compact_conversation("conv-1")
    assert result.compacted is False
    assert result.reason == "no_boundary"


@pytest.mark.asyncio
async def test_force_compact_nothing_new_when_boundary_not_advanced() -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
    extra = {"compaction_until_msg_index": 8}
    cp = _FakeCp(messages=msgs, extra=extra)
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
            return_value=8,  # <= prev_boundary
        ),
    ):
        result = await force_compact_conversation("conv-1")
    assert result.compacted is False
    assert result.reason == "no_boundary"


@pytest.mark.asyncio
async def test_force_compact_busy_before_write() -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
    cp = _FakeCp(messages=msgs)
    calls = {"n": 0}

    async def busy() -> bool:
        calls["n"] += 1
        return calls["n"] >= 2  # free on first poll, busy before save

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
        result = await force_compact_conversation("conv-1", is_busy=busy)

    assert result.ok is False
    assert result.reason == "busy"
    assert cp.saved is None


@pytest.mark.asyncio
async def test_force_compact_busy_after_save() -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
    cp = _FakeCp(messages=msgs)
    calls = {"n": 0}

    async def busy() -> bool:
        calls["n"] += 1
        # free for start + pre-save; busy on post-save poll
        return calls["n"] >= 3

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
        result = await force_compact_conversation("conv-1", is_busy=busy)

    assert result.ok is False
    assert result.reason == "busy_after_save"
    assert result.compacted is True
    assert result.marker is not None
    assert cp.saved is not None


@pytest.mark.asyncio
async def test_force_compact_aborts_when_history_changes() -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
    grown = msgs + [_user("new turn")]
    cp = _FakeCp(messages=msgs, second_messages=grown)
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

    assert result.compacted is False
    assert result.reason == "history_changed"
    assert cp.saved is None


@pytest.mark.asyncio
async def test_force_compact_history_null_on_reload() -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
    cp = _FakeCp(messages=msgs, null_on_second_load=True)
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
    assert result.reason == "history_changed"
