"""Cache stability invariants for the memory system.

Why this exists: Anthropic's prompt cache and OpenAI's auto-cache only
hit when the prefix bytes of the request are identical across calls.
The whole point of the memory snapshot channel is to give us a stable
historical prefix. If a future change ever sneaks dynamic content
(timestamps, random ids, dict-key reordering) into the rendered output,
the cache breaks silently and bills go up — without breaking any
behavioral test.

These tests pin two things without needing a real LLM:

1. The reducer on `memory_snapshots` refuses to overwrite an existing
   key with a different value. This is the cache-correctness contract:
   once we've sent a snapshot for message `mid`, the next turn's render
   for `mid` must produce the same bytes (so the cache hits).

2. Rendering the same memory + same message twice produces byte-identical
   output. Sorting is deterministic, no time-based fields leak in.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from cubebox.agents.state import _merge_snapshots
from cubebox.middleware.memory import MemoryMiddleware
from cubebox.models.memory import (
    MemoryItem,
    MemoryScope,
    MemoryStatus,
    MemoryType,
)

# ---- reducer contract -------------------------------------------------


def test_reducer_accepts_first_write_for_a_key() -> None:
    snap = {"captured_at": "t1", "memory_ids": ["mem-1"], "rendered_text": "x"}
    out = _merge_snapshots(None, {"msg-1": snap})
    assert out == {"msg-1": snap}


def test_reducer_idempotent_for_same_value() -> None:
    snap = {"captured_at": "t1", "memory_ids": ["mem-1"], "rendered_text": "x"}
    out = _merge_snapshots({"msg-1": snap}, {"msg-1": snap})
    assert out == {"msg-1": snap}


def test_reducer_rejects_overwrite_with_different_value() -> None:
    snap_a = {"captured_at": "t1", "memory_ids": ["mem-1"], "rendered_text": "old"}
    snap_b = {"captured_at": "t2", "memory_ids": ["mem-1"], "rendered_text": "new"}
    with pytest.raises(ValueError, match="already exists"):
        _merge_snapshots({"msg-1": snap_a}, {"msg-1": snap_b})


# ---- rendering byte-stability ---------------------------------------


def _mk_memory(
    *,
    scope: MemoryScope,
    type_: MemoryType,
    content: str,
    confidence: float = 0.8,
    created_at: datetime | None = None,
) -> MemoryItem:
    """Build a MemoryItem instance without touching the DB."""
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
        created_at=created_at or datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
    )


class _FakeRepo:
    """Stand-in for MemoryRepository — returns a fixed list."""

    def __init__(self, items: list[MemoryItem]) -> None:
        self._items = items

    async def list(self, **_kwargs: Any) -> list[MemoryItem]:
        return list(self._items)


def test_pinned_render_byte_stable_across_calls() -> None:
    """Two _render_pinned calls with identical state produce identical bytes."""
    items = [
        _mk_memory(
            scope=MemoryScope.PERSONAL,
            type_=MemoryType.PREFERENCE,
            content="Reply concisely.",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _mk_memory(
            scope=MemoryScope.WORKSPACE,
            type_=MemoryType.CORRECTION,
            content="Use TDD; never skip tests.",
            created_at=datetime(2026, 2, 1, tzinfo=UTC),
        ),
    ]
    mw = MemoryMiddleware(repo_factory=lambda: None)  # type: ignore[arg-type]

    import asyncio

    async def _render() -> str:
        return await mw._render_pinned(_FakeRepo(items))  # type: ignore[arg-type]

    a = asyncio.run(_render())
    b = asyncio.run(_render())
    assert a == b
    assert a  # not empty


def test_snapshot_replay_is_byte_identical() -> None:
    """Same snapshot dict + same message → same rendered output, twice in a row.

    This is what protects historical-prefix cache reuse. The snapshot is the
    persisted bytes; replaying must re-emit those bytes verbatim.
    """
    mw = MemoryMiddleware(repo_factory=lambda: None)  # type: ignore[arg-type]
    msg = HumanMessage(content="What's next?", id="msg-old")
    snapshot = {
        "captured_at": "2026-05-01T12:00:00+00:00",
        "memory_ids": ["mem-1"],
        "rendered_text": "<workspace_memory>\n- [procedure] Run E2E with pnpm.\n</workspace_memory>",
    }
    snapshots = {"msg-old": snapshot}

    a = mw._render_messages_with_snapshots([msg], snapshots)
    b = mw._render_messages_with_snapshots([msg], snapshots)

    assert len(a) == 1 and len(b) == 1
    assert a[0].content == b[0].content
    # Snapshot text is included verbatim, not regenerated
    assert snapshot["rendered_text"] in a[0].content


def test_pinned_order_is_deterministic() -> None:
    """Same pinned items in different input order render identically.

    Cache keys depend on byte order. If pinned sort is unstable, two
    requests with the same memory in different repo-return order would
    produce different prompts and miss cache.
    """
    items_a = [
        _mk_memory(
            scope=MemoryScope.PERSONAL,
            type_=MemoryType.PREFERENCE,
            content="A",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        _mk_memory(
            scope=MemoryScope.PERSONAL,
            type_=MemoryType.PREFERENCE,
            content="B",
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    ]
    items_b = list(reversed(items_a))

    mw = MemoryMiddleware(repo_factory=lambda: None)  # type: ignore[arg-type]

    import asyncio

    out_a = asyncio.run(mw._render_pinned(_FakeRepo(items_a)))  # type: ignore[arg-type]
    out_b = asyncio.run(mw._render_pinned(_FakeRepo(items_b)))  # type: ignore[arg-type]
    assert out_a == out_b
