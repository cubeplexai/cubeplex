# Model picker avatar + short labels — implementation plan

**Goal**: Workspace model presets expose provider logo metadata; the chat
model picker shows avatar + short preset name instead of long mono primary.

**Architecture**: Backend enriches `WorkspacePresetSummary` from the existing
LLM snapshot + catalog logo resolution. Frontend maps fields into a shared
`ProviderLogo` and restyles `ModelPicker` list/trigger. No migrations (logo
columns already exist on `providers`).

**Tech stack**: FastAPI schemas, `load_llm_snapshot`, catalog `_resolve_logo`,
React ModelPicker, existing ProviderLogo / lobehub icons.

---

## Unit 1: Backend — extend workspace preset summary

**Files**:

- `backend/cubeplex/api/schemas/model_presets.py`
- `backend/cubeplex/api/routes/v1/model_presets.py`
- Optionally extract logo resolve helper next to admin
  (`admin_providers._resolve_logo`) into a shared module if import cycles
  appear — prefer reusing the same function

**Interfaces**:

```python
class WorkspacePresetSummary(BaseModel):
    key: str
    kind: Literal["tier", "custom"]
    primary: str
    description: str
    is_default: bool
    provider_slug: str | None = None
    provider_name: str | None = None
    logo: str | None = None
    logo_url: str | None = None
    model_id: str | None = None
```

**Core logic** (in `get_workspace_model_presets`):

```
for each preset p in snap.model_presets:
  slug, model_id = split_primary(p.primary)  # first "/"
  provider = snap.providers.get(slug) if slug else None
  logo_url = provider.logo_url if provider else None
  logo = _resolve_logo(provider.preset_slug) if provider else None
  provider_name = provider.name if provider else slug
  emit WorkspacePresetSummary(..., logo=..., logo_url=..., ...)
```

Confirm actual attribute names on snapshot provider config (`logo_url`,
`preset_slug`, `name`) when implementing — map 1:1 from DB-backed snapshot
fields already loaded for chat.

**Tests intent**:

- Unit/API test: workspace presets response includes logo fields for a
  catalog-backed provider and a custom provider with `logo_url`.
- Custom provider without logo: `logo` null, `logo_url` null, name present.
- Malformed primary without `/`: graceful nulls, no 500.

---

## Unit 2: Frontend types + fetch path

**Files**:

- `frontend/packages/web/lib/types/presets.ts` (`WorkspacePresetSummary`)
- Any `@cubeplex/core` type if presets are duplicated there
- `frontend/packages/web/lib/api/presets.ts` (passthrough JSON)

**What changes**: Add optional/required fields matching API. Keep backward
tolerant parsing if needed (`?? null`).

**Tests intent**: typecheck; optional schema assert in existing presets
tests.

---

## Unit 3: Share `ProviderLogo`

**Files**:

- Move or re-export from
  `frontend/packages/web/components/admin/models/ProviderLogo.tsx`
  → e.g. `frontend/packages/web/components/models/ProviderLogo.tsx`
- Update admin imports to the new path

**What changes**: No visual redesign; ensure `sm` size works in picker rows
and trigger. Keep priority `logo_url → logo → letter`.

**Tests intent**: existing admin logo tests if any; smoke import from chat
bundle.

---

## Unit 4: ModelPicker UI

**Files**:

- `frontend/packages/web/components/chat/ModelPicker.tsx`

**What changes**:

1. List row: `ProviderLogo` + `nameOf(p)` as hero; demote/remove mono
   `primary` as main line; tooltip `title={p.primary}`.
2. Trigger: selected `ProviderLogo` instead of `Cpu` when `selected`
   exists; fallback `Cpu` only if no presets.
3. Preserve effort slider, selection store, hydration gating for labels.
4. `aria-pressed` / aria labels remain correct.

**Core logic**: pure presentational mapping from summary fields.

**Tests intent**:

- Component test: renders preset name without requiring full primary as
  visible main text; shows logo props when provided.
- Selection still updates store on click.

---

## Unit 5: Docs (implementation PR)

**Files** (if user-facing):

- `docs/site/docs/admin/models.md` — one line: optional Logo URL improves
  chat model picker; monogram used otherwise.
- Conversations / composer docs only if they describe the picker layout.

**Tests intent**: none.

---

## Unit 6: Verification

- Backend unit/API tests for summary enrichment.
- Frontend ModelPicker test(s).
- Manual: catalog provider brand icon; custom with URL; custom without URL
  (letter); long model ids no longer dominate the row.

---

## Non-goals

- Logo file upload endpoint
- Changing preset selection or thinking defaults
- Group-by-provider list
