"""Provider probe — exercises a candidate provider configuration end-to-end.

See spec §4.4 for the two-phase sequence (liveness + per-model capability).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from cubepi.providers.base import (
    Model,
    StreamOptions,
    TextContent,
    ThinkingLevel,
    ToolDefinition,
    UserMessage,
)
from cubepi.providers.capability import CapabilityDescriptor
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


def _probe_error(exc: Exception) -> ProbeError:
    """Build a ProbeError from an exception, defensively extracting a status code.

    raw_status is what Task 9's model_not_found classifier keys on, so we check
    the common shapes: a top-level ``status_code`` or one on a ``response`` attr.
    """
    raw_status = getattr(exc, "status_code", None)
    if raw_status is None:
        raw_status = getattr(getattr(exc, "response", None), "status_code", None)
    if not isinstance(raw_status, int):
        raw_status = None
    return ProbeError(type=type(exc).__name__, message=str(exc)[:200], raw_status=raw_status)


async def _drain_stream(
    provider: Any,
    model_id: str,
    *,
    thinking: ThinkingLevel = "off",
    prompt: str = "Reply with OK.",
    max_output: int = 64,
    max_seconds: float = 15.0,
    temperature: float | None = None,
    tools: list[ToolDefinition] | None = None,
) -> tuple[list[Any], float]:
    """Run a minimal stream, draining events. Return (events, elapsed_seconds)."""
    start = time.perf_counter()
    model = Model(id=model_id, provider="probe", context_window=8192, max_tokens=max_output)
    if temperature is not None:
        model.temperature = temperature
    stream = await asyncio.wait_for(
        provider.stream(
            model=model,
            messages=[UserMessage(content=[TextContent(text=prompt)])],
            options=StreamOptions(thinking=thinking),
            tools=tools,
        ),
        timeout=max_seconds,
    )
    events: list[Any] = []
    async for evt in stream:
        events.append(evt)
        if getattr(evt, "type", None) == "done":
            break
    return events, time.perf_counter() - start


async def probe_liveness(provider: Any, *, model_id: str) -> ProbeStep:
    # Spec §4.4 step 1: minimal completion — max_tokens=1, prompt ".",
    # 5s timeout. Proves base_url + key + network reach the endpoint.
    try:
        events, elapsed = await _drain_stream(
            provider,
            model_id,
            thinking="off",
            prompt=".",
            max_output=1,
            max_seconds=5.0,
        )
    except Exception as exc:
        return ProbeStep(name="liveness", status="fail", error=_probe_error(exc))
    return ProbeStep(
        name="liveness",
        status="pass",
        latency_ms=int(elapsed * 1000),
        detail=f"{len(events)} events in {int(elapsed * 1000)}ms",
    )


async def probe_reasoning_toggle(
    provider: Any, *, model_id: str, capability: CapabilityDescriptor
) -> ProbeStep:
    if not capability.reasoning_off_payload and not capability.reasoning_on_payload:
        return ProbeStep(
            name="reasoning",
            status="skip",
            detail="capability has no reasoning_off/on payload",
        )
    try:
        await _drain_stream(provider, model_id, thinking="off")
        on_events, _ = await _drain_stream(provider, model_id, thinking="medium")
    except Exception as exc:
        # A model-not-found error here is what Task 9's _is_model_not_found keys
        # on to short-circuit to "unavailable" — keep type/raw_status in the error.
        return ProbeStep(name="reasoning", status="fail", error=_probe_error(exc))
    return ProbeStep(
        name="reasoning",
        status="pass",
        detail="off + on payload both accepted",
        observed_chunks=len(on_events),
    )


# Stream event types that signal the endpoint emitted a tool call. cubepi's
# StreamEvent.type uses the "toolcall_*" family (see cubepi.providers.base);
# seeing any of these proves the endpoint can drive tool use.
_TOOLCALL_EVENT_TYPES = {"toolcall_start", "toolcall_delta", "toolcall_end"}


async def probe_temperature(
    provider: Any, *, model_id: str, capability: CapabilityDescriptor
) -> ProbeStep:
    # Spec §4.4 step 3 (advisory): confirm the endpoint accepts the temperature
    # we'd actually send. mode="ignored" means the key is stripped, so there's
    # nothing to probe. Failures only WARN — temperature must never block save.
    spec = capability.temperature
    if spec.mode == "ignored":
        return ProbeStep(name="temperature", status="skip", detail="temperature mode is ignored")
    if spec.mode == "fixed" and spec.fixed_value is not None:
        value = spec.fixed_value
    else:
        value = spec.default
    try:
        await _drain_stream(provider, model_id, temperature=value)
    except Exception as exc:
        return ProbeStep(
            name="temperature",
            status="warn",
            detail=f"endpoint rejected temperature={value}",
            error=_probe_error(exc),
        )
    return ProbeStep(name="temperature", status="pass", detail=f"accepted temperature={value}")


async def probe_tools(
    provider: Any, *, model_id: str, capability: CapabilityDescriptor
) -> ProbeStep:
    # Spec §4.4 step 4 (advisory): if the endpoint claims tool support, send a
    # one-tool probe and confirm a tool-call event came back. Failures only WARN
    # so an over-eager supports_tools flag doesn't block save.
    if not capability.supports_tools:
        return ProbeStep(name="tools", status="skip", detail="capability has supports_tools=False")
    tool = ToolDefinition(
        name="echo",
        description="Echo the provided text back to the caller.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )
    try:
        events, _ = await _drain_stream(
            provider,
            model_id,
            prompt="Call the echo tool with text='ping'.",
            tools=[tool],
        )
    except Exception as exc:
        return ProbeStep(
            name="tools",
            status="warn",
            detail="endpoint did not emit tool call; consider unchecking supports_tools",
            error=_probe_error(exc),
        )
    saw_tool_call = any(getattr(e, "type", None) in _TOOLCALL_EVENT_TYPES for e in events)
    if saw_tool_call:
        return ProbeStep(name="tools", status="pass", detail="endpoint emitted a tool call")
    return ProbeStep(
        name="tools",
        status="warn",
        detail="endpoint did not emit tool call; consider unchecking supports_tools",
    )


def probe_streaming(*, observed_chunks: int, name: str = "streaming") -> ProbeStep:
    # Spec §4.4 step 5 (advisory, pure): reuse the chunk count captured by the
    # reasoning probe. Zero chunks means the endpoint answered but never streamed
    # — surface a warning rather than silently passing an inert config.
    if observed_chunks > 0:
        return ProbeStep(name="streaming", status="pass", detail=f"{observed_chunks} chunks")
    return ProbeStep(name="streaming", status="warn", detail="no SSE chunks observed")
