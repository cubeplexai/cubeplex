"""Unit tests for force-compact service (no active agent / DB)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from cubeloop.middleware.compaction import CompactionResult
from cubeloop.middleware.compaction.state import CompactionState
from cubeloop.providers.base import TextContent, UserMessage

from cubeplex.services import conversation_compact as compact_mod
from cubeplex.services.conversation_compact import (
    _load_state,
    force_compact_conversation,
)


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


def _creds() -> dict[str, Any]:
    return {"session": object(), "org_id": "org-1", "encryption_backend": object()}


def _result(*, compacted: bool, reason: str | None, boundary: int = 8) -> CompactionResult:
    state = CompactionState(summary="llm handoff", is_fallback=False) if compacted else None
    return CompactionResult(
        compacted=compacted,
        reason=reason,
        state=state,
        boundary=boundary,
    )


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


async def _ok(*_args: Any, **_kwargs: Any) -> CompactionResult:
    return _result(compacted=True, reason=None)


@pytest.mark.asyncio
async def test_force_compact_writes_extra_when_enough_messages() -> None:
    msgs = [_user(f"message {i} " + ("x" * 200)) for i in range(12)]
    cp = _FakeCp(messages=msgs)
    with (
        patch(
            "cubeplex.services.conversation_compact.shared_checkpointer",
            return_value=_CpCtx(cp),
        ),
        patch("cubeplex.services.conversation_compact._compact_with_model", _ok),
    ):
        result = await force_compact_conversation("conv-1", **_creds())

    assert result.compacted is True
    assert result.boundary == 8
    assert cp.saved is not None
    assert cp.saved["compaction"]["summary"] == "llm handoff"
    assert cp.saved["compaction"]["is_fallback"] is False
    assert cp.saved["compaction_until_msg_index"] == 8
    assert result.marker is not None
    assert result.marker["id"]
    assert result.marker["metadata"]["synthetic"] is True
    assert result.marker["metadata"]["synthetic_source"] == "compaction"
    assert cp.appended is not None
    assert len(cp.appended) == 1
    assert cp.appended[0].id == result.marker["id"]


@pytest.mark.asyncio
async def test_force_compact_keep_tail_zero_uses_default() -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
    cp = _FakeCp(messages=msgs)
    seen: dict[str, Any] = {}

    async def _capture(*_args: Any, **kwargs: Any) -> CompactionResult:
        seen.update(kwargs)
        return _result(compacted=True, reason=None)

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
        patch("cubeplex.services.conversation_compact._compact_with_model", _capture),
    ):
        result = await force_compact_conversation("conv-1", **_creds())
    assert result.compacted is True
    assert seen["keep_tail_tokens"] == compact_mod._DEFAULT_KEEP_TAIL


@pytest.mark.asyncio
async def test_force_compact_no_boundary() -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
    cp = _FakeCp(messages=msgs)

    async def _none(*_args: Any, **_kwargs: Any) -> CompactionResult:
        return _result(compacted=False, reason="no_boundary")

    with (
        patch(
            "cubeplex.services.conversation_compact.shared_checkpointer",
            return_value=_CpCtx(cp),
        ),
        patch("cubeplex.services.conversation_compact._compact_with_model", _none),
    ):
        result = await force_compact_conversation("conv-1", **_creds())
    assert result.compacted is False
    assert result.reason == "no_boundary"
    assert cp.saved is None


@pytest.mark.asyncio
async def test_force_compact_below_threshold_does_not_write() -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
    cp = _FakeCp(messages=msgs, extra={"compaction_until_msg_index": 8})

    async def _under(*_args: Any, **_kwargs: Any) -> CompactionResult:
        return _result(compacted=False, reason="below_threshold", boundary=8)

    with (
        patch(
            "cubeplex.services.conversation_compact.shared_checkpointer",
            return_value=_CpCtx(cp),
        ),
        patch("cubeplex.services.conversation_compact._compact_with_model", _under),
    ):
        result = await force_compact_conversation("conv-1", **_creds())
    assert result.compacted is False
    assert result.reason == "below_threshold"
    assert cp.saved is None


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
        patch("cubeplex.services.conversation_compact._compact_with_model", _ok),
    ):
        result = await force_compact_conversation("conv-1", is_busy=busy, **_creds())

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
        patch("cubeplex.services.conversation_compact._compact_with_model", _ok),
    ):
        result = await force_compact_conversation("conv-1", is_busy=busy, **_creds())

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
        patch("cubeplex.services.conversation_compact._compact_with_model", _ok),
    ):
        result = await force_compact_conversation("conv-1", **_creds())

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
        patch("cubeplex.services.conversation_compact._compact_with_model", _ok),
    ):
        result = await force_compact_conversation("conv-1", **_creds())
    assert result.reason == "history_changed"


@pytest.mark.asyncio
async def test_force_compact_summarizer_failure_does_not_write() -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
    cp = _FakeCp(messages=msgs)

    async def _failed(*_args: Any, **_kwargs: Any) -> CompactionResult:
        return _result(compacted=False, reason="summarizer_failed", boundary=0)

    with (
        patch(
            "cubeplex.services.conversation_compact.shared_checkpointer",
            return_value=_CpCtx(cp),
        ),
        patch("cubeplex.services.conversation_compact._compact_with_model", _failed),
    ):
        result = await force_compact_conversation("conv-1", **_creds())

    assert result.ok is False
    assert result.compacted is False
    assert result.reason == "summarizer_failed"
    assert cp.saved is None
    assert cp.appended is None


def _budget_config(budget: int | None) -> Any:
    class _Cfg:
        def get(self, key: str, default: object = None) -> object:
            if key == "compaction.max_summary_tokens":
                return budget
            if key == "compaction.keep_tail_tokens":
                return 8_000
            if key == "compaction.min_compact_messages":
                return 4
            return default

    return _Cfg()


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [None, 256])
async def test_force_compact_calls_compaction_model(budget: int | None) -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
    cp = _FakeCp(messages=msgs)
    snap = object()
    preset = object()
    model = object()
    seen: dict[str, Any] = {}

    async def _load(session: object, org_id: str, backend: object) -> object:
        assert session is not None
        assert org_id == "org-1"
        assert backend is not None
        return snap

    def _preset(loaded: object, task: str) -> object:
        assert loaded is snap
        assert task == "compaction"
        return preset

    def _build(loaded: object, chosen: object) -> object:
        assert loaded is snap
        assert chosen is preset
        return model

    async def _history(*_args: Any, **kwargs: Any) -> CompactionResult:
        seen.update(kwargs)
        return _result(compacted=True, reason=None)

    with (
        patch("cubeplex.services.conversation_compact._config", _budget_config(budget)),
        patch(
            "cubeplex.services.conversation_compact.shared_checkpointer",
            return_value=_CpCtx(cp),
        ),
        patch("cubeplex.llm.snapshot.load_llm_snapshot", _load),
        patch("cubeplex.llm.resolver.resolve_task_preset", _preset),
        patch("cubeplex.llm.builder.build_chain_model", _build),
        patch("cubeplex.services.conversation_compact.compact_history", _history),
    ):
        result = await force_compact_conversation("conv-1", **_creds())

    assert result.compacted is True
    assert seen["model"] is model
    assert seen["force"] is True
    assert seen["fallback_on_error"] is False
    assert seen["max_summary_tokens"] == budget
    assert seen["keep_tail_tokens"] == 8_000
    assert seen["min_compact_messages"] == 4
    assert seen["tool_result_compressor"].__name__ == "preserve_tool_result_for_compaction"


@pytest.mark.asyncio
async def test_force_compact_summarizer_unavailable_without_credentials() -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
    cp = _FakeCp(messages=msgs)
    with patch(
        "cubeplex.services.conversation_compact.shared_checkpointer",
        return_value=_CpCtx(cp),
    ):
        result = await force_compact_conversation("conv-1")
    assert result.ok is False
    assert result.reason == "summarizer_unavailable"
    assert cp.saved is None


@pytest.mark.asyncio
async def test_force_compact_model_build_failure_does_not_write() -> None:
    msgs = [_user(f"message {i}") for i in range(12)]
    cp = _FakeCp(messages=msgs)

    async def _boom(*_args: Any, **_kwargs: Any) -> CompactionResult:
        raise RuntimeError("preset missing")

    with (
        patch(
            "cubeplex.services.conversation_compact.shared_checkpointer",
            return_value=_CpCtx(cp),
        ),
        patch("cubeplex.services.conversation_compact._compact_with_model", _boom),
    ):
        result = await force_compact_conversation("conv-1", **_creds())

    assert result.ok is False
    assert result.reason == "summarizer_failed"
    assert cp.saved is None
    assert cp.appended is None
