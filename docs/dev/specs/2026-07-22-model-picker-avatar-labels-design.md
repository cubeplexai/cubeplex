# Model picker: model-brand logo + tier + hover details

## Goal

Make the chat composer **model picker** scannable:

1. Leading **model-family brand logo** (inferred from `model_id` / display
   name), not the gateway **provider** logo.
2. List label = **tier name** (or custom preset key) only.
3. **No description** in the list row.
4. On **hover**, a Tooltip with model details (provider, model-id, context,
   reasoning, modalities, description, etc.).

No logo upload. No provider avatar work. Unknown models get a default model
icon.

## Context

### Composer UI today

`frontend/packages/web/components/chat/ModelPicker.tsx`:

| Surface | Today |
| --- | --- |
| Closed trigger | Generic `Cpu` + preset display name (tier i18n or custom key) + thinking effort |
| Open list row | Checkmark + **mono full `primary`** + preset name + optional description + default badge |

`primary` is the org preset chain head (`slug/model_id`). Long custom slugs
and model IDs dominate the `w-72` popover. Description adds vertical noise.

### Workspace API today

`WorkspacePresetSummary` (`backend/cubeplex/api/schemas/model_presets.py`):

```text
key, kind, primary, description, is_default
```

No model metadata for tooltips. `primary` alone cannot supply
`context_window`, `reasoning`, or `input_modalities`.

### What already exists

| Asset | Where | Use in this feature |
| --- | --- | --- |
| Model config in snapshot | `ProviderConfig.models[]` (`id`, `name`, `contextWindow`, `reasoning`, `input`, …) | Hover fields after resolving `primary` |
| Brand icons | `ProviderLogo` + `@lobehub/icons` map (`anthropic`, `openai`, `qwen`, …) | Reuse as **model-family** glyphs via shared helper |
| Catalog vendors | `llm/catalog/data/vendors.yaml` | Pattern reference only; matching is heuristic on model id/name |
| Provider `logo_url` / upload | Admin provider form | **Out of scope** — not used for picker identity |

### Product revision (vs earlier draft)

Earlier draft (#402 rev1) focused on **provider** logo + API logo fields +
optional upload. That is **superseded**:

- Gateway provider logo is low-signal (vLLM / OpenRouter / custom reverse
  proxies hide the real model family).
- Asking operators to upload logos is friction without proportional UX win.
- Users care whether the model is Claude / GPT / Qwen, not which proxy slug
  routes it.

## Approaches considered

| Option | Notes |
| --- | --- |
| **A. Model-family brand from model_id + tier label + hover details** | Recommended. Frontend brand inference; API only adds model detail fields. |
| B. Provider logo from DB | Weak for proxies; needs logo_url / upload. Rejected. |
| C. Truncate mono `primary` only | No brand, still noisy. Rejected. |
| D. Group list by provider | Heavier; defer. |

**Chosen: A.**

## Design

### List and trigger (UI)

**List row**

- Leading: model-family logo (`sm`), from `inferModelBrand(model_id, model_display_name)`.
- Title: **tier / custom label only**
  - `kind === "tier"` → existing i18n `adminPresets.modelTiers.<key>.name`
  - `kind === "custom"` → `p.key` (custom preset label/key)
- **Do not** render mono `primary` as a visible line.
- **Do not** render description under the row.
- Default badge retained when `is_default`.
- Checkmark retained for active state.
- Row is the Tooltip trigger (see below).

**Closed trigger**

- Replace generic `Cpu` with selected preset’s model-family logo (same
  inference). Fallback default model icon if brand unknown or no selection.
- Keep: short tier/custom name · thinking effort · chevron.
- Trigger accessible name includes selected label and full `primary` when a
  preset is selected.

### Brand inference (model family, not provider)

Pure client (and optionally a tiny shared pure function under web `lib/`):

```text
inferModelBrand(modelId: string, displayName?: string | null): string | null
```

- Input: `model_id` portion of `primary` (after first `/`), plus optional
  `model_display_name`.
- Output: brand id already known to the icon map (`anthropic`, `openai`,
  `qwen`, `moonshot`, `zhipu`, `doubao`, `deepseek`, `minimax`, `mistral`,
  `xai`, …) or `null`.
- **Do not** use provider slug for brand (openrouter / vllm / ollama would
  mis-label).

Suggested match order (case-insensitive; first hit wins; refine in impl):

| Patterns (illustrative) | Brand id |
| --- | --- |
| `claude*` | anthropic |
| `gpt-*`, `o1*`, `o3*`, `o4*`, `chatgpt*` | openai |
| `qwen*` | qwen |
| `kimi*` | moonshot |
| `glm*` | zhipu |
| `doubao*`, `seed-*` (when clearly Doubao) | doubao |
| `deepseek*` | deepseek |
| `minimax*`, `MiniMax*` | minimax |
| `mistral*`, `mixtral*`, `codestral*` | mistral |
| `grok*` | xai |
| else | `null` → default model icon |

Optional later: exact match against catalog model ids. Not required for MVP.

**Default icon:** reuse `Cpu` (or a single shared “generic model” glyph) —
never an empty broken image, never provider letter monogram for this surface.

**Component reuse:** extract brand icon rendering from admin
`ProviderLogo` into a neutral path (e.g. `components/models/ModelBrandLogo.tsx`
or shared `BrandLogo`) that accepts `brand: string | null` and falls back to
default icon. Admin provider UI may keep letter/`logo_url` behavior; chat
picker does **not** need `logo_url`.

### Hover details (Tooltip)

Use existing `components/ui/tooltip` (not native `title` alone — content is
multi-field).

**Fields (product-locked):**

| Field | Source |
| --- | --- |
| Provider | `provider_slug` (and optional display name if we add it later; slug is enough for MVP) |
| Model ID | `model_id` |
| Display name | `model_display_name` when present |
| Context | `context_window` (format e.g. `128k` / raw int — pick one consistent formatter) |
| Reasoning | `reasoning` boolean |
| Input modalities | `input_modalities` (e.g. `text`, `image`) |
| Description | preset `description`; for tiers, existing i18n tier description by key |

**Not in tooltip:** full provider `capability` blob, cost, failover chain,
max_tokens (unless free to include later — not required).

Layout: compact definition list / stacked labeled rows; max width so the
popover does not fight the picker. Delay: default Tooltip provider delay is
fine (~400ms).

A11y: tooltip content should be reachable for keyboard focus per existing
Tooltip patterns; row/trigger `aria-label` still includes display label +
full `primary`.

### Workspace preset summary (API)

Extend `WorkspacePresetSummary` with nullable model detail fields resolved
from the preset `primary` and the LLM snapshot (no new DB tables):

```text
key, kind, primary, description, is_default   # existing
provider_slug: str | null
model_id: str | null
model_display_name: str | null
context_window: int | null
reasoning: bool | null
input_modalities: list[str] | null
```

**Wire contract:** all new fields optional / default `None` (or empty list
only if we prefer `[]` for modalities — prefer `None` when unknown so clients
can distinguish missing vs empty). Older clients ignore extras.

**Resolution** in `get_workspace_model_presets` (or a small helper):

1. Parse `primary` on **first `/` only**: `provider_slug`, `model_id`
   (everything after first slash). No slash → treat whole string as slug or
   model_id defensively; never 500.
2. Look up `snap.providers.get(provider_slug)`.
3. Find model where `m.id == model_id` (snapshot `ModelConfig.id`).
4. Fill:
   - `model_display_name` ← `m.name`
   - `context_window` ← `m.context_window`
   - `reasoning` ← `m.reasoning`
   - `input_modalities` ← `m.input`
5. Missing provider/model → null detail fields; UI still shows tier label +
   default icon + tooltip with whatever is known (`primary` / description).

**Do not:**

- Extend `ProviderConfig` with logo fields for this feature.
- Join provider `logo_url` / `preset_slug` for the picker.
- Return a backend `logo` / brand id unless we later choose server-side
  inference for consistency (frontend inference is the default).

Frontend types (`WorkspacePresetSummary` in `lib/types/presets.ts`) must
match. Normalize before render: `model_id ?? split(primary)`.

### Label rules (product-locked)

| Preset kind | List / trigger title |
| --- | --- |
| tier | i18n tier name |
| custom | `key` |

`model_display_name` appears in the **tooltip**, not as the list hero.

### Phasing

Single implementation PR preferred:

1. API enrichment of model detail fields + tests  
2. `inferModelBrand` + logo component  
3. ModelPicker list/trigger/tooltip  

No separate logo-upload PR.

## Out of scope

- Provider logo **file upload** or promoting Logo URL for the picker
- Provider letter monogram as the picker’s primary identity
- CDN pack for every open-weight id beyond the heuristic table
- Full `capability` object in tooltip (only reasoning + input_modalities)
- Failover chain UI, group-by-provider list
- Changing which presets appear or selection / thinking behavior
- Redesigning org model-preset admin

## Success criteria

1. List no longer uses long untruncated `provider/model` as the primary
   readable label.
2. List does not show description under rows.
3. List and closed trigger show model-family brand when `model_id` matches
   the heuristic table; otherwise default model icon.
4. List/trigger title is tier i18n name or custom `key`.
5. Hover Tooltip shows provider slug, model id, display name (if any),
   context window, reasoning, input modalities, and description (tier i18n
   or custom text).
6. Thinking effort + selection behavior unchanged.
7. API schema + frontend types updated; tests for primary parse, missing
   model, two-provider no cross-wiring of model details; ModelPicker tests
   for label, no description row, brand/default icon, tooltip fields.

## Resolved product choices

| Question | Decision |
| --- | --- |
| Logo identity | **Model family** via `model_id` / name heuristics |
| Unknown brand | Default model icon (`Cpu` or equivalent) |
| Provider logo / upload | **No** |
| List hero text | Tier i18n name / custom `key` |
| List description | **Hidden** (tooltip only) |
| Hover carrier | `Tooltip` component |
| Tooltip capability | **Only** `reasoning` + `input_modalities` (plus provider, model id, display name, context, description) |
| Brand resolution | Frontend pure function; not provider slug |
| API | Extend `WorkspacePresetSummary` with model detail fields only |

## Related

- Issue #393, PR #402
- `ModelPicker.tsx`, `ProviderLogo.tsx` (brand map source), `tooltip.tsx`
- `model_presets.py` routes + schemas
- `load_llm_snapshot` / `ProviderConfig` / `ModelConfig`
- Supersedes earlier provider-avatar + logo-upload direction on this branch
