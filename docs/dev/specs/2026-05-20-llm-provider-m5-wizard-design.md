# LLM Provider Platform — M5 (Add Provider wizard) + M7 (polish) design

**Status:** Draft for review (rev 3 — codex re-review: SSE takes explicit model-id list; footer keys off probe `overall`, not readiness)
**Author:** xfgong
**Date:** 2026-05-20
**Parent spec:** `docs/dev/specs/2026-05-19-llm-provider-platform-design.md` (§4.5, §4.7, §4.8, M5/M7)
**Builds on:** integration branch `feat/llm-provider-platform` (M3+M4+M6 backend, PR #124, draft)

This is the Add-Provider wizard UI plus the backend gaps it needs to actually
work. A codex review of rev 1 found that the M3/M4/M6 backend does **not** yet
accept capability on the create path, the per-model test endpoint re-runs
liveness each call, and a few probe affordances were assumed but not built.
This revision scopes those in.

## 1. Scope

Frontend (M5/M7):
- Add-Provider wizard at `/admin/models/new` (spec §4.5).
- Readiness in model-selection UIs (§4.7) — **on the provider detail page and
  any existing model picker**; the per-task-routing settings dropdown is a
  **separate later slice** (its settings API doesn't exist yet — see §7).
- Page-shows-configured-only rule (§4.8).
- M7 polish: provider liveness dot + per-model status dots + re-test buttons;
  i18n sweep.

Backend additions M5 requires (each verified missing against the current tree):
- **Persist capability on create/update.** `ProviderCreate`/`ProviderUpdate`
  gain `capability`, `model_capability_overrides`, `preset_slug`;
  `create_provider`/`update_provider` persist them. Without this the wizard
  cannot save a configured provider — `create_provider` currently drops them.
- **`ModelCreate.enabled`.** Wizard creates models `enabled=false`, flips to
  `true` when a model passes its probe (`Model.enabled` defaults `True` today).
- **SSE test endpoint** (see §6) — streams liveness + per-model results.
- **`usage` advisory probe step** (see §5).

Out of scope: new presets; task_models settings API/UI; multi-tenant provider
scoping; the "skip optional probes" toggle (deferred — not in v1).

## 2. Decisions

1. **Full-page route, not modal/drawer.** Wizard at `/admin/models/new` inside
   the admin shell. The 4 steps + preset grid + per-model test results need the
   room; matches the parent spec's "own route per page" rule.
2. **Provider row persisted after step 2 (Configure).** Steps 3 (Models) and 4
   (Test) operate on the saved provider and reuse the `/{id}/...` endpoints.
   This requires the create-path capability persistence above. An abandoned
   wizard leaves an untested provider with no enabled models — deletable.
3. **SSE for the Test step.** A new SSE endpoint runs liveness once, then each
   model's probe, emitting an event per result as it completes. This fulfills
   parent spec §4.5 ("test results streamed back as the probe runs (SSE)"),
   reuses cubeplex's SSE infrastructure, and avoids the multi-minute
   single-request timeout risk of looping JSON calls (worst case ~45–50s per
   model × N sequentially). *(Rev 1 proposed JSON polling; the latency reality
   reverses that.)*
4. **Test step maps the two-phase probe + readiness onto the UI** (§6): Phase A
   liveness, then per-model Phase B with 5 sub-checks — Reasoning (blocking) +
   Temperature / Tools / Streaming / **Usage** (advisory) — and a per-model
   outcome badge. Save gated at the model grain.

## 3. Routes & page structure

- `/admin/models` (existing) — provider list + detail. **Configured rows only**
  (§4.8); presets never appear here. "Add provider" → `/admin/models/new`.
- `/admin/models/new` (new) — the wizard.
- Provider detail stays in the `/admin/models` detail pane; gains M7 status
  dots + re-test (§7).

## 4. Wizard flow & persistence

Step rail: **1 Preset · 2 Configure · 3 Models · 4 Test.**

1. **Preset** — grid from `GET /admin/llm/presets`, brand icon via
   `@lobehub/icons`, name, wire-api, reasoning-shape badge; search + category
   filter. Selecting carries `capability`, `base_url`, `provider_type` (wire
   literal), default model list forward. Custom tiles start empty.
2. **Configure** — preset auto-fills name/base URL/`provider_type`/capability;
   API key the only required free input. "Advanced" expander = capability
   editor (JSON view + reasoning-template popover for custom). **On "Next" the
   provider row is created** via the extended `POST /admin/providers` (now
   carrying `preset_slug` + `capability` + `model_capability_overrides`); the
   wizard switches to working against `{id}`.
3. **Models** — import preset defaults (checkbox list) or add custom; each
   `POST /admin/providers/{id}/models` with `enabled=false`.
4. **Test** — runs the SSE probe (§6); models that pass are flipped
   `enabled=true` (PATCH); finishes back to the list with the provider selected.

The pre-save `/providers/liveness` + `/providers/test` (one-model) endpoints
stay as an optional "quick check before you commit a key" affordance on the
Configure step; authoritative per-model testing is post-persist on step 4.

## 5. Backend addition — `usage` probe step

In `cubeplex/services/provider_probe.py`:
- `ProbeStepName` gains `"usage"`; **not** in `_BLOCKING_STEPS`.
- `probe_usage(...)` runs its **own** minimal stream (rev 1 wrongly said it
  could reuse the reasoning step's drained events — the reasoning step exposes
  only `observed_chunks`, not the event buffer). It inspects the response for a
  parseable token-usage structure (prompt/completion tokens + cache read/write
  when present). `pass` if found; `warn` if absent
  ("no usage block → cost recorded as zero"). Joins the Phase-B parallel gather.
- Result rides inside the model's `last_test_summary` ProbeResult JSON; no new
  column. **Adds no new readiness enum value** — a usage `warn` aggregates to
  overall `warn` → existing `degraded` (rev 1's "readiness unaffected" was
  inaccurate).
- *Note:* this validates that the endpoint **returns** a usage structure;
  runtime cost accounting reads `AssistantMessage.usage`
  (`middleware/cost.py`), not probe events. The probe is a wiring check, not the
  cost path.

## 6. Test step UX + transport (step 4)

Mock: `step4-test-v2.html` (brainstorm session; ignore its "skip optional"
toggle — dropped per §1).

**Transport — SSE.** New endpoint (e.g. `POST /admin/providers/{id}/test/stream`,
`text/event-stream`, `compress: false` per the SSE-buffering caveat). Its
request body carries an **explicit list of model ids to test** — it must NOT
filter by `enabled=true`, because wizard models are still `enabled=false` at
this point (and `run_all_models_test_saved`'s enabled-only filter is therefore
not reusable here). It runs `run_liveness` **once**, emits a `liveness` event,
then for each model id in the request runs `run_model_probe` and emits a
`model` event with that model's `ProbeResult`, then a terminal `done` event.
This replaces the rev-1 plan of
looping `/{id}/models/{mid}/test` (which re-runs liveness every call —
`run_model_test_saved` does liveness + persist each time). The per-model JSON
endpoints remain for single-model re-test from the detail page (§7).

**Display.** Phase A liveness row → green "可达 · Nms"; on fail all models
render `provider_error` and Phase B is skipped, save blocked. Then per model, a
card streamed in as its event arrives, with 5 sub-check chips and an outcome
badge derived from the `ProbeResult.overall`:
`pass→可用` / `warn→降级` / `fail→无法启用` / `unavailable→无法启用 (model_not_found)`,
with reason + "重测/移除" on failure. The badge is computed from the streamed
`ProbeResult.overall` (`pass`/`warn`/`fail`/`unavailable`) — the derived
`readiness` enum is NOT in stream state; it's re-read from
`GET /admin/providers/{id}` when the page refreshes after the run.
Footer "保存 Provider (N 个模型可用)" enabled once liveness passed and ≥1 model's
probe `overall` was `pass`/`warn` (read from the stream, not from readiness);
saving flips those models `enabled=true`. (Their derived readiness — `ready` for
`pass`, `degraded` for `warn` — shows on the detail page from the GET after.)

## 7. Readiness surfaces (§4.7) + detail polish (M7)

- **`ReadinessBadge`** component — maps server `readiness`
  (`ready`/`degraded`/`stale`/`provider_error`/`model_error`/`unavailable`) to
  dot + label + tooltip. Server returns `readiness` per model on
  `GET /admin/providers/{id}`; the UI never re-derives.
- **Surfaces in M5:** the provider **detail page** (per-model dots) and any
  **existing model picker** that lists provider models. Unusable models show
  **disabled + reason + fix**, not hidden.
- **Out of M5:** the per-task-routing settings dropdown (§4.6 "task_models").
  `/admin/settings/llm` exposes only `default_model`/`fallback_models`; there is
  no API to write `OrgSettings.task_models`. That settings API + UI is a
  separate later slice; this slice does not surface readiness there.
- **Detail (M7):** provider header liveness dot; per-model readiness dots;
  re-test buttons — provider liveness (`POST /{id}/liveness`), single model
  (`POST /{id}/models/{mid}/test`), and "test all" via the SSE stream. Dots come
  from the persisted columns (no probing on load).

## 8. Page rule (§4.8)

`/admin/models` list + detail render **only configured rows**
(`GET /admin/providers` + each provider's models). Presets are fetched **only**
in the wizard. A configured-but-broken model stays on the page disabled with a
reason; an un-added preset model never appears there.

## 9. Architecture / files

`@lobehub/icons` added to `packages/web`; rendered via `<ProviderIcon … />`,
null logo → generic fallback (extend the existing `ProviderLogo`).

Backend:
- `api/schemas/provider.py` — `ProviderCreate`/`ProviderUpdate` gain
  `capability` / `model_capability_overrides` / `preset_slug`;
  `ModelCreate` gains `enabled` (default `false`).
- `services/provider_service.py` — `create_provider`/`update_provider` persist
  the new fields; new SSE test runner (liveness once + per-model events).
- `api/routes/v1/admin_providers.py` — new SSE test endpoint.
- `services/provider_probe.py` — `probe_usage` + `"usage"` in `ProbeStepName`.

Frontend:
- `app/admin/models/new/page.tsx` — wizard route + step state machine.
- `components/admin/models/wizard/` — `PresetPicker`, `ConfigureStep`
  (+ `CapabilityEditor`), `ModelsStep`, `TestStep` (+ `ModelTestCard`,
  `LivenessRow`, SSE client), `WizardStepRail`.
- `components/admin/models/ReadinessBadge.tsx` — shared by detail + pickers.
- `ProviderDetail` / `ModelRow` — readiness dots + re-test (M7).
- `@cubeplex/core` `api/providers.ts` — helpers: `listPresets`,
  `createProvider`/`updateProvider` (extended bodies), `presaveLiveness`,
  `presaveTest`, `testModel(id, mid)`, `testStream(id, modelIds)` (SSE), `setModelEnabled`;
  types for `ProbeResult`/`ProbeStep`/`ProviderPreset`/readiness.
- Reuse: `ProviderList`, `ProviderCard`, `ProviderLogo`, `ModelFormDialog`,
  `ModelsToolbar`, the providers/models stores (extended).

## 10. Testing

- Backend: `probe_usage` unit (present→pass, absent→warn) + wired into Phase-B;
  capability round-trips through create/update (unit + e2e: create with
  capability → GET returns it); the SSE test endpoint (e2e: emits liveness +
  per-model + done events; monkeypatched orchestrators).
- Frontend: vitest for the wizard step-state machine, `ReadinessBadge` mapping,
  and the SSE test client (mock the event stream; assert rendered states +
  enable-on-pass). Playwright e2e happy path (pick preset → configure → import
  model → SSE test → save → appears in list) + readiness-disabled in a picker.
  Per the enforced layout: e2e under Playwright, unit under vitest.

## 11. Open questions

1. **Wizard abandonment cleanup.** Persist-after-Configure can leave stray
   untested providers (deletable). A later polish could auto-prune zero-model
   providers older than N.
2. **SSE endpoint placement.** A dedicated `…/test/stream` route vs reusing the
   agent SSE infra wrapper — decide in the plan against how cubeplex's existing
   SSE endpoints are structured.
3. **Capability editor depth.** v1 = JSON view + reasoning-template popover;
   a full per-field form can follow.
