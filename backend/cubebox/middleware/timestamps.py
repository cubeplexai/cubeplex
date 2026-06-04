"""TimestampMiddleware — cubepi port of TimestampMiddleware (M3.d.2).

Stamps timing data across the full hook surface:

- ``transform_context``: records turn-start wall time on a ContextVar so
  downstream hooks (``after_model_response``) can compute turn duration
  without touching message content.
- ``before_tool_call``: records per-tool-call start time in an instance
  dict keyed by ``tool_call.id``.
- ``after_tool_call``: writes ``tool_started_at`` + ``tool_ended_at`` into
  ``AfterToolCallResult.details``, merged with any existing details from
  the tool execution.
- ``after_model_response``: writes ``created_at`` and ``turn_started_at``
  into ``response.metadata``; also records ``reasoning_duration_ms`` when
  already present in metadata (left untouched — set by the LLM adapter).

Cache-discipline contract
-------------------------
**Timestamps NEVER appear in prompt text, system prompt, or message
content.**  They land exclusively in:

- ``UserMessage.metadata`` (cubepi side channel, not forwarded to the LLM)
- ``AssistantMessage.metadata`` (out-of-band, never converted to LLM payload)
- ``AfterToolCallResult.details`` (tool result details dict, not in content)

These are all out-of-band fields that the checkpointer stores but the
LLM never sees as prompt tokens, which keeps the cache-eligible prefix
free of per-turn dynamic content.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from cubepi.agent.types import AfterToolCallContext, AfterToolCallResult, BeforeToolCallContext
from cubepi.middleware.base import Middleware
from cubepi.providers.base import AssistantMessage

from cubebox.utils.time import utc_isoformat

# ---------------------------------------------------------------------------
# ContextVar: carries turn-start time from transform_context to
# after_model_response without touching message content.
# ---------------------------------------------------------------------------

_turn_started_at: ContextVar[str | None] = ContextVar("_turn_started_at", default=None)


class TimestampMiddleware(Middleware):
    """Stamps timing metadata on messages — never in prompt text.

    All timestamps land in out-of-band metadata fields (message.metadata,
    response.metadata, tool result details), preserving prompt-cache
    stability.
    """

    def __init__(self) -> None:
        # Per-tool-call start times, keyed by tool_call.id.
        # Populated by before_tool_call; consumed and removed by after_tool_call.
        self._tool_started_at: dict[str, str] = {}

    # ------------------------------------------------------------------
    # transform_context — records turn-start time
    # ------------------------------------------------------------------

    async def transform_context(
        self,
        messages: list[Any],
        *,
        ctx: Any,
        signal: Any = None,
    ) -> list[Any]:
        """Capture turn-start wall time on a ContextVar.

        Message content is returned byte-identical — no modifications — so
        the stable prefix (historical messages) stays cache-eligible.
        """
        del ctx, signal  # not used
        _turn_started_at.set(utc_isoformat(datetime.now(UTC)))
        return messages

    # ------------------------------------------------------------------
    # before_tool_call — records per-call start time
    # ------------------------------------------------------------------

    async def before_tool_call(
        self,
        ctx: BeforeToolCallContext,
        *,
        signal: Any = None,
    ) -> None:
        """Stash the tool-call start time; returns None (no blocking)."""
        del signal  # not used
        self._tool_started_at[ctx.tool_call.id] = utc_isoformat(datetime.now(UTC))
        return None

    # ------------------------------------------------------------------
    # after_tool_call — writes timing into result details
    # ------------------------------------------------------------------

    async def after_tool_call(
        self,
        ctx: AfterToolCallContext,
        *,
        signal: Any = None,
    ) -> AfterToolCallResult | None:
        """Merge tool timing into AfterToolCallResult.details.

        Retrieves the stashed tool_started_at from before_tool_call (keyed
        by tool_call.id) and records tool_ended_at.  The timing dict is
        merged with any existing ``ctx.result.details`` so other middleware
        contributions are preserved.

        Returns ``None`` if no start time is found (defensive fallback).
        """
        del signal  # not used

        tool_id = ctx.tool_call.id
        started_at = self._tool_started_at.pop(tool_id, None)
        if started_at is None:
            return None

        ended_at = utc_isoformat(datetime.now(UTC))

        timing: dict[str, str] = {
            "tool_started_at": started_at,
            "tool_ended_at": ended_at,
        }

        existing_details = ctx.result.details
        if isinstance(existing_details, dict):
            merged: dict[str, Any] = {**existing_details, **timing}
        elif existing_details is not None:
            # Preserve non-dict details under a key and add timing alongside
            merged = {"_details": existing_details, **timing}
        else:
            merged = timing

        return AfterToolCallResult(details=merged)

    # ------------------------------------------------------------------
    # after_model_response — writes turn timing to response.metadata
    # ------------------------------------------------------------------

    async def after_model_response(
        self,
        response: AssistantMessage,
        ctx: Any,
        *,
        signal: Any = None,
    ) -> None:
        """Stamp created_at and turn_started_at on response.metadata.

        ``response.metadata`` is an out-of-band dict; it is never converted
        to LLM-visible tokens.  Existing keys (e.g. ``reasoning_duration_ms``
        set by the LLM adapter) are left untouched via setdefault.
        """
        del ctx, signal  # not used

        ts = utc_isoformat(datetime.now(UTC))
        response.metadata.setdefault("created_at", ts)

        turn_start = _turn_started_at.get()
        if turn_start is not None:
            response.metadata.setdefault("turn_started_at", turn_start)

        return None
