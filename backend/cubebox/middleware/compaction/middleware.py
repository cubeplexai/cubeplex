"""CompactionMiddleware — persist summary in state, project compressed view per call."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage
from langgraph.runtime import Runtime
from loguru import logger

from cubebox.agents.state import CompactionSummary
from cubebox.middleware.compaction.boundary import safe_boundary
from cubebox.middleware.compaction.summarizer import summarize
from cubebox.middleware.compaction.tokens import approx_tokens

SUMMARY_PREFIX = "[Conversation summary so far]\n"


def _compressed_view(state: Any) -> list[AnyMessage]:
    """Build the messages list the LLM would actually see given current state.

    If a CompactionSummary exists and a boundary has been recorded, the view is
    [SystemMessage(summary), *messages[boundary:]] — exactly what awrap_model_call
    will install on the request. Otherwise it's the raw messages list.

    Used by abefore_model so the threshold check is against what we're about to
    SEND, not against the raw history (which keeps growing and would force
    needless re-compaction on stable conversations, plus break usage_metadata
    scaling — see tokens.py docstring).
    """
    msgs: list[AnyMessage] = list(state.get("messages") or [])
    summary: CompactionSummary | None = state.get("compaction")
    boundary: int | None = state.get("compaction_until_msg_index")
    if summary and boundary and boundary > 0:
        return [
            SystemMessage(content=SUMMARY_PREFIX + summary.summary),
            *msgs[boundary:],
        ]
    return msgs


class CompactionMiddleware(AgentMiddleware[Any, Any, Any]):
    """Compress old turns into a persisted CompactionSummary; project compressed view per call.

    Two responsibilities split across two hooks:
      abefore_model — decide whether to compact further; if so, write new summary state.
      awrap_model_call — install the compressed view on request.messages just for this call.
    """

    def __init__(
        self,
        *,
        summary_llm: BaseChatModel,
        max_tokens_before_compact: int,
        keep_recent_messages: int = 8,
        max_summary_tokens: int = 1024,
        min_compact_messages: int = 4,
    ) -> None:
        self._summary_llm = summary_llm
        self._max_tokens_before = max_tokens_before_compact
        self._keep_recent = keep_recent_messages
        self._max_summary_tokens = max_summary_tokens
        self._min_compact = min_compact_messages

    async def abefore_model(
        self,
        state: Any,
        runtime: Runtime[Any] | None = None,
    ) -> dict[str, Any] | None:
        # Threshold check: measure what we're ABOUT to send (compressed view),
        # not the raw history. If a stable conversation already has a summary
        # that fits, this returns early and avoids re-summarizing.
        if approx_tokens(_compressed_view(state)) < self._max_tokens_before:
            return None

        msgs: list[AnyMessage] = list(state.get("messages") or [])
        existing = cast("CompactionSummary | None", state.get("compaction"))
        last_until: int = cast("int | None", state.get("compaction_until_msg_index")) or 0

        boundary = safe_boundary(
            msgs,
            keep_recent=self._keep_recent,
            min_compact=max(self._min_compact, last_until + 1),
        )
        if boundary is None or boundary <= last_until:
            return None

        to_summarize = msgs[last_until:boundary]
        try:
            new_summary = await summarize(
                model=self._summary_llm,
                messages_to_summarize=to_summarize,
                existing=existing,
                max_summary_tokens=self._max_summary_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("CompactionMiddleware: summarizer failed, skipping: {}", exc)
            return None

        logger.info(
            "CompactionMiddleware: compacted msgs[{}:{}] ({} msgs)",
            last_until,
            boundary,
            len(to_summarize),
        )
        return {
            "compaction": new_summary,
            "compaction_until_msg_index": boundary,
        }

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        state: Any = request.state or {}
        summary = cast("CompactionSummary | None", state.get("compaction"))
        boundary = cast("int | None", state.get("compaction_until_msg_index"))

        if summary and boundary and boundary > 0:
            request.messages = _compressed_view(state)

        return await handler(request)
