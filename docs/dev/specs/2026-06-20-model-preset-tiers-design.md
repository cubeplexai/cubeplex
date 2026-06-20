# Model preset tiers — design

Date: 2026-06-20
Status: design (approved in brainstorming, pending spec review)

## Problem

Model presets today are a free-form list: each preset is `{label, chain,
is_default}`, stored as the `org_settings.model_presets` JSON value. Labels are
arbitrary strings, the chain mixes "primary model" and "fallback models" into
one ordered list (index 0 is implicitly the primary), and there is no
human-facing guidance about which preset a user should pick.

We want:

1. **Four fixed, named tiers** — `lite`, `flash`, `pro`, `max` — with built-in,
   translated descriptions so users understand which to choose. `pro` is the
   seeded default.
2. **Optional enable per tier** — an org can offer only some tiers; a disabled
   tier keeps its configuration but is hidden from users.
3. **Admin-added custom presets** alongside the four tiers (free-form label +
   admin-written description, no i18n).
4. **Primary / fallback split** so the primary model's name can be shown in the
   picker and in task routing.
5. **A config-file structure that matches** the new data model.
6. **Cleaner task-routing UI** (the three `__not_set__` dropdowns).

The project has not shipped publicly, so we **cut over cleanly** — no backward
compatibility with the old preset value shape.

## Goals / non-goals

In scope:

- New `ModelPresetsConfig` data model (tiers + custom presets + default + task
  routing), its validation, runtime resolution, seeder, and config-file shape.
- A migration that drops the old `model_presets` rows so the new seeder reseeds.
- The admin **Model Settings** editor redesign (tiers / custom / task-routing
  sections) including the task-routing visual cleanup.
- Renaming the over-generic `preset(s)` identifiers to `model_preset(s)` across
  backend schema/API and the frontend API layer.

Out of scope (explicitly next step):

- **`PresetPicker` → `ModelPicker` rename + interaction/visual redesign.** This
  step keeps the existing composer picker working against the renamed API
  fields; the picker's own redesign (including showing each preset's primary
  model name + description) is a separate follow-up.

## Data model

Two kinds of selectable "model preset" — built-in tiers and admin custom
presets — modeled separately because they differ in nature (a tier is known,
ordered, and described by fixed product copy; a custom preset is author-defined
and self-described). Both resolve to a model chain, both can be the default,
both can be a task-routing target.

```python
class ModelTier(str, Enum):       # built-in, ordered lite < flash < pro < max
    lite = "lite"
    flash = "flash"
    pro = "pro"
    max = "max"

class TaskKey(str, Enum):
    title = "title"
    summarize = "summarize"
    compaction = "compaction"

class TierSetting(BaseModel):
    enabled: bool = False
    primary: str | None = None     # primary model ref; required when enabled
    fallbacks: list[str] = []      # ordered backups, may be empty

class CustomPreset(BaseModel):
    label: str                     # free-form, must not collide with a tier name
    primary: str                   # required
    fallbacks: list[str] = []
    description: str               # admin free-text, NOT i18n

class ModelPresetsConfig(BaseModel):   # the org_settings.model_presets value
    tiers: dict[ModelTier, TierSetting]    # all four keys present
    custom_presets: list[CustomPreset] = []
    default_preset: str                    # a tier name or custom label; must be enabled
    task_routing: dict[TaskKey, str] = {}  # task -> preset key; unset = default_preset
```

Notes:

- **Tier descriptions are not stored.** They are fixed product copy, looked up
  in the frontend i18n bundle by tier (`modelTiers.<tier>.description`). The
  admin cannot edit them. Custom presets carry their own `description`.
- **`enabled` is an explicit boolean**, not "chain is empty". Disabling a tier
  preserves its `primary`/`fallbacks` so re-enabling restores the config.
- **A preset is *available* (offered to users / usable as default / task
  target) when it is enabled and has a primary.** Tiers must satisfy
  `enabled ⟹ primary is not None`; custom presets always have a primary.
- **`default_preset` and `task_routing` share one "preset key" namespace** —
  a value is either a `ModelTier` value (`"pro"`) or a custom `label`. Custom
  labels may not collide with tier names.

### Validation rules

- `tiers` contains exactly the four `ModelTier` keys.
- For each tier, `enabled` ⟹ `primary` is set.
- `custom_presets` labels are unique, non-empty, and none equals a tier name.
- `default_preset` references an available preset.
- Each `task_routing` value references an available preset (a task may be unset,
  which means "use `default_preset`").

## Runtime

The structured config is the **authoring/storage** shape. The runtime works on
a flattened, uniform view so the resolver/builder don't care about tier-vs-
custom.

```python
class ModelPreset:                 # one available preset, flattened
    key: str                       # tier name or custom label
    primary: str
    fallbacks: tuple[str, ...]
    kind: Literal["tier", "custom"]
    is_default: bool

    @property
    def chain(self) -> tuple[str, ...]:
        return (self.primary, *self.fallbacks)
```

- `LLMSnapshot` exposes `model_presets: list[ModelPreset]` (was the bare
  `presets`) — only the available ones, tiers first in tier order, custom after.
- `resolve_model_preset(snap, key | None)` (was `resolve_preset`) returns the
  preset for `key`, or the default when `key` is `None`.
- Task resolution maps `task_routing[task]` → key → preset; unset → default.
- `builder` / `run_manager` keep using `preset.chain`; the only change is that
  `chain` is derived from `primary + fallbacks` rather than a stored list.

## Naming

| Concept | Name |
|---|---|
| Stored/config structure | `ModelPresetsConfig`; config key `llm.model_presets`; org_settings key `"model_presets"` (unchanged) |
| Built-in four | `tiers: dict[ModelTier, TierSetting]` |
| Admin-added | `custom_presets: list[CustomPreset]` |
| Default selection | `default_preset` |
| Task routing | `task_routing: dict[TaskKey, str]` |
| Flattened runtime item | `ModelPreset` (was `LLMPreset`) |
| Runtime list on snapshot | `snapshot.model_presets` (was `presets`) |
| Resolver | `resolve_model_preset` (was `resolve_preset`) |

Existing-identifier rename map (backend): `LLMPresetSchema` /
`ModelPresetsValue` → folded into `TierSetting` / `CustomPreset` /
`ModelPresetsConfig`; `LLMPreset` → `ModelPreset`; `snap.presets` →
`snap.model_presets`; `resolve_preset` → `resolve_model_preset`;
`task_presets` → `task_routing`; `WorkspacePresetSummary` /
`WorkspacePresetsResponse` fields updated. Frontend (this step, API layer only):
`fetchWorkspaceModelPresets` return shape; `preset-selection` store field
`presetLabel` → `modelPresetKey`.

## Built-in tier descriptions (fixed i18n copy)

Draft product copy (final wording can be tuned during implementation):

| Tier | en | zh |
|---|---|---|
| lite | Fastest and cheapest. Best for simple, high-volume tasks. | 最快最省,适合简单、高频任务。 |
| flash | Fast with solid quality. A balanced everyday choice. | 速度快、质量稳,日常均衡之选。 |
| pro | Recommended. Strong reasoning for most work. | 推荐。多数工作的强力默认。 |
| max | Most capable. For the hardest, highest-stakes tasks. | 能力最强,适合最难、最重要的任务。 |

## Config file structure

Replaces `llm.default_model`, `llm.fallback_models`, `llm.title_model`,
`llm.summarize_model`, and `llm.compaction.summary_model` (preset-seeding part).

```yaml
llm:
  model_presets:
    tiers:
      lite:  { enabled: true,  primary: deepseek/deepseek-v4-flash, fallbacks: [] }
      flash: { enabled: true,  primary: alicode/qwen3.6-plus,       fallbacks: [minimax/MiniMax-M2.7] }
      pro:   { enabled: true,  primary: vllm/gemma-4-31b-it,        fallbacks: [openrouter/stepfun/step-3.5-flash:free] }
      max:   { enabled: false, primary: null,                       fallbacks: [] }
    default_preset: pro
    task_routing:
      title: lite
      summarize: flash
      compaction: pro
```

`custom_presets` are not seeded from config — they are added by admins at
runtime. Config seeds only the four tiers + `default_preset` + `task_routing`.

## Seeding

`seed_default_presets_from_config` becomes `seed_model_presets_from_config`:
reads `llm.model_presets`, builds a `ModelPresetsConfig`, and writes the system
`org_settings` row (`org_id IS NULL`). Idempotent: skip if the system row
exists (never clobber admin edits). If `llm.model_presets` is absent, skip.

## Migration (clean cutover)

The `model_presets` JSON value shape changes incompatibly; old rows would fail
validation on read. Migration: **delete every `org_settings` row with
`key = 'model_presets'`** (system + org). On next boot the seeder reseeds the
system row from config; org admins reconfigure their overrides. No compat shim.

## Admin UI — Model Settings (PresetEditor redesign)

Three sections in the centered content column, under the existing
`AdminPageShell` header and above the sticky save bar (Discard + Save).

**1. Tiers** — four fixed rows in order Lite / Flash / Pro / Max. Each row:

- Tier name + the fixed (read-only) i18n description beneath it.
- An **enable** Switch.
- A **default** radio (one group spanning all available tiers + custom presets).
- When enabled: a **Primary model** single-select + an ordered **Fallbacks**
  list (add / reorder / remove). When disabled: collapsed/greyed, config kept.

**2. Custom presets** — "Add preset" button; each card has a label input, a
free-text **description** input, the same Primary + Fallbacks editor, a default
radio, and a remove button.

**3. Task routing** (visual cleanup of today's three `__not_set__` dropdowns) —
a vertical list, one row per task (Title / Summarize / Compaction):

- Left: task name + a one-line i18n explanation.
- Right: a Select whose options are the available presets. The **empty value
  renders as muted `Default · Pro (Gemma 31B)`** — naming the fallback preset
  and its primary model — instead of `__not_set__`.

Showing a preset's primary model name (here and later in the ModelPicker)
resolves the model ref (`vllm/gemma-4-31b-it`) to its catalog display name
(`Gemma 31B`) via the providers list the frontend already loads.

## Testing

- **Backend e2e** — `ModelPresetsConfig` validation (missing tier key, default
  pointing at a disabled/unknown preset, custom label colliding with a tier,
  task routing to an unavailable preset); seeder writes the expected system row;
  migration deletes old rows and the seeder reseeds; `resolve_model_preset`
  returns the right chain for a key / default / task; admin write→read round
  trip.
- **Unit** — `ModelPreset.chain` derivation; resolver key/default/task logic.
- **Frontend** — keep the existing composer-picker flow green against the
  renamed API fields (no new picker tests here; the ModelPicker redesign owns
  those next step).

## Open follow-ups (next steps, not this spec)

- `ModelPicker` (renamed from `PresetPicker`) interaction + visual redesign,
  surfacing each preset's tier description / custom description and primary
  model name.
