# Preset Catalog Redesign — Design

**Date:** 2026-05-22
**Branch:** `feat/preset-catalog-redesign` (off `main`)
**Status:** ready for review

**Related:**
- `docs/dev/specs/2026-05-19-llm-provider-platform-design.md` (§3.6/§3.7 — current catalog)
- `docs/dev/specs/2026-05-18-llm-vendor-compat-design.md` (capability descriptor)
- `docs/dev/specs/2026-05-20-provider-slug-design.md` (provider *instance* slug — distinct from the catalog `preset_key` defined here)

---

## 1. Why

The provider preset catalog today (`cubepi/providers/catalog/data/providers.yaml`)
is a **flat list**: one entry per `(slug, api, base_url)` triple. That shape has
three concrete problems.

### 1.1 The same vendor is shredded into parallel entries

A single vendor that offers more than one wire protocol, more than one region,
or a "coding" plan in addition to its general plan, becomes several unrelated
top-level entries. From the live catalog:

| Vendor | Entries today |
|---|---|
| OpenAI | `openai` (responses), `openai-legacy` (completions), `openai-codex` |
| DeepSeek | `deepseek-anthropic`, `deepseek-openai` |
| Moonshot | `moonshot`, `moonshot-cn`, `moonshot-coding`, `moonshot-coding-cn` |
| Zhipu | `zhipu`, `zhipu-cn`, `zhipu-coding`, `zhipu-coding-cn` |
| MiniMax | `minimax`, `minimax-cn`, `minimax-coding`, `minimax-coding-cn` |
| Aliyun (Qwen) | `qwen-dashscope`, `qwen-dashscope-cn`, `qwen-coding`, `qwen-coding-cn` |
| Volcengine | `doubao-volcengine`, `volcengine-coding` |

Each entry repeats the vendor's `logo`, `short_name`, `description`, and a
near-identical model list. There is no way to express "DeepSeek, but over the
OpenAI wire instead of the Anthropic wire" — they are just two rows that happen
to share a logo. 37 flat entries collapse to ~15 vendors.

### 1.2 No pricing in the catalog

`ModelPreset` carries `context_window`, `max_tokens`, `input_modalities`,
`reasoning` — but **no price**. Pricing exists only in the operator's
`config.yaml` (`models[].cost.{input,output,cache_read,cache_write}`). So the
catalog (the menu the Add-Provider wizard prefills from) can't show or pre-fill
cost; it has to be hand-entered for every model.

### 1.3 `base_url` is hand-written when it's largely derivable

Most base URLs are **`host(region) + path(endpoint)`**, with the pieces repeated
in full on every row:

```
minimax        intl  openai     https://api.minimax.io/v1
minimax-cn     cn    openai     https://api.minimaxi.com/v1
minimax-coding intl  anthropic  https://api.minimax.io/anthropic
```

`host` tracks region; the path tail tracks protocol/plan. The flat file
denormalizes all of it.

---

## 2. Goals / Non-Goals

**Goals**
- Model the catalog as **vendor → region × protocol × plan endpoints → models**,
  eliminating per-row duplication.
- **Compose `base_url`** from `host + path`, where region supplies a default
  host and an endpoint may override host and/or path. Full-URL override remains
  as an escape hatch.
- Carry **pricing on each model** so the wizard can prefill cost.
- **Move the catalog data + loader to cubeplex** (product/business data). Keep
  the **capability descriptor mechanism in cubepi** (protocol runtime contract),
  and **delete the catalog package from cubepi**.
- Clean cutover — no back-compat shim (project hasn't shipped publicly).

**Non-Goals**
- Changing the `CapabilityDescriptor` schema or the cubepi wire runtime.
- Changing the `Provider` / `Model` DB tables or the provider-instance `slug`
  (separate, already-merged feature).
- Changing the `config.yaml` *file format* beyond adding a `preset:` reference
  and making model/base_url/pricing fields optional (inherited from the
  catalog). The catalog is the **menu**; `config.yaml` stays the **deployment
  manifest** but is simplified to reference the menu rather than restate it (§6).

---

## 3. Ownership split: cubepi mechanism vs cubeplex data

cubeplex touches the cubepi catalog at only three sites, and **cubepi never loads
the catalog at runtime** — capability descriptors flow cubeplex→cubepi when a
provider is constructed. So the split is clean:

| Concern | Owner after | Rationale |
|---|---|---|
| `CapabilityDescriptor` type + the wire runtime that applies it (`reasoning`, `temperature`, `max_tokens_field`, …) | **cubepi** | Protocol mechanism. cubeplex imports the type to validate/construct. |
| `WireApi` literal (`anthropic-messages`/`openai-completions`/`openai-responses`) | **cubepi** | Names of protocols cubepi implements. |
| Catalog **data** (vendors, regions, endpoints, models, pricing, the capability *values* per endpoint) | **cubeplex** | Product/business data. |
| Catalog **loader / types** (`ProviderPreset` equivalent, `ModelPreset`, YAML readers) | **cubeplex** | Moves with the data. |

**Decision:** cubepi's `cubepi/providers/catalog/` package (loader + types +
`providers.yaml` + tests) is **deleted**. Nothing outside cubeplex consumes it.
cubeplex keeps its dependency on cubepi for `CapabilityDescriptor` (the stable,
non-catalog `cubepi.providers.capability` module).

**`WireApi` decoupling (settled):** `WireApi` is just the 3-string protocol
literal. To avoid a cubeplex→cubepi-catalog import (the catalog package is being
deleted) and to keep the cubeplex work independent of the cubepi release,
**cubeplex declares its own `WireApi` literal** in `cubeplex/llm/catalog/types.py`.
cubepi keeps its own `WireApi` for its runtime; the two are intentionally
parallel 3-value literals, not a shared import. (cubepi may relocate its
`WireApi` out of the deleted catalog package — see plan Phase G — but cubeplex
does not depend on that.)

---

## 4. New catalog schema

Lives in cubeplex: `cubeplex/llm/catalog/` with `data/vendors.yaml`,
`data/capabilities.yaml`, `types.py`, `loader.py`.

Three nested levels: **Vendor** → **Endpoint** (region × protocol × plan) →
**Model**. Models are a vendor-level pool. Membership has **exactly one
mechanism**: each model carries a `plan` tag (or list of plans), and an endpoint
serves the models whose `plan` intersects the endpoint's `plan`. Endpoints never
list model ids; models never list endpoints/regions/protocols. (Untagged
vendors — §4.2 — every endpoint serves every model.)

```yaml
# cubeplex/llm/catalog/data/vendors.yaml
- vendor: zhipu
  display_name: Zhipu / GLM
  short_name: Zhipu
  logo: zhipu                    # @lobehub/icons id
  category: saas
  description: Zhipu GLM. General + coding plans, CN + intl.

  regions:
    intl: { host: https://api.z.ai }
    cn:   { host: https://open.bigmodel.cn }

  endpoints:
    - { region: intl, protocol: openai-completions, plan: general, path: /api/paas/v4,        capability: openai-compat-basic }
    - { region: intl, protocol: openai-completions, plan: coding,  path: /api/coding/paas/v4, capability: openai-compat-basic }
    - { region: cn,   protocol: openai-completions, plan: general, path: /api/paas/v4,        capability: openai-compat-basic }
    - { region: cn,   protocol: openai-completions, plan: coding,  path: /api/coding/paas/v4, capability: openai-compat-basic }

  models:
    - { model_id: glm-4.6,        display_name: GLM-4.6,        plan: general, context_window: 200000, max_tokens: 8192, input_modalities: [text], reasoning: true, pricing: { input: 0.60, output: 2.20 } }
    - { model_id: glm-4.6-coding, display_name: GLM-4.6 Coding, plan: coding,  context_window: 200000, max_tokens: 8192, input_modalities: [text], reasoning: true, pricing: { input: 0.60, output: 2.20 } }
```

### 4.1 `base_url` composition

```
base_url = (endpoint.host || regions[endpoint.region].host) + (endpoint.path || "")
```

- **region** supplies the default `host` (the part that varies by geography for
  most vendors).
- an **endpoint** may override `host` and/or set `path`.
- **escape hatch:** an endpoint may set a full `base_url:` to bypass composition
  entirely for pathological cases.

This expresses all four real shapes of a coding plan:

```yaml
# A. path differs, same domain (Zhipu, Volcengine) — set path
- { region: cn, protocol: openai-completions, plan: coding, path: /api/coding/paas/v4 }

# B. domain differs (Alibaba coding lives on coding.dashscope.aliyuncs.com) — override host
- vendor: aliyun
  regions: { cn: { host: https://dashscope.aliyuncs.com }, intl: { host: https://dashscope-intl.aliyuncs.com } }
  endpoints:
    - { region: cn, protocol: openai-completions, plan: general, path: /compatible-mode/v1 }
    - { region: cn, protocol: openai-completions, plan: coding, host: https://coding.dashscope.aliyuncs.com, path: /v1 }

# C. identical URL, only the model list differs (Moonshot) — host/path same; plan partitions models
- { region: intl, protocol: openai-completions, plan: general, path: /v1 }
- { region: intl, protocol: openai-completions, plan: coding,  path: /v1 }

# D. fully irregular — explicit override
- { region: intl, protocol: openai-responses, base_url: https://chatgpt.com/backend-api/codex }
```

**Parity guard:** a table-driven test asserts that, for every entry in today's
flat `providers.yaml` (frozen as a snapshot fixture), the new composition
produces the byte-identical `base_url`. This is the regression net for the
rewrite.

### 4.2 The `plan` dimension

`plan` (e.g. `general`, `coding`) is:
- a **display label** in the wizard ("Zhipu · CN · Coding"),
- the **sole model-membership selector** (§4 intro): a model's `plan` (string or
  list) intersected with an endpoint's `plan`, and
- a **`preset_key` disambiguator** (§4.4).

**All-or-nothing per vendor.** A vendor is either *untagged* (no `plan` anywhere
— on any endpoint or model) or *tiered* (every endpoint AND every model carries a
`plan`). Mixing is rejected by the loader. Rationale: a mixed vendor makes "does
this untagged model belong to the coding endpoint?" undefined.

- **Untagged vendor:** at most one endpoint per `(region, protocol)`; every
  endpoint serves every model.
- **Tiered vendor:** `plan` is **required** and the `(region, protocol, plan)`
  tuple must be **unique** across endpoints (this is what keeps the §4.4
  `preset_key` unique). A model may belong to multiple plans via `plan: [a, b]`.

**Loader validations (fail loudly at load):**
- A tiered endpoint whose `plan` matches **no** model → error (dangling
  endpoint).
- A model whose `plan` matches **no** endpoint → error (unreachable model).
- A vendor mixing tagged and untagged endpoints/models → error.

### 4.3 Capability by reference (named profiles)

Most `openai-completions` vendors share an identical capability block. Instead of
inlining it per endpoint, define **named profiles** referenced by name.

```yaml
# cubeplex/llm/catalog/data/capabilities.yaml
openai-compat-basic:                 # covers ~20 vendors (moonshot, minimax, xai, mistral, groq, …)
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

deepseek-anthropic:
  # … vendor-specific reasoning wiring …
```

An endpoint's `capability:` is **either** a profile name (string → looked up in
`capabilities.yaml`) **or** an inline descriptor (dict → constructed directly,
for one-offs). Discrimination is by YAML type: a scalar string is a profile
reference; a mapping is inline. The loader resolves both into a cubepi
`CapabilityDescriptor`. **A string that names no profile in `capabilities.yaml`
fails loudly at load** (not a silent empty descriptor).

### 4.4 `preset_key` (catalog identity)

The loader flattens `vendor × endpoint` into **endpoint presets**, each with a
stable synthesized key:

```
preset_key = vendor / region / protocol [ / plan ]
# e.g.  deepseek/cn/anthropic-messages
#       zhipu/cn/openai-completions/coding   ← plan segment present only when the vendor has plan tiers
```

`preset_key` replaces today's flat `slug` as the catalog identity — the value
`provider.preset_slug` records and the seeder matches on (§6). An endpoint may
set an optional `key:` override for a prettier public id; otherwise the
composed key is used.

**Uniqueness (loader validations, fail loudly at load):**
- No two endpoints may produce the same `preset_key` (whether composed or
  `key:`-overridden). For tiered vendors this is guaranteed by the unique
  `(region, protocol, plan)` rule in §4.2; for untagged vendors by the unique
  `(region, protocol)` rule.
- A `key:` override may not collide with any other endpoint's composed key or
  override across the whole catalog.

---

## 5. Migration / cutover

Clean cutover, no shim. Ordered so each step is independently reviewable.

1. **cubeplex: new catalog package.** Create `cubeplex/llm/catalog/`
   (`types.py`, `loader.py`, `data/vendors.yaml`, `data/capabilities.yaml`).
   Port the loader; import `CapabilityDescriptor`/`WireApi` from cubepi. Unit
   tests: base_url composition (incl. host override + full override), flattening
   to `preset_key`, capability profile resolution, model→plan membership,
   pricing parse.
2. **cubeplex: port the data** to vendors/regions/endpoints/models+pricing +
   `capabilities.yaml`. Add the §4.1 parity test against a frozen snapshot of
   today's flat URLs.
3. **cubeplex: repoint the 3 consumers**:
   - `admin_llm.py:list_provider_presets` → return the **nested vendor list**
     (new shape; §5.1).
   - `admin_providers.py` logo lookup → resolve via vendor.
   - `provider_seeder.py` → resolve the config provider's `preset:` to a catalog
     endpoint and inherit base_url/api/capability/**model pool** with the §6.2
     precedence + §6.3 validation (no longer just a capability backfill).
   - **Rewrite the seed config exhaustively** (`config.yaml` /
     `config.development.local.yaml` / any env seed) to the §6.1 `preset:` +
     `api_key` form, following the **§6.4 inventory** — every existing provider
     is mapped to a `preset:` or deliberately kept custom with a reason. New
     catalog vendor/endpoint/model+pricing entries are added for any seeded model
     not already in the ported catalog.
4. **Frontend wizard (two-step preset selection):**
   - **Step 1 (`PresetPicker`)** lists **vendors** (~15) instead of 37 flat
     presets. `pickPreset` → `pickVendor`.
   - **Step 2 (`ConfigureStep`)** gains **region / protocol / plan** selectors.
     Choosing them selects the endpoint, which drives the composed `base_url`
     and filters the model list. Existing configure fields (name, slug, key)
     stay. This matches the already-multi-step wizard — it only enriches step 2.
   - `@cubeplex/core` preset types update in lockstep.
5. **cubepi: delete `providers/catalog/`** + tests; bump cubepi; switch
   cubeplex's dependency.

### 5.1 API contract change

`GET /api/v1/admin/llm/presets` changes shape, decided here (not left open):
it returns a **nested vendor list**, so the wizard's step 1 lists vendors and
step 2 reads `vendor.endpoints`. Concrete shape:

```jsonc
[
  {
    "vendor": "zhipu", "display_name": "Zhipu / GLM", "short_name": "Zhipu",
    "logo": "zhipu", "category": "saas", "description": "…",
    "endpoints": [
      { "preset_key": "zhipu/cn/openai-completions/coding",
        "region": "cn", "protocol": "openai-completions", "plan": "coding",
        "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",   // already composed server-side
        "model_ids": ["glm-4.6-coding"] }    // loader-DERIVED from plan intersection (§4 intro); not in source YAML
    ],
    "models": [
      { "model_id": "glm-4.6-coding", "display_name": "GLM-4.6 Coding",
        "plan": ["coding"], "context_window": 200000, "max_tokens": 8192,
        "input_modalities": ["text"], "reasoning": true,
        "pricing": { "input": 0.60, "output": 2.20 } }
    ]
  }
]
```

`base_url` is composed server-side (the frontend never composes). The loader
also exposes a flat `preset_key → endpoint` lookup **in-process** for the
seeder and logo paths (not a separate HTTP shape). The `@cubeplex/core` types
mirror this nested structure.

---

## 6. Catalog ⇄ config.yaml: reference, don't restate

- **Catalog** (this doc) = the **menu**: per endpoint it knows `base_url`
  (composed), `api`, `capability`, and the full model list with `pricing`,
  `context_window`, `max_tokens`, `input_modalities`, `reasoning`.
- **`config.yaml` `llm.providers`** = the **deployment manifest**: which
  endpoints this deployment actually turns on, and the secrets to reach them.

Today config restates everything the catalog already knows (base_url, every
model, every cost). The redesign lets config **reference a `preset:` and inherit
the rest**, so a seeded provider shrinks to a `preset` + an `api_key`.

### 6.1 Simplified config shape

```yaml
llm:
  providers:
    alicode:
      preset: aliyun/cn/openai-completions/coding   # → base_url, api, models, pricing, capability
      api_key: ${ALICODE_KEY}                      # secret: always from config, never in catalog
      # models: [qwen3.6-plus]                     # OPTIONAL subset filter; omit = all preset models
```

vs today's ~15-line block per provider (base_url + api + per-model id/name/cost/
context_window/max_tokens). The seeded set (deepseek/arkcode/alicode/sensedeal/…)
is rewritten to this form as part of this change.

### 6.2 Resolution & precedence (seeder)

For each configured provider:

1. If `preset:` is set, resolve it to a catalog endpoint. Pull `base_url`, `api`
   (`provider_type`), `capability` snapshot, and the **candidate model pool**
   (the endpoint's models, each with pricing + window + max_tokens + modalities +
   reasoning).
2. **Model selection:** if config lists `models: [...]` (ids, or id+overrides),
   seed that subset from the pool; **omit `models` → seed all** of the endpoint's
   models.
3. **Field precedence (explicit, per field):**
   - `api_key` — always from config; never in the catalog.
   - `base_url` — config override allowed (self-hosted / proxy). Overriding the
     host does **not** change the protocol, so the inherited `capability` still
     applies.
   - `api` (protocol) and `capability` — **not** independently overridable under
     `preset:`. The protocol is intrinsic to the chosen endpoint, and capability
     is protocol-specific; to use a different protocol, reference a different
     `preset_key`. Setting `api` alongside `preset:` is a **validation error**
     (caught at boot), rather than a silent capability mismatch.
   - per-model `cost`/`pricing` — config override **deep-merges by leaf**: a
     config `cost: { input: 0.5 }` replaces only `input` and inherits the
     catalog's `output`/`cache_*`. (A whole-object replace would silently zero
     the unspecified legs.)
   - any other unset config field inherits from the catalog.
4. **No `preset:`** → behaves like today: config must supply `base_url` and a
   non-empty `models` list (validated, fail loudly); `api` keeps its
   long-standing `openai-completions` default (most custom endpoints are
   OpenAI-compatible). No catalog backfill. (Custom/self-hosted providers.)

This supersedes the old "match by name, backfill only capability" rule — the
catalog now backfills the whole model set, not just the capability descriptor.
`provider.preset_slug` records the resolved `preset_key`.

### 6.3 Validation

Catalog-load validations (§4.2 plan/membership, §4.3 capability profile, §4.4
`preset_key` uniqueness) run first, at import. Then, per config provider:

- A `preset:` that names no catalog endpoint → seed fails loudly (not silent
  skip), so a typo'd `preset_key` is caught at boot.
- A `models:` subset id not present in the endpoint's pool → fail loudly.
- `api` set alongside `preset:` → fail loudly (§6.2.3).
- **No silent custom-downgrade:** see §6.4 — a provider that *used* to receive
  capability backfill must not become an un-backfilled "custom" provider just
  because it was overlooked in the rewrite.

### 6.4 Exhaustive migration inventory (no silent backfill loss)

Under the new rule, a provider with no `preset:` gets no catalog inheritance.
The old rule backfilled capability for any provider whose name matched a flat
slug. So an existing seeded provider that is *omitted* from the rewrite would
**silently** drop from "capability-backfilled" to "custom."

To prevent that, the migration is **exhaustive, not illustrative**:

- Enumerate **every** provider in the seed configs (`config.yaml`,
  `config.development.local.yaml`, and any env-specific seed). The
  deepseek/arkcode/alicode/sensedeal list in §5/§6.1 is illustrative; the plan
  must inventory the full set.
- Each one is **either** given a `preset:` (and a matching catalog endpoint is
  added if missing) **or** explicitly and deliberately left as `preset:`-less
  custom, recorded in the plan with a one-line reason.
- A migration test asserts: for every provider that resolved to a capability
  snapshot under the *old* name-match rule, the *new* config still yields a
  capability snapshot (via `preset:`). This is the regression guard for the P1
  risk.

---

## 7. Testing

- **Composition parity** (§4.1): every current flat URL reproduced byte-for-byte
  by the new composition. Primary regression guard.
- **Loader unit tests:** flattening to `preset_key` (with/without plan segment),
  capability profile resolution (named + inline), model→plan membership, pricing
  parse, host-override and full-`base_url`-override paths.
- **Seeder test:** `preset:`-referenced providers (deepseek/arkcode/alicode/
  sensedeal) seed the right base_url + full model pool + pricing + capability;
  `models:` subset filters correctly; an **allowed** config override (`base_url`,
  per-model `cost`) beats the catalog while `api`/`capability` overrides are
  rejected (§6.2.3); an unknown `preset_key` or unknown subset model id fails
  loudly (§6.3); a
  partial `cost` override deep-merges (inherits the unspecified legs); `api`
  alongside `preset:` is rejected.
- **Backfill-parity test (§6.4):** every provider that got a capability snapshot
  under the old name-match rule still gets one under the new `preset:` config.
- **Catalog-load validation tests (§4.2/§4.3/§4.4):** dangling endpoint,
  unreachable model, mixed tagged/untagged vendor, duplicate `preset_key`,
  unknown capability-profile name — each fails loudly at load.
- **Wizard E2E:** Add-Provider — pick vendor (step 1), choose region/protocol/
  plan (step 2), confirm composed base_url + filtered models (+ prefilled
  pricing).

---

## 8. Decisions (settled)

1. **cubepi catalog removed entirely** — data + loader move to cubeplex; cubepi
   keeps only `CapabilityDescriptor` + `WireApi`.
2. **`plan` dimension** — all-or-nothing per vendor (untagged vs tiered); the
   model `plan` tag is the *sole* membership mechanism; display label +
   `preset_key` disambiguator. coding-plan URL differences handled by host/path
   overrides on the endpoint (§4.1). Loader validates uniqueness + no
   dangling/unreachable (§4.2).
3. **Named capability profiles** in `capabilities.yaml`, referenced by name;
   inline still allowed for one-offs.
4. **`preset_key = vendor/region/protocol[/plan]`**, optional `key:` override.
5. **Two-step preset selection** — vendor in step 1, endpoint (region/protocol/
   plan) selectors added to the existing Configure step.
6. **Seeder match via explicit `config.yaml` `preset:` field** — no name
   heuristic.
7. **Config references the catalog instead of restating it** — a `preset:` +
   `api_key` is enough; base_url/models/pricing/capability inherit. Only
   *allowed* fields override (`base_url`, per-model `cost`); `api`/`capability`
   are fixed by the chosen endpoint (§6.2.3). Optional `models:` subset filter.
   The seeded config is rewritten to this form (§6.4 exhaustive).
