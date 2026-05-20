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

    def __init__(self, *, events=None, raise_error=None):
        self._events = events or []
        self._raise_error = raise_error
        self.calls: list[dict] = []

    async def stream(self, model, messages, *, options=None, system_prompt="", tools=None):
        self.calls.append({"thinking": getattr(options, "thinking", "off")})
        if self._raise_error is not None:
            raise self._raise_error
        events = self._events

        class _Stream:
            def __aiter__(_self):
                async def gen():
                    for e in events:
                        yield e

                return gen()

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


def test_aggregate_advisory_step_fail_warns_not_blocks():
    steps = [
        ProbeStep(name="liveness", status="pass"),
        ProbeStep(name="tools", status="fail"),
    ]
    overall, blocked = _aggregate_overall(steps)
    assert overall == "warn"
    assert blocked is False


def test_aggregate_reasoning_fail_is_blocking():
    steps = [
        ProbeStep(name="liveness", status="pass"),
        ProbeStep(name="reasoning", status="fail"),
    ]
    overall, blocked = _aggregate_overall(steps)
    assert overall == "fail"
    assert blocked is True


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
    from cubepi.providers.capability import CapabilityDescriptor

    cap = CapabilityDescriptor(supports_tools=True)
    provider = _StubProvider(events=[type("E", (), {"type": "toolcall_start"})()])
    step = await probe_tools(provider, model_id="m", capability=cap)
    assert step.name == "tools"
    assert step.status == "pass"


@pytest.mark.asyncio
async def test_probe_tools_skips_when_unsupported():
    from cubepi.providers.capability import CapabilityDescriptor

    cap = CapabilityDescriptor(supports_tools=False)
    step = await probe_tools(_StubProvider(), model_id="m", capability=cap)
    assert step.status == "skip"


def test_probe_streaming_warns_on_zero_chunks():
    assert probe_streaming(observed_chunks=0).status == "warn"


def test_probe_streaming_passes_on_chunks():
    step = probe_streaming(observed_chunks=3)
    assert step.status == "pass"
    assert "3" in step.detail


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
    not_found_404 = ProbeStep(
        name="reasoning",
        status="fail",
        error=ProbeError(type="NotFoundError", message="boom", raw_status=404),
    )
    assert _is_model_not_found(not_found_404) is True

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
