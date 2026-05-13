"""CompactionMiddlewarePi — cubepi port of CompactionMiddleware (M3.b.2).

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

Summarizer is the same async ``summarize()`` from
``cubebox.middleware.compaction.summarizer``; it requires a
``langchain_core.language_models.BaseChatModel``.  The boundary helper
``safe_boundary`` and the ``CompactionSummary`` dataclass are likewise
re-used unchanged from the LangGraph version.

Token counting: ``approx_tokens`` works on LangChain message types, so
this module uses a lightweight cubepi-compatible estimator
(``_cubepi_approx_tokens``) that applies the same ``_CHARS_PER_TOKEN``
conservative constant and checks ``usage.input_tokens`` from
``AssistantMessage.usage`` for self-scaling — matching the semantics of
the LangGraph version.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

from cubepi.middleware.base import Middleware
from cubepi.providers.base import AssistantMessage, Message, TextContent, UserMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage as LCAssistantMessage
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage
from loguru import logger

from cubebox.agents.state import CompactionSummary
from cubebox.middleware.compaction.boundary import safe_boundary
from cubebox.middleware.compaction.summarizer import summarize
from cubebox.middleware.compaction.tokens import _CHARS_PER_TOKEN

SUMMARY_PREFIX = "[Conversation summary so far]\n"

# Minimum token count before we start trusting usage_metadata scaling.
# Below this we rely solely on the chars-per-token estimate.
_SCALE_MIN_TOKENS = 100


def _cubepi_approx_tokens(messages: list[Message]) -> int:
    """Approximate token count for a cubepi message list.

    Uses the same conservative ``_CHARS_PER_TOKEN`` constant as the
    LangGraph ``approx_tokens``.  For ``AssistantMessage`` objects that
    carry ``usage.input_tokens > 0``, applies a usage-metadata scaling
    factor (clamped to [1.0, 1.25]) to self-calibrate against the first
    assistant turn that has real token counts.
    """
    if not messages:
        return 0

    total_chars = 0
    scale_factor: float | None = None

    for msg in messages:
        if isinstance(msg, UserMessage):
            for ub in msg.content:
                if hasattr(ub, "text"):
                    total_chars += len(ub.text)
        elif isinstance(msg, AssistantMessage):
            for ab in msg.content:
                if hasattr(ab, "text"):
                    total_chars += len(ab.text)
                elif hasattr(ab, "arguments"):
                    # ToolCall
                    total_chars += len(json.dumps(getattr(ab, "arguments", {})))
            # Self-scaling: if this assistant message has real usage data,
            # derive a chars-per-token factor (clamped to avoid overcorrection).
            usage = msg.usage
            if usage and usage.input_tokens >= _SCALE_MIN_TOKENS and scale_factor is None:
                chars_estimate = usage.input_tokens * _CHARS_PER_TOKEN
                if chars_estimate > 0:
                    raw_factor = total_chars / chars_estimate
                    scale_factor = max(1.0, min(raw_factor, 1.25))
        else:
            # ToolResultMessage — count content text
            for tb in getattr(msg, "content", []):
                if hasattr(tb, "text"):
                    total_chars += len(tb.text)

    char_token_estimate = total_chars / _CHARS_PER_TOKEN
    if scale_factor is not None:
        return int(char_token_estimate * scale_factor)
    return int(char_token_estimate)


def _compressed_view_pi(
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


def _to_langchain_messages(messages: list[Message]) -> list[AnyMessage]:
    """Convert cubepi messages to LangChain messages for the summarizer.

    The summarizer (``cubebox.middleware.compaction.summarizer.summarize``)
    requires LangChain message objects.  This function produces a minimal
    faithful conversion — enough for the summarizer to read ``.content``
    and ``.__class__.__name__``.
    """
    lc: list[AnyMessage] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            text_parts = [block.text for block in msg.content if hasattr(block, "text")]
            lc.append(HumanMessage(content="\n".join(text_parts)))
        elif isinstance(msg, AssistantMessage):
            text_parts = []
            tool_calls = []
            for block in msg.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)
                elif hasattr(block, "name") and hasattr(block, "arguments"):
                    # ToolCall
                    tool_calls.append(
                        {
                            "id": getattr(block, "id", ""),
                            "name": block.name,
                            "args": block.arguments,
                            "type": "tool_call",
                        }
                    )
            ai_msg = LCAssistantMessage(content="\n".join(text_parts))
            if tool_calls:
                ai_msg.tool_calls = tool_calls  # type: ignore[assignment]
            lc.append(ai_msg)
        else:
            # ToolResultMessage
            text_parts = [
                block.text for block in getattr(msg, "content", []) if hasattr(block, "text")
            ]
            lc.append(
                ToolMessage(
                    content="\n".join(text_parts),
                    tool_call_id=getattr(msg, "tool_call_id", ""),
                )
            )
    return lc


class CompactionMiddlewarePi(Middleware):
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
            A ``BaseChatModel`` used to generate / update the running
            summary.
        max_tokens_before_compact:
            Token threshold for the compressed view.  When
            ``_cubepi_approx_tokens`` of the current compressed view
            exceeds this value, a new summary is generated.
        keep_recent_messages:
            Minimum number of messages to keep verbatim (not summarized).
        max_summary_tokens:
            ``max_tokens`` passed to the summarizer LLM.
        min_compact_messages:
            Minimum number of messages that must be in the prefix before
            compaction fires.
    """

    def __init__(
        self,
        *,
        extra_ref: Callable[[], dict[str, Any]],
        summary_llm: BaseChatModel,
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
        3. If ``_cubepi_approx_tokens(compressed_view) < threshold``,
           return the compressed view unchanged.
        4. Otherwise run the summarizer over unsummarized turns, write
           the new summary + boundary back to ``extra``, and return the
           freshly compressed view.
        """
        del signal  # unused

        extra = self._extra_ref()
        summary = cast("CompactionSummary | None", extra.get("compaction"))
        boundary = cast("int | None", extra.get("compaction_until_msg_index")) or 0

        compressed = _compressed_view_pi(messages, summary, boundary)

        if _cubepi_approx_tokens(compressed) < self._max_tokens_before:
            return compressed

        # Need to compact further.
        boundary = boundary or 0
        new_boundary = safe_boundary(
            _to_langchain_messages(messages),
            keep_recent=self._keep_recent,
            min_compact=max(self._min_compact, boundary + 1),
        )
        if new_boundary is None or new_boundary <= boundary:
            # Cannot advance the boundary — return what we have.
            return compressed

        to_summarize_lc = _to_langchain_messages(messages)[boundary:new_boundary]
        try:
            new_summary = await summarize(
                model=self._summary_llm,
                messages_to_summarize=to_summarize_lc,
                existing=summary,
                max_summary_tokens=self._max_summary_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("CompactionMiddlewarePi: summarizer failed, skipping: {}", exc)
            return compressed

        logger.info(
            "CompactionMiddlewarePi: compacted msgs[{}:{}] ({} msgs)",
            boundary,
            new_boundary,
            len(to_summarize_lc),
        )

        # Write new state back to the live extra dict.
        extra["compaction"] = new_summary
        extra["compaction_until_msg_index"] = new_boundary

        return _compressed_view_pi(messages, new_summary, new_boundary)
