# Traces filter typeahead

## Goal

Replace the four free-text filters on `/admin/traces` (workspace, user,
conversation, model) with searchable select/typeahead controls so admins pick
entities instead of typing UUIDs.

## Architecture

Query-time only. No write-path changes (we do **not** write names into Tempo
spans - that was explored and rejected: it can't scale on the high-cardinality
conversation field because Tempo tag-values has no server-side prefix filter,
and it taxes the run-start path for a diagnostic tool).

Dropdown cost is driven by **distinct-entity count, not trace count**, so the
design never materializes full cardinality:

- **model** (low card) - reuse the existing `GET /tag-values?tag=gen_ai.request.model`
  (Tempo). The value IS the label. Fetch once, filter client-side.
- **workspace** (low card) - new Postgres-backed endpoint, returns all org
  workspaces `{id, name}`. Fetch once, filter client-side.
- **user** / **conversation** (medium/high card) - new Postgres-backed endpoint,
  **server-side prefix typeahead**, `LIMIT 20`. Never fetch all.

The stored filter value is always the raw ID (or model name) - identical to
today - so `list_traces` and its TraceQL are **unchanged**. The new endpoint is
purely read-only suggestions. A free-text fallback lets an admin paste a known
ID (the "advanced search by ID" the user asked for) and never regresses the
current capability.

`run_id`, `start`, `end`, `min_duration_ms`, `max_duration_ms` stay as-is.

## Tech stack

FastAPI + SQLModel/SQLAlchemy (backend), Next.js + React 19 + base-ui
Combobox (frontend). No migration, no DB schema change, no new dependency.

---

## Unit 1 - Backend: `filter-options` endpoint

### Files

- `backend/cubeplex/api/routes/v1/admin_traces.py` - add `GET /filter-options`.
- `backend/cubeplex/api/schemas/trace.py` - add `FilterOption`, `FilterOptionsResponse`.

### Interfaces

`GET /api/v1/admin/traces/filter-options` (require_org_admin, same auth/CSRF as
the sibling routes; `org_id` from session, never from a param):

- `kind`: `workspace` | `user` | `conversation` (required, 400 on unknown).
- `q`: optional prefix string. Used for `user`/`conversation` typeahead.
  Ignored for `workspace` (returns all).
- `limit`: int, default 20, max 50.

Response: `{ "options": [ { "id": str, "name": str } ] }`

### Core logic

Org-scoped queries (org_id from `resolve_current_org_id`):

- `workspace`: `SELECT id, name FROM workspaces WHERE org_id = :org_id ORDER BY name` (cap at 200; org workspaces are coarse/low-cardinality).
- `conversation`: `SELECT id, title FROM conversations WHERE org_id = :org_id AND title ILIKE :q || '%' ORDER BY title LIMIT :limit` (q required to narrow; `conversation` is `OrgScopedMixin`).
- `user`: users belong to an org only via membership. `SELECT DISTINCT u.id, COALESCE(u.display_name, u.email) FROM users u JOIN memberships m ON u.id = m.user_id JOIN workspaces w ON m.workspace_id = w.id WHERE w.org_id = :org_id AND (u.display_name ILIKE :q || '%' OR u.email ILIKE :q || '%') ORDER BY label LIMIT :limit`.

`name` is the display label; `id` is the filter value the combobox stores. For
`user`, label falls back to `email` when `display_name` is null (email is
unique + always present; display_name is nullable).

### Tests (e2e - `backend/tests/e2e/test_admin_traces.py`)

- Each `kind` returns only entities belonging to the session org (seed a second
  org's workspace/user/conversation, assert absent) - the org-scoping invariant.
- `conversation`/`user` prefix `q` narrows results and respects `limit`.
- `workspace` returns all org workspaces regardless of `q`.
- Unknown `kind` -> 400; non-org-admin -> 403.
- These touch the app + Postgres -> e2e, full stop.

---

## Unit 2 - Frontend: `FilterCombobox` + TraceFilterBar rewrite

### Files

- `frontend/packages/web/components/admin/traces/FilterCombobox.tsx` - **new**. Reusable searchable combobox built on `components/ui/combobox.tsx` (base-ui; currently unused repo-wide - this wires it up).
- `frontend/packages/web/components/admin/traces/TraceFilterBar.tsx` - replace the 4 `field()` text inputs with `<FilterCombobox>`; keep `run_id`/time/duration inputs.
- `frontend/packages/web/lib/api/admin-traces.ts` - add `getAdminFilterOptions(kind, q?, limit?)` -> `{id,name}[]`; keep existing `getAdminTraceTagValues` (used for model).
- `frontend/packages/web/components/admin/traces/types.ts` - `TraceFilterValues` unchanged (values stay raw IDs / model name).
- `frontend/packages/web/messages/{en,zh}.json` - `adminTraces.filters`: add placeholder + empty-state keys per field.

### Interfaces

`FilterCombobox` props:

```
type Option = { value: string; label: string }
type LoadMode =
  | { mode: 'list'; load: () => Promise<Option[]> }        // model, workspace
  | { mode: 'typeahead'; load: (q: string) => Promise<Option[]> }  // user, conversation

FilterCombobox({
  label, value, onChange, mode, placeholder?
})
```

- `value` / `onChange` are the raw filter string (the ID or model name), so
  `TraceFilterBar`'s state + URL-sync logic is untouched.
- On selecting an option: `onChange(option.value)`.
- Free-text fallback: committing typed text that matches no option calls
  `onChange(typedText)` - this is the paste-an-ID path. (For model, typed text
  is a model name; for ws/user/conversation, a raw ID.)
- Clearable (× button sets `onChange(undefined)`).

### Core logic

- `list` mode: fetch once on first open, filter options client-side by the
  typed string. Used for model (Tempo tag-values, value=label=model) and
  workspace (filter-options, label=name value=id).
- `typeahead` mode: debounce (≈200ms) `load(q)` on keystroke; skip queries
  shorter than 1 char; show top results. Used for user + conversation.
- Loading / error / empty states inside the popup; a failed fetch leaves the
  field usable as free-text (degrades to current behavior).
- All fetches go through the existing `getJson` helper (credentials, 401
  redirect, 503 -> `AdminTracesDisabledError`). On 503 the page already shows
  the disabled state from the list call.

### Tests

- Component-level: selecting an option calls `onChange` with `option.value`;
  typing + committing commits the typed string; clear button clears. (unit,
  jsdom - no real services.)
- No existing frontend test covers the filter bar, so nothing breaks. A full
  Playwright e2e for the dropdown is optional; the backend org-scoping e2e is
  the load-bearing invariant.

---

## Out of scope

- Writing readable names into Tempo spans (rejected).
- Resolving names for `run_id` (not in tag-values allowlist; stays free-text).
- Friendly names in the trace **detail** view (list table columns are now
  addressed - see Unit 6 below).

(Duration filter and trace-list pagination were originally scoped out here;
see Units 3-6 below - a follow-up round addressed both, plus two bugs found
during a hands-on review of the shipped filter bar.)

## Success criteria

- Admin can pick workspace / user / conversation / model from a searchable list
  instead of typing a UUID.
- user/conversation lists are prefix-narrowed server-side and never exceed 20
  rows per query, so 10k+ conversations don't slow or bloat the dropdown.
- Selecting an option filters the trace list identically to typing that ID today
  (filter value semantics unchanged).
- Pasting a raw ID still works (free-text fallback).
- Only the session org's entities are ever returned.

## Plan self-review

- Spec coverage: the 4 fields -> Unit 1 (3 Postgres kinds) + existing tag-values
  (model) -> Unit 2 wires all 4. ✓
- Interface consistency: `Option {value,label}` is the single combobox contract;
  backend returns `{id,name}`, mapped to `{value:id,label:name}` in the API
  helper; model maps `{value:x,label:x}`. ✓
- Vagueness: cardinality caps (workspace 200, user/conversation 20), the
  user-via-membership join, and the free-text-fallback rule are all named. ✓

---

# Round 2 - time range, pagination, model-filter fix, name rendering

Triggered by a hands-on PM-style review of the shipped filter bar (screenshots
+ direct backend/Tempo probing), which surfaced two live bugs alongside the
user's next-round asks. Same architecture as Round 1: query-time/UI only, no
DB migration, no change to what cubepi writes into spans.

## Unit 3 - Backend: fix the Model tag-values TraceQL scoping bug

**Bug found:** `tag-values?tag=gen_ai.request.model` always returned
`{"values":[]}`, even though traces clearly had `gen_ai.request.model` set on
their `chat` spans. Root cause, confirmed by querying Tempo directly:
`TempoClient.tag_values` (`backend/cubeplex/services/tempo_client.py`) scoped
org + tag in a single `{...}` selector. `cubepi.metadata.org_id` lives on the
`invoke_agent` span; `gen_ai.request.model` lives on the child `chat` span - a
single selector requires both on the *same* span, so it silently matched
nothing. `search()` already had this exact problem documented and solved (see
its comment) - `tag_values` just never got the same treatment.

**Fix:** sibling spansets, same pattern as `search()`:
`{ resource.service.name="cubeplex" && span.cubepi.metadata.org_id=... } && { span.<tag> != "" }`.
Also discovered mid-fix: this Tempo deployment hard-caps every search/tag-values
query at 168h (7 days) - omitting a time range isn't safe either (Tempo's own
default window is too narrow). `tag_values` now always sends an explicit
30-day-intent window, clamped to 167h to stay under the cap.

Tests: `backend/tests/unit/test_tempo_client.py::test_tag_values_scopes_org_and_tag_as_sibling_spansets`
asserts the emitted `q` string shape directly.

## Unit 4 - Backend: default time range server-side + drop duration filter

**Bug found:** opening `/admin/traces` with no filters showed "No traces match
these filters" despite traces existing - `list_traces` sent no `start`/`end`
to Tempo when both were empty, so Tempo used its own (much narrower) default
window. Setting only "From" (no "To") produced an opaque 502 - Tempo rejects
an open-ended range with a 400, which `_bad_upstream` masks.

**Fix** (`backend/cubeplex/api/routes/v1/admin_traces.py::list_traces`):
self-default the range server-side, independent of the client - never call
Tempo without an explicit range. Both `start`/`end` absent -> last 1h; one
side absent -> fill it relative to the other. Also reject a >168h range with
a clear 400 (`"Range exceeds the maximum supported window (7 days)"`) instead
of forwarding a doomed request to Tempo and letting `_bad_upstream` mask it.

Dropped `min_duration_ms`/`max_duration_ms` end-to-end (route params, the two
`TempoClient.search` params + TraceQL clauses, and their one unit test) - the
UI control is gone per user decision, so the plumbing was dead weight.

Tests: `test_list_defaults_to_last_hour_when_no_range_given`,
`test_list_fills_missing_side_of_a_partial_range`,
`test_list_rejects_range_over_168h` in `backend/tests/e2e/test_admin_traces.py`.

## Unit 5 - Frontend: time-range presets + limit + load-more pagination

**Presets:** `[1h] [1d] [7d] [Custom]`, default `1h` - plain button row
(`components/ui/button.tsx`), not a popover, so the active window is always
visible. **No `1m` preset**: the 168h Tempo cap (discovered in Unit 3/4) can't
serve a full month in one query; the user chose to drop the option rather than
build cross-query stitching for a low-value case.

**Pagination:** Tempo's `/api/search` has no cursor. "Load more" is a
time-keyset shift mirroring the repo's one existing pagination precedent
(`components/admin/SSOIdentitiesList.tsx`'s offset/`hasMore`/append pattern,
adapted to a time cursor instead of an offset): fixed `start`, `end` shrinks to
(last visible trace's `start_time` − 1ms) on each click, results append.
`limit` select: `25/50/100`, default `50`.

**URL scheme:** `preset` param (default `1h`); `start`/`end` only round-trip
through the URL in `custom` mode (presets are relative-to-now and would go
stale as frozen absolute timestamps in a bookmarked URL); `limit` param.

Files: `components/admin/traces/TraceFilterBar.tsx`,
`app/admin/traces/page.tsx`, `components/admin/traces/types.ts`,
`messages/{en,zh}.json`.

## Unit 6 - Frontend + backend: render names instead of raw IDs in the trace list table

Extended `GET /admin/traces/filter-options` with an optional repeated `ids`
param (exact-match `.in_()`, mutually exclusive with `q`, `ids` wins) - same
org-scoped queries as Round 1's Unit 1, just a different predicate. Capped at
200 ids per request (400 above that).

Frontend (`app/admin/traces/page.tsx`): after each page of traces loads
(initial or "load more"), batch-resolves newly-seen `workspace_id`/`user_id`/
`conversation_id` values via `getAdminFilterOptionsByIds`, merging into three
`Record<id, name>` caches that persist across pages (never evicted).
`TraceListTable.tsx` renders the resolved name with the raw id as a `title`
tooltip, falling back to the raw id if a lookup misses (e.g. a deleted
workspace/user).

Tests: `test_filter_options_ids_batch_lookup`, `test_filter_options_ids_wins_over_q`,
`test_filter_options_ids_rejects_oversized_batch` in
`backend/tests/e2e/test_admin_traces.py`.

## Round 2 verification

- `cd backend && uv run pytest tests/unit/test_tempo_client.py tests/e2e/test_admin_traces.py --no-cov` - 40 passed.
- `cd frontend && pnpm --filter web run type-check && pnpm --filter web run lint` - clean.
- Manual: Playwright-driven screenshots against the live dev deployment for
  every case above (empty-on-load fixed, model dropdown populated, preset
  switching, custom-range-over-168h shows a real error, load-more appends
  without duplicates, table cells show names with id tooltips).
