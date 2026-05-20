# LLM Provider Platform — M5 (Add Provider wizard) + M7 (polish) design

**Status:** Draft for review
**Author:** xfgong
**Date:** 2026-05-20
**Parent spec:** `docs/dev/specs/2026-05-19-llm-provider-platform-design.md` (§4.5, §4.7, §4.8, M5/M7)
**Builds on:** integration branch `feat/llm-provider-platform` (M3+M4+M6 backend, PR #124, draft)

This is the frontend slice plus a small backend addition. It turns the
M3/M4/M6 backend (capability schema, two-phase probe + test endpoints,
readiness derivation, task-model routing) into the admin UI: an Add-Provider
wizard, readiness in model pickers, the configured-only page rule, and the
provider detail polish.

## 1. Scope

In scope:
- **M5** — Add-Provider wizard (spec §4.5); readiness in model-selection UIs
  (§4.7); the page-shows-configured-only rule (§4.8).
- **M7** — provider detail page: provider liveness dot + per-model status
  dots + re-test buttons; i18n sweep of all new strings.
- **Small backend addition** — a new advisory `usage` probe step (see §5).

Out of scope: any further backend probe/endpoint redesign; new presets
(catalog is cubepi's); multi-tenant/workspace provider scoping (still
org/admin only, per parent spec §5).

## 2. Decisions settled in brainstorming

1. **Full-page route, not modal/drawer.** The wizard lives at
   `/admin/models/new` inside the admin shell (top bar + admin sub-nav +
   main). The 4 steps + preset grid + per-model test results need the room;
   a popup cramps them. Matches the parent spec's scope-isolated
   "own route per page" rule.
2. **Provider row is persisted after step 2 (Configure), not at the end.**
   The wizard creates the provider row when the admin leaves Configure, so
   steps 3 (Models) and 4 (Test) operate on a *saved* provider and can reuse
   the existing saved endpoints (`/providers/{id}/models`,
   `/providers/{id}/liveness`, `/providers/{id}/models/{mid}/test`). Rationale
   below (§4). A wizard abandoned after step 2 leaves an untested provider
   with no enabled models — harmless, and deletable from the list.
3. **No backend SSE needed for the Test step.** The frontend orchestrates the
   live feel by calling existing JSON endpoints in sequence: one liveness
   call, then one per-model test call per selected model, rendering each
   model card as its result returns. Sub-checks within a single model arrive
   together (that model's probe runs them server-side and returns one
   `ProbeResult`). Per-model granularity is enough; we avoid an SSE rebuild.
4. **Test step shows the two-phase probe + readiness verbatim** (see §6 mock):
   Phase A liveness (provider), then per-model Phase B with 5 sub-checks —
   Reasoning (blocking) + Temperature / Tools / Streaming / **Usage** (all
   advisory) — and a per-model readiness badge (可用 / 降级 / 无法启用).
   Save is gated at the model grain.

## 3. Routes & page structure

- `/admin/models` (existing) — provider list + detail. **Shows only
  configured rows** (§4.8); presets never appear here. "Add provider" button
  → `/admin/models/new`.
- `/admin/models/new` (new) — the wizard (steps 1–4).
- Provider detail stays on `/admin/models` (selected provider in the detail
  pane) — gains M7 status dots + re-test (see §7).

## 4. Wizard flow & persistence

Step rail: **1 Preset · 2 Configure · 3 Models · 4 Test.**

1. **Preset** — grid of cubepi presets (`GET /admin/llm/presets`), brand icon
   via `@lobehub/icons`, name, wire-api, reasoning-shape badge; search +
   category filter (Hosted / Self-hosted / Custom). Selecting one carries its
   `capability`, `base_url`, `api`, default model list forward. A "Custom
   (OpenAI-compatible / Anthropic)" tile starts from an empty descriptor.
2. **Configure** — preset auto-fills display name, base URL, `provider_type`
   (wire literal), capability. API key is the only required free input.
   "Advanced" expander = capability editor (JSON view + per-field form;
   for custom presets, "use a template" copies a vendor's reasoning block).
   **On "Next", the provider row is created** (`POST /admin/providers`) and
   the wizard switches to working against `{id}`.
3. **Models** — import preset defaults (checkbox list) or add custom; each
   creates a `models` row (`POST /admin/providers/{id}/models`),
   `enabled=false` until it passes its probe.
4. **Test** — runs the probe (see §6), enables models that pass, finishes the
   wizard back to the list with the new provider selected.

**Why persist after Configure (decision 2):** the per-model probe + the
"a wired model is a tested model" save-gating both key off saved model rows
and the `{id}` endpoints. Persisting early lets steps 3–4 reuse them directly
instead of inventing pre-save multi-model test plumbing. The existing pre-save
`/providers/liveness` + `/providers/test` (one-model) endpoints remain for a
quick "test before you commit a key" affordance on the Configure step, but the
authoritative per-model testing happens post-persist on step 4.

## 5. Backend addition — `usage` probe step

A new advisory Phase-B sub-check in `cubebox/services/provider_probe.py`:

- `ProbeStepName` gains `"usage"`. `probe_usage(...)` inspects the streamed
  response (reuse the events already drained by the reasoning/streaming step)
  for a parseable token-usage structure — prompt/completion tokens, and cache
  read/write when present — i.e. what `billing_llm_events` / cost tracking
  consumes.
- **Advisory**: `pass` when a usage block is found; `warn` when absent
  ("no usage block → cost recorded as zero"). Never blocks (not in
  `_BLOCKING_STEPS`). Runs in the Phase-B parallel gather alongside
  temperature/tools/streaming.
- Honors the "skip optional probes" toggle (Open Q#3) like the other advisory
  steps.

No new column: the usage result rides inside the model's `last_test_summary`
ProbeResult JSON. Readiness is unaffected (a `warn` → `degraded`, which is
already covered).

## 6. Test step UX (step 4)

Mock: `step4-test-v2.html` (brainstorm session). Behavior:

- **Phase A liveness** once → green "可达 · Nms" row. On fail → all models
  render `provider_error`, Phase B skipped, save blocked.
- **Phase B** per selected model, rendered as it returns: a card with the
  model name + 5 sub-check chips (status icon each) + a readiness badge:
  - all pass → **可用 (ready)**; an advisory `warn` (e.g. Tools or Usage) →
    **降级 (degraded)**, still enabled; Reasoning `fail` → **无法启用
    (model_error)** with reason + "重测 / 移除"; vendor `model_not_found` →
    **无法启用 (unavailable)**.
- A "跳过可选探针" checkbox (default on for re-test, off for first run).
- Footer "保存 Provider (N 个模型可用)" enabled once liveness passed and ≥1
  model is `ready`/`degraded`.
- Frontend orchestration: `POST /{id}/liveness` → then per enabled model
  `POST /{id}/models/{mid}/test`, sequential, rendering each as it resolves.

## 7. Readiness surfaces (§4.7) + detail polish (M7)

- **`ReadinessBadge`** component — maps the server-derived `readiness`
  (`ready`/`degraded`/`stale`/`provider_error`/`model_error`/`unavailable`)
  to dot color + label + tooltip. The server already returns `readiness` per
  model on `GET /admin/providers/{id}` — the UI never re-derives.
- **Model-selection UIs read readiness** (§4.7): the task-routing dropdowns
  (admin settings) and any model picker show `ready`/`degraded`/`stale` as
  selectable; `provider_error`/`model_error`/`unavailable` as **disabled +
  reason + fix affordance** (not hidden).
- **Provider detail (M7):** provider header shows the liveness dot
  (`last_liveness_status`); each model row shows its readiness dot; re-test
  buttons at both grains — provider liveness re-check (`POST /{id}/liveness`),
  single-model (`POST /{id}/models/{mid}/test`), and "test all"
  (`POST /{id}/test`). Status dots come straight from the persisted columns
  (no probing on page load).

## 8. Page rule (§4.8)

The `/admin/models` list + detail render **only configured rows**
(`GET /admin/providers` + each provider's models). The preset catalog is
fetched **only** inside the wizard. A configured-but-broken model
(`provider_error`/`model_error`/`unavailable`) stays on the page disabled with
a reason; an un-added preset model never appears there.

## 9. Frontend architecture

`@lobehub/icons` added to `packages/web` (`pnpm add`), rendered via
`<ProviderIcon provider={preset.logo} size={…} type="color" />`; null logo →
generic fallback (the existing `ProviderLogo` gains this).

New / changed:
- `packages/web/app/admin/models/new/page.tsx` — wizard route + step state
  machine.
- `components/admin/models/wizard/` — `PresetPicker`, `ConfigureStep`
  (+ `CapabilityEditor`), `ModelsStep`, `TestStep` (+ `ModelTestCard`,
  `LivenessRow`), `WizardStepRail`.
- `components/admin/models/ReadinessBadge.tsx` — shared by detail + pickers.
- `ProviderDetail` / `ModelRow` — add readiness dots + re-test buttons (M7).
- `@cubebox/core`: `api/providers.ts` gains helpers for the new endpoints
  (`listPresets`, `checkLiveness`, `testModel(id, mid)`, `testAllModels(id)`,
  `presaveLiveness`, `presaveTest`); types for `ProbeResult`/`ProbeStep`/
  `ProviderPreset`/readiness. (The old `testConnection`/`testModel` helpers
  removed in slice-2 are replaced by these.)
- Reuse existing: `ProviderList`, `ProviderCard`, `ProviderLogo`,
  `ModelFormDialog`, `ModelsToolbar`, the providers/models Zustand stores
  (extended, not replaced).

## 10. Testing

- Frontend: vitest for the wizard step-state machine, `ReadinessBadge`
  mapping, and the test-step orchestration (mock the api client; assert the
  liveness→per-model call sequence and rendered states). Playwright e2e for
  the happy path (pick preset → configure → import a model → test → save →
  appears in list) and the readiness-disabled case in a picker. Per the
  enforced layout, e2e under `frontend` Playwright; unit under vitest.
- Backend: a unit test for `probe_usage` (usage block present → pass; absent →
  warn) + that it's wired into the Phase-B gather and honors skip-optional.

## 11. Open questions

1. **Wizard abandonment cleanup.** Persisting after Configure can leave
   stray untested providers. Acceptable for v1 (deletable from the list); a
   later polish could auto-prune providers with zero models older than N.
2. **Capability editor depth.** v1 = JSON view + the reasoning-template
   popover for custom presets. A full per-field form for every capability
   field can follow if needed; most admins use a preset and never open it.
