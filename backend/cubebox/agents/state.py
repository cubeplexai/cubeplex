"""Cubebox-specific extensions to the langchain agent state schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NotRequired

from langchain.agents.middleware.types import AgentState


@dataclass
class CompactionSummary:
    """Persisted running summary of a conversation's older turns.

    Stored on agent state, serialized by the LangGraph checkpointer.
    Three-field shape mirrors the canonical "running summary" pattern: the text,
    which messages it covers, and where the rolling window currently ends.
    """

    summary: str
    summarized_message_ids: list[str] = field(default_factory=list)
    last_summarized_message_id: str | None = None


class CubeboxState(AgentState[Any]):
    """Agent state with compaction fields.

    Extends AgentState (TypedDict) with two optional keys:
      compaction:                 CompactionSummary persisted across turns
      compaction_until_msg_index: int boundary in state["messages"]
    """

    compaction: NotRequired[CompactionSummary | None]
    compaction_until_msg_index: NotRequired[int | None]
