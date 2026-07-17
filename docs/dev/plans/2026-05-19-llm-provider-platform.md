# LLM Provider Platform — Plan Slice 1 (cubepi M1 + M2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/dev/specs/2026-05-19-llm-provider-platform-design.md`
**Slice:** Milestones M1 (capability core) + M2 (preset catalog) — both
in the cubepi repo. cubeplex-side milestones (M3–M7) get a follow-up
plan once this slice is merged + cubepi released.

**Revision 4** (icons): `ProviderPreset` gains a `logo: str | None`
field carrying an `@lobehub/icons` provider id. cubepi ships no
SVG assets — only the lookup key. cubeplex frontend installs
`@lobehub/icons` (M4 milestone) and renders via
`<ProviderIcon provider={preset.logo} … />`. All 20 preset entries
in the YAML now declare a `logo:` line (`logo: null` for the two
`custom-*` presets). Spec §3.6, §3.7, §7 Q5.

**Revision 3** (file layout): `capability.py` and the `catalog/`
package moved under `cubepi/providers/` (was top-level
`cubepi/capability.py` and `cubepi/catalog/`). Both are
provider-layer concerns and belong next to `base.py` and
`models.py` for discoverability. `cubepi.__init__` re-exports stay
the same so the public import surface (`from cubepi import
CapabilityDescriptor`) is unchanged.

**Revision 2** (after codex review pass): introduced `_cap_active`
flag on OpenAIProvider / OpenAIResponsesProvider so legacy callers
(no capability passed) see byte-identical behavior — temperature /
max_tokens / reasoning payload are only applied when capability or
overrides were explicitly given. Anthropic budgets in the
default-capability map aligned to `ThinkingBudgets` (minimal=1024,
low=2048, medium=8192, high=16384, xhigh=16384). Dropped the never-
triggerable `"max"` level keys. pyyaml made explicit dep. Hatch
package-data declaration switched to `include` pattern (replacing
`force-include`). Added explicit ruff + mypy verification steps in
Task 16.

**Goal:** Land the `CapabilityDescriptor` runtime in cubepi so every
Provider class translates vendor-specific reasoning / temperature /
max-tokens quirks from data, not branched code. Ship the
`ProviderPreset` catalog alongside it so cubeplex can pull a list of 20
preset bundles and offer one-click vendor onboarding.

**Architecture:** A `CapabilityDescriptor` pydantic model is passed to
each `Provider.__init__` (default `None` → no-op for legacy callers).
The Provider's `stream()` applies the descriptor in a fixed sequence:
temperature constraint, max_tokens field rename, reasoning off/on
payload deep-merge, optional fine-grain level write. A
`model_capability_overrides: dict[model_id, CapabilityDescriptor]`
handles the OpenRouter case (one endpoint, divergent model
conventions). The preset catalog is a YAML data file at
`cubepi/providers/catalog/data/providers.yaml`, loaded once on import.

**Tech Stack:** Python 3.11+, pydantic 2.x, pyyaml (already transitive
via anthropic/openai SDK deps), pytest + pytest-asyncio (existing).

**Where the executor works:**
- Code changes happen in `/home/chris/cubepi`.
- This plan and the spec live in cubeplex worktree
  `/home/chris/cubeplex/.worktrees/feat/llm-provider-platform` (port slot 28).
  After this slice is done, the cubeplex worktree gets a follow-up plan
  for M3 onwards.

---

## File Structure

### Created
- `/home/chris/cubepi/cubepi/providers/capability.py` — `CapabilityDescriptor`,
  `TemperatureSpec`, `ReasoningLevelSpec`, helpers `merge_capability_payload`,
  `apply_temperature`, `write_reasoning_level`.
- `/home/chris/cubepi/cubepi/providers/catalog/__init__.py` — public API
  (`list_provider_presets`, `get_provider_preset`, `WireApi` re-export).
- `/home/chris/cubepi/cubepi/providers/catalog/types.py` — `ProviderPreset`,
  `ModelPreset`, `AuthSpec`, `WireApi`.
- `/home/chris/cubepi/cubepi/providers/catalog/data/providers.yaml` — 20-entry
  preset list.
- `/home/chris/cubepi/tests/test_capability.py` — type defaults, merge
  rules, temperature, reasoning level helpers.
- `/home/chris/cubepi/tests/providers/test_openai_capability.py` —
  OpenAIProvider applies capability correctly.
- `/home/chris/cubepi/tests/providers/test_openai_responses_capability.py`
- `/home/chris/cubepi/tests/providers/test_anthropic_capability.py`
- `/home/chris/cubepi/tests/test_catalog.py` — preset loader + per-preset
  validation.

### Modified
- `/home/chris/cubepi/cubepi/providers/openai.py` — add `capability` +
  `model_capability_overrides` kwargs; apply them in `stream()`;
  retire `_payload_quirks`.
- `/home/chris/cubepi/cubepi/providers/openai_responses.py` — same as
  above (no `_payload_quirks` retire there).
- `/home/chris/cubepi/cubepi/providers/anthropic.py` — same; migrate
  the inline thinking-budget + temperature-when-thinking logic onto
  the capability path.
- `/home/chris/cubepi/cubepi/__init__.py` — export new public surface
  (`CapabilityDescriptor`, `TemperatureSpec`, `ReasoningLevelSpec`,
  `list_provider_presets`, `get_provider_preset`).
- `/home/chris/cubepi/pyproject.toml` — bump version to `0.5.0`; add
  `pyyaml` to runtime deps if not already pulled.

### Reference (read-only)
- `/home/chris/cubepi/cubepi/providers/base.py` — `Model`, `StreamOptions`,
  `ThinkingLevel`, `BaseProvider`, listener registry hooks.
- `/home/chris/cubepi/cubepi/providers/models.py` — existing
  `adjust_max_tokens_for_thinking`, `clamp_thinking_level`,
  `THINKING_BUDGETS` (these may be deprecated in T15 once capability
  paths cover their behavior).

---

## Task 1: `CapabilityDescriptor` + sub-types

**Files:**
- Create: `/home/chris/cubepi/cubepi/providers/capability.py`
- Test: `/home/chris/cubepi/tests/test_capability.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_capability.py`:

```python
from cubepi.providers.capability import (
    CapabilityDescriptor,
    ReasoningLevelSpec,
    TemperatureSpec,
)


def test_descriptor_defaults_are_legacy_safe():
    """Empty descriptor must encode 'no-op' so existing callers behave the same."""
    cap = CapabilityDescriptor()
    assert cap.reasoning_off_payload == {}
    assert cap.reasoning_on_payload == {}
    assert cap.reasoning_level is None
    assert cap.temperature.mode == "free"
    assert cap.temperature.min == 0.0
    assert cap.temperature.max == 2.0
    assert cap.max_tokens_field == "max_tokens"
    assert cap.supports_tools is True
    assert cap.supports_images is False
    assert cap.supports_streaming is True


def test_temperature_fixed_requires_value():
    """mode=fixed without fixed_value must fail validation."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TemperatureSpec(mode="fixed")


def test_reasoning_level_int_budget_requires_map():
    """kind=int_budget without level_budgets must fail."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReasoningLevelSpec(path="thinking.budget_tokens", kind="int_budget")


def test_reasoning_level_effort_requires_map():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReasoningLevelSpec(path="reasoning_effort", kind="effort")


def test_reasoning_level_enum_requires_map():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReasoningLevelSpec(path="extra_body.thinking.type", kind="enum")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_capability.py -v
```

Expected: collection error / ModuleNotFoundError for `cubepi.providers.capability`.

- [ ] **Step 3: Write minimal implementation**

Create `cubepi/providers/capability.py`:

```python
"""Capability descriptor — vendor quirks expressed as data, bound to a Provider.

See docs/dev/specs/2026-05-19-llm-provider-platform-design.md §3.1.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class TemperatureSpec(BaseModel):
    mode: Literal["free", "fixed", "ignored"] = "free"
    min: float = 0.0
    max: float = 2.0
    default: float = 1.0
    fixed_value: float | None = None

    @model_validator(mode="after")
    def _validate_fixed(self) -> "TemperatureSpec":
        if self.mode == "fixed" and self.fixed_value is None:
            raise ValueError("TemperatureSpec(mode='fixed') requires fixed_value")
        return self


class ReasoningLevelSpec(BaseModel):
    """How to express a fine-grain reasoning level on this endpoint."""

    path: str
    kind: Literal["int_budget", "effort", "enum"]
    level_budgets: dict[str, int] | None = None
    level_to_effort: dict[str, str] | None = None
    level_to_enum: dict[str, str] | None = None

    @model_validator(mode="after")
    def _validate_kind_map(self) -> "ReasoningLevelSpec":
        if self.kind == "int_budget" and not self.level_budgets:
            raise ValueError("kind='int_budget' requires level_budgets")
        if self.kind == "effort" and not self.level_to_effort:
            raise ValueError("kind='effort' requires level_to_effort")
        if self.kind == "enum" and not self.level_to_enum:
            raise ValueError("kind='enum' requires level_to_enum")
        return self


class CapabilityDescriptor(BaseModel):
    """Vendor quirks for one endpoint. Empty default = legacy no-op."""

    reasoning_off_payload: dict[str, Any] = Field(default_factory=dict)
    reasoning_on_payload: dict[str, Any] = Field(default_factory=dict)
    reasoning_level: ReasoningLevelSpec | None = None

    temperature: TemperatureSpec = Field(default_factory=TemperatureSpec)

    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"

    supports_tools: bool = True
    supports_images: bool = False
    supports_streaming: bool = True
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_capability.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubepi
git checkout -b feat/capability-descriptor
git add cubepi/providers/capability.py tests/test_capability.py
git commit -m "feat(capability): introduce CapabilityDescriptor with validated defaults"
```

---

## Task 2: `merge_capability_payload` — three rules

**Files:**
- Modify: `/home/chris/cubepi/cubepi/providers/capability.py`
- Test: `/home/chris/cubepi/tests/test_capability.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_capability.py`:

```python
from cubepi.providers.capability import merge_capability_payload


def test_merge_empty_patch_is_noop():
    kwargs = {"a": 1, "extra_body": {"b": 2}}
    merge_capability_payload(kwargs, {})
    assert kwargs == {"a": 1, "extra_body": {"b": 2}}


def test_merge_adds_new_top_level_keys():
    kwargs = {"a": 1}
    merge_capability_payload(kwargs, {"reasoning_effort": "low"})
    assert kwargs == {"a": 1, "reasoning_effort": "low"}


def test_merge_recurses_into_nested_dicts():
    kwargs: dict = {"extra_body": {"existing": True}}
    merge_capability_payload(kwargs, {"extra_body": {"enable_thinking": False}})
    assert kwargs == {"extra_body": {"existing": True, "enable_thinking": False}}


def test_merge_capability_wins_on_leaf_collision():
    kwargs = {"extra_body": {"enable_thinking": True}}
    merge_capability_payload(kwargs, {"extra_body": {"enable_thinking": False}})
    assert kwargs == {"extra_body": {"enable_thinking": False}}


def test_merge_arrays_are_atomic_capability_wins():
    """Arrays at colliding keys are replaced, not unioned."""
    kwargs = {"stop": ["\n", "."]}
    merge_capability_payload(kwargs, {"stop": ["END"]})
    assert kwargs == {"stop": ["END"]}


def test_merge_does_not_mutate_patch():
    """The patch dict the caller passes in must be left untouched."""
    patch = {"extra_body": {"enable_thinking": False}}
    kwargs: dict = {}
    merge_capability_payload(kwargs, patch)
    kwargs["extra_body"]["enable_thinking"] = True
    assert patch == {"extra_body": {"enable_thinking": False}}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_capability.py -v
```

Expected: `ImportError: cannot import name 'merge_capability_payload'`.

- [ ] **Step 3: Implement**

Append to `cubepi/providers/capability.py`:

```python
def merge_capability_payload(kwargs: dict[str, Any], patch: dict[str, Any]) -> None:
    """Deep-merge ``patch`` into ``kwargs`` in place.

    Rules (spec §3.3):
    1. Recurse into nested dicts.
    2. Arrays are atomic — capability replaces caller's array on collision.
    3. On scalar / array key collision, capability (``patch``) wins.
    4. Patch is never mutated; nested dicts are copied on write.
    """

    for key, patch_value in patch.items():
        if (
            key in kwargs
            and isinstance(kwargs[key], dict)
            and isinstance(patch_value, dict)
        ):
            merge_capability_payload(kwargs[key], patch_value)
        elif isinstance(patch_value, dict):
            # Copy nested dict so subsequent kwargs mutation doesn't bleed into patch.
            kwargs[key] = {}
            merge_capability_payload(kwargs[key], patch_value)
        else:
            kwargs[key] = patch_value
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_capability.py -v
```

Expected: all 11 tests pass (5 prior + 6 new).

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubepi
git add cubepi/providers/capability.py tests/test_capability.py
git commit -m "feat(capability): merge_capability_payload deep-merge with array atomicity"
```

---

## Task 3: `apply_temperature` — free / fixed / ignored

**Files:**
- Modify: `/home/chris/cubepi/cubepi/providers/capability.py`
- Test: `/home/chris/cubepi/tests/test_capability.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_capability.py`:

```python
from cubepi.providers.capability import apply_temperature


def test_apply_temperature_free_passes_through():
    kwargs = {"temperature": 0.7}
    apply_temperature(kwargs, TemperatureSpec(mode="free"))
    assert kwargs == {"temperature": 0.7}


def test_apply_temperature_free_clamps_above_max():
    kwargs = {"temperature": 5.0}
    apply_temperature(kwargs, TemperatureSpec(mode="free", min=0, max=2))
    assert kwargs == {"temperature": 2.0}


def test_apply_temperature_free_clamps_below_min():
    kwargs = {"temperature": -1.0}
    apply_temperature(kwargs, TemperatureSpec(mode="free", min=0, max=2))
    assert kwargs == {"temperature": 0.0}


def test_apply_temperature_ignored_strips():
    kwargs = {"temperature": 0.7}
    apply_temperature(kwargs, TemperatureSpec(mode="ignored"))
    assert "temperature" not in kwargs


def test_apply_temperature_fixed_overwrites():
    kwargs = {"temperature": 0.7}
    apply_temperature(kwargs, TemperatureSpec(mode="fixed", fixed_value=0.0))
    assert kwargs == {"temperature": 0.0}


def test_apply_temperature_fixed_sets_when_absent():
    kwargs: dict = {}
    apply_temperature(kwargs, TemperatureSpec(mode="fixed", fixed_value=0.0))
    assert kwargs == {"temperature": 0.0}


def test_apply_temperature_free_no_op_when_absent():
    kwargs: dict = {}
    apply_temperature(kwargs, TemperatureSpec(mode="free"))
    assert kwargs == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_capability.py -v
```

Expected: `ImportError: cannot import name 'apply_temperature'`.

- [ ] **Step 3: Implement**

Append to `cubepi/providers/capability.py`:

```python
def apply_temperature(kwargs: dict[str, Any], spec: TemperatureSpec) -> None:
    """Mutate ``kwargs['temperature']`` in place per ``spec``.

    - mode="ignored": strip the key entirely.
    - mode="fixed": overwrite with ``fixed_value`` (set the key if absent).
    - mode="free":  clamp caller's value to ``[min, max]``; no-op if absent.
    """

    if spec.mode == "ignored":
        kwargs.pop("temperature", None)
        return

    if spec.mode == "fixed":
        assert spec.fixed_value is not None  # enforced by validator
        kwargs["temperature"] = spec.fixed_value
        return

    if "temperature" in kwargs:
        value = kwargs["temperature"]
        kwargs["temperature"] = max(spec.min, min(spec.max, value))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_capability.py -v
```

Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubepi
git add cubepi/providers/capability.py tests/test_capability.py
git commit -m "feat(capability): apply_temperature with free/fixed/ignored modes"
```

---

## Task 4: `write_reasoning_level` — int_budget / effort / enum

**Files:**
- Modify: `/home/chris/cubepi/cubepi/providers/capability.py`
- Test: `/home/chris/cubepi/tests/test_capability.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_capability.py`:

```python
from cubepi.providers.capability import write_reasoning_level


def test_int_budget_writes_top_level_path():
    kwargs: dict = {}
    spec = ReasoningLevelSpec(
        path="thinking.budget_tokens",
        kind="int_budget",
        level_budgets={"off": 0, "low": 4000, "medium": 10000, "high": 32000},
    )
    write_reasoning_level(kwargs, spec, "medium")
    assert kwargs == {"thinking": {"budget_tokens": 10000}}


def test_int_budget_skips_when_level_absent_in_map():
    kwargs: dict = {}
    spec = ReasoningLevelSpec(
        path="thinking.budget_tokens",
        kind="int_budget",
        level_budgets={"low": 4000},
    )
    write_reasoning_level(kwargs, spec, "xhigh")
    assert kwargs == {}


def test_effort_writes_string():
    kwargs: dict = {}
    spec = ReasoningLevelSpec(
        path="reasoning_effort",
        kind="effort",
        level_to_effort={"low": "low", "medium": "medium", "high": "high"},
    )
    write_reasoning_level(kwargs, spec, "high")
    assert kwargs == {"reasoning_effort": "high"}


def test_enum_writes_nested_extra_body():
    kwargs: dict = {}
    spec = ReasoningLevelSpec(
        path="extra_body.thinking.type",
        kind="enum",
        level_to_enum={"off": "disabled", "low": "enabled", "medium": "enabled", "high": "enabled"},
    )
    write_reasoning_level(kwargs, spec, "medium")
    assert kwargs == {"extra_body": {"thinking": {"type": "enabled"}}}


def test_writes_into_existing_nested_dict():
    kwargs: dict = {"extra_body": {"other": True}}
    spec = ReasoningLevelSpec(
        path="extra_body.thinking.type",
        kind="enum",
        level_to_enum={"off": "disabled", "medium": "enabled"},
    )
    write_reasoning_level(kwargs, spec, "off")
    assert kwargs == {"extra_body": {"other": True, "thinking": {"type": "disabled"}}}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_capability.py -v
```

Expected: `ImportError: cannot import name 'write_reasoning_level'`.

- [ ] **Step 3: Implement**

Append to `cubepi/providers/capability.py`:

```python
from cubepi.providers.base import ThinkingLevel


def _resolve_level_value(spec: ReasoningLevelSpec, level: ThinkingLevel) -> Any | None:
    """Return the wire value for ``level`` per ``spec``, or None to skip writing."""
    if spec.kind == "int_budget":
        assert spec.level_budgets is not None
        return spec.level_budgets.get(level)
    if spec.kind == "effort":
        assert spec.level_to_effort is not None
        return spec.level_to_effort.get(level)
    if spec.kind == "enum":
        assert spec.level_to_enum is not None
        return spec.level_to_enum.get(level)
    return None


def _write_dotted_path(target: dict[str, Any], path: str, value: Any) -> None:
    """Walk a dotted path into ``target``, creating dicts as needed; set the leaf."""
    parts = path.split(".")
    cursor: Any = target
    for part in parts[:-1]:
        if part not in cursor or not isinstance(cursor[part], dict):
            cursor[part] = {}
        cursor = cursor[part]
    cursor[parts[-1]] = value


def write_reasoning_level(
    kwargs: dict[str, Any],
    spec: ReasoningLevelSpec,
    level: ThinkingLevel,
) -> None:
    """Write the resolved wire value for ``level`` at ``spec.path`` inside ``kwargs``.

    If the level is not in the spec's level map (i.e. unsupported), no write happens.
    """
    value = _resolve_level_value(spec, level)
    if value is None:
        return
    _write_dotted_path(kwargs, spec.path, value)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_capability.py -v
```

Expected: 23 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubepi
git add cubepi/providers/capability.py tests/test_capability.py
git commit -m "feat(capability): write_reasoning_level for int_budget/effort/enum"
```

---

## Task 5: OpenAIProvider — accept `capability` + `model_capability_overrides`

**Files:**
- Modify: `/home/chris/cubepi/cubepi/providers/openai.py`
- Test: `/home/chris/cubepi/tests/providers/test_openai_capability.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/providers/test_openai_capability.py`:

```python
"""OpenAIProvider — capability descriptor wiring.

Each test constructs a provider with a known capability, then either
calls a small helper or runs a fake-streamed request, and asserts on
the kwargs the SDK would receive.
"""

import pytest

from cubepi.providers.capability import CapabilityDescriptor, TemperatureSpec
from cubepi.providers.openai import OpenAIProvider


def test_provider_accepts_capability_kwarg():
    cap = CapabilityDescriptor(temperature=TemperatureSpec(mode="ignored"))
    p = OpenAIProvider(api_key="x", base_url="http://example", capability=cap)
    assert p._capability is cap


def test_provider_accepts_model_overrides():
    cap = CapabilityDescriptor()
    overrides = {"deepseek-r1": CapabilityDescriptor(reasoning_off_payload={"reasoning": {"effort": "low"}})}
    p = OpenAIProvider(
        api_key="x",
        base_url="http://example",
        capability=cap,
        model_capability_overrides=overrides,
    )
    assert p._model_overrides == overrides


def test_resolve_capability_uses_override_when_present():
    base = CapabilityDescriptor()
    override = CapabilityDescriptor(reasoning_off_payload={"reasoning": {"effort": "low"}})
    p = OpenAIProvider(
        api_key="x",
        base_url="http://example",
        capability=base,
        model_capability_overrides={"deepseek-r1": override},
    )
    assert p._resolve_capability("deepseek-r1") is override
    assert p._resolve_capability("llama-3") is base


def test_capability_default_when_kwarg_none():
    p = OpenAIProvider(api_key="x", base_url="http://example")
    # No capability passed -> legacy no-op default, _cap_active=False
    assert isinstance(p._capability, CapabilityDescriptor)
    assert p._capability.reasoning_off_payload == {}
    assert p._cap_active is False


def test_cap_active_when_capability_passed():
    p = OpenAIProvider(api_key="x", base_url="http://example", capability=CapabilityDescriptor())
    assert p._cap_active is True


def test_cap_active_when_only_overrides_passed():
    p = OpenAIProvider(
        api_key="x", base_url="http://example",
        model_capability_overrides={"m": CapabilityDescriptor()},
    )
    assert p._cap_active is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_openai_capability.py -v
```

Expected: TypeError / unexpected keyword argument `capability`.

- [ ] **Step 3: Modify OpenAIProvider.__init__**

Edit `cubepi/providers/openai.py`. Find the `__init__` signature
(currently around line 35) and extend it:

```python
def __init__(
    self,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    payload_quirks: list[Literal["max_completion_tokens_alias"]] | None = None,
    extra_body: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
    capability: "CapabilityDescriptor | None" = None,
    model_capability_overrides: "dict[str, CapabilityDescriptor] | None" = None,
) -> None:
    super().__init__()
    import openai

    kwargs: dict[str, Any] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    if extra_headers:
        kwargs["default_headers"] = extra_headers
    self._client = openai.AsyncOpenAI(**kwargs)
    self._payload_quirks: set[str] = set(payload_quirks or [])
    self._extra_body: dict[str, Any] = extra_body or {}
    from cubepi.providers.capability import CapabilityDescriptor as _Cap
    # Track whether capability was explicitly passed so the OpenAI path
    # (which today injects no temperature / no max_tokens) can stay
    # behavior-identical for legacy callers. Spec §3.5.
    self._cap_active: bool = (
        capability is not None or model_capability_overrides is not None
    )
    self._capability: _Cap = capability or _Cap()
    self._model_overrides: dict[str, _Cap] = model_capability_overrides or {}

def _resolve_capability(self, model_id: str) -> "CapabilityDescriptor":
    return self._model_overrides.get(model_id, self._capability)
```

Add the top-of-file import:

```python
from cubepi.providers.capability import CapabilityDescriptor
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_openai_capability.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run full openai test suite to verify nothing broke**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_openai.py tests/providers/test_openai_extras_and_schema.py tests/providers/test_openai_reasoning.py -v
```

Expected: all existing tests pass (no behavior change yet).

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubepi
git add cubepi/providers/openai.py tests/providers/test_openai_capability.py
git commit -m "feat(openai): accept capability + model_capability_overrides kwargs"
```

---

## Task 6: OpenAIProvider.stream — apply temperature + max_tokens rename

**Files:**
- Modify: `/home/chris/cubepi/cubepi/providers/openai.py`
- Test: `/home/chris/cubepi/tests/providers/test_openai_capability.py`

We capture the payload via the existing `request_listeners` hook —
`BaseProvider._fire_request_listeners` runs after all kwargs mutations,
exactly the inspection point this test needs.

- [ ] **Step 1: Write the failing test**

Append to `tests/providers/test_openai_capability.py`:

```python
import asyncio

from cubepi.providers.base import (
    Model,
    StreamOptions,
    TextContent,
    UserMessage,
)


async def _capture_payload_openai(provider: OpenAIProvider, model: Model) -> dict:
    """Run a stream with a fake openai client and return the kwargs sent."""
    captured: dict = {}

    async def listener(kwargs: dict, m: Model) -> None:
        captured.update(kwargs)

    provider._request_listeners.append(listener)

    # Stub the openai client so it doesn't try to actually call the network.
    class _FakeResponse:
        response = None

        def __aiter__(self):
            async def gen():
                return
                yield  # never
            return gen()

    async def fake_create(**kw):
        return _FakeResponse()

    provider._client.chat.completions.create = fake_create  # type: ignore[assignment]
    stream = await provider.stream(
        model=model,
        messages=[UserMessage(content=[TextContent(text="hi")])],
        options=StreamOptions(thinking="off"),
    )
    async for _ in stream:
        pass
    return captured


def _model(id: str = "test-model", **kw) -> Model:
    return Model(
        id=id,
        provider="test",
        context_window=kw.get("context_window", 32000),
        max_tokens=kw.get("max_tokens", 4096),
        temperature=kw.get("temperature", 0.7),
    )


@pytest.mark.asyncio
async def test_temperature_ignored_strips_field():
    cap = CapabilityDescriptor(temperature=TemperatureSpec(mode="ignored"))
    p = OpenAIProvider(api_key="x", base_url="http://e", capability=cap)
    payload = await _capture_payload_openai(p, _model())
    assert "temperature" not in payload


@pytest.mark.asyncio
async def test_temperature_fixed_overwrites():
    cap = CapabilityDescriptor(temperature=TemperatureSpec(mode="fixed", fixed_value=0.0))
    p = OpenAIProvider(api_key="x", base_url="http://e", capability=cap)
    payload = await _capture_payload_openai(p, _model(temperature=0.7))
    assert payload["temperature"] == 0.0


@pytest.mark.asyncio
async def test_max_tokens_field_renamed():
    cap = CapabilityDescriptor(max_tokens_field="max_completion_tokens")
    p = OpenAIProvider(api_key="x", base_url="http://e", capability=cap)
    payload = await _capture_payload_openai(p, _model())
    assert "max_completion_tokens" in payload
    assert "max_tokens" not in payload


@pytest.mark.asyncio
async def test_legacy_no_capability_does_not_inject_temperature_or_max_tokens():
    """Regression guard: no capability passed -> wire bytes identical to today."""
    p = OpenAIProvider(api_key="x", base_url="http://e")  # no capability
    payload = await _capture_payload_openai(p, _model())
    assert "temperature" not in payload
    assert "max_tokens" not in payload
```

Note: the existing OpenAIProvider does not currently set
`temperature` or `max_tokens` in `kwargs` — they only get there via
`opts.on_payload` or `extra_body`. The Step 3 implementation will add
**capability-driven** writes of these fields (read from `model`) so
the tests above describe the new behavior. The exact source for the
default temperature value is `model.temperature`.

Looking at `cubepi/providers/base.py`'s `Model` definition: `Model`
already has a `temperature` field (used by AnthropicProvider). Confirm
it before implementing.

- [ ] **Step 2: Verify `Model.temperature` exists**

```bash
cd /home/chris/cubepi && grep -nE "^\s+temperature" cubepi/providers/base.py | head
```

Expected: a line `temperature: float = 0.7` inside the `Model` class
(already present; AnthropicProvider uses it). Do NOT add or change
this — just confirm.

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_openai_capability.py -v
```

Expected: 3 new tests fail (temperature/max_tokens not in payload).

- [ ] **Step 4: Implement capability application in stream()**

Critical: today the OpenAIProvider does NOT inject
`temperature` / `max_tokens` into the base kwargs. Adding them
unconditionally would change wire bytes for every existing caller.
The capability path must therefore be **gated on `self._cap_active`**
(set in Task 5 from the constructor) so callers that didn't pass
`capability` see identical behavior.

Edit `cubepi/providers/openai.py`. Inside the `_produce` inner
function, AFTER the existing `invoke_on_payload` call and the
existing `max_completion_tokens_alias` block (which Task 8 will
delete), add the gated capability application:

```python
from cubepi.providers.capability import (
    apply_temperature,
    merge_capability_payload,
    write_reasoning_level,
)  # actually move these to top-of-file imports

cap = self._resolve_capability(model.id)
if self._cap_active:
    # Inject model defaults only when a caller opted into capability.
    # setdefault preserves anything on_payload / extra_body already set.
    kwargs.setdefault("temperature", model.temperature)
    kwargs.setdefault("max_tokens", model.max_tokens)
    apply_temperature(kwargs, cap.temperature)
    if cap.max_tokens_field != "max_tokens" and "max_tokens" in kwargs:
        kwargs[cap.max_tokens_field] = kwargs.pop("max_tokens")
```

The reasoning blocks (added in Task 7) go inside the same
`if self._cap_active:` body. Leave that placement for now; the Task 7
diff below extends this block.

Do NOT add `temperature` / `max_tokens` to the base `kwargs` literal
above. Today they're absent and must stay absent for legacy callers.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_openai_capability.py -v
```

Expected: all 7 tests in the file pass.

- [ ] **Step 6: Run full openai suite for regressions**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_openai.py tests/providers/test_openai_extras_and_schema.py tests/providers/test_openai_reasoning.py -v
```

Expected: no failures. If the existing `test_openai_reasoning.py`
expects `max_tokens` absent, we'll fix it in Task 9 when retiring the
old `_payload_quirks` path. For now: if it fails, **commit the new
behavior first** (Step 7) then come back; the test will get refactored
in Task 9.

- [ ] **Step 7: Commit**

```bash
cd /home/chris/cubepi
git add cubepi/providers/openai.py cubepi/providers/base.py tests/providers/test_openai_capability.py
git commit -m "feat(openai): apply capability temperature + max_tokens_field rename"
```

---

## Task 7: OpenAIProvider.stream — reasoning off/on payload + level

**Files:**
- Modify: `/home/chris/cubepi/cubepi/providers/openai.py`
- Test: `/home/chris/cubepi/tests/providers/test_openai_capability.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/providers/test_openai_capability.py`:

```python
from cubepi.providers.capability import ReasoningLevelSpec


@pytest.mark.asyncio
async def test_reasoning_off_payload_merged_qwen():
    cap = CapabilityDescriptor(
        reasoning_off_payload={"extra_body": {"enable_thinking": False}},
        reasoning_on_payload={"extra_body": {"enable_thinking": True}},
    )
    p = OpenAIProvider(api_key="x", base_url="http://e", capability=cap)

    payload = await _capture_payload_openai(p, _model())  # default thinking=off
    assert payload["extra_body"]["enable_thinking"] is False


@pytest.mark.asyncio
async def test_reasoning_on_payload_merged_qwen():
    cap = CapabilityDescriptor(
        reasoning_off_payload={"extra_body": {"enable_thinking": False}},
        reasoning_on_payload={"extra_body": {"enable_thinking": True}},
    )
    p = OpenAIProvider(api_key="x", base_url="http://e", capability=cap)
    # capture path uses thinking=off by default; for this test we need thinking=medium
    # so write an explicit variant of the helper inline:
    captured: dict = {}

    async def listener(kw, m):
        captured.update(kw)

    p._request_listeners.append(listener)

    class _FakeResponse:
        response = None

        def __aiter__(self):
            async def gen():
                return
                yield
            return gen()

    async def fake_create(**kw):
        return _FakeResponse()

    p._client.chat.completions.create = fake_create  # type: ignore
    stream = await p.stream(
        model=_model(),
        messages=[UserMessage(content=[TextContent(text="hi")])],
        options=StreamOptions(thinking="medium"),
    )
    async for _ in stream:
        pass
    assert captured["extra_body"]["enable_thinking"] is True


@pytest.mark.asyncio
async def test_reasoning_level_effort_written():
    cap = CapabilityDescriptor(
        reasoning_off_payload={},
        reasoning_on_payload={},
        reasoning_level=ReasoningLevelSpec(
            path="reasoning_effort",
            kind="effort",
            level_to_effort={"low": "low", "medium": "medium", "high": "high"},
        ),
    )
    p = OpenAIProvider(api_key="x", base_url="http://e", capability=cap)

    captured: dict = {}

    async def listener(kw, m):
        captured.update(kw)

    p._request_listeners.append(listener)

    class _FakeResponse:
        response = None

        def __aiter__(self):
            async def gen():
                return
                yield
            return gen()

    async def fake_create(**kw):
        return _FakeResponse()

    p._client.chat.completions.create = fake_create  # type: ignore
    stream = await p.stream(
        model=_model(),
        messages=[UserMessage(content=[TextContent(text="hi")])],
        options=StreamOptions(thinking="medium"),
    )
    async for _ in stream:
        pass
    assert captured["reasoning_effort"] == "medium"


@pytest.mark.asyncio
async def test_model_override_wins_for_reasoning():
    base = CapabilityDescriptor()  # no reasoning toggle
    override = CapabilityDescriptor(
        reasoning_on_payload={"reasoning": {"effort": "low"}},
    )
    p = OpenAIProvider(
        api_key="x",
        base_url="http://e",
        capability=base,
        model_capability_overrides={"deepseek-r1": override},
    )

    captured: dict = {}
    async def listener(kw, m): captured.update(kw)
    p._request_listeners.append(listener)

    class _FakeResponse:
        response = None
        def __aiter__(self):
            async def gen():
                return
                yield
            return gen()
    async def fake_create(**kw): return _FakeResponse()
    p._client.chat.completions.create = fake_create  # type: ignore

    stream = await p.stream(
        model=_model("deepseek-r1"),
        messages=[UserMessage(content=[TextContent(text="hi")])],
        options=StreamOptions(thinking="medium"),
    )
    async for _ in stream:
        pass
    assert captured["reasoning"] == {"effort": "low"}


@pytest.mark.asyncio
async def test_legacy_no_capability_does_not_merge_reasoning_payload():
    """Regression: no capability -> no reasoning_off/on payload write."""
    p = OpenAIProvider(api_key="x", base_url="http://e")  # legacy
    captured: dict = {}
    async def listener(kw, m): captured.update(kw)
    p._request_listeners.append(listener)

    class _FakeResponse:
        response = None
        def __aiter__(self):
            async def gen():
                return
                yield
            return gen()
    async def fake_create(**kw): return _FakeResponse()
    p._client.chat.completions.create = fake_create  # type: ignore

    stream = await p.stream(
        model=_model(),
        messages=[UserMessage(content=[TextContent(text="hi")])],
        options=StreamOptions(thinking="off"),
    )
    async for _ in stream:
        pass
    assert "extra_body" not in captured  # no merge fired
    assert "reasoning_effort" not in captured
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_openai_capability.py -v
```

Expected: 5 new tests fail (4 capability tests + the legacy guard,
which already passes — it just asserts the absence of fields that
would only appear after Step 3 wires the merge correctly).

- [ ] **Step 3: Implement reasoning application**

Edit `cubepi/providers/openai.py` inside `_produce`. The reasoning
block goes **inside the same `if self._cap_active:` body** introduced
in Task 6 (immediately after the temperature / max_tokens-rename
lines), so legacy callers with no capability see no reasoning
payload merge:

```python
if self._cap_active:
    # ...temperature + max_tokens rename from Task 6 above...
    if opts.thinking == "off":
        merge_capability_payload(kwargs, cap.reasoning_off_payload)
    else:
        merge_capability_payload(kwargs, cap.reasoning_on_payload)
        if cap.reasoning_level is not None:
            write_reasoning_level(kwargs, cap.reasoning_level, opts.thinking)
```

`merge_capability_payload` and `write_reasoning_level` should be
imported at top-of-file alongside `apply_temperature` (one combined
import line).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_openai_capability.py -v
```

Expected: all 11 tests pass.

- [ ] **Step 5: Run full openai suite again**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_openai.py tests/providers/test_openai_extras_and_schema.py tests/providers/test_openai_reasoning.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubepi
git add cubepi/providers/openai.py tests/providers/test_openai_capability.py
git commit -m "feat(openai): apply capability reasoning_off/on payload + level"
```

---

## Task 8: Retire `_payload_quirks` in favor of `capability.max_tokens_field`

**Files:**
- Modify: `/home/chris/cubepi/cubepi/providers/openai.py`
- Modify: `/home/chris/cubepi/tests/providers/test_openai.py` (if it
  references the quirk)
- Test: `/home/chris/cubepi/tests/providers/test_openai_capability.py`

- [ ] **Step 1: Find callers of `payload_quirks` / `max_completion_tokens_alias`**

```bash
cd /home/chris/cubepi && grep -rn "payload_quirks\|max_completion_tokens_alias" cubepi tests
```

Note every hit — those tests / callers will move to the capability path.

- [ ] **Step 2: Write a passing-state test for capability-only path**

This case is already covered by `test_max_tokens_field_renamed` from
Task 6. No new test needed — we just need to confirm the old test
still passes after removing the `_payload_quirks` set.

- [ ] **Step 3: Remove `_payload_quirks` from OpenAIProvider**

Edit `cubepi/providers/openai.py`:

1. Remove the `payload_quirks` kwarg from `__init__`.
2. Remove `self._payload_quirks = ...`.
3. Inside `_produce`, remove the block:
   ```python
   if "max_completion_tokens_alias" in self._payload_quirks:
       if "max_completion_tokens" in kwargs:
           kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
   ```
4. Remove the now-unused `Literal["max_completion_tokens_alias"]` import.

- [ ] **Step 4: Update existing tests / callers that used the quirk**

For every hit from Step 1, replace `payload_quirks=["max_completion_tokens_alias"]`
with `capability=CapabilityDescriptor(max_tokens_field="max_completion_tokens")`.
Note: the rename direction is inverted — the old quirk renamed
`max_completion_tokens` → `max_tokens`; the new field selects what
name to USE. The presets reflect the actual wire convention.

The most likely caller is `cubeplex/backend/cubeplex/llm/factory.py`,
but we are not changing cubeplex in this slice. Confirm cubepi has no
internal callers other than tests:

```bash
cd /home/chris/cubepi && grep -rn "payload_quirks" cubepi tests
```

Expected: only test files.

- [ ] **Step 5: Run all openai tests**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_openai.py tests/providers/test_openai_extras_and_schema.py tests/providers/test_openai_reasoning.py tests/providers/test_openai_capability.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubepi
git add cubepi/providers/openai.py tests/
git commit -m "refactor(openai): retire _payload_quirks; use capability.max_tokens_field"
```

---

## Task 9: OpenAIResponsesProvider — same capability wiring

**Files:**
- Modify: `/home/chris/cubepi/cubepi/providers/openai_responses.py`
- Test: `/home/chris/cubepi/tests/providers/test_openai_responses_capability.py` (new)

- [ ] **Step 1: Locate the equivalent stream() body**

```bash
cd /home/chris/cubepi && grep -n "async def stream\|kwargs: dict\|reasoning\|invoke_on_payload" cubepi/providers/openai_responses.py | head -20
```

This Provider already honors `opts.thinking` via a hardcoded
`_THINKING_TO_EFFORT` map (`reasoning.effort`). Migrate it to read
from capability.

- [ ] **Step 2: Write the failing test**

Create `tests/providers/test_openai_responses_capability.py` mirroring
the OpenAI test structure (constructor accepts kwargs; capability
applies). Capture payload via `_request_listeners`. Key cases:

1. `capability=None` default → behaves like today (effort applied per
   existing internal map).
2. `capability` with `reasoning_level=ReasoningLevelSpec(path="reasoning.effort", kind="effort", level_to_effort={...})`
   → custom map honored.
3. `capability.temperature(mode="ignored")` → strips temperature.

Write the three tests now. Use the same fake-client pattern as Task 6.

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_openai_responses_capability.py -v
```

- [ ] **Step 4: Modify OpenAIResponsesProvider**

In `cubepi/providers/openai_responses.py`:
1. Add `capability`, `model_capability_overrides` kwargs to `__init__`
   (same shape as OpenAIProvider, Task 5). Set
   `self._cap_active = capability is not None or model_capability_overrides is not None`
   for the same legacy-preservation reason.
2. Add `_resolve_capability(model_id)` helper.
3. In `stream()`, gate all capability application on
   `if self._cap_active:` — temperature constraint, max_tokens-field
   rename (Responses uses `max_output_tokens` natively; the rename
   targets that field), and reasoning level write via
   `write_reasoning_level(kwargs, cap.reasoning_level, opts.thinking)`
   when `cap.reasoning_level is not None`.
4. When `self._cap_active is False`, leave the existing inline
   `_THINKING_TO_EFFORT[opts.thinking]` write intact so legacy callers
   see byte-identical behavior. (Task 11 / a follow-up can migrate
   the legacy path to the catalog preset; this slice doesn't.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_openai_responses_capability.py tests/providers/test_openai_responses.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubepi
git add cubepi/providers/openai_responses.py tests/providers/test_openai_responses_capability.py
git commit -m "feat(openai-responses): wire CapabilityDescriptor; reasoning level via capability"
```

---

## Task 10: AnthropicProvider — capability path with int_budget level

**Files:**
- Modify: `/home/chris/cubepi/cubepi/providers/anthropic.py`
- Test: `/home/chris/cubepi/tests/providers/test_anthropic_capability.py` (new)

The Anthropic provider has the most intricate existing logic
(`clamp_thinking_level`, `adjust_max_tokens_for_thinking`, temperature
disabled-when-thinking rule). The plan is conservative: keep all
existing tests green by mapping the default Anthropic capability
descriptor to behavior identical to today.

- [ ] **Step 1: Define the Anthropic-default capability**

Anthropic native wire expects:
- `thinking: {"type": "enabled"|"disabled", "budget_tokens": int}`
- `temperature` only when thinking is off.

The default Anthropic descriptor (constructed when caller passes
`capability=None`) must produce this:

```python
ANTHROPIC_DEFAULT_CAPABILITY = CapabilityDescriptor(
    reasoning_off_payload={"thinking": {"type": "disabled"}},
    reasoning_on_payload={"thinking": {"type": "enabled"}},
    reasoning_level=ReasoningLevelSpec(
        path="thinking.budget_tokens",
        kind="int_budget",
        level_budgets={
            # MUST mirror cubepi.providers.base.ThinkingBudgets defaults.
            # Verified in cubepi/providers/base.py: minimal=1024, low=2048,
            # medium=8192, high=16384. xhigh is clamped to high by
            # clamp_thinking_level() today — preserve that by setting
            # xhigh to the same value as high. ThinkingLevel literal has
            # NO "max" value, so don't add a key that can't trigger.
            "off": 0, "minimal": 1024, "low": 2048,
            "medium": 8192, "high": 16384, "xhigh": 16384,
        },
    ),
    temperature=TemperatureSpec(mode="free", min=0.0, max=1.0, default=1.0),
)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/providers/test_anthropic_capability.py`:

```python
import pytest

from cubepi.providers.capability import CapabilityDescriptor, ReasoningLevelSpec, TemperatureSpec
from cubepi.providers.anthropic import AnthropicProvider
from cubepi.providers.base import (
    Model, StreamOptions, TextContent, UserMessage,
)


def _model() -> Model:
    return Model(
        id="claude-sonnet-test",
        provider="anthropic",
        context_window=200000,
        max_tokens=8192,
        reasoning=True,
        temperature=1.0,
    )


async def _capture_anthropic(p: AnthropicProvider, opts: StreamOptions) -> dict:
    captured: dict = {}
    async def listener(kw, m): captured.update(kw)
    p._request_listeners.append(listener)

    # Stub the anthropic SDK streaming context manager.
    class _FakeStream:
        response = None
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def __aiter__(self):
            async def gen():
                return
                yield
            return gen()
        async def get_final_message(self):
            from anthropic.types import Message
            return Message.model_construct(
                id="m_test", model="claude-sonnet-test", role="assistant",
                content=[], stop_reason="end_turn", stop_sequence=None,
                type="message", usage={"input_tokens": 1, "output_tokens": 1},
            )

    def fake_stream(**kw): return _FakeStream()
    p._client.messages.stream = fake_stream  # type: ignore

    s = await p.stream(
        model=_model(),
        messages=[UserMessage(content=[TextContent(text="hi")])],
        options=opts,
    )
    async for _ in s:
        pass
    return captured


@pytest.mark.asyncio
async def test_default_capability_matches_legacy_thinking_off():
    p = AnthropicProvider(api_key="x")
    payload = await _capture_anthropic(p, StreamOptions(thinking="off"))
    assert payload.get("thinking") == {"type": "disabled"} or "thinking" not in payload
    # Temperature is allowed when thinking off
    assert payload.get("temperature") == 1.0


@pytest.mark.asyncio
async def test_default_capability_thinking_medium_writes_budget():
    p = AnthropicProvider(api_key="x")
    payload = await _capture_anthropic(p, StreamOptions(thinking="medium"))
    assert payload["thinking"]["type"] == "enabled"
    # Mirrors ThinkingBudgets.medium in cubepi/providers/base.py.
    assert payload["thinking"]["budget_tokens"] == 8192
    # Anthropic rejects custom temperature with thinking on — make sure we strip it
    assert "temperature" not in payload


@pytest.mark.asyncio
async def test_custom_capability_overrides_default():
    custom = CapabilityDescriptor(
        reasoning_off_payload={"thinking": {"type": "disabled"}},
        reasoning_on_payload={"thinking": {"type": "enabled"}},
        reasoning_level=ReasoningLevelSpec(
            path="thinking.budget_tokens", kind="int_budget",
            level_budgets={"medium": 99999},
        ),
        temperature=TemperatureSpec(mode="ignored"),
    )
    p = AnthropicProvider(api_key="x", capability=custom)
    payload = await _capture_anthropic(p, StreamOptions(thinking="medium"))
    assert payload["thinking"]["budget_tokens"] == 99999
    assert "temperature" not in payload
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_anthropic_capability.py -v
```

Expected: TypeError on `capability=` kwarg.

- [ ] **Step 4: Modify AnthropicProvider**

In `cubepi/providers/anthropic.py`:

1. Add `capability` + `model_capability_overrides` to `__init__`. When
   `capability` is None, instantiate the module-level
   `_ANTHROPIC_DEFAULT_CAPABILITY` (defined below).
2. Define the default at module top:
   ```python
   _ANTHROPIC_DEFAULT_CAPABILITY = CapabilityDescriptor(...)  # as in Step 1
   ```
3. Add `_resolve_capability(model_id)` helper.
4. In `stream()`, replace the existing thinking-handling block (the
   `clamp_thinking_level` call and the `adjust_max_tokens_for_thinking`
   block and the `if thinking != "off":` write) with:
   ```python
   cap = self._resolve_capability(model.id)
   if opts.thinking == "off":
       merge_capability_payload(kwargs, cap.reasoning_off_payload)
       apply_temperature(kwargs, cap.temperature)
   else:
       merge_capability_payload(kwargs, cap.reasoning_on_payload)
       if cap.reasoning_level is not None:
           write_reasoning_level(kwargs, cap.reasoning_level, opts.thinking)
       # When thinking on, Anthropic rejects custom temperature → strip.
       kwargs.pop("temperature", None)
   ```
5. Keep `clamp_thinking_level` import only if other callers use it; if
   not, remove the import. Same for `adjust_max_tokens_for_thinking`.

- [ ] **Step 5: Run all Anthropic tests**

```bash
cd /home/chris/cubepi && uv run pytest tests/providers/test_anthropic.py tests/providers/test_anthropic_cache_policy.py tests/providers/test_thinking_budgets.py tests/providers/test_anthropic_capability.py -v
```

Expected: all pass. If `test_thinking_budgets.py` was testing the
`adjust_max_tokens_for_thinking` helper directly, it still works
because we haven't removed that helper. The integration test
behavior continues to match.

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubepi
git add cubepi/providers/anthropic.py tests/providers/test_anthropic_capability.py
git commit -m "feat(anthropic): drive thinking + temperature via CapabilityDescriptor"
```

---

## Task 11: Export new types from `cubepi.__init__`

**Files:**
- Modify: `/home/chris/cubepi/cubepi/__init__.py`
- Test: `/home/chris/cubepi/tests/test_init.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_init.py`:

```python
def test_capability_types_re_exported():
    import cubepi

    assert hasattr(cubepi, "CapabilityDescriptor")
    assert hasattr(cubepi, "TemperatureSpec")
    assert hasattr(cubepi, "ReasoningLevelSpec")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_init.py::test_capability_types_re_exported -v
```

- [ ] **Step 3: Add exports**

Edit `cubepi/__init__.py`. Append:

```python
from cubepi.providers.capability import (
    CapabilityDescriptor,
    ReasoningLevelSpec,
    TemperatureSpec,
)

__all__ = list(__all__) + [
    "CapabilityDescriptor",
    "ReasoningLevelSpec",
    "TemperatureSpec",
]
```

(If the existing `__all__` is a tuple or absent, adjust the syntax
accordingly.)

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_init.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubepi
git add cubepi/__init__.py tests/test_init.py
git commit -m "feat(capability): export CapabilityDescriptor + helpers from cubepi"
```

---

## Task 12: Catalog types — `ProviderPreset`, `ModelPreset`, `AuthSpec`

**Files:**
- Create: `/home/chris/cubepi/cubepi/providers/catalog/__init__.py` (placeholder)
- Create: `/home/chris/cubepi/cubepi/providers/catalog/types.py`
- Test: `/home/chris/cubepi/tests/test_catalog.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_catalog.py`:

```python
from cubepi.providers.catalog.types import (
    AuthSpec, ModelPreset, ProviderPreset, WireApi,
)
from cubepi.providers.capability import CapabilityDescriptor, TemperatureSpec


def test_wire_api_values():
    assert WireApi.__args__ == ("anthropic-messages", "openai-completions", "openai-responses")


def test_minimal_provider_preset_constructs():
    p = ProviderPreset(
        slug="custom-openai",
        display_name="Custom OpenAI",
        short_name="Custom",
        category="custom",
        description="",
        api="openai-completions",
        base_url="https://example.com/v1",
        auth=AuthSpec(mode="api_key"),
        capability=CapabilityDescriptor(),
        default_models=[],
    )
    assert p.slug == "custom-openai"
    assert p.model_capability_overrides == {}
    assert p.logo is None  # custom presets default to no brand mark


def test_model_preset_minimal():
    m = ModelPreset(
        model_id="gpt-4o", display_name="GPT-4o",
        context_window=128000, max_tokens=16384,
        input_modalities=["text", "image"],
    )
    assert m.reasoning is False


def test_auth_spec_api_key_defaults():
    a = AuthSpec(mode="api_key")
    assert a.header_name in (None, "Authorization")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_catalog.py -v
```

- [ ] **Step 3: Implement**

Create `cubepi/providers/catalog/__init__.py` (empty placeholder for now):

```python
"""Provider preset catalog.

See docs/dev/specs/2026-05-19-llm-provider-platform-design.md §3.6.
"""
```

Create `cubepi/providers/catalog/types.py`:

```python
"""Catalog types: ProviderPreset, ModelPreset, AuthSpec, WireApi."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from cubepi.providers.capability import CapabilityDescriptor

WireApi = Literal["anthropic-messages", "openai-completions", "openai-responses"]


class AuthSpec(BaseModel):
    mode: Literal["api_key", "bearer", "none", "oauth", "iam"]
    header_name: str | None = None
    header_prefix: str | None = "Bearer "


class ModelPreset(BaseModel):
    model_id: str
    display_name: str
    context_window: int
    max_tokens: int
    input_modalities: list[str]
    reasoning: bool = False


class ProviderPreset(BaseModel):
    slug: str
    display_name: str
    short_name: str
    category: Literal["saas", "oss-framework", "custom"]
    description: str
    # @lobehub/icons provider id (lowercase, e.g. "anthropic", "openai",
    # "deepseek"). cubeplex frontend renders via
    # <ProviderIcon provider={preset.logo} size=28 type="color" />.
    # None = render generic fallback. Spec §3.6, §7 Q5.
    logo: str | None = None

    api: WireApi
    base_url: str
    auth: AuthSpec

    capability: CapabilityDescriptor
    model_capability_overrides: dict[str, CapabilityDescriptor] = Field(default_factory=dict)

    default_models: list[ModelPreset] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_catalog.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubepi
git add cubepi/providers/catalog/ tests/test_catalog.py
git commit -m "feat(catalog): ProviderPreset / ModelPreset / AuthSpec / WireApi"
```

---

## Task 13: Catalog YAML data file (20 presets)

**Files:**
- Create: `/home/chris/cubepi/cubepi/providers/catalog/data/providers.yaml`
- Modify: `/home/chris/cubepi/pyproject.toml` (ensure `pyyaml` is a
  declared dep)

- [ ] **Step 1: Add pyyaml as an explicit dependency**

pyyaml is not a transitive dep of anthropic or openai — don't rely on
it being present accidentally.

```bash
cd /home/chris/cubepi && uv add pyyaml
```

Verify:

```bash
cd /home/chris/cubepi && uv run python -c "import yaml; print(yaml.__version__)"
```

Expected: prints a 6.x version.

- [ ] **Step 2: Author the YAML**

Create `cubepi/providers/catalog/data/providers.yaml`. Below is the **complete**
20-preset file. Type it verbatim; the test in Task 14 validates every
entry.

```yaml
# Provider preset catalog. Spec: docs/dev/specs/2026-05-19-llm-provider-platform-design.md §3.7
# Each entry parses into cubepi.providers.catalog.types.ProviderPreset.

- slug: anthropic
  display_name: Anthropic
  short_name: Anthropic
  logo: anthropic
  category: saas
  description: Anthropic Claude (Messages API). Native thinking + budget.
  api: anthropic-messages
  base_url: https://api.anthropic.com
  auth: { mode: api_key, header_name: x-api-key, header_prefix: "" }
  capability:
    reasoning_off_payload: { thinking: { type: disabled } }
    reasoning_on_payload:  { thinking: { type: enabled  } }
    reasoning_level:
      path: thinking.budget_tokens
      kind: int_budget
      # Aligned with cubepi.providers.base.ThinkingBudgets so AnthropicProvider's
      # capability=None default-capability reproduces today's wire bytes.
      level_budgets: { off: 0, minimal: 1024, low: 2048, medium: 8192, high: 16384, xhigh: 16384 }
    temperature: { mode: free, min: 0.0, max: 1.0, default: 1.0 }
    max_tokens_field: max_tokens
    supports_tools: true
    supports_images: true
  default_models:
    - { model_id: claude-opus-4-7,    display_name: Claude Opus 4.7,    context_window: 200000, max_tokens: 8192,  input_modalities: [text, image], reasoning: true }
    - { model_id: claude-sonnet-4-6,  display_name: Claude Sonnet 4.6,  context_window: 200000, max_tokens: 8192,  input_modalities: [text, image], reasoning: true }
    - { model_id: claude-haiku-4-5,   display_name: Claude Haiku 4.5,   context_window: 200000, max_tokens: 8192,  input_modalities: [text, image], reasoning: false }

- slug: openai
  display_name: OpenAI
  short_name: OpenAI
  logo: openai
  category: saas
  description: OpenAI Responses API (GPT-5, o-series reasoning).
  api: openai-responses
  base_url: https://api.openai.com/v1
  auth: { mode: api_key }
  capability:
    reasoning_level:
      path: reasoning.effort
      kind: effort
      level_to_effort: { minimal: minimal, low: low, medium: medium, high: high, xhigh: high }
    temperature: { mode: free, min: 0.0, max: 2.0, default: 1.0 }
    max_tokens_field: max_tokens
    supports_tools: true
    supports_images: true
  default_models:
    - { model_id: gpt-5,        display_name: GPT-5,        context_window: 400000, max_tokens: 16384, input_modalities: [text, image], reasoning: true }
    - { model_id: gpt-5-mini,   display_name: GPT-5 Mini,   context_window: 400000, max_tokens: 16384, input_modalities: [text, image], reasoning: true }
    - { model_id: o3-mini,      display_name: o3-mini,      context_window: 200000, max_tokens: 16384, input_modalities: [text],        reasoning: true }

- slug: openai-legacy
  display_name: OpenAI (Chat Completions)
  short_name: OpenAI-Chat
  logo: openai
  category: saas
  description: OpenAI legacy chat/completions wire (GPT-4/3.5).
  api: openai-completions
  base_url: https://api.openai.com/v1
  auth: { mode: api_key }
  capability:
    temperature: { mode: free, min: 0.0, max: 2.0, default: 1.0 }
    max_tokens_field: max_completion_tokens
    supports_tools: true
    supports_images: true
  default_models:
    - { model_id: gpt-4o, display_name: GPT-4o, context_window: 128000, max_tokens: 16384, input_modalities: [text, image], reasoning: false }

- slug: qwen-dashscope
  display_name: 通义千问 (DashScope)
  short_name: Qwen
  logo: qwen
  category: saas
  description: Alibaba Cloud DashScope OpenAI-compatible endpoint for Qwen models.
  api: openai-completions
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
  auth: { mode: api_key }
  capability:
    reasoning_off_payload: { extra_body: { enable_thinking: false } }
    reasoning_on_payload:  { extra_body: { enable_thinking: true  } }
    temperature: { mode: free, min: 0.0, max: 2.0, default: 1.0 }
    supports_tools: true
    supports_images: true
  default_models:
    - { model_id: qwen3.6-plus,  display_name: Qwen 3.6 Plus,  context_window: 256000, max_tokens: 32000, input_modalities: [text, image], reasoning: true }
    - { model_id: qwen3.6-flash, display_name: Qwen 3.6 Flash, context_window: 128000, max_tokens: 16000, input_modalities: [text, image], reasoning: false }

- slug: doubao-volcengine
  display_name: 豆包 (火山方舟)
  short_name: Doubao
  logo: doubao
  category: saas
  description: ByteDance Volcengine OpenAI-compatible endpoint for Doubao models.
  api: openai-completions
  base_url: https://ark.cn-beijing.volces.com/api/v3
  auth: { mode: api_key }
  capability:
    reasoning_off_payload: { extra_body: { thinking: { type: disabled } } }
    reasoning_on_payload:  { extra_body: { thinking: { type: enabled  } } }
    temperature: { mode: free, min: 0.0, max: 2.0, default: 1.0 }
    supports_tools: true
    supports_images: true
  default_models:
    - { model_id: doubao-seed-2.0-pro, display_name: Doubao Seed 2.0 Pro, context_window: 128000, max_tokens: 16384, input_modalities: [text, image], reasoning: true }

- slug: deepseek-anthropic
  display_name: DeepSeek (Anthropic shape)
  short_name: DeepSeek
  logo: deepseek
  category: saas
  description: DeepSeek via its Anthropic-shape endpoint.
  api: anthropic-messages
  base_url: https://api.deepseek.com/anthropic
  auth: { mode: api_key, header_name: x-api-key, header_prefix: "" }
  capability:
    reasoning_off_payload: { thinking: { type: disabled } }
    reasoning_on_payload:  { thinking: { type: enabled } }
    reasoning_level:
      path: thinking.budget_tokens
      kind: int_budget
      # Mirror Anthropic's budgets (same ThinkingBudgets contract).
      level_budgets: { off: 0, low: 2048, medium: 8192, high: 16384, xhigh: 16384 }
    temperature: { mode: free, min: 0.0, max: 1.0, default: 1.0 }
  default_models:
    - { model_id: deepseek-v4-pro, display_name: DeepSeek V4 Pro, context_window: 64000, max_tokens: 12000, input_modalities: [text], reasoning: true }

- slug: deepseek-openai
  display_name: DeepSeek (OpenAI shape)
  short_name: DeepSeek
  logo: deepseek
  category: saas
  description: DeepSeek via its OpenAI chat-completions endpoint.
  api: openai-completions
  base_url: https://api.deepseek.com
  auth: { mode: api_key }
  capability:
    reasoning_off_payload: { extra_body: { reasoning: { exclude: true } } }
    reasoning_on_payload:  { extra_body: { reasoning: { exclude: false } } }
    temperature: { mode: free, min: 0.0, max: 2.0, default: 1.0 }
  default_models:
    - { model_id: deepseek-chat, display_name: DeepSeek Chat, context_window: 64000, max_tokens: 8192, input_modalities: [text], reasoning: false }

- slug: moonshot
  display_name: Moonshot Kimi
  short_name: Moonshot
  logo: moonshot
  category: saas
  description: Moonshot AI Kimi OpenAI-compatible endpoint.
  api: openai-completions
  base_url: https://api.moonshot.cn/v1
  auth: { mode: api_key }
  capability:
    temperature: { mode: free, min: 0.0, max: 2.0, default: 0.7 }
  default_models:
    - { model_id: moonshot-v1-128k, display_name: Kimi 128k, context_window: 128000, max_tokens: 8192, input_modalities: [text], reasoning: false }

- slug: xai
  display_name: xAI
  short_name: xAI
  logo: xai
  category: saas
  description: xAI Grok OpenAI-compatible endpoint.
  api: openai-completions
  base_url: https://api.x.ai/v1
  auth: { mode: api_key }
  capability:
    temperature: { mode: free, min: 0.0, max: 2.0, default: 1.0 }
    supports_tools: true
  default_models:
    - { model_id: grok-4, display_name: Grok 4, context_window: 131072, max_tokens: 16384, input_modalities: [text], reasoning: true }

- slug: mistral
  display_name: Mistral
  short_name: Mistral
  logo: mistral
  category: saas
  description: Mistral La Plateforme OpenAI-compatible.
  api: openai-completions
  base_url: https://api.mistral.ai/v1
  auth: { mode: api_key }
  capability:
    temperature: { mode: free, min: 0.0, max: 1.0, default: 0.7 }
  default_models:
    - { model_id: mistral-large-latest, display_name: Mistral Large, context_window: 131072, max_tokens: 8192, input_modalities: [text], reasoning: false }

- slug: openrouter
  display_name: OpenRouter
  short_name: OpenRouter
  logo: openrouter
  category: saas
  description: OpenRouter unified gateway. Per-model reasoning overrides for known reasoning models.
  api: openai-completions
  base_url: https://openrouter.ai/api/v1
  auth: { mode: api_key }
  capability:
    temperature: { mode: free, min: 0.0, max: 2.0, default: 1.0 }
    supports_tools: true
    supports_images: true
  model_capability_overrides:
    deepseek/deepseek-r1:
      reasoning_off_payload: { reasoning: { exclude: true } }
      reasoning_on_payload:  { reasoning: { exclude: false } }
      reasoning_level:
        path: reasoning.effort
        kind: effort
        level_to_effort: { low: low, medium: medium, high: high }
    openai/o3-mini:
      reasoning_level:
        path: reasoning.effort
        kind: effort
        level_to_effort: { low: low, medium: medium, high: high }
  default_models: []

- slug: together-ai
  display_name: Together AI
  short_name: Together
  logo: together
  category: saas
  description: Together AI OpenAI-compatible.
  api: openai-completions
  base_url: https://api.together.xyz/v1
  auth: { mode: api_key }
  capability:
    temperature: { mode: free, min: 0.0, max: 2.0, default: 0.7 }
  default_models: []

- slug: groq
  display_name: Groq
  short_name: Groq
  logo: groq
  category: saas
  description: Groq high-throughput OpenAI-compatible.
  api: openai-completions
  base_url: https://api.groq.com/openai/v1
  auth: { mode: api_key }
  capability:
    temperature: { mode: free, min: 0.0, max: 2.0, default: 1.0 }
  default_models: []

- slug: fireworks
  display_name: Fireworks AI
  short_name: Fireworks
  logo: fireworks
  category: saas
  description: Fireworks AI OpenAI-compatible.
  api: openai-completions
  base_url: https://api.fireworks.ai/inference/v1
  auth: { mode: api_key }
  capability:
    temperature: { mode: free, min: 0.0, max: 2.0, default: 0.7 }
  default_models: []

- slug: vllm
  display_name: vLLM (self-hosted)
  short_name: vLLM
  logo: vllm
  category: oss-framework
  description: vLLM OpenAI-compatible server. Reasoning conventions depend on the loaded model and reasoning parser plugin.
  api: openai-completions
  base_url: http://localhost:8000/v1
  auth: { mode: api_key }
  capability:
    temperature: { mode: free, min: 0.0, max: 2.0, default: 0.7 }
    supports_tools: false
  default_models: []

- slug: ollama
  display_name: Ollama
  short_name: Ollama
  logo: ollama
  category: oss-framework
  description: Ollama local OpenAI-compatible endpoint.
  api: openai-completions
  base_url: http://localhost:11434/v1
  auth: { mode: none }
  capability:
    temperature: { mode: free, min: 0.0, max: 2.0, default: 0.7 }
    supports_tools: false
  default_models: []

- slug: lm-studio
  display_name: LM Studio
  short_name: LM Studio
  logo: lmstudio
  category: oss-framework
  description: LM Studio local server.
  api: openai-completions
  base_url: http://localhost:1234/v1
  auth: { mode: none }
  capability:
    temperature: { mode: free, min: 0.0, max: 2.0, default: 0.7 }
    supports_tools: false
  default_models: []

- slug: tgi
  display_name: HuggingFace TGI
  short_name: TGI
  logo: huggingface
  category: oss-framework
  description: Text Generation Inference OpenAI-compatible endpoint.
  api: openai-completions
  base_url: http://localhost:8080/v1
  auth: { mode: api_key }
  capability:
    temperature: { mode: free, min: 0.0, max: 2.0, default: 0.7 }
    supports_tools: false
  default_models: []

- slug: custom-openai
  display_name: Custom OpenAI-compatible
  short_name: Custom
  logo: null
  category: custom
  description: Bring your own OpenAI chat-completions endpoint.
  api: openai-completions
  base_url: ""
  auth: { mode: api_key }
  capability:
    temperature: { mode: free, min: 0.0, max: 2.0, default: 1.0 }
  default_models: []

- slug: custom-anthropic
  display_name: Custom Anthropic-compatible
  short_name: Custom
  logo: null
  category: custom
  description: Bring your own Anthropic Messages endpoint.
  api: anthropic-messages
  base_url: ""
  auth: { mode: api_key, header_name: x-api-key, header_prefix: "" }
  capability:
    temperature: { mode: free, min: 0.0, max: 1.0, default: 1.0 }
  default_models: []
```

- [ ] **Step 3: Verify the YAML parses syntactically**

```bash
cd /home/chris/cubepi && uv run python -c "import yaml; yaml.safe_load(open('cubepi/providers/catalog/data/providers.yaml'))"
```

Expected: no exception, no output.

- [ ] **Step 4: Declare YAML as package data**

Hatchling's default wheel builder includes `.py` files but skips
non-Python assets. The catalog YAML must be opted in or
`get_provider_preset()` will silently fail at runtime in installed
wheels. The current `[tool.hatch.build.targets.wheel]` section in
`pyproject.toml` only has `packages = ["cubepi"]`. Extend it to:

```toml
[tool.hatch.build.targets.wheel]
packages = ["cubepi"]
include = ["cubepi/providers/catalog/data/*.yaml"]
```

- [ ] **Step 5: Verify the YAML is reachable via importlib resources**

```bash
cd /home/chris/cubepi && uv run python -c "
from pathlib import Path
import cubepi.providers.catalog as c
f = Path(c.__file__).parent / 'data' / 'providers.yaml'
assert f.is_file(), f'missing: {f}'
print('ok:', f.stat().st_size, 'bytes')
"
```

Expected: prints `ok: <N> bytes`.

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubepi
git add cubepi/providers/catalog/data/providers.yaml pyproject.toml
git commit -m "feat(catalog): bundle 20-entry provider preset YAML"
```

---

## Task 14: Catalog loader — `list_provider_presets`, `get_provider_preset`

**Files:**
- Modify: `/home/chris/cubepi/cubepi/providers/catalog/__init__.py`
- Test: `/home/chris/cubepi/tests/test_catalog.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_catalog.py`:

```python
def test_list_provider_presets_returns_all_entries():
    from cubepi.providers.catalog import list_provider_presets

    presets = list_provider_presets()
    slugs = [p.slug for p in presets]
    # Spot-check the headline entries — the full set is enforced by
    # the parse round-trip below.
    for required in ("anthropic", "openai", "qwen-dashscope",
                     "doubao-volcengine", "openrouter",
                     "custom-openai", "custom-anthropic"):
        assert required in slugs


def test_every_preset_parses_into_typed_model():
    from cubepi.providers.catalog import list_provider_presets
    from cubepi.providers.catalog.types import WireApi

    presets = list_provider_presets()
    assert len(presets) == 20
    valid_apis = WireApi.__args__
    for p in presets:
        assert p.api in valid_apis, p.slug
        assert p.slug == p.slug.lower()
        # capability descriptor must already be typed (validation succeeded)
        assert p.capability.temperature.min <= p.capability.temperature.max


def test_get_provider_preset_by_slug():
    from cubepi.providers.catalog import get_provider_preset

    qwen = get_provider_preset("qwen-dashscope")
    assert qwen.api == "openai-completions"
    assert qwen.capability.reasoning_off_payload == {"extra_body": {"enable_thinking": False}}


def test_get_provider_preset_unknown_raises():
    import pytest
    from cubepi.providers.catalog import get_provider_preset

    with pytest.raises(KeyError):
        get_provider_preset("nonexistent")


def test_openrouter_has_model_capability_overrides():
    from cubepi.providers.catalog import get_provider_preset

    p = get_provider_preset("openrouter")
    assert "deepseek/deepseek-r1" in p.model_capability_overrides
    over = p.model_capability_overrides["deepseek/deepseek-r1"]
    assert over.reasoning_level is not None
    assert over.reasoning_level.kind == "effort"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_catalog.py -v
```

Expected: ImportError on `list_provider_presets`.

- [ ] **Step 3: Implement the loader**

Replace `cubepi/providers/catalog/__init__.py` contents:

```python
"""Provider preset catalog. See spec §3.6."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

from cubepi.providers.catalog.types import ProviderPreset

_DATA_FILE = Path(__file__).parent / "data" / "providers.yaml"


@cache
def _load() -> dict[str, ProviderPreset]:
    with _DATA_FILE.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, list):
        raise ValueError(f"providers.yaml must be a top-level list, got {type(raw)}")
    presets: dict[str, ProviderPreset] = {}
    for idx, entry in enumerate(raw):
        try:
            preset = ProviderPreset.model_validate(entry)
        except Exception as exc:
            raise ValueError(f"providers.yaml entry #{idx}: {exc}") from exc
        if preset.slug in presets:
            raise ValueError(f"providers.yaml: duplicate slug {preset.slug!r}")
        presets[preset.slug] = preset
    return presets


def list_provider_presets() -> list[ProviderPreset]:
    """All registered provider presets, in catalog order."""
    return list(_load().values())


def get_provider_preset(slug: str) -> ProviderPreset:
    """Look up by slug. Raises KeyError if not found."""
    presets = _load()
    if slug not in presets:
        raise KeyError(slug)
    return presets[slug]


__all__ = ["list_provider_presets", "get_provider_preset"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_catalog.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubepi
git add cubepi/providers/catalog/__init__.py tests/test_catalog.py
git commit -m "feat(catalog): YAML loader with per-entry pydantic validation"
```

---

## Task 15: Export catalog API + bump version

**Files:**
- Modify: `/home/chris/cubepi/cubepi/__init__.py`
- Modify: `/home/chris/cubepi/pyproject.toml`
- Test: `/home/chris/cubepi/tests/test_init.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_init.py`:

```python
def test_catalog_re_exported():
    import cubepi

    assert hasattr(cubepi, "list_provider_presets")
    assert hasattr(cubepi, "get_provider_preset")
    presets = cubepi.list_provider_presets()
    assert len(presets) == 20
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/chris/cubepi && uv run pytest tests/test_init.py -v
```

- [ ] **Step 3: Add exports + version bump**

Edit `cubepi/__init__.py` — append to the existing additive block:

```python
from cubepi.providers.catalog import get_provider_preset, list_provider_presets

__all__ = list(__all__) + [
    "list_provider_presets",
    "get_provider_preset",
]
```

Edit `pyproject.toml`:

```toml
version = "0.5.0"  # was 0.4.0
```

- [ ] **Step 4: Run all tests**

```bash
cd /home/chris/cubepi && uv run pytest -q
```

Expected: every existing test plus the new ones pass.

- [ ] **Step 5: Commit + tag**

```bash
cd /home/chris/cubepi
git add cubepi/__init__.py pyproject.toml tests/test_init.py
git commit -m "feat(cubepi): export catalog API; bump 0.4.0 -> 0.5.0"
git tag v0.5.0
```

(Don't push the tag yet — wait for the user's review of the full
slice before tagging the published version.)

---

## Task 16: Final regression sweep

- [ ] **Step 1: Full test run**

```bash
cd /home/chris/cubepi && uv run pytest -q --tb=short
```

Expected: all tests pass; report any failures inline rather than
patching over them.

- [ ] **Step 2: ruff check**

```bash
cd /home/chris/cubepi && uv run ruff check cubepi tests
```

Expected: clean. Address any lints touching files we modified; leave
pre-existing lints in untouched files alone.

- [ ] **Step 3: mypy (if mypy is configured)**

```bash
cd /home/chris/cubepi && (ls mypy.ini pyproject.toml | xargs grep -l "\[tool.mypy\]" 2>/dev/null) \
  && uv run mypy cubepi --no-error-summary --hide-error-context \
  || echo "no mypy config — skipped"
```

Expected: no errors in files we modified. (cubepi has no pre-commit
gate, so this is the only place mypy runs — do not skip if config
exists.)

- [ ] **Step 4: Smoke test the import path**

```bash
cd /home/chris/cubepi && uv run python -c "import cubepi; print(cubepi.list_provider_presets()[0].slug)"
```

Expected: prints `anthropic`.

- [ ] **Step 5: Final commit (only if any earlier step produced edits)**

```bash
cd /home/chris/cubepi
git status
# If clean: skip the commit.
```

- [ ] **Step 6: Open the PR**

```bash
cd /home/chris/cubepi
git push -u origin feat/capability-descriptor
gh pr create --title "feat: CapabilityDescriptor + provider preset catalog" \
  --body "$(cat <<'EOF'
## Summary
- Introduce CapabilityDescriptor — vendor quirks as data, bound to Provider instance.
- All three Provider classes (OpenAI, OpenAIResponses, Anthropic) drive temperature, max_tokens field name, and reasoning toggle through the descriptor.
- model_capability_overrides handles per-model divergence on a shared endpoint (OpenRouter case).
- Retire _payload_quirks string-set hack in favor of capability.max_tokens_field.
- Ship cubepi.providers.catalog with 20 provider presets (Anthropic, OpenAI, Qwen/DashScope, Doubao/Volcengine, DeepSeek both shapes, Moonshot, xAI, Mistral, OpenRouter, Together, Groq, Fireworks, vLLM, Ollama, LM Studio, TGI, Custom OpenAI/Anthropic).

## Test plan
- [ ] Unit tests for merge_capability_payload, apply_temperature, write_reasoning_level
- [ ] OpenAIProvider applies capability fields in stream()
- [ ] OpenAIResponsesProvider applies capability; default behavior preserved when capability=None
- [ ] AnthropicProvider applies capability; existing thinking-budget tests still pass
- [ ] Catalog round-trip: every preset parses, every wire api valid, OpenRouter override map present
- [ ] cubepi.list_provider_presets() / get_provider_preset() round-trip
- [ ] No regressions in existing test_openai*.py / test_anthropic*.py / test_openai_responses.py
EOF
)"
```

---

## Self-Review Notes (filled during writing)

- **Spec §3.1 (CapabilityDescriptor)** → Tasks 1-4 implement the type
  and the three helpers; Task 8 retires `_payload_quirks` as the spec
  requires.
- **Spec §3.2 (model_capability_overrides)** → Task 5 plumbs the
  override map; Task 7's `test_model_override_wins_for_reasoning`
  covers the OpenRouter case end-to-end.
- **Spec §3.3 (merge rules)** → Task 2 spells out the three rules in
  unit tests.
- **Spec §3.4 (runtime flow per Provider)** → Tasks 6-7 (OpenAI), Task
  9 (OpenAIResponses), Task 10 (Anthropic).
- **Spec §3.5 (constructor signature, capability=None default)** →
  Task 5 Step 4 (no-capability test); Task 10 Step 5 (Anthropic
  default capability mirrors legacy behavior).
- **Spec §3.6 + §3.7 (catalog + 20 presets)** → Tasks 12-15.

Skipped vs spec for this slice:
- §4 (cubeplex changes), §6 milestones M3-M7 — follow-up plan.
- §7 open questions — addressed implicitly (Q1 logo: not yet, no logo
  files included; Q2 catalog version pinning: cubeplex will cache
  capability on row in the next slice; Q3 probe cost: M4 plan; Q4
  override editability: M5 plan).

---

**Plan complete and saved to
`docs/dev/plans/2026-05-19-llm-provider-platform.md`.** Two execution
options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent
per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using
executing-plans, batch execution with checkpoints.

**Which approach?**
