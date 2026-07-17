# LLM Provider Platform — Capability-Driven Providers + Preset Catalog + Test

**Status:** Draft for review (revision 3)
**Author:** xfgong
**Date:** 2026-05-19
**Supersedes:** `2026-05-18-llm-vendor-compat-design.md`
**Revision 3 changes** (vs revision 2):

- **Readiness state moves to the model grain.** Whether a thing is usable
  is split across two layers, mirroring MCP's catalog/install model:
  - *Provider layer* — one cheap liveness/credential check ("can we reach
    this base_url with this key?"). One bad key fails the whole provider;
    no point probing 20 models to learn the key is wrong.
  - *Model layer* — per-model capability probe ("does THIS model exist,
    and do its reasoning / tools toggles actually work on the wire?").
    Stored on the `models` row. This is what the model picker reads.
- This does **not** reopen §4.1's "no per-model capability column"
  decision: that rule bars per-model *capability config* (input); a
  per-model *test status* (observed output) is a different axis.
- §4.4 probe is re-cut into a provider-liveness phase + a per-model
  capability phase. "A wired model is a tested model" now applies at the
  model grain. Save-gating moves with it.
- Added runtime status writeback: a real-call 401 flips the provider to
  `error`; a `model_not_found` flips that one model to `unavailable`.
- Readiness is defined auth-mode-agnostically ("credential valid"), so
  the future OAuth slot (§5 non-goal today) reuses the same state machine.
- §4.8 added: the admin page shows only configured rows; the preset
  catalog appears only in the Add flow. Pure rendering rule, no schema /
  API change (catalog and configured data are already separate endpoints).

**Revision 2 changes** (vs the morning's first cut of this same file):

- Capability config is bound to the **Provider instance** at construction,
  not to individual Model rows. An endpoint typically has one unified
  reasoning / temperature API across all the models it serves; that's the
  fact the new shape names.
- Where one endpoint genuinely serves models with divergent conventions
  (OpenRouter is the realistic case), an optional per-model override map
  on the Provider handles it. Model > Provider fallback.
- Reasoning toggle is **declarative payload data**, not a registry of
  Python callables keyed by `vendor.field`. Three shapes for the value
  side: `binary` (on/off only), `int_budget` (Anthropic-style), `effort`
  (OpenAI Responses-style), `enum` (豆包-style 3-state). The shape lives
  in the capability descriptor.
- Precise merge semantics specified — shallow merge per dict level,
  capability wins on collision at reasoning keys.

The earlier sketch's "thinking_protocol registry of vendor-named Python
functions" is dropped. It baked vendor identity into protocol names,
couldn't be expressed in YAML, and conflated *where the field lives* with
*how levels map to values*.

**Scope** unchanged from the rev-1 framing:

1. Adding a model in the cubeplex UI is a one-form task — pick preset,
   paste API key, click Test, save.
2. cubepi owns vendor knowledge as **data**, not branched code.
3. A wired model is a tested model.

The title-gen 30s incident remains a sub-feature (§4.4 task models), not
the spec's organizing principle.

---

## 1. Why a bigger scope (carry-over)

A narrow `thinking_protocol` column + `title_model` would solve today's
symptom but leave the same problem standing for every next vendor: more
schema, more `extra_body` documentation, no in-UI validation. The product
unit is **Provider Preset** — a declarative bundle of (display name,
wire protocol, base URL, auth mode, capability descriptor, default model
list). cubeplex sees a catalog of presets; picking one fills the form;
capability descriptors travel through DB → cubepi → wire; the Test step
proves the wiring before save.

## 2. Mental model

Three axes — name each slot, keep them in their own home.

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │ wire protocol     "what HTTP shape does this endpoint speak?"      │
 │                   anthropic-messages | openai-completions |        │
 │                   openai-responses                                 │
 │                   → drives WHICH Provider class is instantiated    │
 ├─────────────────────────────────────────────────────────────────────┤
 │ capability        "given a unified intent, how does THIS endpoint  │
 │                    expect it expressed on the wire?"               │
 │                   reasoning, temperature, max_tokens field,        │
 │                   tools/images flags                               │
 │                   → bound to the Provider INSTANCE at construction │
 │                   → with optional per-model overrides              │
 ├─────────────────────────────────────────────────────────────────────┤
 │ task ↔ model     "for THIS task (chat / title / summarize), what  │
 │   matching         model should we actually call?"                 │
 │                   → org settings layer (OrgSettings.task_models)   │
 └─────────────────────────────────────────────────────────────────────┘
```

The whole point of "capability on Provider" is that **after Provider is
constructed, the outward-facing API is uniform**. Callers say
`StreamOptions(thinking="off")`; the Provider translates per its
constructor-time capability. No call site, including cubeplex, ever
branches on vendor identity.

```
caller code   ───── stream(model, msgs, opts=StreamOptions(thinking="off"))
                      │
                      ▼
Provider(capability=…)  ←── constructed once, knows its endpoint's quirks
                      │
                      ├── deep-merge capability.reasoning_off_payload
                      ├── clamp / strip temperature per capability.temperature
                      └── rename max_tokens field per capability.max_tokens_field
                      │
                      ▼
               POST to base_url
```

## 3. cubepi changes

### 3.1 CapabilityDescriptor — the central type

One pydantic model. Lives at `cubepi/providers/capability.py`. Fields cover the
four axes confirmed in scoping (reasoning, temperature, max_tokens field
name, modality / tools flags) and nothing more.

```python
from typing import Literal
from pydantic import BaseModel, Field

class CapabilityDescriptor(BaseModel):
    """Vendor quirks for one endpoint. Bound to a Provider at construction."""

    # ── Reasoning toggle (binary) ─────────────────────────────────────
    # Deep-merged into the request body. Empty {} = endpoint has no
    # explicit off switch; cubepi treats thinking="off" as a no-op.
    reasoning_off_payload: dict = Field(default_factory=dict)
    reasoning_on_payload:  dict = Field(default_factory=dict)

    # ── Reasoning fine-grain (optional) ───────────────────────────────
    # When present, drives ThinkingLevel beyond on/off. When None, levels
    # other than "off" use reasoning_on_payload as-is (effectively binary).
    reasoning_level: "ReasoningLevelSpec | None" = None

    # ── Temperature ───────────────────────────────────────────────────
    temperature: "TemperatureSpec" = Field(default_factory=lambda: TemperatureSpec())

    # ── Wire parameter naming ─────────────────────────────────────────
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"

    # ── Capability flags (hint UI; cubepi doesn't enforce) ────────────
    supports_tools:     bool = True
    supports_images:    bool = False
    supports_streaming: bool = True


class TemperatureSpec(BaseModel):
    mode: Literal["free", "fixed", "ignored"] = "free"
    min: float = 0.0
    max: float = 2.0
    default: float = 1.0
    fixed_value: float | None = None     # mode="fixed"
    # mode="ignored": cubepi strips temperature before sending


class ReasoningLevelSpec(BaseModel):
    """How to express a fine-grain reasoning level on this endpoint."""
    # Where in the payload the level value goes. JSONPath-ish dotted path
    # rooted at the request body. Examples:
    #   "thinking.budget_tokens"          → Anthropic
    #   "reasoning_effort"                → OpenAI Responses
    #   "extra_body.thinking.type"        → Doubao
    path: str

    # Value shape.
    kind: Literal["int_budget", "effort", "enum"]

    # For kind="int_budget":
    level_budgets: dict[str, int] | None = None
    # e.g. {"off": 0, "low": 4000, "medium": 10000, "high": 32000}

    # For kind="effort":
    level_to_effort: dict[str, str] | None = None
    # e.g. {"low": "low", "medium": "medium", "high": "high"}
    # When a level is not in the map (e.g. "minimal"), it's omitted.

    # For kind="enum":
    level_to_enum: dict[str, str] | None = None
    # e.g. {"off": "disabled", "low": "enabled", ..., "auto": "auto"}
```

### 3.2 Per-model overrides (the OpenRouter case)

A single OpenRouter endpoint serves DeepSeek-R1, o3-mini, plain Llama-3,
plain Mistral, etc. Reasoning models accept `reasoning.effort`; plain
models reject the field or silently ignore it. Capability-on-Provider
alone can't express this. Solution: Provider keeps an optional override
map, model entry wins over the Provider-level descriptor.

```python
class OpenAIProvider(BaseProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        capability: CapabilityDescriptor | None = None,
        model_capability_overrides: dict[str, CapabilityDescriptor] | None = None,
        ...
    ):
        self._capability = capability or CapabilityDescriptor()  # legacy no-op
        self._model_overrides = model_capability_overrides or {}

    def _resolve_capability(self, model_id: str) -> CapabilityDescriptor:
        return self._model_overrides.get(model_id, self._capability)
```

For 99% of presets the override map is empty. OpenRouter's preset ships
with entries for known reasoning models; everything else inherits the
"no reasoning off-switch" default.

### 3.3 Merge semantics — spelled out

Three concrete rules so this never ambiguates in implementation or test:

1. **Shallow merge at each dict level.** Recurse into nested dicts.
   Do NOT recurse into lists — when both sides have a list at the same
   key, capability wins (replaces, not unions). Arrays are atomic
   values.
2. **Capability wins on collision at reasoning keys.** When the caller
   passes their own `extra_body` and the capability's
   `reasoning_off_payload` writes into `extra_body`, capability's keys
   overwrite the caller's at the leaf. Caller's other (non-overlapping)
   keys are preserved.
3. **Absence is not falsity.** If a vendor expects "reasoning off" to
   be expressed as field-absent rather than `field=false`, encode it
   that way in the descriptor:
   ```python
   reasoning_off_payload = {}             # don't write the field
   reasoning_on_payload  = {"extra_body": {"reasoning": {"effort": "low"}}}
   ```
   This is intentionally the default — an empty `reasoning_off_payload`
   means "to disable reasoning, don't say anything special." Vendors
   that need an explicit `false` (Qwen) declare it.

Implementation: a single function `merge_capability_payload(kwargs:
dict, patch: dict) -> None` in `cubepi/providers/capability.py`, with focused
unit tests on each rule.

### 3.4 Provider runtime flow

All three Provider classes (`OpenAIProvider`,
`OpenAIResponsesProvider`, `AnthropicProvider`) get the same internal
sequence after the existing message/tool conversion:

```python
async def stream(self, model, messages, *, system_prompt="", tools=None,
                 options=None):
    opts = options or StreamOptions()
    cap = self._resolve_capability(model.id)
    kwargs = self._build_base_kwargs(model, messages, system_prompt, tools)

    # 1. Temperature constraint
    _apply_temperature(kwargs, cap.temperature)

    # 2. max_tokens field rename (no-op for Anthropic which uses its own field)
    if cap.max_tokens_field != "max_tokens" and "max_tokens" in kwargs:
        kwargs[cap.max_tokens_field] = kwargs.pop("max_tokens")

    # 3. Reasoning
    if opts.thinking == "off":
        merge_capability_payload(kwargs, cap.reasoning_off_payload)
    else:
        merge_capability_payload(kwargs, cap.reasoning_on_payload)
        if cap.reasoning_level is not None:
            _write_reasoning_level(kwargs, cap.reasoning_level, opts.thinking)

    # ... continue with existing flow
```

`_write_reasoning_level` walks `path` to the target dict, sets the
value per `kind` and the level map. Missing level → no write (the
value falls back to whatever `reasoning_on_payload` set).

For **Anthropic**: its existing typed path (`thinking={"type":
"enabled", "budget_tokens": N}`) is replaced by capability lookup. The
preset for Anthropic ships:

```python
CapabilityDescriptor(
    reasoning_off_payload={"thinking": {"type": "disabled"}},
    reasoning_on_payload={"thinking": {"type": "enabled"}},
    reasoning_level=ReasoningLevelSpec(
        path="thinking.budget_tokens",
        kind="int_budget",
        # Mirror cubepi.providers.base.ThinkingBudgets defaults so that
        # capability=None on AnthropicProvider reproduces today's wire
        # budgets exactly. xhigh = high preserves the existing
        # clamp_thinking_level() behavior. ThinkingLevel literal has no
        # "max" value — don't add a key that can never trigger.
        level_budgets={"off": 0, "minimal": 1024, "low": 2048,
                       "medium": 8192, "high": 16384, "xhigh": 16384},
    ),
)
```

For **OpenAI Responses**: existing `reasoning_effort` logic likewise
moves into capability. Preset:

```python
CapabilityDescriptor(
    reasoning_off_payload={},                # field absent
    reasoning_on_payload={},
    reasoning_level=ReasoningLevelSpec(
        path="reasoning_effort",
        kind="effort",
        level_to_effort={"minimal": "minimal", "low": "low",
                         "medium": "medium", "high": "high",
                         "xhigh": "high"},  # ThinkingLevel has no "max"
    ),
)
```

The three Provider classes **stay separate**. Wire-shape differences
(SSE schemas, error envelopes, token usage fields, tool-call formats)
are large enough that merging them into one class with a `wire:` switch
would just hide branching inside the class.

### 3.5 Constructor signature — backward compat

```python
class OpenAIProvider(BaseProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        capability: CapabilityDescriptor | None = None,
        model_capability_overrides: dict[str, CapabilityDescriptor] | None = None,
        # ... existing kwargs (extra_body, extra_headers, payload_quirks, etc.)
    ):
```

`capability=None` → constructor instantiates `CapabilityDescriptor()`
with all fields at default. The defaults are an **empty
reasoning_off/on_payload, free 0–2 temperature, max_tokens field, all
flags True**. Result: behavior identical to today for any existing
caller that doesn't pass `capability`.

**Important: "active capability" gating for OpenAI-completions and
OpenAI Responses.** Today these classes do **not** inject
`temperature` / `max_tokens` into the base kwargs — they leave them
absent and rely on the SDK / caller. Naively running every capability
hook unconditionally would change wire bytes for every existing
caller. So both classes track whether the caller explicitly passed
`capability` (or `model_capability_overrides`):

```python
self._cap_active = capability is not None or model_capability_overrides is not None
self._capability = capability or CapabilityDescriptor()
```

Inside `stream()`, capability application is gated on `self._cap_active`.
When inactive, the class behaves exactly as today: no
`temperature`/`max_tokens` injection, no reasoning payload merge, no
max_tokens-field rename. When active, the descriptor drives every
field.

`AnthropicProvider` is different — today it already injects
`temperature` and writes a thinking block, so its
`capability=None` path instantiates a non-empty
`_ANTHROPIC_DEFAULT_CAPABILITY` that mirrors today's behavior
verbatim. No `_cap_active` flag is needed there.

`_payload_quirks` (the existing string-set for cubepi's older
`"max_completion_tokens_alias"` hack) is removed in this milestone —
replaced by `capability.max_tokens_field`.

### 3.6 Preset catalog

New module `cubepi.providers.catalog`. Bundled YAML at
`cubepi/providers/catalog/data/providers.yaml` containing the §3.7 preset list.
Public API:

```python
def list_provider_presets() -> list[ProviderPreset]: ...
def get_provider_preset(slug: str) -> ProviderPreset: ...
```

```python
class ProviderPreset(BaseModel):
    slug: str                       # "qwen-dashscope"
    display_name: str               # "通义千问 (DashScope)"
    short_name: str
    category: Literal["saas", "oss-framework", "custom"]
    description: str
    # Logo identifier from @lobehub/icons (200+ AI/LLM brand SVGs, CC0
    # license, used by the cubeplex frontend). The catalog only carries
    # the lookup key (lowercase id like "anthropic", "openai",
    # "deepseek"); the rendering side belongs to cubeplex UI, which
    # imports @lobehub/icons and renders <ProviderIcon
    # provider={preset.logo} size={28} type="color" />. None = render
    # a generic fallback (custom-* presets, OSS frameworks without a
    # listed brand).
    logo: str | None = None

    api: WireApi                    # → drives which Provider class
    base_url: str
    auth: AuthSpec

    capability: CapabilityDescriptor                          # default for all models
    model_capability_overrides: dict[str, CapabilityDescriptor] = {}

    default_models: list[ModelPreset]


class ModelPreset(BaseModel):
    model_id: str
    display_name: str
    context_window: int
    max_tokens: int
    input_modalities: list[str]
    reasoning: bool = False
```

The preset is the **single source of truth** the admin UI pulls from.
cubeplex stores `preset_slug` on the provider row; the descriptor itself
is cached in the row (so cubeplex doesn't depend on a particular cubepi
version being installed to render the form), then refreshable from the
catalog on demand.

### 3.7 Initial catalog

`logo` column is the `@lobehub/icons` provider id (verify each against
the live catalog at https://lobehub.com/icons when populating the
YAML; some IDs may differ from the slug — e.g. Qwen on dashscope
likely maps to `qwen` rather than `dashscope`).

| slug | api | logo | reasoning shape |
|---|---|---|---|
| `anthropic` | anthropic-messages | `anthropic` | binary on/off + int_budget |
| `openai` | openai-responses | `openai` | effort field |
| `openai-legacy` | openai-completions | `openai` | none (older GPT-4/3.5) |
| `qwen-dashscope` | openai-completions | `qwen` | binary `extra_body.enable_thinking` |
| `doubao-volcengine` | openai-completions | `doubao` | enum `extra_body.thinking.type` |
| `deepseek-anthropic` | anthropic-messages | `deepseek` | binary + budget (mirrors Anthropic) |
| `deepseek-openai` | openai-completions | `deepseek` | binary via `extra_body.reasoning` |
| `moonshot` | openai-completions | `moonshot` | none today |
| `xai` | openai-completions | `xai` | binary today |
| `mistral` | openai-completions | `mistral` | none |
| `openrouter` | openai-completions | `openrouter` | binary off via `reasoning.exclude` + effort field; **per-model overrides for non-reasoning models** |
| `together-ai` | openai-completions | `together` | none |
| `groq` | openai-completions | `groq` | none |
| `fireworks` | openai-completions | `fireworks` | none |
| `vllm` | openai-completions | `vllm` | per-deployment; default = none, admin overrides |
| `ollama` | openai-completions | `ollama` | none; auth=none |
| `lm-studio` | openai-completions | `lmstudio` | none; auth=none |
| `tgi` | openai-completions | `huggingface` | none |
| `custom-openai` | openai-completions | `null` | empty descriptor; admin fills |
| `custom-anthropic` | anthropic-messages | `null` | empty descriptor; admin fills |

OpenRouter's preset entry is the one with a non-trivial
`model_capability_overrides`: keys for DeepSeek-R1, o1-mini, o3,
qwen-plus, etc. carrying their respective reasoning shape; non-listed
models inherit the "no reasoning off-switch" provider default.

## 4. cubeplex changes

### 4.1 Schema

```sql
ALTER TABLE providers ADD COLUMN preset_slug VARCHAR(64) NULL;
ALTER TABLE providers ADD COLUMN capability  JSON NULL;        -- CapabilityDescriptor as JSON
ALTER TABLE providers ADD COLUMN model_capability_overrides JSON NULL;  -- dict[model_id, CapabilityDescriptor]
-- Provider-level test = liveness/credential ONLY ("can we reach base_url
-- with this key?"). Not the per-model capability result — that lives on
-- the models row below.
ALTER TABLE providers ADD COLUMN last_liveness_at      TIMESTAMP NULL;
ALTER TABLE providers ADD COLUMN last_liveness_status  VARCHAR(16) NULL;  -- "ok" | "fail"
ALTER TABLE providers ADD COLUMN last_liveness_summary JSON NULL;

-- Per-model test = the capability probe (reasoning / tools / temperature /
-- streaming) plus model existence. This is the axis the model picker reads.
ALTER TABLE models ADD COLUMN last_test_at      TIMESTAMP NULL;
ALTER TABLE models ADD COLUMN last_test_status  VARCHAR(16) NULL;  -- "ok" | "warn" | "fail" | "unavailable"
ALTER TABLE models ADD COLUMN last_test_summary JSON NULL;          -- ProbeResult JSON
```

**Two status layers, not one.** Usability is decided across both, the
same way MCP splits `authed` (install-level) from the per-tool contents
of `tools_cache`:

| Layer | Answers | Source | Failure blast radius |
|---|---|---|---|
| Provider `last_liveness_*` | "base_url + key reachable?" | one cheap liveness call; runtime 401 writeback | all models under it |
| Model `last_test_*` | "does this model exist + do its toggles work on the wire?" | per-model capability probe; runtime `model_not_found` writeback | just that model |

A **model is usable** ⟺ its provider's `last_liveness_status = "ok"` AND
its own `last_test_status ∈ {ok, warn}`. Derived readiness states the UI
renders (no separate stored enum needed — compute from the two layers):

| readiness | condition | picker behavior |
|---|---|---|
| `ready` | provider liveness ok + model test ok | selectable |
| `degraded` | model test = warn (an advisory probe failed, e.g. tools) | selectable, yellow dot + hint |
| `provider_error` | provider liveness fail (key/url) | disabled — "Provider unreachable; fix credentials / re-test" |
| `model_error` | provider ok but model test fail | disabled — "Model test failing; re-test" |
| `unavailable` | model test = unavailable (vendor returned model_not_found) | disabled — "Model not offered by this provider" |
| `stale` | capability edited since last model test | selectable, hint "config changed; re-test recommended" |

`models.capability` (a per-model *capability config* column) is still
intentionally **not** added (rationale below). The `last_test_*` columns
above are the *observed result* axis — orthogonal to capability config,
so adding them does not reopen that decision.

`providers.provider_type` (existing) — its values become wire api
directly (`openai-completions` / `anthropic-messages` /
`openai-responses`). Migration backfills the current `openai_compat`
default to `openai-completions`.

Rationale for no `models.capability` config column: capability is a
Provider-level property by design; the OpenRouter-style "different models
on same endpoint" case is handled by `providers.model_capability_overrides`
(an editable JSON column). Reserving a separate `models.capability` row
column would invite the wrong mental model (admins editing model-level
capability per-model on a Qwen provider where it's wrong). The per-model
`last_test_*` columns added above are unaffected by this — they record
what the probe *observed*, not capability the admin *configures*.

### 4.2 LLMFactory

`build_cubepi_provider(provider_config)`:

1. Load the JSON `capability` from the provider row → pydantic
   `CapabilityDescriptor`.
2. Load `model_capability_overrides` (also JSON dict → typed).
3. Pass both as kwargs to the appropriate cubepi Provider class
   (selected by `provider_type` → wire api).

The factory no longer needs `_provider_type_to_api` as a separate
mapping table — the column value IS the wire api.

### 4.3 Admin endpoints

```
GET  /api/v1/admin/llm/presets                       → ProviderPreset[]
POST /api/v1/admin/providers/liveness                → LivenessResult (pre-save; base_url+key only)
POST /api/v1/admin/providers/{id}/liveness           → LivenessResult (re-check saved provider)
POST /api/v1/admin/providers/test                    → ProbeResult (pre-save: liveness + probe one model)
POST /api/v1/admin/providers/{id}/models/{mid}/test  → ProbeResult (re-test one saved model)
POST /api/v1/admin/providers/{id}/test               → ProbeResult[] (re-test all enabled models)
GET  /api/v1/admin/providers/{id}                    → row + capability + liveness + per-model status
POST /api/v1/admin/providers                         → create
PUT  /api/v1/admin/providers/{id}                    → update
```

The pre-save `POST /providers/test` carries a `model_id` in its body —
the wizard probes the model the admin is about to import (step 4's first
pick). It runs liveness then the capability probe against that one model
and returns a single `ProbeResult`. Saved-provider re-tests fan out per
model and persist each model's `last_test_*`; liveness persists on the
provider row.

`ProbeResult` shape:

```python
class ProbeStep(BaseModel):
    name: Literal["liveness", "reasoning", "temperature", "tools", "streaming"]
    status: Literal["pass", "fail", "skip", "warn"]
    latency_ms: int | None = None
    detail: str
    error: ProbeError | None = None

class ProbeResult(BaseModel):
    overall: Literal["pass", "fail", "warn"]
    blocking_failed: bool       # save is forbidden when True
    steps: list[ProbeStep]
```

### 4.4 Test probe sequence

Two phases. **Phase A (provider liveness)** runs once and gates everything.
**Phase B (per-model capability)** runs against a specific model and is
what writes the model's `last_test_*`.

Within phase B, steps 2 blocks the model's save on failure; steps 3–5 are
advisory (warn, don't block).

**Phase A — provider liveness** (writes `providers.last_liveness_*`):

1. **Liveness** — minimal completion against any one model:
   `max_tokens=1`, prompt `"."` (or `"ping"`). 5s timeout. Fail = wrong
   base URL, bad API key, network issue. On fail, phase B is skipped
   entirely — every model under this provider is `provider_error`. This
   is the cheap gate that answers Open Q#3's probe-cost worry: a wrong
   key is caught in one call, not N.

**Phase B — per-model capability** (writes `models.last_test_*` for the
probed model). Runs only after phase A passes:
2. **Reasoning toggle** — runs only if `capability.reasoning_off_payload`
   or `reasoning_on_payload` is non-empty.
   - Send a probe with `StreamOptions(thinking="off")` and a prompt
     "Reply OK." Verify: no 4xx, completion succeeds.
   - Send same prompt with `StreamOptions(thinking="medium")`. Verify
     completion. If the picked probe model has `reasoning=true`, also
     verify the off-run produced no thinking deltas and the on-run did.
   Fail = capability descriptor is rejected by endpoint (wrong field
   name or shape). 15s each.
3. **Temperature** — runs only if `capability.temperature.mode !=
   "ignored"`. Send a probe at `temperature.default` (or `fixed_value`).
   Verify accepted (no 400 mentioning temperature). For mode="fixed",
   send a second probe at a *different* temperature value and verify
   accepted-or-silently-ignored.
4. **Tools** — runs only if `capability.supports_tools=True`. Send a
   one-tool definition with an instruction to call it ("Use the
   `echo` tool with arg 'hi'"). Verify tool_call emitted. On fail, warn
   "endpoint did not emit tool call; consider unchecking
   supports_tools."
5. **Streaming** — verify at least one SSE chunk arrived during step 1
   or 2 with `stream=True`. Warn (don't fail) on no chunks.

Steps 2–5 run in parallel after step 1 passes. Persist `last_liveness_*`
on the provider row (once) and `last_test_*` (`ok` / `warn` / `fail`) +
`last_test_summary` on each probed **model** row, so the model picker and
provider detail can show status dots without re-probing.

**Save-gating at the model grain.** "A wired model is a tested model"
means: a model row can only be saved as `enabled=true` if phase A passed
*and* its own step 2 passed. A provider can be saved with liveness-ok and
zero tested models; each model is then enabled as it passes its own probe.
This replaces rev-2's provider-grain "steps 1–2 block save."

### 4.4a Runtime status writeback

Tests are point-in-time; keys get revoked and models get retired between
tests. Mirror MCP's "refresh failure flips `authed=false`" so the UI
reflects reality without a manual re-test:

- A real agent call that returns **401 / 403 (auth)** flips
  `providers.last_liveness_status = "fail"`. All models under it render
  `provider_error` until the next successful call or re-check.
- A real call that returns **model-not-found** (vendor's
  `model_not_found` / 404-on-model) flips that one model's
  `last_test_status = "unavailable"`; siblings are untouched.
- A subsequent successful call clears the flag back to `ok` (liveness) /
  leaves the model for the next explicit probe to re-validate.

Writeback is best-effort and out-of-band of the user's stream — never
block or fail a live request on the status update.

### 4.5 Add Provider UI (4-step wizard)

(Unchanged in shape from rev-1's §4.5 — just rebound to read/write
`capability` JSON instead of scattered fields.)

**Icons.** The wizard's preset list, the Provider list page, and the
ProviderDetail header render each preset's brand mark via
`@lobehub/icons`. `pnpm add @lobehub/icons` in the web package.
Render with `<ProviderIcon provider={preset.logo} size={28}
type="color" />`. When `preset.logo` is null, fall back to a generic
gear/wrench icon. The icon library is CC0; no NOTICE shim needed.

Step 1: pick from preset catalog.
Step 2: configure — preset auto-fills `display_name`, `base_url`, all
capability fields. "Advanced" expander reveals the capability editor
(JSON view + per-field form). API key is the only required free input.
Step 3: Default models — import from preset (checkbox list) or add
custom. (Moved ahead of Test in rev-3: the per-model probe needs a model
to target.)
Step 4: Test connection — runs phase A (liveness) once, then phase B
against each selected model, streaming results per model. Can't finish
the wizard until liveness passes and at least one model's step 2
(reasoning) passes. Models that fail their probe are saved but left
`enabled=false` with their `last_test_status`; the admin can re-test
later from provider detail.

For custom presets (`custom-openai`, `custom-anthropic`), step 2's
advanced fields are visible by default. Reasoning toggle is filled by
clicking "Use a preset template" (popover lists Qwen / Doubao / OpenAI
effort / Anthropic budget templates that copy their respective
`capability.reasoning_*` blocks into the form).

### 4.6 Task model routing (absorbs the title-gen incident)

`OrgSettings(key="task_models", value={"chat": "...", "title": "...",
"summarize": "..."})`. `LLMFactory.resolve_task_model(task: str)` walks
this → yaml fallback → default. Title gen switches to
`resolve_task_model("title")`. UI section under admin settings is a
dropdown per task; options are filtered/annotated by readiness per §4.7
(unusable models disabled with reason). Default is "Use chat model."

This independently fixes the 30s title timeout for admins who route
title to a small non-reasoning model; for everyone else it preserves
today's behavior.

### 4.7 Readiness in model-selection UIs

Every surface that lets someone pick a model — the task-routing dropdowns
(§4.6), the Add-Provider wizard's model list (§4.5), and any end-user chat
model selector — reads the **derived readiness** computed from the two
status layers in §4.1. The rule is uniform:

- `ready` / `degraded` / `stale` → selectable (the latter two carry a
  dot + tooltip).
- `provider_error` / `model_error` / `unavailable` → shown **disabled,
  not hidden**, each with a one-line reason and the matching fix
  affordance (re-test model, fix credentials, or "not offered"). This is
  the LLM parallel to MCP's "Reconnect" — surface the broken thing with
  the path to fix it rather than silently dropping it.

Readiness is computed server-side and returned alongside each model in
the list/picker payloads, so the frontend never re-derives the rule.

### 4.8 Page shows configured rows only; catalog lives in the Add flow

The Providers / Models admin page renders **only configured rows** —
`GET /admin/providers` (DB rows) plus each provider's `models` rows. The
preset catalog (presets and their `default_models`) is **never** pre-
rendered on this page; it appears **only** inside the Add-Provider /
Add-Model flow, sourced from `GET /admin/llm/presets`. No preset
materializes a row until the admin imports it. This needs no schema or
API change — catalog and configured data are already served by different
endpoints; it is purely a rendering rule.

Do not conflate this with §4.7's "disabled, not hidden":

- A **configured-but-broken** model (`provider_error` / `model_error` /
  `unavailable`) is a real `models` row → stays on the page, disabled,
  with a reason and fix affordance.
- An **un-added preset model** is catalog template data → does **not**
  appear on the page at all; it only shows as an importable option inside
  the Add flow.

The smell to avoid: rendering a preset's `default_models` greyed-out on
the main page as an "available to add" list. Available-to-add belongs in
the wizard, not the page.

## 5. Non-goals

- **No auto-discovery of vendor by base_url.** Admin picks a preset.
- **No per-org preset extension.** Customization is via the "custom"
  presets + capability JSON. Internal-gateway presets get PR'd to
  cubepi.
- **No models.capability column** (see §4.1 rationale).
- **No general-purpose `extra_body` rules engine.** Capability is the
  four-field shape, period.
- **No OAuth provider auth implementation** in this round (slot
  reserved in `AuthSpec`). The readiness machine (§4.1) is written
  auth-mode-agnostically — provider liveness means "credential valid,"
  not "API key present" — so when OAuth lands, an expired/revoked grant
  flips `last_liveness_status` exactly like a bad key does, with no
  state-model change.
- **No org/workspace/user scope for providers** — providers stay
  org/admin-level; the MCP-style multi-scope install matrix is
  explicitly out of scope.
- **No streaming-format quirks** in capability — stays inside the
  Provider classes' wire handling.

## 6. Rollout — milestones

### M1 — cubepi: capability core (cubepi repo)

- `cubepi/providers/capability.py`: `CapabilityDescriptor`, `TemperatureSpec`,
  `ReasoningLevelSpec`, `merge_capability_payload`.
- All three Provider classes accept `capability=None` and
  `model_capability_overrides=None` kwargs. Default = no-op.
- `_payload_quirks` retired; replaced by `capability.max_tokens_field`.
- Existing thinking handling in Anthropic / OpenAIResponses migrated
  to read from capability (so they share one code path).
- Tests: each merge rule, each level shape, every existing Provider
  test still passes with no capability passed.

No behavior change for existing callers.

### M2 — cubepi: preset catalog (cubepi repo)

- `cubepi/providers/catalog/data/providers.yaml` with §3.7 list.
- `list_provider_presets()`, `get_provider_preset(slug)`.
- Catalog round-trip tested: every preset parses, every wire api is
  one of the three.

### M3 — cubeplex: schema + factory + read-only catalog endpoints

- Alembic: provider columns + provider `last_liveness_*` + model
  `last_test_*` per §4.1.
- `LLMFactory` reads capability + overrides from row; passes to
  cubepi.
- `GET /admin/llm/presets`, `GET /admin/providers/{id}` (returns
  liveness + per-model status).
- Server-side derived-readiness helper (the §4.1 table) reused by every
  picker payload.
- Seed migration: existing system providers backfilled with their
  matching preset's capability where slug matches.

### M4 — cubeplex: test endpoint + runtime writeback

- Probe runner per §4.4: phase A (liveness) + phase B (per-model).
- `POST /admin/providers/liveness` + `/{id}/liveness`,
  `POST /admin/providers/test` (pre-save, one model),
  `POST /admin/providers/{id}/models/{mid}/test`,
  `POST /admin/providers/{id}/test` (all enabled models).
- `last_liveness_*` persisted on provider; `last_test_*` on each model.
- Runtime writeback (§4.4a): 401/403 → provider liveness fail;
  model_not_found → model unavailable. Best-effort, out-of-band.

### M5 — cubeplex: Add Provider wizard UI

- Four-step flow per §4.5.
- Test results streamed back as the probe runs (SSE).

### M6 — cubeplex: task model routing + title-gen switch

- `OrgSettings.task_models` schema.
- `resolve_task_model`.
- Title-gen service swap.
- Admin Settings → Task routing UI.

### M7 — Polish

- Provider detail: provider liveness dot + a per-model status list
  (dots from each model's `last_test_status`, reason tooltips per §4.7).
- Re-test buttons at both grains (provider liveness; single model).
- i18n sweep.

## 7. Open questions

1. **Where is the canonical CapabilityDescriptor serialization?**
   pydantic's `.model_dump()` produces a dict that can be stored in
   the JSON column straight through. Question: do we want a stable,
   versioned JSON shape? If we add fields in cubepi later, old rows
   with the older shape should keep loading. pydantic's default
   permissive parsing covers additive change; we should write a
   regression test that pins this.
2. **Catalog version pinning.** A cubeplex install that has a preset
   slug in DB that's been removed from a newer cubepi catalog must
   still load the provider. Solution: cubeplex caches the full
   capability on the row at creation time. cubepi version skew never
   breaks loading; admin can re-pull preset on demand to update.
3. **Probe-cost surface.** Each capability probe is 1–3 LLM calls. For
   pay-per-token vendors that adds up if admins click Test often.
   Mitigated by the rev-3 two-phase split: a bad key is caught by one
   liveness call (phase A) instead of one probe per model, and re-tests
   target a single model rather than the whole provider. Still provide a
   "skip optional probes" checkbox on re-test (default ON for re-test;
   OFF for first-save).
4. **OpenRouter override editability.** When `preset_slug=openrouter`,
   the override map is non-trivial. Edit UI: a tab per overridden
   model in the wizard's "Advanced" expander, or a separate "Model
   overrides" sub-screen on provider detail. Lean toward the
   sub-screen — keeps the wizard short.

5. **Provider logos** — resolved. `ProviderPreset.logo` carries an
   `@lobehub/icons` provider id (e.g. `anthropic`, `openai`,
   `deepseek`). cubepi ships only the string; no SVG assets in cubepi.
   The cubeplex frontend `pnpm add @lobehub/icons` and renders via
   `<ProviderIcon provider={preset.logo} size={28} type="color" />`.
   When `preset.logo` is null (custom-* presets), the UI shows a
   generic fallback icon. Verify each preset's `logo` id against the
   live catalog at https://lobehub.com/icons when authoring the YAML;
   some IDs may not match the obvious vendor name (e.g. Qwen-on-
   dashscope likely uses the `qwen` icon, not `dashscope` or
   `alibabacloud`). License: lobehub icons are CC0, no
   trademark/license shim needed in cubepi.
