"""Unit tests for the pure readiness-derivation helper (spec §4.1)."""

from __future__ import annotations

import pytest

from cubeplex.llm.readiness import (
    capability_fingerprint,
    derive_readiness,
)


def test_provider_fail_overrides_everything() -> None:
    # Even a passing model is provider_error when liveness failed (blast radius).
    assert (
        derive_readiness(
            liveness_status="fail",
            model_test_status="ok",
            capability_changed_since_test=True,
        )
        == "provider_error"
    )


def test_provider_fail_with_never_tested_model() -> None:
    assert (
        derive_readiness(
            liveness_status="fail",
            model_test_status=None,
            capability_changed_since_test=False,
        )
        == "provider_error"
    )


def test_auth_error_is_distinct_from_provider_error() -> None:
    # A rejected credential is provider-grain like "fail", but must surface as a
    # separate enum so the UI says "fix the key", not "endpoint unreachable".
    assert (
        derive_readiness(
            liveness_status="auth_error",
            model_test_status="ok",
            capability_changed_since_test=True,
        )
        == "auth_error"
    )


def test_model_unavailable() -> None:
    assert (
        derive_readiness(
            liveness_status="ok",
            model_test_status="unavailable",
            capability_changed_since_test=True,
        )
        == "unavailable"
    )


def test_model_fail() -> None:
    assert (
        derive_readiness(
            liveness_status="ok",
            model_test_status="fail",
            capability_changed_since_test=True,
        )
        == "model_error"
    )


def test_capability_changed_is_stale() -> None:
    assert (
        derive_readiness(
            liveness_status="ok",
            model_test_status="ok",
            capability_changed_since_test=True,
        )
        == "stale"
    )


def test_warn_is_degraded() -> None:
    assert (
        derive_readiness(
            liveness_status="ok",
            model_test_status="warn",
            capability_changed_since_test=False,
        )
        == "degraded"
    )


def test_warn_with_capability_changed_prefers_stale() -> None:
    # Precedence: stale outranks degraded.
    assert (
        derive_readiness(
            liveness_status="ok",
            model_test_status="warn",
            capability_changed_since_test=True,
        )
        == "stale"
    )


def test_ok_is_ready() -> None:
    assert (
        derive_readiness(
            liveness_status="ok",
            model_test_status="ok",
            capability_changed_since_test=False,
        )
        == "ready"
    )


def test_never_tested_liveness_is_not_a_failure() -> None:
    # liveness_status is None (never probed) must NOT yield provider_error.
    assert (
        derive_readiness(
            liveness_status=None,
            model_test_status="ok",
            capability_changed_since_test=False,
        )
        == "ready"
    )


def test_never_tested_model_is_ready() -> None:
    # model_test_status is None (never probed) → ready (presumed usable).
    assert (
        derive_readiness(
            liveness_status="ok",
            model_test_status=None,
            capability_changed_since_test=False,
        )
        == "ready"
    )


def test_both_never_tested_is_ready() -> None:
    assert (
        derive_readiness(
            liveness_status=None,
            model_test_status=None,
            capability_changed_since_test=False,
        )
        == "ready"
    )


def test_never_tested_liveness_with_failing_model() -> None:
    # None liveness is not failed, so the model's own fail surfaces.
    assert (
        derive_readiness(
            liveness_status=None,
            model_test_status="fail",
            capability_changed_since_test=False,
        )
        == "model_error"
    )


def test_capability_fingerprint_is_stable_and_order_insensitive() -> None:
    a = capability_fingerprint({"reasoning": True, "tools": False}, {"m1": {"x": 1}})
    b = capability_fingerprint({"tools": False, "reasoning": True}, {"m1": {"x": 1}})
    assert a == b


def test_capability_fingerprint_changes_with_input() -> None:
    a = capability_fingerprint({"reasoning": True}, {})
    b = capability_fingerprint({"reasoning": False}, {})
    assert a != b


@pytest.mark.parametrize(
    ("liveness", "model", "changed", "expected"),
    [
        ("fail", "ok", False, "provider_error"),
        ("ok", "unavailable", False, "unavailable"),
        ("ok", "fail", False, "model_error"),
        ("ok", "ok", True, "stale"),
        ("ok", "warn", False, "degraded"),
        ("ok", "ok", False, "ready"),
        (None, None, False, "ready"),
    ],
)
def test_full_branch_matrix(
    liveness: str | None, model: str | None, changed: bool, expected: str
) -> None:
    assert (
        derive_readiness(
            liveness_status=liveness,
            model_test_status=model,
            capability_changed_since_test=changed,
        )
        == expected
    )
