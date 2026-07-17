# LLM Snapshot Refactor — Spec 1

**Status:** Draft
**Date:** 2026-06-09
**Branch:** `feat/llm-snapshot-refactor`
**Worktree:** `/home/chris/cubeplex/.worktrees/feat/llm-snapshot-refactor`

## Context

cubepi 0.9.0 ships `FallbackBoundModel` — an ordered chain of `BoundModel`
that transparently fails over on `RateLimited / ProviderUnavailable /
ContextLengthExceeded`. cubeplex has carried a TODO at
`backend/cubeplex/streams/run_manager.py:1991` waiting for exactly this
upstream feature.

This refactor lands fallback support, but does not stop there. The current
`LLMFactory` class — named in the langgraph era — has accumulated mixed
responsibilities (DB I/O, config merging, ref parsing, provider construction,
table lookups) and is the wrong abstraction for the new cubepi mental model
(`Provider → BoundModel`, with a clear `FallbackBoundModel` wrapper).

Simultaneously, the product team is reshaping LLM configuration: from "org
picks one default model + one global fallback list" to **named presets**
(ultra / max / pro / mini / flash — labels are user-defined) each with its
own fallback chain, with per-task and per-message selection on top.

The three concerns — fallback enablement, code structure cleanup, and the
preset product model — share enough code paths that solving any one cleanly
requires fixing the other two. This spec covers the backend foundation.
The admin CRUD experience and the workspace chat picker UI are separate
specs.

### Scope decomposition

Three specs, executed in order:

1. **Spec 1 (this document):** Backend foundation — data model, runtime
   resolver/builder, `FallbackBoundModel` integration, per-message API
   fields. No frontend changes.
2. **Spec 2:** Admin preset management — `/api/v1/admin/model-presets` CRUD,
   admin frontend, "delete model blocked by referencing presets" UX.
3. **Spec 3:** Workspace chat composer — preset picker, thinking depth
   control, frontend rendering of `model_failover` SSE events.

Specs 2 and 3 can be developed in parallel after Spec 1 lands.

## Goals

- Delete `LLMFactory`; replace with a three-module split (`snapshot.py` /
  `resolver.py` / `builder.py`) where I/O and pure logic are separated.
- Introduce `LLMPreset` as the org-level configuration unit: a labeled,
  ordered chain of model refs, with at most one `is_default=true`.
- Wire `cubepi.FallbackBoundModel` into the main agent and subagent code
  paths, with per-failover SSE marker emission.
- Accept per-message `preset_label` and `thinking` on the main conversation
  SSE endpoint.
- Reduce YAML's role from "live runtime configuration" to
  "bootstrap-only seeder input" — consistent with how provider/model rows
  are already handled.

## Non-goals

- Admin CRUD endpoints or admin frontend for preset management.
- Workspace API to list available presets.
- Workspace chat composer UI (preset picker / thinking control).
- Frontend rendering of `model_failover` events.
- Image-generation model selection (kept on its own track via
  `config.llm.images.*`).
- Per-workspace or per-user preset overrides (future iteration).
- Cubepi-side `Tracer.attach()` / `Meter.attach()` chain coverage — tracked
  as an upstream cubepi follow-up PR.

## Background and current state

### What works today

- **Seeder** (`cubeplex/seeders/provider_seeder.py`) reads `config.llm.providers`
  on startup, upserts `providers` / `models` / `credentials` tables. DB is
  the runtime source of truth for provider/model rows.
- **OrgSettings overrides** are partially wired: `default_model` and
  `task_models` are read by `LLMFactory._get_org_default_model()` and the
  `task_model_resolver`.

### What is broken

- **`LLMFactory` is a stateful god-object.** Its constructor parameters
  (`session`, `org_id`, `encryption_backend`) determine which subset of its
  methods actually work. Several methods mutate `self.llm_config` as a
  side effect. Call sites in `run_manager.py` carry a `try/except` that
  falls back from `LLMFactory(session=...)` to bare `LLMFactory()`,
  hiding the dual mode.
- **`fallback_models` is plumbed but never used.** `LLMConfig.fallback_models`,
  `LLMFactory._get_org_fallback_models()`, and the merged-config
  `fallback_models` field all exist; `run_manager.py:1991` explicitly
  notes the runtime ignores them.
- **YAML is read at every request.** `LLMFactory.__init__()` loads
  `config.llm` into memory; `_build_merged_config` then merges YAML
  providers (those not in DB) with DB providers on every factory
  instantiation. The merge has been a no-op since the seeder started
  covering every YAML provider, but the cost — and the false signal that
  YAML is live — remains.
- **Encapsulation has already broken.** `services/task_model_resolver.py`
  reaches into `factory._session`, `factory._org_id`, `factory._parse_model_ref`,
  `factory._load_db_provider_configs`, and `factory._build_merged_config`.
  When private fields become public API, the abstraction has failed.
- **Product model is wrong.** Org admin sets one default model; all
  workspaces are stuck with it. There is no way to expose
  multiple curated tiers (ultra / mini) or let a user pick per message.

### What cubepi 0.9.0 brought

`FallbackBoundModel`:

- Ordered tuple of `BoundModel`s; `chain[0]` is primary.
- Default trigger errors: `RateLimited`, `ProviderUnavailable`,
  `ContextLengthExceeded`. Mid-stream errors past the first event are
  forwarded as-is.
- `stream()` and `generate()` both fail over.
- `provider` and `spec` proxy `chain[0]` so existing tracing/billing code
  that reads `agent._model.spec.provider_id` continues working.
- `on_failover` callback — sync or async — receives `(failed, next, error)`.
- **Known limitation:** `Tracer.attach()` and `Meter.attach()` subscribe
  only to `chain[0].provider`. Successful fallback calls go untraced at
  the provider layer. The agent-level event stream is unaffected, so our
  `CostMiddleware` continues to attribute spend correctly.

cubepi also ships `FauxProvider` (`cubepi/providers/faux.py`) with
`set_responses([...])` — each step is an `AssistantMessage` or a factory
that may `raise RateLimited(...)`. The fallback E2E test reuses this
directly; no cubeplex-side stub provider is needed.

## Design

### Module layout

```
cubeplex/llm/
  snapshot.py          async — sole DB I/O entry point
  resolver.py          pure sync — operates on LLMSnapshot
  builder.py           pure sync — emits cubepi Provider / BoundModel
  config.py            unchanged shape; now seeder-only
  factory.py           DELETED
  catalog/             unchanged — provider protocol presets (separate concept)
```

`services/task_model_resolver.py` is deleted; its logic moves into
`resolver.resolve_task_preset`.

### `OrgSettings` schema refactor (added mid-implementation)

The original spec assumed `OrgSettings(org_id=NULL, key='model_presets')` would
just work for the system-level fallback row. It did not: the existing
table had `(org_id, key)` as a composite PK with both columns non-null and
`org_id` FK-referencing `organizations.id`.

The fix matches the established Credential / Provider system-row pattern:

- Add a surrogate `id: str` primary key with `oset-` prefix.
- Make `org_id: str | None` nullable.
- Replace the composite PK with two partial unique indexes:
  - `uq_org_settings_org_key` on `(org_id, key)` where `org_id IS NOT NULL`.
  - `uq_org_settings_system_key` on `(key,)` where `org_id IS NULL`.

This refactor lives in a dedicated alembic migration; it is a prerequisite
for `load_llm_snapshot` to read the system row.

### Data structures

```python
# cubeplex/llm/snapshot.py

from cubepi.providers.base import ThinkingLevel

@dataclass(frozen=True)
class LLMPreset:
    label: str                       # e.g. "ultra", "default", "mini"
    chain: tuple[str, ...]           # ordered model refs "slug/model_id"
    is_default: bool

@dataclass(frozen=True)
class LLMSnapshot:
    providers: dict[str, ProviderConfig]      # keyed by slug; from DB only
    presets: tuple[LLMPreset, ...]            # from OrgSettings.model_presets
    task_presets: dict[str, str]              # {"title": "mini", "compaction": "mini", ...}
```

Both dataclasses are frozen — once a request has its `LLMSnapshot`, it is
immutable for the duration of that request.

### Snapshot storage

A new `OrgSettings` row with `key = 'model_presets'`:

```jsonc
{
  "presets": [
    {
      "label": "ultra",
      "chain": ["anthropic/claude-opus-4-7", "openai/gpt-4o"],
      "is_default": true
    },
    {
      "label": "mini",
      "chain": ["openai/gpt-4o-mini"],
      "is_default": false
    }
  ],
  "task_presets": {
    "title": "mini",
    "compaction": "mini",
    "summarize": "mini"
  }
}
```

Loading semantics: `load_llm_snapshot` queries OrgSettings for
`org_id IS NULL` (system fallback, written by seeder) and `org_id = <org>`
(admin-customised). If the org row exists, it **replaces** the system row
in full — no field-level merge. List invariants like "exactly one default"
do not field-merge correctly; an "all or nothing" override avoids
half-configured states.

Pydantic schema validation runs both at write time (Spec 2's admin
endpoint) and at read time (`load_llm_snapshot`). Invariants enforced:

1. `label` non-empty, ASCII alphanumeric / `-` / `_`, ≤64 chars, unique
   within `presets`.
2. `chain` has at least one ref.
3. Exactly one entry has `is_default = true`.
4. `task_presets` keys ⊆ `{"title", "compaction", "summarize"}`.
5. `task_presets` values ⊆ labels in `presets`.

Model-ref validation (`"slug/model_id"` format + slug ∈ snapshot.providers
+ model_id ∈ that provider's models) happens in `resolver` / `builder`,
not the schema — schema validation runs before providers are joined.

### YAML role

YAML reverts to seeder-only input. On startup, `seed_system_providers_from_config`
gains a second pass:

```
provider rows (existing)        ← config.llm.providers
OrgSettings(org_id=NULL,         ← config.llm.default_model
            key='model_presets')   + config.llm.fallback_models
                                   + config.llm.title_model
                                   + config.llm.compaction.summary_model
                                   + config.llm.summarize_model
```

The seeded preset has `label = "default"`, `is_default = true`, and chain
= `[default_model, *fallback_models]`. `task_presets` maps to a
single preset (also labeled `"default"`) when no explicit task model is
configured, or to a synthesized preset per task otherwise. Writing is
gated on row absence — admin edits never get overwritten on restart.

Runtime code does not import `cubeplex.config` for `llm.*` fields. Changing
YAML and not restarting has no effect — consistent with provider rows.

### Three pure modules

```python
# cubeplex/llm/snapshot.py

async def load_llm_snapshot(
    session: AsyncSession,
    org_id: str,
    encryption_backend: EncryptionBackend,
) -> LLMSnapshot:
    """Read DB + OrgSettings; return frozen snapshot. No YAML."""
```

```python
# cubeplex/llm/resolver.py

def resolve_preset(snap: LLMSnapshot, label: str | None) -> LLMPreset:
    """label=None → snap.presets where is_default. Raises LLMConfigError if absent."""

def resolve_task_preset(snap: LLMSnapshot, task: str) -> LLMPreset:
    """snap.task_presets[task] → label → preset. Falls back to default preset."""

def parse_model_ref(ref: str) -> tuple[str, str]:
    """'slug/model_id' → (slug, model_id). Raises LLMConfigError on bad shape."""
```

```python
# cubeplex/llm/builder.py

def build_provider(
    snap: LLMSnapshot,
    slug: str,
    *,
    cache_policy: CacheMarkerPolicy | None = None,
) -> Provider:
    """Route by snap.providers[slug].api to cubepi Provider subclass."""

def build_bound_model(
    snap: LLMSnapshot,
    ref: str,
    *,
    thinking: ThinkingLevel = "off",
    cache_policy: CacheMarkerPolicy | None = None,
) -> BoundModel:
    """Build provider, then provider.model(model_id, reasoning=…, max_tokens=…)
    with max_tokens/temperature pulled from snap.providers[slug].models[model_id]."""

def build_chain_model(
    snap: LLMSnapshot,
    preset: LLMPreset,
    *,
    thinking: ThinkingLevel = "off",
    cache_policy_factory: Callable[[str], CacheMarkerPolicy | None] | None = None,
    on_failover: OnFailoverCb | None = None,
) -> BoundModel | FallbackBoundModel:
    """chain length 1 → BoundModel; >1 → FallbackBoundModel. cache_policy_factory
    is called per chain element with the provider slug so AnthropicProvider
    legs can be marked while OpenAI legs are not."""
```

### Runtime wiring

#### Main agent

```python
# run_manager.py, replacing L1958-1999

async with async_session_maker() as s:
    snap = await load_llm_snapshot(s, ctx.org_id, self._app.state.encryption_backend)

preset = resolve_preset(snap, body.preset_label)
this_run_model = build_chain_model(
    snap, preset,
    thinking=body.thinking,
    cache_policy_factory=lambda slug:
        CubeplexCacheMarkerPolicy() if snap.providers[slug].api == "anthropic-messages" else None,
    on_failover=_make_failover_publisher(run_id, sse_publisher),
)
```

The fallback `try/except → LLMFactory()` block is deleted. The
hand-rolled `_model_max_tokens` / `_model_temperature` defaults are
deleted — `build_chain_model` binds these per BoundModel from
`snap.providers[slug].models[model_id]`.

#### Subagent

The subagent middleware receives the **same** `this_run_model` instance:

```python
SubagentMiddleware(default_model=this_run_model, ...)
```

`FallbackBoundModel` is stateless (each `stream()` / `generate()` call
starts from `chain[0]`), so sharing the instance is safe and correct —
subagent invocations inherit the same chain and the same fallback
behavior, including a fresh attempt at the primary on each call.

#### Task-level calls

`conversation_title.py` and `run_manager.run_consolidation`:

```python
async with async_session_maker() as s:
    snap = await load_llm_snapshot(s, org_id, backend)

preset = resolve_task_preset(snap, "title")     # or "compaction"
model = build_chain_model(snap, preset, thinking="off")
```

`"summarize"` is reserved in the schema but has no runtime consumer in
this spec. Spec 1 verifies the key parses and resolves; no call site is
wired.

#### Image generation

Unchanged. The image path goes through `config.llm.images.*` and stays
on its own track until a future spec brings it into the preset model.

### API surface

`POST /api/v1/ws/{ws}/conversations/{conv}/messages` request body adds
two fields:

```python
class CreateMessageBody(BaseModel):
    content: str | list[ContentBlock]
    # ... existing fields
    preset_label: str | None = None
    thinking: ThinkingLevel = "off"
```

`thinking` uses cubepi's `Literal["off", "minimal", "low", "medium", "high", "xhigh"]`
directly — no cubeplex-side enum or mapping.

Error semantics:

| Condition                                              | Code | Type                    |
|--------------------------------------------------------|------|-------------------------|
| `preset_label` set, no matching label                  | 400  | `unknown_preset`        |
| Resolved preset has broken refs (model not in snapshot)| 400  | `broken_preset` + refs  |
| No `preset_label`, no `is_default` preset              | 500  | `no_default_preset`     |
| `thinking` value not in enum                           | 422  | Pydantic                |
| Provider missing credential                            | 500  | `provider_not_configured` (existing) |

`LLMConfigError` base class lives in `cubeplex/errors.py` with subclasses
per case; a FastAPI exception handler maps each subclass to the right
status code and payload.

### Failover SSE event

A new SSE event type:

```python
class FailoverEvent(BaseModel):
    type: Literal["model_failover"] = "model_failover"
    failed_ref: str
    next_ref: str | None
    reason: str            # str(error), truncated to 256 chars
```

Emitted by an `on_failover` callback closure built per-request:

```python
def _make_failover_publisher(run_id, publish):
    async def _on_failover(failed, next_bound, error):
        await publish(run_id, {
            "type": "model_failover",
            "failed_ref": f"{failed.spec.provider_id}/{failed.spec.id}",
            "next_ref": f"{next_bound.spec.provider_id}/{next_bound.spec.id}" if next_bound else None,
            "reason": str(error)[:256],
        })
    return _on_failover
```

`attempt` and `chain_length` are deliberately omitted — cubepi's
`on_failover` signature does not currently expose them. They can be
added in a follow-up once cubepi widens the callback signature.

Frontend rendering of this event is Spec 3's responsibility. Spec 1
guarantees the event is defined and emitted.

### Observability gap

`Tracer.attach()` / `Meter.attach()` only subscribe to `chain[0].provider`.
After failover, chat spans and provider-level token/cost metrics are
absent for `chain[1..]`. `CostMiddleware` is agent-event-driven and is
unaffected — cost attribution remains correct.

This is accepted in Spec 1 and tracked as an upstream cubepi PR: modify
`Tracer.attach` and `Meter.attach` to detect `FallbackBoundModel` and
iterate `chain` providers, subscribing to each unique `BaseProvider`.

## Migration

A single alembic data migration:

```python
def upgrade():
    # For each (org_id, key) row where key in {'default_model',
    # 'fallback_models', 'task_models'}, synthesize the new
    # 'model_presets' row in the same org_id. Then delete the old rows.
    ...

def downgrade():
    # Reverse: split a 'model_presets' row back into the three legacy keys.
    ...
```

The migration covers existing dev-machine OrgSettings rows; the seeder
covers fresh installs. Both paths converge on the same final shape.

cubeplex has not shipped publicly (per `CLAUDE.md`), so backwards-compat
shims are out of scope: the migration runs, old keys are gone, no parallel
code paths.

## File-level change list

### Deleted

- `cubeplex/llm/factory.py`
- `cubeplex/services/task_model_resolver.py`

### New

- `cubeplex/llm/snapshot.py` — dataclasses, pydantic schema, `load_llm_snapshot`
- `cubeplex/llm/resolver.py` — pure resolution functions
- `cubeplex/llm/builder.py` — provider / BoundModel / chain builder
- `cubeplex/llm/errors.py` (or extend `cubeplex/errors.py`) — `LLMConfigError`
  hierarchy
- `alembic/versions/<rev>_migrate_orgsettings_to_model_presets.py`

### Modified

- `cubeplex/seeders/provider_seeder.py` — extend to seed
  `OrgSettings.model_presets` from `config.llm.{default_model,
  fallback_models, title_model, compaction.summary_model, summarize_model}`.
- `cubeplex/streams/run_manager.py` — all 6 `LLMFactory(...)` sites; delete
  fallback try/except; delete manual `_model_max_tokens` / `_temperature`
  computation; wire `_make_failover_publisher`.
- `cubeplex/services/conversation_title.py` — snapshot + resolver + builder.
- `cubeplex/services/provider_service.py` — `LLMFactory().build_cubepi_provider(...)`
  → `builder.build_provider(...)`.
- `cubeplex/services/usage.py` — `LLMFactory(...)` → `load_llm_snapshot(...)`.
- `cubeplex/models/org_settings.py` — drop `TASK_MODELS_KEY` and related;
  add `MODEL_PRESETS_KEY = "model_presets"`.
- `cubeplex/llm/config.py` — unchanged shape; comment update noting
  seeder-only role.
- `cubeplex/errors.py` — add `LLMConfigError` and subclasses; FastAPI
  handler registration in `api/app.py`.
- `cubeplex/api/...` — request body schema gains `preset_label` + `thinking`.

### Test changes

| File                                          | Action       |
|-----------------------------------------------|--------------|
| `tests/unit/test_llm_factory_cubepi.py`       | Rewrite as `test_snapshot_loader.py` |
| `tests/unit/test_task_model_resolver.py`      | Rewrite as part of `test_resolver.py` |
| `tests/unit/test_factory_slug_resolve.py`     | Folds into `test_snapshot_loader.py` |
| `tests/unit/llm/test_factory_provider_id.py`  | Becomes `test_builder.py` |
| `tests/unit/test_run_manager_build_agent.py`  | Mock `load_llm_snapshot` instead of factory |
| `tests/unit/test_conversation_title_pi.py`    | Same |
| `tests/test_provider_capability_factory.py`   | Builder test |
| `tests/e2e/test_admin_providers_crud.py`      | Import updates only |
| `tests/e2e/test_admin_llm_endpoints.py`       | Add preset + failover cases |
| `tests/e2e/test_title_model_routing_e2e.py`   | Verify task_preset routing |
| `tests/e2e/test_cubepi_path_conversation.py`  | Add per-message `preset_label` + `thinking` cases |

New tests:

- `tests/unit/test_seeder_presets.py` — seeder writes
  `OrgSettings.model_presets` on first run; subsequent runs do not
  overwrite admin edits.
- `tests/unit/test_failover_marker.py` — `_make_failover_publisher`
  payload structure.
- `tests/e2e/test_fallback_e2e.py` — FauxProvider on chain[0] raises
  `RateLimited`; real (or second Faux) on chain[1] returns a normal
  message; verify SSE marker + chain[1] used + CostMiddleware
  attribution.

## Testing strategy

- `resolver` and `builder` are pure; their tests construct an
  `LLMSnapshot` directly. No DB. No cubepi monkey-patching.
- `load_llm_snapshot` tests use the existing async-session fixture and
  cover: system-only row, org-override row, broken-ref preset detection,
  pydantic schema rejection on malformed JSON.
- Fallback E2E uses cubepi's `FauxProvider`:
  `chain[0] = FauxProvider().set_responses([lambda *_: raise RateLimited(...)])`;
  `chain[1]` is the real configured Anthropic provider (or a second
  FauxProvider with a normal `AssistantMessage` response). Assertions:
  SSE stream contains `model_failover`; final assistant message is
  attributable to `chain[1]`; CostMiddleware records cost against the
  successful provider.
- Mocking `FallbackBoundModel` itself is forbidden — the spec is about
  real cubepi behavior; mocked tests provide no value.
- Resolver tests are forbidden from passing in `session` — the type
  signature prevents it; reviewers reject anything that monkey-patches
  around the boundary.

## PR plan

Three PRs, in order, each independently revertable:

### PR 1 — Infrastructure refactor (no behavior change)

- New `snapshot.py` / `resolver.py` / `builder.py`.
- Seeder writes `OrgSettings.model_presets`.
- Alembic data migration.
- All call sites switched to new modules; `factory.py` and
  `task_model_resolver.py` deleted.
- `build_chain_model` returns a `BoundModel` (chain length forced to 1
  even when YAML has fallback_models — the second-leg upgrade is PR 2).
- Acceptance: existing E2E suite stays green. No new behavior.

### PR 2 — FallbackBoundModel integration

- `build_chain_model` returns `FallbackBoundModel` when chain length >1.
- `_make_failover_publisher` + `FailoverEvent` schema + SSE emission.
- `tests/e2e/test_fallback_e2e.py` with FauxProvider chains.
- Acceptance: fallback E2E green; main agent and subagent both fail over
  correctly.

### PR 3 — Per-message preset + thinking

- `CreateMessageBody.preset_label` + `thinking`.
- `LLMConfigError` hierarchy + FastAPI handler.
- E2E coverage of API error matrix.
- Acceptance: preset switching and thinking-depth selection both work;
  error codes match the schema.

PRs 1 and 2 must land before Spec 2 (admin CRUD) starts, since admin CRUD
depends on the snapshot abstraction. PR 3 can land in parallel with the
start of Spec 3's frontend work.

## Open follow-ups (not blocking this spec)

- **cubepi upstream PR:** make `Tracer.attach()` and `Meter.attach()`
  detect `FallbackBoundModel` and subscribe to every provider in the
  chain. Until this lands, post-failover chat spans and provider metrics
  are missing.
- **cubepi `on_failover` signature widening:** add `attempt` and
  `chain_length` to the callback so `FailoverEvent` can carry them.
- **Spec 2:** admin CRUD plus the "delete model blocked by referencing
  presets" UX.
- **Spec 3:** workspace API + chat composer UI + failover marker
  rendering.
