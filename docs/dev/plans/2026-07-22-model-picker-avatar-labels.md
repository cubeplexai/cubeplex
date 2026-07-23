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
- Extract `_resolve_logo` from `admin_providers` into a shared module
  (e.g. `cubeplex/llm/provider_logo.py` or `services/provider_logo.py`) so
  workspace routes do not import admin route modules.
- Either: slim provider metadata query next to the route/service, **or**
  extend `ProviderConfig` + `_load_providers` with `name` / `logo_url` /
  `preset_slug` (see design — prefer metadata map if snapshot should stay
  LLM-call-only).

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

All new fields default `None` (additive wire contract).

**Core logic** (in `get_workspace_model_presets`) — **do not** read
`logo_url` / `preset_slug` / `name` from today’s `ProviderConfig`:

```
meta = load_provider_display_meta(session, org_id)
  # slug -> {name, logo_url, preset_slug} from Provider rows
for each preset p in snap.model_presets:
  slug, model_id = split_primary(p.primary)  # first "/" only
  m = meta.get(slug) if slug else None
  logo_url = m.logo_url if m else None
  logo = _resolve_logo(m.preset_slug) if m else None
  provider_name = (m.name if m else None) or slug
  emit WorkspacePresetSummary(
    ..., provider_slug=slug, provider_name=provider_name,
    logo=logo, logo_url=logo_url, model_id=model_id)
```

If architecture B is chosen instead, extend snapshot load once and map from
the enriched `ProviderConfig`.

**Tests intent**:

- Unit/API test: workspace presets response includes logo fields for a
  catalog-backed provider and a custom provider with `logo_url`.
- Custom provider without logo: `logo` null, `logo_url` null, name present.
- Malformed primary without `/`: graceful nulls, no 500.
- **Two-provider fixture**: presets referencing A vs B return A’s logo/name
  for A’s primary and B’s for B’s — assert no cross-wiring.
- Nested model id after first slash preserved in `model_id`.

---

## Unit 2: Frontend types + fetch path

**Files**:

- `frontend/packages/web/lib/types/presets.ts` (`WorkspacePresetSummary`)
- Any `@cubeplex/core` type if presets are duplicated there
- `frontend/packages/web/lib/api/presets.ts` (passthrough JSON)

**What changes**: Add nullable fields matching API. Backward-tolerant
parsing (`field ?? null`). Before `ProviderLogo`, normalize
`name = provider_name ?? provider_slug ?? primary`.

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
   `primary` as main line; `title={p.primary}`; row `aria-label` includes
   display name + full `primary`.
2. Trigger: selected `ProviderLogo` instead of `Cpu` when `selected`
   exists; fallback `Cpu` only if no presets; trigger aria includes
   selected display name + `primary`.
3. Preserve effort slider, selection store, hydration gating for labels.
4. `aria-pressed` / aria labels remain correct per above.

**Core logic**: pure presentational mapping from summary fields.

**Tests intent**:

- Component test: renders preset name without requiring full primary as
  visible main text; shows logo props when provided.
- Accessible names include full `primary` on row and trigger.
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
