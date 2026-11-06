# In-Sandbox Agent Delegation via `cubeplex_context` — implementation plan

**Status:** Proposed; ready for implementation

**Date:** 2026-11-05

**Spec:** [In-Sandbox Agent Delegation via `cubeplex_context` (Code Mode)](../specs/2026-11-04-codemode-design.md)

**Issue:** [#724](https://github.com/cubeplexai/cubeplex/issues/724)

**Goal:** Enable scripts running inside OpenSandbox containers to programmatically orchestrate workspace tools (MCP and approved native tools) and delegate sub-model prompts through a dedicated client package (`cubeplex_context`), without requiring a separate `codemode` tool, without nested QuickJS sandboxes, and without public port exposure in production.

**Architecture:**
- **Execution Environment:** Code runs natively inside the existing OpenSandbox container (Python 3.12+), retaining normal read/write access to `/workspace` (PVC) and pre-installed document/data libraries (`docx`, `pptx`, `xlsx`, `pandas`).
- **Communication Channel:** Calls from `from cubeplex_context import tools, llm` target a local plaintext HTTP listener on the pod's egress addon (`127.0.0.1:8319`). The addon (in `deploy/kubernetes/charts/cubeplex/files/egress/inject.py`) forwards requests via standard library `http.client` using `EGRESS_EXCHANGE_URL`:
  - In `mtls` mode (production): forwards over mTLS (using `/etc/egress-client/` client certs) to internal `ExchangeListener` on port 8443 (`/api/v1/internal/context/dispatch`). The bridge is never exposed on the public API port (8000) or through Ingress.
  - In `dev` mode (local & tests): the dispatch router is mounted directly on the public app in `backend/cubeplex/api/app.py` (`create_app`) under `mode == "dev"` using existing sidecar dev authentication headers (`x-egress-dev-token` + `x-egress-sandbox-id`).
- **Authentication & Nonce Lifecycle:**
  - Before `sandbox.start`, the backend generates an ephemeral `command_nonce` and passes both `CUBEPLEX_COMMAND_NONCE` and `CUBEPLEX_COMMAND_ID` in `opts.envs`.
  - The nonce is persisted to cluster-shared Redis keyed by `codemode:nonce:{command_id}` right after `_persist_reserve` commits.
  - The Redis record TTL is derived from the `_persist_reserve` `timeout_seconds` parameter, or `(monitor_deadline_at - now).total_seconds()`, with a fallback to 3600 seconds.
  - The SDK sends `X-CodeMode-Command-Id` alongside `X-CodeMode-Nonce`, enabling exact O(1) command lookup even with concurrent commands in the same sandbox.
  - Accepted in both `starting` and `running` states; `provider_ref` backfilled on `persist_running`.
  - Nonce deletion occurs strictly when the command transitions to a terminal status (`exited` or `killed`), preserving the nonce during running log-cursor observations (`record_observation`), while immediate deletion on `_persist_start_failed` is retained.
- **Scope Boundary:** In-sandbox delegation strictly covers managed commands that go through `_persist_reserve` (`execute` and `monitor`). Subagents running via `driver.start` or direct fallbacks to `sandbox.execute` do not create command rows, do not receive nonces, and cannot access the bridge.
- **Identity Verification:** In `mtls` mode, the client certificate CN represents the OpenSandbox `sandbox_id` and is verified against `sandbox_commands.sandbox_instance_id` (not a Kubernetes pod name).
- **Prompt Cache Discipline & Discovery:** System prompt prefixes remain byte-stable across conversation turns by including only fixed SDK usage instructions. Dynamic tool discovery is handled via runtime reflection (`tools.list()`, `tools.describe()`) and `/workspace/.codemode/tools.pyi` synced to running sandboxes via `Sandbox.upload`.
- **Accounting & Anti-Recursion:** In-sandbox `llm.*` queries are strictly single-turn, non-agentic completions (no tool access, depth capped at 1). Concurrency is capped at 4 using a Redis-backed distributed semaphore. Tokens are tracked as isolated `BillingEvent` and `LlmBillingEvent` records linked by `parent_run_id` without inflating the parent conversation's `turn_usage` or `context_tokens`.

**Tech stack:** Python 3.12+, FastAPI/Starlette, mitmproxy (egress addon), Redis, PostgreSQL/SQLAlchemy, OpenSandbox SDK, Next.js / React 19 (Chat UI).

---

## 1. Delivery boundary and execution order

The delivery is structured in three strictly ordered milestones to ensure transport and security contracts exist before surfacing reflection and model delegation capabilities:

1. **Phase 1: In-Sandbox SDK Package, Egress Addon Forwarder, and Exchange Bridge**
   - Package `cubeplex_context` client library.
   - Add local HTTP listener (`127.0.0.1:8319`) to `deploy/kubernetes/charts/cubeplex/files/egress/inject.py` using standard library `http.client` targeting `EGRESS_EXCHANGE_URL`, and fix the dangling symlink in `deploy/kubernetes/egress-bundle/addon/inject.py`.
   - Implement dispatch router in `build_exchange_app` (`backend/cubeplex/sandbox_env/exchange_listener.py`); mount on public app in `dev` mode, on port 8443 in `mtls` mode.
   - Hook pre-start nonce generation and Redis persistence in `backend/cubeplex/middleware/sandbox.py` (`_persist_reserve`), and hook nonce deletion in terminalization functions (`record_observation`, `mark_terminal`, `_persist_start_failed`, `_terminalize`).
   - Implement dual-factor validation (mTLS CN / dev secret + Redis command status check) and a strict default-deny tool whitelist.
2. **Phase 2: Prompt Cache Discipline, Tool Stubs Synchronization, and Runtime Discovery**
   - Add byte-stable SDK instruction to workspace agent system prompt.
   - Implement `tools.pyi` generator and sync pipeline via `Sandbox.upload` on sandbox startup / MCP catalog refresh.
   - Implement `cubeplex_context.tools.list()` and `describe()` reflection APIs.
3. **Phase 3: Sub-Model Delegation, Token Accounting, and UI Observability**
   - Connect `cubeplex_context.llm.generate()` and `classify()` to workspace model presets via Bridge.
   - Enforce anti-recursion, Redis-backed concurrency limit (4), and insert `billing_events` / `billing_llm_events` rows with `parent_run_id` set to `sandbox_commands.run_id`.
   - Publish nested execution progress events to the run stream keyed by `SandboxCommand.tool_call_id`.
   - Update Chat UI command cards to render nested execution trees.

---

## 2. Phase 1: In-Sandbox SDK, Egress Addon Forwarder & Exchange Bridge

### Files

- **Create** `packages/cubeplex-context/pyproject.toml` and `packages/cubeplex-context/cubeplex_context/__init__.py`, `_client.py`, `tools.py`, `llm.py`: lightweight in-sandbox SDK client.
- **Modify** `deploy/images/sandbox/Dockerfile` and `deploy/images/sandbox/VERSION`: install `cubeplex_context` into the base sandbox image.
- **Modify** `deploy/kubernetes/charts/cubeplex/files/egress/inject.py`: add local HTTP listener on `127.0.0.1:8319` using standard library `http.server` / `http.client` to forward requests with `X-CodeMode-Nonce` to `EGRESS_EXCHANGE_URL`.
- **Fix Symlink** `deploy/kubernetes/egress-bundle/addon/inject.py`: point correctly to `../../charts/cubeplex/files/egress/inject.py`.
- **Modify** `backend/cubeplex/sandbox_env/exchange_listener.py`: register dispatch router in `build_exchange_app`.
- **Modify** `backend/cubeplex/api/app.py`: in `create_app()`, mount dispatch router under prefix `/api/v1` when `_egress_auth_config.get("mode", "mtls") == "dev"`, alongside `internal_egress.router`.
- **Create** `backend/cubeplex/sandbox_env/context_bridge.py`: handler implementing mTLS CN (`sandbox_instance_id`) and dev auth (`x-egress-dev-token` / `x-egress-sandbox-id`) validation, Redis nonce status check (`starting` or `running`), and default-deny tool execution.
- **Modify** `backend/cubeplex/middleware/sandbox.py`:
  - In `_persist_reserve`: after row commit, write Redis nonce record under `reservation.command.id`. Calculate TTL from `timeout_seconds`, or `(monitor_deadline_at - now).total_seconds()`, with a fallback to 3600 seconds.
  - In `sandbox.start(...)` call sites (`execute` and `monitor`): pass `envs={"CUBEPLEX_COMMAND_ID": reservation.command.id, "CUBEPLEX_COMMAND_NONCE": command_nonce, "CUBEPLEX_BRIDGE_URL": "http://127.0.0.1:8319"}` into `opts.envs`.
  - In `persist_running`: backfill `provider_ref` on the Redis record.
  - In terminal state transitions (`mark_terminal` when status is `exited` or `killed`, `_persist_terminal`, `_persist_killed`, `_persist_start_failed`): delete `codemode:nonce:{command_id}` from Redis. Running log-cursor observations (`_persist_cursor`, `record_observation` with `status="running"`) MUST NOT delete the key.
- **Add** `packages/cubeplex-context/tests/test_client.py` (pure unit tests for SDK client).
- **Add** `backend/tests/e2e/test_context_bridge.py` and `backend/tests/e2e/test_command_nonce_lifecycle.py` (E2E tests covering routes, Redis, DB status, and auth modes).

### Interfaces

```python
# Redis nonce schema:
# Key: f"codemode:nonce:{command_id}"
# Value (JSON):
{
    "nonce": str,
    "org_id": str,
    "workspace_id": str,
    "user_id": str,
    "sandbox_instance_id": str,
    "provider_ref": str | None,
    "status": "starting" | "running",
}
# TTL: command timeout_seconds from _persist_reserve, or (monitor_deadline_at - now), or fallback 3600s

# Bridge Request / Response Schemas:
class ContextDispatchRequest(BaseModel):
    action: Literal["tool_call", "tool_list", "tool_describe", "llm_generate", "llm_classify"]
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)

class ContextDispatchResponse(BaseModel):
    success: bool
    result: Any = None
    error: str | None = None
```

### Core Logic

1. **Pre-Execution Generation & Redis Persistence:**
   - In `backend/cubeplex/middleware/sandbox.py`:
     - Before invoking `sandbox.start`, generate `command_nonce = secrets.token_urlsafe(32)`.
     - After `BackgroundTaskService.reserve_task` commits in `_persist_reserve`:
       - Calculate TTL: use `timeout_seconds` if provided, else `int((monitor_deadline_at - now).total_seconds())` if `monitor_deadline_at` is set, with a fallback of 3600 seconds.
       - Write Redis key `codemode:nonce:{reservation.command.id}` with `status="starting"` and the calculated TTL.
       - Pass `CUBEPLEX_COMMAND_ID=reservation.command.id`, `CUBEPLEX_COMMAND_NONCE=command_nonce`, and `CUBEPLEX_BRIDGE_URL=http://127.0.0.1:8319` via `envs` into `sandbox.start(command, opts, envs=...)`.
2. **Persistence & Terminal Lifecycle Cleanup:**
   - In `persist_running(command_id, provider_ref)`: update Redis entry setting `status="running"` and `provider_ref=provider_ref`.
   - Running log observations (`_persist_cursor`, `record_observation` with `status="running"`, `BackgroundTaskCoordinator` heartbeats) **MUST NOT** delete the nonce.
   - Deletion occurs strictly on terminal transitions (`mark_terminal` for `exited` or `killed`, `_persist_terminal`, `_persist_killed`, `_terminalize`).
   - In `_persist_start_failed`: immediately call `await redis.delete(f"codemode:nonce:{command_id}")` to eliminate the ~90-second orphan exposure window.
3. **Egress Addon Local Forwarder:**
   - In `deploy/kubernetes/charts/cubeplex/files/egress/inject.py`:
     - Start an internal HTTP server on `127.0.0.1:8319`.
     - Read target from environment variable `EGRESS_EXCHANGE_URL` (avoiding hardcoded port 8443).
     - In `mtls` mode, forward using standard library `http.client.HTTPSConnection` with client certs in `/etc/egress-client/`.
     - In `dev` mode, forward with `x-egress-dev-token` (dev secret) and `x-egress-sandbox-id` headers.
     - Forward incoming `X-CodeMode-Command-Id`, `X-CodeMode-Nonce` headers and request body; stream upstream response back to local client.
4. **Bridge Security & Default-Deny Whitelist:**
   - Retrieve `command_id` from `X-CodeMode-Command-Id` header (sent by `cubeplex_context` SDK).
   - Fetch `codemode:nonce:{command_id}` from Redis; verify `X-CodeMode-Nonce` matches. If missing or mismatched, fail-closed with HTTP 401.
   - Load `sandbox_commands` record for `command_id`; verify status is currently `starting` or `running`.
   - Validate caller identity:
     - In `mtls` mode: verify client cert CN matches `sandbox_commands.sandbox_instance_id` (OpenSandbox sandbox ID, not Kubernetes pod name).
     - In `dev` mode: verify `x-egress-dev-token` and `x-egress-sandbox-id == sandbox_commands.sandbox_instance_id`.
   - **Scope Boundary:** Code mode delegation strictly covers managed commands that pass through `_persist_reserve` (`execute` and `monitor`). Subagents running via `driver.start` or direct fallbacks to `sandbox.execute` do not create command rows, do not receive nonces, and are blocked from bridge calls.
   - **Strict Default-Deny Whitelist:**
     - Whitelisted: external MCP tools named `{slug}__{tool_name}` from `slugify_for_namespace`, plus exactly four native tools: `read`, `calculator`, `datetime`, and `memory_search`.
     - Forbidden: all other tools (including `write`, `edit`, `execute`, `monitor`, `kill_execute`, `scheduled_tasks`, `save_artifact`, `present_file`, `generate_image`, `create_trigger`, `create_scheduled_task`, `load_skill`, `show_widget`, `persona_get`, `persona_update`, `sandbox_config`, `view_images`, `ask_user`, `write_todos`, `subagent`).
     - Any non-whitelisted tool call is rejected with HTTP 403 Forbidden.

### Tests

- **Unit tests (`packages/cubeplex-context/tests/`):**
  - SDK client serialization, error handling, and parameter encoding without backend dependencies.
- **E2E tests (`backend/tests/e2e/`):**
  - `test_command_nonce_lifecycle.py`: verify Redis key created on reserve, updated on `persist_running`, deleted on `mark_terminal` and `_persist_start_failed`, and surviving across agent run `Done`.
  - `test_context_bridge.py`:
    - Valid nonce + valid caller -> 200.
    - Expired/missing nonce -> 401.
    - Sandbox ID mismatch -> 403.
    - Whitelisted MCP tool (`linear__list_issues`) -> 200.
    - Whitelisted native tool (`read`) -> 200.
    - Disallowed native tools (`write`, `scheduled_tasks`, `execute`, `ask_user`) -> 403.
    - Dev mode authentication roundtrip vs mTLS mode verification.

---

## 3. Phase 2: Prompt Cache Discipline, Tool Stubs & Runtime Discovery

### Files

- **Modify** `backend/cubeplex/prompts/` (agent system prompt builder in `cubeplex/streams/run_manager.py`): add byte-stable SDK instruction block.
- **Create** `backend/cubeplex/services/stub_generator.py`: generate `.pyi` type definitions for configured workspace tools.
- **Modify** `backend/cubeplex/middleware/sandbox.py` (post-start / bootstrap hooks): synchronize `/workspace/.codemode/tools.pyi` via `Sandbox.upload` once container is running.
- **Extend** `packages/cubeplex-context/cubeplex_context/tools.py`: implement `tools.list()`, `tools.describe(tool_name)`, and dynamic attribute lookup.
- **Add** `backend/tests/unit/services/test_stub_generator.py` (pure unit test for AST/stub generation).
- **Add** `backend/tests/e2e/test_tool_discovery.py` (E2E test for stub upload and bridge reflection).

### Interfaces

```python
# cubeplex_context/tools.py API:
class ToolCatalog:
    def list(self) -> list[dict[str, str]]:
        """Return list of available tools as [{'name': ..., 'description': ...}]."""
        ...
        
    def describe(self, name: str) -> dict[str, Any]:
        """Return full parameter schema and documentation for a specific tool."""
        ...

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        """Dynamic async callable for normalized tool names."""
        ...

    def __getitem__(self, name: str) -> Callable[..., Awaitable[Any]]:
        """Direct access using exact namespaced identifier (e.g. tools['linear__create_issue'])."""
        ...
```

### Core Logic

1. **Byte-Stable System Prompt:**
   - The system prompt receives a static text section explaining how to import and use `cubeplex_context`.
   - Tool names and parameters are NEVER formatted into the system prompt text, maintaining prompt cache stability.
2. **Stub File Generation (`/workspace/.codemode/tools.pyi`):**
   - Translate active MCP tools and whitelisted native tools into typed Python function stubs with docstrings.
   - Use `{slug}__{tool_name}` runtime format derived from `slugify_for_namespace`.
   - Because the backend API does not mount workspace PVCs, write the file via `Sandbox.upload` after container startup or on connector catalog updates.
3. **In-Sandbox Reflection API:**
   - `cubeplex_context.tools.list()` and `tools.describe()` dispatch requests to the Bridge endpoint with `action="tool_list"` / `action="tool_describe"`.
   - The Bridge queries the active workspace tool registry and returns schemas without LLM round trips.

### Tests

- Verification that agent system prompt remains byte-identical across turns regardless of tool changes.
- Stub generator produces valid Python `.pyi` syntax with correct type annotations for complex JSON Schemas.
- `tools.pyi` upload via `Sandbox.upload` triggers upon sandbox bootstrap.
- Dynamic SDK calls correctly map `tools.foo_bar()` and `tools['foo__bar']()` to the expected bridge payloads.

---

## 4. Phase 3: Sub-Model Delegation, Token Accounting & UI Observability

### Files

- **Modify** `backend/cubeplex/sandbox_env/context_bridge.py`: handle `action="llm_generate"` and `action="llm_classify"`.
- **Modify** `backend/cubeplex/repositories/billing.py`: record sub-model tokens via `insert_llm_event`.
- **Modify** `backend/cubeplex/streams/run_events.py` and run stream publishers: publish nested execution events tagged with `SandboxCommand.tool_call_id`.
- **Modify** `frontend/packages/core/src/types/events.ts`: declare nested execution event shapes.
- **Modify** `frontend/packages/web/components/chat/`: render nested progress steps within the command execution card.
- **Add** `backend/tests/e2e/test_submodel_delegation.py` (E2E test for LLM dispatch, Redis concurrency, and billing rows).
- **Add** `frontend/packages/web/__tests__/components/NestedToolActivity.test.tsx`.

### Interfaces

```python
# In cubeplex_context/llm.py:
class LLMClient:
    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        ...

    async def classify(
        self,
        data: Any,
        *,
        instruction: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        ...
```

### Core Logic

1. **Anti-Recursion & Distributed Concurrency Ceiling:**
   - Bridge enforces non-agentic completions: sub-calls directly invoke provider completion APIs without tool schemas or agent loops. Depth is strictly capped at 1.
   - Enforce concurrency using cluster-shared Redis (e.g. atomic counter on `codemode:concurrency:{command_id}` with max 4). Requests exceeding 4 wait or fail with 429 across multi-replica backend pods.
2. **Isolated Token Accounting & Billing:**
   - Sub-model tokens are NOT merged into parent run's `turn_usage` or `context_tokens` (preventing artificial inflation of the conversation's active context window).
   - The Bridge reads `SandboxCommand.run_id` (from `TaskSpec.originating_run_id`).
   - The Bridge calls `BillingRepository.insert_llm_event` to create a `BillingEvent` (`event_type="llm_call"`) and an `LlmBillingEvent` with `parent_run_id` set to the command's originating run ID.
3. **Observability & Nested UI Stream:**
   - The Bridge receives requests in a separate HTTP request outside the agent process. It publishes nested progress events to the conversation run stream in Redis, tagged with `SandboxCommand.tool_call_id`.
   - Chat UI command cards join on that tool-call ID and render an expandable nested execution tree showing sub-tool calls and sub-model queries with durations.

### Tests

- Distributed concurrency enforcement: issuing concurrent `llm.generate()` calls across backend replicas respects the hard limit of 4.
- Token accounting E2E test: parent run's context tokens remain unchanged while `billing_events` and `billing_llm_events` rows are inserted with `parent_run_id`.
- Provider sub-call error handling: upstream 429/timeout surfaces cleanly to SDK.
- Frontend rendering test: command card successfully folds and displays nested sub-tool steps.

---

## 5. Security & Edge Case Checklist

- [ ] **Port Isolation & Dev Mounting:** Bridge route is registered in `build_exchange_app` (port 8443 in `mtls` mode) and mounted on the public app in `backend/cubeplex/api/app.py` in `dev` mode using `x-egress-dev-token` / `x-egress-sandbox-id`, never exposed on public port 8000 in production.
- [ ] **No Egress Policy Drift:** Uses existing cluster FQDN without modifying `build_network_policy`.
- [ ] **Correct Addon Path:** Local HTTP forwarder added to `deploy/kubernetes/charts/cubeplex/files/egress/inject.py` using stdlib `http.client` targeting `EGRESS_EXCHANGE_URL`; broken symlink fixed.
- [ ] **Unambiguous Nonce Lookup:** SDK passes `X-CodeMode-Command-Id` and `X-CodeMode-Nonce` populated from environment, enabling unambiguous Redis lookup across concurrent commands in the same sandbox.
- [ ] **Accurate Lifecycle Hooks:** Nonce generated before `sandbox.start`, persisted after `_persist_reserve` with calculated TTL (from `timeout_seconds` or deadline), preserved across running cursor updates (`_persist_cursor`), and deleted strictly in terminal state transitions (`mark_terminal` on exited/killed) and `_persist_start_failed`.
- [ ] **Multi-Turn Command Support:** Background and `lifetime=conversation` commands continue executing across agent turns without early nonce deletion.
- [ ] **Identity Matching on sandbox_instance_id:** Bridge validates mTLS client cert CN against `sandbox_commands.sandbox_instance_id` (OpenSandbox sandbox ID, not pod name).
- [ ] **Same-UID Container Risk Acknowledged:** Sibling UID 1000 processes stealing the nonce can access whitelisted MCP tools and `llm.*` while the command is running; the Default-Deny whitelist only prevents re-executing host shell/admin tools.
- [ ] **Scope Boundary Documented:** Covers managed commands in `_persist_reserve` (`execute` and `monitor`); subagent `driver.start` and direct `sandbox.execute` fallbacks do not receive command credentials.
- [ ] **Prompt Cache Byte Stability:** System prompt remains byte-identical across conversation turns; tool stubs maintained via `/workspace/.codemode/tools.pyi` and reflection APIs.
- [ ] **Correct Accounting:** Token usage recorded via `BillingRepository.insert_llm_event` with `parent_run_id`, never merged into parent `turn_usage`.
- [ ] **E2E Test Placement:** All tests touching FastAPI routes, database `AsyncSession`, Alembic, or Redis are placed in `backend/tests/e2e/`.
