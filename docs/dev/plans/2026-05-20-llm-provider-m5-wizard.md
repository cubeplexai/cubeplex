# LLM Provider Platform — M5 (Add Provider wizard) + M7 (polish) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the admin "Add Provider" wizard (full-page, 4 steps, streaming
test) plus per-model readiness display and provider-detail polish, on top of
the M3/M4/M6 backend.

**Architecture:** A full-page wizard at `/admin/models/new` persists the
provider after step 2, then creates models (`enabled=false`) and tests them via
a new SSE endpoint (liveness once + per-model events). Required backend gaps are
filled first (capability on create/update, `ModelCreate.enabled`, a `usage`
probe step, the SSE test endpoint). Readiness is server-derived; the UI renders
it via a shared `ReadinessBadge`.

**Spec:** `docs/dev/specs/2026-05-20-llm-provider-m5-wizard-design.md` (rev 3).
**Branch:** `feat/llm-provider-m5-wizard` (from integration `feat/llm-provider-platform`).
**Tech Stack:** FastAPI + StreamingResponse SSE, SQLModel; Next.js/React 19,
Zustand, shadcn/ui, `@lobehub/icons`, vitest + Playwright.

---

## File Structure

**Backend (`backend/cubebox/`):**
- `api/schemas/provider.py` — extend `ProviderCreate`/`ProviderUpdate`/`ModelCreate`; add `ProviderTestStreamRequest`.
- `services/provider_service.py` — persist new create/update fields; `run_test_stream` async generator.
- `api/routes/v1/admin_providers.py` — `POST /providers/{id}/test/stream` (SSE).
- `services/provider_probe.py` — `probe_usage` + `"usage"` in `ProbeStepName`.

**Frontend core (`frontend/packages/core/src/`):**
- `types/provider.ts` — add capability fields, `ProviderPreset`, `ProbeStep`, `ProbeResult`, `Readiness`, per-model read fields.
- `api/providers.ts` — `listPresets`, `presaveLiveness`, `presaveTest`, `testModel`, `setModelEnabled`; reuse `createProvider`/`updateProvider`.
- `api/providerTestStream.ts` — SSE client for the test stream (reuses `readLines`).

**Frontend web (`frontend/packages/web/`):**
- `app/admin/models/new/page.tsx` — wizard route + state machine.
- `components/admin/models/wizard/{WizardStepRail,PresetPicker,ConfigureStep,CapabilityEditor,ModelsStep,TestStep,ModelTestCard,LivenessRow}.tsx`.
- `components/admin/models/ReadinessBadge.tsx`.
- `components/admin/models/{ProviderDetail,ModelRow,ProviderLogo}.tsx` — M7 dots + re-test, lobehub icon.
- locale files under `packages/web/messages/` (or wherever next-intl messages live) — new keys.

---

## Task B1: Persist capability on provider create/update

**Files:**
- Modify: `backend/cubebox/api/schemas/provider.py`
- Modify: `backend/cubebox/services/provider_service.py`
- Test: `backend/tests/e2e/test_admin_providers_crud.py`

- [ ] **Step 1: Failing test — create persists capability + preset_slug**

Append to `tests/e2e/test_admin_providers_crud.py` (admin_client tuple fixture):
```python
@pytest.mark.asyncio
async def test_create_provider_persists_capability(admin_client):
    client, _ = admin_client
    cap = {"reasoning_off_payload": {"thinking": {"type": "disabled"}}}
    res = await client.post("/api/v1/admin/providers", json={
        "name": "cap-create-e2e", "provider_type": "anthropic-messages",
        "base_url": "https://example.com", "auth_type": "api_key", "api_key": "sk-x",
        "preset_slug": "anthropic", "capability": cap,
        "model_capability_overrides": {},
    })
    assert res.status_code == 201
    pid = res.json()["id"]
    got = (await client.get(f"/api/v1/admin/providers/{pid}")).json()
    assert got["preset_slug"] == "anthropic"
    assert got["capability"] == cap
    await client.delete(f"/api/v1/admin/providers/{pid}")
```

- [ ] **Step 2: Run — expect fail** (`capability` not echoed / dropped)

Run: `cd backend && set -a && source ../.worktree.env && set +a && uv run pytest tests/e2e/test_admin_providers_crud.py::test_create_provider_persists_capability -q`
Expected: FAIL (`preset_slug`/`capability` missing or null).

- [ ] **Step 3: Extend schemas**

In `schemas/provider.py`, add to `ProviderCreate` (after `extra_headers`):
```python
    preset_slug: str | None = Field(default=None, max_length=64)
    capability: dict[str, Any] = Field(default_factory=dict)
    model_capability_overrides: dict[str, Any] = Field(default_factory=dict)
```
Add the same three to `ProviderUpdate` as optional:
```python
    preset_slug: str | None = None
    capability: dict[str, Any] | None = None
    model_capability_overrides: dict[str, Any] | None = None
```

- [ ] **Step 4: Persist in `create_provider`**

In `services/provider_service.py` `create_provider`, add to the `Provider(...)`
constructor kwargs:
```python
            preset_slug=data.preset_slug,
            capability=data.capability,
            model_capability_overrides=data.model_capability_overrides,
```

- [ ] **Step 5: Persist in `update_provider`**

`update_provider` builds a field set from the non-None `ProviderUpdate` fields
(follow the existing pattern around the `for field in (...)` list). Add
`"preset_slug"`, `"capability"`, `"model_capability_overrides"` to that list so
they update when present.

- [ ] **Step 6: Run test — expect pass.** Then `uv run mypy cubebox/`.

- [ ] **Step 7: Commit**

```bash
git add backend/cubebox/api/schemas/provider.py backend/cubebox/services/provider_service.py backend/tests/e2e/test_admin_providers_crud.py
git commit -m "feat(provider): persist capability + preset_slug on create/update (M5)"
```

---

## Task B2: `ModelCreate.enabled`

**Files:**
- Modify: `backend/cubebox/api/schemas/provider.py`
- Modify: `backend/cubebox/services/provider_service.py` (`create_model`)
- Test: `backend/tests/e2e/test_admin_providers_crud.py`

- [ ] **Step 1: Failing test**

```python
@pytest.mark.asyncio
async def test_create_model_disabled(admin_client, seeded_provider_id):
    client, _ = admin_client
    res = await client.post(f"/api/v1/admin/providers/{seeded_provider_id}/models", json={
        "model_id": "m-disabled", "display_name": "M", "context_window": 8192,
        "max_tokens": 1024, "enabled": False,
    })
    assert res.status_code == 201
    assert res.json()["enabled"] is False
```
(Reuse or add a `seeded_provider_id` fixture creating one provider; see existing tests.)

- [ ] **Step 2: Run — expect fail** (model comes back `enabled=true`).

- [ ] **Step 3: Add field**

`ModelCreate` gains `enabled: bool = True` (default keeps existing callers
unchanged). In `create_model`, pass `enabled=data.enabled` into the `Model(...)`
constructor.

- [ ] **Step 4: Run — expect pass. mypy.**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(provider): ModelCreate.enabled so wizard can create disabled models (M5)"
```

---

## Task B3: `usage` advisory probe step

**Files:**
- Modify: `backend/cubebox/services/provider_probe.py`
- Test: `backend/tests/unit/test_provider_probe.py`

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.asyncio
async def test_probe_usage_pass_when_usage_present():
    from cubebox.services.provider_probe import probe_usage
    ev = type("E", (), {"type": "done", "usage": {"input_tokens": 10, "output_tokens": 3}})()
    step = await probe_usage(_StubProvider(events=[ev]), model_id="m")
    assert step.name == "usage" and step.status == "pass"

@pytest.mark.asyncio
async def test_probe_usage_warn_when_absent():
    from cubebox.services.provider_probe import probe_usage
    ev = type("E", (), {"type": "text_delta", "delta": "hi"})()
    step = await probe_usage(_StubProvider(events=[ev]), model_id="m")
    assert step.status == "warn"
```

- [ ] **Step 2: Run — expect ImportError on `probe_usage`.**

- [ ] **Step 3: Implement**

In `provider_probe.py`: add `"usage"` to `ProbeStepName`. Leave `_BLOCKING_STEPS`
unchanged. Add:
```python
def _extract_usage(events: list) -> dict | None:
    """Find a token-usage block on any drained event (cubepi exposes usage on
    the terminal/message event). Returns the usage dict or None."""
    for evt in events:
        usage = getattr(evt, "usage", None)
        if usage:
            return usage if isinstance(usage, dict) else getattr(usage, "__dict__", None)
    return None


async def probe_usage(provider: Any, *, model_id: str) -> ProbeStep:
    """Advisory: did the response carry a parseable token-usage structure?
    cubebox cost tracking records zeros without it. Own minimal stream."""
    try:
        events, _ = await _drain_stream(provider, model_id, thinking="off",
                                        prompt="hi", max_output=16)
    except Exception as exc:
        return ProbeStep(name="usage", status="warn", error=_probe_error(exc))
    usage = _extract_usage(events)
    if usage:
        return ProbeStep(name="usage", status="pass",
                         detail=f"in {usage.get('input_tokens','?')} / out {usage.get('output_tokens','?')}")
    return ProbeStep(name="usage", status="warn", detail="no usage block → cost recorded as zero")
```
Add `probe_usage(...)` to the Phase-B `asyncio.gather` in `run_model_probe` and
append its result to `steps`.

- [ ] **Step 4: Run all probe tests — expect green. mypy.**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(probe): advisory usage step — verify token-usage structure (M5)"
```

---

## Task B4: SSE test endpoint (liveness once + per-model events)

**Files:**
- Modify: `backend/cubebox/api/schemas/provider.py` (request body)
- Modify: `backend/cubebox/services/provider_service.py` (`run_test_stream`)
- Modify: `backend/cubebox/api/routes/v1/admin_providers.py` (route)
- Test: `backend/tests/e2e/test_admin_llm_endpoints.py`

- [ ] **Step 1: Request body schema**

In `schemas/provider.py`:
```python
class ProviderTestStreamRequest(BaseModel):
    """Explicit model ids to test (wizard models are enabled=false, so we do
    NOT filter by enabled here)."""
    model_ids: list[str] = Field(min_length=1)
```

- [ ] **Step 2: Failing test (SSE emits liveness + per-model + done)**

```python
@pytest.mark.asyncio
async def test_test_stream_emits_events(admin_client, monkeypatch):
    client, _ = admin_client
    from cubebox.services import provider_probe
    async def stub_liveness(*a, **k):
        return provider_probe.ProbeStep(name="liveness", status="pass", latency_ms=10)
    async def stub_model(*a, **k):
        return provider_probe.ProbeResult(overall="pass", blocking_failed=False,
            steps=[provider_probe.ProbeStep(name="reasoning", status="pass")])
    monkeypatch.setattr(provider_probe, "run_liveness", stub_liveness)
    monkeypatch.setattr(provider_probe, "run_model_probe", stub_model)
    # seed provider + 1 model (via admin API) → pid, mid (model db id)
    ...
    res = await client.post(f"/api/v1/admin/providers/{pid}/test/stream",
                            json={"model_ids": [mid]})
    assert res.status_code == 200
    body = res.text
    assert "event: liveness" in body and "event: model" in body and "event: done" in body
```

- [ ] **Step 3: Run — expect 404 (route absent).**

- [ ] **Step 4: Implement `run_test_stream` (async generator of SSE bytes)**

In `services/provider_service.py`:
```python
async def run_test_stream(self, provider_id: str, model_ids: list[str]):
    """Yield SSE frames: one `liveness`, one `model` per id, then `done`.
    Persists liveness on the provider and last_test_* per model (reuses the
    same persistence as run_model_test_saved)."""
    provider = await self.get_provider(provider_id)
    cfg = await self._config_from_provider(provider)
    factory = self._provider_factory_from_config(cfg)
    models = {m.id: m for m in await self._models.list_all_for_provider(provider_id)}

    liveness = await provider_probe.run_liveness(
        provider_factory=factory, model_id=models[model_ids[0]].model_id)
    await self._persist_provider_liveness(provider, liveness)
    yield _sse("liveness", liveness.model_dump(mode="json"))
    if liveness.status != "pass":
        yield _sse("done", {"liveness": "fail"}); return

    fingerprint = capability_fingerprint(provider.capability or {},
                                         provider.model_capability_overrides or {})
    for mid in model_ids:
        model = models[mid]
        cap = self._resolve_capability(cfg, model.model_id)
        result = await provider_probe.run_model_probe(
            provider_factory=factory, model_id=model.model_id, capability=cap)
        await self._persist_model_test(model, result, fingerprint)
        yield _sse("model", {"model_db_id": mid, **result.model_dump(mode="json")})
    yield _sse("done", {})
```
Add a module helper:
```python
def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
```
(Use `list_all_for_provider` — a repo method that does NOT filter by enabled; if
only an enabled-filtered `list_by_provider` exists, add the unfiltered variant.)

- [ ] **Step 5: Add the route**

In `admin_providers.py`:
```python
@router.post("/providers/{provider_id}/test/stream")
async def test_provider_stream(
    provider_id: str, body: ProviderTestStreamRequest, *, request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    svc = await _svc(user, session, request)
    return StreamingResponse(svc.run_test_stream(provider_id, body.model_ids),
                             media_type="text/event-stream")
```

- [ ] **Step 6: Run test — expect pass. mypy. Run `-k provider_probe` green.**

- [ ] **Step 7: Commit**

```bash
git commit -am "feat(admin): SSE provider test endpoint — liveness + per-model events (M5)"
```

---

## Task F1: core types

**Files:** Modify `frontend/packages/core/src/types/provider.ts`

- [ ] **Step 1: Add types** (no test; consumed by later tasks)

```ts
export type WireApi = 'openai-completions' | 'openai-responses' | 'anthropic-messages'
export type Readiness = 'ready' | 'degraded' | 'stale' | 'provider_error' | 'model_error' | 'unavailable'

export interface ProviderPreset {
  slug: string; display_name: string; short_name: string
  category: 'saas' | 'oss-framework' | 'custom'; description: string
  logo: string | null; api: WireApi; base_url: string
  capability: Record<string, unknown>
  model_capability_overrides: Record<string, Record<string, unknown>>
  default_models: Array<{ model_id: string; display_name: string; context_window: number; max_tokens: number; input_modalities: string[]; reasoning: boolean }>
}
export interface ProbeStep { name: string; status: 'pass'|'fail'|'skip'|'warn'; latency_ms?: number|null; detail?: string; error?: { type: string; message: string; raw_status?: number|null } | null }
export interface ProbeResult { overall: 'pass'|'fail'|'warn'|'unavailable'; blocking_failed: boolean; steps: ProbeStep[] }
```
Extend `ProviderCreate`/`ProviderUpdate` with `preset_slug?`, `capability?`,
`model_capability_overrides?`; `ModelCreate` with `enabled?: boolean`; the
per-model read type with `last_test_status?`, `last_test_at?`,
`last_test_summary?`, `readiness?: Readiness`; `Provider` with `last_liveness_status?` etc.

- [ ] **Step 2: Build core** `cd frontend && pnpm --filter @cubebox/core build` — expect clean. **Commit.**

```bash
git commit -am "feat(core): provider preset + probe + readiness types (M5)"
```

---

## Task F2: core api helpers + SSE client

**Files:**
- Modify: `frontend/packages/core/src/api/providers.ts`
- Create: `frontend/packages/core/src/api/providerTestStream.ts`
- Test: `frontend/packages/core/src/api/__tests__/providerTestStream.test.ts`

- [ ] **Step 1: api helpers** — append to `providers.ts`:
```ts
export async function listPresets(client: ApiClient): Promise<ProviderPreset[]> {
  const res = await client.get('/api/v1/admin/llm/presets')
  if (!res.ok) throw await toApiError(res); return res.json()
}
export async function presaveLiveness(client: ApiClient, body: { api: string; base_url: string; api_key?: string|null; capability: Record<string,unknown>; model_capability_overrides?: Record<string,unknown>; model_id: string }): Promise<ProbeStep> {
  const res = await client.post('/api/v1/admin/providers/liveness', body)
  if (!res.ok) throw await toApiError(res); return res.json()
}
export async function setModelEnabled(client: ApiClient, providerId: string, mid: string, enabled: boolean): Promise<Model> {
  const res = await client.patch(`/api/v1/admin/providers/${providerId}/models/${mid}`, { enabled })
  if (!res.ok) throw await toApiError(res); return res.json()
}
```

- [ ] **Step 2: Failing test for the SSE client**

```ts
import { describe, it, expect } from 'vitest'
import { parseTestStream } from '../providerTestStream'
describe('parseTestStream', () => {
  it('yields liveness then model then done', async () => {
    const text = 'event: liveness\ndata: {"name":"liveness","status":"pass"}\n\nevent: model\ndata: {"model_db_id":"m1","overall":"pass","blocking_failed":false,"steps":[]}\n\nevent: done\ndata: {}\n\n'
    const events: string[] = []
    for await (const e of parseTestStream(streamFromString(text))) events.push(e.event)
    expect(events).toEqual(['liveness', 'model', 'done'])
  })
})
```
(`streamFromString` — a tiny helper building a `ReadableStream` of the encoded text; include it in the test file.)

- [ ] **Step 3: Implement `providerTestStream.ts`** (reuse the `readLines` pattern from `api/runStreams.ts`):
```ts
export interface TestStreamEvent { event: 'liveness'|'model'|'done'; data: any }
export async function* parseTestStream(stream: ReadableStream<Uint8Array>): AsyncGenerator<TestStreamEvent> {
  const reader = stream.getReader(); const dec = new TextDecoder(); let buf = ''
  let curEvent = ''
  for (;;) {
    const { value, done } = await reader.read(); if (done) break
    buf += dec.decode(value, { stream: true })
    let i: number
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trimEnd(); buf = buf.slice(i + 1)
      if (line.startsWith('event: ')) curEvent = line.slice(7)
      else if (line.startsWith('data: ')) yield { event: curEvent as any, data: JSON.parse(line.slice(6)) }
    }
  }
}
export async function startTestStream(client: ApiClient, providerId: string, modelIds: string[]): Promise<ReadableStream<Uint8Array>> {
  const res = await client.postRaw(`/api/v1/admin/providers/${providerId}/test/stream`, { model_ids: modelIds }, { Accept: 'text/event-stream' })
  if (!res.ok || !res.body) throw await toApiError(res); return res.body
}
```
(If `ApiClient` lacks a raw/streaming POST, add `postRaw` that returns the
`Response` without `.json()` — mirror how `runStreams.ts` issues its fetch.)

- [ ] **Step 4: Run vitest — expect pass. Build core.**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(core): provider preset/liveness helpers + SSE test-stream client (M5)"
```

---

## Task F3: `@lobehub/icons` + ProviderLogo

**Files:** `frontend/packages/web/package.json`, `components/admin/models/ProviderLogo.tsx`

- [ ] **Step 1:** `cd frontend && pnpm --filter @cubebox/web add @lobehub/icons` (pnpm, not npm).
- [ ] **Step 2:** Update `ProviderLogo` to render `<ProviderIcon provider={logo} size={size} type="color" />` when a lobehub `logo` id is given, else the existing fallback (gear) when null. Keep the current props.
- [ ] **Step 3:** `pnpm --filter @cubebox/web type-check`. **Commit.**

```bash
git commit -am "feat(web): lobehub provider icons in ProviderLogo (M5)"
```

---

## Task F4: ReadinessBadge

**Files:** Create `components/admin/models/ReadinessBadge.tsx`; Test `__tests__/ReadinessBadge.test.tsx`

- [ ] **Step 1: Failing test** — `ready`→green dot + label "可用"; `model_error`→red + reason tooltip; `degraded`→amber. Assert the rendered dot class + accessible label per readiness value.
- [ ] **Step 2: Implement** a pure presentational component:
```tsx
const MAP: Record<Readiness, { dot: string; key: string }> = {
  ready: { dot: 'bg-green-500', key: 'ready' }, degraded: { dot: 'bg-amber-500', key: 'degraded' },
  stale: { dot: 'bg-amber-400', key: 'stale' }, provider_error: { dot: 'bg-red-500', key: 'providerError' },
  model_error: { dot: 'bg-red-500', key: 'modelError' }, unavailable: { dot: 'bg-zinc-400', key: 'unavailable' },
}
export function ReadinessBadge({ readiness }: { readiness: Readiness }) {
  const t = useTranslations('adminModels.readiness'); const m = MAP[readiness]
  return <span title={t(m.key)} className="inline-flex items-center gap-1.5">
    <span className={`size-2 rounded-full ${m.dot}`} /><span className="text-xs text-muted-foreground">{t(m.key)}</span></span>
}
```
- [ ] **Step 3: vitest pass. Commit.**

```bash
git commit -am "feat(web): ReadinessBadge (M5 §4.7)"
```

---

## Task F5: Wizard route + step state machine + step rail

**Files:** Create `app/admin/models/new/page.tsx`, `components/admin/models/wizard/WizardStepRail.tsx`; Test `__tests__/wizardMachine.test.ts` + a small `wizardMachine.ts` pure reducer.

- [ ] **Step 1: Failing test for the pure step machine** (`components/admin/models/wizard/wizardMachine.ts`): a reducer over `{ step, presetSlug, providerId, selectedModelIds }` with actions `pickPreset`, `providerCreated`, `next`, `back`. Assert: can't advance past Configure until `providerId` set; `back` decrements; `pickPreset` sets slug + carries capability.
- [ ] **Step 2: Implement the reducer** (pure, no React) — full switch over the actions above.
- [ ] **Step 3: Run vitest — pass.**
- [ ] **Step 4: Build the route** `app/admin/models/new/page.tsx`: `'use client'`, uses the reducer for `step`, renders `<WizardStepRail step=… />` + the active step component (Tasks F6–F9), a footer with Back/Next/Cancel. On Cancel/finish `router.push('/admin/models')`.
- [ ] **Step 5:** `WizardStepRail` — the 4 numbered steps (Preset/Configure/Models/Test), active/done styling per the design mock (primary dot active, check done).
- [ ] **Step 6: type-check. Commit.**

```bash
git commit -am "feat(web): add-provider wizard route + step machine + rail (M5)"
```

---

## Task F6: Step 1 — PresetPicker

**Files:** Create `components/admin/models/wizard/PresetPicker.tsx`; Test `__tests__/PresetPicker.test.tsx`

- [ ] **Step 1: Failing test** — given a mocked `listPresets` returning 2 presets, renders 2 cards; clicking one calls `onPick(preset)`; search filters by name; category tabs filter by `category`.
- [ ] **Step 2: Implement** — fetch via `listPresets(client)` on mount; grid of cards (brand `ProviderLogo` by `preset.logo`, name, `preset.api`, reasoning-shape badge); search input + category tabs (All/Hosted=saas/Self-hosted=oss-framework/Custom); selected card highlighted; "Next" enabled when one selected. Matches the hi-fi mock.
- [ ] **Step 3: vitest pass. type-check. Commit.**

```bash
git commit -am "feat(web): wizard step 1 PresetPicker (M5)"
```

---

## Task F7: Step 2 — ConfigureStep + CapabilityEditor (persists provider)

**Files:** Create `components/admin/models/wizard/{ConfigureStep,CapabilityEditor}.tsx`; Test `__tests__/ConfigureStep.test.tsx`

- [ ] **Step 1: Failing test** — preset auto-fills display name/base URL/provider_type; API key required (Next disabled until filled); clicking Next calls `createProvider` with `{ name, provider_type: preset.api, base_url, api_key, preset_slug, capability, model_capability_overrides }` and reports the new id via `onProviderCreated(id)`.
- [ ] **Step 2: Implement `ConfigureStep`** — controlled form seeded from the picked preset; "Advanced" expander renders `<CapabilityEditor value … onChange … />`; Next → `createProvider(client, body)` then `onProviderCreated(p.id)`. Optional "Test connection" button calls `presaveLiveness` for a quick check (non-blocking).
- [ ] **Step 3: Implement `CapabilityEditor`** — a JSON `<textarea>` bound to the capability object (parse on change, show parse errors) + a "use a template" popover for custom presets that injects a vendor reasoning block. Keep v1 simple (JSON view) per spec §11.
- [ ] **Step 4: vitest pass. type-check. Commit.**

```bash
git commit -am "feat(web): wizard step 2 Configure + CapabilityEditor; persists provider (M5)"
```

---

## Task F8: Step 3 — ModelsStep

**Files:** Create `components/admin/models/wizard/ModelsStep.tsx`; Test `__tests__/ModelsStep.test.tsx`

- [ ] **Step 1: Failing test** — renders the preset's `default_models` as a checkbox list (checked by default); "add custom" appends a row; Next calls `createModel` once per checked model with `enabled: false`, collecting the created model db ids into `onModelsCreated(ids)`.
- [ ] **Step 2: Implement** — checkbox list from `preset.default_models` + custom add (reuse `ModelFormDialog` fields or a compact inline row); Next loops `createModel(client, providerId, { ...model, enabled: false })`, gathers ids.
- [ ] **Step 3: vitest pass. type-check. Commit.**

```bash
git commit -am "feat(web): wizard step 3 ModelsStep — import disabled models (M5)"
```

---

## Task F9: Step 4 — TestStep (SSE) + ModelTestCard + LivenessRow

**Files:** Create `components/admin/models/wizard/{TestStep,ModelTestCard,LivenessRow}.tsx`; Test `__tests__/TestStep.test.tsx`

- [ ] **Step 1: Failing test** — given a mocked `startTestStream`/`parseTestStream` yielding a `liveness` pass then a `model` pass for each id then `done`: renders the liveness row green, a `ModelTestCard` per model with its badge, and enables "Save" once liveness passed + ≥1 model `overall` ∈ {pass,warn}; on Save calls `setModelEnabled` for passing models and `onFinish()`.
- [ ] **Step 2: Implement `TestStep`** — on mount (or "Run test" click) call `startTestStream(client, providerId, modelIds)` → iterate `parseTestStream`; update liveness state on `liveness`, push/replace a per-model result on `model`, mark complete on `done`. Footer Save gating per the test. On Save: for each model whose `overall` ∈ {pass,warn}, `setModelEnabled(client, providerId, mid, true)`, then `onFinish()` → back to list.
- [ ] **Step 3: Implement `LivenessRow`** (status + latency) and `ModelTestCard` (5 sub-check chips from `ProbeResult.steps` + outcome badge derived from `overall`: pass→可用/warn→降级/fail→无法启用/unavailable→无法启用; reason + 重测/移除 on failure).
- [ ] **Step 4: vitest pass. type-check. Commit.**

```bash
git commit -am "feat(web): wizard step 4 TestStep with SSE results + enable-on-pass (M5)"
```

---

## Task F10: M7 — detail page readiness dots + re-test

**Files:** Modify `components/admin/models/{ProviderDetail,ModelRow}.tsx`; Test `__tests__/ModelRow.test.tsx`

- [ ] **Step 1: Failing test** — `ModelRow` given a model with `readiness:'degraded'` renders `<ReadinessBadge readiness="degraded">`; a "re-test" button calls `testModel(client, providerId, mid)` and updates the row.
- [ ] **Step 2: Implement** — `ProviderDetail` header shows provider liveness dot from `last_liveness_status`; each `ModelRow` shows `<ReadinessBadge>` from the model's `readiness`; re-test buttons: provider liveness (`presave... no` → use saved `POST /{id}/liveness` via a `checkLiveness` helper), single model (`testModel`), "test all" (open the SSE stream over the provider's model ids). Re-fetch the provider after to refresh readiness.
- [ ] **Step 3: vitest pass. type-check. Commit.**

```bash
git commit -am "feat(web): provider detail readiness dots + re-test (M7)"
```

---

## Task F11: page-configured-only + readiness in pickers + i18n

**Files:** `app/admin/models/page.tsx`, any model-picker component, locale message files.

- [ ] **Step 1:** Verify `/admin/models` list/detail read only `fetchProviders`/`fetchProvider` (configured rows) — presets are fetched ONLY in the wizard. Add a test asserting the list page never calls `listPresets`.
- [ ] **Step 2:** In any existing model picker that lists provider models, render unusable models (`provider_error`/`model_error`/`unavailable`) disabled with `<ReadinessBadge>` + reason (not hidden). Test the disabled state.
- [ ] **Step 3:** Add all new i18n keys under `adminModels.*` (wizard step labels, readiness labels, test sub-check names, buttons) to every locale file; run the i18n parity check.
- [ ] **Step 4:** `pnpm --filter @cubebox/web lint && pnpm --filter @cubebox/web type-check`. **Commit.**

```bash
git commit -am "feat(web): configured-only page + readiness in pickers + i18n (M5/M7)"
```

---

## Task F12: Final sweep + PR

- [ ] **Step 1:** Backend: `cd backend && set -a && source ../.worktree.env && set +a && uv run pytest -k "provider or probe" -q` + `uv run mypy cubebox/` — green.
- [ ] **Step 2:** Frontend: `cd frontend && pnpm --filter @cubebox/core build && pnpm -w lint && pnpm -w type-check && pnpm -w vitest run` — green. Playwright happy-path spec (pick preset → configure → import model → SSE test → save → appears in list) green.
- [ ] **Step 3:** `git push -u origin feat/llm-provider-m5-wizard`.
- [ ] **Step 4:** `gh pr create --base feat/llm-provider-platform` (base is the **integration branch**, not main) with summary + test plan; tag `@codex`.

---

## Self-review notes

- Spec coverage: wizard 4 steps (F5–F9), readiness §4.7 (F4/F10/F11), page rule §4.8 (F11), M7 detail (F10), backend gaps (B1–B4), usage probe (B3), SSE (B4). All mapped.
- The SSE endpoint takes explicit `model_ids` (not enabled-filter) — B4 + F9.
- Save gating keys off `ProbeResult.overall` (pass/warn), enable-on-save — F9.
- PR base = `feat/llm-provider-platform` (stacked), not main — F12.
