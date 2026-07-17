# LLM Provider Platform — Plan Slice 2 (cubeplex M3 + M4 + M6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/dev/specs/2026-05-19-llm-provider-platform-design.md`
**Reconciled to spec rev-3** (model-grain readiness): test status is split
into provider-level `last_liveness_*` + per-model `last_test_*`; the probe
runs in two phases; a `readiness` field is derived server-side; runtime
401 / model_not_found writes status back (Task 10b). The spec's §4.5/4.7/
4.8 UI surfaces stay in the next slice (M5/M7); only the M3 schema + M4
probe halves of rev-3 land here.
**Slice:** Milestones M3 (schema + factory + read-only catalog endpoints),
M4 (test endpoint), M6 (task model routing + title-gen switch). M5
(Add Provider wizard UI) and M7 (polish) are the next slice.

**Prior slice:** `2026-05-19-llm-provider-platform.md` — cubepi capability
core + 20-preset catalog. **Lands on `feat/capability-descriptor`
branch in `/home/chris/cubepi`**; this slice depends on the latest
commit of that branch (e.g. `38bb4e8` at plan-writing time, but use
whatever's HEAD when the executor starts — see Task 1).

**Goal:** Make cubeplex honor `CapabilityDescriptor` end-to-end —
provider rows carry the descriptor JSON, `LLMFactory` plumbs it into
cubepi at call time, an admin Test endpoint can validate a provider's
wiring before save, and conversation title generation is routed to a
dedicated `task_models.title` model so the original 30s incident is
fixed.

**Architecture:**
1. Bump cubeplex's `cubepi` dependency from `==0.4.0` to a git ref of
   the feat branch so we don't have to wait for a cubepi PyPI release.
2. Provider DB row gains `preset_slug`, `capability` (JSON),
   `model_capability_overrides` (JSON), and provider-level
   `last_liveness_*` columns. The `models` row gains per-model
   `last_test_*` columns (rev-3, spec §4.1). `provider_type` semantics
   shift to wire-api literally.
3. `LLMFactory.build_cubepi_provider` reads the JSON columns into
   pydantic `CapabilityDescriptor` and passes through to cubepi.
4. New admin endpoints serve the preset catalog and probe a provider in
   two phases — provider liveness (once) + per-model capability — before
   save. Liveness persists on the provider; each model's ProbeResult
   persists on its row. A server-derived `readiness` per model drives the
   UI status dots without re-probing. A runtime 401 / model_not_found
   writes status back out-of-band (spec §4.4a).
5. `LLMFactory.resolve_task_model(task)` walks OrgSettings → yaml →
   default. `conversation_title` calls `resolve_task_model("title")`.

**Tech Stack:** Python 3.13 + FastAPI + SQLModel/Alembic; cubepi 0.5
(git ref); pytest-asyncio.

**Where the executor works:**
- All code lives in cubeplex worktree
  `/home/chris/cubeplex/.worktrees/feat/llm-provider-platform`. Worktree
  uses ports 8028 (backend) + 3028 (frontend) per `.worktree.env`.
- Branch `feat/llm-provider-platform` is already current.
- **No frontend changes** in this slice — UI wizard (M5) is the next
  slice. Backend tasks only.

---

## Pre-Implementation Review Amendments

A pre-implementation review (verified against the live cubeplex tree on
2026-05-20) found four issues the executor MUST fold in. The task bodies
below predate these; apply the amendments as you reach each task.

**A1 — `provider_type` enum fan-out (blocks Create/Update after the
Task 2 migration).** Changing the column value alone is not enough;
several readers hard-code the OLD enum (`openai_compat` / `anthropic`):
- `backend/cubeplex/services/provider_service.py:284` — rejects anything
  not in `("openai_compat",)`. Update to accept the three wire-api
  literals (`openai-completions` / `anthropic-messages` /
  `openai-responses`).
- `backend/cubeplex/services/provider_service.py:341-348` — branches on
  `provider_type == "anthropic"` / `"openai_compat"`. Rewrite to use the
  value as the wire api directly.
- `backend/cubeplex/api/schemas/provider.py` — three `provider_type`
  defaults of `"openai_compat"`; change to `"openai-completions"`.
- `backend/cubeplex/seeders/provider_seeder.py:105-125` — drops the
  `api_to_provider_type(...)` call; assign `cfg_dict.get("api",
  "openai-completions")` directly.
- Grep the frontend (`frontend/packages/**`) for `openai_compat` /
  `provider_type` enum typedefs and update or note them.

  → **Add a new Task 2b** "update provider_type readers" right after
  Task 2; do not consider the migration done until these pass.

**A2 — admin route placement.** Existing routers:
`backend/cubeplex/api/routes/v1/admin_providers.py` (provider rows) +
`admin.py`, `admin_mcp.py`, etc. There is **no** `admin_llm.py` yet.
- `/admin/llm/presets` → new `admin_llm.py` (catalog is LLM-scoped, not
  a provider row). (Task 4.)
- `/admin/providers/test` + `/{id}/test` → **extend** the existing
  `admin_providers.py`, do NOT create a parallel router. (Task 10.)

**A3 — path/reference corrections.**
- Task 1 Step 5: the real test paths are
  `backend/tests/unit/test_conversation_title_pi.py` and
  `backend/tests/unit/test_llm_factory_cubepi.py` (not
  `tests/test_conversation_title.py` / `tests/test_llm_factory.py`).
- Task 13: the seed module is
  `backend/cubeplex/seeders/provider_seeder.py` (not
  `backend/cubeplex/db/seeds/system_providers.py`).
- Task 1 Step 2: before editing, confirm `mcp` + `postgres` are still
  declared extras in the pinned cubepi commit's `pyproject.toml`.

**A4 — Task 12 must use the merged provider_config, not a raw dict
lookup.** `factory.llm_config.providers[provider_name]` misses
DB-overridden providers (the legacy `resolve_default_provider_and_config`
runs `_load_db_provider_configs` + `_build_merged_config` first).
- Fix: make `resolve_task_model` a **drop-in** replacement that returns
  `(provider_name, model_id, provider_config)` — i.e. it loads/merges DB
  configs the same way `resolve_default_provider_and_config` does, then
  resolves the task ref against the merged config. Task 11's signature
  and Task 12's swap both change to this 3-tuple shape.

Confirmed OK (no change needed): `ProviderConfig` is permissive
(`extra` default = ignore), so adding `capability` fields is
non-breaking (A-finding 2); `OrgSettings` is free-form `(org_id, key)`
+ `value: JSON` as assumed (A-finding 4); `StreamOptions(thinking="off")`
is a clean addition to `conversation_title._generate_title`'s
`provider.stream(...)` call (A-finding 5); `backend/tests/e2e/` exists
with a conftest (A-finding 6); the `_StubProvider` async-iterable shape
matches cubepi's `MessageStream` (A-finding 7); the seed migration
correctly skips admin-renamed providers, leaving capability empty →
cubepi legacy path (A-finding 8).

---

## File Structure

### Created
- `backend/cubeplex/api/routes/v1/admin_llm.py` — **catalog only**:
  `GET /admin/llm/presets` (per Amendment A2 — catalog is LLM-scoped, not
  a provider row).

### Extended (existing routers, per Amendment A2)
- `backend/cubeplex/api/routes/v1/admin_providers.py` — the provider-row
  endpoints go here, NOT in `admin_llm.py`:
  `POST /admin/providers/liveness` + `/{id}/liveness`,
  `POST /admin/providers/test` (pre-save, one model),
  `POST /admin/providers/{id}/models/{mid}/test`,
  `POST /admin/providers/{id}/test` (all enabled models), and the
  `GET /admin/providers/{id}` extension (Task 5).

### Created (cont.)
- `backend/cubeplex/services/provider_probe.py` — `ProbeResult`,
  `ProbeStep`, the two-phase runner: `run_liveness(...)` (phase A,
  provider grain) + `run_model_probe(...)` (phase B, per model) + per-step
  helpers.
- `backend/cubeplex/llm/readiness.py` — pure readiness-derivation helper
  (§4.1 enum); single source of truth for status the UI renders.
- `backend/cubeplex/services/task_model_resolver.py` — small module
  with `resolve_task_model(factory, task)` per spec §4.6. Keeps
  `LLMFactory` from growing.
- `backend/alembic/versions/<rev>_provider_capability_columns.py`
  — autogenerated alembic migration.
- `backend/tests/test_provider_capability_factory.py` — factory
  round-trip with capability JSON.
- `backend/tests/test_admin_llm_endpoints.py` — preset listing +
  provider GET/POST/PUT with capability.
- `backend/tests/test_provider_probe.py` — probe runner unit tests
  (mock cubepi.stream responses for each step).
- `backend/tests/test_task_model_resolver.py` — resolver fallback chain.
- `backend/tests/e2e/test_title_model_routing_e2e.py` — title gen
  uses configured `title` model.

### Modified
- `backend/pyproject.toml` — switch `cubepi` dep to git ref of
  `feat/capability-descriptor`.
- `backend/cubeplex/models/provider.py` — `Provider` gains 6 columns:
  `preset_slug`, `capability`, `model_capability_overrides`,
  `last_liveness_at`, `last_liveness_status`, `last_liveness_summary`;
  `Model` gains 3 columns: `last_test_at`, `last_test_status`,
  `last_test_summary` (rev-3, spec §4.1).
- `backend/cubeplex/llm/factory.py` — `build_cubepi_provider` reads
  capability JSON → typed CapabilityDescriptor; passes through. Replace
  the `_PROVIDER_TYPE_TO_API` mapping (provider_type stored value is
  now the wire api directly; keep the mapping as a one-row migration
  helper that backfills `openai_compat` → `openai-completions`).
- `backend/cubeplex/llm/config.py` — `ProviderConfig` and `ModelConfig`
  gain optional `capability` and `model_capability_overrides` typed
  via cubepi's `CapabilityDescriptor`.
- `backend/cubeplex/services/conversation_title.py` — switch from
  `factory.resolve_default_provider_and_config()` to
  `resolve_task_model(factory, "title")`.
- `backend/cubeplex/models/org_settings.py` — add `task_models` as a
  recognised key constant (the model itself is generic JSON, but adding
  the constant is the standard cubeplex pattern).
- `backend/cubeplex/config.py` (or wherever LLMConfig lives) — accept
  an optional top-level `llm.title_model` for yaml fallback.

### Reference (read-only)
- `cubepi` git ref of `feat/capability-descriptor` —
  `cubepi.CapabilityDescriptor`, `cubepi.list_provider_presets()`,
  `cubepi.get_provider_preset(slug)`. Public surface used.
- `backend/cubeplex/api/routes/v1/conversations.py` — title-gen route
  caller; doesn't need to change, the service does.
- `backend/cubeplex/services/conversation_title.py:_generate_title` —
  the LLM call site we're rerouting.

---

## Task 1: Switch cubepi dependency to git ref of feat branch

**Files:**
- Modify: `backend/pyproject.toml`

**Step 1: Verify the cubepi branch tip**

```bash
cd /home/chris/cubepi && git fetch origin feat/capability-descriptor \
  && git log origin/feat/capability-descriptor --oneline -1
```

Capture the SHA — pin to this exact commit so a force-push on the
cubepi branch (which is normal during the PR review loop) doesn't
silently change cubeplex behavior.

- [ ] **Step 2: Update `backend/pyproject.toml`**

Find the existing dep line:

```toml
"cubepi[mcp,postgres]==0.4.0",
```

Replace with a git ref pointing at the SHA from Step 1:

```toml
"cubepi[mcp,postgres] @ git+https://github.com/cubeplexai/cubepi.git@<SHA>",
```

Use `<SHA>` from Step 1 (full 40-char hash for reproducibility).

- [ ] **Step 3: Re-resolve and install**

```bash
cd /home/chris/cubeplex/.worktrees/feat/llm-provider-platform/backend
uv sync 2>&1 | tail -5
```

Expected: `+ cubepi @ git+...` line confirms the new resolution.

- [ ] **Step 4: Smoke check the new cubepi surface**

```bash
cd /home/chris/cubeplex/.worktrees/feat/llm-provider-platform/backend && \
  uv run python -c "
from cubepi.providers.capability import CapabilityDescriptor, TemperatureSpec
from cubepi.providers.catalog import list_provider_presets, get_provider_preset
print(f'capability OK, {len(list_provider_presets())} presets')
print(get_provider_preset('anthropic').logo)
"
```

Expected: `capability OK, 20 presets` + `anthropic`.

- [ ] **Step 5: Run existing backend tests for regression**

```bash
cd /home/chris/cubeplex/.worktrees/feat/llm-provider-platform/backend && \
  uv run pytest tests/unit/test_conversation_title_pi.py tests/unit/test_llm_factory_cubepi.py -q 2>&1 | tail -5
```

(Real paths per Amendment A3. The goal is to confirm the legacy
LLMFactory + title-gen still work against the new cubepi.) Expect green —
no source changes yet, just dep bump.

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/llm-provider-platform
git add backend/pyproject.toml backend/uv.lock
git commit -m "feat(deps): pin cubepi to feat/capability-descriptor commit (slice 2 prereq)"
```

---

## Task 2: Provider + Model DB columns + alembic migration

> **rev-3:** test status is split across two grains (spec §4.1). The
> **provider** carries only `last_liveness_*` (can we reach base_url with
> this key?). Each **model** carries `last_test_*` (does this model exist
> + do its toggles work?). Do NOT put `last_test_*` on the provider.

**Files:**
- Modify: `backend/cubeplex/models/provider.py`
- Create: `backend/alembic/versions/<rev>_provider_capability_columns.py`

- [ ] **Step 1a: Add capability + liveness fields to `Provider`**

Edit `backend/cubeplex/models/provider.py`. After the existing
`extra_headers` field on `Provider`, insert:

```python
    preset_slug: str | None = Field(default=None, max_length=64)
    capability: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    model_capability_overrides: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )
    # Provider-level test = liveness/credential ONLY (spec §4.1).
    last_liveness_at: datetime | None = Field(default=None)
    last_liveness_status: str | None = Field(default=None, max_length=16)  # "ok" | "fail"
    last_liveness_summary: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )
```

- [ ] **Step 1b: Add per-model test fields to `Model`**

In the same file, after the existing `extra_headers` field on `Model`,
insert:

```python
    # Per-model test = capability probe + model existence (spec §4.1).
    # "ok" | "warn" | "fail" | "unavailable".
    last_test_at: datetime | None = Field(default=None)
    last_test_status: str | None = Field(default=None, max_length=16)
    last_test_summary: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
```

Add `from datetime import datetime` to the imports if not already
present.

`capability` and `model_capability_overrides` are stored as opaque
JSON. Pydantic-typed access lives in the factory (Task 3); the model
itself stays generic JSON to avoid recursive validation on every
SQLAlchemy load. The `last_test_*` columns on `Model` are the *observed*
status axis — they do not reopen §4.1's "no per-model capability config
column" decision (that bars per-model capability *input*, not test
*output*).

- [ ] **Step 2: Generate the migration**

```bash
cd /home/chris/cubeplex/.worktrees/feat/llm-provider-platform/backend && \
  source .venv/bin/activate && \
  alembic revision --autogenerate -m "provider capability + liveness + model test columns"
```

The autogen should produce something like
`backend/alembic/versions/<rev>_provider_capability_+_liveness_+_model_test_columns.py`.
**Inspect** the generated file — autogen sometimes drops or duplicates
columns; verify all 6 new `providers` columns (`preset_slug`,
`capability`, `model_capability_overrides`, `last_liveness_at`,
`last_liveness_status`, `last_liveness_summary`) and all 3 new `models`
columns (`last_test_at`, `last_test_status`, `last_test_summary`) appear
in `op.add_column(...)` calls, and that no unrelated changes are present.

- [ ] **Step 3: Add a one-shot backfill for `provider_type` semantics**

In the same migration file's `upgrade()`, **after** the
`op.add_column(...)` block, add:

```python
    # provider_type used to be a short enum (openai_compat | anthropic).
    # New semantics: the column stores the cubepi wire api directly.
    # Backfill existing rows.
    op.execute(
        "UPDATE providers SET provider_type = 'openai-completions' "
        "WHERE provider_type = 'openai_compat'"
    )
    op.execute(
        "UPDATE providers SET provider_type = 'anthropic-messages' "
        "WHERE provider_type = 'anthropic'"
    )
```

Mirror the inverse in `downgrade()`:

```python
    op.execute(
        "UPDATE providers SET provider_type = 'openai_compat' "
        "WHERE provider_type = 'openai-completions'"
    )
    op.execute(
        "UPDATE providers SET provider_type = 'anthropic' "
        "WHERE provider_type = 'anthropic-messages'"
    )
```

- [ ] **Step 4: Apply migration**

```bash
cd /home/chris/cubeplex/.worktrees/feat/llm-provider-platform/backend && \
  source .venv/bin/activate && alembic upgrade head 2>&1 | tail -3
```

Expected: migration runs without errors.

- [ ] **Step 5: Verify schema**

```bash
cd /home/chris/cubeplex/.worktrees/feat/llm-provider-platform/backend && \
  uv run python -c "
from sqlalchemy import inspect
from cubeplex.db import get_engine
import asyncio
async def run():
    async with get_engine().connect() as conn:
        pcols = await conn.run_sync(
            lambda sync_conn: [c['name'] for c in inspect(sync_conn).get_columns('providers')]
        )
        for needed in ('preset_slug', 'capability', 'model_capability_overrides',
                       'last_liveness_at', 'last_liveness_status', 'last_liveness_summary'):
            assert needed in pcols, f'providers.{needed}'
        mcols = await conn.run_sync(
            lambda sync_conn: [c['name'] for c in inspect(sync_conn).get_columns('models')]
        )
        for needed in ('last_test_at', 'last_test_status', 'last_test_summary'):
            assert needed in mcols, f'models.{needed}'
        print('schema OK')
asyncio.run(run())
"
```

Expected: `schema OK`.

(Adapt the connection helper if `get_engine` has a different name in
this codebase; the goal is just to confirm the columns exist.)

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/models/provider.py backend/alembic/versions/
git commit -m "feat(db): provider capability + liveness + per-model test columns (slice 2 §4.1)"
```

---

## Task 3: LLMFactory plumbs capability through to cubepi

**Files:**
- Modify: `backend/cubeplex/llm/factory.py`
- Modify: `backend/cubeplex/llm/config.py`
- Create: `backend/tests/test_provider_capability_factory.py`

- [ ] **Step 1: Extend `ProviderConfig` with capability fields**

Edit `backend/cubeplex/llm/config.py`. Add to `ProviderConfig` (or
wherever the model-config types live):

```python
from typing import Any
# ...

class ProviderConfig(BaseModel):
    # ... existing fields ...
    capability: dict[str, Any] = Field(default_factory=dict)
    model_capability_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
```

Keep them as `dict[str, Any]` here — typed-pydantic conversion happens
inside `build_cubepi_provider` to keep the config-layer light.

- [ ] **Step 2: Plumb capability through the DB loader**

In `backend/cubeplex/llm/factory.py`'s `_load_db_provider_configs`,
where the per-provider dict is constructed (current code reads
`p.base_url`, `p.api_key`, etc.), add:

```python
db_configs[p.name] = {
    "base_url": p.base_url,
    # ... existing keys ...
    "capability": p.capability or {},
    "model_capability_overrides": p.model_capability_overrides or {},
}
```

(Empty-dict fallback ensures pydantic always gets a dict, not None.)

- [ ] **Step 3: Convert dict → typed inside `build_cubepi_provider`**

Replace the existing `build_cubepi_provider` body:

```python
def build_cubepi_provider(
    self,
    provider_config: ProviderConfig,
    *,
    cache_policy: "CacheMarkerPolicy | None" = None,
) -> Any:
    from cubepi.providers.capability import CapabilityDescriptor

    cap_dict = provider_config.capability or {}
    cap = CapabilityDescriptor.model_validate(cap_dict) if cap_dict else None
    overrides_raw = provider_config.model_capability_overrides or {}
    overrides = {
        model_id: CapabilityDescriptor.model_validate(d)
        for model_id, d in overrides_raw.items()
    } or None

    api = provider_config.api  # equal to the new wire-api value

    if api == "anthropic-messages":
        from cubepi.providers.anthropic import AnthropicProvider
        return AnthropicProvider(
            api_key=provider_config.api_key,
            base_url=provider_config.base_url or None,
            cache_policy=cache_policy,
            capability=cap,
            model_capability_overrides=overrides,
        )
    if api == "openai-completions":
        from cubepi.providers.openai import OpenAIProvider
        return OpenAIProvider(
            api_key=provider_config.api_key,
            base_url=provider_config.base_url,
            extra_body=provider_config.extra_body or None,
            extra_headers=provider_config.extra_headers or None,
            capability=cap,
            model_capability_overrides=overrides,
        )
    if api == "openai-responses":
        from cubepi.providers.openai_responses import OpenAIResponsesProvider
        return OpenAIResponsesProvider(
            api_key=provider_config.api_key,
            base_url=provider_config.base_url,
            capability=cap,
            model_capability_overrides=overrides,
        )
    raise ValueError(f"unsupported api for cubepi provider: {api!r}")
```

- [ ] **Step 4: Retire `_PROVIDER_TYPE_TO_API` mapping**

The new semantics: `Provider.provider_type` stores the wire api
directly. Find `_provider_type_to_api` callers and replace each with
the column value verbatim. The mapping function can stay as a stub
returning the value unchanged for one release (deprecated) — or
delete it entirely if no external caller relies on it. Grep first:

```bash
grep -rn "_provider_type_to_api\|api_to_provider_type" backend/cubeplex backend/tests
```

If only the factory itself calls them, delete both. If anything else
calls them, leave them as no-op identity functions with a TODO comment
to remove next slice.

- [ ] **Step 5: Write failing tests**

Create `backend/tests/test_provider_capability_factory.py`:

```python
"""LLMFactory — capability JSON round-trip through to cubepi.Provider."""

import pytest

from cubeplex.llm.config import LLMConfig, ProviderConfig, ModelConfig
from cubeplex.llm.factory import LLMFactory


def _bare_provider_config(api: str = "openai-completions", **kw) -> ProviderConfig:
    return ProviderConfig(
        base_url=kw.get("base_url", "https://example.com/v1"),
        api_key=kw.get("api_key", "test"),
        api=api,
        models=[],
        extra_body={},
        extra_headers={},
        capability=kw.get("capability", {}),
        model_capability_overrides=kw.get("model_capability_overrides", {}),
    )


def _factory_with(cfg: ProviderConfig) -> LLMFactory:
    llm = LLMConfig(default_model="x/y", fallback_models=[], providers={"p": cfg})
    return LLMFactory(llm_config=llm)


def test_build_openai_provider_no_capability_legacy_behavior():
    """Empty capability dict → cubepi OpenAIProvider has _cap_active=False."""
    p = _factory_with(_bare_provider_config()).build_cubepi_provider(
        _bare_provider_config()
    )
    assert p._cap_active is False


def test_build_openai_provider_with_capability_kwargs_active():
    cap = {
        "reasoning_off_payload": {"extra_body": {"enable_thinking": False}},
        "reasoning_on_payload": {"extra_body": {"enable_thinking": True}},
        "temperature": {"mode": "free", "min": 0.0, "max": 2.0, "default": 1.0},
    }
    cfg = _bare_provider_config(capability=cap)
    p = _factory_with(cfg).build_cubepi_provider(cfg)
    assert p._cap_active is True
    assert p._capability.reasoning_off_payload == {
        "extra_body": {"enable_thinking": False}
    }


def test_build_openai_provider_with_model_overrides_active():
    overrides = {"deepseek-r1": {"reasoning_on_payload": {"reasoning": {"effort": "low"}}}}
    cfg = _bare_provider_config(model_capability_overrides=overrides)
    p = _factory_with(cfg).build_cubepi_provider(cfg)
    assert p._cap_active is True
    assert "deepseek-r1" in p._model_overrides
    assert p._model_overrides["deepseek-r1"].reasoning_on_payload == {
        "reasoning": {"effort": "low"}
    }


def test_build_anthropic_provider_passes_capability():
    cap = {
        "reasoning_off_payload": {"thinking": {"type": "disabled"}},
        "reasoning_on_payload": {"thinking": {"type": "enabled"}},
    }
    cfg = _bare_provider_config(api="anthropic-messages", capability=cap)
    p = _factory_with(cfg).build_cubepi_provider(cfg)
    # AnthropicProvider always has _capability set (non-empty default
    # if capability=None is passed; here we pass the cap explicitly).
    assert p._capability.reasoning_off_payload == {"thinking": {"type": "disabled"}}


def test_build_openai_responses_provider_passes_capability():
    cap = {
        "reasoning_on_payload": {"reasoning": {"summary": "auto"}},
    }
    cfg = _bare_provider_config(api="openai-responses", capability=cap)
    p = _factory_with(cfg).build_cubepi_provider(cfg)
    assert p._cap_active is True
```

- [ ] **Step 6: Run tests**

```bash
cd /home/chris/cubeplex/.worktrees/feat/llm-provider-platform/backend && \
  uv run pytest tests/test_provider_capability_factory.py -v 2>&1 | tail -10
```

Expected: 5 passed.

- [ ] **Step 7: Run existing factory tests for regression**

```bash
uv run pytest tests/unit/test_llm_factory_cubepi.py -q 2>&1 | tail -5
```

Adjust file name if needed. Expected: all existing tests still pass —
providers with empty capability JSON behave identically to today.

- [ ] **Step 8: Commit**

```bash
git add backend/cubeplex/llm/factory.py backend/cubeplex/llm/config.py \
        backend/tests/test_provider_capability_factory.py
git commit -m "feat(llm): plumb capability + overrides through factory to cubepi"
```

---

## Task 4: GET /admin/llm/presets endpoint

**Files:**
- Locate or create: `backend/cubeplex/api/routes/v1/admin_llm.py`
- Create: `backend/tests/test_admin_llm_endpoints.py`

- [ ] **Step 1: Locate or create the admin LLM router**

```bash
grep -rn "admin/llm\|admin_llm\|llm_admin" backend/cubeplex/api/routes 2>&1 | head -5
```

If a router file already serves admin LLM concerns, extend it. If
not, create `backend/cubeplex/api/routes/v1/admin_llm.py` with the
standard FastAPI router pattern used by sibling files in
`backend/cubeplex/api/routes/v1/`.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_admin_llm_endpoints.py`:

```python
"""Admin LLM endpoints — preset catalog + provider GET / capability."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_provider_presets_returns_catalog(admin_client: AsyncClient):
    resp = await admin_client.get("/api/v1/admin/llm/presets")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 20
    # Spot-check one entry shape.
    anthropic = next(p for p in data if p["slug"] == "anthropic")
    assert anthropic["api"] == "anthropic-messages"
    assert anthropic["logo"] == "anthropic"
    assert "capability" in anthropic
    assert anthropic["capability"]["reasoning_level"]["kind"] == "int_budget"
```

(Use the existing `admin_client` fixture if cubeplex already has one;
adapt to whatever the established pattern is in
`backend/tests/conftest.py`.)

- [ ] **Step 3: Run to verify it fails**

```bash
uv run pytest tests/test_admin_llm_endpoints.py::test_list_provider_presets_returns_catalog -v 2>&1 | tail -5
```

Expected: 404 / not found.

- [ ] **Step 4: Implement the endpoint**

Add to the admin LLM router:

```python
@router.get("/admin/llm/presets")
async def list_provider_presets(
    ctx: Annotated[RequestContext, Depends(require_admin)],
) -> list[dict]:
    """Return the cubepi-bundled provider preset catalog as a list of dicts.

    Each entry serializes a ProviderPreset (slug, display_name, api, logo,
    capability, default_models, ...).
    """
    import cubepi
    return [p.model_dump(mode="json") for p in cubepi.list_provider_presets()]
```

Wire the router into `backend/cubeplex/api/app.py` (or wherever
routers are registered) if it's a new file.

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_admin_llm_endpoints.py::test_list_provider_presets_returns_catalog -v 2>&1 | tail -5
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/v1/admin_llm.py \
        backend/cubeplex/api/app.py \
        backend/tests/test_admin_llm_endpoints.py
git commit -m "feat(admin): GET /admin/llm/presets — serve cubepi catalog"
```

---

## Task 5: GET /admin/providers/{id} returns capability + liveness + per-model status

> **rev-3:** the response carries provider-level `last_liveness_*` plus,
> for each model in the provider, that model's `last_test_*` and a
> server-derived `readiness` field (spec §4.1 table). The picker/UI never
> re-derives readiness — it reads this field.

**Files:**
- Extend: `backend/cubeplex/api/routes/v1/admin_providers.py` (provider-row
  endpoint, per Amendment A2 — NOT `admin_llm.py`).
- Modify: `backend/tests/test_admin_llm_endpoints.py`

- [ ] **Step 1: Locate the existing provider admin endpoint**

```bash
grep -rn '"/admin/providers"\|admin/providers/{' backend/cubeplex/api/routes 2>&1 | head
```

If `GET /admin/providers/{id}` exists, extend it to include
`capability`, `model_capability_overrides`, the provider's
`last_liveness_at` / `last_liveness_status` / `last_liveness_summary`,
and a `models` list where each entry carries `last_test_at` /
`last_test_status` / `last_test_summary` / `readiness`. If it doesn't
exist, add it.

- [ ] **Step 1b: Add the readiness-derivation helper**

Create `backend/cubeplex/llm/readiness.py` with a pure function that maps
`(provider.last_liveness_status, model.last_test_status,
capability_changed_since_test)` → the §4.1 readiness enum
(`ready` / `degraded` / `provider_error` / `model_error` / `unavailable`
/ `stale`). `capability_changed_since_test` must reflect *capability*
edits specifically (spec §4.1 says "capability edited since last test"),
not any provider edit — so base it on a hash/snapshot of
`(capability, model_capability_overrides)` rather than the generic
`provider.updated_at`. Persist a `capability_fingerprint` on the model
row at probe time (or store the hash in `last_test_summary`) and compare;
do NOT use `updated_at > last_test_at` (it flips stale on unrelated edits
like a rename). This helper is the single source of truth reused by Task
5's serializer and any later picker payloads. Unit-test each branch.

- [ ] **Step 2: Append failing test**

Append to `tests/test_admin_llm_endpoints.py`:

```python
@pytest.mark.asyncio
async def test_get_provider_includes_capability_liveness_and_model_status(
    admin_client: AsyncClient, seeded_provider_id: str
):
    resp = await admin_client.get(f"/api/v1/admin/providers/{seeded_provider_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "capability" in body
    assert "model_capability_overrides" in body
    assert "last_liveness_at" in body
    assert "last_liveness_status" in body
    assert "last_liveness_summary" in body
    assert isinstance(body["models"], list)
    if body["models"]:
        m = body["models"][0]
        assert "last_test_status" in m
        assert "readiness" in m
```

You'll need a `seeded_provider_id` fixture — see Step 4 below for the
shape if it doesn't exist already.

- [ ] **Step 3: Run to verify it fails**

```bash
uv run pytest tests/test_admin_llm_endpoints.py::test_get_provider_includes_capability_liveness_and_model_status -v 2>&1 | tail -5
```

- [ ] **Step 4: Extend the provider + model response schemas**

If `backend/cubeplex/api/schemas/provider.py` (or equivalent) defines a
`ProviderRead` pydantic model, add the capability + `last_liveness_*`
fields, and add `last_test_*` + `readiness` to the per-model read
schema. Otherwise update the inline serialization in the handler.
`readiness` comes from the Step 1b helper.

- [ ] **Step 5: Confirm the test passes**

```bash
uv run pytest tests/test_admin_llm_endpoints.py -v 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/v1/admin_providers.py \
        backend/cubeplex/api/schemas/provider.py \
        backend/cubeplex/llm/readiness.py \
        backend/tests/test_admin_llm_endpoints.py
git commit -m "feat(admin): GET /admin/providers/{id} returns liveness + per-model readiness"
```

---

## Task 6: ProbeResult + ProbeStep types + probe runner skeleton

> **rev-3 — two phases (spec §4.4).** The probe is split by grain:
> - **Phase A — liveness** (provider grain): one cheap call. Result
>   persists to `providers.last_liveness_*`. If it fails, phase B is
>   skipped and every model is `provider_error`.
> - **Phase B — per-model capability** (model grain): reasoning +
>   temperature + tools + streaming, run against ONE model. Result
>   (`ProbeResult`) persists to that `models` row's `last_test_*`.
>
> The per-step helpers (Tasks 7–8) are unchanged; only the orchestrator
> (Task 9) and endpoints/persistence (Task 10) re-shape around the two
> phases. `liveness` is no longer one of phase B's blocking steps — it's
> phase A. Phase B's only blocking step is `reasoning`.

**Files:**
- Create: `backend/cubeplex/services/provider_probe.py`
- Create: `backend/tests/test_provider_probe.py`

- [ ] **Step 1: Define the result types**

Create `backend/cubeplex/services/provider_probe.py`:

```python
"""Provider probe — exercises a candidate provider configuration end-to-end.

See spec §4.4 for the five-step sequence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ProbeStepName = Literal["liveness", "reasoning", "temperature", "tools", "streaming"]
ProbeStepStatus = Literal["pass", "fail", "skip", "warn"]


class ProbeError(BaseModel):
    type: str            # e.g. "AuthError", "BadRequest", "Timeout"
    message: str
    raw_status: int | None = None


class ProbeStep(BaseModel):
    name: ProbeStepName
    status: ProbeStepStatus
    latency_ms: int | None = None
    detail: str = ""
    error: ProbeError | None = None
    # Count of SSE chunks observed during this step's stream. Lets the
    # streaming check (Task 8/9) verify a chunk arrived without re-streaming.
    # Excluded from the API payload — internal probe plumbing only.
    observed_chunks: int = Field(default=0, exclude=True)


class ProbeResult(BaseModel):
    # "unavailable" is the model-not-found short-circuit (Task 9); the
    # aggregator only ever returns pass/fail/warn.
    overall: Literal["pass", "fail", "warn", "unavailable"]
    blocking_failed: bool
    steps: list[ProbeStep] = Field(default_factory=list)
```

Persisted-column mapping (model probe): `overall` maps to
`models.last_test_status` as `pass → "ok"`, `warn → "warn"`,
`fail → "fail"`, `unavailable → "unavailable"`.

- [ ] **Step 2: Write the failing test (overall=pass with all-pass steps)**

Create `backend/tests/test_provider_probe.py`:

```python
"""Provider probe — aggregate logic.

Per-step behavior is tested in dedicated tests (Tasks 7-9). This file
covers the orchestrator's overall-result computation.
"""

import pytest

from cubeplex.services.provider_probe import (
    ProbeResult, ProbeStep, ProbeError, _aggregate_overall,
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
        ProbeStep(name="liveness", status="fail",
                  error=ProbeError(type="AuthError", message="401")),
    ]
    overall, blocked = _aggregate_overall(steps)
    assert overall == "fail"
    assert blocked is True


def test_aggregate_advisory_step_fail_warns_not_blocks():
    steps = [
        ProbeStep(name="liveness", status="pass"),
        ProbeStep(name="tools", status="fail"),  # advisory
    ]
    overall, blocked = _aggregate_overall(steps)
    assert overall == "warn"
    assert blocked is False


def test_aggregate_reasoning_fail_is_blocking():
    steps = [
        ProbeStep(name="liveness", status="pass"),
        ProbeStep(name="reasoning", status="fail"),  # blocking
    ]
    overall, blocked = _aggregate_overall(steps)
    assert overall == "fail"
    assert blocked is True
```

- [ ] **Step 3: Implement `_aggregate_overall`**

Append to `provider_probe.py`:

```python
# Steps that block save when they fail; the remainder are advisory.
# Phase-agnostic: phase A passes [liveness]; phase B passes the model
# steps. Each phase only ever feeds its own step names, so keeping both
# blocking names in one set is harmless and keeps the helper reusable.
_BLOCKING_STEPS: set[ProbeStepName] = {"liveness", "reasoning"}


def _aggregate_overall(steps: list[ProbeStep]) -> tuple[str, bool]:
    """Roll up per-step statuses into (overall, blocking_failed)."""
    blocked = any(s.status == "fail" and s.name in _BLOCKING_STEPS for s in steps)
    if blocked:
        return "fail", True
    if any(s.status in ("fail", "warn") for s in steps):
        return "warn", False
    return "pass", False
```

- [ ] **Step 4: Confirm tests pass**

```bash
uv run pytest tests/test_provider_probe.py -v 2>&1 | tail -5
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/services/provider_probe.py backend/tests/test_provider_probe.py
git commit -m "feat(probe): ProbeResult types + overall aggregation"
```

---

## Task 7: Probe step — liveness + reasoning

**Files:**
- Modify: `backend/cubeplex/services/provider_probe.py`
- Modify: `backend/tests/test_provider_probe.py`

- [ ] **Step 1: Append failing tests**

```python
import pytest

from cubeplex.services.provider_probe import (
    probe_liveness, probe_reasoning_toggle,
)


class _StubProvider:
    """Fake cubepi.Provider for probe tests.

    Records calls and returns canned events.
    """

    def __init__(self, *, events=None, raise_error=None):
        self._events = events or []
        self._raise_error = raise_error
        self.calls: list[dict] = []

    async def stream(self, model, messages, *, options=None, system_prompt=""):
        self.calls.append({"thinking": getattr(options, "thinking", "off")})
        if self._raise_error is not None:
            raise self._raise_error
        # Return a tiny async iterator yielding the canned events + a done.
        class _Stream:
            def __aiter__(_self):
                async def gen():
                    for e in self._events:
                        yield e
                return gen()
        return _Stream()


@pytest.mark.asyncio
async def test_probe_liveness_pass():
    """Endpoint responds in time → step passes with latency."""
    provider = _StubProvider(events=[type("E", (), {"type": "text_delta", "delta": "OK"})()])
    step = await probe_liveness(provider, model_id="test-model")
    assert step.name == "liveness"
    assert step.status == "pass"
    assert step.latency_ms is not None


@pytest.mark.asyncio
async def test_probe_liveness_fail_on_exception():
    """Provider raises → step fails with the error type."""
    provider = _StubProvider(raise_error=RuntimeError("401 Unauthorized"))
    step = await probe_liveness(provider, model_id="test-model")
    assert step.status == "fail"
    assert step.error is not None
    assert "401" in step.error.message


@pytest.mark.asyncio
async def test_probe_reasoning_skips_when_capability_empty():
    """No reasoning_off / on payload → reasoning probe is skipped."""
    from cubepi.providers.capability import CapabilityDescriptor
    cap = CapabilityDescriptor()
    provider = _StubProvider()
    step = await probe_reasoning_toggle(provider, model_id="m", capability=cap)
    assert step.status == "skip"


@pytest.mark.asyncio
async def test_probe_reasoning_runs_both_off_and_on():
    """Capability with reasoning payloads → probe sends both off and on requests."""
    from cubepi.providers.capability import CapabilityDescriptor
    cap = CapabilityDescriptor(
        reasoning_off_payload={"extra_body": {"enable_thinking": False}},
        reasoning_on_payload={"extra_body": {"enable_thinking": True}},
    )
    provider = _StubProvider(events=[type("E", (), {"type": "text_delta", "delta": "OK"})()])
    step = await probe_reasoning_toggle(provider, model_id="m", capability=cap)
    assert step.status == "pass"
    # Two calls (off then on).
    assert len(provider.calls) == 2
    assert provider.calls[0]["thinking"] == "off"
    assert provider.calls[1]["thinking"] == "medium"
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_provider_probe.py::test_probe_liveness_pass -v 2>&1 | tail -5
```

Expected: ImportError on `probe_liveness`.

- [ ] **Step 3: Implement**

Append to `backend/cubeplex/services/provider_probe.py`:

```python
import asyncio
import time
from typing import Any

from cubepi.providers.base import (
    Model, StreamOptions, TextContent, UserMessage,
)
from cubepi.providers.capability import CapabilityDescriptor


async def _drain_stream(provider: Any, model_id: str, *, thinking: str = "off",
                        prompt: str = "Reply with OK.", max_output: int = 64,
                        max_seconds: float = 15.0) -> tuple[list, float]:
    """Run a minimal stream, draining events. Return (events, elapsed_seconds)."""
    start = time.perf_counter()
    stream = await asyncio.wait_for(
        provider.stream(
            model=Model(id=model_id, provider="probe", context_window=8192,
                        max_tokens=max_output),
            messages=[UserMessage(content=[TextContent(text=prompt)])],
            options=StreamOptions(thinking=thinking),
        ),
        timeout=max_seconds,
    )
    events = []
    async for evt in stream:
        events.append(evt)
        if getattr(evt, "type", None) == "done":
            break
    return events, time.perf_counter() - start


async def probe_liveness(provider: Any, *, model_id: str) -> ProbeStep:
    # Spec §4.4 step 1: minimal completion — max_tokens=1, prompt ".",
    # 5s timeout. Just proves base_url + key + network reach the endpoint.
    try:
        events, elapsed = await _drain_stream(
            provider, model_id, thinking="off", prompt=".", max_output=1,
            max_seconds=5.0,
        )
    except (asyncio.TimeoutError, Exception) as exc:
        return ProbeStep(
            name="liveness",
            status="fail",
            error=ProbeError(type=type(exc).__name__, message=str(exc)[:200]),
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
    if not capability.reasoning_off_payload and not capability.reasoning_on_payload:
        return ProbeStep(
            name="reasoning",
            status="skip",
            detail="capability has no reasoning_off/on payload",
        )
    try:
        await _drain_stream(provider, model_id, thinking="off")
        on_events, _ = await _drain_stream(provider, model_id, thinking="medium")
    except Exception as exc:
        err = ProbeError(type=type(exc).__name__, message=str(exc)[:200])
        # A model-not-found error here is what Task 9's _is_model_not_found
        # keys on to short-circuit to "unavailable" — keep the type/status
        # in ProbeError so the caller can classify it.
        return ProbeStep(
            name="reasoning",
            status="fail",
            error=err,
        )
    # Capture the chunk count so reasoning_events_of() / the streaming
    # check can confirm a chunk arrived without re-streaming.
    return ProbeStep(
        name="reasoning",
        status="pass",
        detail="off + on payload both accepted",
        observed_chunks=len(on_events),
    )
```

`_is_model_not_found(step)` (used by Task 9) inspects
`step.error` — true when `error.raw_status == 404` or `error.type` /
`error.message` indicate the vendor's `model_not_found`. Populate
`ProbeError.raw_status` in `_drain_stream`'s except path where the
cubepi error exposes a status code, so this classification is reliable.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_provider_probe.py -v 2>&1 | tail -8
```

Expected: 8 passed (4 from Task 6 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/services/provider_probe.py backend/tests/test_provider_probe.py
git commit -m "feat(probe): liveness + reasoning toggle steps"
```

---

## Task 8: Probe step — temperature + tools + streaming

**Files:**
- Modify: `backend/cubeplex/services/provider_probe.py`
- Modify: `backend/tests/test_provider_probe.py`

Same TDD pattern as Task 7. Implement three more helpers:

```python
async def probe_temperature(
    provider: Any, *, model_id: str, capability: CapabilityDescriptor
) -> ProbeStep:
    """Send a probe at capability.temperature.default (or fixed_value).
    'ignored' mode → skip."""

async def probe_tools(
    provider: Any, *, model_id: str, capability: CapabilityDescriptor
) -> ProbeStep:
    """If capability.supports_tools, send a one-tool probe and verify tool_call.
    Else → skip."""

def probe_streaming(*, observed_chunks: int, name: str = "streaming") -> ProbeStep:
    """Pure inspection: did a chunk arrive during the reasoning probe?
    `observed_chunks` comes from ProbeStep.observed_chunks (set by
    probe_reasoning_toggle). 0 → warn (don't fail); >0 → pass."""
```

- [ ] Write tests for each (one happy path + one skip path each = 6 tests).
- [ ] **Add the empty-chunk regression test:** `probe_streaming(observed_chunks=0)`
      → `status="warn"`; `probe_streaming(observed_chunks=3)` → `status="pass"`.
      (This is the test that would have caught the inert `events=[]` bug.)
- [ ] Implement helpers — use `StreamOptions(thinking="off")` to keep
      the temperature probe cheap; use the stub `_StubProvider` pattern
      from Task 7 for the tool-call event shape.
- [ ] Run all probe tests; expect 15 passed (8 prior + 6 new + 1 streaming).
- [ ] Commit:
   ```bash
   git commit -m "feat(probe): temperature + tools + streaming steps"
   ```

---

## Task 9: Two-phase orchestrators (liveness + per-model probe)

**Files:**
- Modify: `backend/cubeplex/services/provider_probe.py`
- Modify: `backend/tests/test_provider_probe.py`

> **rev-3:** there is no longer one `run_provider_probe`. The endpoint
> (Task 10) calls two entry points: `run_liveness(...)` (phase A, once per
> provider) and `run_model_probe(...)` (phase B, once per model). Phase B
> is only reached after phase A passes.

- [ ] **Step 1: Write the integration tests**

```python
@pytest.mark.asyncio
async def test_run_liveness_pass_and_fail():
    """Phase A: a good stub provider → ProbeStep(name='liveness', pass);
    a 401 stub → status='fail'."""
    from cubeplex.services.provider_probe import run_liveness
    # ok = await run_liveness(provider_factory=<good stub>, model_id="m")
    # assert ok.name == "liveness" and ok.status == "pass"
    # bad = await run_liveness(provider_factory=<401 stub>, model_id="m")
    # assert bad.status == "fail"

@pytest.mark.asyncio
async def test_run_model_probe_happy_path():
    """Phase B: stub provider with good events → overall=pass, reasoning passes."""
    from cubeplex.services.provider_probe import run_model_probe
    from cubepi.providers.capability import CapabilityDescriptor

    result = await run_model_probe(
        provider_factory=...,  # callable returning _StubProvider
        model_id="probe-model",
        capability=...,        # CapabilityDescriptor with non-empty reasoning payloads
    )
    assert result.overall == "pass"
    assert result.blocking_failed is False
    step_names = {s.name for s in result.steps}
    assert "reasoning" in step_names          # phase B's only blocking step
    assert "liveness" not in step_names       # liveness is phase A, not here
```

- [ ] **Step 2: Implement the two entry points**

```python
async def run_liveness(*, provider_factory, model_id: str) -> ProbeStep:
    """Phase A — provider grain. One minimal call against any model.
    Caller persists the result to providers.last_liveness_*."""
    provider = provider_factory()
    return await probe_liveness(provider, model_id=model_id)


async def run_model_probe(
    *,
    provider_factory,   # callable -> cubepi.Provider
    model_id: str,
    capability: CapabilityDescriptor,
) -> ProbeResult:
    """Phase B — model grain. Assumes phase A already passed. Runs the
    capability steps and aggregates. Caller persists the result to that
    models row's last_test_*."""
    provider = provider_factory()

    # model-not-found is a valid Phase B outcome (spec §4.1/§4.4): a probe
    # against a model the vendor doesn't offer must yield "unavailable".
    # The per-step helpers raise/return on model_not_found; detect it here.
    reasoning = await probe_reasoning_toggle(provider, model_id=model_id, capability=capability)
    if _is_model_not_found(reasoning):
        return ProbeResult(overall="unavailable", blocking_failed=True, steps=[reasoning])

    # reasoning already drained a stream → reuse its observed chunk count for
    # the streaming check instead of passing an empty list (spec §4.4 step 5).
    temperature, tools = await asyncio.gather(
        probe_temperature(provider, model_id=model_id, capability=capability),
        probe_tools(provider, model_id=model_id, capability=capability),
        return_exceptions=False,
    )
    streaming = probe_streaming(observed_chunks=reasoning.observed_chunks)
    steps = [reasoning, temperature, tools, streaming]
    overall, blocked = _aggregate_overall(steps)
    return ProbeResult(overall=overall, blocking_failed=blocked, steps=steps)
```

> **Status mapping (spec §4.1 vs §4.4).** Probe steps speak
> `pass/fail/warn/skip`; the persisted columns speak a different
> vocabulary. Define the translation explicitly and persist via it:
> - liveness `pass → providers.last_liveness_status = "ok"`; `fail → "fail"`.
> - model probe `overall`: `pass → "ok"`, `warn → "warn"`, `fail → "fail"`;
>   the `unavailable` short-circuit above writes `"unavailable"` directly.
>
> `probe_reasoning_toggle` (Task 7) must distinguish a model_not_found
> error from a generic 4xx so `_is_model_not_found` can fire; it also sets
> `ProbeStep.observed_chunks` from its drained stream so the streaming
> check reads `reasoning.observed_chunks` instead of re-streaming.

- [ ] **Step 3: Run all probe tests; expect green (prior + 2 new)**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(probe): two-phase orchestrators — run_liveness + run_model_probe"
```

---

## Task 10: Test endpoints — liveness + pre-save + per-model + all-models

> **rev-3 endpoints (spec §4.3):**
> - `POST /admin/providers/liveness` + `/{id}/liveness` — phase A only.
> - `POST /admin/providers/test` — pre-save: phase A then phase B against
>   `model_id`; returns one composed `ProbeResult` whose `steps` are
>   `[liveness, *model_steps]` (so the wizard sees liveness first). No DB
>   write.
> - `POST /admin/providers/{id}/models/{mid}/test` — saved single model:
>   phase A (writes provider liveness) + phase B (writes that model's
>   `last_test_*`).
> - `POST /admin/providers/{id}/test` — all enabled models: phase A once,
>   then phase B per model; returns `ProbeResult[]`; persists each.

**Files:**
- Extend: `backend/cubeplex/api/routes/v1/admin_providers.py` (per
  Amendment A2 — provider-row endpoints, NOT `admin_llm.py`).
- Modify: `backend/tests/test_admin_llm_endpoints.py`

- [ ] **Step 1: Define the request body shape**

```python
class ProviderTestRequest(BaseModel):
    """Pre-save dry-run probe input."""
    preset_slug: str | None = None
    api: str                        # wire api
    base_url: str
    api_key: str | None = None
    capability: dict
    model_capability_overrides: dict[str, dict] = {}
    # The model to probe (spec §4.3 names this `model_id`). Caller passes
    # one explicit pick from the preset's default_models.
    model_id: str
```

- [ ] **Step 2: Append failing test**

```python
@pytest.mark.asyncio
async def test_probe_dryrun_returns_step_summary(admin_client, monkeypatch):
    # Monkey-patch both phase entry points to deterministic results.
    from cubeplex.services import provider_probe
    async def stub_liveness(*a, **k):
        return provider_probe.ProbeStep(name="liveness", status="pass", latency_ms=180)
    async def stub_model(*a, **k):
        return provider_probe.ProbeResult(
            overall="pass", blocking_failed=False,
            steps=[provider_probe.ProbeStep(name="reasoning", status="pass")],
        )
    monkeypatch.setattr(provider_probe, "run_liveness", stub_liveness)
    monkeypatch.setattr(provider_probe, "run_model_probe", stub_model)

    resp = await admin_client.post(
        "/api/v1/admin/providers/test",
        json={
            "preset_slug": "qwen-dashscope",
            "api": "openai-completions",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-test",
            "capability": {"reasoning_off_payload": {"extra_body": {"enable_thinking": False}}},
            "model_id": "qwen3.6-flash",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall"] == "pass"
    assert body["blocking_failed"] is False
    assert body["steps"][0]["name"] == "liveness"
```

- [ ] **Step 3: Implement the pre-save `/test` + `/liveness` endpoints**

Pre-save `POST /providers/liveness` builds a transient cubepi.Provider
from the request body, calls `run_liveness` only, returns the step. **No
DB write** (dry-run — there's no row yet).

Saved `POST /providers/{id}/liveness` looks up the row, rebuilds its
provider, calls `run_liveness`, and **persists** `providers.last_liveness_*`
(`ok`/`fail` per the mapping). This is the re-check path (spec §4.3).

The pre-save `/test` builds a transient provider, calls `run_liveness`;
if it fails, returns a `ProbeResult` with `steps=[liveness]`,
`blocking_failed=True`; if it passes, calls `run_model_probe(model_id)`
and returns a composed `ProbeResult` with
`steps=[liveness, *model_result.steps]`. **No DB write** (dry-run).

- [ ] **Step 4: Implement the saved-provider test endpoints + persistence**

`/{id}/models/{mid}/test`: look up the saved provider + that model,
build the cubepi.Provider the way `LLMFactory.build_cubepi_provider`
does. Run `run_liveness` → **persist** `providers.last_liveness_*`. If
liveness passed, run `run_model_probe` → **persist** that model's
`last_test_at` / `last_test_status` / `last_test_summary`.

`/{id}/test`: run `run_liveness` once → persist provider liveness. If it
passed, fan out `run_model_probe` over every `enabled` model, persist
each model's `last_test_*`, and return the list of results.

Add regression tests for both persistence targets (provider liveness row
+ model test row).

- [ ] **Step 5: Run admin endpoint tests; expect green**

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(admin): liveness + pre-save + per-model + all-models test endpoints"
```

---

## Task 10b: Runtime status writeback (spec §4.4a)

**Files:**
- Modify: the agent LLM call path (where cubepi stream errors surface —
  locate via `grep -rn "build_cubepi_provider\|except" backend/cubeplex/llm`).
- Modify: `backend/tests/test_provider_runtime_writeback.py` (create).

> Tests are point-in-time; keys get revoked and models retired between
> probes. Mirror MCP's "refresh failure flips authed=false" so the UI
> reflects reality without a manual re-test.

- [ ] **Step 1: Failing tests**

  - A real call raising an auth error (401/403) → `providers.last_liveness_status`
    flips to `"fail"`.
  - A real call raising model-not-found (vendor `model_not_found` / 404
    on the model) → that `models.last_test_status` flips to
    `"unavailable"`; sibling models untouched.
  - A subsequent successful call clears provider liveness back to `"ok"`.

- [ ] **Step 2: Implement the writeback hook**

In the agent call path, wrap the cubepi stream call: on the mapped
error types, enqueue a best-effort status update (separate DB session /
background task) keyed by provider_id (liveness) or (provider_id,
model_id) (model). **Never block or fail the live request on the status
write** — swallow writeback errors and log.

- [ ] **Step 3: Run the writeback tests; expect green**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(llm): runtime status writeback — 401->liveness fail, model_not_found->unavailable (§4.4a)"
```

---

## Task 11: Task model resolver

**Files:**
- Create: `backend/cubeplex/services/task_model_resolver.py`
- Modify: `backend/cubeplex/models/org_settings.py`
- Modify: `backend/cubeplex/llm/config.py` (yaml fallback)
- Create: `backend/tests/test_task_model_resolver.py`

- [ ] **Step 1: Define the OrgSettings key constant**

In `backend/cubeplex/models/org_settings.py` (or wherever cubeplex keeps
its setting key constants), add:

```python
TASK_MODELS_KEY = "task_models"
# Value shape: {"chat": "<provider/model>", "title": "...", "summarize": "..."}
# All keys optional; missing keys fall back to default_model.
```

- [ ] **Step 2: Extend yaml `LLMConfig`**

Add an optional `title_model: str | None` (and other task-model keys
as forward-looking placeholders) to the pydantic `LLMConfig` schema in
`backend/cubeplex/llm/config.py`.

- [ ] **Step 3: Write failing tests**

Create `backend/tests/test_task_model_resolver.py`:

```python
"""Task model resolver — OrgSettings → yaml → default fallback."""

import pytest

from cubeplex.services.task_model_resolver import resolve_task_model


@pytest.mark.asyncio
async def test_resolve_title_uses_orgsettings_when_set(...):
    """OrgSettings.task_models['title'] takes precedence over yaml + default."""
    ...


@pytest.mark.asyncio
async def test_resolve_title_falls_back_to_yaml_title_model(...):
    """No OrgSettings entry → use config.llm.title_model."""
    ...


@pytest.mark.asyncio
async def test_resolve_title_falls_back_to_default_model(...):
    """No OrgSettings, no yaml title_model → use default_model."""
    ...


@pytest.mark.asyncio
async def test_resolve_unknown_task_returns_default(...):
    """resolve_task_model('summarize') with nothing configured → default_model."""
    ...
```

Fill in the fixtures by mocking an `LLMFactory` with the relevant
session + org_id + yaml config.

- [ ] **Step 4: Implement `resolve_task_model`**

> **Amendment A4:** `resolve_task_model` must be a **drop-in** replacement
> for `resolve_default_provider_and_config` — it returns the same 3-tuple
> `(provider_name, model_id, provider_config)` and loads/merges DB provider
> configs the SAME way (`_load_db_provider_configs` + `_build_merged_config`),
> then resolves the task ref against the merged config. Returning a bare
> `(provider_name, model_id)` and re-looking-up the config via
> `factory.llm_config.providers[...]` misses DB-overridden providers.

```python
async def resolve_task_model(
    factory: "LLMFactory", task: str
) -> tuple[str, str, ProviderConfig]:
    """Resolve (provider_name, model_id, provider_config) for ``task``.

    Walks: OrgSettings.task_models[task] → config.llm.<task>_model
    (e.g. ``title_model``) → default. The provider_config is taken from
    the SAME merged config that resolve_default_provider_and_config builds,
    so DB-overridden providers resolve correctly.
    """
    merged = await factory._build_merged_config()  # DB + yaml, same as default path

    def _resolve_ref(model_ref: str) -> tuple[str, str, ProviderConfig]:
        provider_name, model_id = factory._parse_model_ref(model_ref)
        return provider_name, model_id, merged.providers[provider_name]

    # 1. OrgSettings
    if factory._session and factory._org_id:
        from cubeplex.models.org_settings import OrgSettings as DBS, TASK_MODELS_KEY
        stmt = select(DBS).where(
            DBS.org_id == factory._org_id,
            DBS.key == TASK_MODELS_KEY,
        )
        result = await factory._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row and (model_ref := (row.value or {}).get(task)):
            return _resolve_ref(model_ref)

    # 2. yaml fallback (e.g. config.llm.title_model)
    yaml_ref = getattr(factory.llm_config, f"{task}_model", None)
    if yaml_ref:
        return _resolve_ref(yaml_ref)

    # 3. default — already returns the merged 3-tuple
    return await factory.resolve_default_provider_and_config()
```

(Confirm the real internal helper names — `_build_merged_config`,
`resolve_default_provider_and_config` — against `factory.py` when
implementing; match whatever the default path actually calls.)

- [ ] **Step 5: Run tests; expect 4 passed**

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(llm): resolve_task_model walks orgsettings -> yaml -> default"
```

---

## Task 12: Title-gen service uses `resolve_task_model("title")`

**Files:**
- Modify: `backend/cubeplex/services/conversation_title.py`
- Create: `backend/tests/e2e/test_title_model_routing_e2e.py`

- [ ] **Step 1: Append failing E2E test**

Create `backend/tests/e2e/test_title_model_routing_e2e.py`:

```python
"""E2E: title generation routes to OrgSettings.task_models['title']."""

import pytest


@pytest.mark.asyncio
async def test_title_uses_configured_title_model(client, db, ...):
    """With OrgSettings.task_models['title'] = small-model, title gen
    calls that model instead of the default reasoning chat model."""
    # 1. Seed OrgSettings with task_models = {"title": "small-provider/small-model"}.
    # 2. Trigger generate-title via the conversations API.
    # 3. Assert the provider/model picked is the small one (inspect via
    #    request_listeners on the cubepi provider, or via a captured-payload
    #    fixture). The fastest path is to monkey-patch
    #    factory.resolve_task_model and assert called with "title".
    ...
```

- [ ] **Step 2: Change the title-gen service**

Open `backend/cubeplex/services/conversation_title.py`. Find the call:

```python
provider_name, model_id, provider_config = await factory.resolve_default_provider_and_config()
```

Replace with (drop-in — same 3-tuple, no separate config lookup):

```python
from cubeplex.services.task_model_resolver import resolve_task_model

provider_name, model_id, provider_config = await resolve_task_model(factory, "title")
```

Also pass `StreamOptions(thinking="off")` to the cubepi call —
ensures even if a reasoning model is mistakenly configured, the
descriptor's reasoning-off payload disables thinking.

- [ ] **Step 3: Run e2e + unit tests**

```bash
uv run pytest tests/e2e/test_title_model_routing_e2e.py tests/unit/test_conversation_title.py -v 2>&1 | tail -10
```

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(title): route conversation_title via resolve_task_model('title')"
```

---

## Task 13: Seed migration — backfill capability for system providers

**Files:**
- Modify: existing seed module (probably
  `backend/cubeplex/db/seeds/system_providers.py` or similar — locate
  via grep first).
- Or: new alembic data-migration revision.

- [ ] **Step 1: Locate the existing system-provider seed**

```bash
grep -rn "system_providers\|seed_providers\|seed.*Provider" backend/cubeplex 2>&1 | head -5
```

The cubeplex bootstrap inserts a known set of system providers
(probably alicode/sensedeal/etc. from the yaml). Find that path and
extend it.

- [ ] **Step 2: Build the slug→preset capability lookup**

When the seed inserts a Provider row whose name matches a known
preset slug, also fill `preset_slug`, `capability`, and
`model_capability_overrides` from `cubepi.get_provider_preset(slug)`:

```python
from cubepi import get_provider_preset

def _capability_for(slug: str) -> tuple[str | None, dict, dict]:
    """If the seed slug matches a cubepi preset, return its capability JSON."""
    try:
        preset = get_provider_preset(slug)
    except KeyError:
        return None, {}, {}
    return (
        preset.slug,
        preset.capability.model_dump(mode="json"),
        {
            mid: cap.model_dump(mode="json")
            for mid, cap in preset.model_capability_overrides.items()
        },
    )
```

Wire that into the existing seed insert call.

- [ ] **Step 3: Add a data-migration step (idempotent)**

For installs that already have provider rows from before this slice,
add a one-shot data migration that backfills capability for rows
where it's still empty AND the row's `name` matches a preset slug.
Create a new alembic revision (data migration, not schema):

```bash
alembic revision -m "backfill provider capability from cubepi catalog"
```

The migration body uses `op.execute(...)` with a SQL statement that
joins on `name = slug` is not portable — better to do the lookup in
Python:

```python
def upgrade():
    conn = op.get_bind()
    from cubepi import list_provider_presets
    presets = {p.slug: p for p in list_provider_presets()}
    rows = conn.execute(sa.text(
        "SELECT id, name FROM providers "
        "WHERE (capability IS NULL OR capability::text = '{}')"
    )).fetchall()
    for row in rows:
        preset = presets.get(row.name)
        if not preset:
            continue
        conn.execute(
            sa.text(
                "UPDATE providers SET preset_slug = :slug, capability = :cap "
                "WHERE id = :id"
            ),
            {
                "slug": preset.slug,
                "cap": json.dumps(preset.capability.model_dump(mode="json")),
                "id": row.id,
            },
        )
```

- [ ] **Step 4: Apply migration and verify**

```bash
alembic upgrade head
uv run python -c "
import asyncio, json
from cubeplex.db import async_session
from sqlalchemy import select
from cubeplex.models.provider import Provider

async def run():
    async with async_session() as s:
        rows = (await s.execute(select(Provider))).scalars().all()
        for r in rows:
            print(r.name, '->', bool(r.capability))
asyncio.run(run())
"
```

Expected: every known-slug system provider has `capability != {}`.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(seed): backfill provider capability from cubepi catalog"
```

---

## Task 14: Final regression sweep + PR

- [ ] **Step 1: Full backend test run**

```bash
cd /home/chris/cubeplex/.worktrees/feat/llm-provider-platform/backend
uv run pytest -q --tb=short -x 2>&1 | tail -10
```

Expected: all green. If anything was structurally coupled to the old
`_PROVIDER_TYPE_TO_API` mapping or to `payload_quirks`, surface it
now.

- [ ] **Step 2: Worktree port sanity**

Confirm the worktree env is loaded (the backend should boot on port
8028, not 8000, per `.worktree.env`):

```bash
cd /home/chris/cubeplex/.worktrees/feat/llm-provider-platform
./scripts/worktree-env doctor 2>&1 | tail -3
```

- [ ] **Step 3: ruff + mypy if configured**

```bash
cd backend && uv run ruff check cubeplex tests && \
  (grep -q "\[tool.mypy\]" pyproject.toml && uv run mypy cubeplex || echo "mypy skipped")
```

- [ ] **Step 4: Boot the server, do a smoke admin call**

```bash
cd /home/chris/cubeplex/.worktrees/feat/llm-provider-platform
# (Use the worktree's wrapped backend launcher per docs/worktrees.md.)
# After it's up, curl /api/v1/admin/llm/presets and confirm 20 entries.
```

- [ ] **Step 5: Commit any stragglers, push, open PR**

```bash
git push
gh pr create --title "feat(cubeplex): capability schema + admin probe + task-model routing" \
  --body "$(cat <<'EOF'
## Summary

Slice 2 of the LLM Provider Platform spec — cubeplex side. Brings
`CapabilityDescriptor` end-to-end: provider rows carry it as JSON, the
factory plumbs it through to cubepi, an admin Test endpoint validates
a candidate config before save, and conversation title generation is
routed via a configurable `task_models.title` (fixing the original 30s
title-gen incident for admins who route title to a small model).

**Depends on:** cubepi `feat/capability-descriptor` branch (see
`backend/pyproject.toml` for the pinned commit). cubepi 0.5.0 release
not required.

**Scope:** M3 + M4 + M6 from the spec. M5 (Add Provider wizard UI) and
M7 (polish) are the next slice.

## What changed

- DB: 6 new columns on `providers` (preset_slug, capability,
  model_capability_overrides, last_liveness_at, last_liveness_status,
  last_liveness_summary) + 3 new columns on `models` (last_test_at,
  last_test_status, last_test_summary) — rev-3 two-grain split.
  `provider_type` semantics shifted to wire api literally; one-shot SQL
  backfill in the migration.
- `LLMFactory.build_cubepi_provider` reads JSON capability + overrides
  → typed pydantic → passes to cubepi.
- `cubeplex/llm/readiness.py` — pure §4.1 readiness-derivation helper.
- `GET /api/v1/admin/llm/presets` — serves cubepi's 20-preset catalog.
- `GET /api/v1/admin/providers/{id}` — includes capability,
  model_capability_overrides, provider last_liveness_*, and per-model
  last_test_* + derived readiness.
- Test endpoints (§4.3): `/liveness` (+`/{id}/liveness`), pre-save
  `/test`, `/{id}/models/{mid}/test`, `/{id}/test` (all enabled models) —
  two-phase probe: phase A liveness (provider grain) + phase B capability
  (model grain: reasoning blocking, temperature/tools/streaming advisory).
- Runtime status writeback (§4.4a): 401/403 → provider liveness fail;
  model_not_found → model unavailable; best-effort, out-of-band.
- `OrgSettings.task_models` + `LLMFactory.resolve_task_model(task)` —
  conversation_title uses it; passes `StreamOptions(thinking="off")`.
- Seed migration backfills capability for known system providers from
  cubepi catalog.

## Test plan
- [x] Factory round-trip with capability JSON (5 tests)
- [x] Admin endpoints: preset list + provider GET shape (N tests)
- [x] Probe runner per-step (14 tests including aggregator + orchestrator)
- [x] Probe endpoint dry-run + persistence (2 tests)
- [x] Task model resolver fallback chain (4 tests)
- [x] E2E: title-gen routed to configured task_models.title
- [x] No regressions in existing factory / conversation_title tests

@codex please review.
EOF
)"
```

Then run the pr-codex-review-loop on the new PR per the user's
workflow.

---

## Self-Review Notes (filled during writing)

- **Spec §4.1 (DB schema)** → Task 2.
- **Spec §4.2 (LLMFactory)** → Task 3.
- **Spec §4.3 (admin endpoints, read-only catalog + provider GET)** →
  Tasks 4 + 5.
- **Spec §4.3 (POST /test + /{id}/test)** → Task 10.
- **Spec §4.4 (5-step probe sequence)** → Tasks 6 + 7 + 8 + 9.
- **Spec §4.6 (task model routing)** → Tasks 11 + 12.
- **Spec §6 M3 seed migration** → Task 13.

Skipped vs spec for this slice:
- §4.5 (Add Provider wizard UI) → slice 3.
- §7 polish (provider detail status dot, re-test button, i18n) →
  slice 3.

---

**Plan complete and saved to
`docs/dev/plans/2026-05-20-llm-provider-platform-slice-2.md`.** Send to
local codex for review before starting implementation.
