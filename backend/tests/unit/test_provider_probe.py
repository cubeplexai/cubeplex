"""Provider probe — aggregate logic.

Per-step behavior is tested in dedicated additions (Tasks 7-9). This file
covers the orchestrator's overall-result computation.
"""

import pytest

from cubebox.services.provider_probe import (
    ProbeError,
    ProbeStep,
    _aggregate_overall,
    _is_model_not_found,
    liveness_status_for,
    probe_liveness,
    probe_reasoning_toggle,
    probe_streaming,
    probe_temperature,
    probe_tools,
    run_liveness,
    run_model_probe,
)


def test_aggregate_all_pass_is_pass():
    steps = [
        ProbeStep(name="liveness", status="pass", latency_ms=120),
        ProbeStep(name="reasoning", status="pass"),
    ]
    overall, blocked = _aggregate_overall(steps)
    assert overall == "pass"
    assert blocked is False


def test_aggregate_liveness_fail_is_blocking():
    steps = [
        ProbeStep(
            name="liveness",
            status="fail",
            error=ProbeError(type="AuthError", message="401"),
        ),
    ]
    overall, blocked = _aggregate_overall(steps)
    assert overall == "fail"
    assert blocked is True


class _StubProvider:
    """Fake cubepi.Provider for probe tests. Records calls, returns canned events."""

    def __init__(self, *, events=None, raise_error=None, result_content=None):
        self._events = events or []
        self._raise_error = raise_error
        self._result_content = result_content or []
        self.calls: list[dict] = []

    def model(self, id: str, **kwargs):  # type: ignore[override]
        from cubepi.providers.base import BoundModel, Model

        return BoundModel(provider=self, spec=Model(id=id, **kwargs))  # type: ignore[arg-type]

    async def stream(self, model, messages, *, options=None, system_prompt="", tools=None):
        self.calls.append({"thinking": getattr(options, "thinking", "off")})
        if self._raise_error is not None:
            raise self._raise_error
        events = self._events
        result_content = self._result_content

        class _Stream:
            def __aiter__(_self):
                async def gen():
                    for e in events:
                        yield e

                return gen()

            async def result(_self):
                # Default: report non-zero usage so the advisory usage probe
                # passes for the orchestrator happy-path tests. ``result_content``
                # lets a test return e.g. a ToolCall block.
                from cubepi.providers.base import AssistantMessage, Usage

                return AssistantMessage(
                    content=result_content, usage=Usage(input_tokens=5, output_tokens=2)
                )

        return _Stream()


@pytest.mark.asyncio
async def test_probe_liveness_pass():
    provider = _StubProvider(events=[type("E", (), {"type": "text_delta", "delta": "OK"})()])
    step = await probe_liveness(provider, model_id="test-model")
    assert step.name == "liveness"
    assert step.status == "pass"
    assert step.latency_ms is not None


@pytest.mark.asyncio
async def test_probe_liveness_fail_on_exception():
    provider = _StubProvider(raise_error=RuntimeError("401 Unauthorized"))
    step = await probe_liveness(provider, model_id="test-model")
    assert step.status == "fail"
    assert step.error is not None
    assert "401" in step.error.message


@pytest.mark.asyncio
async def test_probe_reasoning_skips_when_capability_empty():
    from cubepi.providers.capability import CapabilityDescriptor

    step = await probe_reasoning_toggle(
        _StubProvider(), model_id="m", capability=CapabilityDescriptor()
    )
    assert step.status == "skip"


@pytest.mark.asyncio
async def test_probe_reasoning_runs_both_off_and_on():
    from cubepi.providers.capability import CapabilityDescriptor

    cap = CapabilityDescriptor(
        reasoning_off_payload={"extra_body": {"enable_thinking": False}},
        reasoning_on_payload={"extra_body": {"enable_thinking": True}},
    )
    provider = _StubProvider(events=[type("E", (), {"type": "text_delta", "delta": "OK"})()])
    step = await probe_reasoning_toggle(provider, model_id="m", capability=cap)
    assert step.status == "pass"
    assert len(provider.calls) == 2
    assert provider.calls[0]["thinking"] == "off"
    assert provider.calls[1]["thinking"] == "medium"


def test_aggregate_tools_fail_is_blocking():
    steps = [
        ProbeStep(name="liveness", status="pass"),
        ProbeStep(name="tools", status="fail"),
    ]
    overall, blocked = _aggregate_overall(steps)
    assert overall == "fail"
    assert blocked is True


def test_aggregate_streaming_fail_is_blocking():
    steps = [
        ProbeStep(name="liveness", status="pass"),
        ProbeStep(name="streaming", status="fail"),
    ]
    overall, blocked = _aggregate_overall(steps)
    assert overall == "fail"
    assert blocked is True


def test_aggregate_advisory_fail_does_not_block_or_degrade():
    # reasoning/temperature/usage are advisory: a live model that streams + calls
    # tools is usable even if these warn/fail, so overall stays "pass".
    steps = [
        ProbeStep(name="liveness", status="pass"),
        ProbeStep(name="tools", status="pass"),
        ProbeStep(name="streaming", status="pass"),
        ProbeStep(name="reasoning", status="fail"),
        ProbeStep(name="usage", status="warn"),
    ]
    overall, blocked = _aggregate_overall(steps)
    assert overall == "pass"
    assert blocked is False


@pytest.mark.asyncio
async def test_probe_temperature_pass():
    from cubepi.providers.capability import CapabilityDescriptor, TemperatureSpec

    cap = CapabilityDescriptor(temperature=TemperatureSpec(mode="free", default=1.0))
    provider = _StubProvider(events=[type("E", (), {"type": "text_delta", "delta": "OK"})()])
    step = await probe_temperature(provider, model_id="m", capability=cap)
    assert step.name == "temperature"
    assert step.status == "pass"


@pytest.mark.asyncio
async def test_probe_temperature_skips_when_ignored():
    from cubepi.providers.capability import CapabilityDescriptor, TemperatureSpec

    cap = CapabilityDescriptor(temperature=TemperatureSpec(mode="ignored"))
    step = await probe_temperature(_StubProvider(), model_id="m", capability=cap)
    assert step.status == "skip"


@pytest.mark.asyncio
async def test_probe_tools_pass_on_toolcall_event():
    provider = _StubProvider(events=[type("E", (), {"type": "toolcall_start"})()])
    step = await probe_tools(provider, model_id="m")
    assert step.name == "tools"
    assert step.status == "pass"


@pytest.mark.asyncio
async def test_probe_tools_pass_on_result_toolcall_without_stream_event():
    # Buffering gateway: no per-chunk toolcall_* event, but the assembled result
    # carries a ToolCall block. Must still pass.
    from cubepi.providers.base import ToolCall

    provider = _StubProvider(
        events=[type("E", (), {"type": "text_delta", "delta": "ok"})()],
        result_content=[ToolCall(id="t1", name="echo", arguments={"text": "ping"})],
    )
    step = await probe_tools(provider, model_id="m")
    assert step.status == "pass"


@pytest.mark.asyncio
async def test_probe_tools_fails_when_no_toolcall():
    provider = _StubProvider(events=[type("E", (), {"type": "text_delta", "delta": "hi"})()])
    step = await probe_tools(provider, model_id="m")
    assert step.status == "fail"


@pytest.mark.asyncio
async def test_probe_streaming_passes_on_chunks():
    provider = _StubProvider(events=[type("E", (), {"type": "text_delta", "delta": "OK"})()])
    step = await probe_streaming(provider, model_id="m")
    assert step.status == "pass"
    assert "1" in step.detail


@pytest.mark.asyncio
async def test_probe_streaming_fails_on_zero_chunks():
    step = await probe_streaming(_StubProvider(events=[]), model_id="m")
    assert step.status == "fail"


@pytest.mark.asyncio
async def test_probe_liveness_fails_on_error_event():
    # cubepi surfaces a 401 as an `error` stream event (not an exception); it must
    # NOT count as a successful "1 event" liveness pass.
    err = type("E", (), {"type": "error", "error": "401 Unauthorized"})()
    step = await probe_liveness(_StubProvider(events=[err]), model_id="m")
    assert step.status == "fail"
    assert "401" in step.detail


@pytest.mark.asyncio
async def test_probe_liveness_surfaces_cubepi_error_message():
    # cubepi's StreamEvent carries the upstream failure in `error_message` (see
    # cubepi.providers.base.StreamEvent), NOT `error`/`message`/`detail`. The
    # probe must read that field so a 401 reaches the UI instead of a generic
    # "stream returned an error event".
    err = type(
        "E",
        (),
        {
            "type": "error",
            "error_message": "AuthenticationError: 401 - Missing Authentication header",
        },
    )()
    step = await probe_liveness(_StubProvider(events=[err]), model_id="m")
    assert step.status == "fail"
    assert "401" in step.detail
    assert "Authentication" in step.detail


def _err_event(message: str):
    return type("E", (), {"type": "error", "error_message": message})()


@pytest.mark.asyncio
async def test_liveness_402_is_provider_reachable():
    # OpenRouter "out of credits" for one free model: the endpoint answered, so
    # the provider is reachable — liveness must NOT condemn the whole provider.
    evt = _err_event(
        "[probe/deepseek:free @ .../] APIStatusError: Error code: 402 - "
        "{'error': {'message': 'Provider returned error', 'code': 402}}"
    )
    step = await probe_liveness(_StubProvider(events=[evt]), model_id="deepseek:free")
    assert step.status == "pass"
    assert "reachable" in step.detail


@pytest.mark.asyncio
async def test_liveness_404_model_removed_is_provider_reachable():
    # OpenRouter 404 "No endpoints found for <model>": model gone, provider up.
    evt = _err_event(
        "[probe/stepfun:free @ .../] NotFoundError: Error code: 404 - "
        "{'error': {'message': 'No endpoints found for stepfun:free.', 'code': 404}}"
    )
    step = await probe_liveness(_StubProvider(events=[evt]), model_id="stepfun:free")
    assert step.status == "pass"
    assert "reachable" in step.detail


@pytest.mark.asyncio
async def test_liveness_bare_404_is_provider_level_fail():
    # A 404 WITHOUT a model-not-found marker is a wrong base_url/path (config), which
    # breaks every model → must surface as provider_error, not a passing liveness.
    evt = _err_event("NotFoundError: Error code: 404 - {'detail': 'Not Found'}")
    step = await probe_liveness(_StubProvider(events=[evt]), model_id="m")
    assert step.status == "fail"


@pytest.mark.asyncio
async def test_liveness_400_bad_request_is_provider_level_fail():
    # A 400 request-shape mismatch affects every model → provider-grain fail.
    evt = _err_event("BadRequestError: Error code: 400 - {'error': 'unsupported parameter'}")
    step = await probe_liveness(_StubProvider(events=[evt]), model_id="m")
    assert step.status == "fail"


@pytest.mark.asyncio
async def test_liveness_401_is_provider_level_fail():
    # A rejected credential breaks every model → provider-grain fail.
    evt = _err_event("AuthenticationError: Error code: 401 - Missing Authentication header")
    step = await probe_liveness(_StubProvider(events=[evt]), model_id="m")
    assert step.status == "fail"
    assert "401" in step.detail


@pytest.mark.asyncio
async def test_liveness_5xx_is_provider_level_fail():
    evt = _err_event("APIStatusError: Error code: 503 - service unavailable")
    step = await probe_liveness(_StubProvider(events=[evt]), model_id="m")
    assert step.status == "fail"


@pytest.mark.asyncio
async def test_liveness_no_status_is_provider_level_fail():
    # No HTTP status at all (network/DNS/timeout) → unreachable.
    step = await probe_liveness(
        _StubProvider(events=[_err_event("connection refused")]), model_id="m"
    )
    assert step.status == "fail"


@pytest.mark.asyncio
async def test_liveness_status_for_401_is_auth_error():
    # A 401 liveness fail persists as "auth_error" so the badge says "fix key".
    evt = _err_event("AuthenticationError: Error code: 401 - Missing Authentication header")
    step = await probe_liveness(_StubProvider(events=[evt]), model_id="m")
    assert liveness_status_for(step) == "auth_error"


def test_liveness_status_for_pass_is_ok():
    assert liveness_status_for(ProbeStep(name="liveness", status="pass")) == "ok"


def test_liveness_status_for_non_auth_fail_is_fail():
    step = ProbeStep(
        name="liveness",
        status="fail",
        error=ProbeError(type="StreamError", message="503 service unavailable", raw_status=503),
    )
    assert liveness_status_for(step) == "fail"


@pytest.mark.asyncio
async def test_probe_streaming_fails_on_error_event():
    err = type("E", (), {"type": "error", "error": "boom"})()
    step = await probe_streaming(_StubProvider(events=[err]), model_id="m")
    assert step.status == "fail"


# --- Task 9: orchestrators + model_not_found classifier ---------------------


_GOOD_EVENT = type("E", (), {"type": "text_delta", "delta": "OK"})


def _good_event():
    return _GOOD_EVENT()


def _reasoning_cap():
    from cubepi.providers.capability import CapabilityDescriptor

    return CapabilityDescriptor(
        reasoning_off_payload={"extra_body": {"enable_thinking": False}},
        reasoning_on_payload={"extra_body": {"enable_thinking": True}},
    )


class _NotFoundError(Exception):
    """Vendor-style 404 exposing a status_code attribute."""

    def __init__(self, message: str, status_code: int = 404):
        super().__init__(message)
        self.status_code = status_code


def test_is_model_not_found_classifier():
    # A bare 404 with no model marker is NOT model_not_found — it's likely a
    # wrong base_url / route mismatch (provider/config failure), which phase-A
    # liveness catches at the provider grain. (PR #124 codex P1.)
    bare_404 = ProbeStep(
        name="reasoning",
        status="fail",
        error=ProbeError(type="NotFoundError", message="boom", raw_status=404),
    )
    assert _is_model_not_found(bare_404) is False

    # A 404 that DOES carry a model marker is model_not_found.
    not_found_404_marked = ProbeStep(
        name="reasoning",
        status="fail",
        error=ProbeError(type="NotFoundError", message="unknown model gpt-x", raw_status=404),
    )
    assert _is_model_not_found(not_found_404_marked) is True

    not_found_message = ProbeStep(
        name="reasoning",
        status="fail",
        error=ProbeError(type="RuntimeError", message="error: model_not_found"),
    )
    assert _is_model_not_found(not_found_message) is True

    warn_step = ProbeStep(name="tools", status="warn", detail="advisory only")
    assert _is_model_not_found(warn_step) is False

    other_fail = ProbeStep(
        name="reasoning",
        status="fail",
        error=ProbeError(type="AuthError", message="401 Unauthorized", raw_status=401),
    )
    assert _is_model_not_found(other_fail) is False


@pytest.mark.asyncio
async def test_run_liveness_pass_and_fail():
    good = run_liveness(
        provider_factory=lambda: _StubProvider(events=[_good_event()]),
        model_id="m",
    )
    step = await good
    assert step.name == "liveness"
    assert step.status == "pass"

    bad = run_liveness(
        provider_factory=lambda: _StubProvider(raise_error=RuntimeError("401 ...")),
        model_id="m",
    )
    fail_step = await bad
    assert fail_step.name == "liveness"
    assert fail_step.status == "fail"


@pytest.mark.asyncio
async def test_run_model_probe_happy_path():
    # Default capability has supports_tools=True, so the tools probe runs and
    # needs a toolcall event to pass; pair it with a text delta for reasoning.
    happy_events = [
        type("E", (), {"type": "toolcall_start"})(),
        _good_event(),
    ]
    result = await run_model_probe(
        provider_factory=lambda: _StubProvider(events=happy_events),
        model_id="m",
        capability=_reasoning_cap(),
    )
    assert result.overall == "pass"
    assert result.blocking_failed is False
    names = [s.name for s in result.steps]
    assert "reasoning" in names
    assert "liveness" not in names


@pytest.mark.asyncio
async def test_run_model_probe_model_not_found_is_unavailable():
    result = await run_model_probe(
        provider_factory=lambda: _StubProvider(
            raise_error=_NotFoundError("model_not_found", status_code=404)
        ),
        model_id="m",
        capability=_reasoning_cap(),
    )
    assert result.overall == "unavailable"
    assert result.blocking_failed is True


@pytest.mark.asyncio
async def test_run_model_probe_skipped_reasoning_still_detects_unavailable():
    from cubepi.providers.capability import CapabilityDescriptor, TemperatureSpec

    # Empty reasoning payloads → reasoning SKIPS, so temperature is the first
    # real call. The stub always raises model_not_found, so the robustness path
    # in run_model_probe must catch it on temperature/tools.
    cap = CapabilityDescriptor(temperature=TemperatureSpec(mode="free", default=1.0))
    result = await run_model_probe(
        provider_factory=lambda: _StubProvider(
            raise_error=_NotFoundError("model_not_found", status_code=404)
        ),
        model_id="m",
        capability=cap,
    )
    assert result.overall == "unavailable"
    assert result.blocking_failed is True


# --- Task B3: advisory usage probe ------------------------------------------


class _UsageStub(_StubProvider):
    """Stub whose stream.result() returns an AssistantMessage with given usage."""

    def __init__(self, *, usage=None, **kw):
        super().__init__(**kw)
        self._usage = usage

    async def stream(self, *a, **k):
        s = await super().stream(*a, **k)
        usage = self._usage

        async def _result():
            from cubepi.providers.base import AssistantMessage

            return AssistantMessage(content=[], usage=usage)

        s.result = _result  # type: ignore[attr-defined,method-assign]
        return s


@pytest.mark.asyncio
async def test_probe_usage_pass_when_usage_present():
    from cubepi.providers.base import Usage

    from cubebox.services.provider_probe import probe_usage

    step = await probe_usage(
        _UsageStub(usage=Usage(input_tokens=10, output_tokens=3), events=[]),
        model_id="m",
    )
    assert step.name == "usage"
    assert step.status == "pass"


@pytest.mark.asyncio
async def test_probe_usage_warn_when_absent():
    from cubebox.services.provider_probe import probe_usage

    step = await probe_usage(_UsageStub(usage=None, events=[]), model_id="m")
    assert step.status == "warn"
