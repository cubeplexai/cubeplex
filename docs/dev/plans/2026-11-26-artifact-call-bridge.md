# Interactive Artifacts — Artifact Call Bridge

- **Goal**: Let agent-authored artifact HTML call the platform at runtime — starting
  with a one-shot call to the organization's configured model — so an artifact can
  accept user input, send it to a model, and render the answer, without leaving the
  preview panel.
- **Second goal (drives the design)**: adding another callable capability later
  (knowledge-base search, memory recall, workspace metadata) must be a single
  registry entry plus its handler module — no route changes, no frontend changes.
- **Architecture**: a server-side **function registry** (`services/artifact_bridge/`)
  exposing named, schema-validated functions over one generic route pair
  (`GET .../functions`, `POST .../call/{fn}`). The first registered function is
  `chat`, a one-shot LLM call built from the same primitives as
  `services/conversation_title.py`. Artifact HTML reaches the route with a plain
  same-origin `fetch` (the preview iframe is `allow-scripts allow-same-origin`
  and served from the app origin), so there is no frontend bridge and no new
  frontend source.
- **Tech stack**: Python 3.13 / FastAPI / SQLModel / pytest / respx (backend),
  cubeloop providers, Redis rate limiting, Docusaurus (English + `zh-Hans` docs).

---

## Decisions

1. **Callable surface is a registry, not a single endpoint.** Considered: (a) a
   dedicated `POST .../chat` route; (b) a generic `POST .../call/{fn}` dispatching
   through a registry; (c) routing artifact calls through the existing agent-tool
   registry in `agents/actions/`. Chose (b). (a) makes every future capability a
   new route + new client contract; (c) couples browser-called functions to
   agent-tool semantics (deferred tool groups, `ScopeContext` role gating) that
   browser calls do not need. (b) keeps one auditable entry point, one rate-limit
   path, one CSRF decision, and makes a new capability a file + a registration.
2. **Member-only, but built for a share-token front door later.** `ArtifactCallContext`
   is constructed by the route from the authenticated request; a future public-share
   route builds the same context from a share token and reuses `dispatch()`
   unchanged. No `public: bool` flag is stored on functions — the front door
   decides.
3. **One-shot Q&A, no history, nothing persisted.** The artifact supplies a single
   prompt; the response is returned and never written to the messages table. Keeps
   artifact interaction out of the conversation transcript and out of the agent loop.
4. **Non-streaming JSON for v1.** One POST, one `{"result": {...}}` body. Streaming
   would add an SSE route, a Next route-handler proxy (mirroring
   `app/api/v1/ws/[wsId]/conversations/[id]/messages/route.ts`), and a chunk
   protocol in agent-authored JS — deferred until latency is a real complaint.
5. **CSRF exemption for the call path instead of teaching artifact JS the cookie
   name.** `auth.csrf_cookie_name` is configurable and per-worktree overrides exist
   via `NEXT_PUBLIC_CSRF_COOKIE_NAME`; agent-authored HTML cannot read a build-time
   env var, so a hardcoded cookie name would 403 on renamed deployments. The call
   endpoint is read-only (no state change), so double-submit CSRF adds nothing there.
   The exemption is a strict path regex, not a prefix.
6. **Model selection uses the organization default preset**, not a new task route.
   `resolve_task_preset` keys are for internal utility tasks (title, summarize,
   compaction, memory). Artifact Q&A is user-facing, so it follows the org default
   the user configured. A dedicated `artifact` `TaskKey` is a deferred follow-up.
7. **Artifact JS derives its own ids from the preview URL path.** The preview route
   already encodes workspace, conversation, artifact, and version in the path
   (`.../artifacts/{id}/preview/v{n}/{file}`), so no injected bootstrap script and no
   change to the serving path (which returns object-store bytes with an `immutable`
   cache header).
8. **No call on load.** Artifact thumbnails render with `sandbox="allow-scripts"`
   (opaque origin, no cookies — see `ArtifactHtmlThumb.tsx`), so a load-time fetch
   would 401 there. The authoring contract requires calling only from a user-initiated
   handler.

---

## Unit 1: Function registry and dispatch

### Files
- New: `backend/cubeplex/services/artifact_bridge/__init__.py` — re-export the public
  surface (`ArtifactCallContext`, `ArtifactFunction`, `register_function`,
  `get_function`, `catalogue`, `dispatch`, error types).
- New: `backend/cubeplex/services/artifact_bridge/types.py` — context + function
  dataclasses and the error hierarchy (`ArtifactFunctionNotFound`,
  `ArtifactCallInvalidInput`, `ArtifactCallRateLimited`, `ArtifactCallFailed`).
- New: `backend/cubeplex/services/artifact_bridge/registry.py` — the function table,
  registration helper, lookup, and catalogue serialisation (name, description, JSON
  schema from `input_model`).
- New: `backend/cubeplex/services/artifact_bridge/dispatch.py` — validate args, apply
  rate limit and timeout, invoke the handler, translate errors.
- New: `backend/cubeplex/services/artifact_bridge/functions/__init__.py` — imports each
  function module so registration happens on package import (same pattern as
  `cubeplex.parsers.registry` discovery and `agents/actions/registry.py`).
- Modify: `backend/config.yaml` — add the `artifact_bridge` block (see Unit 5).

### Interfaces
```python
# types.py
@dataclass(frozen=True)
class ArtifactCallContext:
    org_id: str
    workspace_id: str
    user_id: str
    role: Role
    conversation_id: str
    artifact: Artifact
    session: AsyncSession
    encryption_backend: EncryptionBackend | None
    tracer: Any | None

@dataclass(frozen=True)
class ArtifactFunction:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ArtifactCallContext, Any], Awaitable[dict[str, Any]]]
    rate_limit_per_minute: int | None = None  # None -> artifact_bridge config default
    timeout_seconds: float | None = None

# registry.py
def register_function(fn: ArtifactFunction) -> None: ...
def get_function(name: str) -> ArtifactFunction: ...  # ArtifactFunctionNotFound
def catalogue() -> list[dict[str, Any]]: ...  # [{"name", "description", "input_schema"}]

# dispatch.py
async def dispatch(ctx: ArtifactCallContext, name: str, raw_args: Mapping[str, Any]) -> dict[str, Any]: ...
```

### Core logic
- `dispatch()` resolves the function, validates `raw_args` against `input_model`
  (Pydantic `ValidationError` → `ArtifactCallInvalidInput`), enforces a per-user,
  per-artifact, per-function Redis counter (`{redis key prefix}:artbridge:{user}:
  {artifact}:{fn}:{minute}`), wraps the handler in `asyncio.timeout`, and logs one
  line per call (function, artifact, latency, outcome). Handler output is validated
  as a `dict` and returned under `{"result": ...}` by the route.
- Registration is declarative: importing `functions/<name>.py` calls
  `register_function(...)` at module scope. Adding a capability later is a new module
  in `functions/` plus one import line in `functions/__init__.py`.
- Handlers receive the scoped context, so a future `memory.recall` or
  `knowledge.search` function has session, org/workspace/user, conversation, and
  artifact available without touching the route or the dispatcher.

### Tests
- `backend/tests/unit/test_artifact_bridge_registry.py`: unknown function raises
  `ArtifactFunctionNotFound`; duplicate registration is rejected; catalogue schema is
  JSON-serialisable and marks required fields.
- `backend/tests/unit/test_artifact_bridge_dispatch.py`: invalid args →
  `ArtifactCallInvalidInput`; handler exception → `ArtifactCallFailed`; timeout path
  raises `ArtifactCallFailed`; rate-limit counter denies the N+1th call inside a
  minute and allows it again in the next minute (fake Redis handle).

---

## Unit 2: `chat` function (one-shot model call)

### Files
- New: `backend/cubeplex/services/artifact_bridge/functions/chat.py`.
- Modify: `backend/cubeplex/services/artifact_bridge/functions/__init__.py` — import it.

### Interfaces
```python
class ChatInput(BaseModel):
    prompt: str = Field(min_length=1)  # max length enforced from config

ARTIFACT_CHAT_SYSTEM: str  # short, artifact-name-scoped system prompt
```

### Core logic
- Mirror `services/conversation_title.py`: `load_llm_snapshot(session, org_id,
  encryption_backend)` → `resolve_model_preset(snap, None)` →
  `build_chain_model(snap, preset)`, clamp `bound.spec.max_tokens` to the configured
  ceiling, then either `tracer.oneshot(model=..., operation="artifact_chat",
  metadata={"artifact_id", "conversation_id", "org_id"}).generate(...)` when a tracer
  is present or `bound.provider.stream(...)` directly when it is not. Collect
  `text_delta` events; raise `ArtifactCallFailed` on `error` events.
- `cache_policy_factory=None` semantics as in title-gen: a one-shot call has no prior
  context, so no cache breakpoints are inserted (see
  `backend/docs/prompt-cache-discipline.md`).
- Prompt content is capped by `artifact_bridge.max_prompt_chars` at validation time
  (config value injected into the model field's `max_length` at import).
- Returns `{"text": ...}`. No DB writes.

### Tests
- `backend/tests/e2e/test_artifact_bridge_chat.py`: monkeypatch the module-level
  `build_chain_model` with a fake bound model whose `provider.stream` yields
  `text_delta` then `done`; assert `200` + `{"result": {"text": ...}}`, that the
  artifact name reached the system prompt, and that `max_tokens` was clamped.
  A provider `error` event surfaces as `502` and does not leak the raw error text.

---

## Unit 3: Routes

### Files
- Modify: `backend/cubeplex/api/routes/v1/artifacts.py` — add
  `GET /{artifact_id}/functions` and `POST /{artifact_id}/call/{fn_name}` next to the
  existing handlers, reusing `_require_conversation` and the artifact-ownership gate
  already used by `preview_artifact_file`.

### Interfaces
```python
@router.get("/{artifact_id}/functions")
async def list_artifact_functions(...) -> dict[str, object]:  # {"functions": [...]}

@router.post("/{artifact_id}/call/{fn_name}")
async def call_artifact_function(
    artifact_id: str, fn_name: str, body: CallBody, request: Request, ...
) -> dict[str, object]:  # {"result": {...}}
```

### Core logic
- Both handlers run `_require_conversation`, load the artifact through
  `ArtifactRepository`, and 404 when the artifact does not belong to the conversation
  (identical to the preview handler).
- `POST` builds `ArtifactCallContext` from `RequestContext` +
  `request.app.state.encryption_backend` + `request.app.state.tracer` and delegates to
  `dispatch()`. Error mapping: `ArtifactFunctionNotFound` → 404,
  `ArtifactCallInvalidInput` → 400 with the validation message, `ArtifactCallRateLimited`
  → 429 + `Retry-After`, `ArtifactCallFailed` → 502.
- Body model is `{"args": {}}` (defaults to `{}`) so the function schema owns its own
  fields; unknown top-level keys are ignored by the route.

### Tests
- `backend/tests/e2e/test_artifact_bridge_routes.py`: 404 for an artifact belonging to
  another conversation; 404 for an unknown function name; 400 for a bad `args` payload;
  `GET .../functions` lists `chat` with a schema; a stranger's workspace is 404.
- `backend/tests/e2e/test_artifact_bridge_chat.py` covers the happy path end to end.

---

## Unit 4: CSRF exemption for the read-only call path

### Files
- Modify: `backend/cubeplex/api/middleware/csrf.py` — add an exact-match exemption
  regex and a short module comment explaining why a read-only POST is exempt.

### Core logic
- Exempt only `POST` requests whose path matches
  `^/api/v1/ws/[^/]+/conversations/[^/]+/artifacts/[^/]+/call/[^/]+$`. Everything else
  keeps double-submit enforcement. The exemption is a full-match regex, so a future
  mutating `POST` on a neighbouring path stays protected.

### Tests
- `backend/tests/unit/test_csrf_middleware.py`: exempt path with an auth cookie and no
  `X-CSRF-Token` passes; the same path with a trailing segment or a sibling path is
  rejected; mutating requests elsewhere still require the header.

---

## Unit 5: Limits configuration

### Files
- Modify: `backend/config.yaml` — add:
  ```yaml
  artifact_bridge:
    enabled: true
    max_prompt_chars: 8000
    max_output_tokens: 1024
    timeout_seconds: 60
    rate_limit_per_minute: 20
  ```
- Modify: `backend/config.test.yaml` — same keys, `rate_limit_per_minute: 1000` so the
  suite is not throttled.

### Core logic
- `dispatch()` reads the config once per call (cheap `config.get`), so tests can
  monkeypatch `config` values without reimport. `enabled: false` makes `POST .../call/*`
  return 503 and `GET .../functions` return an empty list.

### Tests
- Covered by the dispatch unit tests plus one route test with `enabled` forced false.

---

## Unit 6: Agent authoring contract

### Files
- Modify: `backend/cubeplex/prompts/artifacts.py` — append an "Interactive artifacts"
  section to `ARTIFACT_PROMPT` (appended, not inserted, to keep the prompt-cache prefix
  stable — see `backend/docs/prompt-cache-discipline.md`).

### Core logic
- State the endpoint shape, the fact that ids come from the preview URL path, that no
  CSRF header is needed, and that calls must be user-initiated (never on load). Include
  one copy-pasteable snippet so the model does not reinvent the URL parsing:
  ```js
  const m = location.pathname.match(/\/ws\/([^/]+)\/conversations\/([^/]+)\/artifacts\/([^/]+)\//)
  const r = await fetch(`/api/v1/ws/${m[1]}/conversations/${m[2]}/artifacts/${m[3]}/call/chat`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ args: { prompt: text } }) })
  ```
- Document `GET .../functions` as the discovery surface so future capabilities are
  discoverable without a prompt change.

### Tests
- No unit test; asserted indirectly by the `chat` e2e test (system prompt contents) and
  by a manual check in the verification section.

---

## Unit 7: Docs (English + 简体中文, same PR)

### Files
- Modify: `docs/site/docs/guides/conversations/artifacts.md` — new "Interactive
  artifacts" section: what it is, how to ask for it, the security/session boundaries
  (panel only; thumbnails and public shares cannot call), and the cost note (each
  interaction is a model call, rate limited per artifact).
- Modify: `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/guides/conversations/artifacts.md`
  — equivalent section with identical behaviour, commands, and limits.
- Screenshot: reuse the existing panel screenshot; if a new one is needed, add the
  placeholder in **both** languages rather than only English.

---

## Verification

Run from the worktree backend (`../.worktree.env` sourced as needed):

```bash
uv run pytest tests/unit/test_artifact_bridge_registry.py tests/unit/test_artifact_bridge_dispatch.py \
  tests/unit/test_csrf_middleware.py --no-cov -q
uv run pytest tests/e2e/test_artifact_bridge_routes.py tests/e2e/test_artifact_bridge_chat.py --no-cov -q
```

Manual check (worktree ports from `.worktree.env`): ask the agent for a small artifact
with an input box and a button, open the panel, click, and confirm the answer renders.
Also confirm the gallery thumbnail still renders and does not fire a request.

### Local environment notes (this machine had no infra)
- Postgres/Redis/rustfs were started with rootless podman:
  `cubeplex/postgresql-pgroonga-pgvector:18.2-pgroonga4.0.6-pgvector0.8.2` (password
  `testpass`, matches `config.test.yaml`), `redis:7-alpine` (no password),
  `rustfs/rustfs` with `rustfsadmin/rustfsadmin` and bucket `cubeplex-test`.
- Per-worktree test DB created with `podman exec cubeplex-pg psql` (no `psql` client on
  the host), then `CUBEPLEX_DATABASE__NAME=cubeplex_test_<slug>
  CUBEPLEX_DATABASE__PASSWORD=testpass uv run alembic upgrade head`.
- `backend/.env` (gitignored) carries `CUBEPLEX_AUTH__VAULT_KEY`,
  `CUBEPLEX_SANDBOX__{DOMAIN,IMAGE,API_KEY}`, and dummy
  `CUBEPLEX_E2E_LLM_{BASE_URL,API_KEY,MODEL_ID}` — `config.yaml`/`config.test.yaml`
  interpolate those with `@format {env[...]}`, so they must exist.

---

## Deferred (record in `todo.md`, do not build now)

- **Public share front door**: build an `ArtifactCallContext` from a share token and
  reuse `dispatch()`; requires a token-scoped route and a share-page bridge
  (`sandbox="allow-scripts"` is an opaque origin there).
- **Streaming**: SSE endpoint + Next route-handler proxy + chunk protocol.
- **Artifact content in context**: load the entry file into the system prompt with a
  token budget so the model can answer about its own content.
- **`artifact` task preset**: add `TaskKey.artifact` in `llm/snapshot_schema.py` plus
  the `PresetEditor.tsx` task-routing label in both languages.
- **Additional functions**: `memory.recall`, `knowledge.search` — each a module in
  `functions/` plus one import.
- **Per-artifact call budget** (daily cap) beyond the per-minute limit.

---

## References

- One-shot LLM pattern: `backend/cubeplex/services/conversation_title.py`
- Artifact scoping gate: `backend/cubeplex/api/routes/v1/artifacts.py` (`_require_conversation`)
- Model resolution: `backend/cubeplex/llm/snapshot.py`, `resolver.py`, `builder.py`
- Tracing: `cubeloop.tracing.tracer.Tracer.oneshot`
- Preview iframe (same-origin): `frontend/packages/web/components/panel/artifact/HtmlPreview.tsx`
- Thumbnail (opaque origin, no cookies): `frontend/packages/web/components/artifacts/ArtifactHtmlThumb.tsx`
- CSRF middleware: `backend/cubeplex/api/middleware/csrf.py`; cookie naming
  `frontend/packages/core/src/api/cookieNames.ts`
- Prompt cache discipline: `backend/docs/prompt-cache-discipline.md`
- Test taxonomy and placement: `docs/testing.md`
- Existing registry precedent: `backend/cubeplex/agents/actions/registry.py`,
  `backend/cubeplex/parsers/registry.py`
- Authoring-contract precedent: `backend/cubeplex/prompts/widget.py`,
  `backend/skills/preinstalled/show-widget/SKILL.md`
