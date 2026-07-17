# LLM Snapshot Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `LLMFactory` with a three-module split (`snapshot.py` / `resolver.py` / `builder.py`), wire `cubepi.FallbackBoundModel` into the agent runtime, and add per-message `preset_label` + `thinking` to the chat API. Implements [Spec 1](../specs/2026-06-09-llm-snapshot-refactor-design.md).

**Architecture:** I/O lives only in `snapshot.py` (one async loader that reads DB + OrgSettings once). `resolver.py` and `builder.py` are pure functions operating on a frozen `LLMSnapshot`. YAML's role drops to seeder-only bootstrap. Runtime imports `cubeplex.config` for `llm.*` are deleted.

**Tech Stack:** Python 3.13, FastAPI, SQLModel, alembic, pydantic v2, cubepi 0.9.0 (`FallbackBoundModel`, `FauxProvider`, `ThinkingLevel`).

**Worktree:** `/home/chris/cubeplex/.worktrees/feat/llm-snapshot-refactor` — branch `feat/llm-snapshot-refactor`. Always `cat .worktree.env` first; ports 8019 / 3019; DB `cubeplex_feat_llm_snapshot_refactor` and `cubeplex_test_feat_llm_snapshot_refactor`. Run all commands from `.worktrees/feat/llm-snapshot-refactor/backend/`.

**Test runner:** `uv run pytest <path> -v`. Type-check: `uv run mypy cubeplex/`. Lint: `uv run ruff check cubeplex/`.

**PR structure:** This plan ships as **three PRs**, each independently revertable. Tasks are grouped under Part A / B / C.

---

## Part A — Infrastructure refactor (PR 1, no behavior change)

### Task A1: OrgSettings `model_presets` key constant + pydantic schema

**Files:**
- Modify: `cubeplex/models/org_settings.py`
- Test: `tests/unit/llm/test_preset_schema.py`

- [ ] **Step 1: Write failing schema tests**

Create `tests/unit/llm/test_preset_schema.py`:

```python
"""Pydantic schema for OrgSettings.model_presets row value."""

import pytest
from pydantic import ValidationError

from cubeplex.llm.snapshot_schema import ModelPresetsValue


def _make(label="default", chain=("a/b",), is_default=True):
    return {"label": label, "chain": list(chain), "is_default": is_default}


def test_accepts_minimal_valid_payload():
    ModelPresetsValue.model_validate(
        {"presets": [_make()], "task_presets": {}}
    )


def test_rejects_zero_presets():
    with pytest.raises(ValidationError):
        ModelPresetsValue.model_validate({"presets": [], "task_presets": {}})


def test_rejects_zero_chain_entries():
    with pytest.raises(ValidationError):
        ModelPresetsValue.model_validate(
            {"presets": [_make(chain=())], "task_presets": {}}
        )


def test_rejects_duplicate_labels():
    with pytest.raises(ValidationError, match="label"):
        ModelPresetsValue.model_validate(
            {
                "presets": [_make(label="x"), _make(label="x", is_default=False)],
                "task_presets": {},
            }
        )


def test_rejects_zero_defaults():
    with pytest.raises(ValidationError, match="default"):
        ModelPresetsValue.model_validate(
            {"presets": [_make(is_default=False)], "task_presets": {}}
        )


def test_rejects_two_defaults():
    with pytest.raises(ValidationError, match="default"):
        ModelPresetsValue.model_validate(
            {
                "presets": [_make(label="a"), _make(label="b", is_default=True)],
                "task_presets": {},
            }
        )


def test_rejects_unknown_task_key():
    with pytest.raises(ValidationError, match="task"):
        ModelPresetsValue.model_validate(
            {"presets": [_make()], "task_presets": {"unknown": "default"}}
        )


def test_rejects_task_value_not_in_labels():
    with pytest.raises(ValidationError, match="task"):
        ModelPresetsValue.model_validate(
            {"presets": [_make(label="default")], "task_presets": {"title": "ghost"}}
        )


def test_rejects_label_with_bad_chars():
    with pytest.raises(ValidationError):
        ModelPresetsValue.model_validate(
            {"presets": [_make(label="bad space")], "task_presets": {}}
        )
```

- [ ] **Step 2: Run tests, expect ModuleNotFoundError**

Run: `cd backend && uv run pytest tests/unit/llm/test_preset_schema.py -v`
Expected: collection error / `ModuleNotFoundError: cubeplex.llm.snapshot_schema`.

- [ ] **Step 3: Create `cubeplex/llm/snapshot_schema.py` with the pydantic schema**

```python
"""Pydantic schema for OrgSettings.model_presets row value.

Validates the JSON shape that admin endpoints write and that the snapshot
loader reads. Model-ref well-formedness is validated; ref-exists-in-providers
is enforced later by the loader (after providers are joined).
"""

from typing import Self

from pydantic import BaseModel, Field, model_validator

ALLOWED_TASKS: frozenset[str] = frozenset({"title", "compaction", "summarize"})

_LABEL_PATTERN = r"^[A-Za-z0-9_-]+$"


class LLMPresetSchema(BaseModel):
    label: str = Field(min_length=1, max_length=64, pattern=_LABEL_PATTERN)
    chain: list[str] = Field(min_length=1)
    is_default: bool = False


class ModelPresetsValue(BaseModel):
    presets: list[LLMPresetSchema] = Field(min_length=1)
    task_presets: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        labels = [p.label for p in self.presets]
        if len(set(labels)) != len(labels):
            raise ValueError("preset label must be unique")
        default_count = sum(1 for p in self.presets if p.is_default)
        if default_count != 1:
            raise ValueError(
                f"exactly one preset must be is_default=true (found {default_count})"
            )
        for task_key in self.task_presets:
            if task_key not in ALLOWED_TASKS:
                raise ValueError(
                    f"task_presets key {task_key!r} not in {sorted(ALLOWED_TASKS)}"
                )
        for task_key, label in self.task_presets.items():
            if label not in labels:
                raise ValueError(
                    f"task_presets[{task_key!r}]={label!r} not in preset labels"
                )
        return self
```

- [ ] **Step 4: Run tests, expect all PASS**

Run: `cd backend && uv run pytest tests/unit/llm/test_preset_schema.py -v`
Expected: 9 passed.

- [ ] **Step 5: Add `MODEL_PRESETS_KEY` constant to `org_settings.py`**

Modify `cubeplex/models/org_settings.py` — add after the `TASK_MODELS_KEY` line:

```python
# Replacement for the legacy default_model / fallback_models / task_models keys.
# Schema lives in cubeplex.llm.snapshot_schema.ModelPresetsValue.
MODEL_PRESETS_KEY = "model_presets"
```

Do not delete `TASK_MODELS_KEY` yet — Task A19 removes it.

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/llm-snapshot-refactor
git add backend/cubeplex/llm/snapshot_schema.py backend/cubeplex/models/org_settings.py backend/tests/unit/llm/test_preset_schema.py
git commit -m "feat(llm): add ModelPresetsValue pydantic schema + MODEL_PRESETS_KEY"
```

---

### Task A2: `LLMSnapshot` / `LLMPreset` dataclasses

**Files:**
- Create: `cubeplex/llm/snapshot.py`
- Test: `tests/unit/llm/test_snapshot_types.py`

- [ ] **Step 1: Write failing test**

```python
"""LLMSnapshot / LLMPreset frozen dataclass behavior."""

import pytest

from cubeplex.llm.snapshot import LLMPreset, LLMSnapshot


def test_preset_is_frozen():
    p = LLMPreset(label="x", chain=("a/b",), is_default=True)
    with pytest.raises(Exception):
        p.label = "y"  # type: ignore[misc]


def test_snapshot_is_frozen():
    s = LLMSnapshot(providers={}, presets=(), task_presets={})
    with pytest.raises(Exception):
        s.providers = {}  # type: ignore[misc]


def test_snapshot_holds_data_unchanged():
    p = LLMPreset(label="x", chain=("a/b", "c/d"), is_default=True)
    s = LLMSnapshot(providers={}, presets=(p,), task_presets={"title": "x"})
    assert s.presets[0].chain == ("a/b", "c/d")
    assert s.task_presets == {"title": "x"}
```

- [ ] **Step 2: Run test, expect ModuleNotFoundError**

Run: `cd backend && uv run pytest tests/unit/llm/test_snapshot_types.py -v`

- [ ] **Step 3: Create `cubeplex/llm/snapshot.py` skeleton**

```python
"""LLMSnapshot — per-request frozen view of LLM configuration.

A snapshot is loaded once per request via load_llm_snapshot(). Resolver
and builder modules take a snapshot as input and never read DB or
cubeplex.config themselves.
"""

from dataclasses import dataclass

from cubeplex.llm.config import ProviderConfig


@dataclass(frozen=True)
class LLMPreset:
    label: str
    chain: tuple[str, ...]
    is_default: bool


@dataclass(frozen=True)
class LLMSnapshot:
    providers: dict[str, ProviderConfig]
    presets: tuple[LLMPreset, ...]
    task_presets: dict[str, str]
```

- [ ] **Step 4: Run test, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/llm/snapshot.py backend/tests/unit/llm/test_snapshot_types.py
git commit -m "feat(llm): add LLMSnapshot / LLMPreset frozen dataclasses"
```

---

### Task A3: `LLMConfigError` hierarchy

**Files:**
- Create: `cubeplex/llm/errors.py`
- Test: `tests/unit/llm/test_errors.py`

- [ ] **Step 1: Write failing test**

```python
"""LLMConfigError hierarchy + HTTP status mapping."""

from cubeplex.llm.errors import (
    BrokenPresetError,
    LLMConfigError,
    NoDefaultPresetError,
    UnknownPresetError,
)


def test_unknown_preset_status_400():
    err = UnknownPresetError("ultra")
    assert err.status_code == 400
    assert err.error_code == "unknown_preset"
    assert "ultra" in err.message


def test_broken_preset_status_400_payload_lists_refs():
    err = BrokenPresetError("ultra", missing_refs=["bad/x", "bad/y"])
    assert err.status_code == 400
    assert err.error_code == "broken_preset"
    assert "bad/x" in err.message and "bad/y" in err.message


def test_no_default_preset_status_500():
    err = NoDefaultPresetError()
    assert err.status_code == 500
    assert err.error_code == "no_default_preset"


def test_all_subclass_llmconfigerror():
    for cls in (UnknownPresetError, BrokenPresetError, NoDefaultPresetError):
        assert issubclass(cls, LLMConfigError)
```

- [ ] **Step 2: Run test, expect ImportError**

- [ ] **Step 3: Create `cubeplex/llm/errors.py`**

```python
"""LLM configuration / resolution errors.

Inherit from APIException so the existing FastAPI handler maps them to
HTTP status + error_code automatically.
"""

from cubeplex.api.exceptions import APIException


class LLMConfigError(APIException):
    """Base — never raise directly. Subclasses pick error_code + status_code."""


class UnknownPresetError(LLMConfigError):
    def __init__(self, label: str) -> None:
        super().__init__(
            error_code="unknown_preset",
            message=f"preset {label!r} not found",
            status_code=400,
        )


class BrokenPresetError(LLMConfigError):
    def __init__(self, label: str, *, missing_refs: list[str]) -> None:
        refs = ", ".join(missing_refs)
        super().__init__(
            error_code="broken_preset",
            message=f"preset {label!r} has missing refs: {refs}",
            status_code=400,
            details=f"missing_refs={missing_refs}",
        )
        self.missing_refs = missing_refs


class NoDefaultPresetError(LLMConfigError):
    def __init__(self) -> None:
        super().__init__(
            error_code="no_default_preset",
            message="no preset is marked is_default; admin must configure one",
            status_code=500,
        )


class InvalidModelRefError(LLMConfigError):
    def __init__(self, ref: str) -> None:
        super().__init__(
            error_code="invalid_model_ref",
            message=f"model ref {ref!r} must be 'provider/model'",
            status_code=400,
        )
```

- [ ] **Step 4: Run tests, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/llm/errors.py backend/tests/unit/llm/test_errors.py
git commit -m "feat(llm): add LLMConfigError hierarchy (unknown/broken/no_default)"
```

---

### Task A4: `parse_model_ref` + `resolve_preset` + `resolve_task_preset`

**Files:**
- Create: `cubeplex/llm/resolver.py`
- Test: `tests/unit/llm/test_resolver.py`

- [ ] **Step 1: Write failing test**

```python
"""Pure resolver: snapshot → LLMPreset selection."""

import pytest

from cubeplex.llm.errors import (
    InvalidModelRefError,
    NoDefaultPresetError,
    UnknownPresetError,
)
from cubeplex.llm.resolver import parse_model_ref, resolve_preset, resolve_task_preset
from cubeplex.llm.snapshot import LLMPreset, LLMSnapshot


def _snap(*presets: LLMPreset, task_presets: dict[str, str] | None = None) -> LLMSnapshot:
    return LLMSnapshot(
        providers={},
        presets=presets,
        task_presets=task_presets or {},
    )


def test_parse_model_ref_ok():
    assert parse_model_ref("anthropic/claude-opus-4-7") == ("anthropic", "claude-opus-4-7")


@pytest.mark.parametrize("bad", ["no-slash", "/leading", "trailing/", ""])
def test_parse_model_ref_invalid(bad):
    with pytest.raises(InvalidModelRefError):
        parse_model_ref(bad)


def test_resolve_preset_none_returns_default():
    default = LLMPreset(label="default", chain=("a/b",), is_default=True)
    mini = LLMPreset(label="mini", chain=("c/d",), is_default=False)
    assert resolve_preset(_snap(default, mini), None) is default


def test_resolve_preset_label_match():
    default = LLMPreset(label="default", chain=("a/b",), is_default=True)
    mini = LLMPreset(label="mini", chain=("c/d",), is_default=False)
    assert resolve_preset(_snap(default, mini), "mini") is mini


def test_resolve_preset_unknown_label():
    default = LLMPreset(label="default", chain=("a/b",), is_default=True)
    with pytest.raises(UnknownPresetError, match="ghost"):
        resolve_preset(_snap(default), "ghost")


def test_resolve_preset_no_default_raises():
    with pytest.raises(NoDefaultPresetError):
        resolve_preset(_snap(), None)


def test_resolve_task_preset_uses_task_mapping():
    default = LLMPreset(label="default", chain=("a/b",), is_default=True)
    mini = LLMPreset(label="mini", chain=("c/d",), is_default=False)
    snap = _snap(default, mini, task_presets={"title": "mini"})
    assert resolve_task_preset(snap, "title") is mini


def test_resolve_task_preset_falls_back_to_default():
    default = LLMPreset(label="default", chain=("a/b",), is_default=True)
    assert resolve_task_preset(_snap(default), "compaction") is default
```

- [ ] **Step 2: Run test, expect ImportError**

- [ ] **Step 3: Create `cubeplex/llm/resolver.py`**

```python
"""Pure resolver — turns an LLMSnapshot + caller intent into an LLMPreset.

Functions are sync, no I/O, no cubepi imports. Tests construct snapshots
directly.
"""

from cubeplex.llm.errors import (
    InvalidModelRefError,
    NoDefaultPresetError,
    UnknownPresetError,
)
from cubeplex.llm.snapshot import LLMPreset, LLMSnapshot


def parse_model_ref(ref: str) -> tuple[str, str]:
    parts = ref.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise InvalidModelRefError(ref)
    return parts[0], parts[1]


def resolve_preset(snap: LLMSnapshot, label: str | None) -> LLMPreset:
    if label is None:
        for p in snap.presets:
            if p.is_default:
                return p
        raise NoDefaultPresetError()
    for p in snap.presets:
        if p.label == label:
            return p
    raise UnknownPresetError(label)


def resolve_task_preset(snap: LLMSnapshot, task: str) -> LLMPreset:
    label = snap.task_presets.get(task)
    if label is not None:
        for p in snap.presets:
            if p.label == label:
                return p
    return resolve_preset(snap, None)
```

- [ ] **Step 4: Run tests, expect 8 PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/llm/resolver.py backend/tests/unit/llm/test_resolver.py
git commit -m "feat(llm): add pure resolver (resolve_preset / resolve_task_preset)"
```

---

### Task A5: `builder.build_provider`

Ports `LLMFactory.build_cubepi_provider` to a free function operating on `LLMSnapshot`.

**Files:**
- Create: `cubeplex/llm/builder.py`
- Test: `tests/unit/llm/test_builder_provider.py`

- [ ] **Step 1: Write failing test**

```python
"""builder.build_provider — Provider construction from snapshot.providers[slug]."""

import pytest

from cubeplex.llm.builder import build_provider
from cubeplex.llm.config import ProviderConfig
from cubeplex.llm.snapshot import LLMSnapshot


def _snap(**provider_kwargs) -> LLMSnapshot:
    return LLMSnapshot(
        providers={"acme": ProviderConfig(api="openai-completions", **provider_kwargs)},
        presets=(),
        task_presets={},
    )


def test_build_provider_openai_completions():
    p = build_provider(_snap(base_url="https://x", api_key="k"), "acme")
    from cubepi.providers.openai import OpenAIProvider
    assert isinstance(p, OpenAIProvider)
    assert p.provider_id == "acme"


def test_build_provider_anthropic_messages_with_cache_policy():
    snap = LLMSnapshot(
        providers={
            "anthr": ProviderConfig(api="anthropic-messages", base_url=None, api_key="k"),
        },
        presets=(),
        task_presets={},
    )
    from cubepi.providers.anthropic import AnthropicProvider
    from cubeplex.llm.cache_markers import CubeplexCacheMarkerPolicy
    p = build_provider(snap, "anthr", cache_policy=CubeplexCacheMarkerPolicy())
    assert isinstance(p, AnthropicProvider)


def test_build_provider_unknown_slug_raises():
    with pytest.raises(ValueError, match="acme"):
        build_provider(LLMSnapshot(providers={}, presets=(), task_presets={}), "acme")
```

- [ ] **Step 2: Run test, expect ImportError**

- [ ] **Step 3: Create `cubeplex/llm/builder.py`**

```python
"""Pure builders — emit cubepi Provider / BoundModel objects from a snapshot.

No DB. No cubeplex.config. The chain wrapper in build_chain_model() is
added in Task A7 (chain length 1 only for PR 1) and Task B1 (length >1).
"""

from typing import TYPE_CHECKING, Any

from cubeplex.llm.snapshot import LLMSnapshot

if TYPE_CHECKING:
    from cubepi.providers.anthropic import CacheMarkerPolicy


def build_provider(
    snap: LLMSnapshot,
    slug: str,
    *,
    cache_policy: "CacheMarkerPolicy | None" = None,
) -> Any:
    """Build a cubepi Provider for snap.providers[slug] based on its api type."""
    cfg = snap.providers.get(slug)
    if cfg is None:
        raise ValueError(f"provider slug {slug!r} not in snapshot")

    from cubepi.providers.capability import CapabilityDescriptor

    cap_dict = cfg.capability or {}
    capability = CapabilityDescriptor.model_validate(cap_dict) if cap_dict else None

    overrides_raw = cfg.model_capability_overrides or {}
    overrides = {
        mid: CapabilityDescriptor.model_validate(d) for mid, d in overrides_raw.items()
    } or None

    api = cfg.api
    if api == "anthropic-messages":
        from cubepi.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            provider_id=slug,
            api_key=cfg.api_key,
            base_url=cfg.base_url or None,
            cache_policy=cache_policy,
            capability=capability,
            model_capability_overrides=overrides,
        )
    if api == "openai-completions":
        from cubepi.providers.openai import OpenAIProvider

        return OpenAIProvider(
            provider_id=slug,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            extra_body=cfg.extra_body or None,
            extra_headers=cfg.extra_headers or None,
            capability=capability,
            model_capability_overrides=overrides,
        )
    if api == "openai-responses":
        from cubepi.providers.openai_responses import OpenAIResponsesProvider

        return OpenAIResponsesProvider(
            provider_id=slug,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            capability=capability,
            model_capability_overrides=overrides,
        )

    raise ValueError(f"unsupported api for cubepi provider: {api!r}")
```

- [ ] **Step 4: Run tests, expect 3 PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/llm/builder.py backend/tests/unit/llm/test_builder_provider.py
git commit -m "feat(llm): add builder.build_provider (free function port of factory)"
```

---

### Task A6: `builder.build_bound_model`

**Files:**
- Modify: `cubeplex/llm/builder.py`
- Test: `tests/unit/llm/test_builder_bound.py`

- [ ] **Step 1: Write failing test**

```python
"""builder.build_bound_model — bind max_tokens/temperature/reasoning per-leg."""

import pytest

from cubeplex.llm.builder import build_bound_model
from cubeplex.llm.config import ModelConfig, ProviderConfig
from cubeplex.llm.snapshot import LLMSnapshot


def _snap_with_model() -> LLMSnapshot:
    return LLMSnapshot(
        providers={
            "acme": ProviderConfig(
                api="openai-completions",
                base_url="https://x",
                api_key="k",
                models=[
                    ModelConfig(
                        id="m1",
                        name="m1",
                        reasoning=True,
                        max_tokens=42000,
                    )
                ],
            )
        },
        presets=(),
        task_presets={},
    )


def test_build_bound_model_returns_cubepi_boundmodel():
    bm = build_bound_model(_snap_with_model(), "acme/m1")
    from cubepi.providers.base import BoundModel
    assert isinstance(bm, BoundModel)
    assert bm.spec.id == "m1"
    assert bm.spec.provider_id == "acme"


def test_build_bound_model_unknown_provider():
    snap = LLMSnapshot(providers={}, presets=(), task_presets={})
    with pytest.raises(ValueError, match="acme"):
        build_bound_model(snap, "acme/m1")


def test_build_bound_model_unknown_model_id():
    with pytest.raises(ValueError, match="m99"):
        build_bound_model(_snap_with_model(), "acme/m99")
```

- [ ] **Step 2: Run test, expect ImportError**

- [ ] **Step 3: Add `build_bound_model` to `cubeplex/llm/builder.py`**

Append after `build_provider`:

```python
from cubepi.providers.base import ThinkingLevel

from cubeplex.llm.resolver import parse_model_ref


def build_bound_model(
    snap: LLMSnapshot,
    ref: str,
    *,
    thinking: ThinkingLevel = "off",
    cache_policy: "CacheMarkerPolicy | None" = None,
) -> Any:
    """Build a cubepi BoundModel for `ref`, binding max_tokens / reasoning."""
    slug, model_id = parse_model_ref(ref)
    cfg = snap.providers.get(slug)
    if cfg is None:
        raise ValueError(f"provider slug {slug!r} not in snapshot")
    model_cfg = next((m for m in cfg.models if m.id == model_id), None)
    if model_cfg is None:
        raise ValueError(f"model {model_id!r} not in provider {slug!r}")
    provider = build_provider(snap, slug, cache_policy=cache_policy)
    return provider.model(
        model_id,
        reasoning=model_cfg.reasoning,
        max_tokens=model_cfg.max_tokens or 32000,
        temperature=0.7,
    )
```

Note: `thinking` is accepted but not yet plumbed; cubepi binds thinking
at `Agent.prompt()` time, not at `provider.model()`. Keep the parameter
so the signature is stable across A7 / B1 / C2.

- [ ] **Step 4: Run tests, expect 3 PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/llm/builder.py backend/tests/unit/llm/test_builder_bound.py
git commit -m "feat(llm): add builder.build_bound_model"
```

---

### Task A7: `builder.build_chain_model` — chain length 1 only

**Files:**
- Modify: `cubeplex/llm/builder.py`
- Test: `tests/unit/llm/test_builder_chain.py`

- [ ] **Step 1: Write failing test**

```python
"""builder.build_chain_model — chain length 1 returns BoundModel (PR 1)."""

import pytest

from cubeplex.llm.builder import build_chain_model
from cubeplex.llm.config import ModelConfig, ProviderConfig
from cubeplex.llm.snapshot import LLMPreset, LLMSnapshot


def _snap() -> LLMSnapshot:
    return LLMSnapshot(
        providers={
            "acme": ProviderConfig(
                api="openai-completions",
                base_url="https://x",
                api_key="k",
                models=[ModelConfig(id="m1", name="m1")],
            )
        },
        presets=(LLMPreset(label="default", chain=("acme/m1",), is_default=True),),
        task_presets={},
    )


def test_chain_length_1_returns_boundmodel():
    snap = _snap()
    preset = snap.presets[0]
    from cubepi.providers.base import BoundModel
    bm = build_chain_model(snap, preset)
    assert isinstance(bm, BoundModel)


def test_chain_length_gt_1_raises_in_pr1():
    """PR 1 deliberately rejects chain > 1; B1 lifts this."""
    snap = LLMSnapshot(
        providers={
            "acme": ProviderConfig(
                api="openai-completions",
                base_url="https://x",
                api_key="k",
                models=[ModelConfig(id="m1", name="m1"), ModelConfig(id="m2", name="m2")],
            )
        },
        presets=(LLMPreset(label="d", chain=("acme/m1", "acme/m2"), is_default=True),),
        task_presets={},
    )
    preset = snap.presets[0]
    with pytest.raises(NotImplementedError):
        build_chain_model(snap, preset)
```

- [ ] **Step 2: Run test, expect ImportError**

- [ ] **Step 3: Add `build_chain_model` to `cubeplex/llm/builder.py`**

Append:

```python
from collections.abc import Awaitable, Callable

from cubeplex.llm.snapshot import LLMPreset


OnFailoverCb = Callable[[Any, Any, BaseException | str], Awaitable[None] | None]


def build_chain_model(
    snap: LLMSnapshot,
    preset: LLMPreset,
    *,
    thinking: ThinkingLevel = "off",
    cache_policy_factory: Callable[[str], "CacheMarkerPolicy | None"] | None = None,
    on_failover: OnFailoverCb | None = None,
) -> Any:
    """chain length 1 → BoundModel; >1 → FallbackBoundModel (added in Task B1)."""
    if len(preset.chain) == 0:
        raise ValueError(f"preset {preset.label!r} has empty chain")
    if len(preset.chain) > 1:
        raise NotImplementedError(
            "chain length >1 lands in PR 2 (Task B1); enforce length 1 for PR 1"
        )
    ref = preset.chain[0]
    slug, _ = parse_model_ref(ref)
    policy = cache_policy_factory(slug) if cache_policy_factory else None
    return build_bound_model(snap, ref, thinking=thinking, cache_policy=policy)
```

- [ ] **Step 4: Run tests, expect 2 PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/llm/builder.py backend/tests/unit/llm/test_builder_chain.py
git commit -m "feat(llm): add builder.build_chain_model (PR1: length 1 only)"
```

---

### Task A8: `load_llm_snapshot` — system-row read

Reads `Provider` / `Model` / `Credential` and `OrgSettings(org_id=NULL, key='model_presets')`.

**Files:**
- Modify: `cubeplex/llm/snapshot.py`
- Test: `tests/unit/llm/test_snapshot_loader.py`

- [ ] **Step 1: Write failing test**

```python
"""load_llm_snapshot — read DB providers + OrgSettings system row."""

import pytest

from cubeplex.llm.snapshot import LLMPreset, LLMSnapshot, load_llm_snapshot
from cubeplex.models import Credential
from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
from cubeplex.models.provider import Model, Provider


@pytest.mark.asyncio
async def test_snapshot_loads_system_provider_and_preset(async_session, encryption_backend):
    # Seed a system provider + model.
    p = Provider(
        org_id=None, name="acme", slug="acme",
        provider_type="openai-completions", base_url="https://x",
        auth_type="api_key", enabled=True,
    )
    async_session.add(p)
    await async_session.flush()
    async_session.add(Model(
        org_id=None, provider_id=p.id, model_id="m1", display_name="m1",
        reasoning=False, input_modalities=["text"],
        cost_input=0, cost_output=0, cost_cache_read=0, cost_cache_write=0,
        context_window=128000, max_tokens=32000, enabled=True,
    ))
    # Seed system model_presets row.
    async_session.add(OrgSettings(
        org_id=None, key=MODEL_PRESETS_KEY,
        value={
            "presets": [{"label": "default", "chain": ["acme/m1"], "is_default": True}],
            "task_presets": {},
        },
    ))
    await async_session.commit()

    snap = await load_llm_snapshot(async_session, org_id="org_test", encryption_backend=encryption_backend)
    assert "acme" in snap.providers
    assert snap.presets == (
        LLMPreset(label="default", chain=("acme/m1",), is_default=True),
    )
```

- [ ] **Step 2: Run test, expect ImportError**

(`async_session` and `encryption_backend` fixtures already exist in `tests/conftest.py`; use them as-is.)

- [ ] **Step 3: Implement `load_llm_snapshot` in `cubeplex/llm/snapshot.py`**

Add to the existing file:

```python
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.llm.config import ProviderConfig
from cubeplex.llm.snapshot_schema import ModelPresetsValue

logger = logging.getLogger(__name__)


async def load_llm_snapshot(
    session: AsyncSession,
    org_id: str,
    encryption_backend: EncryptionBackend,
) -> LLMSnapshot:
    """Read DB providers + OrgSettings → frozen snapshot. No YAML."""
    providers = await _load_providers(session, org_id, encryption_backend)
    presets, task_presets = await _load_presets(session, org_id)
    _check_broken_refs(presets, providers)
    return LLMSnapshot(providers=providers, presets=presets, task_presets=task_presets)


async def _load_providers(
    session: AsyncSession,
    org_id: str,
    backend: EncryptionBackend,
) -> dict[str, ProviderConfig]:
    from cubeplex.models import Credential
    from cubeplex.models.org_provider_override import OrgProviderOverride as DBO
    from cubeplex.models.provider import Model as DBM
    from cubeplex.models.provider import Provider as DBP

    stmt = (
        select(DBP)
        .outerjoin(DBO, (DBP.id == DBO.provider_id) & (DBO.org_id == org_id))
        .where((DBP.org_id == None) | (DBP.org_id == org_id))  # noqa: E711
        .where(func.coalesce(DBO.enabled, DBP.enabled, True))
    )
    rows = (await session.execute(stmt)).scalars().all()
    out: dict[str, ProviderConfig] = {}
    for p in rows:
        models = (
            await session.execute(
                select(DBM).where(DBM.provider_id == p.id, DBM.enabled)
            )
        ).scalars().all()
        api_key: str | None = None
        if p.credential_id is not None:
            cred = await session.get(Credential, p.credential_id)
            if cred is not None and cred.kind == "provider_api_key":
                try:
                    api_key = (await backend.decrypt(cred.value_encrypted)).decode("utf-8")
                except Exception:
                    logger.warning("decrypt failed for provider %s", p.name)
        out[p.slug] = ProviderConfig(
            base_url=p.base_url,
            api_key=api_key,
            api=p.provider_type,
            extra_body=p.extra_body,
            extra_headers=p.extra_headers,
            capability=p.capability or {},
            model_capability_overrides=p.model_capability_overrides or {},
            models=[
                {
                    "id": m.model_id,
                    "name": m.display_name,
                    "reasoning": m.reasoning,
                    "input": m.input_modalities,
                    "cost": {
                        "input": m.cost_input,
                        "output": m.cost_output,
                        "cache_read": m.cost_cache_read,
                        "cache_write": m.cost_cache_write,
                    },
                    "contextWindow": m.context_window,
                    "maxTokens": m.max_tokens,
                    "extra_body": m.extra_body,
                    "extra_headers": m.extra_headers,
                }
                for m in models
            ],
        )
    return out


async def _load_presets(
    session: AsyncSession,
    org_id: str,
) -> tuple[tuple[LLMPreset, ...], dict[str, str]]:
    from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings

    # Org row overrides system row in full.
    org_stmt = select(OrgSettings).where(
        OrgSettings.org_id == org_id, OrgSettings.key == MODEL_PRESETS_KEY
    )
    org_row = (await session.execute(org_stmt)).scalar_one_or_none()
    if org_row is None:
        sys_stmt = select(OrgSettings).where(
            OrgSettings.org_id.is_(None), OrgSettings.key == MODEL_PRESETS_KEY
        )
        org_row = (await session.execute(sys_stmt)).scalar_one_or_none()
    if org_row is None:
        return (), {}
    parsed = ModelPresetsValue.model_validate(org_row.value)
    presets = tuple(
        LLMPreset(label=p.label, chain=tuple(p.chain), is_default=p.is_default)
        for p in parsed.presets
    )
    return presets, dict(parsed.task_presets)


def _check_broken_refs(
    presets: tuple[LLMPreset, ...],
    providers: dict[str, ProviderConfig],
) -> None:
    """Log warnings for chain refs whose provider+model don't exist in snapshot.providers.

    Does NOT remove broken presets — the resolver surfaces broken_preset at
    request time so the API can return a 400 with the missing refs.
    """
    for preset in presets:
        missing: list[str] = []
        for ref in preset.chain:
            try:
                slug, model_id = ref.split("/", 1)
            except ValueError:
                missing.append(ref)
                continue
            cfg = providers.get(slug)
            if cfg is None or all(m.id != model_id for m in cfg.models):
                missing.append(ref)
        if missing:
            logger.warning(
                "preset %r has broken refs: %s", preset.label, missing,
            )
```

- [ ] **Step 4: Run test, expect PASS**

Run: `cd backend && uv run pytest tests/unit/llm/test_snapshot_loader.py::test_snapshot_loads_system_provider_and_preset -v`

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/llm/snapshot.py backend/tests/unit/llm/test_snapshot_loader.py
git commit -m "feat(llm): load_llm_snapshot reads DB providers + system OrgSettings row"
```

---

### Task A9: `load_llm_snapshot` — org override + broken-ref detection in resolver

**Files:**
- Modify: `cubeplex/llm/resolver.py` (add `BrokenPresetError` raise)
- Modify: `tests/unit/llm/test_snapshot_loader.py`
- Modify: `tests/unit/llm/test_resolver.py`

- [ ] **Step 1: Add org-override loader test**

Append to `tests/unit/llm/test_snapshot_loader.py`:

```python
@pytest.mark.asyncio
async def test_org_row_replaces_system_row(async_session, encryption_backend):
    async_session.add(OrgSettings(
        org_id=None, key=MODEL_PRESETS_KEY,
        value={
            "presets": [{"label": "sys", "chain": ["acme/m1"], "is_default": True}],
            "task_presets": {},
        },
    ))
    async_session.add(OrgSettings(
        org_id="org_test", key=MODEL_PRESETS_KEY,
        value={
            "presets": [{"label": "org", "chain": ["acme/m1"], "is_default": True}],
            "task_presets": {},
        },
    ))
    # Seed provider/model so refs validate.
    p = Provider(org_id=None, name="acme", slug="acme",
                 provider_type="openai-completions", base_url="https://x",
                 auth_type="api_key", enabled=True)
    async_session.add(p); await async_session.flush()
    async_session.add(Model(
        org_id=None, provider_id=p.id, model_id="m1", display_name="m1",
        reasoning=False, input_modalities=["text"],
        cost_input=0, cost_output=0, cost_cache_read=0, cost_cache_write=0,
        context_window=128000, max_tokens=32000, enabled=True,
    ))
    await async_session.commit()

    snap = await load_llm_snapshot(async_session, org_id="org_test", encryption_backend=encryption_backend)
    assert [p.label for p in snap.presets] == ["org"]
```

- [ ] **Step 2: Add resolver test for broken ref**

Append to `tests/unit/llm/test_resolver.py`:

```python
from cubeplex.llm.config import ProviderConfig
from cubeplex.llm.errors import BrokenPresetError


def test_resolve_preset_broken_ref_raises():
    snap = LLMSnapshot(
        providers={},  # no providers → every ref is broken
        presets=(LLMPreset(label="default", chain=("ghost/x",), is_default=True),),
        task_presets={},
    )
    with pytest.raises(BrokenPresetError) as exc:
        resolve_preset(snap, None)
    assert "ghost/x" in exc.value.missing_refs
```

- [ ] **Step 3: Update `resolver.resolve_preset` to detect broken refs**

In `cubeplex/llm/resolver.py`, change the function to take an optional snapshot-time validation:

```python
def resolve_preset(snap: LLMSnapshot, label: str | None) -> LLMPreset:
    if label is None:
        preset = next((p for p in snap.presets if p.is_default), None)
        if preset is None:
            raise NoDefaultPresetError()
    else:
        preset = next((p for p in snap.presets if p.label == label), None)
        if preset is None:
            raise UnknownPresetError(label)
    missing = _missing_refs(preset, snap.providers)
    if missing:
        from cubeplex.llm.errors import BrokenPresetError
        raise BrokenPresetError(preset.label, missing_refs=missing)
    return preset


def _missing_refs(preset: LLMPreset, providers: dict) -> list[str]:
    missing: list[str] = []
    for ref in preset.chain:
        try:
            slug, model_id = ref.split("/", 1)
        except ValueError:
            missing.append(ref)
            continue
        cfg = providers.get(slug)
        if cfg is None or all(m.id != model_id for m in cfg.models):
            missing.append(ref)
    return missing
```

- [ ] **Step 4: Run both test files, expect all PASS**

Run: `cd backend && uv run pytest tests/unit/llm/test_resolver.py tests/unit/llm/test_snapshot_loader.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/llm/resolver.py backend/tests/unit/llm/test_resolver.py backend/tests/unit/llm/test_snapshot_loader.py
git commit -m "feat(llm): org row overrides system; resolver detects broken refs"
```

---

### Task A10: Seeder extension — write default `model_presets` row

Extends the existing `seed_system_providers_from_config` to translate YAML
`default_model + fallback_models + title_model + compaction.summary_model + summarize_model`
into an `OrgSettings(org_id=NULL, key='model_presets')` row, written only if absent.

**Files:**
- Modify: `cubeplex/seeders/provider_seeder.py`
- Test: `tests/unit/test_seeder_presets.py`

- [ ] **Step 1: Write failing test**

```python
"""Seeder writes OrgSettings.model_presets on first run; idempotent after."""

import pytest

from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings
from cubeplex.seeders.provider_seeder import seed_default_presets_from_config
from sqlalchemy import select


@pytest.mark.asyncio
async def test_first_run_writes_default_preset(async_session, monkeypatch):
    monkeypatch.setattr("cubeplex.config.config.llm", {
        "default_model": "acme/m1",
        "fallback_models": ["acme/m2"],
        "title_model": "acme/mini",
        "compaction": {"summary_model": "acme/mini"},
    })
    await seed_default_presets_from_config(async_session)
    await async_session.commit()
    row = (await async_session.execute(
        select(OrgSettings).where(
            OrgSettings.org_id.is_(None), OrgSettings.key == MODEL_PRESETS_KEY
        )
    )).scalar_one()
    val = row.value
    labels = {p["label"] for p in val["presets"]}
    assert "default" in labels
    default = next(p for p in val["presets"] if p["label"] == "default")
    assert default["chain"] == ["acme/m1", "acme/m2"]
    assert default["is_default"] is True
    # task_presets entries created for distinct task models.
    assert val["task_presets"].get("title") in labels
    assert val["task_presets"].get("compaction") in labels


@pytest.mark.asyncio
async def test_second_run_does_not_overwrite_admin_edits(async_session, monkeypatch):
    monkeypatch.setattr("cubeplex.config.config.llm", {
        "default_model": "acme/m1",
        "fallback_models": [],
    })
    async_session.add(OrgSettings(
        org_id=None, key=MODEL_PRESETS_KEY,
        value={
            "presets": [{"label": "custom", "chain": ["acme/m1"], "is_default": True}],
            "task_presets": {},
        },
    ))
    await async_session.commit()
    await seed_default_presets_from_config(async_session)
    await async_session.commit()
    row = (await async_session.execute(
        select(OrgSettings).where(
            OrgSettings.org_id.is_(None), OrgSettings.key == MODEL_PRESETS_KEY
        )
    )).scalar_one()
    labels = {p["label"] for p in row.value["presets"]}
    assert labels == {"custom"}  # not overwritten
```

- [ ] **Step 2: Run, expect ImportError**

- [ ] **Step 3: Add `seed_default_presets_from_config` to `cubeplex/seeders/provider_seeder.py`**

Append at the bottom of the file:

```python
async def seed_default_presets_from_config(session: AsyncSession) -> None:
    """Translate YAML default_model / fallback_models / *_model into an
    OrgSettings(org_id=NULL, key='model_presets') row. Idempotent: writes
    only if row does not exist; never overrides admin edits.
    """
    from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings

    cfg: dict[str, Any] = dict(settings.get("llm", {}))
    default_model = cfg.get("default_model")
    if not default_model:
        logger.info("No default_model in config — skipping preset seed")
        return

    # Skip when row already exists.
    existing = (
        await session.execute(
            select(OrgSettings).where(
                OrgSettings.org_id.is_(None),
                OrgSettings.key == MODEL_PRESETS_KEY,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.debug("OrgSettings.model_presets already present — preserving")
        return

    fallback = list(cfg.get("fallback_models") or [])
    default_chain = [str(default_model)] + [str(m) for m in fallback]

    presets: list[dict[str, Any]] = [
        {"label": "default", "chain": default_chain, "is_default": True}
    ]
    task_presets: dict[str, str] = {}

    def _add_task_preset(task_key: str, ref: str) -> None:
        label = f"task-{task_key}"
        presets.append({"label": label, "chain": [ref], "is_default": False})
        task_presets[task_key] = label

    title_model = cfg.get("title_model")
    if title_model and title_model != default_model:
        _add_task_preset("title", str(title_model))

    summarize_model = cfg.get("summarize_model")
    if summarize_model and summarize_model != default_model:
        _add_task_preset("summarize", str(summarize_model))

    comp_cfg = cfg.get("compaction") or {}
    comp_model = comp_cfg.get("summary_model")
    if comp_model:
        ref = f"{comp_cfg.get('summary_provider', '')}/{comp_model}".strip("/")
        if ref and "/" in ref and ref != default_model:
            _add_task_preset("compaction", ref)

    row = OrgSettings(
        org_id=None,
        key=MODEL_PRESETS_KEY,
        value={"presets": presets, "task_presets": task_presets},
    )
    session.add(row)
    await session.flush()
    logger.info("Seeded OrgSettings.model_presets (default chain: %s)", default_chain)
```

- [ ] **Step 4: Run, expect 2 PASS**

- [ ] **Step 5: Wire the new function into the startup hook**

Modify `cubeplex/api/app.py` near the existing `seed_system_providers_from_config(...)` call (around line 268):

```python
async with async_session_maker() as seed_session:
    await seed_system_providers_from_config(seed_session, _app.state.encryption_backend)
    from cubeplex.seeders.provider_seeder import seed_default_presets_from_config
    await seed_default_presets_from_config(seed_session)
```

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/seeders/provider_seeder.py backend/cubeplex/api/app.py backend/tests/unit/test_seeder_presets.py
git commit -m "feat(seeder): write OrgSettings.model_presets from YAML on bootstrap"
```

---

### Task A11: Alembic migration — legacy keys → `model_presets`

**Files:**
- Create: `backend/alembic/versions/<rev>_migrate_orgsettings_to_model_presets.py`

- [ ] **Step 1: Generate migration skeleton**

Run from `backend/`:

```bash
uv run alembic revision -m "migrate orgsettings to model_presets"
```

Note the generated revision id and file path.

- [ ] **Step 2: Replace skeleton with hand-rolled data migration**

(autogen won't generate this — it's pure data, no schema diff)

```python
"""migrate orgsettings to model_presets

Revision ID: <generated>
Revises: <prev head>
Create Date: 2026-06-09

Translates OrgSettings rows with keys in
{'default_model', 'fallback_models', 'task_models'} into a single
'model_presets' row per (org_id) tuple. No table schema changes.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "<generated>"
down_revision: Union[str, Sequence[str], None] = "<prev>"
branch_labels = None
depends_on = None


LEGACY_KEYS = ("default_model", "fallback_models", "task_models")
NEW_KEY = "model_presets"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT org_id, key, value FROM org_settings WHERE key = ANY(:keys)"
    ), {"keys": list(LEGACY_KEYS)}).fetchall()

    by_org: dict[str | None, dict[str, dict]] = {}
    for r in rows:
        by_org.setdefault(r.org_id, {})[r.key] = r.value

    for org_id, legacy in by_org.items():
        default_ref = (legacy.get("default_model") or {}).get("model_ref")
        fallback_refs = (legacy.get("fallback_models") or {}).get("models") or []
        task_models = legacy.get("task_models") or {}

        if not default_ref:
            # Nothing to translate; seeder will fill later.
            continue

        presets = [{
            "label": "default",
            "chain": [default_ref] + list(fallback_refs),
            "is_default": True,
        }]
        task_presets: dict[str, str] = {}
        for task_key, ref in task_models.items():
            if not ref or ref == default_ref:
                continue
            label = f"task-{task_key}"
            presets.append({"label": label, "chain": [ref], "is_default": False})
            task_presets[task_key] = label

        new_value = {"presets": presets, "task_presets": task_presets}
        conn.execute(sa.text("""
            INSERT INTO org_settings (org_id, key, value, created_at, updated_at)
            VALUES (:org_id, :key, :value, now(), now())
            ON CONFLICT (org_id, key) DO UPDATE SET value = EXCLUDED.value
        """), {"org_id": org_id, "key": NEW_KEY, "value": json.dumps(new_value)})

    conn.execute(sa.text(
        "DELETE FROM org_settings WHERE key = ANY(:keys)"
    ), {"keys": list(LEGACY_KEYS)})


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT org_id, value FROM org_settings WHERE key = :key"
    ), {"key": NEW_KEY}).fetchall()
    for r in rows:
        v = r.value
        default = next((p for p in v.get("presets", []) if p.get("is_default")), None)
        if default is None:
            continue
        conn.execute(sa.text("""
            INSERT INTO org_settings (org_id, key, value, created_at, updated_at)
            VALUES (:org_id, 'default_model', :v, now(), now())
            ON CONFLICT (org_id, key) DO UPDATE SET value = EXCLUDED.value
        """), {"org_id": r.org_id, "v": json.dumps({"model_ref": default["chain"][0]})})
        if len(default["chain"]) > 1:
            conn.execute(sa.text("""
                INSERT INTO org_settings (org_id, key, value, created_at, updated_at)
                VALUES (:org_id, 'fallback_models', :v, now(), now())
                ON CONFLICT (org_id, key) DO UPDATE SET value = EXCLUDED.value
            """), {"org_id": r.org_id, "v": json.dumps({"models": default["chain"][1:]})})
        task_models = {
            t: next(p for p in v["presets"] if p["label"] == label)["chain"][0]
            for t, label in v.get("task_presets", {}).items()
        }
        if task_models:
            conn.execute(sa.text("""
                INSERT INTO org_settings (org_id, key, value, created_at, updated_at)
                VALUES (:org_id, 'task_models', :v, now(), now())
                ON CONFLICT (org_id, key) DO UPDATE SET value = EXCLUDED.value
            """), {"org_id": r.org_id, "v": json.dumps(task_models)})

    conn.execute(sa.text("DELETE FROM org_settings WHERE key = :key"), {"key": NEW_KEY})
```

Replace `<generated>` and `<prev>` with real values from Step 1.

- [ ] **Step 3: Apply migration locally**

Run from `backend/`:

```bash
uv run alembic upgrade head
```

Expected: applies cleanly. (Worktree DB is already fresh from `new-worktree`.)

- [ ] **Step 4: Test round trip**

```bash
uv run alembic downgrade -1
uv run alembic upgrade head
```

Expected: both succeed.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/*_migrate_orgsettings_to_model_presets.py
git commit -m "feat(alembic): migrate orgsettings keys to model_presets"
```

---

### Task A12: Switch `conversation_title.py` to new API

**Files:**
- Modify: `cubeplex/services/conversation_title.py`
- Test: `tests/unit/test_conversation_title_pi.py`

- [ ] **Step 1: Update the existing test to expect snapshot-based path**

In `tests/unit/test_conversation_title_pi.py`, find the test that asserts which model is used, and replace its setup. The test should mock `load_llm_snapshot` to return an `LLMSnapshot` with a known preset, then assert the resolved model_id matches.

Concrete change pattern (find equivalent in current file):

```python
from cubeplex.llm.snapshot import LLMPreset, LLMSnapshot
from cubeplex.llm.config import ModelConfig, ProviderConfig

def _snap():
    return LLMSnapshot(
        providers={"acme": ProviderConfig(
            api="openai-completions", base_url="https://x", api_key="k",
            models=[ModelConfig(id="title-m", name="title-m")],
        )},
        presets=(LLMPreset(label="default", chain=("acme/title-m",), is_default=True),),
        task_presets={"title": "default"},
    )

@pytest.mark.asyncio
async def test_title_uses_task_preset(monkeypatch):
    async def _fake_load(*_a, **_kw): return _snap()
    monkeypatch.setattr("cubeplex.services.conversation_title.load_llm_snapshot", _fake_load)
    # rest of existing test — assert title call lands on acme/title-m
```

- [ ] **Step 2: Run test, expect failure**

- [ ] **Step 3: Modify `cubeplex/services/conversation_title.py`**

Locate the existing `factory = LLMFactory(...)` block (around line 207) and the
`factory.build_cubepi_provider(...)` (around line 146). Replace with:

```python
from cubeplex.llm.builder import build_chain_model
from cubeplex.llm.resolver import resolve_task_preset
from cubeplex.llm.snapshot import load_llm_snapshot

async with async_session_maker() as s:
    snap = await load_llm_snapshot(s, org_id, encryption_backend)
preset = resolve_task_preset(snap, "title")
model = build_chain_model(snap, preset, thinking="off")
# pass `model` directly where the old code passed `provider.model(model_id, ...)`
```

Remove the import `from cubeplex.llm.factory import LLMFactory`.

- [ ] **Step 4: Run test, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/services/conversation_title.py backend/tests/unit/test_conversation_title_pi.py
git commit -m "refactor(title): use load_llm_snapshot + resolve_task_preset"
```

---

### Task A13: Switch `provider_service.py` + `usage.py` to new API

**Files:**
- Modify: `cubeplex/services/provider_service.py`
- Modify: `cubeplex/services/usage.py`
- Test: `tests/test_provider_capability_factory.py` (existing)

- [ ] **Step 1: Update `provider_service.py` lines 376 and 389**

These two sites currently do `LLMFactory().build_cubepi_provider(...)`.
They're stateless lookups — `build_provider` is a free function now.
Replace each occurrence:

```python
# Before
from cubeplex.llm.factory import LLMFactory
return LLMFactory().build_cubepi_provider(provider_config, provider_name=provider_name)

# After
from cubeplex.llm.builder import build_provider
# Wrap the loose ProviderConfig in a one-key snapshot:
from cubeplex.llm.snapshot import LLMSnapshot
snap = LLMSnapshot(providers={provider_name: provider_config}, presets=(), task_presets={})
return build_provider(snap, provider_name)
```

- [ ] **Step 2: Update `usage.py` line 142**

Find the `LLMFactory(session=session, org_id=org_id)` use and the field
it then reads from `llm_config.providers`. Replace with `load_llm_snapshot`:

```python
from cubeplex.llm.snapshot import load_llm_snapshot
snap = await load_llm_snapshot(session, org_id, encryption_backend)
# Use snap.providers[slug].models[...] in place of the old llm_config path.
```

If usage.py reaches for model cost only and has no `encryption_backend`
in scope, take it from `request.app.state.encryption_backend` at the
call's outer frame.

- [ ] **Step 3: Run existing tests, expect PASS**

```bash
uv run pytest tests/test_provider_capability_factory.py tests/unit/llm/test_factory_provider_id.py -v
```

(`test_factory_provider_id.py` will need its imports updated to point at
`cubeplex.llm.builder.build_provider`. Do that as part of this task.)

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/services/provider_service.py backend/cubeplex/services/usage.py backend/tests/unit/llm/test_factory_provider_id.py
git commit -m "refactor(services): provider_service + usage use builder/snapshot"
```

---

### Task A14: Switch main agent run in `run_manager.py`

**Files:**
- Modify: `cubeplex/streams/run_manager.py`
- Test: `tests/unit/test_run_manager_build_agent.py`

- [ ] **Step 1: Update test setup**

In `tests/unit/test_run_manager_build_agent.py`, replace any `LLMFactory`
mocking with `load_llm_snapshot` mocking that returns an `LLMSnapshot`.

- [ ] **Step 2: Replace L1958-1999 in `run_manager.py`**

Locate the existing block (search for `factory = LLMFactory(`). Replace
the whole resolve-default-then-try-except-then-build with:

```python
from cubeplex.llm.builder import build_chain_model
from cubeplex.llm.resolver import resolve_preset
from cubeplex.llm.snapshot import load_llm_snapshot

async with async_session_maker() as llm_session:
    snap = await load_llm_snapshot(
        llm_session, ctx.org_id, self._app.state.encryption_backend,
    )
    await llm_session.commit()

preset = resolve_preset(snap, None)  # PR 3 adds body.preset_label override
provider_cache_policy = (
    lambda slug: CubeplexCacheMarkerPolicy()
    if snap.providers[slug].api == "anthropic-messages"
    else None
)
this_run_model = build_chain_model(
    snap, preset,
    thinking="off",                              # PR 3 wires body.thinking
    cache_policy_factory=provider_cache_policy,
)
```

Delete:
- The fallback `except` block that constructed a bare `LLMFactory()`.
- The `_model_max_tokens` / `_model_temperature` lines (now bound in the BoundModel).
- The comment block at L1991-1996 about "no fallback chain yet".

Everywhere later code refers to `provider.model(model_id, ...)`, replace
with `this_run_model`.

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/unit/test_run_manager_build_agent.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py backend/tests/unit/test_run_manager_build_agent.py
git commit -m "refactor(run_manager): main agent uses load_llm_snapshot + build_chain_model"
```

---

### Task A15: Switch subagent default model + memory consolidation

**Files:**
- Modify: `cubeplex/streams/run_manager.py` (around L2557, L2734)

- [ ] **Step 1: Subagent**

At L2557-2564 replace `default_model=provider.model(model_id, reasoning=..., max_tokens=..., temperature=...)`
with `default_model=this_run_model` (the same `FallbackBoundModel | BoundModel`
built for the main agent).

- [ ] **Step 2: Memory consolidation (L2720-2737)**

Replace the existing `LLMFactory(session=...)` + `factory.build_cubepi_provider`
block with:

```python
async with async_session_maker() as _llm_session:
    snap = await load_llm_snapshot(
        _llm_session, ctx.org_id, self._app.state.encryption_backend,
    )
    await _llm_session.commit()

preset = resolve_task_preset(snap, "compaction")
bound_model = build_chain_model(snap, preset, thinking="off")
```

The remaining wiring (`mc.run_consolidation(..., model=bound_model, ...)`) stays.

- [ ] **Step 3: Other run_manager sites (L2943, L3447, L2407)**

Search the file for remaining `LLMFactory(` occurrences and replace each
with `load_llm_snapshot` + `resolve_task_preset` (with the appropriate task
name from context, or `resolve_preset(snap, None)` for the main default).

- [ ] **Step 4: Run unit tests**

```bash
uv run pytest tests/unit/ -v -k "run_manager or steer or run_control or reflection"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py
git commit -m "refactor(run_manager): subagent + compaction use load_llm_snapshot"
```

---

### Task A16: Delete `factory.py` and `task_model_resolver.py`; clear stale imports

**Files:**
- Delete: `cubeplex/llm/factory.py`
- Delete: `cubeplex/services/task_model_resolver.py`
- Modify: `cubeplex/llm/__init__.py`

- [ ] **Step 1: Verify nothing imports the doomed modules**

```bash
cd backend && grep -rn "from cubeplex.llm.factory\|from cubeplex.services.task_model_resolver\|LLMFactory" cubeplex/ tests/
```

Expected: only the two files themselves and stale tests show up. If any
non-test cubeplex source still imports them, fix it before deleting.

- [ ] **Step 2: Delete**

```bash
rm backend/cubeplex/llm/factory.py backend/cubeplex/services/task_model_resolver.py
```

- [ ] **Step 3: Update `cubeplex/llm/__init__.py`**

Remove the `from cubeplex.llm.factory import LLMFactory` line (and the
`__all__` entry if present). Add re-exports for the new API:

```python
from cubeplex.llm.snapshot import LLMPreset, LLMSnapshot, load_llm_snapshot
from cubeplex.llm.resolver import resolve_preset, resolve_task_preset, parse_model_ref
from cubeplex.llm.builder import build_provider, build_bound_model, build_chain_model

__all__ = [
    "LLMPreset", "LLMSnapshot", "load_llm_snapshot",
    "resolve_preset", "resolve_task_preset", "parse_model_ref",
    "build_provider", "build_bound_model", "build_chain_model",
]
```

- [ ] **Step 4: Delete stale test files**

```bash
rm backend/tests/unit/test_llm_factory_cubepi.py \
   backend/tests/unit/test_task_model_resolver.py \
   backend/tests/unit/test_factory_slug_resolve.py
```

The replacement tests (`test_snapshot_loader.py` / `test_resolver.py` /
`test_builder*.py`) were created in earlier tasks.

- [ ] **Step 5: Drop legacy constant from `org_settings.py`**

Edit `cubeplex/models/org_settings.py`:

```python
# Remove these lines:
# TASK_MODELS_KEY = "task_models"
# # Per-task model routing... (the comment above it)
```

Search for usages of `TASK_MODELS_KEY` and confirm none remain.

- [ ] **Step 6: Run full unit + type checks**

```bash
uv run mypy cubeplex/
uv run ruff check cubeplex/
uv run pytest tests/unit/ -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add -A backend/
git commit -m "refactor(llm): delete LLMFactory and task_model_resolver"
```

---

### Task A17: PR 1 E2E sweep

**Files:** None.

- [ ] **Step 1: Run full E2E suite**

```bash
cd backend
cat .worktree.env  # confirm slot 19 / port 8019
uv run pytest tests/e2e/ -v
```

Expected: green. Pre-existing test files that still reference `LLMFactory`
or `task_model_resolver` must already be updated by Tasks A12-A16.

- [ ] **Step 2: Push PR 1**

```bash
git push -u origin feat/llm-snapshot-refactor
gh pr create --title "refactor(llm): LLMFactory → snapshot/resolver/builder (no behavior change)" --body "$(cat <<'EOF'
## Summary
- Replace `LLMFactory` with three modules: `snapshot.py` (async I/O), `resolver.py` (pure), `builder.py` (pure).
- Seeder synthesizes `OrgSettings.model_presets` from YAML on first boot; runtime never reads `config.llm.*`.
- Alembic data migration translates legacy `default_model` / `fallback_models` / `task_models` OrgSettings rows.
- All 6 `LLMFactory(...)` call sites in `run_manager.py` switched; `conversation_title` / `usage` / `provider_service` switched.
- Chain length is still 1 (FallbackBoundModel lands in PR 2).
- No API surface change; no observable behavior change.

Spec: docs/dev/specs/2026-06-09-llm-snapshot-refactor-design.md

## Test plan
- [ ] Unit tests green
- [ ] E2E tests green
- [ ] Alembic upgrade + downgrade clean
- [ ] mypy + ruff clean
EOF
)"
```

- [ ] **Step 3: Run codex review loop**

Follow `.claude/skills/pr-codex-review-loop/SKILL.md` until green.

---

## Part B — FallbackBoundModel integration (PR 2)

### Task B1: Lift chain-length-1 restriction in `build_chain_model`

**Files:**
- Modify: `cubeplex/llm/builder.py`
- Modify: `tests/unit/llm/test_builder_chain.py`

- [ ] **Step 1: Add failing test for chain length 2**

Append to `tests/unit/llm/test_builder_chain.py`:

```python
def test_chain_length_2_returns_fallback_bound_model():
    snap = LLMSnapshot(
        providers={
            "acme": ProviderConfig(
                api="openai-completions",
                base_url="https://x",
                api_key="k",
                models=[ModelConfig(id="m1", name="m1"), ModelConfig(id="m2", name="m2")],
            )
        },
        presets=(LLMPreset(label="d", chain=("acme/m1", "acme/m2"), is_default=True),),
        task_presets={},
    )
    preset = snap.presets[0]
    from cubepi.providers.fallback import FallbackBoundModel
    bm = build_chain_model(snap, preset)
    assert isinstance(bm, FallbackBoundModel)
    assert len(bm.chain) == 2


def test_chain_passes_on_failover_callback():
    snap = LLMSnapshot(
        providers={
            "acme": ProviderConfig(
                api="openai-completions",
                base_url="https://x",
                api_key="k",
                models=[ModelConfig(id="m1", name="m1"), ModelConfig(id="m2", name="m2")],
            )
        },
        presets=(LLMPreset(label="d", chain=("acme/m1", "acme/m2"), is_default=True),),
        task_presets={},
    )
    calls: list = []
    async def cb(failed, nxt, err): calls.append((failed, nxt, err))
    bm = build_chain_model(snap, snap.presets[0], on_failover=cb)
    assert bm.on_failover is cb
```

Remove the existing `test_chain_length_gt_1_raises_in_pr1` test.

- [ ] **Step 2: Update `builder.build_chain_model`**

Replace the `NotImplementedError` branch with:

```python
if len(preset.chain) == 1:
    ref = preset.chain[0]
    slug, _ = parse_model_ref(ref)
    policy = cache_policy_factory(slug) if cache_policy_factory else None
    return build_bound_model(snap, ref, thinking=thinking, cache_policy=policy)

from cubepi.providers.fallback import FallbackBoundModel

bounds = []
for ref in preset.chain:
    slug, _ = parse_model_ref(ref)
    policy = cache_policy_factory(slug) if cache_policy_factory else None
    bounds.append(build_bound_model(snap, ref, thinking=thinking, cache_policy=policy))

return FallbackBoundModel(chain=tuple(bounds), on_failover=on_failover)
```

- [ ] **Step 3: Run tests, expect PASS**

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/llm/builder.py backend/tests/unit/llm/test_builder_chain.py
git commit -m "feat(llm): build_chain_model wraps chain>1 in FallbackBoundModel"
```

---

### Task B2: `FailoverEvent` schema + `_make_failover_publisher`

**Files:**
- Modify: `cubeplex/agents/schemas.py` (or wherever SSE event types live)
- Modify: `cubeplex/streams/run_manager.py`
- Test: `tests/unit/test_failover_marker.py`

- [ ] **Step 1: Locate the existing SSE event union**

```bash
grep -rn "class TextDeltaEvent\|class ToolCallEvent\|class ErrorEvent" cubeplex/ | head
```

Identify the module that defines SSE event Pydantic models. (Likely
`cubeplex/agents/schemas.py`.)

- [ ] **Step 2: Add `FailoverEvent`**

In the schemas module, add:

```python
class FailoverEvent(BaseModel):
    type: Literal["model_failover"] = "model_failover"
    failed_ref: str
    next_ref: str | None
    reason: str
```

And add `FailoverEvent` to the discriminated union of SSE events if one
exists.

- [ ] **Step 3: Write failing test for `_make_failover_publisher`**

```python
"""_make_failover_publisher closure builds correct payload."""

import pytest

from cubeplex.streams.run_manager import _make_failover_publisher


class _FakeSpec:
    def __init__(self, pid, mid):
        self.provider_id = pid
        self.id = mid


class _FakeBound:
    def __init__(self, pid, mid):
        self.spec = _FakeSpec(pid, mid)


@pytest.mark.asyncio
async def test_publisher_emits_correct_shape():
    sent: list[tuple[str, dict]] = []
    async def publish(run_id, payload): sent.append((run_id, payload))
    cb = _make_failover_publisher("run_abc", publish)
    await cb(_FakeBound("p1", "m1"), _FakeBound("p2", "m2"), RuntimeError("boom"))
    assert sent == [("run_abc", {
        "type": "model_failover",
        "failed_ref": "p1/m1",
        "next_ref": "p2/m2",
        "reason": "boom",
    })]


@pytest.mark.asyncio
async def test_publisher_handles_none_next():
    sent: list = []
    async def publish(run_id, payload): sent.append(payload)
    cb = _make_failover_publisher("r", publish)
    await cb(_FakeBound("p1", "m1"), None, "exhausted")
    assert sent[0]["next_ref"] is None
    assert sent[0]["reason"] == "exhausted"


@pytest.mark.asyncio
async def test_publisher_truncates_reason():
    sent: list = []
    async def publish(run_id, payload): sent.append(payload)
    cb = _make_failover_publisher("r", publish)
    await cb(_FakeBound("p", "m"), None, "x" * 1000)
    assert len(sent[0]["reason"]) == 256
```

- [ ] **Step 4: Add `_make_failover_publisher` to `run_manager.py`**

Near the other private helpers, add:

```python
def _make_failover_publisher(
    run_id: str,
    publish: Callable[[str, dict], Awaitable[None]],
) -> Callable[[Any, Any, BaseException | str], Awaitable[None]]:
    async def _on_failover(failed: Any, next_bound: Any, error: BaseException | str) -> None:
        await publish(run_id, {
            "type": "model_failover",
            "failed_ref": f"{failed.spec.provider_id}/{failed.spec.id}",
            "next_ref": (
                f"{next_bound.spec.provider_id}/{next_bound.spec.id}"
                if next_bound is not None else None
            ),
            "reason": str(error)[:256],
        })
    return _on_failover
```

- [ ] **Step 5: Run tests, expect PASS**

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/agents/schemas.py backend/cubeplex/streams/run_manager.py backend/tests/unit/test_failover_marker.py
git commit -m "feat(stream): FailoverEvent schema + _make_failover_publisher"
```

---

### Task B3: Wire `on_failover` into main-agent `build_chain_model`

**Files:**
- Modify: `cubeplex/streams/run_manager.py`

- [ ] **Step 1: Locate the main-agent `build_chain_model` call from Task A14**

Wrap the SSE publisher in the failover closure and pass it through:

```python
this_run_model = build_chain_model(
    snap, preset,
    thinking="off",
    cache_policy_factory=provider_cache_policy,
    on_failover=_make_failover_publisher(run_id, self._sse_publish),
)
```

Use the existing SSE publish helper on `self` (search for how
`TextDeltaEvent` is emitted today and reuse the same publisher).

- [ ] **Step 2: Confirm subagent inherits**

The subagent line set in Task A15 uses `default_model=this_run_model` —
this means subagents share the same `FallbackBoundModel` instance,
including the same `on_failover` callback. No additional wiring needed.

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py
git commit -m "feat(run_manager): pipe on_failover into main chain model"
```

---

### Task B4: E2E fallback test using `cubepi.FauxProvider`

**Files:**
- Create: `tests/e2e/test_fallback_e2e.py`

- [ ] **Step 1: Write the E2E test**

```python
"""Failover end-to-end: chain[0] raises RateLimited → chain[1] answers.

Asserts:
- SSE stream contains a model_failover event.
- Final assistant message comes from chain[1].
- The agent does not surface chain[0]'s RateLimited to the caller.
"""

import pytest
from cubepi.errors import RateLimited
from cubepi.providers.faux import FauxProvider, faux_assistant_message

from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings


@pytest.mark.asyncio
async def test_main_agent_fails_over(
    api_client, async_session, provider_seed,
):
    # provider_seed fixture registers two providers in DB: "primary" and "backup".
    # Provider construction is faked via a monkeypatch hook on builder.build_provider
    # so that primary returns a FauxProvider raising RateLimited.

    # Seed model_presets row pointing at the two-element chain.
    async_session.add(OrgSettings(
        org_id=None, key=MODEL_PRESETS_KEY,
        value={
            "presets": [{
                "label": "default",
                "chain": ["primary/m1", "backup/m1"],
                "is_default": True,
            }],
            "task_presets": {},
        },
    ))
    await async_session.commit()

    events: list[dict] = []
    async for ev in api_client.stream_post(
        f"/api/v1/ws/{api_client.workspace_id}/conversations",
        json={"message": "hello"},
    ):
        events.append(ev)

    failover_events = [e for e in events if e.get("type") == "model_failover"]
    assert failover_events, "expected a model_failover event"
    assert failover_events[0]["failed_ref"] == "primary/m1"
    assert failover_events[0]["next_ref"] == "backup/m1"

    final = next(e for e in events if e.get("type") == "message_end")
    assert "backup" in final.get("model", "") or final.get("content")
```

(Adapt fixture names to match the worktree's `conftest.py` —
`api_client`, `async_session`, and `provider_seed` are placeholders;
inspect existing E2E tests for exact names.)

- [ ] **Step 2: Add `provider_seed` fixture wiring**

In `tests/e2e/conftest.py`, add a fixture that:
1. Registers a `Provider(slug='primary', provider_type='openai-completions', ...)`.
2. Registers a `Provider(slug='backup', provider_type='openai-completions', ...)`.
3. Monkeypatches `cubeplex.llm.builder.build_provider` so `slug='primary'`
   returns a `FauxProvider` configured to raise `RateLimited` on the
   first response step; `slug='backup'` returns a `FauxProvider`
   returning `faux_assistant_message("hello back")`.

```python
@pytest.fixture
def provider_seed(monkeypatch, async_session):
    from cubeplex.llm import builder

    real_build_provider = builder.build_provider

    def _build(snap, slug, *, cache_policy=None):
        if slug == "primary":
            primary = FauxProvider(provider_id="primary")
            primary.set_responses([
                lambda *_a, **_kw: (_ for _ in ()).throw(
                    RateLimited("429", provider="primary", model="m1")
                ),
            ])
            return primary
        if slug == "backup":
            backup = FauxProvider(provider_id="backup")
            backup.set_responses([faux_assistant_message("hello back")])
            return backup
        return real_build_provider(snap, slug, cache_policy=cache_policy)

    monkeypatch.setattr(builder, "build_provider", _build)
    # ... DB insert Provider rows for primary / backup
```

- [ ] **Step 3: Run the E2E test**

```bash
cd backend
uv run pytest tests/e2e/test_fallback_e2e.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/e2e/test_fallback_e2e.py backend/tests/e2e/conftest.py
git commit -m "test(e2e): main-agent failover via FauxProvider chain"
```

---

### Task B5: PR 2 push + codex review

- [ ] **Step 1: Run full unit + E2E**

```bash
cd backend && uv run pytest tests/ -v
```

- [ ] **Step 2: Push PR 2**

```bash
git push
gh pr create --title "feat(llm): wire FallbackBoundModel into main agent + subagent" --body "$(cat <<'EOF'
## Summary
- chain length >1 in a preset now produces `FallbackBoundModel`.
- New SSE event `model_failover` emitted on each failover.
- Main agent and subagent share the same `FallbackBoundModel` instance.
- E2E test uses `cubepi.providers.faux.FauxProvider` to drive failover.

Known limitation (tracked as cubepi follow-up PR): `Tracer.attach()` and
`Meter.attach()` only subscribe to `chain[0].provider`, so post-failover
chat spans + provider metrics are missing for chain[1..]. CostMiddleware
attribution is unaffected.

Spec: docs/dev/specs/2026-06-09-llm-snapshot-refactor-design.md

## Test plan
- [ ] `tests/e2e/test_fallback_e2e.py` green
- [ ] Existing E2E green
EOF
)"
```

- [ ] **Step 3: Run codex review loop until clean**

---

## Part C — Per-message preset + thinking (PR 3)

### Task C1: `preset_label` + `thinking` on `CreateMessageBody`

**Files:**
- Modify: the chat-message request schema (locate via grep)
- Test: existing API schema test or new one

- [ ] **Step 1: Locate the request schema**

```bash
grep -rn "class CreateMessage\|class SendMessage\|preset_label" backend/cubeplex/api/ | head
```

Identify the Pydantic model used by the SSE message-create endpoint.

- [ ] **Step 2: Add the two fields**

```python
from cubepi.providers.base import ThinkingLevel

class CreateMessageBody(BaseModel):
    # ... existing fields
    preset_label: str | None = None
    thinking: ThinkingLevel = "off"
```

- [ ] **Step 3: Write/extend test**

```python
def test_request_accepts_preset_label_and_thinking():
    body = CreateMessageBody.model_validate({
        "content": "hi",
        "preset_label": "ultra",
        "thinking": "high",
    })
    assert body.preset_label == "ultra"
    assert body.thinking == "high"


def test_thinking_defaults_to_off():
    body = CreateMessageBody.model_validate({"content": "hi"})
    assert body.thinking == "off"


def test_thinking_rejects_unknown_value():
    with pytest.raises(ValidationError):
        CreateMessageBody.model_validate({"content": "hi", "thinking": "nuclear"})
```

- [ ] **Step 4: Run tests, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/<schema_file>.py backend/tests/<schema_test>.py
git commit -m "feat(api): CreateMessageBody accepts preset_label + thinking"
```

---

### Task C2: Wire `body.preset_label` + `body.thinking` into `run_manager.py`

**Files:**
- Modify: `cubeplex/streams/run_manager.py`

- [ ] **Step 1: Replace hardcoded values in the main-agent `build_chain_model` call**

The block from Task A14 currently reads:

```python
preset = resolve_preset(snap, None)
this_run_model = build_chain_model(snap, preset, thinking="off", ...)
```

Change to:

```python
preset = resolve_preset(snap, body.preset_label)
this_run_model = build_chain_model(
    snap, preset,
    thinking=body.thinking,
    cache_policy_factory=provider_cache_policy,
    on_failover=_make_failover_publisher(run_id, self._sse_publish),
)
```

(Confirm `body` is reachable in this scope — it should be, since it's
the request body received by the route handler.)

- [ ] **Step 2: Verify subagent inheritance still holds**

The subagent line `default_model=this_run_model` is unchanged — the
subagent inherits the resolved preset + thinking automatically.

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py
git commit -m "feat(run_manager): apply per-message preset_label + thinking"
```

---

### Task C3: Register FastAPI handler for `LLMConfigError`

**Files:**
- Modify: `cubeplex/api/exceptions.py` (or `api/app.py` where handlers are registered)

- [ ] **Step 1: Locate the existing exception-handler registration**

```bash
grep -n "register_exception_handlers\|add_exception_handler" cubeplex/api/exceptions.py cubeplex/api/app.py
```

- [ ] **Step 2: Confirm `APIException` is already handled**

`LLMConfigError` subclasses `APIException`, which the existing handler
maps to `status_code` + JSON body. **No new handler is needed if
`APIException` is already covered** — verify by reading the existing
handler. If so, this task is just a comment update in the spec.

If the existing handler matches `APIException` exactly (not subclasses),
extend it to catch `APIException` and use `isinstance`.

- [ ] **Step 3: Write E2E test for the error matrix**

`tests/e2e/test_preset_errors_e2e.py`:

```python
"""HTTP error matrix for preset / thinking on the chat endpoint."""

import pytest

from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings


@pytest.mark.asyncio
async def test_unknown_preset_label_400(api_client, async_session, provider_seed):
    async_session.add(OrgSettings(
        org_id=None, key=MODEL_PRESETS_KEY,
        value={
            "presets": [{"label": "default", "chain": ["backup/m1"], "is_default": True}],
            "task_presets": {},
        },
    ))
    await async_session.commit()

    resp = await api_client.post(
        f"/api/v1/ws/{api_client.workspace_id}/conversations",
        json={"content": "hi", "preset_label": "ghost"},
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "unknown_preset"


@pytest.mark.asyncio
async def test_broken_preset_400_lists_refs(api_client, async_session, provider_seed):
    async_session.add(OrgSettings(
        org_id=None, key=MODEL_PRESETS_KEY,
        value={
            "presets": [{"label": "default", "chain": ["ghost/x"], "is_default": True}],
            "task_presets": {},
        },
    ))
    await async_session.commit()

    resp = await api_client.post(
        f"/api/v1/ws/{api_client.workspace_id}/conversations",
        json={"content": "hi"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error_code"] == "broken_preset"
    assert "ghost/x" in body["details"]


@pytest.mark.asyncio
async def test_no_default_preset_500(api_client, async_session, provider_seed):
    # No model_presets row at all (delete the one seeder wrote).
    from sqlalchemy import delete
    await async_session.execute(
        delete(OrgSettings).where(OrgSettings.key == MODEL_PRESETS_KEY)
    )
    await async_session.commit()

    resp = await api_client.post(
        f"/api/v1/ws/{api_client.workspace_id}/conversations",
        json={"content": "hi"},
    )
    assert resp.status_code == 500
    assert resp.json()["error_code"] == "no_default_preset"
```

- [ ] **Step 4: Run E2E**

```bash
uv run pytest tests/e2e/test_preset_errors_e2e.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/e2e/test_preset_errors_e2e.py backend/cubeplex/api/exceptions.py
git commit -m "test(e2e): preset error matrix (unknown/broken/no_default)"
```

---

### Task C4: E2E preset switching

**Files:**
- Create: `tests/e2e/test_preset_switching_e2e.py`

- [ ] **Step 1: Write E2E**

```python
"""Per-message preset_label selects the right chain."""

import pytest

from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings


@pytest.mark.asyncio
async def test_preset_label_switches_model(api_client, async_session, provider_seed):
    """provider_seed registers two FauxProviders 'small' and 'big' returning
    distinguishable text. Pick a preset, assert the right one answered."""
    async_session.add(OrgSettings(
        org_id=None, key=MODEL_PRESETS_KEY,
        value={
            "presets": [
                {"label": "big", "chain": ["big/m1"], "is_default": True},
                {"label": "small", "chain": ["small/m1"], "is_default": False},
            ],
            "task_presets": {},
        },
    ))
    await async_session.commit()

    # 1. Default preset.
    resp = await api_client.collect_stream(
        f"/api/v1/ws/{api_client.workspace_id}/conversations",
        json={"content": "hi"},
    )
    assert "from-big" in resp.full_text

    # 2. Explicit preset_label.
    resp = await api_client.collect_stream(
        f"/api/v1/ws/{api_client.workspace_id}/conversations",
        json={"content": "hi", "preset_label": "small"},
    )
    assert "from-small" in resp.full_text
```

Adapt `collect_stream` / `full_text` to the helpers exposed by the
existing e2e test infrastructure.

- [ ] **Step 2: Run, expect PASS**

```bash
uv run pytest tests/e2e/test_preset_switching_e2e.py -v
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_preset_switching_e2e.py
git commit -m "test(e2e): per-message preset_label switches model"
```

---

### Task C5: PR 3 push + codex review

- [ ] **Step 1: Run full suite**

```bash
cd backend
uv run mypy cubeplex/
uv run ruff check cubeplex/
uv run pytest tests/ -v
```

- [ ] **Step 2: Push PR 3**

```bash
git push
gh pr create --title "feat(api): per-message preset_label + thinking on chat endpoint" --body "$(cat <<'EOF'
## Summary
- `CreateMessageBody` accepts `preset_label: str | None` and `thinking: ThinkingLevel = "off"`.
- run_manager applies the per-message overrides to `resolve_preset` + `build_chain_model`.
- Subagent inherits the resolved preset + thinking from the parent run (same `FallbackBoundModel` instance).
- HTTP error matrix: 400 `unknown_preset`, 400 `broken_preset`, 500 `no_default_preset`, 422 invalid `thinking`.

Spec: docs/dev/specs/2026-06-09-llm-snapshot-refactor-design.md

## Test plan
- [ ] `tests/e2e/test_preset_switching_e2e.py` green
- [ ] `tests/e2e/test_preset_errors_e2e.py` green
- [ ] Existing E2E green
EOF
)"
```

- [ ] **Step 3: Run codex review loop until clean**

---

## Spec coverage check

| Spec section | Tasks |
|---|---|
| Module layout (snapshot/resolver/builder) | A2-A9 |
| Data structures (`LLMPreset`, `LLMSnapshot`, schema) | A1, A2 |
| Snapshot storage (OrgSettings JSON shape) | A1, A8, A9 |
| YAML role (seeder-only) | A10 |
| `LLMConfigError` hierarchy | A3 |
| `parse_model_ref` / `resolve_preset` / `resolve_task_preset` | A4, A9 |
| `build_provider` / `build_bound_model` / `build_chain_model` | A5-A7, B1 |
| Main agent wiring | A14, B3, C2 |
| Subagent inheritance | A15, B3 (no-op) |
| Title / compaction task wiring | A12, A15 |
| Summarize reserved but unwired | (verified in A4 test for resolve_task_preset fallback) |
| Image gen unchanged | (no tasks — documented in spec non-goals) |
| `CreateMessageBody` fields | C1 |
| API error semantics | A3, C3 |
| `FailoverEvent` + `_make_failover_publisher` | B2, B3 |
| Observability gap | (documented in PR 2 PR body; cubepi upstream follow-up) |
| Alembic data migration | A11 |
| File deletions | A16 |
| Test changes | A1-A15 (inline); B4, C3, C4 (E2E new) |
| 3-PR plan | Part A / B / C with explicit `gh pr create` calls |
