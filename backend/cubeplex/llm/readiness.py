"""Pure helpers for the per-model `readiness` enum (spec §4.1).

Readiness combines one cheap provider-level liveness status with each model's
own probe status into a single value the UI reads verbatim (it never
re-derives). The model picker (later slice) only looks at `readiness`.

Two never-tested decisions are baked in here:

- ``liveness_status is None`` (never probed) is NOT a failure. A seeded/legacy
  provider is presumed reachable; the liveness probe only DOWNGRADES on an
  explicit ``"fail"``. So only an explicit ``"fail"`` yields ``provider_error``.
- ``model_test_status is None`` (never probed) → ``"ready"``. Absence of a
  failing test is not a failure: wizard-created models are gated on a passing
  test, so only seeded/legacy models lack probe data, and we must not disable
  an org's actual working models.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# §4.1 readiness enum values (string literals, server-derived).
READY = "ready"
DEGRADED = "degraded"
STALE = "stale"
MODEL_ERROR = "model_error"
UNAVAILABLE = "unavailable"
PROVIDER_ERROR = "provider_error"
AUTH_ERROR = "auth_error"


def derive_readiness(
    *,
    liveness_status: str | None,
    model_test_status: str | None,
    capability_changed_since_test: bool,
) -> str:
    """Map (provider liveness, model test, capability-edit) → readiness enum.

    Precedence (spec §4.1), highest first:
    1. provider liveness == "auth_error"       -> "auth_error" (all models)
    2. provider liveness == "fail"             -> "provider_error" (all models)
    3. model_test_status == "unavailable"      -> "unavailable"
    4. model_test_status == "fail"             -> "model_error"
    5. capability_changed_since_test           -> "stale"
    6. model_test_status == "warn"             -> "degraded"
    7. otherwise                               -> "ready"

    ``auth_error`` and ``fail`` are both provider-grain (they black out every
    model), but split so the UI can say "fix the key" vs "endpoint is down".

    Never-probed inputs are presumed healthy: ``liveness_status is None`` is
    treated as "not failed" and ``model_test_status is None`` falls through to
    "ready". See module docstring for the rationale.
    """
    if liveness_status == "auth_error":
        return AUTH_ERROR
    if liveness_status == "fail":
        return PROVIDER_ERROR
    if model_test_status == "unavailable":
        return UNAVAILABLE
    if model_test_status == "fail":
        return MODEL_ERROR
    if capability_changed_since_test:
        return STALE
    if model_test_status == "warn":
        return DEGRADED
    return READY


def capability_fingerprint(
    capability: dict[str, Any],
    model_capability_overrides: dict[str, Any],
) -> str:
    """Stable hash of the capability inputs that gate `stale` readiness.

    Persisted into a model's ``last_test_summary`` by the probe (Task 10) so a
    later read can detect a capability edit since the last test. Kept here so
    the probe and the serializer agree on the exact algorithm.
    """
    payload = json.dumps(
        {"capability": capability, "model_capability_overrides": model_capability_overrides},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
