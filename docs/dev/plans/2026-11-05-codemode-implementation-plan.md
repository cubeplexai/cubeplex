# In-Sandbox Agent Delegation via `cubeplex_context` — implementation plan

**Status:** Proposed; ready for implementation

**Date:** 2026-11-05

**Spec:** [In-Sandbox Agent Delegation via `cubeplex_context` (Code Mode)](../specs/2026-11-04-codemode-design.md)

**Issue:** [#724](https://github.com/cubeplexai/cubeplex/issues/724)

**Goal:** Enable scripts running inside OpenSandbox containers to programmatically orchestrate workspace tools (MCP and approved native tools) and delegate sub-model prompts through a dedicated client package (`cubeplex_context`), without requiring a separate `codemode` tool, without nested QuickJS sandboxes, and without public port exposure.

**Architecture:**
- **Execution Environment:** Code runs natively inside the existing OpenSandbox container (Python 3.12+), retaining normal read/write access to `/workspace` (PVC) and pre-installed document/data libraries (`docx`, `pptx`, `xlsx`, `pandas`).
- **Communication Channel:** Calls from `from cubeplex_context import tools, llm` target a local plaintext HTTP listener on the pod's egress addon (`127.0.0.1:8319`). The addon (mitmproxy sidecar) uses its existing client certificate to forward requests over mTLS to the internal `ExchangeListener` on port 8443 (`/api/v1/internal/context/dispatch`). The bridge is never exposed on the public API port (8000) or through Ingress.
- **Authentication & Nonce Lifecycle:** The backend generates an ephemeral `command_nonce` before `sandbox.start` and persists it to cluster-shared Redis keyed by `command_id` with a TTL equal to the command's own `timeout_seconds`. The nonce is passed via command `opts.envs` (never in `set_run_env`), accepted by the Bridge in both `starting` and `running` states, backfilled with `provider_ref` on `persist_running`, and immediately purged on command terminalization (`_terminalize`) or startup failure (`_persist_start_failed`).
- **Prompt Cache Discipline & Discovery:** System prompt prefixes remain byte-stable across conversation turns by including only fixed SDK usage instructions. Dynamic tool discovery is handled via runtime reflection (`tools.list()`, `tools.describe()`) and `/workspace/.codemode/tools.pyi` synced to running sandboxes via `Sandbox.upload`.
- **Accounting & Anti-Recursion:** In-sandbox `llm.*` queries are strictly single-turn, non-agentic completions (no tool access, depth capped at 1, max 4 concurrent calls). Tokens are tracked as isolated `llm_call` records linked by `parent_run_id` without inflating the parent conversation's `context_tokens`.

**Tech stack:** Python 3.12+, FastAPI/Starlette, mitmproxy (egress addon), Redis, PostgreSQL/SQLAlchemy, OpenSandbox SDK, Next.js / React 19 (Chat UI).

---

## 1. Delivery boundary and execution order

The delivery is structured in three strictly ordered milestones to ensure transport and security contracts exist before surfacing reflection and model delegation capabilities:

1. **Phase 1: In-Sandbox SDK Package, Egress Addon Forwarder, and Exchange Bridge**
   - Package `cubeplex_context` client library.
   - Add local HTTP listener to egress addon (`deploy/kubernetes/egress-bundle/` & `deploy/images/`).
   - Implement `/api/v1/internal/context/dispatch` endpoint on `ExchangeListener` (port 8443).
   - Hook pre-start nonce generation, Redis persistence, `_terminalize`, and `_persist_start_failed` cleanup in `SandboxService` / `RunManager`.
   - Implement dual-factor validation (mTLS CN + Redis nonce status check) and default-deny tool whitelist.
2. **Phase 2: Prompt Cache Discipline, Tool Stubs Synchronization, and Runtime Discovery**
   - Add byte-stable SDK instruction to workspace agent system prompt.
   - Implement `tools.pyi` generator and sync pipeline via `Sandbox.upload` on sandbox startup / MCP catalog refresh.
   - Implement `cubeplex_context.tools.list()` and `describe()` reflection APIs.
3. **Phase 3: Sub-Model Delegation, Token Accounting, and UI Observability**
   - Connect `cubeplex_context.llm.generate()` and `classify()` to workspace model presets via Bridge.
   - Enforce anti-recursion, concurrency limit (4), and create separate `llm_call` rows with `parent_run_id`.
   - Track financial costs via `billing_llm_events`.
   - Update `agents/stream.py` and Chat UI to render nested execution steps within command execution cards.

---

## 2. Phase 1: In-Sandbox SDK, Egress Addon Forwarder & Exchange Bridge

### Files

- **Create** `packages/cubeplex-context/pyproject.toml` and `packages/cubeplex-context/cubeplex_context/__init__.py`, `_client.py`, `tools.py`, `llm.py`: lightweight in-sandbox SDK client.
- **Modify** `deploy/images/sandbox/Dockerfile` and `deploy/images/sandbox/VERSION`: install `cubeplex_context` into the base sandbox image.
- **Modify** `deploy/kubernetes/egress-bundle/addon/inject.py` (and corresponding container build scripts): add local HTTP listener on `127.0.0.1:8319` using Python standard library `http.server` / `http.client` to forward requests with `X-CodeMode-Nonce` over mTLS to backend port 8443.
- **Modify** `backend/cubeplex/sandbox/exchange.py` (or `ExchangeListener` definition): register route `/api/v1/internal/context/dispatch`.
- **Modify** `backend/cubeplex/services/sandbox.py` and `backend/cubeplex/sandbox/manager.py`:
  - Generate `command_nonce` prior to `sandbox.start()`.
  - Pass `CUBEPLEX_COMMAND_NONCE` via command `opts.envs`.
  - Save nonce record to Redis before calling `sandbox.start()`.
  - Update Redis record in `persist_running` to backfill `provider_ref`.
  - Clean up nonce in `_terminalize` and `_persist_start_failed`.
- **Create** `backend/cubeplex/api/routes/v1/_internal_context.py` (or handler module under `backend/cubeplex/sandbox/`): dispatch handler implementing mTLS CN verification, Redis command status check (`starting` or `running`), and tool execution.
- **Add** `backend/tests/unit/sandbox/test_context_bridge.py`, `backend/tests/unit/sandbox/test_command_nonce_lifecycle.py`, and `packages/cubeplex-context/tests/test_client.py`.

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
    "sandbox_id": str,
    "provider_ref": str | None,
    "status": "starting" | "running",
}
# TTL: command.timeout_seconds

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
   - Before `sandbox.start(command, opts, ...)`:
     - Generate `command_nonce = secrets.token_urlsafe(32)`.
     - Inject `CUBEPLEX_COMMAND_NONCE` and `CUBEPLEX_BRIDGE_URL=http://127.0.0.1:8319` into `opts.envs`.
     - Write Redis key `codemode:nonce:{command_id}` with status `starting` and TTL set to `command.timeout_seconds`.
2. **Persistence & Lifecycle Hooks:**
   - In `on_started` / `persist_running(command_id, provider_ref)`: update Redis entry setting `status="running"` and `provider_ref=provider_ref`.
   - In `_terminalize(command_id, ...)`: `await redis.delete(f"codemode:nonce:{command_id}")`.
   - In `_persist_start_failed(command_id, ...)`: immediately call `await redis.delete(f"codemode:nonce:{command_id}")` to eliminate the 90-second orphan exposure window.
3. **Egress Addon Local Forwarder:**
   - In mitmproxy addon `inject.py`, start an async/threaded `http.server.HTTPServer` on `127.0.0.1:8319`.
   - On incoming HTTP POST `/v1/dispatch`:
     - Read incoming headers and body (forwarding `X-CodeMode-Nonce`).
     - Use `http.client.HTTPSConnection` targeting the backend exchange service host (`ExchangeListener` on port 8443) using client certs in `/etc/egress-client/`.
     - Return upstream response directly to the local client.
4. **Bridge Security Gates:**
   - Verify mTLS client cert subject contains valid sandbox identity matching the registered sandbox for that command.
   - Extract `X-CodeMode-Nonce` from request header; fetch Redis record. If missing, fail-closed with HTTP 401.
   - Verify matching `sandbox_commands` status is `starting` or `running`.
   - Check tool name against `allowed_callers`: reject `execute`, `monitor`, `ask_user`, `write_todos`, `subagent`, `persona_update` with HTTP 403.
   - Execute authorized tool through existing runtime dispatcher and return result.

### Tests

- Nonce generation and environment passing in `opts.envs` (verify `set_run_env` is untouched).
- Redis record write on pre-start, status update on `persist_running`, and deletion on `_terminalize`.
- Explicit test for `_persist_start_failed` verifying immediate nonce deletion.
- Bridge authentication unit tests: valid nonce + valid cert -> 200; invalid/expired nonce -> 401; cert mismatch -> 403.
- Whitelist enforcement: whitelisted MCP tool succeeds; disallowed native tools (`execute`, `ask_user`) rejected with 403.

---

## 3. Phase 2: Prompt Cache Discipline, Tool Stubs & Runtime Discovery

### Files

- **Modify** `backend/cubeplex/prompts/` (or agent system prompt builder in `cubeplex/streams/run_manager.py`): add byte-stable SDK instruction block.
- **Create** `backend/cubeplex/services/stub_generator.py`: generate `.pyi` type definitions for configured workspace tools.
- **Modify** `backend/cubeplex/services/sandbox.py` (or post-start lifecycle): trigger `Sandbox.upload` to write `/workspace/.codemode/tools.pyi` once sandbox is running.
- **Extend** `packages/cubeplex-context/cubeplex_context/tools.py`: implement `tools.list()`, `tools.describe(tool_name)`, and dynamic attribute lookup.
- **Add** `backend/tests/unit/services/test_stub_generator.py` and `packages/cubeplex-context/tests/test_tool_discovery.py`.

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
   - When a sandbox instance is initialized or when MCP connectors are enabled/disabled, use `Sandbox.upload` to upload the generated stubs to `/workspace/.codemode/tools.pyi`.
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

- **Modify** `backend/cubeplex/api/routes/v1/_internal_context.py` (Bridge handler): handle `action="llm_generate"` and `action="llm_classify"`.
- **Modify** `backend/cubeplex/models/llm_call.py` (or existing DB schema): link sub-model calls via `parent_run_id`.
- **Modify** `backend/cubeplex/billing/` (or billing recorder): record tokens via `billing_llm_events` pointing to `parent_run_id`.
- **Modify** `backend/cubeplex/agents/stream.py`: handle nested tool and sub-model execution events.
- **Modify** `frontend/packages/core/src/types/events.ts`: declare nested execution event shapes.
- **Modify** `frontend/packages/web/components/chat/`: render nested progress steps within the command execution card.
- **Add** `backend/tests/unit/sandbox/test_submodel_delegation.py` and `frontend/packages/web/__tests__/components/NestedToolActivity.test.tsx`.

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

1. **Anti-Recursion & Concurrency Bounds:**
   - Bridge enforces non-agentic completions: sub-calls directly invoke provider completion APIs without tool schemas or agent loops.
   - An `asyncio.Semaphore(4)` per command session limits concurrent sub-calls to 4.
   - Sub-calls cannot spawn sub-agents or re-invoke code execution (depth strictly 1).
2. **Isolated Token Accounting:**
   - Tokens consumed by `llm.generate()` / `llm.classify()` are recorded in a new `llm_call` row with `parent_run_id` set to the active agent run ID.
   - Tokens are NOT merged into the parent run's `turn_usage` or `context_tokens` (preventing artificial inflation of the conversation context token counter).
   - Billing events (`billing_events` / `billing_llm_events`) are logged with `parent_run_id` for accurate organization cost attribution.
3. **Observability & Nested UI Stream:**
   - When the Bridge begins executing a delegated tool or sub-model query, it emits a nested progress event tagged with `parent_tool_call_id` (the ID of the active `execute` command).
   - The Chat UI renders an expandable nested execution tree inside the command card showing sub-tool and model calls with execution duration.

### Tests

- Concurrency enforcement: issuing 8 concurrent `llm.generate()` calls respects the semaphore of 4.
- Token accounting test: parent run's context tokens remain unchanged while child `llm_call` and `billing_llm_events` rows are created.
- Provider sub-call error handling: upstream 429/timeout cleanly surfaces back to the in-sandbox SDK without terminating the host agent run.
- Frontend rendering test: command card successfully folds and displays nested sub-tool steps.

---

## 5. Security & Edge Case Checklist

- [ ] **No Port 8000 Exposure:** Bridge route is exclusively on `ExchangeListener:8443` and not mounted on public routers.
- [ ] **No Egress Policy Drift:** Uses existing cluster FQDN without modifying `build_network_policy`.
- [ ] **Process Liveness Gap Closed:** Nonce generated before `sandbox.start`, Redis written before process launches, accepted in `starting` & `running`, and immediately deleted on `_persist_start_failed`.
- [ ] **Multi-Turn Command Support:** Background and `lifetime=conversation` commands continue executing across agent turns without early nonce deletion.
- [ ] **Same-UID Container Risk Acknowledged:** Server-side Bridge Default-Deny whitelist is the authoritative security boundary against same-container UID 1000 processes.
- [ ] **Prompt Cache Byte Stability:** System prompt remains byte-identical across conversation turns; tool stubs maintained via `/workspace/.codemode/tools.pyi` and reflection APIs.
