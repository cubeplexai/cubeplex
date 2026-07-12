"""Provider probe — exercises a candidate provider configuration end-to-end.

See spec §4.4 for the two-phase sequence (liveness + per-model capability).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from typing import Any, Literal, cast

from cubepi.providers.base import (
    ReasoningControl,
    StreamOptions,
    TextContent,
    ToolCall,
    ToolDefinition,
    UserMessage,
)
from cubepi.providers.capability import CapabilityDescriptor
from pydantic import BaseModel, Field

ProbeStepName = Literal["liveness", "reasoning", "temperature", "tools", "streaming", "usage"]
ProbeStepStatus = Literal["pass", "fail", "skip", "warn"]

# Cap stored error text so a verbose upstream body can't bloat last_test_summary,
# while leaving enough room for the human message the UI extracts (the cubepi
# "[probe/<model> @ <url>] ..." prefix alone eats ~80 chars).
_MAX_DETAIL_CHARS = 500


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
# An agent-platform model MUST be reachable, stream, and call tools — those are
# the runtime's hard requirements. Reasoning is optional (plenty of usable models
# aren't reasoning models), and temperature/usage are advisory niceties.
_BLOCKING_STEPS: set[ProbeStepName] = {"liveness", "tools", "streaming"}


def _aggregate_overall(steps: list[ProbeStep]) -> tuple[str, bool]:
    """Roll up per-step statuses into (overall, blocking_failed).

    A blocking step (liveness/tools/streaming) failing → "fail" (model unusable).
    Otherwise the model is usable → "pass"; warns on advisory steps
    (reasoning/temperature/usage) are surfaced as notes per-step but do NOT
    downgrade the model — a live model that streams and calls tools is usable
    even if it doesn't report token usage or isn't a reasoning model.
    """
    blocked = any(s.status == "fail" and s.name in _BLOCKING_STEPS for s in steps)
    if blocked:
        return "fail", True
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
    return ProbeError(
        type=type(exc).__name__, message=str(exc)[:_MAX_DETAIL_CHARS], raw_status=raw_status
    )


async def _drain_stream(
    provider: Any,
    model_id: str,
    *,
    reasoning: ReasoningControl | None = None,
    prompt: str = "Reply with OK.",
    max_output: int = 64,
    max_seconds: float = 15.0,
    temperature: float | None = None,
    tools: list[ToolDefinition] | None = None,
) -> tuple[list[Any], float, Any]:
    """Run a minimal stream. Return (events, elapsed_seconds, result_message).

    ``result_message`` is the assembled AssistantMessage (or None if it can't be
    resolved) so callers can inspect final content — e.g. a ``ToolCall`` block
    that a buffering gateway returns without emitting per-chunk tool-call events.
    """
    start = time.perf_counter()
    reasoning = reasoning or ReasoningControl()
    model = provider.model(
        model_id,
        context_window=8192,
        max_tokens=max_output,
        reasoning=reasoning.mode != "off",
    ).spec
    if temperature is not None:
        model.temperature = temperature
    stream = await asyncio.wait_for(
        provider.stream(
            model=model,
            messages=[UserMessage(content=[TextContent(text=prompt)])],
            options=StreamOptions(reasoning=reasoning),
            tools=tools,
        ),
        timeout=max_seconds,
    )
    events: list[Any] = []
    async for evt in stream:
        events.append(evt)
        if getattr(evt, "type", None) == "done":
            break
    try:
        result = await stream.result()
    except Exception:
        result = None
    return events, time.perf_counter() - start, result


def _first_error_event(events: list[Any]) -> Any | None:
    """cubepi surfaces upstream API errors (e.g. 401) as an ``error`` stream event
    rather than raising, so callers must inspect events — a lone error event must
    NOT be mistaken for a successful chunk."""
    return next((e for e in events if getattr(e, "type", None) == "error"), None)


def _error_event_detail(evt: Any) -> str:
    # cubepi's StreamEvent carries the upstream failure in `error_message` (see
    # cubepi.providers.base.StreamEvent); the others are defensive fallbacks for
    # differently-shaped event objects. Without error_message a 401/403 would be
    # masked by the generic string below, making a wrong key look like a bug.
    for attr in ("error_message", "error", "message", "detail"):
        v = getattr(evt, attr, None)
        if v:
            return str(v)[:_MAX_DETAIL_CHARS]
    return "stream returned an error event"


# Pull an HTTP status out of a cubepi error string. cubepi formats upstream
# failures as "... Error code: 404 - {...}", so the status isn't a structured
# field on the error event — we have to read it back out of the message.
_ERROR_CODE_RE = re.compile(r"error code:\s*(\d{3})", re.IGNORECASE)


def _status_from_text(text: str) -> int | None:
    m = _ERROR_CODE_RE.search(text)
    return int(m.group(1)) if m else None


def _liveness_error_is_provider_level(error: ProbeError) -> bool:
    """Does a failed liveness probe mean the *provider* is bad, not just the model?

    Liveness is provider-grain, so it defaults to a provider-level failure: a
    network error, 5xx, rejected credential, wrong base_url/path (a bare 404), or
    a malformed request (400) all break *every* model and must surface as
    ``provider_error`` — masking them as healthy would hide a real outage.

    The only exceptions — where the endpoint clearly answered about the one probe
    model, so the provider is reachable and the per-model probe should carry the
    verdict instead — are:

    - HTTP 402 (out-of-credits / insufficient quota) for that model/account, and
    - an explicit model-not-found marker (e.g. OpenRouter's "no endpoints found
      for <model>"). A *bare* 404 without such a marker is NOT model-scoped — it
      is treated as a provider/config failure (matching ``_error_says_model_not_found``).
    """
    status = error.raw_status
    if status is None:
        status = _status_from_text(f"{error.type} {error.message}")
    if status == 402:
        return False
    if _error_says_model_not_found(error):
        return False
    return True


async def probe_liveness(provider: Any, *, model_id: str) -> ProbeStep:
    # Spec §4.4 step 1: minimal completion — max_tokens=1, prompt ".",
    # 5s timeout. Proves base_url + key + network reach the endpoint.
    try:
        events, elapsed, _ = await _drain_stream(
            provider,
            model_id,
            reasoning=ReasoningControl(mode="off"),
            prompt=".",
            max_output=1,
            max_seconds=5.0,
        )
    except Exception as exc:
        err = _probe_error(exc)
        if _liveness_error_is_provider_level(err):
            return ProbeStep(name="liveness", status="fail", error=err)
        return ProbeStep(
            name="liveness",
            status="pass",
            detail=f"provider reachable; probe model '{model_id}' unusable: {err.message[:140]}",
        )
    err_evt = _first_error_event(events)
    if err_evt is not None:
        detail = _error_event_detail(err_evt)
        err = ProbeError(type="StreamError", message=detail, raw_status=_status_from_text(detail))
        if _liveness_error_is_provider_level(err):
            return ProbeStep(name="liveness", status="fail", detail=detail, error=err)
        return ProbeStep(
            name="liveness",
            status="pass",
            detail=f"provider reachable; probe model '{model_id}' unusable: {detail[:140]}",
        )
    return ProbeStep(
        name="liveness",
        status="pass",
        latency_ms=int(elapsed * 1000),
        detail=f"{len(events)} events in {int(elapsed * 1000)}ms",
    )


async def probe_reasoning_toggle(
    provider: Any, *, model_id: str, capability: CapabilityDescriptor
) -> ProbeStep:
    if capability.reasoning is None:
        return ProbeStep(
            name="reasoning",
            status="skip",
            detail="capability has no reasoning mapping",
        )
    try:
        await _drain_stream(provider, model_id, reasoning=ReasoningControl(mode="off"))
        await _drain_stream(
            provider,
            model_id,
            reasoning=ReasoningControl(mode="on", effort="medium"),
        )
    except Exception as exc:
        # A model-not-found error here is what Task 9's _is_model_not_found keys
        # on to short-circuit to "unavailable" — keep type/raw_status in the error.
        return ProbeStep(name="reasoning", status="fail", error=_probe_error(exc))
    return ProbeStep(name="reasoning", status="pass", detail="off + on reasoning accepted")


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


async def probe_tools(provider: Any, *, model_id: str) -> ProbeStep:
    # Tool calling is a hard requirement for the agent runtime, so always test it
    # (modern endpoints support tools). A buffering gateway may deliver the tool
    # call only in the assembled result — no per-chunk toolcall_* events — so we
    # accept EITHER a toolcall stream event OR a ToolCall block in the result.
    # Failure here is BLOCKING (the model is unusable for the agent).
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
        events, _, result = await _drain_stream(
            provider,
            model_id,
            prompt="Call the echo tool with text='ping'.",
            tools=[tool],
        )
    except Exception as exc:
        return ProbeStep(
            name="tools", status="fail", detail="tool-call request failed", error=_probe_error(exc)
        )
    saw_event = any(getattr(e, "type", None) in _TOOLCALL_EVENT_TYPES for e in events)
    result_content = getattr(result, "content", None) or []
    saw_result_toolcall = any(isinstance(c, ToolCall) for c in result_content)
    if saw_event or saw_result_toolcall:
        return ProbeStep(name="tools", status="pass", detail="endpoint returned a tool call")
    err = _first_error_event(events)
    if err is not None:
        return ProbeStep(name="tools", status="fail", detail=_error_event_detail(err))
    return ProbeStep(name="tools", status="fail", detail="endpoint did not return a tool call")


async def probe_streaming(provider: Any, *, model_id: str) -> ProbeStep:
    # Streaming is a hard requirement for the agent runtime, so run an
    # INDEPENDENT minimal stream and require at least one chunk. (Previously this
    # borrowed the reasoning probe's chunk count, which is 0 whenever reasoning is
    # skipped — the false "no SSE chunks" seen on empty-capability providers.)
    # Failure here is BLOCKING.
    try:
        events, _, _ = await _drain_stream(provider, model_id, prompt="Reply with OK.")
    except Exception as exc:
        return ProbeStep(
            name="streaming",
            status="fail",
            detail="streaming request failed",
            error=_probe_error(exc),
        )
    err = _first_error_event(events)
    if err is not None:
        return ProbeStep(name="streaming", status="fail", detail=_error_event_detail(err))
    chunks = len(events)
    if chunks > 0:
        return ProbeStep(name="streaming", status="pass", detail=f"{chunks} chunks")
    return ProbeStep(name="streaming", status="fail", detail="no streaming chunks observed")


async def probe_usage(provider: Any, *, model_id: str) -> ProbeStep:
    """Advisory: did the response carry a parseable token-usage structure?
    cubeplex cost tracking records zeros without it. Own minimal stream."""
    try:
        stream = await asyncio.wait_for(
            provider.stream(
                model=provider.model(model_id, context_window=8192, max_tokens=16).spec,
                messages=[UserMessage(content=[TextContent(text="hi")])],
                options=StreamOptions(reasoning=ReasoningControl(mode="off")),
            ),
            timeout=15.0,
        )
        async for _ in stream:  # drain
            pass
        msg = await stream.result()
    except Exception as exc:
        return ProbeStep(name="usage", status="warn", error=_probe_error(exc))
    usage = msg.usage
    if usage is not None and (usage.input_tokens or usage.output_tokens):
        return ProbeStep(
            name="usage",
            status="pass",
            detail=f"in {usage.input_tokens} / out {usage.output_tokens}",
        )
    return ProbeStep(name="usage", status="warn", detail="no usage block → cost recorded as zero")


# Case-insensitive markers a vendor uses to say the model doesn't exist. Paired
# with a 404 raw_status, these are the only signals that map probe → "unavailable".
_MODEL_NOT_FOUND_MARKERS = (
    "model_not_found",
    "model not found",
    "does not exist",
    "unknown model",
    "no such model",
    "invalid model",
    "no endpoints found for",  # OpenRouter's phrasing for a removed/unavailable model
)


def _error_says_model_not_found(error: ProbeError) -> bool:
    """Raw check: does this error mean the vendor lacks the model?

    Requires a model-specific marker in the error type/message. A bare 404 is
    NOT enough: a wrong base_url / route mismatch also 404s, and that is a
    provider/config failure, not a missing model — misreading it would flip a
    single model to "unavailable" and mask the real outage. Provider-level
    reachability is caught at the provider grain by phase-A liveness instead.
    Used by _is_model_not_found and by run_model_probe's robustness path, where
    the advisory temperature/tools steps carry the error on a *warn* status.
    """
    haystack = f"{error.type} {error.message}".lower()
    return any(marker in haystack for marker in _MODEL_NOT_FOUND_MARKERS)


# Case-insensitive markers a vendor uses to say the credential is bad. Paired
# with a 401/403 raw_status, these map a runtime error → provider liveness fail.
_AUTH_ERROR_MARKERS = (
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "authentication",
    "incorrect api key",
    "permission denied",
    "forbidden",
)


def _error_says_auth_failure(error: ProbeError) -> bool:
    """Raw check: does this error mean the provider credential is rejected?

    Keys on a 401/403 raw_status or a known marker in the error type/message.
    Used by the runtime status writeback to flip provider liveness to "fail".
    """
    if error.raw_status in (401, 403):
        return True
    haystack = f"{error.type} {error.message}".lower()
    return any(marker in haystack for marker in _AUTH_ERROR_MARKERS)


def liveness_status_for(step: ProbeStep) -> Literal["ok", "auth_error", "fail"]:
    """Map a liveness ProbeStep to the persisted provider liveness status.

    Splits a provider-level failure into a rejected-credential case
    (``auth_error``) and everything else (``fail``) so the UI can tell the user
    to fix the key versus the endpoint. A pass is ``ok``. (A model-specific
    failure already returns a passing step — see ``probe_liveness`` — so it
    never reaches here as a fail.)
    """
    if step.status == "pass":
        return "ok"
    if step.error is not None and _error_says_auth_failure(step.error):
        return "auth_error"
    return "fail"


def _is_model_not_found(step: ProbeStep) -> bool:
    """True only when a failed step's error means the vendor lacks the model.

    Keys on either a 404 raw_status or a known marker in the error type/message.
    skip/warn steps are never model_not_found.
    """
    if step.status != "fail" or step.error is None:
        return False
    return _error_says_model_not_found(step.error)


async def run_liveness(*, provider_factory: Callable[[], Any], model_id: str) -> ProbeStep:
    """Phase A — provider grain. One minimal call against any model.
    Caller persists the result to providers.last_liveness_*."""
    provider = provider_factory()
    return await probe_liveness(provider, model_id=model_id)


async def run_model_probe(
    *, provider_factory: Callable[[], Any], model_id: str, capability: CapabilityDescriptor
) -> ProbeResult:
    """Phase B — model grain. Assumes phase A already passed. Runs the
    capability steps and aggregates. Caller persists to that model's last_test_*."""
    provider = provider_factory()
    reasoning = await probe_reasoning_toggle(provider, model_id=model_id, capability=capability)
    if _is_model_not_found(reasoning):
        return ProbeResult(overall="unavailable", blocking_failed=True, steps=[reasoning])
    temperature, tools, streaming, usage = await asyncio.gather(
        probe_temperature(provider, model_id=model_id, capability=capability),
        probe_tools(provider, model_id=model_id),
        probe_streaming(provider, model_id=model_id),
        probe_usage(provider, model_id=model_id),
        return_exceptions=False,
    )
    # ROBUSTNESS: when capability has no reasoning payloads, probe_reasoning_toggle
    # SKIPS (no real call), so it can't catch model_not_found. In that case the
    # first real calls are temperature/tools/streaming — check them too via the
    # carried error (a blocking tools/streaming fail would otherwise read as a
    # capability gap rather than a missing model).
    if reasoning.status == "skip":
        for s in (temperature, tools, streaming, usage):
            if s.error is not None and _error_says_model_not_found(s.error):
                return ProbeResult(
                    overall="unavailable",
                    blocking_failed=True,
                    steps=[reasoning, temperature, tools, streaming, usage],
                )
    steps = [reasoning, temperature, tools, streaming, usage]
    overall, blocked = _aggregate_overall(steps)
    # _aggregate_overall is typed str but only yields pass/fail/warn here; the
    # "unavailable" verdict is reached via the short-circuit returns above.
    return ProbeResult(
        overall=cast(Literal["pass", "fail", "warn"], overall),
        blocking_failed=blocked,
        steps=steps,
    )
