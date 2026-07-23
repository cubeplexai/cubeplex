# Model picker: avatar + short labels

## Goal

Make the chat composer **model picker** scannable: show a **provider avatar**
and a **short human name** instead of a long mono `provider_slug/model_id`
string as the primary list label. Keep the full primary ref discoverable
via tooltip / secondary text / aria.

## Context

### Composer UI today

`frontend/packages/web/components/chat/ModelPicker.tsx`:

| Surface | Today |
| --- | --- |
| Closed trigger | Generic `Cpu` icon + preset display name (tier i18n or custom key) + thinking effort |
| Open list row | Checkmark + **mono full `primary`** + preset name + optional description + default badge |

`primary` is the org preset chain head (`slug/model_id`). Long custom slugs
and model IDs dominate the `w-72` popover.

### Workspace API today

`WorkspacePresetSummary` (`backend/cubeplex/api/schemas/model_presets.py`):

```text
key, kind, primary, description, is_default
```

No logo metadata. Workspace list is built in
`get_workspace_model_presets` from `load_llm_snapshot` without joining
provider `logo_url` / catalog brand id.

### Admin logos already exist

| Mechanism | Where |
| --- | --- |
| Brand icon id `logo` | Catalog vendor via `preset_slug` → `_resolve_logo` in `admin_providers.py` |
| Custom URL `logo_url` | `Provider.logo_url` (optional string, max 512) on create/update |
| Fallback | `ProviderLogo` letter monogram from provider **name** |

UI: `frontend/packages/web/components/admin/models/ProviderLogo.tsx`  
Priority: **`logo_url` → brand `logo` → letter fallback**.

### Custom providers

| Capability | Supported? |
| --- | --- |
| Public **logo URL** in Advanced settings | **Yes** |
| **File upload** for logo | **No** (not in this feature) |
| Catalog brand icon | Yes when `preset_slug` maps to a known vendor |
| Empty logo | Letter avatar — acceptable for MVP |

## Approaches considered

**A. Avatar + short name (recommended)**  
List: leading `ProviderLogo`, title = preset display name, secondary muted
model id or tooltip for full `primary`. Trigger: replace `Cpu` with selected
provider avatar. Requires workspace summary logo fields.

**B. Truncate mono `primary` only**  
CSS-only; no brand clarity; weak for custom providers.

**C. Two-line layout without avatar**  
Name + truncated primary; better than today, still text-heavy.

**D. Group list by provider**  
Heavier UX; defer unless many models per provider become common.

**Chosen: A** (ship API logo fields + UI together; pure UI demotion of
`primary` alone is a weak intermediate if both land in one PR).

## Design

### Workspace preset summary (API)

Extend `WorkspacePresetSummary` with fields resolved from the primary ref
and the LLM snapshot’s provider map:

```text
key, kind, primary, description, is_default   # existing
provider_slug: str | null
provider_name: str | null
logo: str | null          # catalog brand id (e.g. "openai", "anthropic")
logo_url: str | null      # custom URL if set
model_id: str | null      # portion after first "/" of primary
```

Resolution in `get_workspace_model_presets` (or a small helper):

1. Parse `primary` as `provider_slug/model_id` (first `/` split; tolerate
   missing slash → slug only / model only as null).
2. Look up provider in `snap.providers` by slug (same map used for LLM
   calls).
3. `logo_url` from provider row/config; `logo` via existing catalog
   `_resolve_logo(preset_slug)` (reuse helper, do not fork brand tables).
4. `provider_name` from provider display name for letter fallback + a11y.

Frontend types (`WorkspacePresetSummary` in web `lib/types/presets.ts` and
any core mirror) must match.

### Model picker UI

**List row**

- Leading: `ProviderLogo` (`sm`) with `name=provider_name`, `logoUrl`,
  `logo`.
- Title line: preset display name (`nameOf(p)` — tier i18n / custom key)
  as the **hero** font (not mono primary).
- Secondary (optional muted truncate): `model_id` only, or omit and put
  full `primary` on `title` / tooltip.
- Default badge unchanged.
- Description stays under the title when present.
- Checkmark retained for active state.

**Closed trigger**

- Replace generic `Cpu` with selected preset’s `ProviderLogo` (sm).
- Keep short name · effort · chevron.
- `aria-label` / existing picker aria includes enough to name the model
  (preset name + full `primary` if useful).

**Full ref discoverability**

- Tooltip or `title` on the row / avatar with full `primary`.
- Screen readers: do not rely on mono primary alone.

### Component reuse

- Reuse `ProviderLogo` logic. Prefer **moving or re-exporting** to a
  neutral path (e.g. `components/models/ProviderLogo.tsx` or
  `components/shared/ProviderLogo.tsx`) so chat does not import from
  `admin/models/`. Admin keeps a re-export or updated import.
- Do not invent a second logo system.
- `logo_url` remote images: reuse admin Next/image or `<img>` patterns;
  verify remote patterns already cover admin usage.

### Custom provider product note

- Operators set **Logo URL** under provider Advanced settings.
- No upload in this issue.
- Missing URL → monogram from provider name — not a broken empty icon.
- Optional later: object-store upload; not required for acceptance.

### Phasing within the feature

Prefer one implementation PR that does API + picker together (avatar is
the product point). If split:

1. API fields + types  
2. Picker UI + ProviderLogo share  

Not in this feature: logo file upload, failover-chain UI, admin preset
redesign.

## Out of scope

- Provider logo **file upload**
- CDN pack for every open-weight model
- Changing which presets appear or failover chain behavior
- Requiring every custom provider to set a logo before ship
- Redesigning org model-preset admin beyond summary fields
- Showing full failover chain in the picker

## Success criteria

1. List no longer uses long untruncated `provider/model` as the primary
   readable label.
2. When brand id or `logo_url` is available, list rows and closed trigger
   show that avatar.
3. Custom providers without `logo_url` show letter fallback — not empty
   broken icons.
4. Full `primary` remains discoverable (tooltip / secondary / aria).
5. Thinking effort + selection behavior unchanged.
6. i18n/a11y: trigger still clearly names the selection.
7. API schema + frontend types updated; unit/API tests for summary fields;
   ModelPicker display tests updated.

## Resolved product choices

| Question | Decision |
| --- | --- |
| Hero text | Preset display name (tier i18n / custom key), not raw model id |
| Provider text in list | Hidden when avatar present; full ref via tooltip |
| Closed trigger avatar | Always (including letter fallback) |
| Custom logo | URL only; upload is P2 |
| API | Extend `WorkspacePresetSummary` in the same feature |

## Related

- Issue #393
- `ModelPicker.tsx`, `ProviderLogo.tsx`, `ProviderConfigForm.tsx`
- `model_presets.py` routes + schemas
- `Provider.logo_url`, `_resolve_logo`, LLM snapshot providers map
