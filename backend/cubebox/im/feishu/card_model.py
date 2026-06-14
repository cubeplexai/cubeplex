"""Typed card state for outbound Feishu rendering.

Pure data: no IO, no Feishu SDK imports. The renderer turns one of these
into a CardKit JSON 2.0 payload; the tailer mutates one of these as
cubepi events arrive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class ToolStep:
    """One row in the tool-use panel."""

    id: str
    name: str
    args: dict[str, Any]
    status: Literal["running", "succeeded", "failed"] = "running"
    result: Any = None
    error: str | None = None
    elapsed_ms: int = 0
    start_monotonic: float = 0.0

    def mark_succeeded(self, *, result: Any, elapsed_ms: int) -> None:
        self.status = "succeeded"
        self.result = result
        self.elapsed_ms = elapsed_ms

    def mark_failed(self, *, error: str, elapsed_ms: int) -> None:
        self.status = "failed"
        self.error = error
        self.elapsed_ms = elapsed_ms


@dataclass(slots=True)
class ArtifactItem:
    """One row in the artifacts panel."""

    id: str
    artifact_type: str
    name: str
    share_url: str | None = None
    image_key: str | None = None
    description: str | None = None


@dataclass(slots=True)
class PendingInput:
    """An awaiting-user-input prompt rendered as an interactive_container."""

    kind: Literal["ask_user", "sandbox_confirm"]
    run_id: str
    question: str
    choices: list[tuple[str, str]] = field(default_factory=list)
    """Pairs of (choice_key, button_type). button_type ∈ {"primary","default","danger"}."""
    question_id: str | None = None
    """cubepi-side identifier for matching the resume call."""
    answer_key: str | None = None
    """cubepi form schema key (questions[0].key for ask_user). The resume call
    builds the answer dict as {answer_key: choice} — without this, cubepi
    rejects the answer because our key won't match its schema."""
    resolved_choice: str | None = None
    resolved_by_open_id: str | None = None
    resolved_at_iso: str | None = None


@dataclass(slots=True)
class SubAgentRow:
    """Light SubAgent marker — one line in the tool panel above the regular steps."""

    agent_id: str
    name: str
    tool_count: int = 0


@dataclass(slots=True)
class CardState:
    """Per-run accumulating state, projected into CardKit JSON by the renderer.

    Mutated in-place by `fold_event`. The renderer never mutates.
    """

    bot_name: str
    run_id: str
    streaming_content: str = ""
    tool_steps: list[ToolStep] = field(default_factory=list)
    sub_agents: list[SubAgentRow] = field(default_factory=list)
    artifacts: list[ArtifactItem] = field(default_factory=list)
    citation_index: dict[str, tuple[str, str]] = field(default_factory=dict)
    """citation_id → (url, title). Renderer rewrites 【citation_id-chunk_index】 markers via this map."""
    pending_input: PendingInput | None = None
    finalized: bool = False
    error: str | None = None
    elapsed_ms: int = 0
    next_seq: int = 0
    epoch: int = 0
    """Bumped on run abort; in-flight responses for a stale epoch are dropped."""
    run_start_monotonic: float = 0.0
    """Stashed on first event; used to compute elapsed_ms on done (cubepi done.data is empty)."""

    def advance_seq(self) -> int:
        seq = self.next_seq
        self.next_seq += 1
        return seq

    def find_tool(self, tool_id: str) -> ToolStep | None:
        for step in self.tool_steps:
            if step.id == tool_id:
                return step
        return None

    def find_sub_agent(self, agent_id: str) -> SubAgentRow | None:
        for row in self.sub_agents:
            if row.agent_id == agent_id:
                return row
        return None


__all__ = [
    "ArtifactItem",
    "CardState",
    "PendingInput",
    "SubAgentRow",
    "ToolStep",
]
