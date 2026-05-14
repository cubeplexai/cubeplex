"""CompactionMiddleware — cubepi port of CompactionMiddleware (M3.b.2).

Keeps long conversation history within the model's context window by
maintaining a running summary of older turns.

State lives in ``ctx.extra`` instead of LangGraph channels:
    - ``ctx.extra["compaction"]``                  → CompactionSummary | None
    - ``ctx.extra["compaction_until_msg_index"]``  → int (boundary)

The ``transform_context`` hook cannot receive ``ctx`` directly (the cubepi
signature is ``transform_context(messages, *, signal)``).  Instead the
middleware holds a reference to the live ``extra`` dict via an
``extra_ref: Callable[[], dict]`` constructor argument.  The agent
factory passes a closure over ``agent._extra``, which is the same dict
object that ``Agent._create_context_snapshot`` passes as
``AgentContext.extra`` — so mutations are visible to the checkpointer's
``save_extra`` call at ``agent_end``.

Summarizer is the async ``summarize()`` from
``cubebox.middleware.compaction.summarizer``; it accepts any object
satisfying the ``_OneShotProvider`` Protocol (duck-typed: an async
``generate_once(*, system, messages, max_output_tokens) -> str``).
Production wires ``cubebox.llm.oneshot.OneShotLLM`` over a real
``cubepi.Provider``; tests pass in fakes implementing the same surface.

Token counting is now native to cubepi: ``tokens.approx_tokens`` walks
``UserMessage`` / ``AssistantMessage`` / ``ToolResultMessage`` content
directly and self-scales off ``AssistantMessage.usage.input_tokens``
when available. No LangChain bridge involved.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from cubepi.middleware.base import Middleware
from cubepi.providers.base import Message, TextContent, UserMessage
from loguru import logger

from cubebox.middleware.compaction.boundary import safe_boundary
from cubebox.middleware.compaction.summarizer import (
    CompactionSummary,
    _OneShotProvider,
    summarize,
)
from cubebox.middleware.compaction.tokens import approx_tokens

SUMMARY_PREFIX = "[Conversation summary so far]\n"


def _compressed_view(
    messages: list[Message],
    summary: CompactionSummary | None,
    boundary: int | None,
) -> list[Message]:
    """Build the view the LLM would see given current compaction state.

    If a summary + valid boundary exist, returns:
        [UserMessage(summary_text), *messages[boundary:]]

    The summary is injected as a ``UserMessage`` with a leading
    TextContent block (cubepi has no dedicated SystemMessage type).
    Otherwise returns the original messages list unchanged.
    """
    if summary and boundary and boundary > 0:
        summary_msg = UserMessage(
            content=[TextContent(text=SUMMARY_PREFIX + summary.summary)],
        )
        return [summary_msg, *messages[boundary:]]
    return list(messages)


class CompactionMiddleware(Middleware):
    """cubepi port of CompactionMiddleware (M3.b.2).

    Compresses older conversation turns into a persisted
    ``CompactionSummary`` stored in ``ctx.extra``, and installs a
    "compressed view" (summary + recent messages) for each model call.

    Constructor args:
        extra_ref:
            Callable returning the live ``extra`` dict associated with
            the current agent.  The agent factory passes a closure over
            ``agent._extra`` so mutations persist via ``save_extra`` at
            ``agent_end``.
        summary_llm:
            Any object satisfying the ``_OneShotProvider`` Protocol
            (async ``generate_once``).  Production wires
            ``cubebox.llm.oneshot.OneShotLLM``.
        max_tokens_before_compact:
            Token threshold for the compressed view.  When
            ``approx_tokens`` of the current compressed view exceeds
            this value, a new summary is generated.
        keep_recent_messages:
            Minimum number of messages to keep verbatim (not summarized).
        max_summary_tokens:
            ``max_output_tokens`` passed to the summarizer LLM.
        min_compact_messages:
            Minimum number of messages that must be in the prefix before
            compaction fires.
    """

    def __init__(
        self,
        *,
        extra_ref: Callable[[], dict[str, Any]],
        summary_llm: _OneShotProvider,
        max_tokens_before_compact: int,
        keep_recent_messages: int = 8,
        max_summary_tokens: int = 1024,
        min_compact_messages: int = 4,
    ) -> None:
        self._extra_ref = extra_ref
        self._summary_llm = summary_llm
        self._max_tokens_before = max_tokens_before_compact
        self._keep_recent = keep_recent_messages
        self._max_summary_tokens = max_summary_tokens
        self._min_compact = min_compact_messages

    async def transform_context(
        self,
        messages: list[Message],
        *,
        signal: object = None,
    ) -> list[Message]:
        """Build compressed view; update summary in ctx.extra when over threshold.

        1. Read ``compaction`` and ``compaction_until_msg_index`` from
           the live ``extra`` dict.
        2. Compute the compressed view (summary + messages[boundary:]).
        3. If ``approx_tokens(compressed_view) < threshold``,
           return the compressed view unchanged.
        4. Otherwise run the summarizer over unsummarized turns, write
           the new summary + boundary back to ``extra``, and return the
           freshly compressed view.
        """
        del signal  # unused

        extra = self._extra_ref()
        summary = cast("CompactionSummary | None", extra.get("compaction"))
        boundary = cast("int | None", extra.get("compaction_until_msg_index")) or 0

        compressed = _compressed_view(messages, summary, boundary)

        if approx_tokens(compressed) < self._max_tokens_before:
            return compressed

        # Need to compact further.
        boundary = boundary or 0
        new_boundary = safe_boundary(
            messages,
            keep_recent=self._keep_recent,
            min_compact=max(self._min_compact, boundary + 1),
        )
        if new_boundary is None or new_boundary <= boundary:
            # Cannot advance the boundary — return what we have.
            return compressed

        to_summarize = messages[boundary:new_boundary]
        try:
            new_summary = await summarize(
                provider=self._summary_llm,
                messages_to_summarize=to_summarize,
                existing=summary,
                max_summary_tokens=self._max_summary_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("CompactionMiddleware: summarizer failed, skipping: {}", exc)
            return compressed

        logger.info(
            "CompactionMiddleware: compacted msgs[{}:{}] ({} msgs)",
            boundary,
            new_boundary,
            len(to_summarize),
        )

        # Write new state back to the live extra dict.
        extra["compaction"] = new_summary
        extra["compaction_until_msg_index"] = new_boundary

        return _compressed_view(messages, new_summary, new_boundary)
