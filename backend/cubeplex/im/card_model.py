"""Typed card state for IM platform rendering.

Pure data: no IO, no platform SDK imports. Platform renderers turn one of these
into a platform-specific payload; the tailer mutates one of these as
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
    """An awaiting-user-input prompt rendered as an interactive element."""

    kind: Literal["ask_user", "sandbox_confirm"]
    run_id: str
    question: str
    choices: list[tuple[str, str, str]] = field(default_factory=list)
    """Tuples of ``(label, value, button_type)``.

    - ``label`` is the human-visible button text (the cubepi option's
      ``label`` field). What the user actually reads on the card.
    - ``value`` is the schema key cubepi expects back in the answer dict
      (the option's ``value`` / ``key`` field). What the resume call sends.
    - ``button_type`` ∈ {"primary","default","danger"} — rendering
      hint (the option's ``type`` field).

    Keeping label and value separate matters: cubepi schemas commonly use
    machine values like ``yes`` / ``no`` with human-readable labels like
    ``Yes`` / ``No``. Rendering the value as button text would force the
    user to choose between machine tokens.
    """
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
    """Per-run accumulating state, projected into platform-specific payloads by renderers.

    Mutated in-place by `fold_event`. Renderers never mutate.
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
    hitl_resolved: bool = False
    post_hitl_content: str = ""
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
