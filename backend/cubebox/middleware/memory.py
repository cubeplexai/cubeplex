"""MemoryMiddleware — injects pinned + relevance memory and writes snapshots.

Layout responsibilities:

- Pinned tier (preference + correction): rendered into the system prompt's
  cache-eligible region. Sorted by created_at ASC so additions append.
- Relevance tier (project_fact + procedure + decision + org_policy):
  retrieved per turn against the current user message, captured as an
  immutable MemorySnapshot in state.memory_snapshots, and rendered as a
  prefix on the current user message.
- Historical snapshots are read from state and rendered byte-identical
  alongside their corresponding past user messages.

The middleware is provider-agnostic. cache_control markers are inserted
later by the LLM adapter (cubebox/llm/cache_markers.py).

State-flow design notes:
- `abefore_model` computes the relevance snapshot for the latest user
  message and returns `{"memory_snapshots": {mid: snap}}` so LangGraph
  persists it via the CubeboxState reducer.
- `awrap_model_call` reads the (now-persisted) snapshots from
  `request.state` and renders them. Because LangGraph applies the
  `before_model` state update before invoking the model, the snapshot
  written above is visible here.
"""

from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from cubebox.agents.state import CubeboxState
from cubebox.middleware._utils import append_to_system_message
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


class MemoryMiddleware(AgentMiddleware[CubeboxState, Any]):
    """Reads memory, injects pinned into system prompt, captures per-turn
    relevance snapshots, and replays historical snapshots."""

    tools: Sequence[BaseTool] = []
    state_schema = CubeboxState

    def __init__(
        self,
        *,
        repo_factory: Callable[[], AbstractAsyncContextManager[MemoryRepository]],
        relevance_token_budget: int = 4000,
    ) -> None:
        # repo_factory yields a request-scoped repo via async context manager
        # so the underlying AsyncSession is always closed after each call —
        # leaving sessions to GC produces SAWarnings and can exhaust the
        # pool under sustained chat traffic.
        self._repo_factory = repo_factory
        self._budget = relevance_token_budget

    async def abefore_model(
        self,
        state: CubeboxState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        """Capture a relevance snapshot for the latest user message (once)."""
        messages = state.get("messages", []) or []
        snapshots: dict[str, dict[str, Any]] = state.get("memory_snapshots", {}) or {}

        idx = self._last_human_idx(messages)
        if idx < 0:
            return None
        msg = messages[idx]
        if not isinstance(msg, HumanMessage):
            return None
        mid = self._stable_msg_id(msg, idx)
        if mid in snapshots:
            return None  # replay/resume — keep existing snapshot

        async with self._repo_factory() as repo:
            snap = await self._capture_current_snapshot(repo, msg)
        if snap is None:
            return None
        return {"memory_snapshots": {mid: snap}}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        async with self._repo_factory() as repo:
            pinned_text = await self._render_pinned(repo)

        new_system: SystemMessage | None
        if pinned_text:
            new_system = append_to_system_message(
                request.system_message, MEMORY_PROMPT_HEADER + pinned_text
            )
        else:
            new_system = request.system_message

        # Render messages with snapshots from state (set by abefore_model)
        raw_state = request.state.get("memory_snapshots", {}) if request.state else {}
        snapshots: dict[str, dict[str, Any]] = raw_state if isinstance(raw_state, dict) else {}
        new_messages = self._render_messages_with_snapshots(list(request.messages), snapshots)

        new_request = request.override(system_message=new_system, messages=new_messages)
        return await handler(new_request)

    # ------------------------------------------------------------------
    # internals

    async def _render_pinned(self, repo: MemoryRepository) -> str:
        all_active = await repo.list(status=MemoryStatus.ACTIVE)
        pinned = [m for m in all_active if m.type in PINNED_TYPES]
        # Stable sort: scope > type > created_at ASC (append-only)
        pinned.sort(key=lambda m: (m.scope.value, m.type.value, m.created_at))
        if not pinned:
            return ""
        return "\n" + _render_block(pinned)

    def _render_messages_with_snapshots(
        self,
        messages: Sequence[BaseMessage],
        snapshots: dict[str, dict[str, Any]],
    ) -> list[Any]:
        if not snapshots:
            return list(messages)
        current_idx = self._last_human_idx(messages)
        out: list[Any] = []
        for idx, msg in enumerate(messages):
            if not isinstance(msg, HumanMessage):
                out.append(msg)
                continue
            mid = self._stable_msg_id(msg, idx)
            snap = snapshots.get(mid)
            if not snap:
                out.append(msg)
                continue
            current = idx == current_idx
            rendered = _render_snapshot_text(snap, current=current)
            # Preserve additional_kwargs/response_metadata/name — downstream
            # middleware (e.g. AttachmentHintMiddleware) reads
            # additional_kwargs.attachments_meta to render file paths into the
            # LLM prompt. Dropping these here strips attachments whenever a
            # memory snapshot is injected.
            out.append(
                HumanMessage(
                    content=f"{rendered}\n\n{_msg_text(msg)}",
                    id=msg.id,
                    additional_kwargs=msg.additional_kwargs,
                    response_metadata=msg.response_metadata,
                    name=msg.name,
                )
            )
        return out

    @staticmethod
    def _stable_msg_id(msg: BaseMessage, idx: int) -> str:
        """Fallback to positional id if msg.id is missing.

        LangGraph normally assigns ids; this fallback only matters in tests
        where messages are constructed without ids.
        """
        return msg.id or f"msg-{idx}"

    @staticmethod
    def _last_human_idx(messages: Sequence[BaseMessage]) -> int:
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                return i
        return -1

    async def _capture_current_snapshot(
        self, repo: MemoryRepository, user_msg: HumanMessage
    ) -> dict[str, Any] | None:
        # v1 has no embeddings; substring-matching the entire user prompt
        # against memory.content almost never hits ("How do I deploy?" never
        # appears verbatim in "The deploy script lives at scripts/deploy.sh").
        # List all active relevance items and rely on the deterministic ranking
        # + token budget below to pick what fits. Embedding-based retrieval
        # lands in a follow-up.
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
        char_budget = self._budget * 4
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


def _msg_text(msg: BaseMessage) -> str:
    if isinstance(msg.content, str):
        return msg.content
    if isinstance(msg.content, list):
        parts: list[str] = []
        for block in msg.content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts)
    return str(msg.content)


def _render_block(items: list[MemoryItem]) -> str:
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
        # corrections first within scope, then by type, then created_at
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
