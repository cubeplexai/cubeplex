"""Provider probe — aggregate logic.

Per-step behavior is tested in dedicated additions (Tasks 7-9). This file
covers the orchestrator's overall-result computation.
"""

from cubebox.services.provider_probe import (
    ProbeError,
    ProbeStep,
    _aggregate_overall,
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
