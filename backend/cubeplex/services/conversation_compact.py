"""Manual force-compact for a conversation (slash ``/compact``).

Reuses cubeloop compaction helpers (boundary + the compaction-task summariser).
On success also appends a durable **timeline marker** (synthetic user message)
so the UI history shows where context was compacted — without storing the
literal ``/compact`` user command.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from cubeloop.middleware.compaction.boundary import safe_boundary, tail_start_by_tokens
from cubeloop.middleware.compaction.state import CompactionState, message_refs
from cubeloop.middleware.compaction.summarizer import summarize
from cubeloop.providers.base import Message, synthetic_user_message
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.agents.checkpointer import shared_checkpointer
from cubeplex.config import config as _config
from cubeplex.credentials.encryption import EncryptionBackend

logger = logging.getLogger(__name__)

# Match CompactionMiddleware defaults used in run_manager wiring.
_DEFAULT_KEEP_TAIL = 8_000
_DEFAULT_MIN_COMPACT = 4

# UI + history marker. Empty body so the model gets almost nothing; the
# frontend keys off synthetic_source == "compaction".
_COMPACTION_MARKER_SOURCE = "compaction"
_COMPACTION_MARKER_TEXT = ""

BusyCheck = Callable[[], Awaitable[bool]]
Summarizer = Callable[[list[Message], CompactionState | None], Awaitable[CompactionState]]


@dataclass(frozen=True)
class ForceCompactResult:
    ok: bool
    compacted: bool
    reason: str | None = None
    boundary: int | None = None
    """Wire-ready marker message dict for the client to append locally."""
    marker: dict[str, Any] | None = None


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


def _build_marker(*, boundary: int, source: str = "manual") -> Message:
    """Synthetic timeline row: not a real user command, durable in history."""
    msg = synthetic_user_message(_COMPACTION_MARKER_TEXT, source=_COMPACTION_MARKER_SOURCE)
    # Preserve synthetic flags; add product metadata for the web renderer.
    meta = dict(msg.metadata or {})
    meta["kind"] = "compaction"
    meta["compaction"] = {
        "source": source,
        "boundary": boundary,
    }
    return msg.model_copy(
        update={
            "metadata": meta,
            "timestamp": time.time(),
            # Stable id so the client append and the later history row match.
            "id": uuid.uuid4().hex,
        }
    )


def _max_summary_tokens() -> int | None:
    raw = _config.get("compaction.max_summary_tokens")
    if raw is None:
        return None
    return int(raw)


async def _compaction_summarizer(
    session: AsyncSession,
    org_id: str,
    encryption_backend: EncryptionBackend,
) -> Summarizer:
    """Same compaction-task model automatic compaction uses."""
    from cubeplex.llm.builder import build_chain_model
    from cubeplex.llm.resolver import resolve_task_preset
    from cubeplex.llm.snapshot import load_llm_snapshot

    snap = await load_llm_snapshot(session, org_id, encryption_backend)
    preset = resolve_task_preset(snap, "compaction")
    model = build_chain_model(snap, preset)
    budget = _max_summary_tokens()

    async def _run(
        messages: list[Message],
        existing: CompactionState | None,
    ) -> CompactionState:
        return await summarize(
            model=model,
            messages_to_summarize=messages,
            ref_messages=messages,
            existing=existing,
            max_summary_tokens=budget,
        )

    return _run


async def force_compact_conversation(
    conversation_id: str,
    *,
    is_busy: BusyCheck | None = None,
    source: str = "manual",
    summarizer: Summarizer | None = None,
    session: AsyncSession | None = None,
    org_id: str | None = None,
    encryption_backend: EncryptionBackend | None = None,
) -> ForceCompactResult:
    """Summarise older turns with the compaction-task model.

    Does **not** rewrite transcript messages. On success appends a synthetic
    compaction marker so history UI can show where compact happened.

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

        try:
            run = summarizer
            if run is None:
                if session is None or not org_id or encryption_backend is None:
                    return ForceCompactResult(
                        ok=False,
                        compacted=False,
                        reason="summarizer_unavailable",
                    )
                run = await _compaction_summarizer(session, org_id, encryption_backend)
            new_state = await run(to_summarize, existing)
        except Exception:
            logger.warning(
                "force_compact summarizer failed conversation_id=%s",
                conversation_id,
                exc_info=True,
            )
            return ForceCompactResult(ok=False, compacted=False, reason="summarizer_failed")

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

        marker = _build_marker(boundary=new_boundary, source=source)
        await cp.append(conversation_id, [marker])
        marker_wire = marker.model_dump(mode="json")

        if await _busy():
            # Run claimed during save/append — state may still be a valid
            # prefix summary, but the client should treat this as conflict.
            logger.warning(
                "force_compact race: run became active after save conversation_id=%s",
                conversation_id,
            )
            return ForceCompactResult(
                ok=False,
                compacted=True,
                reason="busy_after_save",
                boundary=new_boundary,
                marker=marker_wire,
            )

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
            marker=marker_wire,
        )
