# Model picker: model-brand logo + tier + hover — implementation plan

**Goal**: Workspace model presets expose primary-model detail fields; the
chat model picker shows a model-family brand icon + tier/custom label, hides
list description, and shows details on hover Tooltip.

**Architecture**: Backend enriches `WorkspacePresetSummary` from
`load_llm_snapshot` by parsing `primary` and reading `ModelConfig`. Frontend
infers brand from `model_id` (not provider slug), restyles `ModelPicker`, and
uses shared Tooltip. No migrations. No logo upload.

**Tech stack**: FastAPI schemas, LLM snapshot, React ModelPicker, existing
brand icons (`@lobehub/icons`), `components/ui/tooltip`.

---

## Unit 1: Backend — model detail fields on workspace preset summary

**Files**:

- `backend/cubeplex/api/schemas/model_presets.py`
- `backend/cubeplex/api/routes/v1/model_presets.py`
- Optional tiny helper module if parse/lookup is non-trivial (keep out of
  admin routes)

**Interfaces**:

```python
class WorkspacePresetSummary(BaseModel):
    key: str
    kind: Literal["tier", "custom"]
    primary: str
    description: str
    is_default: bool
    provider_slug: str | None = None
    model_id: str | None = None
    model_display_name: str | None = None
    context_window: int | None = None
    reasoning: bool | None = None
    input_modalities: list[str] | None = None
```

All new fields default `None` (additive wire contract).

**Core logic** (in `get_workspace_model_presets`):

```
snap = load_llm_snapshot(...)
for each preset p in snap.model_presets:
  slug, model_id = split_primary(p.primary)  # first "/" only
  mc = find_model(snap.providers, slug, model_id)
  emit WorkspacePresetSummary(
    key=..., kind=..., primary=..., description=..., is_default=...,
    provider_slug=slug,
    model_id=model_id,
    model_display_name=mc.name if mc else None,
    context_window=mc.context_window if mc else None,
    reasoning=mc.reasoning if mc else None,
    input_modalities=list(mc.input) if mc else None,
  )
```

Malformed / missing provider or model → nulls, no 500.

**Tests intent**:

- Known primary fills all detail fields from fixture provider/model.
- Missing model: slug/model_id still parsed when possible; detail nulls.
- Nested model id after first slash preserved.
- Two-provider fixture: no cross-wiring of context/reasoning between A and B.
- No logo fields asserted (feature does not return them).

---

## Unit 2: Frontend types + fetch path

**Files**:

- `frontend/packages/web/lib/types/presets.ts`
- `frontend/packages/web/lib/api/presets.ts` (passthrough if untyped parse)

**What changes**: Add nullable fields matching API. Tolerate missing keys
(`?? null`).

**Tests intent**: typecheck; optional assert in existing presets tests.

---

## Unit 3: `inferModelBrand` + logo presentation

**Files**:

- New e.g. `frontend/packages/web/lib/models/infer-model-brand.ts`
- New or shared e.g. `frontend/packages/web/components/models/ModelBrandLogo.tsx`
  (extract brand map from admin `ProviderLogo` without requiring `logo_url`
  letter path for chat)

**What changes**:

1. `inferModelBrand(modelId, displayName?) → brandId | null` per design table.
2. `ModelBrandLogo`: brand icon if known, else default model icon (`Cpu`).
3. Unit tests for patterns: claude→anthropic, gpt→openai, qwen→qwen,
   openrouter-style ids still match on model portion, unknown→null.

**Do not** use provider slug in the inference function.

---

## Unit 4: ModelPicker UI

**Files**:

- `frontend/packages/web/components/chat/ModelPicker.tsx`
- `frontend/packages/web/__tests__/components/ModelPicker.test.tsx`

**What changes**:

1. **List row**: `ModelBrandLogo` + tier/custom title only; remove mono
   `primary` main line; **remove description under row**; keep default badge
   + checkmark.
2. **Trigger**: selected brand logo instead of bare `Cpu` when selected;
   keep name · effort · chevron.
3. **Tooltip** on each row (and optionally trigger): labeled fields —
   provider slug, model id, display name, context, reasoning, input
   modalities, description (tier i18n desc or custom `description`).
4. Labels: `nameOf` = tier i18n / custom `key` only (unchanged hero rule).
5. Aria: row + trigger include label + full `primary`.
6. Preserve effort slider, selection store, hydration gating.

**Tests intent**:

- Renders tier name; does not require mono primary as main text.
- Description not in list DOM as secondary line (tooltip content may hold it).
- Unknown brand still shows a default icon node.
- Selection still updates store on click.

---

## Unit 5: Docs (implementation PR)

**Files** (if any user-facing admin/composer docs mention the picker):

- Short note only if docs currently describe mono primary rows — update to
  tier + hover details.

**Tests intent**: none.

---

## Unit 6: Verification

- Backend unit/API tests for summary enrichment.
- Frontend `inferModelBrand` + ModelPicker tests.
- Manual: Claude/GPT/Qwen ids show correct brands; unknown id → default icon;
  hover shows context/reasoning/modalities; list has no description clutter;
  thinking effort still works.

---

## Non-goals

- Provider logo upload or Logo URL promotion for picker
- Provider letter monogram as picker identity
- Full capability blob / cost / failover in tooltip
- Group-by-provider list
- Changing preset selection or thinking defaults
