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

**Backend (`backend/cubeplex/`):**
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
- Modify: `backend/cubeplex/api/schemas/provider.py`
- Modify: `backend/cubeplex/services/provider_service.py`
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
Also add `preset_slug: str | None = None` to **`ProviderOut`** (it is needed in
the GET response the Step-1 test asserts; `capability`/`model_capability_overrides`
are already on `ProviderOut` from M3).

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

- [ ] **Step 5b: Emit `preset_slug` in `_provider_out`**

In `admin_providers.py`, `_provider_out(...)` builds `ProviderOut`. Add
`preset_slug=p.preset_slug` to its kwargs (alongside the existing
`capability`/`model_capability_overrides` it already emits). Also add
`preset_slug?: string | null` to the frontend `Provider` type in
`packages/core/src/types/provider.ts` (done in Task F1, noted here for
traceability).

- [ ] **Step 6: Run test — expect pass.** Then `uv run mypy cubeplex/`.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/api/schemas/provider.py backend/cubeplex/services/provider_service.py backend/tests/e2e/test_admin_providers_crud.py
git commit -m "feat(provider): persist capability + preset_slug on create/update (M5)"
```

---

## Task B2: `ModelCreate.enabled`

**Files:**
- Modify: `backend/cubeplex/api/schemas/provider.py`
- Modify: `backend/cubeplex/services/provider_service.py` (`create_model`)
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

`ModelCreate` gains `enabled: bool = True`. **Decision (vs spec §3.2):** keep
the schema default `True` so existing callers and the seeder are unchanged and
**no DB-default migration is needed** (the column default stays `True`); the
**wizard explicitly sends `enabled=false`** when creating models (Task F8). The
spec's "wizard creates disabled models" is satisfied by the caller, not by
flipping the schema/DB default. In `create_model`, pass `enabled=data.enabled`
into the `Model(...)` constructor.

- [ ] **Step 4: Run — expect pass. mypy.**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(provider): ModelCreate.enabled so wizard can create disabled models (M5)"
```

---

## Task B3: `usage` advisory probe step

**Files:**
- Modify: `backend/cubeplex/services/provider_probe.py`
- Test: `backend/tests/unit/test_provider_probe.py`

- [ ] **Step 1: Failing tests**

```python
from cubepi.providers.base import Usage
# _UsageStub is defined in Step 3 (a _StubProvider whose .result() returns an
# AssistantMessage with the given usage).

@pytest.mark.asyncio
async def test_probe_usage_pass_when_usage_present():
    from cubeplex.services.provider_probe import probe_usage
    step = await probe_usage(_UsageStub(usage=Usage(input_tokens=10, output_tokens=3),
                                        events=[]), model_id="m")
    assert step.name == "usage" and step.status == "pass"

@pytest.mark.asyncio
async def test_probe_usage_warn_when_absent():
    from cubeplex.services.provider_probe import probe_usage
    step = await probe_usage(_UsageStub(usage=None, events=[]), model_id="m")
    assert step.status == "warn"
```

- [ ] **Step 2: Run — expect ImportError on `probe_usage`.**

- [ ] **Step 3: Implement**

In `provider_probe.py`: add `"usage"` to `ProbeStepName`. Leave `_BLOCKING_STEPS`
unchanged.

**Key fact (verified):** cubepi does NOT put usage on a `StreamEvent`. Usage
lives on the `AssistantMessage` returned by `MessageStream.result()`
(`cubepi/providers/base.py`: `MessageStream.result() -> AssistantMessage`;
`AssistantMessage.usage: Usage | None`; `Usage(input_tokens, output_tokens,
cache_read_tokens, cache_write_tokens)`). So `probe_usage` opens its own stream,
drains it, then awaits `stream.result()` and inspects `.usage`:
```python
async def probe_usage(provider: Any, *, model_id: str) -> ProbeStep:
    """Advisory: did the response carry a parseable token-usage structure?
    cubeplex cost tracking records zeros without it. Own minimal stream."""
    try:
        stream = await asyncio.wait_for(
            provider.stream(
                model=Model(id=model_id, provider="probe", context_window=8192, max_tokens=16),
                messages=[UserMessage(content=[TextContent(text="hi")])],
                options=StreamOptions(thinking="off"),
            ),
            timeout=15.0,
        )
        async for _ in stream:  # drain
            pass
        msg = await stream.result()
    except Exception as exc:
        return ProbeStep(name="usage", status="warn", error=_probe_error(exc))
    usage = msg.usage
    if usage is not None and (usage.input_tokens or usage.output_tokens):
        return ProbeStep(name="usage", status="pass",
                         detail=f"in {usage.input_tokens} / out {usage.output_tokens}")
    return ProbeStep(name="usage", status="warn", detail="no usage block → cost recorded as zero")
```
Add `probe_usage(...)` to the Phase-B `asyncio.gather` in `run_model_probe` and
append its result to `steps`.

Update the Step-1 unit tests to use a stub whose `.result()` returns an
`AssistantMessage` with / without `usage` (NOT a fake `evt.usage`):
```python
class _UsageStub(_StubProvider):
    def __init__(self, *, usage=None, **kw):
        super().__init__(**kw); self._usage = usage
    async def stream(self, *a, **k):
        s = await super().stream(*a, **k)
        async def result():
            from cubepi.providers.base import AssistantMessage
            return AssistantMessage(role="assistant", content=[], usage=self._usage)
        s.result = result  # type: ignore[attr-defined]
        return s
```
(present `Usage(input_tokens=10, output_tokens=3)` → pass; `usage=None` → warn.)

- [ ] **Step 4: Run all probe tests — expect green. mypy.**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(probe): advisory usage step — verify token-usage structure (M5)"
```

---

## Task B4: SSE test endpoint (liveness once + per-model events)

**Files:**
- Modify: `backend/cubeplex/api/schemas/provider.py` (request body)
- Modify: `backend/cubeplex/repositories/model.py` (add `list_all_for_provider`)
- Modify: `backend/cubeplex/services/provider_service.py` (`run_test_stream`)
- Modify: `backend/cubeplex/api/routes/v1/admin_providers.py` (route)
- Test: `backend/tests/e2e/test_admin_llm_endpoints.py`

> **Naming:** the request carries **model DB ids** (`Model.id`, the `mdl_…`
> primary keys), NOT the vendor `model_id` strings. The field is `model_db_ids`
> throughout (schema, service, frontend) to avoid confusion with `Model.model_id`.

- [ ] **Step 0: Add the unfiltered repo method**

`ModelRepository` only has `list_by_provider` (enabled-filtered; uses
`self.session`, no org field — provider-scoping is enough). Add the unfiltered
twin (mirror `list_by_provider` exactly, minus `.where(Model.enabled)`):
```python
async def list_all_for_provider(self, provider_id: str) -> list[Model]:
    """All models for a provider, including disabled (wizard models are
    enabled=false). Mirrors list_by_provider minus the enabled filter."""
    stmt = (
        select(Model)
        .where(Model.provider_id == provider_id)  # type: ignore[arg-type]
        .order_by(Model.model_id)
    )
    result = await self.session.execute(stmt)
    return list(result.scalars().all())
```

- [ ] **Step 1: Request body schema**

In `schemas/provider.py`:
```python
class ProviderTestStreamRequest(BaseModel):
    """Explicit model DB ids to test (wizard models are enabled=false, so we do
    NOT filter by enabled here)."""
    model_db_ids: list[str] = Field(min_length=1)
```

- [ ] **Step 2: Failing test (SSE emits liveness + per-model + done)**

```python
@pytest.mark.asyncio
async def test_test_stream_emits_events(admin_client, monkeypatch):
    client, _ = admin_client
    from cubeplex.services import provider_probe
    async def stub_liveness(*a, **k):
        return provider_probe.ProbeStep(name="liveness", status="pass", latency_ms=10)
    async def stub_model(*a, **k):
        return provider_probe.ProbeResult(overall="pass", blocking_failed=False,
            steps=[provider_probe.ProbeStep(name="reasoning", status="pass")])
    monkeypatch.setattr(provider_probe, "run_liveness", stub_liveness)
    monkeypatch.setattr(provider_probe, "run_model_probe", stub_model)
    # full setup (no placeholders): create a provider + one model via the admin API
    pres = await client.post("/api/v1/admin/providers", json={
        "name": "sse-test-e2e", "provider_type": "anthropic-messages",
        "base_url": "https://example.com", "auth_type": "api_key", "api_key": "sk-x",
    })
    pid = pres.json()["id"]
    mres = await client.post(f"/api/v1/admin/providers/{pid}/models", json={
        "model_id": "claude-x", "display_name": "X", "context_window": 8192,
        "max_tokens": 1024, "enabled": False,
    })
    mid = mres.json()["id"]  # model DB id (mdl_…)
    res = await client.post(f"/api/v1/admin/providers/{pid}/test/stream",
                            json={"model_db_ids": [mid]})
    assert res.status_code == 200
    body = res.text
    assert "event: liveness" in body and "event: model" in body and "event: done" in body
    await client.delete(f"/api/v1/admin/providers/{pid}")
```

- [ ] **Step 3: Run — expect 404 (route absent).**

- [ ] **Step 4: Implement `run_test_stream` (async generator of SSE bytes)**

In `services/provider_service.py`:
```python
async def run_test_stream(self, provider_id: str, model_db_ids: list[str]):
    """Yield SSE frames: one `liveness`, one `model` per id, then `done`.
    Persists liveness on the provider and last_test_* per model (reuses the
    same persistence as run_model_test_saved). Caller (route) has already
    preflighted that the provider and all model_db_ids exist."""
    provider = await self.get_provider(provider_id)
    cfg = await self._config_from_provider(provider)
    factory = self._provider_factory_from_config(cfg)
    models = {m.id: m for m in await self._models.list_all_for_provider(provider_id)}

    liveness = await provider_probe.run_liveness(
        provider_factory=factory, model_id=models[model_db_ids[0]].model_id)
    await self._persist_provider_liveness(provider, liveness)
    yield _sse("liveness", liveness.model_dump(mode="json"))
    if liveness.status != "pass":
        yield _sse("done", {"liveness": "fail"}); return

    fingerprint = capability_fingerprint(provider.capability or {},
                                         provider.model_capability_overrides or {})
    for db_id in model_db_ids:
        model = models[db_id]
        cap = self._resolve_capability(cfg, model.model_id)
        result = await provider_probe.run_model_probe(
            provider_factory=factory, model_id=model.model_id, capability=cap)
        await self._persist_model_test(model, result, fingerprint)
        yield _sse("model", {"model_db_id": db_id, **result.model_dump(mode="json")})
    yield _sse("done", {})

async def preflight_test_stream(self, provider_id: str, model_db_ids: list[str]) -> None:
    """Raise ProviderNotFoundError / ModelNotFoundError synchronously before the
    route returns a StreamingResponse (so errors are real HTTP codes, not a
    half-open stream)."""
    await self.get_provider(provider_id)  # raises if missing
    known = {m.id for m in await self._models.list_all_for_provider(provider_id)}
    missing = [i for i in model_db_ids if i not in known]
    if missing:
        raise ModelNotFoundError(f"models not found: {missing}")
```
Add a module helper (and add `import json` to `provider_service.py`'s imports —
it is not currently imported there):
```python
def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
```

- [ ] **Step 5: Add the route**

In `admin_providers.py`:
```python
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

@router.post("/providers/{provider_id}/test/stream")
async def test_provider_stream(
    provider_id: str, body: ProviderTestStreamRequest, *, request: Request,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    svc = await _svc(user, session, request)
    try:  # preflight synchronously → real HTTP errors, not a half-open stream
        await svc.preflight_test_stream(provider_id, body.model_db_ids)
    except ProviderNotFoundError as e:
        raise HTTPException(status_code=404, detail="provider_not_found") from e
    except ModelNotFoundError as e:
        raise HTTPException(status_code=404, detail="model_not_found") from e
    return StreamingResponse(
        svc.run_test_stream(provider_id, body.model_db_ids),
        media_type="text/event-stream", headers=_SSE_HEADERS,
    )
```
(Mirror the headers used by the existing SSE route in `conversations.py`. Add
`from fastapi.responses import StreamingResponse` to `admin_providers.py` — it
currently imports only from `fastapi`.)

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

// cubepi catalog AuthSpec is a nested object (verified): mode is one of
// api_key|bearer|none|oauth|iam; header_name/header_prefix accompany it.
export interface AuthSpec { mode: 'api_key' | 'bearer' | 'none' | 'oauth' | 'iam'; header_name?: string; header_prefix?: string }
export interface ProviderPreset {
  slug: string; display_name: string; short_name: string
  category: 'saas' | 'oss-framework' | 'custom'; description: string
  logo: string | null; api: WireApi; base_url: string
  auth: AuthSpec   // gate the key input on mode; block oauth/iam in the wizard
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

- [ ] **Step 2: Build core** `cd frontend && pnpm --filter @cubeplex/core build` — expect clean. **Commit.**

```bash
git commit -am "feat(core): provider preset + probe + readiness types (M5)"
```

---

## Task F2: core api helpers + SSE client

**Files:**
- Modify: `frontend/packages/core/src/api/client.ts` (add `postRaw`)
- Modify: `frontend/packages/core/src/api/providers.ts`
- Create: `frontend/packages/core/src/api/providerTestStream.ts`
- Modify: `frontend/packages/core/src/api/index.ts` (barrel export the new file + helpers)
- Test: `frontend/packages/core/src/api/__tests__/providerTestStream.test.ts`

- [ ] **Step 0: `postRaw` on `ApiClient`** — `client.ts` has `get/post/patch/del`
  but no streaming POST. `createApiClient` returns an object whose methods close
  over local `doFetch` + `buildHeaders` (there is no `this`). Add `postRaw` to
  the **`ApiClient` interface** (line ~21) and implement it in the returned
  object the same way `post` is implemented:
```ts
// in interface ApiClient:
postRaw(path: string, body: unknown, headers?: Record<string, string>): Promise<Response>
// in the object returned by createApiClient (mirror the existing post()):
postRaw(path, body, headers) {
  return doFetch(path, {
    method: 'POST', body: JSON.stringify(body),
    headers: buildHeaders('POST', { 'Content-Type': 'application/json', ...(headers ?? {}) }),
  })
},
```

- [ ] **Step 1: api helpers** — append to `providers.ts` (ALL helpers the rest
  of the plan references — `listPresets`, `presaveLiveness`, `presaveTest`,
  `testModel`, `checkLiveness`, `setModelEnabled`):
```ts
export async function listPresets(client: ApiClient): Promise<ProviderPreset[]> {
  const res = await client.get('/api/v1/admin/llm/presets')
  if (!res.ok) throw await toApiError(res); return res.json()
}
type LivenessBody = { api: string; base_url: string; api_key?: string|null; capability: Record<string,unknown>; model_capability_overrides?: Record<string,unknown>; model_id: string }
export async function presaveLiveness(client: ApiClient, body: LivenessBody): Promise<ProbeStep> {
  const res = await client.post('/api/v1/admin/providers/liveness', body)
  if (!res.ok) throw await toApiError(res); return res.json()
}
export async function presaveTest(client: ApiClient, body: LivenessBody): Promise<ProbeResult> {
  const res = await client.post('/api/v1/admin/providers/test', body)
  if (!res.ok) throw await toApiError(res); return res.json()
}
export async function checkLiveness(client: ApiClient, providerId: string, modelId: string): Promise<ProbeStep> {
  // saved-provider liveness re-check; body carries the vendor model_id string
  const res = await client.post(`/api/v1/admin/providers/${providerId}/liveness`, { model_id: modelId })
  if (!res.ok) throw await toApiError(res); return res.json()
}
export async function testModel(client: ApiClient, providerId: string, modelDbId: string): Promise<ProbeResult> {
  const res = await client.post(`/api/v1/admin/providers/${providerId}/models/${modelDbId}/test`, {})
  if (!res.ok) throw await toApiError(res); return res.json()
}
export async function setModelEnabled(client: ApiClient, providerId: string, modelDbId: string, enabled: boolean): Promise<Model> {
  const res = await client.patch(`/api/v1/admin/providers/${providerId}/models/${modelDbId}`, { enabled })
  if (!res.ok) throw await toApiError(res); return res.json()
}
```

- [ ] **Step 1b: barrel export** — in `api/index.ts`, export the new helpers and
  re-export `./providerTestStream` (`parseTestStream`, `startTestStream`,
  `TestStreamEvent`). Confirm `@cubeplex/core`'s public index re-exports them too
  if components import from the package root.

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
export async function startTestStream(client: ApiClient, providerId: string, modelDbIds: string[]): Promise<ReadableStream<Uint8Array>> {
  const res = await client.postRaw(`/api/v1/admin/providers/${providerId}/test/stream`, { model_db_ids: modelDbIds }, { Accept: 'text/event-stream' })
  if (!res.ok || !res.body) throw await toApiError(res); return res.body
}
```
(`postRaw` is added in Step 0 above.)

- [ ] **Step 4: Run vitest — expect pass. Build core.**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(core): provider preset/liveness helpers + SSE test-stream client (M5)"
```

---

## Task F3: `@lobehub/icons` + ProviderLogo

**Files:** `frontend/packages/web/package.json`, `components/admin/models/ProviderLogo.tsx`

- [ ] **Step 1:** `cd frontend && pnpm --filter @cubeplex/web add @lobehub/icons` (pnpm, not npm).
- [ ] **Step 2:** Update `ProviderLogo` to render `<ProviderIcon provider={logo} size={size} type="color" />` when a lobehub `logo` id is given, else the existing fallback (gear) when null. Keep the current props.
- [ ] **Step 3:** `pnpm --filter @cubeplex/web type-check`. **Commit.**

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

- [ ] **Step 1: Failing tests** — (a) for an `api_key`/`bearer` preset: auto-fills display name/base URL/provider_type, **API key required** (Next disabled until filled), and Next calls `createProvider` with `{ name, provider_type: preset.api, base_url, api_key, auth_type, preset_slug, capability, model_capability_overrides }` then `onProviderCreated(id)`; (b) for a `none`-auth preset: **no key input shown** and Next is enabled without a key (sends `auth_type: "none"`, no `api_key`); (c) for an `oauth`/`iam` preset: Next disabled with the unsupported note. (`auth_type` per the §-mapping below.)
- [ ] **Step 2: Implement `ConfigureStep`** — controlled form seeded from the picked preset; "Advanced" expander renders `<CapabilityEditor value … onChange … />`; Next → `createProvider(client, body)` then `onProviderCreated(p.id)`. Optional "Test connection" button calls `presaveLiveness` for a quick check (non-blocking).

  **Auth gating (`preset.auth.mode`):** show the API-key input only when mode is
  `api_key` or `bearer` (required then). When `none`, hide the key input (send
  `auth_type: "none"`, no key). When `oauth` or `iam`, the wizard cannot
  complete the connection (OAuth provider auth is a parent-spec non-goal, IAM
  unsupported) — disable Next with an inline note "OAuth/IAM presets aren't
  supported yet" (the preset can still be picked to inspect, but not saved).
  **Map `preset.auth.mode` → backend `auth_type` explicitly** — the names
  differ: cubepi uses `bearer`, backend `_validate_auth_creds` expects
  `bearer_token`. Map `api_key → "api_key"`, `bearer → "bearer_token"`,
  `none → "none"`; `oauth`/`iam` are blocked (never sent). Key input required
  for `api_key`/`bearer`.
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
- [ ] **Step 2: Implement `TestStep`** — `modelDbIds` (the model DB ids from F8's `onModelsCreated`) drive the run. On mount (or "Run test" click) call `startTestStream(client, providerId, modelDbIds)` → iterate `parseTestStream`; update liveness state on `liveness`, push/replace a per-model result keyed by `model_db_id` on `model`, mark complete on `done`. Footer Save gating per the test. On Save: for each model whose `overall` ∈ {pass,warn}, `setModelEnabled(client, providerId, modelDbId, true)`, then `onFinish()` → back to list.
- [ ] **Step 3: Implement `LivenessRow`** (status + latency) and `ModelTestCard` (5 sub-check chips from `ProbeResult.steps` + outcome badge derived from `overall`: pass→可用/warn→降级/fail→无法启用/unavailable→无法启用; reason + 重测/移除 on failure).
- [ ] **Step 4: vitest pass. type-check. Commit.**

```bash
git commit -am "feat(web): wizard step 4 TestStep with SSE results + enable-on-pass (M5)"
```

---

## Task F10: M7 — detail page readiness dots + re-test

**Files:** Modify `components/admin/models/{ProviderDetail,ModelRow}.tsx`; Test `__tests__/ModelRow.test.tsx`

- [ ] **Step 1: Failing test** — `ModelRow` given a model with `readiness:'degraded'` renders `<ReadinessBadge readiness="degraded">`; a "re-test" button calls `testModel(client, providerId, mid)` and updates the row.
- [ ] **Step 2: Implement** — `ProviderDetail` header shows provider liveness dot from `last_liveness_status`; each `ModelRow` shows `<ReadinessBadge>` from the model's `readiness`. Re-test buttons:
  - **provider liveness** — `checkLiveness(client, id, modelId)`; the saved
    `/{id}/liveness` endpoint **requires a `model_id`** (vendor string), so pass
    the first configured model's `model_id`. **Disable this button when the
    provider has no models** (nothing to issue the cheap call against).
  - **single model** — `testModel(client, id, model.id)` (model DB id).
  - **test all** — open the SSE stream (`startTestStream`) over the provider's
    model DB ids (all of them, enabled or not).
  Re-fetch the provider (`fetchProvider`) after each to refresh `readiness`/dots.
- [ ] **Step 3: vitest pass. type-check. Commit.**

```bash
git commit -am "feat(web): provider detail readiness dots + re-test (M7)"
```

---

## Task F11: page-configured-only + readiness in pickers + i18n

**Files:** `app/admin/models/page.tsx`, any model-picker component, locale message files.

- [ ] **Step 1:** Verify `/admin/models` list/detail read only `fetchProviders`/`fetchProvider` (configured rows) — presets are fetched ONLY in the wizard. Add a test asserting the list page never calls `listPresets`.
- [ ] **Step 2: Model-picker readiness (concrete).** The existing
  `frontend/packages/web/hooks/useAllModels.ts` currently **filters disabled
  models out** and its option type has no `enabled`/`readiness`. Changes:
  - Extend the option type with `enabled: boolean` and `readiness: Readiness`
    (sourced from each provider's per-model read fields).
  - Stop filtering disabled models out — pass them through.
  - In the consuming picker (`OrgLLMSettingsCard`'s model `<Select>`), render a
    not-usable option (`provider_error`/`model_error`/`unavailable`, or
    `enabled === false`) with `<ReadinessBadge>` + reason and
    `pointer-events: none` / `aria-disabled` (visible, not selectable — not
    hidden). `ready`/`degraded`/`stale` stay selectable.
  - Test: an unusable model renders disabled with its badge; a `ready` one is
    selectable.
- [ ] **Step 3:** Add all new i18n keys under `adminModels.*` (wizard step labels, readiness labels, test sub-check names, buttons) to every locale file; run the i18n parity check.
- [ ] **Step 4:** `pnpm --filter @cubeplex/web lint && pnpm --filter @cubeplex/web type-check`. **Commit.**

```bash
git commit -am "feat(web): configured-only page + readiness in pickers + i18n (M5/M7)"
```

---

## Task F12: Final sweep + PR

- [ ] **Step 1:** Backend: `cd backend && set -a && source ../.worktree.env && set +a && uv run pytest -k "provider or probe" -q` + `uv run mypy cubeplex/` — green.
- [ ] **Step 2:** Frontend: `cd frontend && pnpm --filter @cubeplex/core build && pnpm -w lint && pnpm -w type-check && pnpm -w vitest run` — green. Playwright happy-path spec (pick preset → configure → import model → SSE test → save → appears in list) green.
- [ ] **Step 3:** `git push -u origin feat/llm-provider-m5-wizard`.
- [ ] **Step 4:** `gh pr create --base feat/llm-provider-platform` (base is the **integration branch**, not main) with summary + test plan; tag `@codex`.

---

## Self-review notes

- Spec coverage: wizard 4 steps (F5–F9), readiness §4.7 (F4/F10/F11), page rule §4.8 (F11), M7 detail (F10), backend gaps (B1–B4), usage probe (B3), SSE (B4). All mapped.
- The SSE endpoint takes explicit `model_db_ids` (not enabled-filter) — B4 + F9.
- Save gating keys off `ProbeResult.overall` (pass/warn), enable-on-save — F9.
- PR base = `feat/llm-provider-platform` (stacked), not main — F12.
