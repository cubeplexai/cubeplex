# Preset Catalog Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the LLM provider preset catalog from cubepi into cubeplex, restructure it from a flat `(slug, api, base_url)` list into `vendor → region×protocol×plan endpoints → models-with-pricing`, compose `base_url` from parts, and let `config.yaml` reference catalog entries by `preset:` instead of restating base_url/models/pricing.

**Architecture:** A new `cubeplex/llm/catalog/` package owns the data (`vendors.yaml`, `capabilities.yaml`) and a validating loader. The loader flattens vendors into endpoint presets keyed by `preset_key = vendor/region/protocol[/plan]`, composes `base_url = (endpoint.host || region.host) + path`, and resolves named capability profiles into cubepi `CapabilityDescriptor` objects. Three existing consumers (admin presets endpoint, logo lookup, seeder) repoint to it; the seeder gains preset-reference resolution with field-precedence rules. The wizard becomes two-step (vendor → endpoint). Finally cubepi's catalog package is deleted.

**Tech Stack:** Python 3.13 / FastAPI / Pydantic v2 / SQLModel / Alembic (backend); pytest; Next.js 15 / React 19 / TypeScript / Vitest (frontend); `uv` (backend deps), `pnpm` (frontend deps).

**Spec:** `docs/dev/specs/2026-05-22-preset-catalog-redesign-design.md` (read it first — every `§` reference below points there).

**Worktree:** `/home/chris/cubeplex/.worktrees/feat/preset-catalog-redesign` (branch `feat/preset-catalog-redesign`, slot 82 — backend `:8082`, frontend `:3082`, DB `cubeplex_feat_preset_catalog_redesign`). Run `cat .worktree.env` before starting; backend tests run from `backend/`, frontend from `frontend/`.

**Conventions (from CLAUDE.md):** type annotations everywhere (mypy strict), 100-char lines, `uv add` for deps, `alembic revision --autogenerate` for migrations (none expected here — no schema change), `utc_isoformat()` for DB datetimes. Stay on this branch; never switch to main. Commit after every green step.

---

## File Structure

**New (cubeplex backend):**
- `cubeplex/llm/catalog/__init__.py` — public API: `load_catalog()`, `get_catalog()` (cached), re-exports.
- `cubeplex/llm/catalog/types.py` — Pydantic models: `Pricing`, `ModelPreset`, `Endpoint`, `Region`, `Vendor`, and the resolved/derived `ResolvedEndpoint`, `Catalog`.
- `cubeplex/llm/catalog/loader.py` — YAML load + all validations (§4.2/§4.3/§4.4) + `compose_base_url` + `preset_key_for` + capability resolution + flattening.
- `cubeplex/llm/catalog/data/vendors.yaml` — ported nested catalog data.
- `cubeplex/llm/catalog/data/capabilities.yaml` — named capability profiles.
- `tests/unit/llm/catalog/test_loader.py` — loader unit tests.
- `tests/unit/llm/catalog/test_composition.py` — base_url composition + parity test.
- `tests/unit/llm/catalog/data/flat_providers_snapshot.yaml` — frozen copy of today's cubepi `providers.yaml` (parity fixture).

**Modified (cubeplex backend):**
- `cubeplex/api/routes/v1/admin_llm.py` — return nested vendor list (§5.1).
- `cubeplex/api/routes/v1/admin_providers.py` — `_resolve_logo` resolves via catalog vendor.
- `cubeplex/seeders/provider_seeder.py` — `preset:` resolution + precedence (§6.2) + validation (§6.3).
- `config.development.local.yaml` (+ `config.yaml` if present) — exhaustive rewrite to `preset:` form (§6.1/§6.4).

**Modified (frontend):**
- `frontend/packages/core/src/types/provider.ts` — replace flat `ProviderPreset` with nested `VendorPreset` + `EndpointPreset`.
- `frontend/packages/core/src/api/providers.ts` — `listPresets` returns `VendorPreset[]`.
- `frontend/packages/web/components/admin/models/wizard/PresetPicker.tsx` — list vendors.
- `frontend/packages/web/components/admin/models/wizard/ConfigureStep.tsx` + `ProviderConfigForm.tsx` — region/protocol/plan selectors driving composed base_url + filtered models.
- `frontend/packages/web/components/admin/models/wizard/wizardMachine.ts` — `pickVendor` + selected endpoint state.

**Deleted (cubepi, Phase G):**
- `cubepi/providers/catalog/` (loader, types, `data/providers.yaml`, tests) — via a cubepi release + dependency bump.

**Decoupling decision:** cubeplex's catalog `types.py` declares its **own** `WireApi` literal (the 3 protocol strings) and imports only `CapabilityDescriptor` from `cubepi.providers.capability` (a stable, non-catalog module). This removes any cubeplex→cubepi-catalog import so Phases A–F do not depend on the cubepi release; Phase G only deletes cubepi's now-unused catalog.

---

## Phase A — Catalog package: types, composition, loader, validations

No real data yet — tests use small inline fixtures so logic is verified in isolation.

### Task A1: Pydantic source-schema types

**Files:**
- Create: `cubeplex/llm/catalog/__init__.py` (empty for now)
- Create: `cubeplex/llm/catalog/types.py`
- Test: `tests/unit/llm/catalog/test_loader.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/llm/catalog/__init__.py` (empty) and `tests/unit/llm/catalog/test_loader.py`:

```python
from cubeplex.llm.catalog.types import Endpoint, ModelPreset, Pricing, Region, Vendor


def test_vendor_parses_minimal():
    v = Vendor.model_validate(
        {
            "vendor": "deepseek",
            "display_name": "DeepSeek",
            "short_name": "DeepSeek",
            "logo": "deepseek",
            "category": "saas",
            "description": "DeepSeek V-series.",
            "regions": {"cn": {"host": "https://api.deepseek.com"}},
            "endpoints": [
                {"region": "cn", "protocol": "openai-completions", "capability": "openai-compat-basic"}
            ],
            "models": [
                {
                    "model_id": "deepseek-v4",
                    "display_name": "DeepSeek V4",
                    "context_window": 64000,
                    "max_tokens": 8192,
                    "input_modalities": ["text"],
                    "reasoning": True,
                    "pricing": {"input": 0.27, "output": 1.10},
                }
            ],
        }
    )
    assert v.regions["cn"].host == "https://api.deepseek.com"
    assert v.endpoints[0].protocol == "openai-completions"
    assert v.endpoints[0].plan is None
    assert v.models[0].pricing.cache_read == 0.0
    assert v.models[0].plan is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/llm/catalog/test_loader.py::test_vendor_parses_minimal -v`
Expected: FAIL — `ModuleNotFoundError: cubeplex.llm.catalog.types`.

- [ ] **Step 3: Write the types**

`cubeplex/llm/catalog/types.py`:

```python
"""Catalog source-schema + resolved/derived types. Spec §4."""

from __future__ import annotations

from typing import Literal

from cubepi.providers.capability import CapabilityDescriptor
from pydantic import BaseModel, Field

# The protocols cubeplex offers in its catalog. Mirrors cubepi's WireApi but
# declared locally so the catalog does not import cubepi's (to-be-deleted)
# catalog package. See plan "Decoupling decision".
WireApi = Literal["anthropic-messages", "openai-completions", "openai-responses"]

# A model's plan membership: a single plan, a list, or None (untagged vendor).
PlanRef = str | list[str] | None


class Pricing(BaseModel):
    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0


class ModelPreset(BaseModel):
    model_id: str
    display_name: str
    context_window: int
    max_tokens: int
    input_modalities: list[str]
    reasoning: bool = False
    plan: PlanRef = None
    pricing: Pricing

    def plans(self) -> list[str] | None:
        """Normalized plan list, or None for untagged."""
        if self.plan is None:
            return None
        return [self.plan] if isinstance(self.plan, str) else list(self.plan)


class Region(BaseModel):
    host: str


class Endpoint(BaseModel):
    region: str
    protocol: WireApi
    plan: str | None = None
    path: str = ""
    host: str | None = None  # overrides region host (§4.1)
    base_url: str | None = None  # full override, bypasses composition (§4.1)
    capability: str | dict  # profile name (str) or inline descriptor (dict)
    key: str | None = None  # optional preset_key override (§4.4)


class Vendor(BaseModel):
    vendor: str
    display_name: str
    short_name: str
    logo: str | None = None
    category: Literal["saas", "oss-framework", "custom"]
    description: str
    regions: dict[str, Region] = Field(default_factory=dict)
    endpoints: list[Endpoint] = Field(default_factory=list)
    models: list[ModelPreset] = Field(default_factory=list)


class ResolvedEndpoint(BaseModel):
    """One flattened endpoint preset — what consumers (seeder/API) read."""

    preset_key: str
    vendor: str
    region: str
    protocol: WireApi
    plan: str | None
    base_url: str
    capability: CapabilityDescriptor
    models: list[ModelPreset]  # the subset serving this endpoint (§4 membership)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/llm/catalog/test_loader.py::test_vendor_parses_minimal -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cubeplex/llm/catalog/__init__.py cubeplex/llm/catalog/types.py tests/unit/llm/catalog/
git commit -m "feat(catalog): source-schema + resolved types for nested preset catalog"
```

### Task A2: `compose_base_url` (§4.1)

**Files:**
- Create: `cubeplex/llm/catalog/loader.py`
- Test: `tests/unit/llm/catalog/test_composition.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/llm/catalog/test_composition.py`:

```python
import pytest

from cubeplex.llm.catalog.loader import compose_base_url
from cubeplex.llm.catalog.types import Endpoint, Region


@pytest.mark.parametrize(
    "regions,endpoint,expected",
    [
        # A. path differs, region host
        ({"cn": Region(host="https://open.bigmodel.cn")},
         Endpoint(region="cn", protocol="openai-completions", path="/api/coding/paas/v4", capability="x"),
         "https://open.bigmodel.cn/api/coding/paas/v4"),
        # B. host override (Alibaba coding lives on a different domain)
        ({"cn": Region(host="https://dashscope.aliyuncs.com")},
         Endpoint(region="cn", protocol="openai-completions",
                  host="https://coding.dashscope.aliyuncs.com", path="/v1", capability="x"),
         "https://coding.dashscope.aliyuncs.com/v1"),
        # C. empty path (DeepSeek openai)
        ({"cn": Region(host="https://api.deepseek.com")},
         Endpoint(region="cn", protocol="openai-completions", capability="x"),
         "https://api.deepseek.com"),
        # D. full base_url override bypasses composition
        ({"intl": Region(host="https://ignored")},
         Endpoint(region="intl", protocol="openai-responses",
                  base_url="https://chatgpt.com/backend-api/codex", capability="x"),
         "https://chatgpt.com/backend-api/codex"),
    ],
)
def test_compose_base_url(regions, endpoint, expected):
    assert compose_base_url(regions, endpoint) == expected


def test_compose_base_url_unknown_region_raises():
    with pytest.raises(ValueError, match="unknown region"):
        compose_base_url({"cn": Region(host="https://x")},
                         Endpoint(region="intl", protocol="openai-completions", capability="x"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/llm/catalog/test_composition.py -v`
Expected: FAIL — `ModuleNotFoundError: cubeplex.llm.catalog.loader`.

- [ ] **Step 3: Implement `compose_base_url`**

Create `cubeplex/llm/catalog/loader.py`:

```python
"""Catalog loader: YAML → validated, flattened catalog. Spec §4."""

from __future__ import annotations

from cubeplex.llm.catalog.types import Endpoint, Region


def compose_base_url(regions: dict[str, Region], endpoint: Endpoint) -> str:
    """base_url = (endpoint.host || regions[endpoint.region].host) + endpoint.path.

    A full ``endpoint.base_url`` bypasses composition entirely (§4.1).
    """
    if endpoint.base_url is not None:
        return endpoint.base_url
    host = endpoint.host
    if host is None:
        region = regions.get(endpoint.region)
        if region is None:
            raise ValueError(f"endpoint references unknown region {endpoint.region!r}")
        host = region.host
    return host + endpoint.path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/llm/catalog/test_composition.py -v`
Expected: PASS (5 cases).

- [ ] **Step 5: Commit**

```bash
git add cubeplex/llm/catalog/loader.py tests/unit/llm/catalog/test_composition.py
git commit -m "feat(catalog): compose_base_url with host/path/full-override (§4.1)"
```

### Task A3: `preset_key_for` (§4.4)

**Files:**
- Modify: `cubeplex/llm/catalog/loader.py`
- Test: `tests/unit/llm/catalog/test_loader.py`

- [ ] **Step 1: Write the failing test** (append to `test_loader.py`)

```python
from cubeplex.llm.catalog.loader import preset_key_for


def test_preset_key_without_plan():
    ep = Endpoint(region="cn", protocol="anthropic-messages", capability="x")
    assert preset_key_for("deepseek", ep) == "deepseek/cn/anthropic-messages"


def test_preset_key_with_plan():
    ep = Endpoint(region="cn", protocol="openai-completions", plan="coding", capability="x")
    assert preset_key_for("zhipu", ep) == "zhipu/cn/openai-completions/coding"


def test_preset_key_override_wins():
    ep = Endpoint(region="cn", protocol="openai-completions", key="pretty-key", capability="x")
    assert preset_key_for("zhipu", ep) == "pretty-key"
```

(Add `from cubeplex.llm.catalog.types import Endpoint` to the imports if not present.)

- [ ] **Step 2: Run** `uv run pytest tests/unit/llm/catalog/test_loader.py -k preset_key -v` → FAIL (`preset_key_for` undefined).

- [ ] **Step 3: Implement** (append to `loader.py`):

```python
def preset_key_for(vendor: str, endpoint: Endpoint) -> str:
    """preset_key = vendor/region/protocol[/plan], or endpoint.key override (§4.4)."""
    if endpoint.key is not None:
        return endpoint.key
    parts = [vendor, endpoint.region, endpoint.protocol]
    if endpoint.plan is not None:
        parts.append(endpoint.plan)
    return "/".join(parts)
```

- [ ] **Step 4: Run** the same command → PASS.

- [ ] **Step 5: Commit**

```bash
git add cubeplex/llm/catalog/loader.py tests/unit/llm/catalog/test_loader.py
git commit -m "feat(catalog): preset_key_for vendor/region/protocol[/plan] (§4.4)"
```

### Task A4: capability profile resolution (§4.3)

**Files:**
- Modify: `cubeplex/llm/catalog/loader.py`
- Test: `tests/unit/llm/catalog/test_loader.py`

- [ ] **Step 1: Write the failing test** (append)

```python
import pytest

from cubeplex.llm.catalog.loader import resolve_capability


def test_resolve_capability_named():
    profiles = {"openai-compat-basic": {"supports_tools": True, "supports_images": True}}
    cap = resolve_capability("openai-compat-basic", profiles)
    assert cap.supports_tools is True
    assert cap.supports_images is True


def test_resolve_capability_inline_dict():
    cap = resolve_capability({"supports_images": True, "max_tokens_field": "max_completion_tokens"}, {})
    assert cap.supports_images is True
    assert cap.max_tokens_field == "max_completion_tokens"


def test_resolve_capability_unknown_name_fails_loudly():
    with pytest.raises(ValueError, match="unknown capability profile"):
        resolve_capability("does-not-exist", {"openai-compat-basic": {}})
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/llm/catalog/test_loader.py -k resolve_capability -v` → FAIL.

- [ ] **Step 3: Implement** (append to `loader.py`; add `from cubeplex.llm.catalog.types import CapabilityDescriptor` — re-export it from types, or import from cubepi directly):

```python
from cubepi.providers.capability import CapabilityDescriptor


def resolve_capability(
    ref: str | dict, profiles: dict[str, dict]
) -> CapabilityDescriptor:
    """A scalar string is a profile reference; a mapping is inline (§4.3).

    An unknown profile name fails loudly (not a silent empty descriptor).
    """
    if isinstance(ref, str):
        if ref not in profiles:
            raise ValueError(f"unknown capability profile {ref!r}")
        return CapabilityDescriptor.model_validate(profiles[ref])
    return CapabilityDescriptor.model_validate(ref)
```

- [ ] **Step 4: Run** the same command → PASS.

- [ ] **Step 5: Commit**

```bash
git add cubeplex/llm/catalog/loader.py tests/unit/llm/catalog/test_loader.py
git commit -m "feat(catalog): capability profile resolution, loud-fail on unknown (§4.3)"
```

### Task A5: membership + plan validations (§4.2) and full `load_catalog`

**Files:**
- Modify: `cubeplex/llm/catalog/loader.py`, `cubeplex/llm/catalog/__init__.py`
- Test: `tests/unit/llm/catalog/test_loader.py`

- [ ] **Step 1: Write the failing tests** (append). These cover: untagged-serves-all, tiered membership by plan intersection, all-or-nothing mixing rejected, dangling endpoint rejected, unreachable model rejected, duplicate preset_key rejected.

```python
from cubeplex.llm.catalog.loader import build_catalog

PROFILES = {"x": {}}


def _vendor(**over):
    base = {
        "vendor": "v", "display_name": "V", "short_name": "V", "logo": None,
        "category": "saas", "description": "d",
        "regions": {"cn": {"host": "https://h"}},
        "endpoints": [{"region": "cn", "protocol": "openai-completions", "capability": "x"}],
        "models": [{"model_id": "m1", "display_name": "M1", "context_window": 1, "max_tokens": 1,
                    "input_modalities": ["text"], "pricing": {"input": 1, "output": 1}}],
    }
    base.update(over)
    return base


def test_untagged_endpoint_serves_all_models():
    cat = build_catalog([_vendor()], PROFILES)
    ep = cat.resolve("v/cn/openai-completions")
    assert [m.model_id for m in ep.models] == ["m1"]


def test_tiered_membership_by_plan_intersection():
    v = _vendor(
        endpoints=[
            {"region": "cn", "protocol": "openai-completions", "plan": "general", "capability": "x"},
            {"region": "cn", "protocol": "openai-completions", "plan": "coding",
             "path": "/coding", "capability": "x"},
        ],
        models=[
            {"model_id": "g", "display_name": "G", "context_window": 1, "max_tokens": 1,
             "input_modalities": ["text"], "plan": "general", "pricing": {"input": 1, "output": 1}},
            {"model_id": "c", "display_name": "C", "context_window": 1, "max_tokens": 1,
             "input_modalities": ["text"], "plan": "coding", "pricing": {"input": 1, "output": 1}},
        ],
    )
    cat = build_catalog([v], PROFILES)
    assert [m.model_id for m in cat.resolve("v/cn/openai-completions/general").models] == ["g"]
    assert [m.model_id for m in cat.resolve("v/cn/openai-completions/coding").models] == ["c"]


def test_mixed_tagged_untagged_rejected():
    v = _vendor(
        endpoints=[{"region": "cn", "protocol": "openai-completions", "plan": "coding", "capability": "x"}],
        models=[{"model_id": "m", "display_name": "M", "context_window": 1, "max_tokens": 1,
                 "input_modalities": ["text"], "pricing": {"input": 1, "output": 1}}],  # untagged model
    )
    with pytest.raises(ValueError, match="mix"):
        build_catalog([v], PROFILES)


def test_dangling_endpoint_rejected():
    v = _vendor(
        endpoints=[
            {"region": "cn", "protocol": "openai-completions", "plan": "general", "capability": "x"},
            {"region": "cn", "protocol": "openai-completions", "plan": "coding", "path": "/c", "capability": "x"},
        ],
        models=[{"model_id": "g", "display_name": "G", "context_window": 1, "max_tokens": 1,
                 "input_modalities": ["text"], "plan": "general", "pricing": {"input": 1, "output": 1}}],
    )
    with pytest.raises(ValueError, match="no model"):
        build_catalog([v], PROFILES)


def test_unreachable_model_rejected():
    v = _vendor(
        endpoints=[{"region": "cn", "protocol": "openai-completions", "plan": "general", "capability": "x"}],
        models=[{"model_id": "c", "display_name": "C", "context_window": 1, "max_tokens": 1,
                 "input_modalities": ["text"], "plan": "coding", "pricing": {"input": 1, "output": 1}}],
    )
    with pytest.raises(ValueError, match="no endpoint"):
        build_catalog([v], PROFILES)


def test_duplicate_preset_key_rejected():
    v1, v2 = _vendor(), _vendor()  # same vendor name → same composed key
    with pytest.raises(ValueError, match="duplicate preset_key"):
        build_catalog([v1, v2], PROFILES)


def test_duplicate_endpoint_tuple_rejected_even_with_distinct_key_overrides():
    # Two endpoints with the SAME (region, protocol, plan) but different key:
    # overrides must still be rejected — preset_key dedup alone wouldn't catch it.
    v = _vendor(endpoints=[
        {"region": "cn", "protocol": "openai-completions", "key": "k1", "capability": "x"},
        {"region": "cn", "protocol": "openai-completions", "key": "k2", "capability": "x"},
    ])
    with pytest.raises(ValueError, match="duplicate endpoint"):
        build_catalog([v], PROFILES)
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/llm/catalog/test_loader.py -v` → the new tests FAIL (`build_catalog` undefined).

- [ ] **Step 3: Implement** the `Catalog` type and `build_catalog`. Add `Catalog` to `types.py`:

```python
class Catalog(BaseModel):
    vendors: list[Vendor]
    endpoints: dict[str, ResolvedEndpoint]  # keyed by preset_key

    def resolve(self, preset_key: str) -> ResolvedEndpoint:
        if preset_key not in self.endpoints:
            raise KeyError(preset_key)
        return self.endpoints[preset_key]
```

Append to `loader.py`:

```python
from cubeplex.llm.catalog.types import Catalog, ResolvedEndpoint, Vendor


def _validate_plan_consistency(v: Vendor) -> None:
    ep_tagged = [e.plan is not None for e in v.endpoints]
    m_tagged = [m.plan is not None for m in v.models]
    tagged = ep_tagged + m_tagged
    if any(tagged) and not all(tagged):
        raise ValueError(f"vendor {v.vendor!r} may not mix plan-tagged and untagged endpoints/models")


def _models_for(v: Vendor, endpoint: Endpoint) -> list[ModelPreset]:
    if endpoint.plan is None:  # untagged vendor → every endpoint serves every model
        return list(v.models)
    return [m for m in v.models if endpoint.plan in (m.plans() or [])]


def build_catalog(raw_vendors: list[dict | Vendor], profiles: dict[str, dict]) -> Catalog:
    vendors = [v if isinstance(v, Vendor) else Vendor.model_validate(v) for v in raw_vendors]
    endpoints: dict[str, ResolvedEndpoint] = {}
    for v in vendors:
        _validate_plan_consistency(v)
        # (region, protocol, plan) tuple uniqueness — enforced INDEPENDENTLY of
        # preset_key, because a `key:` override would otherwise let two identical
        # tuples through the preset_key dedup (codex P2, spec §4.2).
        seen_tuples: set[tuple[str, str, str | None]] = set()
        for ep in v.endpoints:
            tup = (ep.region, ep.protocol, ep.plan)
            if tup in seen_tuples:
                raise ValueError(f"vendor {v.vendor!r} duplicate endpoint tuple {tup!r}")
            seen_tuples.add(tup)
            models = _models_for(v, ep)
            if ep.plan is not None and not models:
                raise ValueError(
                    f"vendor {v.vendor!r} endpoint plan {ep.plan!r} matches no model (dangling)"
                )
            key = preset_key_for(v.vendor, ep)
            if key in endpoints:
                raise ValueError(f"duplicate preset_key {key!r}")
            endpoints[key] = ResolvedEndpoint(
                preset_key=key, vendor=v.vendor, region=ep.region, protocol=ep.protocol,
                plan=ep.plan, base_url=compose_base_url(v.regions, ep),
                capability=resolve_capability(ep.capability, profiles), models=models,
            )
        # unreachable-model check: every model's plan(s) must hit some endpoint
        ep_plans = {e.plan for e in v.endpoints}
        for m in v.models:
            mplans = m.plans()
            if mplans is not None and not (set(mplans) & ep_plans):
                raise ValueError(
                    f"vendor {v.vendor!r} model {m.model_id!r} plan(s) {mplans} match no endpoint (unreachable)"
                )
    return Catalog(vendors=vendors, endpoints=endpoints)
```

- [ ] **Step 4: Run** `uv run pytest tests/unit/llm/catalog/test_loader.py -v` → all PASS.

- [ ] **Step 5: Implement `load_catalog()` + cache.** Append to `loader.py`:

```python
from functools import cache
from pathlib import Path

import yaml

_DATA_DIR = Path(__file__).parent / "data"


@cache
def load_catalog() -> Catalog:
    vendors_raw = yaml.safe_load((_DATA_DIR / "vendors.yaml").read_text("utf-8"))
    profiles = yaml.safe_load((_DATA_DIR / "capabilities.yaml").read_text("utf-8"))
    if not isinstance(vendors_raw, list):
        raise ValueError("vendors.yaml must be a top-level list")
    return build_catalog(vendors_raw, profiles or {})
```

`cubeplex/llm/catalog/__init__.py`:

```python
from cubeplex.llm.catalog.loader import build_catalog, compose_base_url, load_catalog, preset_key_for
from cubeplex.llm.catalog.types import (
    Catalog, Endpoint, ModelPreset, Pricing, Region, ResolvedEndpoint, Vendor, WireApi,
)

__all__ = [
    "Catalog", "Endpoint", "ModelPreset", "Pricing", "Region", "ResolvedEndpoint",
    "Vendor", "WireApi", "build_catalog", "compose_base_url", "load_catalog", "preset_key_for",
]
```

- [ ] **Step 6: Commit**

```bash
git add cubeplex/llm/catalog/ tests/unit/llm/catalog/test_loader.py
git commit -m "feat(catalog): build_catalog with plan/membership validations + load_catalog (§4.2)"
```

---

## Phase B — Port the data + parity guard

### Task B1: Freeze the current flat catalog as a parity fixture

**Files:**
- Create: `tests/unit/llm/catalog/data/flat_providers_snapshot.yaml`

- [ ] **Step 1: Copy the published flat catalog** (the file cubeplex currently resolves at runtime):

```bash
cp backend/.venv/lib/python3.13/site-packages/cubepi/providers/catalog/data/providers.yaml \
   backend/tests/unit/llm/catalog/data/flat_providers_snapshot.yaml
```

- [ ] **Step 2: Commit the fixture**

```bash
git add tests/unit/llm/catalog/data/flat_providers_snapshot.yaml
git commit -m "test(catalog): freeze current flat providers.yaml as parity fixture"
```

### Task B2: Author `vendors.yaml` + `capabilities.yaml`

**Files:**
- Create: `cubeplex/llm/catalog/data/vendors.yaml`
- Create: `cubeplex/llm/catalog/data/capabilities.yaml`

This is data entry, not logic — but it must reproduce every flat entry. Work vendor-by-vendor from `flat_providers_snapshot.yaml`. The snapshot's flat entries group into these vendors (regions/plans/protocols noted):

| Vendor | endpoints (region/protocol/plan) | host(s) |
|---|---|---|
| anthropic | intl/anthropic-messages | api.anthropic.com |
| openai | intl/openai-responses, intl/openai-completions (`openai-legacy`) | api.openai.com/v1 |
| deepseek | cn/anthropic-messages (`/anthropic`), cn/openai-completions | api.deepseek.com |
| aliyun (Qwen models) | intl & cn /openai-completions, +coding (intl & cn) | dashscope-intl / dashscope |
| doubao (volcengine) | cn/openai-completions (`/api/v3`), cn/anthropic-messages coding (`/api/coding`) | ark.cn-beijing.volces.com |
| moonshot | intl & cn /openai-completions, +coding (same URL, diff models) | api.moonshot.ai / .cn |
| zhipu | intl & cn /openai-completions, +coding (`/api/coding/paas/v4`) | api.z.ai / open.bigmodel.cn |
| minimax | intl & cn /openai-completions, +coding /anthropic-messages | api.minimax.io / api.minimaxi.com |
| xai, mistral, openrouter, together-ai, groq, fireworks | intl/openai-completions | per-vendor |
| vllm, ollama, lm-studio, tgi | localhost/openai-completions, category oss-framework | localhost |
| anthropic-claude-code, openai-codex | use `base_url` full override (irregular) | — |
| custom-openai, custom-anthropic | category custom, base_url "" | — |

Define capability profiles in `capabilities.yaml`: at minimum `openai-compat-basic` (the ~20 vanilla `openai-completions` vendors), `anthropic-native`, `openai-responses`, and the per-vendor reasoning variants present in the snapshot (deepseek-anthropic, etc. — copy the `capability:` blocks verbatim from the snapshot, deduping identical ones into one profile name).

- [ ] **Step 1:** Author `capabilities.yaml` first — extract each distinct `capability:` block from the snapshot, name it, dedup. Example head:

```yaml
openai-compat-basic:
  temperature: { mode: free, min: 0.0, max: 2.0, default: 1.0 }
  max_tokens_field: max_tokens
  supports_tools: true
  supports_images: true
anthropic-native:
  reasoning_off_payload: { thinking: { type: disabled } }
  reasoning_on_payload:  { thinking: { type: enabled } }
  reasoning_level: { path: thinking.budget_tokens, kind: int_budget, level_budgets: { off: 0, minimal: 1024, low: 2048, medium: 8192, high: 16384, xhigh: 16384 } }
  temperature: { mode: free, min: 0.0, max: 1.0, default: 1.0 }
  max_tokens_field: max_tokens
  supports_tools: true
  supports_images: true
# … openai-responses, deepseek-anthropic, etc. — verbatim from snapshot blocks
```

- [ ] **Step 2:** Author `vendors.yaml` — one entry per vendor per the table, moving each flat entry's `default_models` into the vendor `models` pool (tagging `plan:` only for tiered vendors), referencing capability profiles by name. Models need `pricing:` — for ported presets that had none, set `pricing: { input: 0, output: 0 }` (cost is filled per-deployment via config override; the catalog default is zero so nothing silently bills wrong). Carry over `context_window`, `max_tokens`, `input_modalities`, `reasoning`.

- [ ] **Step 3: Sanity-load** to catch validation errors early:

Run: `cd backend && uv run python -c "from cubeplex.llm.catalog import load_catalog; c=load_catalog(); print(len(c.vendors), 'vendors', len(c.endpoints), 'endpoints')"`
Expected: prints counts, no exception. Fix any validation error it raises.

- [ ] **Step 4: Commit**

```bash
git add cubeplex/llm/catalog/data/vendors.yaml cubeplex/llm/catalog/data/capabilities.yaml
git commit -m "feat(catalog): port flat providers.yaml to nested vendors + capability profiles"
```

### Task B3: base_url parity test (§4.1 regression guard)

**Files:**
- Modify: `tests/unit/llm/catalog/test_composition.py`

- [ ] **Step 1: Write the failing test**

```python
from collections import Counter
from pathlib import Path

import yaml

from cubeplex.llm.catalog import load_catalog

_SNAPSHOT = Path(__file__).parent / "data" / "flat_providers_snapshot.yaml"


def test_every_flat_base_url_is_reproduced():
    """Each flat (api, base_url) must be reproduced with the SAME MULTIPLICITY.

    Multiplicity matters (codex P2): two flat entries can share an identical
    (api, base_url) — e.g. moonshot vs moonshot-coding both
    (openai-completions, https://api.moonshot.ai/v1). A plain set would let one
    new endpoint satisfy both; a Counter requires the new catalog to produce
    the same number of endpoints per (api, base_url). Flat entries with
    base_url == "" (custom-*) are excluded — they have no composed URL.
    """
    flat = yaml.safe_load(_SNAPSHOT.read_text("utf-8"))
    catalog = load_catalog()
    produced = Counter((e.protocol, e.base_url) for e in catalog.endpoints.values())
    expected = Counter(
        (entry["api"], entry["base_url"]) for entry in flat if entry.get("base_url")
    )
    # Every expected (api, base_url) must appear at least as many times as the
    # flat catalog had it. (>= not == so genuinely new endpoints are allowed.)
    deficits = {pair: (cnt, produced[pair]) for pair, cnt in expected.items() if produced[pair] < cnt}
    assert not deficits, f"flat URLs under-reproduced (expected,got): {deficits}"
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/unit/llm/catalog/test_composition.py::test_every_flat_base_url_is_reproduced -v`
Expected: FAIL initially if any vendor entry is missing/mismatched — the failure message lists the under-reproduced `(api, base_url)` pairs as `{pair: (expected_count, got_count)}`.

- [ ] **Step 3: Fix `vendors.yaml`** until the test passes (adjust host/path/override for each listed miss). No code change — data only.

- [ ] **Step 4: Run** the test → PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/llm/catalog/test_composition.py cubeplex/llm/catalog/data/vendors.yaml
git commit -m "test(catalog): byte-parity of composed base_url vs frozen flat catalog (§4.1)"
```

---

## Phase C — Repoint consumers + API contract + core types

### Task C1: `admin_llm.py` returns the nested vendor list (§5.1)

**Files:**
- Modify: `cubeplex/api/routes/v1/admin_llm.py`
- Modify: `cubeplex/llm/catalog/loader.py` (add `to_api()` serializer)
- Test: `tests/unit/llm/catalog/test_loader.py`, `tests/e2e` (existing presets endpoint test if any)

- [ ] **Step 1: Write the failing test** (append to `test_loader.py`) for the API shape:

```python
def test_catalog_to_api_shape():
    from cubeplex.llm.catalog import load_catalog

    api = load_catalog().to_api()
    assert isinstance(api, list)
    v = next(x for x in api if x["vendor"] == "deepseek")
    assert {"vendor", "display_name", "short_name", "logo", "category",
            "description", "endpoints", "models"} <= v.keys()
    ep = v["endpoints"][0]
    assert {"preset_key", "region", "protocol", "plan", "base_url", "model_ids"} <= ep.keys()
    m = v["models"][0]
    assert {"model_id", "display_name", "plan", "context_window", "max_tokens",
            "input_modalities", "reasoning", "pricing"} <= m.keys()
```

- [ ] **Step 2: Run** → FAIL (`to_api` undefined).

- [ ] **Step 3: Implement `Catalog.to_api()`** in `types.py` (uses `endpoints` already grouped by vendor). Add a helper on `Catalog`:

```python
def to_api(self) -> list[dict]:
    """Nested vendor list for GET /admin/llm/presets (spec §5.1)."""
    out: list[dict] = []
    for v in self.vendors:
        v_eps = [e for e in self.endpoints.values() if e.vendor == v.vendor]
        out.append({
            "vendor": v.vendor, "display_name": v.display_name, "short_name": v.short_name,
            "logo": v.logo, "category": v.category, "description": v.description,
            "endpoints": [{
                "preset_key": e.preset_key, "region": e.region, "protocol": e.protocol,
                "plan": e.plan, "base_url": e.base_url,
                "model_ids": [m.model_id for m in e.models],
            } for e in v_eps],
            "models": [{
                "model_id": m.model_id, "display_name": m.display_name, "plan": m.plans(),
                "context_window": m.context_window, "max_tokens": m.max_tokens,
                "input_modalities": m.input_modalities, "reasoning": m.reasoning,
                "pricing": m.pricing.model_dump(),
            } for m in v.models],
        })
    return out
```

- [ ] **Step 4: Rewrite the endpoint** `cubeplex/api/routes/v1/admin_llm.py`:

```python
@router.get("/presets")
async def list_provider_presets(
    *,
    user: Annotated[User, Depends(require_org_admin)],
) -> list[dict[str, Any]]:
    """Return cubeplex's provider-preset catalog as a nested vendor list (spec §5.1)."""
    from cubeplex.llm.catalog import load_catalog

    return load_catalog().to_api()
```

- [ ] **Step 5: Run** `cd backend && uv run pytest tests/unit/llm/catalog/ -v` → PASS. Also run any existing presets-endpoint test: `uv run pytest tests/ -k preset -v`.

- [ ] **Step 6: Commit**

```bash
git add cubeplex/api/routes/v1/admin_llm.py cubeplex/llm/catalog/ tests/unit/llm/catalog/test_loader.py
git commit -m "feat(catalog): /admin/llm/presets returns nested vendor list (§5.1)"
```

### Task C2: `admin_providers.py` logo via catalog

**Files:**
- Modify: `cubeplex/api/routes/v1/admin_providers.py:148-156`
- Test: `tests/unit` (add a focused test for `_resolve_logo`)

`_resolve_logo` currently calls `get_provider_preset(preset_slug).logo`. The new `preset_slug` is a `preset_key` (`vendor/region/protocol[/plan]`); logo lives on the **vendor**.

- [ ] **Step 1: Write the failing test** `tests/unit/test_resolve_logo.py`:

```python
from cubeplex.api.routes.v1.admin_providers import _resolve_logo


def test_resolve_logo_by_preset_key():
    assert _resolve_logo("deepseek/cn/anthropic-messages") == "deepseek"


def test_resolve_logo_by_key_override(monkeypatch):
    # A `key:`-overridden preset_key does NOT start with the vendor, so a split("/")
    # approach would fail. Inject a catalog whose endpoint key is "pretty-id" and
    # assert the logo still resolves via the vendor — a real regression guard.
    from cubeplex.llm.catalog import build_catalog
    import cubeplex.api.routes.v1.admin_providers as mod
    import cubeplex.llm.catalog as catmod  # _resolve_logo does `from cubeplex.llm.catalog import load_catalog`

    catalog = build_catalog(
        [{
            "vendor": "deepseek", "display_name": "DeepSeek", "short_name": "DeepSeek",
            "logo": "deepseek", "category": "saas", "description": "d",
            "regions": {"cn": {"host": "https://api.deepseek.com"}},
            "endpoints": [{"region": "cn", "protocol": "openai-completions",
                           "key": "pretty-id", "capability": "x"}],
            "models": [{"model_id": "m", "display_name": "M", "context_window": 1,
                        "max_tokens": 1, "input_modalities": ["text"],
                        "pricing": {"input": 1, "output": 1}}],
        }],
        {"x": {}},
    )
    monkeypatch.setattr(catmod, "load_catalog", lambda: catalog)
    assert mod._resolve_logo("pretty-id") == "deepseek"


def test_resolve_logo_none_for_unknown():
    assert _resolve_logo("nope/x/y") is None
    assert _resolve_logo(None) is None
```

- [ ] **Step 2: Run** → FAIL (still uses cubepi `get_provider_preset`).

- [ ] **Step 3: Implement** — replace the import `from cubepi.providers.catalog import get_provider_preset` and `_resolve_logo`. Resolve the endpoint **through the catalog** (so `key:` overrides work — codex P2 — not a `split("/")` that assumes the key starts with the vendor):

```python
def _resolve_logo(preset_slug: str | None) -> str | None:
    """Resolve the brand-icon id from the catalog vendor. None if unknown."""
    if not preset_slug:
        return None
    try:
        from cubeplex.llm.catalog import load_catalog

        catalog = load_catalog()
        ep = catalog.resolve(preset_slug)  # works for composed keys AND key: overrides
        for v in catalog.vendors:
            if v.vendor == ep.vendor:
                return v.logo
    except Exception:
        return None
    return None
```

- [ ] **Step 4: Run** `uv run pytest tests/unit/test_resolve_logo.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add cubeplex/api/routes/v1/admin_providers.py tests/unit/test_resolve_logo.py
git commit -m "feat(catalog): resolve provider logo via catalog vendor (preset_key)"
```

### Task C3: `@cubeplex/core` nested preset types + `listPresets`

**Files:**
- Modify: `frontend/packages/core/src/types/provider.ts:17-37`
- Modify: `frontend/packages/core/src/api/providers.ts:95-98`

- [ ] **Step 1: Replace `ProviderPreset`** in `provider.ts` with the nested shape:

```typescript
export interface EndpointPreset {
  preset_key: string
  region: string
  protocol: WireApi
  plan: string | null
  base_url: string
  model_ids: string[]
}

export interface ModelPresetEntry {
  model_id: string
  display_name: string
  plan: string[] | null
  context_window: number
  max_tokens: number
  input_modalities: string[]
  reasoning: boolean
  pricing: { input: number; output: number; cache_read?: number; cache_write?: number }
}

export interface VendorPreset {
  vendor: string
  display_name: string
  short_name: string
  logo: string | null
  category: 'saas' | 'oss-framework' | 'custom'
  description: string
  endpoints: EndpointPreset[]
  models: ModelPresetEntry[]
}
```

Remove the old `ProviderPreset` interface and its `AuthSpec`/`default_models` usage if now unused elsewhere (grep first — `git grep -n ProviderPreset frontend/packages`).

- [ ] **Step 2: Update `listPresets`** in `api/providers.ts`:

```typescript
export async function listPresets(client: ApiClient): Promise<VendorPreset[]> {
  const res = await client.fetch('/api/v1/admin/llm/presets')
  if (!res.ok) throw new Error(`listPresets failed: ${res.status}`)
  return res.json() as Promise<VendorPreset[]>
}
```

Update the `import` and any barrel re-export (`frontend/packages/core/src/index.ts` / `types/index.ts`) to export `VendorPreset`, `EndpointPreset`, `ModelPresetEntry` and drop `ProviderPreset`.

- [ ] **Step 3: Build core** (web depends on the built package):

Run: `cd frontend && pnpm --filter @cubeplex/core build`
Expected: builds; TypeScript errors elsewhere (PresetPicker/ConfigureStep) are expected and fixed in Phase F.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/core/src
git commit -m "feat(core): nested VendorPreset/EndpointPreset types for catalog API"
```

---

## Phase D — Seeder: preset resolution + precedence (§6.2/§6.3)

### Task D1: cost deep-merge helper (§6.2.3)

**Files:**
- Modify: `cubeplex/seeders/provider_seeder.py`
- Test: `tests/unit/test_provider_seeder_resolve.py`

- [ ] **Step 1: Write the failing test** `tests/unit/test_provider_seeder_resolve.py`:

```python
from cubeplex.seeders.provider_seeder import _merge_cost


def test_merge_cost_partial_override_inherits_other_legs():
    catalog = {"input": 0.27, "output": 1.10, "cache_read": 0.07, "cache_write": 0.0}
    override = {"input": 0.5}
    assert _merge_cost(catalog, override) == {
        "input": 0.5, "output": 1.10, "cache_read": 0.07, "cache_write": 0.0
    }


def test_merge_cost_no_override_returns_catalog():
    catalog = {"input": 1.0, "output": 2.0, "cache_read": 0.0, "cache_write": 0.0}
    assert _merge_cost(catalog, None) == catalog
```

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** in `provider_seeder.py`:

```python
def _merge_cost(catalog_cost: dict[str, float], override: dict[str, Any] | None) -> dict[str, float]:
    """Per-leaf deep-merge: an override leg replaces only that leg (§6.2.3)."""
    merged = dict(catalog_cost)
    if override:
        for leg in ("input", "output", "cache_read", "cache_write"):
            if leg in override:
                merged[leg] = float(override[leg])
    return merged
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add cubeplex/seeders/provider_seeder.py tests/unit/test_provider_seeder_resolve.py
git commit -m "feat(seeder): per-leg cost deep-merge for preset overrides (§6.2.3)"
```

### Task D2: preset resolution + precedence + validation in the seed loop

**Files:**
- Modify: `cubeplex/seeders/provider_seeder.py`
- Test: `tests/unit/test_provider_seeder_resolve.py` (a focused resolver test)

The seed loop (`seed_system_providers_from_config`) reads each `config_providers[name]` dict. Add: if `preset:` is present, resolve it and inherit. This means restructuring how `base_url`, `provider_type`, the model list, and `capability` are derived.

- [ ] **Step 1: Write the failing test** for a pure resolver function (extract the per-provider derivation so it's unit-testable without a DB):

```python
import pytest

from cubeplex.seeders.provider_seeder import resolve_provider_config


def test_resolve_with_preset_inherits_base_url_models_capability():
    cfg = {"preset": "deepseek/cn/anthropic-messages", "api_key": "k"}
    r = resolve_provider_config("deepseek", cfg)
    assert r.base_url == "https://api.deepseek.com/anthropic"
    assert r.provider_type == "anthropic-messages"
    assert r.preset_key == "deepseek/cn/anthropic-messages"
    assert r.capability  # non-empty descriptor dict
    assert len(r.models) >= 1
    assert all("cost" in m and "input" in m["cost"] for m in r.models)


def test_resolve_models_subset_filter():
    cfg = {"preset": "deepseek/cn/anthropic-messages", "api_key": "k", "models": ["deepseek-v4-flash"]}
    r = resolve_provider_config("deepseek", cfg)
    assert [m["id"] for m in r.models] == ["deepseek-v4-flash"]


def test_resolve_unknown_preset_fails_loudly():
    with pytest.raises(ValueError, match="unknown preset"):
        resolve_provider_config("x", {"preset": "no/such/key", "api_key": "k"})


def test_resolve_unknown_subset_model_fails_loudly():
    with pytest.raises(ValueError, match="not in preset"):
        resolve_provider_config("deepseek", {"preset": "deepseek/cn/anthropic-messages",
                                             "api_key": "k", "models": ["ghost"]})


def test_resolve_api_override_with_preset_rejected():
    with pytest.raises(ValueError, match="api.*not overridable"):
        resolve_provider_config("deepseek", {"preset": "deepseek/cn/anthropic-messages",
                                             "api_key": "k", "api": "openai-completions"})


def test_resolve_capability_override_with_preset_rejected():
    with pytest.raises(ValueError, match="capability.*not overridable"):
        resolve_provider_config("deepseek", {"preset": "deepseek/cn/anthropic-messages",
                                             "api_key": "k", "capability": {"supports_tools": False}})


def test_resolve_without_preset_requires_base_url_api_models():
    # §6.2.4: no preset → config MUST supply base_url, api, and non-empty models.
    for bad in (
        {"api": "openai-completions", "models": [{"id": "m"}]},          # missing base_url
        {"base_url": "http://x/v1", "models": [{"id": "m"}]},            # missing api
        {"base_url": "http://x/v1", "api": "openai-completions"},        # missing models
        {"base_url": "http://x/v1", "api": "openai-completions", "models": []},  # empty models
    ):
        with pytest.raises(ValueError, match="custom provider.*requires"):
            resolve_provider_config("custom", bad)


def test_resolve_base_url_override_allowed():
    cfg = {"preset": "deepseek/cn/anthropic-messages", "api_key": "k",
           "base_url": "https://proxy.internal/anthropic"}
    r = resolve_provider_config("deepseek", cfg)
    assert r.base_url == "https://proxy.internal/anthropic"


def test_resolve_without_preset_uses_config_verbatim():
    cfg = {"base_url": "http://localhost:8000/v1", "api": "openai-completions",
           "models": [{"id": "m", "name": "M", "context_window": 1, "max_tokens": 1, "input": ["text"]}]}
    r = resolve_provider_config("vllm", cfg)
    assert r.base_url == "http://localhost:8000/v1"
    assert r.preset_key is None
```

NOTE: these tests require the deepseek vendor/endpoint to exist in `vendors.yaml` (Phase B) and the seed config models (`deepseek-v4-pro`, `deepseek-v4-flash`) to be present in that vendor's model pool. Add them to `vendors.yaml` in Phase E when wiring the real config; for this task's test, they must already be in the catalog. **Add `deepseek-v4-pro` / `deepseek-v4-flash` to the deepseek vendor pool now** (they are the real seeded ids) if not done in B2.

- [ ] **Step 2: Run** → FAIL (`resolve_provider_config` undefined).

- [ ] **Step 3: Implement** the resolver. Add to `provider_seeder.py`:

```python
from dataclasses import dataclass

from cubeplex.llm.catalog import load_catalog


@dataclass
class ResolvedProviderConfig:
    base_url: str
    provider_type: str
    preset_key: str | None
    capability: dict[str, Any]
    model_capability_overrides: dict[str, Any]
    models: list[dict[str, Any]]  # normalized to seeder's per-model dict (id/name/cost/…)


def _model_from_preset(m: Any, cost_override: dict[str, Any] | None) -> dict[str, Any]:
    base_cost = m.pricing.model_dump()
    return {
        "id": m.model_id,
        "name": m.display_name,
        "reasoning": m.reasoning,
        "input": list(m.input_modalities),
        "context_window": m.context_window,
        "max_tokens": m.max_tokens,
        "cost": _merge_cost(base_cost, cost_override),
    }


_OVERRIDABLE_UNDER_PRESET = "base_url"  # the ONLY catalog field a preset config may override


def resolve_provider_config(name: str, cfg: dict[str, Any]) -> ResolvedProviderConfig:
    preset_key = cfg.get("preset")
    if not preset_key:
        # No preset → config must fully specify the provider (§6.2.4). Validate
        # rather than default, so an under-specified custom provider fails loudly.
        base_url = cfg.get("base_url")
        api = cfg.get("api")
        models = list(cfg.get("models", []))
        if not base_url or not api or not models:
            raise ValueError(
                f"provider {name!r}: a custom provider (no 'preset:') requires "
                f"'base_url', 'api', and a non-empty 'models' list (§6.2.4)"
            )
        return ResolvedProviderConfig(
            base_url=str(base_url), provider_type=str(api),
            preset_key=None, capability={}, model_capability_overrides={}, models=models,
        )
    # §6.2.3: under a preset, neither 'api' (protocol) nor 'capability' is overridable.
    if cfg.get("api") is not None:
        raise ValueError(f"provider {name!r}: 'api' is not overridable under a preset (§6.2.3)")
    if cfg.get("capability") is not None:
        raise ValueError(f"provider {name!r}: 'capability' is not overridable under a preset (§6.2.3)")
    try:
        ep = load_catalog().resolve(preset_key)
    except KeyError:
        raise ValueError(f"provider {name!r}: unknown preset {preset_key!r}") from None
    pool = {m.model_id: m for m in ep.models}
    subset = cfg.get("models")
    # Per-model cost overrides keyed by model id when config lists dicts.
    overrides: dict[str, dict] = {}
    chosen: list[str]
    if subset is None:
        chosen = list(pool.keys())
    else:
        chosen = []
        for item in subset:
            mid = item if isinstance(item, str) else str(item["id"])
            if mid not in pool:
                raise ValueError(f"provider {name!r}: model {mid!r} not in preset {preset_key!r}")
            chosen.append(mid)
            if isinstance(item, dict) and "cost" in item:
                overrides[mid] = item["cost"]
    return ResolvedProviderConfig(
        base_url=str(cfg.get("base_url") or ep.base_url),  # base_url override allowed
        provider_type=ep.protocol,
        preset_key=preset_key,
        capability=ep.capability.model_dump(mode="json"),
        model_capability_overrides={},
        models=[_model_from_preset(pool[mid], overrides.get(mid)) for mid in chosen],
    )
```

- [ ] **Step 4: Run** `uv run pytest tests/unit/test_provider_seeder_resolve.py -v` → all PASS.

- [ ] **Step 5: Wire the resolver into `seed_system_providers_from_config`.** Replace the inline `base_url` / `provider_type` / capability-backfill / models-loop derivation with a call to `resolve_provider_config(name, cfg_dict)`, then use `resolved.base_url`, `resolved.provider_type`, set `provider.preset_slug = resolved.preset_key`, `provider.capability = resolved.capability` (when non-empty and `provider.capability` empty), and iterate `resolved.models` (each already has `id/name/cost/context_window/max_tokens/reasoning/input`). Keep the existing credential-vault + stale-model-disable logic unchanged. Keep the "skip provider with no models" guard.

  **Deployment-knob pass-through (codex P2 fix):** `extra_body` / `extra_headers` are *deployment* knobs, **not** catalog data — the resolver intentionally does not carry them. The seed loop continues to read **provider-level** `cfg_dict.get("extra_body")` / `cfg_dict.get("extra_headers")` directly onto the `Provider` row (unchanged from today, and it works the same whether or not `preset:` is set). For **model-level** extras on a preset-sourced model, the operator adds them via a config `models:` override entry (a dict with `id` + `extra_body`/`extra_headers`); extend `_model_from_preset` to merge those override keys when the subset item is a dict. Add a test: a config `models: [{id: X, extra_body: {...}}]` under a preset yields a model dict carrying that `extra_body`.

- [ ] **Step 6: Run the seeder idempotency test** (existing): `uv run pytest tests/ -k seed -v`. Fix fallout.

- [ ] **Step 7: Commit**

```bash
git add cubeplex/seeders/provider_seeder.py tests/unit/test_provider_seeder_resolve.py cubeplex/llm/catalog/data/vendors.yaml
git commit -m "feat(seeder): resolve config preset: into base_url/api/capability/model-pool (§6.2/§6.3)"
```

---

## Phase E — Exhaustive seed-config rewrite + backfill parity (§6.4)

### Task E1: Inventory + rewrite ALL seed configs

**Files:**
- Modify: `backend/config.development.local.yaml` (the `llm.providers` block)
- Modify: any other seed config that declares `llm.providers` (see Step 0)
- Modify: `cubeplex/llm/catalog/data/vendors.yaml` (add any seeded model/endpoint not yet present)

- [ ] **Step 0: Enumerate every seed config (codex P1 — §6.4 is exhaustive, not illustrative).** The inventory below covers `config.development.local.yaml` only; before rewriting, list **all** files that carry an `llm.providers` block and inventory each:

```bash
cd backend && git grep -l "^\s*providers:" -- 'config*.yaml' ; ls config*.yaml
# Inspect each hit (config.yaml / config.development.yaml / config.development.local.yaml /
# any env-specific seed). For EVERY provider in EACH file: map it to a preset_key OR
# record it as deliberately-custom with a one-line reason. Record absent files explicitly.
```

Write the resulting full inventory into this task (extend the table) before editing — a provider silently left out is exactly the §6.4 backfill-loss this guards against. **Do not start Step 2 until the table below has a row for every provider in every file Step 0 found** (the table as written covers only `config.development.local.yaml`; add the rows for any other seed file, or explicitly record "no other seed file with llm.providers exists" if Step 0 finds none).

The seeded providers in `config.development.local.yaml` and their mapping (§6.4 inventory — **local-config slice; extend per Step 0**):

| config name | base_url today | mapping |
|---|---|---|
| `deepseek` | api.deepseek.com/anthropic | `preset: deepseek/cn/anthropic-messages` |
| `minimax` | api.minimaxi.com/v1 | `preset: minimax/cn/openai-completions/general` (carry `extra_body`) |
| `arkcode` | ark…/api/coding/v3 (openai) | NEW catalog endpoint: volcengine coding **openai-completions** `/api/coding/v3` → `preset: volcengine/cn/openai-completions/coding` |
| `alicode` | coding.dashscope…/v1 | `preset: aliyun/cn/openai-completions/coding` (host override in catalog) |
| `volengine` | ark…/api/v3 | `preset: volcengine/cn/openai-completions` (general) |
| `openrouter` | openrouter.ai/api/v1 | `preset: openrouter/intl/openai-completions` |
| `sensedeal` | private gateway | **custom** — keep verbatim (no preset); reason: private gateway, not in catalog. (Did NOT match a flat slug → no backfill to lose.) |
| `google` | local IP | **custom** — keep verbatim; reason: self-hosted test endpoint. (Did NOT match a flat slug → no backfill to lose.) |
| `vllm` | local IP | **custom** + add to `DELIBERATE_CUSTOM` (E2). reason: self-hosted OSS framework — its model (`gemma-4-31b-it`) is deployment-specific and not a catalog entry, so the subset filter can't apply; it's openai-compatible, so empty capability → cubepi defaults is fine. |

**Backfill note (codex):** `minimax`, `openrouter`, `vllm` config names match flat slugs (`minimax`/`openrouter`/`vllm`) so they were backfilled under the old rule. `minimax`/`openrouter` are preset-mapped above (backfill preserved). `vllm` is a **deliberate** downgrade to custom — it MUST be listed in `DELIBERATE_CUSTOM` in E2 (with the reason above) so the §6.4 guard treats it as intentional, not a silent loss. `sensedeal`/`google` never matched a flat slug, so custom loses nothing. **Self-hosted OSS frameworks (vllm/ollama/lm-studio) are custom by nature — their models aren't catalog data.**

- [ ] **Step 1:** For each *preset-mapped* provider, ensure its real model ids exist in the catalog vendor pool with correct `context_window`/`max_tokens`/`input`/`reasoning` + a `pricing` (use the config's `cost` when present, else `{input:0,output:0}`). Add to `vendors.yaml`:
  - deepseek pool: `deepseek-v4-pro`, `deepseek-v4-flash`.
  - minimax pool: `MiniMax-M2.7`.
  - volcengine general: `doubao-seed-1-8-251228` (cost input 2.4/output 24); coding: `doubao-seed-2.0-pro`, `kimi-k2.6`.
  - aliyun coding pool: `qwen3.6-plus`.
  - openrouter pool: `google/gemma-4-31b-it:free`, `stepfun/step-3.5-flash:free`.
  - Add the `volcengine/cn/openai-completions/coding` endpoint (path `/api/coding/v3`) and make volcengine a **tiered** vendor (general + coding) — so tag its general models `plan: general` and coding models `plan: coding`.

- [ ] **Step 2:** Rewrite the `llm.providers` block. Preset-mapped example:

```yaml
    providers:
      deepseek:
        preset: deepseek/cn/anthropic-messages
        api_key: key-in-env
      minimax:
        preset: minimax/cn/openai-completions/general
        api_key: sk-cp-…
        extra_body: { "reasoning_split": true }
      arkcode:
        preset: volcengine/cn/openai-completions/coding
        api_key: ark-…
      alicode:
        preset: aliyun/cn/openai-completions/coding
        api_key: sk-sp-…
      volengine:
        preset: volcengine/cn/openai-completions
        api_key: 87d0c8f5-…
      openrouter:
        preset: openrouter/intl/openai-completions
        api_key: sk-or-…
      # custom (no preset) — kept verbatim:
      sensedeal: { base_url: https://gateway.chat.sensedeal.vip/v1, api_key: …, api: openai-completions, models: [...] }
      google:    { base_url: http://192.168.1.218:8000/v1, api_key: test, api: openai-completions, models: [...] }
      vllm:      { base_url: http://192.168.1.218:8008/v1, api_key: test, api: openai-completions, models: [...], extra_body: {...} }
```

(Keep `extra_body`/`extra_headers` on providers/models that had them — those are deployment knobs, not catalog data, and pass through unchanged.)

- [ ] **Step 3: Boot the seeder** against the worktree DB to confirm it resolves:

Run: `cd backend && set -a && source ../.worktree.env && set +a && uv run python -c "import asyncio; from cubeplex.db import ...; ..."` — or simpler, run the app's seed entrypoint / the seed test that exercises real config. Confirm no `ValueError` and that previously-seeded providers still produce models.
Expected: seed completes; `deepseek/minimax/arkcode/alicode/volengine/openrouter` get their models from the catalog.

- [ ] **Step 4: Commit**

```bash
# stage EVERY seed file Step 0 enumerated, not just the local one
git add backend/config.development.local.yaml cubeplex/llm/catalog/data/vendors.yaml
# + any other seed config Step 0 found (e.g. backend/config.yaml / config.development.yaml)
git commit -m "feat(config): rewrite seed providers to preset: refs; add seeded models to catalog (§6.4)"
```

### Task E2: Backfill-parity test (§6.4 P1 guard)

**Files:**
- Create: `tests/unit/test_seed_backfill_parity.py`

**Codex P1:** a hand-written `PRESET_MAPPED` list cannot catch a provider that was silently dropped to custom. The guard must **derive** the at-risk set: every provider that received capability backfill under the *old* name-match rule must still resolve a capability under the *new* config. The old rule backfilled when the config provider **name matched a flat preset slug** (`get_provider_preset(name)` succeeded). So compute that set from the frozen flat snapshot + the actual seed configs, and assert each still resolves.

- [ ] **Step 1: Write the test** — derive the old-backfilled set and assert new-config parity:

```python
from pathlib import Path

import pytest
import yaml

from cubeplex.config import config as settings
from cubeplex.seeders.provider_seeder import resolve_provider_config

_SNAPSHOT = (
    Path(__file__).parent / "llm" / "catalog" / "data" / "flat_providers_snapshot.yaml"
)


# Providers that DID match a flat slug (so were backfilled) but are DELIBERATELY
# downgraded to custom in the rewrite, each with a recorded §6.4 reason. A name
# here is an intentional drop, not a silent one. Keep this list in lockstep with
# the E1 inventory "custom" rows; an accidental omission must NOT be added here.
DELIBERATE_CUSTOM = {
    "vllm": "self-hosted OSS framework; model is deployment-specific (not catalog data), "
            "openai-compatible so cubepi defaults suffice",
}


def _old_backfilled_provider_names() -> set[str]:
    """Provider names that matched a flat preset slug under the OLD rule."""
    flat_slugs = {e["slug"] for e in yaml.safe_load(_SNAPSHOT.read_text("utf-8"))}
    cfg_providers = dict(dict(settings.get("llm", {})).get("providers", {}))
    return {name for name in cfg_providers if name in flat_slugs}


def test_no_provider_silently_loses_capability_backfill():
    """§6.4: every old-backfilled provider still resolves a capability now,
    UNLESS it is an intentional, documented downgrade in DELIBERATE_CUSTOM."""
    cfg_providers = dict(dict(settings.get("llm", {})).get("providers", {}))
    regressed = []
    for name in _old_backfilled_provider_names():
        if name in DELIBERATE_CUSTOM:
            continue
        r = resolve_provider_config(name, dict(cfg_providers[name]))
        if r.preset_key is None or not r.capability:
            regressed.append(name)
    assert not regressed, (
        f"providers lost capability backfill in the rewrite (map to a preset, "
        f"or add to DELIBERATE_CUSTOM with a reason): {regressed}"
    )


# Also assert the preset-mapped providers resolve a capability + models.
PRESET_MAPPED = {
    "deepseek": "deepseek/cn/anthropic-messages",
    "minimax": "minimax/cn/openai-completions/general",
    "arkcode": "volcengine/cn/openai-completions/coding",
    "alicode": "aliyun/cn/openai-completions/coding",
    "volengine": "volcengine/cn/openai-completions",
    "openrouter": "openrouter/intl/openai-completions",
}


@pytest.mark.parametrize("name,key", PRESET_MAPPED.items())
def test_preset_mapped_providers_get_capability_and_models(name, key):
    r = resolve_provider_config(name, {"preset": key, "api_key": "k"})
    assert r.preset_key == key
    assert r.capability is not None
    assert len(r.models) >= 1
```

NOTE: `_old_backfilled_provider_names()` derives the at-risk set so a forgotten provider is caught automatically — the hand-written `PRESET_MAPPED` is a secondary, explicit check. If the loaded config's provider names don't match any flat slug (likely for the local config, whose names like `deepseek` differ from flat slugs like `deepseek-anthropic`), the derived set is empty and the guard matters most for the production `config.yaml` — which Step 0 of E1 must also inventory.

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/unit/test_seed_backfill_parity.py -v` → PASS (proves the inventory + catalog are wired).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_seed_backfill_parity.py
git commit -m "test(seeder): backfill-parity guard for preset-mapped providers (§6.4)"
```

---

## Phase F — Frontend two-step wizard

### Task F1: wizard state — pick vendor + selected endpoint

**Files:**
- Modify: `frontend/packages/web/components/admin/models/wizard/wizardMachine.ts`
- Test: `frontend/packages/web/components/admin/models/wizard/__tests__/wizardMachine.test.ts`

**Step ordering (codex P1 fix):** endpoint (region/protocol/plan) selection happens in **step 2** (ConfigureStep), not step 1. So **step 1 advance requires only `vendor`**; the `selectedPresetKey` is set during step 2 and gates the *create/confirm* action there (`canAdvance(step 2)` requires `selectedPresetKey != null && providerId == null`-pre-create → see F3). Requiring the endpoint to advance *from* step 1 would make step 2 unreachable.

- [ ] **Step 1: Write the failing test** — `pickVendor` sets vendor and allows step-1 advance; `selectEndpoint` (used in step 2) sets the chosen `preset_key`:

```typescript
import { describe, expect, it } from 'vitest'
import { canAdvance, initialWizardState, wizardReducer } from '../wizardMachine'
import type { VendorPreset } from '@cubeplex/core'

const vendor = {
  vendor: 'zhipu', display_name: 'Zhipu', short_name: 'Zhipu', logo: 'zhipu',
  category: 'saas', description: '', endpoints: [
    { preset_key: 'zhipu/cn/openai-completions/coding', region: 'cn',
      protocol: 'openai-completions', plan: 'coding', base_url: 'https://x', model_ids: ['m'] },
  ], models: [],
} as VendorPreset

it('advances from step 1 once a vendor is picked', () => {
  let s = initialWizardState
  expect(canAdvance(s)).toBe(false)
  s = wizardReducer(s, { type: 'pickVendor', vendor })
  expect(canAdvance(s)).toBe(true) // vendor alone is enough to reach step 2
})

it('records the endpoint selected in step 2', () => {
  let s = wizardReducer(initialWizardState, { type: 'pickVendor', vendor })
  s = wizardReducer(s, { type: 'next' }) // now on step 2
  s = wizardReducer(s, { type: 'selectEndpoint', presetKey: 'zhipu/cn/openai-completions/coding' })
  expect(s.selectedPresetKey).toBe('zhipu/cn/openai-completions/coding')
})
```

- [ ] **Step 2: Run** `cd frontend && pnpm --filter @cubeplex/web test wizardMachine` → FAIL.

- [ ] **Step 3: Implement** — update `wizardMachine.ts`: replace `preset: ProviderPreset | null` with `vendor: VendorPreset | null` and `selectedPresetKey: string | null`; actions `pickVendor` / `selectEndpoint`; `canAdvance` **step 1 → `vendor !== null`** (endpoint not required here); the step-2 create action in ConfigureStep is what requires `selectedPresetKey` (F3).

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/admin/models/wizard/wizardMachine.ts \
        frontend/packages/web/components/admin/models/wizard/__tests__/wizardMachine.test.ts
git commit -m "feat(wizard): vendor + endpoint selection state (two-step)"
```

### Task F2: `PresetPicker` lists vendors

**Files:**
- Modify: `frontend/packages/web/components/admin/models/wizard/PresetPicker.tsx`

- [ ] **Step 1:** Change `presets`/`ProviderPreset[]` to `vendors`/`VendorPreset[]`; `onPick(preset)` → `onPickVendor(vendor)`. Filter/search over `vendor.display_name`/`vendor.vendor`. Card renders `vendor.logo`, `display_name`, `description`, a count badge (`${vendor.endpoints.length} endpoints`). Drop the reasoning-shape badge (it read `preset.capability`, which no longer exists at vendor level). `selectedSlug` → `selectedVendor: string | null` compared to `vendor.vendor`.

- [ ] **Step 2: Verify build + existing test** `cd frontend && pnpm --filter @cubeplex/core build && pnpm --filter @cubeplex/web test PresetPicker` (update the test fixture to `VendorPreset`). Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/admin/models/wizard/PresetPicker.tsx \
        frontend/packages/web/components/admin/models/wizard/__tests__/
git commit -m "feat(wizard): PresetPicker lists vendors (step 1)"
```

### Task F3: `ConfigureStep`/`ProviderConfigForm` — endpoint selectors drive base_url + models

**Files:**
- Modify: `frontend/packages/web/components/admin/models/wizard/ConfigureStep.tsx`
- Modify: `frontend/packages/web/components/admin/models/ProviderConfigForm.tsx`

- [ ] **Step 1:** `ConfigureStep` now receives `vendor: VendorPreset` + `selectedPresetKey` (+ a setter). Add **region / protocol / plan** selectors derived from `vendor.endpoints` (distinct regions, then protocols within region, then plans). Selecting them resolves the matching `EndpointPreset` → its `base_url` (read-only display, composed server-side) and the model list filtered to `endpoint.model_ids` (mapped against `vendor.models`). The created `ProviderCreate` body sends `preset_slug: endpoint.preset_key`, `provider_type: endpoint.protocol`, `base_url: endpoint.base_url`, and the chosen models with pricing prefilled from `vendor.models[].pricing`.

- [ ] **Step 2:** `ProviderConfigForm` `preset` prop type changes from `ProviderPreset` to a small `{ base_url, provider_type, logo, models }` shape derived from the selected endpoint (or pass `vendor` + `endpoint` and compute inside). Prefill cost fields from `pricing`.

- [ ] **Step 3: Build + typecheck + tests**

Run: `cd frontend && pnpm --filter @cubeplex/core build && pnpm --filter @cubeplex/web type-check && pnpm --filter @cubeplex/web test`
Expected: PASS (update `ConfigureStep.test.tsx` fixtures to the nested shape).

- [ ] **Step 4: Manual E2E (golden path)** — per CLAUDE.md, exercise the UI. Start backend + frontend on slot-82 ports (bind `0.0.0.0` — user is remote):

```bash
# backend
cd backend && set -a && source ../.worktree.env && set +a && CUBEPLEX_API__HOST=0.0.0.0 uv run python main.py
# frontend (separate shell) — uses the with-worktree-env wrapper so PORT=3082
cd frontend && HOSTNAME=0.0.0.0 pnpm dev
```

Open `http://192.168.1.150:3082/admin/models/new`, add a provider: pick **Zhipu** (step 1) → choose **CN / OpenAI / Coding** (step 2) → confirm base_url shows `https://open.bigmodel.cn/api/coding/paas/v4` and the model list + prefilled pricing. Report what you saw (screenshot or description) — do not claim success without observing it.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/admin/models/
git commit -m "feat(wizard): endpoint selectors drive composed base_url + filtered models (step 2)"
```

---

## Phase G — Delete cubepi catalog + bump dependency

This phase requires a **cubepi release** because cubeplex consumes cubepi from PyPI. Do it last, after Phases A–F prove cubeplex no longer imports cubepi's catalog.

### Task G1: Confirm cubeplex has zero cubepi-catalog imports

- [ ] **Step 1: Grep**

Run: `cd backend && git grep -n "cubepi.providers.catalog" cubeplex/ tests/`
Expected: **no matches** (Phases C/D removed them). If any remain, fix before proceeding.

- [ ] **Step 2:** also confirm nothing imports the old flat `ProviderPreset`/`get_provider_preset`/`list_provider_presets` from cubepi:

Run: `git grep -n "get_provider_preset\|list_provider_presets\|from cubepi.providers.catalog" backend/`
Expected: no matches in `cubeplex/` (the snapshot fixture under `tests/` is local YAML, not an import — OK).

### Task G2: cubepi PR — delete catalog package

**Repo:** `/home/chris/cubepi` (separate worktree per cubepi's own workflow).

- [ ] **Step 1:** In cubepi, confirm `WireApi` (in `cubepi/providers/catalog/types.py`) is only referenced by the catalog package: `git grep -n WireApi` in cubepi. If referenced elsewhere, relocate `WireApi` to a non-catalog module (e.g. `cubepi/providers/wire.py`) and update imports. (cubeplex does NOT depend on this — it declares its own; see Decoupling decision.)
- [ ] **Step 2:** Delete `cubepi/providers/catalog/` (loader, types, `data/providers.yaml`, tests). Remove any `__init__` re-exports of catalog symbols.
- [ ] **Step 3:** Run cubepi's test suite + lint. Fix fallout (likely just removing catalog tests + dead re-exports).
- [ ] **Step 4:** Open the cubepi PR; run its codex review loop; merge; cut a release (per cubepi's release process — likely a version bump + publish).

### Task G3: cubeplex — bump cubepi dependency

**Files:**
- Modify: `backend/pyproject.toml` (cubepi version), `backend/uv.lock`

- [ ] **Step 1: Bump** to the new cubepi release:

Run: `cd backend && uv add 'cubepi==<new-version>'` (do NOT hand-edit pyproject — CLAUDE.md).

- [ ] **Step 2: Full backend sweep**

Run: `cd backend && uv run pytest tests/unit tests/integration -q && uv run mypy cubeplex/`
Expected: PASS — cubeplex uses only `cubepi.providers.capability` now.

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "chore(deps): bump cubepi to <new-version> (catalog moved to cubeplex)"
```

---

## Pre-PR sweep (after all phases)

- [ ] Backend: `cd backend && uv run pytest -q && uv run mypy cubeplex/ && uv run ruff check cubeplex/`
- [ ] Frontend: `cd frontend && pnpm --filter @cubeplex/core build && pnpm --filter @cubeplex/web lint && pnpm --filter @cubeplex/web type-check && pnpm --filter @cubeplex/web test`
- [ ] Confirm the wizard golden path (F3 step 4) once more end-to-end.
- [ ] Open PR; run the `pr-codex-review-loop` skill until clean.

---

## Self-Review (plan vs spec)

**Spec coverage:**
- §3 ownership/delete cubepi → Phase G. ✓
- §4.1 base_url composition → A2 + B3 parity. ✓
- §4.2 plan all-or-nothing + dangling/unreachable + uniqueness → A5. ✓
- §4.3 capability profiles + loud-fail → A4. ✓
- §4.4 preset_key + uniqueness → A3 + A5 dedup. ✓
- §5.1 nested API shape → C1. ✓
- §5 consumers (admin_llm, admin_providers, seeder, frontend) → C1/C2/D/F. ✓
- §6.1 simplified config → E1. ✓
- §6.2 precedence (cost deep-merge, api/capability not overridable, base_url overridable, subset filter, no-preset verbatim) → D1/D2. ✓
- §6.3 validation (unknown preset_key, unknown subset id, api+preset) → D2. ✓
- §6.4 exhaustive inventory + backfill-parity test → E1/E2. ✓
- §7 testing (composition parity, loader validations, seeder, wizard E2E) → A/B/D/F. ✓

**Known judgment calls handed to the implementer:**
- B2/E1 pricing for ported presets defaults to `{input:0, output:0}` (catalog had none); real costs come from config `cost` override or are added to the catalog when known. Flagged so zero-cost is deliberate, not silent.
- The `model_capability_overrides` path is carried as empty in the resolver (D2) — the flat catalog's per-model overrides were rare; if `vendors.yaml` needs them, add a `model_capability_overrides` field to `Vendor`/`ModelPreset` and thread it through `ResolvedEndpoint`. Not required by the seeded set.
