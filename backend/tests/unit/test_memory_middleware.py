"""MemoryMiddleware unit tests (M3.b.1).

Covers:
- Cache-discipline byte-stability: snapshot rendered_text and pinned render
  are deterministic across calls.
- transform_system_prompt: appends pinned memory; pass-through when empty.
- transform_context: prepends rendered snapshot; pass-through without snapshot.
- wire_input_to_cubepi_user_message: memory_snapshot kwarg stored in metadata.
- Multi-turn: snapshots on different user messages render independently.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from cubepi.providers.base import AssistantMessage, TextContent, UserMessage

from cubeplex.agents.convert import wire_input_to_cubepi_user_message
from cubeplex.middleware.memory import (
    MemoryMiddleware,
    _prepend_snapshot_to_user_msg,
    _render_block,
    _render_pinned,
    _render_snapshot_text,
    compute_relevance_snapshot,
)
from cubeplex.models.memory import MemoryItem, MemoryScope, MemoryStatus, MemoryType

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _mk_item(
    *,
    scope: MemoryScope = MemoryScope.PERSONAL,
    type_: MemoryType = MemoryType.PREFERENCE,
    content: str = "test content",
    confidence: float = 0.8,
    created_at: datetime | None = None,
    last_used_at: datetime | None = None,
) -> MemoryItem:
    """Build a MemoryItem without hitting the DB."""
    return MemoryItem(
        scope=scope,
        type=type_,
        content=content,
        confidence=confidence,
        status=MemoryStatus.ACTIVE,
        created_by_user_id="usr-test",
        owner_user_id="usr-test" if scope == MemoryScope.PERSONAL else None,
        workspace_id="ws-1" if scope == MemoryScope.WORKSPACE else None,
        org_id=None if scope == MemoryScope.PERSONAL else "org-1",
        created_at=created_at or datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        last_used_at=last_used_at,
    )


class _FakeRepo:
    """Minimal MemoryRepository surface for unit tests."""

    def __init__(self, items: list[MemoryItem] | None = None) -> None:
        self._items: list[MemoryItem] = items or []

    async def list(self, **_kwargs: Any) -> list[MemoryItem]:
        return list(self._items)


def _make_middleware(items: list[MemoryItem] | None = None) -> MemoryMiddleware:
    """Build a MemoryMiddleware backed by a fake repo."""
    repo = _FakeRepo(items)

    @asynccontextmanager
    async def _factory():  # type: ignore[return]
        yield repo

    return MemoryMiddleware(repo_factory=_factory)


def _user_msg(text: str, snapshot: dict[str, Any] | None = None) -> UserMessage:
    metadata: dict[str, Any] = {}
    if snapshot is not None:
        metadata["memory_snapshot"] = snapshot
    return UserMessage(content=[TextContent(text=text)], metadata=metadata)


def _assistant_msg(text: str = "ok") -> AssistantMessage:
    from cubepi.providers.base import Usage

    return AssistantMessage(
        content=[TextContent(text=text)],
        usage=Usage(input_tokens=1, output_tokens=1),
    )


# ---------------------------------------------------------------------------
# Cache-discipline: rendering is byte-stable
# ---------------------------------------------------------------------------


def test_render_snapshot_text_is_byte_stable_for_historical() -> None:
    """_render_snapshot_text with same dict → same bytes, twice."""
    snap = {
        "captured_at": "2026-05-01T12:00:00+00:00",
        "memory_ids": ["mem-1", "mem-2"],
        "rendered_text": "<workspace_memory>\n- [procedure] run make check\n</workspace_memory>",
    }
    a = _render_snapshot_text(snap, current=False)
    b = _render_snapshot_text(snap, current=False)
    assert a == b
    assert snap["rendered_text"] in a


def test_render_snapshot_text_is_byte_stable_for_current() -> None:
    snap = {
        "captured_at": "2026-05-01T12:00:00+00:00",
        "memory_ids": [],
        "rendered_text": "<personal_memory>\n- [preference] Reply concisely.\n</personal_memory>",
    }
    a = _render_snapshot_text(snap, current=True)
    b = _render_snapshot_text(snap, current=True)
    assert a == b


def test_render_block_is_byte_stable() -> None:
    """_render_block with the same MemoryItem list produces identical bytes."""
    items = [
        _mk_item(
            scope=MemoryScope.WORKSPACE,
            type_=MemoryType.PROCEDURE,
            content="run make check",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _mk_item(
            scope=MemoryScope.PERSONAL,
            type_=MemoryType.PREFERENCE,
            content="Reply concisely.",
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    ]
    a = _render_block(items)
    b = _render_block(items)
    assert a == b


def test_render_pinned_is_byte_stable_across_calls() -> None:
    """_render_pinned with the same repo returns identical bytes across calls."""
    items = [
        _mk_item(
            scope=MemoryScope.PERSONAL,
            type_=MemoryType.PREFERENCE,
            content="Reply concisely.",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _mk_item(
            scope=MemoryScope.WORKSPACE,
            type_=MemoryType.CORRECTION,
            content="Use TDD.",
            created_at=datetime(2026, 2, 1, tzinfo=UTC),
        ),
    ]
    repo = _FakeRepo(items)
    a = asyncio.run(_render_pinned(repo))  # type: ignore[arg-type]
    b = asyncio.run(_render_pinned(repo))  # type: ignore[arg-type]
    assert a == b
    assert a  # not empty


def test_render_pinned_order_is_deterministic_regardless_of_input_order() -> None:
    """Same items in reversed input order produce identical render output."""
    items = [
        _mk_item(
            scope=MemoryScope.PERSONAL,
            type_=MemoryType.PREFERENCE,
            content="A",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _mk_item(
            scope=MemoryScope.PERSONAL,
            type_=MemoryType.PREFERENCE,
            content="B",
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    ]
    repo_fwd = _FakeRepo(items)
    repo_rev = _FakeRepo(list(reversed(items)))
    a = asyncio.run(_render_pinned(repo_fwd))  # type: ignore[arg-type]
    b = asyncio.run(_render_pinned(repo_rev))  # type: ignore[arg-type]
    assert a == b


# ---------------------------------------------------------------------------
# transform_system_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_system_prompt_appends_pinned_memory() -> None:
    """Pinned items are appended to the system prompt."""
    items = [
        _mk_item(type_=MemoryType.PREFERENCE, content="Reply concisely."),
    ]
    mw = _make_middleware(items)
    result = await mw.transform_system_prompt("You are a helpful assistant.", ctx=object())
    assert "Reply concisely." in result
    # Header injected
    assert "Memory" in result
    # Original system prompt preserved at front
    assert result.startswith("You are a helpful assistant.")


@pytest.mark.asyncio
async def test_transform_system_prompt_appends_authoring_when_no_pinned_items() -> None:
    """No pinned items → no pinned block, but the authoring block is always appended."""
    # Repo has only relevance-tier items → pinned filter produces empty list
    items = [
        _mk_item(type_=MemoryType.PROJECT_FACT, content="Backend runs on FastAPI."),
    ]
    mw = _make_middleware(items)
    original = "You are a helpful assistant."
    result = await mw.transform_system_prompt(original, ctx=object())
    assert result.startswith(original)
    assert "memory_save" in result  # authoring block always injected
    assert "Backend runs on FastAPI." not in result  # not pinned


@pytest.mark.asyncio
async def test_transform_system_prompt_appends_authoring_when_repo_empty() -> None:
    """Empty repo → original prompt + authoring block (no pinned header)."""
    mw = _make_middleware([])
    original = "System prompt."
    result = await mw.transform_system_prompt(original, ctx=object())
    assert result.startswith(original)
    assert "memory_save" in result


@pytest.mark.asyncio
async def test_transform_system_prompt_authoring_only_with_empty_prompt() -> None:
    """Empty system prompt → just the authoring block."""
    from cubeplex.prompts.memory import MEMORY_AUTHORING_BLOCK

    mw = _make_middleware([])
    result = await mw.transform_system_prompt("", ctx=object())
    assert result == MEMORY_AUTHORING_BLOCK


@pytest.mark.asyncio
async def test_transform_system_prompt_correction_before_preference_within_scope() -> None:
    """Within a scope, correction items sort before preference items."""
    items = [
        _mk_item(
            type_=MemoryType.PREFERENCE,
            content="PREF",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _mk_item(
            type_=MemoryType.CORRECTION,
            content="CORR",
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    ]
    mw = _make_middleware(items)
    result = await mw.transform_system_prompt("", ctx=object())
    corr_pos = result.index("CORR")
    pref_pos = result.index("PREF")
    assert corr_pos < pref_pos


# ---------------------------------------------------------------------------
# transform_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_context_prepends_snapshot_to_user_msg() -> None:
    """UserMessage with memory_snapshot gets snapshot text prepended."""
    snap = {
        "captured_at": "2026-05-01T12:00:00+00:00",
        "memory_ids": ["mem-1"],
        "rendered_text": "<workspace_memory>\n- [procedure] run make check\n</workspace_memory>",
    }
    mw = _make_middleware()
    msg = _user_msg("What should I do next?", snapshot=snap)
    result = await mw.transform_context([msg], ctx=object())

    assert len(result) == 1
    rendered = result[0]
    assert isinstance(rendered, UserMessage)
    # Snapshot text is prepended
    full_text = "".join(c.text for c in rendered.content if isinstance(c, TextContent))
    assert snap["rendered_text"] in full_text
    assert "What should I do next?" in full_text
    # Snapshot appears before the user text
    assert full_text.index(snap["rendered_text"]) < full_text.index("What should I do next?")


@pytest.mark.asyncio
async def test_transform_context_passthrough_when_no_snapshot() -> None:
    """UserMessage without snapshot metadata is returned as-is (identity)."""
    mw = _make_middleware()
    msg = _user_msg("No snapshot here.")
    result = await mw.transform_context([msg], ctx=object())
    assert len(result) == 1
    assert result[0] is msg


@pytest.mark.asyncio
async def test_transform_context_passthrough_for_assistant_messages() -> None:
    """Non-UserMessage objects are always passed through unchanged."""
    mw = _make_middleware()
    asst = _assistant_msg("I'll help you.")
    result = await mw.transform_context([asst], ctx=object())
    assert len(result) == 1
    assert result[0] is asst


@pytest.mark.asyncio
async def test_transform_context_empty_list() -> None:
    mw = _make_middleware()
    result = await mw.transform_context([], ctx=object())
    assert result == []


@pytest.mark.asyncio
async def test_transform_context_last_user_msg_flagged_current() -> None:
    """The last UserMessage is rendered with current=True XML tag."""
    snap_old = {
        "captured_at": "2026-05-01T10:00:00+00:00",
        "memory_ids": [],
        "rendered_text": "<personal_memory>old</personal_memory>",
    }
    snap_new = {
        "captured_at": "2026-05-01T11:00:00+00:00",
        "memory_ids": [],
        "rendered_text": "<personal_memory>new</personal_memory>",
    }
    mw = _make_middleware()
    msgs = [
        _user_msg("first turn", snapshot=snap_old),
        _assistant_msg(),
        _user_msg("second turn", snapshot=snap_new),
    ]
    result = await mw.transform_context(msgs, ctx=object())

    assert len(result) == 3
    # First user msg → historical tag
    first_text = "".join(c.text for c in result[0].content if isinstance(c, TextContent))
    assert "memory_snapshot turn captured_at=" in first_text

    # Last user msg → current tag
    last_text = "".join(c.text for c in result[2].content if isinstance(c, TextContent))
    assert 'memory_block current="true"' in last_text


# ---------------------------------------------------------------------------
# Multi-turn: snapshots on different messages render independently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_context_multi_turn_independent_snapshots() -> None:
    """Each UserMessage renders only its own snapshot, not another's."""
    snap_a = {
        "captured_at": "2026-05-01T10:00:00+00:00",
        "memory_ids": ["mem-1"],
        "rendered_text": "<personal_memory>turn-A</personal_memory>",
    }
    snap_b = {
        "captured_at": "2026-05-01T11:00:00+00:00",
        "memory_ids": ["mem-2"],
        "rendered_text": "<personal_memory>turn-B</personal_memory>",
    }
    mw = _make_middleware()
    msgs = [
        _user_msg("turn A", snapshot=snap_a),
        _assistant_msg("reply A"),
        _user_msg("turn B", snapshot=snap_b),
    ]
    result = await mw.transform_context(msgs, ctx=object())

    assert len(result) == 3

    text_a = "".join(c.text for c in result[0].content if isinstance(c, TextContent))
    text_b = "".join(c.text for c in result[2].content if isinstance(c, TextContent))

    assert "turn-A" in text_a
    assert "turn-B" not in text_a
    assert "turn-B" in text_b
    assert "turn-A" not in text_b


@pytest.mark.asyncio
async def test_transform_context_mixed_snapshot_and_no_snapshot() -> None:
    """Only messages with snapshots are augmented; others pass through."""
    snap = {
        "captured_at": "2026-05-01T12:00:00+00:00",
        "memory_ids": [],
        "rendered_text": "<personal_memory>fact</personal_memory>",
    }
    mw = _make_middleware()
    msg_with = _user_msg("has snapshot", snapshot=snap)
    msg_without = _user_msg("no snapshot")
    msgs = [msg_with, _assistant_msg(), msg_without]
    result = await mw.transform_context(msgs, ctx=object())

    assert len(result) == 3
    # msg_with was augmented
    text_with = "".join(c.text for c in result[0].content if isinstance(c, TextContent))
    assert "fact" in text_with
    # msg_without was not modified (identity)
    assert result[2] is msg_without


# ---------------------------------------------------------------------------
# compute_relevance_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_relevance_snapshot_returns_none_when_no_items() -> None:
    repo = _FakeRepo([])
    result = await compute_relevance_snapshot(repo)  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_compute_relevance_snapshot_returns_none_when_only_pinned() -> None:
    repo = _FakeRepo([_mk_item(type_=MemoryType.PREFERENCE, content="pref")])
    result = await compute_relevance_snapshot(repo)  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_compute_relevance_snapshot_returns_dict_with_expected_keys() -> None:
    repo = _FakeRepo([_mk_item(type_=MemoryType.PROJECT_FACT, content="Backend runs on FastAPI.")])
    result = await compute_relevance_snapshot(repo)  # type: ignore[arg-type]
    assert result is not None
    assert "captured_at" in result
    assert "memory_ids" in result
    assert "rendered_text" in result
    assert "Backend runs on FastAPI." in result["rendered_text"]


@pytest.mark.asyncio
async def test_compute_relevance_snapshot_rendered_text_is_stable() -> None:
    """render_text field is byte-stable when repo returns the same items."""
    items = [
        _mk_item(
            type_=MemoryType.PROJECT_FACT,
            content="Fact A.",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _mk_item(
            type_=MemoryType.PROCEDURE,
            content="Procedure B.",
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    ]
    repo = _FakeRepo(items)
    snap_a = await compute_relevance_snapshot(repo)  # type: ignore[arg-type]
    snap_b = await compute_relevance_snapshot(repo)  # type: ignore[arg-type]
    assert snap_a is not None and snap_b is not None
    # rendered_text must be byte-identical (cache stability)
    assert snap_a["rendered_text"] == snap_b["rendered_text"]
    # memory_ids must be identical
    assert snap_a["memory_ids"] == snap_b["memory_ids"]


# ---------------------------------------------------------------------------
# wire_input_to_cubepi_user_message: memory_snapshot kwarg
# ---------------------------------------------------------------------------


def test_wire_input_stores_memory_snapshot_in_metadata() -> None:
    snap = {
        "captured_at": "2026-05-01T12:00:00+00:00",
        "memory_ids": ["mem-1"],
        "rendered_text": "<personal_memory>fact</personal_memory>",
    }
    msg = wire_input_to_cubepi_user_message("hello", memory_snapshot=snap)
    assert msg.metadata.get("memory_snapshot") == snap


def test_wire_input_no_snapshot_key_when_none() -> None:
    msg = wire_input_to_cubepi_user_message("hello")
    assert "memory_snapshot" not in msg.metadata


def test_wire_input_snapshot_and_attachments_coexist() -> None:
    snap = {"captured_at": "t", "memory_ids": [], "rendered_text": "x"}
    msg = wire_input_to_cubepi_user_message(
        "hello",
        attachments=[{"path": "/tmp/a.txt", "kind": "text"}],
        memory_snapshot=snap,
    )
    assert "attachments" in msg.metadata
    assert "memory_snapshot" in msg.metadata
    assert msg.metadata["memory_snapshot"] == snap


# ---------------------------------------------------------------------------
# _prepend_snapshot_to_user_msg: metadata preserved
# ---------------------------------------------------------------------------


def test_prepend_snapshot_preserves_metadata() -> None:
    """Metadata (including non-snapshot keys) is preserved after prepend."""
    snap = {
        "captured_at": "t",
        "memory_ids": [],
        "rendered_text": "<personal_memory>x</personal_memory>",
    }
    msg = UserMessage(
        content=[TextContent(text="original text")],
        metadata={"attachments": [{"path": "/tmp/a.txt"}], "memory_snapshot": snap},
    )
    rendered_text = _render_snapshot_text(snap, current=True)
    new_msg = _prepend_snapshot_to_user_msg(msg, rendered_text)

    assert new_msg.metadata.get("attachments") == [{"path": "/tmp/a.txt"}]
    assert "memory_snapshot" in new_msg.metadata
    assert new_msg is not msg  # fresh object, not mutated original


def test_prepend_snapshot_original_text_present() -> None:
    msg = UserMessage(content=[TextContent(text="user question")], metadata={})
    rendered = "snap-block"
    new_msg = _prepend_snapshot_to_user_msg(msg, rendered)

    full_text = "".join(c.text for c in new_msg.content if isinstance(c, TextContent))
    assert "snap-block" in full_text
    assert "user question" in full_text
