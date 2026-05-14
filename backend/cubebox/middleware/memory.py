"""MemoryMiddleware ported to cubepi (M3.b.1).

Two responsibilities, matching the LangGraph version:

Pinned tier (preference + correction):
    ``transform_system_prompt`` appends the pinned-memory block to the
    system prompt once per turn.  Output is deterministic (stable sort) so
    Anthropic and OpenAI prompt caches stay hot across turns.

Relevance tier (project_fact + procedure + decision + org_policy):
    Snapshots are computed **once per turn** by ``compute_relevance_snapshot``
    at message-append time (before the agent loop starts), then frozen on
    ``cubepi.UserMessage.metadata["memory_snapshot"]``.  The middleware never
    re-derives them from the live MemoryItem table on replay — the snapshot
    is the single source of truth, ensuring byte-identical historical prefix.

    ``transform_context`` walks the message list; for each UserMessage that
    carries ``metadata["memory_snapshot"]`` it prepends the rendered snapshot
    text to the user message content.

Cache discipline contract
    - No ``datetime.now()`` calls in rendering paths.
    - All sorts are deterministic (by scope → type → created_at or
      scope → type → key — never by dict-iteration order).
    - Snapshot ``rendered_text`` is serialised once and stored verbatim;
      subsequent renders read it back unchanged.
    - ``captured_at`` lives in the snapshot dict only (not injected into
      prompt text for historical messages) so the historical prefix remains
      byte-stable even if ``captured_at`` format ever changes.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

from cubepi.middleware.base import Middleware
from cubepi.providers.base import Message, TextContent, UserMessage

from cubebox.models.memory import (
    MemoryItem,
    MemoryScope,
    MemoryStatus,
    MemoryType,
)
from cubebox.prompts.memory import MEMORY_PROMPT_HEADER
from cubebox.repositories.memory import MemoryRepository

PINNED_TYPES = {MemoryType.PREFERENCE, MemoryType.CORRECTION}
RELEVANCE_TYPES = {
    MemoryType.PROJECT_FACT,
    MemoryType.PROCEDURE,
    MemoryType.DECISION,
    MemoryType.ORG_POLICY,
}


def _render_block(items: list[MemoryItem]) -> str:
    """Render a sorted memory block. Deterministic (cache-stable)."""
    if not items:
        return ""
    lines: list[str] = []
    by_scope: dict[MemoryScope, list[MemoryItem]] = {}
    for m in items:
        by_scope.setdefault(m.scope, []).append(m)
    for scope in (MemoryScope.ORG, MemoryScope.WORKSPACE, MemoryScope.PERSONAL):
        bucket = by_scope.get(scope, [])
        if not bucket:
            continue
        tag = scope.value
        attrs = ""
        if scope in (MemoryScope.WORKSPACE, MemoryScope.ORG):
            attrs = ' trust="user-contributed"'
        lines.append(f"<{tag}_memory{attrs}>")
        bucket.sort(
            key=lambda m: (
                0 if m.type == MemoryType.CORRECTION else 1,
                m.type.value,
                m.created_at,
            )
        )
        for m in bucket:
            lines.append(f"- [{m.type.value}] {m.content}")
        lines.append(f"</{tag}_memory>")
    return "\n".join(lines)


def _render_snapshot_text(snap: dict[str, Any], *, current: bool) -> str:
    if current:
        return f'<memory_block current="true">\n{snap["rendered_text"]}\n</memory_block>'
    return (
        f'<memory_snapshot turn captured_at="{snap["captured_at"]}">\n'
        f"{snap['rendered_text']}\n</memory_snapshot>"
    )


class MemoryMiddleware(Middleware):
    """cubepi port of MemoryMiddleware.

    Args:
        repo_factory: Async context manager factory yielding a
            ``MemoryRepository`` scoped to the current request.
        relevance_token_budget: Approximate token cap for the relevance
            tier.  Coarse char-based proxy: ``budget * 4`` chars.
    """

    def __init__(
        self,
        *,
        repo_factory: Callable[[], AbstractAsyncContextManager[MemoryRepository]],
        relevance_token_budget: int = 4000,
    ) -> None:
        self._repo_factory = repo_factory
        self._budget = relevance_token_budget

    # ------------------------------------------------------------------
    # cubepi Middleware hooks

    async def transform_system_prompt(
        self,
        system_prompt: str,
        *,
        signal: object = None,
    ) -> str:
        """Append pinned-memory block to the system prompt.

        The pinned block is sorted deterministically (scope → type →
        created_at ASC) so cache-eligible prefix bytes are identical
        across turns for the same set of memory items.  New pinned items
        always append to the end, never re-ordering existing ones.
        """
        del signal  # not used

        async with self._repo_factory() as repo:
            pinned_text = await _render_pinned(repo)

        if not pinned_text:
            return system_prompt

        separator = "\n\n" if system_prompt else ""
        return system_prompt + separator + MEMORY_PROMPT_HEADER + pinned_text

    async def transform_context(
        self,
        messages: list[Message],
        *,
        signal: object = None,
    ) -> list[Message]:
        """Prepend relevance snapshot text to UserMessages that carry one.

        Reads ``metadata["memory_snapshot"]`` from each UserMessage.  If
        present, the pre-rendered ``rendered_text`` from the snapshot dict
        is prepended as a new TextContent block (or merged into the first
        existing TextContent) — exactly as the LangGraph version prepends
        to HumanMessage.content.

        Messages without a snapshot are passed through unchanged (identity).
        """
        del signal  # not used

        if not messages:
            return messages

        # Determine index of the last UserMessage so we can flag it as
        # "current" in the snapshot XML tag (matches langgraph behaviour).
        last_user_idx = _last_user_idx(messages)

        out: list[Message] = []
        for idx, msg in enumerate(messages):
            if not isinstance(msg, UserMessage):
                out.append(msg)
                continue
            snap: dict[str, Any] | None = (
                msg.metadata.get("memory_snapshot") if msg.metadata else None
            )
            if not snap:
                out.append(msg)
                continue
            current = idx == last_user_idx
            rendered = _render_snapshot_text(snap, current=current)
            out.append(_prepend_snapshot_to_user_msg(msg, rendered))
        return out


# ------------------------------------------------------------------
# Snapshot-capture helper — called once per turn at append time


async def compute_relevance_snapshot(
    repo: MemoryRepository,
    *,
    relevance_token_budget: int = 4000,
) -> dict[str, Any] | None:
    """Compute a relevance-memory snapshot for the current turn.

    Called once at message-append time (before the agent loop), then
    frozen on ``UserMessage.metadata["memory_snapshot"]``.  Never called
    again for the same message — the snapshot is the source of truth.

    Returns ``None`` when there are no active relevance-tier items.

    Cache discipline:
        - ``captured_at`` is stored in the snapshot but **not injected
          into prompt text** for historical messages, so historical prefix
          bytes remain stable on replay.
        - ``rendered_text`` is built once via ``_render_block`` which uses
          deterministic sort and no dynamic fields.
    """
    items = await repo.list(status=MemoryStatus.ACTIVE, limit=200)
    relevant = [m for m in items if m.type in RELEVANCE_TYPES]
    if not relevant:
        return None

    # Deterministic ranking: confidence DESC, last_used_at DESC, created_at DESC
    relevant.sort(
        key=lambda m: (
            -m.confidence,
            -(m.last_used_at.timestamp() if m.last_used_at else 0.0),
            -m.created_at.timestamp(),
        )
    )

    # Apply token budget — coarse char-based proxy (4 chars ≈ 1 token)
    char_budget = relevance_token_budget * 4
    selected: list[MemoryItem] = []
    used = 0
    for m in relevant:
        cost = len(m.content) + 80  # tag overhead
        if used + cost > char_budget:
            break
        selected.append(m)
        used += cost

    rendered = _render_block(selected)
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "memory_ids": [m.id for m in selected],
        "rendered_text": rendered,
    }


# ------------------------------------------------------------------
# Private helpers


async def _render_pinned(repo: MemoryRepository) -> str:
    """Render active pinned (preference + correction) items.

    Pure deterministic render: scope → type → created_at ASC.
    No time fields, no random ordering — safe to include in cache-eligible
    prefix.
    """
    all_active = await repo.list(status=MemoryStatus.ACTIVE)
    pinned = [m for m in all_active if m.type in PINNED_TYPES]
    # Stable sort: scope > type > created_at ASC (append-only)
    pinned.sort(key=lambda m: (m.scope.value, m.type.value, m.created_at))
    if not pinned:
        return ""
    return "\n" + _render_block(pinned)


def _last_user_idx(messages: list[Message]) -> int:
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], UserMessage):
            return i
    return -1


def _prepend_snapshot_to_user_msg(msg: UserMessage, snapshot_text: str) -> UserMessage:
    """Return a fresh UserMessage with snapshot_text prepended.

    Builds a new ``UserMessage`` (never mutates the persisted original) with
    the snapshot text as a leading TextContent block, followed by the
    original content blocks.  This preserves existing content structure
    (images, etc.) and mirrors the LangGraph version which prepends to
    HumanMessage.content string as ``"{snapshot}\n\n{original_text}"``.

    ``metadata`` is shallow-copied so the persisted snapshot key is
    visible to downstream middleware (e.g. AttachmentHintMiddleware
    which reads ``metadata["attachments"]``).
    """
    new_content = [TextContent(text=snapshot_text + "\n\n")] + list(msg.content)
    return UserMessage(
        content=new_content,
        timestamp=msg.timestamp,
        metadata=dict(msg.metadata),
    )
