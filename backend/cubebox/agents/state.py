"""Cubebox-specific extensions to the langchain agent state schema.

Adds two channels on top of the default `AgentState`:

- **CompactionSummary** (`compaction` + `compaction_until_msg_index`):
  persisted running summary of older conversation turns, used by
  CompactionMiddleware to keep context within the model's window.
- **memory_snapshots**: per-user-message immutable captures of the
  relevance memory injected at that turn. They are persisted by the
  checkpointer and replayed byte-identical on subsequent requests so the
  prompt cache can hit through history. The reducer rejects overwrites —
  this is the cache-correctness guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, NotRequired

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


def _merge_snapshots(
    left: dict[str, dict[str, Any]] | None,
    right: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Reducer: shallow-merge with right-wins, but reject overwrites of an
    existing key. Snapshots are immutable once written."""
    out: dict[str, dict[str, Any]] = dict(left or {})
    for k, v in (right or {}).items():
        if k in out and out[k] != v:
            # Refuse to overwrite — this is the immutability guarantee.
            # A bug here is a serious cache-correctness violation.
            raise ValueError(f"memory_snapshot for {k} already exists; cannot overwrite")
        out[k] = v
    return out


class CubeboxState(AgentState[Any]):
    """Agent state with compaction + memory channels.

    Extends AgentState (TypedDict) with:
      compaction:                 CompactionSummary persisted across turns
      compaction_until_msg_index: int boundary in state["messages"]
      memory_snapshots:           per-message relevance memory snapshot
                                  (immutable; reducer rejects overwrites)
    """

    compaction: NotRequired[CompactionSummary | None]
    compaction_until_msg_index: NotRequired[int | None]
    memory_snapshots: Annotated[dict[str, dict[str, Any]], _merge_snapshots]
