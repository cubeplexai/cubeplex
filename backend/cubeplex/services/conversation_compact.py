"""Manual force-compact for a conversation (slash ``/compact``).

Reuses cubepi compaction helpers (boundary + fallback summariser). The LLM
summariser path used mid-run is optional here — force-compact must succeed
without a live BoundModel when the threshold has not been hit yet.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from cubepi.middleware.compaction.boundary import safe_boundary, tail_start_by_tokens
from cubepi.middleware.compaction.state import CompactionState, message_refs
from cubepi.middleware.compaction.summarizer import build_fallback_summary
from cubepi.providers.base import Message

from cubeplex.agents.checkpointer import shared_checkpointer
from cubeplex.config import config as _config

logger = logging.getLogger(__name__)

# Match CompactionMiddleware defaults used in run_manager wiring.
_DEFAULT_KEEP_TAIL = 8_000
_DEFAULT_MIN_COMPACT = 4

BusyCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class ForceCompactResult:
    ok: bool
    compacted: bool
    reason: str | None = None
    boundary: int | None = None


def _load_state(value: Any) -> CompactionState | None:
    if value is None:
        return None
    if isinstance(value, CompactionState):
        return value
    if isinstance(value, dict):
        try:
            return CompactionState.model_validate(value)
        except Exception:  # noqa: BLE001 — corrupt extra is non-fatal
            return None
    return None


async def force_compact_conversation(
    conversation_id: str,
    *,
    is_busy: BusyCheck | None = None,
) -> ForceCompactResult:
    """Summarise older turns into checkpointer ``extra`` without a model call.

    Does **not** rewrite transcript messages. Returns ``compacted=False`` when
    there is not enough history to form a safe boundary.

    ``is_busy`` is polled around the load/save window so the route can refuse
    to write when a concurrent agent run claimed the conversation (TOCTOU
    guard against the active-run check outside this function).
    """
    keep_tail = int(_config.get("compaction.keep_tail_tokens", _DEFAULT_KEEP_TAIL))
    min_compact = int(_config.get("compaction.min_compact_messages", _DEFAULT_MIN_COMPACT))

    async def _busy() -> bool:
        return bool(is_busy is not None and await is_busy())

    if await _busy():
        return ForceCompactResult(ok=False, compacted=False, reason="busy")

    async with shared_checkpointer() as cp:
        data = await cp.load(conversation_id)
        if data is None or not data.messages:
            return ForceCompactResult(ok=True, compacted=False, reason="empty")

        messages: list[Message] = list(data.messages)
        fingerprint = message_refs(messages)
        if len(messages) < min_compact:
            return ForceCompactResult(ok=True, compacted=False, reason="too_short")

        existing = _load_state((data.extra or {}).get("compaction"))
        raw_boundary = (data.extra or {}).get("compaction_until_msg_index")
        prev_boundary = int(raw_boundary) if isinstance(raw_boundary, (int, float, str)) else 0

        if keep_tail <= 0:
            keep_tail = _DEFAULT_KEEP_TAIL
        tail_start = tail_start_by_tokens(messages, keep_tail)
        new_boundary = safe_boundary(
            messages,
            tail_start=tail_start,
            min_compact=max(min_compact, prev_boundary + 1),
        )
        if new_boundary is None or new_boundary <= prev_boundary:
            return ForceCompactResult(ok=True, compacted=False, reason="no_boundary")

        to_summarize = messages[prev_boundary:new_boundary]
        if not to_summarize:
            return ForceCompactResult(ok=True, compacted=False, reason="nothing_new")

        new_state = build_fallback_summary(
            to_summarize,
            existing=existing,
            ref_messages=to_summarize,
        )

        # Refuse to write if a run started or history advanced mid-flight.
        if await _busy():
            return ForceCompactResult(ok=False, compacted=False, reason="busy")

        fresh = await cp.load(conversation_id)
        if fresh is None or message_refs(list(fresh.messages)) != fingerprint:
            return ForceCompactResult(ok=True, compacted=False, reason="history_changed")

        extra: dict[str, Any] = dict(fresh.extra or data.extra or {})
        extra["compaction"] = new_state.model_dump(mode="json")
        extra["compaction_until_msg_index"] = new_boundary
        # Reset thrash counters so the next agent turn does not skip.
        extra["compaction_failures"] = 0
        extra["compaction_low_savings_count"] = 0
        extra["compaction_fallback_runs"] = 0
        await cp.save_extra(conversation_id, extra)

        if await _busy():
            # Run claimed during save_extra — state may still be a valid prefix
            # summary, but the client should treat this as conflict.
            logger.warning(
                "force_compact race: run became active after save conversation_id=%s",
                conversation_id,
            )
            return ForceCompactResult(ok=False, compacted=True, reason="busy_after_save")

        logger.info(
            "force_compact conversation_id=%s boundary=%s→%s",
            conversation_id,
            prev_boundary,
            new_boundary,
        )
        return ForceCompactResult(
            ok=True,
            compacted=True,
            reason=None,
            boundary=new_boundary,
        )
