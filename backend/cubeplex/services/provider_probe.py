"""Provider probe — exercises a candidate provider configuration end-to-end.

See spec §4.4 for the two-phase sequence (liveness + per-model capability).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from typing import Any, Literal, cast

from cubeloop.providers.base import (
    ImageContent,
    ReasoningControl,
    StreamOptions,
    TextContent,
    ToolCall,
    ToolDefinition,
    UserMessage,
)
from cubeloop.providers.capability import CapabilityDescriptor, apply_reasoning_control
from pydantic import BaseModel, Field

from cubeplex.llm.config import ModelConfig
from cubeplex.llm.request_parameters import normalized_extra_body, prepare_provider_for_call

ProbeStepName = Literal[
    "liveness", "reasoning", "temperature", "tools", "streaming", "usage", "vision"
]
ProbeStepStatus = Literal["pass", "fail", "skip", "warn"]

# Cap stored error text so a verbose upstream body can't bloat last_test_summary,
# while leaving enough room for the human message the UI extracts (the cubeloop
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
    current: bool = True
    steps: list[ProbeStep] = Field(default_factory=list)


# Steps that block save when they fail; the remainder are advisory.
# Phase-agnostic: phase A passes [liveness]; phase B passes the model steps.
# Each phase only ever feeds its own step names, so keeping both blocking
# names in one set is harmless and keeps the helper reusable.
# An agent-platform model MUST be reachable, stream, and call tools — those are
# the runtime's hard requirements. Reasoning is optional (plenty of usable models
# aren't reasoning models), and temperature/usage are advisory niceties.
_BLOCKING_STEPS: set[ProbeStepName] = {"liveness", "tools", "streaming", "reasoning", "vision"}


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
    max_output: int = 512,
    max_seconds: float = 30.0,
    temperature: float | None = None,
    tools: list[ToolDefinition] | None = None,
    image: ImageContent | None = None,
) -> tuple[list[Any], float, Any]:
    """Run a minimal stream. Return (events, elapsed_seconds, result_message).

    ``result_message`` is the assembled AssistantMessage (or None if it can't be
    resolved) so callers can inspect final content — e.g. a ``ToolCall`` block
    that a buffering gateway returns without emitting per-chunk tool-call events.
    """
    start = time.perf_counter()
    configured = getattr(provider, "_cubeplex_probe_model", None)
    context_window = configured.context_window if isinstance(configured, ModelConfig) else 8192
    output_bound = configured.max_tokens if isinstance(configured, ModelConfig) else max_output
    supports_reasoning = (
        configured.reasoning
        if isinstance(configured, ModelConfig)
        else (reasoning is not None and reasoning.mode != "off")
    )
    if hasattr(provider, "_resolve_capability"):
        provider = prepare_provider_for_call(provider, model_id, reasoning)
    model = provider.model(
        model_id,
        context_window=context_window,
        max_tokens=min(max_output, output_bound),
        reasoning=supports_reasoning,
    ).spec
    if temperature is not None:
        model.temperature = temperature
    signal = asyncio.Event()

    def on_payload(payload: dict[str, Any], _model: Any) -> dict[str, Any]:
        if temperature is not None:
            payload["temperature"] = temperature
        return payload

    content: list[TextContent | ImageContent] = [TextContent(text=prompt)]
    if image is not None:
        content.append(image)
    stream: Any = None
    try:
        async with asyncio.timeout(max_seconds):
            stream = await provider.stream(
                model=model,
                messages=[UserMessage(content=content)],
                options=StreamOptions(
                    reasoning=reasoning or ReasoningControl(), signal=signal, on_payload=on_payload
                ),
                tools=tools,
            )
            events: list[Any] = []
            async for event in stream:
                events.append(event)
                if len(events) > 2000:
                    raise ValueError("Probe stream exceeded its event budget")
                if getattr(event, "type", None) in {"done", "error"}:
                    break
            result = await stream.result()
            return events, time.perf_counter() - start, result
    except TimeoutError as exc:
        raise TimeoutError(
            f"Model did not finish responding within {max_seconds:g} seconds."
        ) from exc
    finally:
        signal.set()
        # MessageStream has no close API; cancel its attached producer on timeout/cancellation.
        task = getattr(stream, "_producer_task", None)
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _stream_error_step(name: ProbeStepName, event: Any, *, warning: bool = False) -> ProbeStep:
    detail = _error_event_detail(event)
    return ProbeStep(
        name=name,
        status="warn" if warning else "fail",
        detail=detail,
        error=ProbeError(type="StreamError", message=detail, raw_status=_status_from_text(detail)),
    )


def _first_error_event(events: list[Any]) -> Any | None:
    """cubeloop surfaces upstream API errors (e.g. 401) as an ``error`` stream event
    rather than raising, so callers must inspect events — a lone error event must
    NOT be mistaken for a successful chunk."""
    return next((e for e in events if getattr(e, "type", None) == "error"), None)


def _error_event_detail(evt: Any) -> str:
    # cubeloop's StreamEvent carries the upstream failure in `error_message` (see
    # cubeloop.providers.base.StreamEvent); the others are defensive fallbacks for
    # differently-shaped event objects. Without error_message a 401/403 would be
    # masked by the generic string below, making a wrong key look like a bug.
    for attr in ("error_message", "error", "message", "detail"):
        v = getattr(evt, attr, None)
        if v:
            return str(v)[:_MAX_DETAIL_CHARS]
    return "stream returned an error event"


# Pull an HTTP status out of a cubeloop error string. cubeloop formats upstream
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
    # Keep the output small while allowing a gateway's first-token latency.
    try:
        events, elapsed, _ = await _drain_stream(
            provider,
            model_id,
            reasoning=None,
            prompt=".",
            max_output=1,
            max_seconds=30.0,
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


def _reasoning_probe_budget(
    provider: Any, capability: CapabilityDescriptor, control: ReasoningControl, model_limit: int
) -> int:
    payload: dict[str, Any] = {
        "extra_body": normalized_extra_body(
            getattr(provider, "_extra_body", {}), capability, control
        )
    }
    apply_reasoning_control(payload, capability, control)
    body = payload.get("extra_body", {})
    thinking = payload.get("thinking", body.get("thinking", {}))
    raw_budget = thinking.get("budget_tokens", 0) if isinstance(thinking, dict) else 0
    if isinstance(thinking, dict) and thinking.get("type") == "disabled":
        raw_budget = 0
    budget = raw_budget if type(raw_budget) is int and raw_budget > 0 else 0
    if budget >= model_limit:
        raise ValueError("The reasoning budget must fit within the configured output limit")
    return min(model_limit, max(512, budget + 128))


async def probe_reasoning_toggle(
    provider: Any, *, model_id: str, capability: CapabilityDescriptor
) -> ProbeStep:
    if capability.reasoning is None:
        return ProbeStep(
            name="reasoning",
            status="skip",
            detail="capability has no reasoning mapping",
        )
    mapping = capability.reasoning
    controls: list[ReasoningControl] = []
    for mode in mapping.mode_payloads:
        efforts = (
            list(mapping.effort_values) if mode != "off" and mapping.effort_path else ["medium"]
        )
        for effort in efforts:
            controls.append(ReasoningControl.model_validate({"mode": mode, "effort": effort}))
    if not controls:
        return ProbeStep(
            name="reasoning", status="skip", detail="No explicitly mapped reasoning controls"
        )
    try:
        for control in controls:
            configured = getattr(provider, "_cubeplex_probe_model", None)
            output_bound = configured.max_tokens if isinstance(configured, ModelConfig) else 8192
            events, _, _ = await _drain_stream(
                provider,
                model_id,
                reasoning=control,
                max_output=_reasoning_probe_budget(provider, capability, control, output_bound),
            )
            error = _first_error_event(events)
            if error is not None:
                detail = _error_event_detail(error)
                return ProbeStep(
                    name="reasoning",
                    status="fail",
                    detail=detail,
                    error=ProbeError(
                        type="StreamError", message=detail, raw_status=_status_from_text(detail)
                    ),
                )
    except Exception as exc:
        return ProbeStep(name="reasoning", status="fail", error=_probe_error(exc))
    return ProbeStep(
        name="reasoning", status="pass", detail="Advertised reasoning parameters accepted"
    )


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
        events, _, _ = await _drain_stream(provider, model_id, temperature=value)
        error = _first_error_event(events)
        if error is not None:
            return _stream_error_step("temperature", error, warning=True)
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
    err = _first_error_event(events)
    if err is not None:
        return _stream_error_step("tools", err)
    event_content = [
        block
        for event in events
        for block in (getattr(getattr(event, "partial", None), "content", None) or [])
    ]
    result_content = getattr(result, "content", None) or []
    saw_result_toolcall = any(
        isinstance(c, ToolCall) and c.name == "echo" and c.arguments.get("text") == "ping"
        for c in [*result_content, *event_content]
    )
    if saw_result_toolcall:
        return ProbeStep(name="tools", status="pass", detail="endpoint returned a tool call")
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
        return _stream_error_step("streaming", err)
    chunks = sum(
        getattr(event, "type", None) in {"text_delta", "thinking_delta", "toolcall_delta"}
        and bool(getattr(event, "delta", None))
        for event in events
    )
    if chunks > 0:
        return ProbeStep(name="streaming", status="pass", detail=f"{chunks} chunks")
    return ProbeStep(name="streaming", status="fail", detail="no streaming chunks observed")


async def probe_usage(provider: Any, *, model_id: str) -> ProbeStep:
    """Advisory: did the response carry a parseable token-usage structure?
    cubeplex cost tracking records zeros without it. Own minimal stream."""
    try:
        events, _, msg = await _drain_stream(provider, model_id, prompt="hi", max_output=64)
        error = _first_error_event(events)
        if error is not None:
            return _stream_error_step("usage", error, warning=True)
    except Exception as exc:
        return ProbeStep(name="usage", status="warn", error=_probe_error(exc))
    usage = getattr(msg, "usage", None)
    if usage is not None and (usage.input_tokens or usage.output_tokens):
        return ProbeStep(
            name="usage",
            status="pass",
            detail=f"in {usage.input_tokens} / out {usage.output_tokens}",
        )
    return ProbeStep(name="usage", status="warn", detail="no usage block → cost recorded as zero")


async def probe_vision(
    provider: Any, *, model_id: str, capability: CapabilityDescriptor
) -> ProbeStep:
    if not capability.supports_images:
        return ProbeStep(name="vision", status="skip", detail="Image input is not advertised")
    import base64
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    width = 64
    pixels = bytearray()
    for y in range(width):
        pixels.append(0)
        for x in range(width):
            pixels.extend(
                (255, 255, 0)
                if y < 32 and x < 32
                else (0, 0, 255)
                if y < 32
                else (0, 128, 0)
                if x < 32
                else (255, 0, 0)
            )
    png = b"\x89PNG\r\n\x1a\n" + chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, width, 8, 2, 0, 0, 0)
    )
    png += chunk(b"IDAT", zlib.compress(bytes(pixels))) + chunk(b"IEND", b"")
    image = ImageContent(source=base64.b64encode(png).decode(), media_type="image/png")
    try:
        events, _, result = await _drain_stream(
            provider,
            model_id,
            image=image,
            prompt="Name the four quadrant colors clockwise from the top left.",
        )
        error = _first_error_event(events)
        if error:
            return _stream_error_step("vision", error)
        text = " ".join(
            block.text
            for block in (getattr(result, "content", []) or [])
            if isinstance(block, TextContent)
        ).lower()
        positions = [text.find(color) for color in ("yellow", "blue", "red", "green")]
        if all(position >= 0 for position in positions) and positions == sorted(positions):
            return ProbeStep(
                name="vision", status="pass", detail="Image-specific response observed"
            )
        return ProbeStep(name="vision", status="fail", detail="Image content was not identified")
    except Exception as exc:
        return ProbeStep(name="vision", status="fail", error=_probe_error(exc))


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
    try:
        return await probe_liveness(provider, model_id=model_id)
    finally:
        client = getattr(provider, "_client", None)
        if client is not None and hasattr(client, "close"):
            await client.close()


async def run_model_probe(
    *, provider_factory: Callable[[], Any], model_id: str, capability: CapabilityDescriptor
) -> ProbeResult:
    """Phase B — model grain. Assumes phase A already passed. Runs the
    capability steps and aggregates. Caller persists to that model's last_test_*."""
    provider = provider_factory()
    try:
        return await _run_model_steps(provider, model_id=model_id, capability=capability)
    finally:
        client = getattr(provider, "_client", None)
        if client is not None and hasattr(client, "close"):
            await client.close()


async def _run_model_steps(
    provider: Any, *, model_id: str, capability: CapabilityDescriptor
) -> ProbeResult:
    reasoning = await probe_reasoning_toggle(provider, model_id=model_id, capability=capability)
    if _is_model_not_found(reasoning):
        return ProbeResult(overall="unavailable", blocking_failed=True, steps=[reasoning])
    temperature, tools, streaming, usage, vision = await asyncio.gather(
        probe_temperature(provider, model_id=model_id, capability=capability),
        probe_tools(provider, model_id=model_id),
        probe_streaming(provider, model_id=model_id),
        probe_usage(provider, model_id=model_id),
        probe_vision(provider, model_id=model_id, capability=capability),
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
    steps = [reasoning, temperature, tools, streaming, usage, vision]
    overall, blocked = _aggregate_overall(steps)
    # _aggregate_overall is typed str but only yields pass/fail/warn here; the
    # "unavailable" verdict is reached via the short-circuit returns above.
    return ProbeResult(
        overall=cast(Literal["pass", "fail", "warn"], overall),
        blocking_failed=blocked,
        steps=steps,
    )
