"""Provider probe — exercises a candidate provider configuration end-to-end.

See spec §4.4 for the two-phase sequence (liveness + per-model capability).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProbeStepName = Literal["liveness", "reasoning", "temperature", "tools", "streaming"]
ProbeStepStatus = Literal["pass", "fail", "skip", "warn"]


class ProbeError(BaseModel):
    type: str
    message: str
    raw_status: int | None = None


class ProbeStep(BaseModel):
    name: ProbeStepName
    status: ProbeStepStatus
    latency_ms: int | None = None
    detail: str = ""
    error: ProbeError | None = None
    # Count of SSE chunks observed during this step's stream. Lets the
    # streaming check (Tasks 8/9) verify a chunk arrived without re-streaming.
    # Excluded from the API payload — internal probe plumbing only.
    observed_chunks: int = Field(default=0, exclude=True)


class ProbeResult(BaseModel):
    # "unavailable" is the model-not-found short-circuit (Task 9); the
    # aggregator only ever returns pass/fail/warn.
    overall: Literal["pass", "fail", "warn", "unavailable"]
    blocking_failed: bool
    steps: list[ProbeStep] = Field(default_factory=list)


# Steps that block save when they fail; the remainder are advisory.
# Phase-agnostic: phase A passes [liveness]; phase B passes the model steps.
# Each phase only ever feeds its own step names, so keeping both blocking
# names in one set is harmless and keeps the helper reusable.
_BLOCKING_STEPS: set[ProbeStepName] = {"liveness", "reasoning"}


def _aggregate_overall(steps: list[ProbeStep]) -> tuple[str, bool]:
    """Roll up per-step statuses into (overall, blocking_failed)."""
    blocked = any(s.status == "fail" and s.name in _BLOCKING_STEPS for s in steps)
    if blocked:
        return "fail", True
    if any(s.status in ("fail", "warn") for s in steps):
        return "warn", False
    return "pass", False
