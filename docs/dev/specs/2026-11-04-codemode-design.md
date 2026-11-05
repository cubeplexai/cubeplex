# In-Sandbox Agent Delegation via `cubeplex_context` (Code Mode)

**Status:** Proposed Specification

**Date:** 2026-11-05

**Scope:** OpenSandbox Runtime, In-Sandbox SDK (`cubeplex_context`), Tool & Model Delegation

**Issue:** [#724](https://github.com/cubeplexai/cubeplex/issues/724)

**Pull Request:** [#729](https://github.com/cubeplexai/cubeplex/pull/729)

**Related Designs:**
- [MCP Progressive Disclosure](2026-05-27-mcp-progressive-disclosure-design.md)
- [Runtime Session Contracts](2026-09-13-runtime-session-design.md)
- [Sandbox Background Execute](2026-09-18-sandbox-background-execute-design.md)
- [Command Lifecycle](2026-09-20-command-lifecycle-design.md)
- [Multi-Instance Run Control](2026-05-25-multi-instance-run-control-design.md)
- [Prompt Cache Discipline](../../../backend/docs/prompt-cache-discipline.md)

---

## 1. Executive Summary & Design Principles

The standard "Code Mode" design found in single-process desktop CLI tools (e.g. `pi.dev`, Docker Agent) treats code execution as an isolated in-memory "tool calling macro". In those environments, the model writes JavaScript inside a synthetic, memory-only QuickJS VM with zero filesystem access purely to avoid network round-trips when chaining API tool calls.

In CubePlex, this design is both **redundant** and **architecturally counter-productive**:
1. **OpenSandbox is Already the Isolation Boundary:** CubePlex already provisions enterprise-grade, kernel-isolated **OpenSandbox containers** scoped to the user, conversation, or topic (sharing a user-level sandbox for personal chats and creator topics, conversation-level for group chats, and topic-level for dedicated topics). Running a stripped QuickJS sandbox *inside* an already isolated container introduces fragile nested sandboxing ("sandbox-in-a-sandbox") while cutting the agent off from the rich Linux and Python runtime.
2. **Filesystem Access is Essential for Productivity Skills:** The agent already has unrestricted read/write/exec permissions in its workspace. Heavy production skills (`docx`, `pptx`, `xlsx`, `pdf`, data science with `pandas`/`matplotlib`) fundamentally rely on reading and writing real files on the persistent `/workspace` volume (PVC).
3. **No Redundant Tool Concept:** Introducing a separate `codemode` tool causes cognitive conflict for the model (forcing it to choose between `execute` and `codemode`) and fragments execution pipelines.

### The Architectural Vision: In-Sandbox Delegation
Instead of introducing a new tool, **CubePlex empowers the existing sandbox environment with a native context library: `cubeplex_context`**:
- The agent continues using its familiar `write` and `execute` tools.
- When running scripts in the sandbox, code can directly import `cubeplex_context`:
  ```python
  from cubeplex_context import tools, llm
  ```
- **Tool Delegation:** Scripts can invoke configured workspace MCP servers and approved native tools asynchronously (`await tools.linear__list_issues(...)`).
- **Model Delegation:** Scripts can query the agent's available LLMs for non-agentic sub-tasks (e.g., batch data classification, content summarization).
- **Universal Skill Uplift:** All existing document and research skills automatically gain the ability to query MCP tools and LLMs directly from their internal Python scripts.

---

## 2. Architecture & Communication Model

```
┌─────────────────────────────────────────────────────────────┐
│                     Model Turn (LLM)                        │
│                                                             │
│  - Learns SDK usage via byte-stable system prompt           │
│  - Discovers tool signatures via tools.list() or .pyi stub  │
│  - Writes Python script using `from cubeplex_context ...`   │
│  - Executes script via standard `execute(command="...")`    │
└──────────────────────────────┬──────────────────────────────┘
                               │ tool_call: execute("python script.py")
                               ▼
═════════════════════════════════════════════════════════════════
                      CUBEPLEX BACKEND
 ┌───────────────────────────────────────────────────────────┐
 │ Sandbox Service / Command Lifecycle                       │
 │  - Before `sandbox.start`: generates `command_nonce`,     │
 │    persists record to Redis (status starting/running),    │
 │    and passes via `opts.envs` (NOT in set_run_env)        │
 │  - On `persist_running(command_id, provider_ref)`:        │
 │    backfills `provider_ref` on the Redis record           │
 │  - On `_terminalize` (exited/killed) or start failure:    │
 │    purges nonce immediately                               │
 │                                                           │
 │ ExchangeListener (Internal mTLS Port 8443):               │
 │  - Route: /api/v1/internal/context/dispatch               │
 │  - NOT exposed via Ingress; port 8000 never serves route  │
 │  - Verifies mTLS client certificate CN matches sandbox    │
 │  - Verifies nonce matches sandbox_commands record in      │
 │    `starting` or `running` state                          │
 │  - Enforces Default-Deny whitelist on delegated tools     │
 │  - Dispatches tool invocations to MCP / Native tools      │
 │  - Dispatches single-turn sub-model completions to LLM;   │
 │    records separate `llm_call` row (parent_run_id link)   │
 └─────────────────────────────▲─────────────────────────────┘
                               │
            mTLS (HTTPS)       │ Egress Addon mTLS Channel
         to port 8443          │ (Uses addon's /etc/egress-client/ certs;
                               │  no new force_allow_hosts needed)
═════════════════════════════════════════════════════════════════
                 WORKSPACE OPENSANDBOX CONTAINER
 ┌───────────────────────────────────────────────────────────┐
 │ OpenSandbox Pod (Local Egress Addon Sidecar)              │
 │                                                           │
 │  - Egress addon listens on localhost (e.g. 127.0.0.1:8319)│
 │    and forwards via stdlib http.client over mTLS          │
 │  - `cubeplex_context` calls local addon HTTP endpoint     │
 │    with `X-CodeMode-Nonce: <command_nonce>`               │
 │                                                           │
 │ Python Script Execution (Python 3.12+):                   │
 │   from cubeplex_context import tools, llm                 │
 │                                                           │
 │   * `tools.<name>(**kwargs)` ──> calls local addon bridge │
 │   * `llm.generate(prompt)`   ──> calls local addon bridge │
 │   * `llm.classify(data)`     ──> calls local addon bridge │
 │                                                           │
 │ Filesystem & Artifacts:                                   │
 │   - Full read/write to `/workspace` (PVC)                 │
 │   - Standard stdout/stderr returned to Agent as normal    │
 └───────────────────────────────────────────────────────────┘
```

---

## 3. Trust Boundary & Security Architecture

### 3.1 Network Egress & Isolated Exchange Port (No Port 8000 Exposure)
- **Zero Exposure on Public API Port (8000):** The reverse dispatch bridge endpoint MUST NOT be mounted on the public FastAPI app (port 8000), nor within `/api/v1/ws/{workspace_id}`. On port 8000, requests without session cookies bypass CSRF middleware, and public Ingress rules map `/api` directly to port 8000.
- **Dedicated Internal Listener (`ExchangeListener:8443`):** The bridge route is registered exclusively on the internal `ExchangeListener` application (listening on port 8443), under an internal prefix such as `/api/v1/internal/context/dispatch`:
  - Port 8443 is exposed only via internal Kubernetes ClusterIP Service; Ingress NEVER routes to 8443.
  - The listener requires valid client certificates (mTLS).
- **Local Egress Addon Forwarding Channel:**
  - Client certificates reside exclusively in the egress sidecar at `/etc/egress-client/`. The sandbox application container does not mount these certificates and cannot connect directly to 8443 (`CERT_REQUIRED`).
  - The existing egress addon (mitmproxy's `inject.py`) only intercepts outbound HTTP traffic matching `cbxref_` placeholders. It does not provide a local bridge endpoint today.
  - An internal HTTP listener (e.g. `127.0.0.1:8319`) must be added to the egress addon process. The addon runs in mitmproxy's embedded Python runtime without third-party dependencies like `httpx`; this local forwarder must use the Python standard library (`http.client` / `urllib.request`).
  - The in-sandbox `cubeplex_context` SDK issues standard plaintext HTTP calls to `127.0.0.1:8319` with `X-CodeMode-Nonce: <command_nonce>`. The addon terminates the local call and forwards it over the established mTLS connection to the backend `ExchangeListener` on port 8443.
  - No new `force_allow_hosts` rules are required in `build_network_policy`, as the cluster-internal FQDN is already permitted.
- **Dual-Factor Bridge Validation:** On every incoming call, the Bridge verifies:
  1. The client certificate CN corresponds to the verified caller sandbox pod.
  2. The nonce corresponds to an active `sandbox_commands` record whose `status` is currently `starting` or `running`, and whose registered sandbox matches the certificate.

### 3.2 Nonce Lifecycle Bound to Sandbox Commands
The lifecycle of the command nonce is strictly aligned with the lifecycle of the sandbox command execution in `sandbox_commands`:

1. **Pre-Execution Generation & Redis Persistence:**
   - The `command_nonce` MUST be generated and saved to cluster-shared Redis **before** invoking `sandbox.start()`. Generating it inside `persist_running` / `on_started` is too late, because the container process is already launched and an early bridge call from a fast script would receive a false 401.
   - The Redis record is keyed by `command_id`, storing `org_id`, `workspace_id`, and `user_id`.
   - **TTL:** The record TTL is set to the command's own `timeout_seconds` (the same duration as `monitor_deadline_at`), serving strictly as a fallback against missed cleanups.
   - The nonce is passed exclusively through the command's own `opts.envs` (as `CUBEPLEX_COMMAND_NONCE`). It must **NEVER** be written to `set_run_env` (which persists sandbox-wide and would leak across subsequent commands).
2. **State Acceptance & `provider_ref` Backfill on `running`:**
   - The Bridge accepts the nonce while the command is in either `starting` or `running` state.
   - When `on_started` invokes `persist_running(command_id, provider_ref)`, the backend updates the Redis record to backfill `provider_ref`.
3. **Multi-Call Lifetime (Not Single-Use):**
   - The script may invoke `tools.*` and `llm.*` multiple times during its execution. All calls during that command execution share the same `command_nonce`. Single-use semantics are explicitly avoided.
4. **Deletion on Command Terminalization & Start Failure:**
   - The nonce is deleted from Redis when the command transitions to `exited` or `killed`.
   - The existing `_terminalize` logic covers process exit, user cancellation, command timeout, sandbox pause/deletion, and cleanup of residual `lifetime=run` commands.
   - **Start Failure Cleanup:** If `sandbox.start` launches the process but subsequent persistence fails, existing `_persist_start_failed` leaves the command row in `starting` without immediately marking it `killed` (the reconciler only cleans up unattached starting rows after ~90 seconds). Because the Bridge accepts nonces in `starting` state, this would leave a 90-second exposure window. To close this gap, `_persist_start_failed` (and startup exception handlers) MUST explicitly delete the Redis nonce record immediately, matching `_terminalize`.
5. **Independent of Agent Turn Completion:**
   - The nonce is **NOT** deleted when `sandbox.start()` returns.
   - The nonce is **NOT** deleted when the 60-second execution result is handed off to a background task.
   - The nonce is **NOT** deleted when the current LLM run completes with `Done`.
   - Commands with `lifetime=conversation` (or background commands) continue executing after the LLM turn concludes and retain full bridge access as long as their status remains `running`.
6. **Fail-Closed Lookup:**
   - Any lookup miss in Redis fails-closed with HTTP 401 Unauthorized across all backend replicas.

### 3.3 Security Boundary & Residual Risk within Pod
Processes sharing the container under UID 1000 (`cubeplex`) can inspect `/proc/<pid>/environ` of other running processes.
- **Residual Exposure:** Any concurrently executing process under UID 1000 in the same sandbox pod can read `CUBEPLEX_COMMAND_NONCE` from `/proc/<pid>/environ` while the command is active, and can send requests to the local addon on `127.0.0.1:8319`. Because mTLS client certificates identify the pod rather than the specific process, mTLS cannot block other processes within the same pod.
- **Scope of Exposure:** Processes that can read `environ` already possess full shell access inside this container. Acquiring the nonce grants that process access to the whitelisted MCP tools and `llm.*` sub-calls using the credentials of the user who initiated the command, with token usage and costs attributed to this run.
- **Server-Side Enforcement:** Consequently, command-level isolation within the same pod is not a secure boundary against same-UID processes. While the command is `starting` or `running`, any sibling process stealing the nonce can call all whitelisted MCP tools and `llm.*`. The server-side rejection list on the Bridge only prevents invoking `execute`, `ask_user`, or admin tools via the bridge; it does not close the leak of delegated MCP and model capabilities to concurrent same-container processes.
  - **Whitelisted for Delegation (`allowed_callers: ["sandbox", "direct"]`):**
    - All workspace-configured external MCP tools (e.g. `{slug}__{tool_name}`).
    - Read-only native utility tools: `read`, `calculator`, `datetime`, `memory_search`.
  - **Forbidden from Delegation (`allowed_callers: ["direct"]`):**
    - Interactive tools: `ask_user`.
    - Process/shell execution: `execute`, `monitor`.
    - Task and workspace control: `write_todos`, `persona_update`, `scheduled_tasks`, `subagent`.

---

## 4. The `cubeplex_context` SDK Contract

The client SDK `cubeplex_context` is pre-installed in the OpenSandbox image as a lightweight Python package (relying on `httpx` and `pydantic`).

### 4.1 Invoking Tools (`cubeplex_context.tools`)

Tools are accessed via dynamic attribute access or dictionary lookups:

```python
from cubeplex_context import tools

# Normalized attribute access
issues = await tools.linear__list_issues(team="CORE", limit=50)

# Exact namespaced name lookup
result = await tools["notion__query_database"](database_id="db_123")
```

#### Naming Conventions for Tools
Runtime tool names follow the `{slug}__{tool_name}` format produced by `slugify_for_namespace` (which preserves case, replaces non-alphanumeric characters with `_`, and appends connector ID suffixes on collisions or excessive length). The examples `linear__list_issues` and `notion__query_database` represent instances of this `{slug}__{tool_name}` pattern.

#### Tool Discovery & Prompt Cache Discipline
In accordance with `backend/docs/prompt-cache-discipline.md`, the system prompt prefix must remain byte-identical across conversation turns to preserve KV-cache reuse. Dynamically injecting changing tool signatures into the system prompt would invalidate prompt caches.

1. **Static System Prompt Instruction:**
   The agent's system prompt receives only a short, byte-stable instruction describing the SDK availability:
   ```text
   You can invoke workspace MCP tools and sub-models from sandbox Python scripts using `cubeplex_context`:
   `from cubeplex_context import tools, llm`
   Inspect available tools using `tools.list()` or `tools.describe("name")`.
   ```
2. **Local Type Hint File (`/workspace/.codemode/tools.pyi`):**
   - The backend API process does not mount workspace PVCs and cannot write directly to `/workspace`.
   - Instead, the `.pyi` type stub file is synchronized via the sandbox file API (`Sandbox.upload`) once the sandbox container is running, or regenerated by an in-sandbox helper prior to command execution when MCP configurations change.
   - The agent can inspect `/workspace/.codemode/tools.pyi` with `read` when full schema definitions are needed.
3. **Runtime Reflection API:**
   Scripts and agents can inspect available tools dynamically:
   ```python
   # List all tool names and descriptions
   all_tools = tools.list()

   # Inspect detailed schema and documentation for a specific tool
   schema = tools.describe("linear__list_issues")
   ```

### 4.2 Delegating to Models (`cubeplex_context.llm`)

Scripts can delegate sub-prompts, summaries, and structured extractions to the workspace's active model:

```python
from cubeplex_context import llm

# Free-form generation
summary = await llm.generate(
    prompt=f"Summarize these tickets:\n{issues}",
    system_prompt="You are an engineering manager. Output concise bullets.",
    temperature=0.2,
)

# Structured JSON classification / extraction
analysis = await llm.classify(
    data=issues,
    instruction="Extract issue priority and root cause category",
    schema={
        "type": "object",
        "properties": {
            "priority": {"type": "string", "enum": ["low", "high", "urgent"]},
            "category": {"type": "string"}
        }
    }
)
```

#### Sub-Model Anti-Recursion, Accounting & Timeouts
1. **Non-Agentic Single-Turn Only:** Sub-calls directly invoke provider completion APIs. Sub-calls **cannot use tools** and cannot spawn sub-agents. Recursion depth is strictly capped at 1.
2. **Concurrency Ceiling:** At most 4 concurrent sub-model requests are allowed per script execution. Additional calls queue asynchronously.
3. **Token Accounting & Billing:**
   - Tokens consumed by `llm.*` sub-calls MUST NOT be merged into the parent run's `turn_usage` or context tokens (merging input tokens into parent turns would artificially inflate the conversation's active `context_tokens`).
   - For every `llm.*` invocation, the Bridge records a separate `llm_call` row with `parent_run_id` pointing to the active run.
   - Financial billing is tracked via `billing_events` and `billing_llm_events` using the existing `parent_run_id` relationship.
4. **Execution Deadline:** In-sandbox execution is bounded by the command's own `timeout_seconds` (configured per command, default 3600s).

### 4.3 Context Metadata

The SDK exposes ambient execution metadata:

```python
import cubeplex_context as ctx

print(ctx.run_id)          # Current Run ID
print(ctx.workspace_id)    # Current Workspace ID
```

---

## 5. End-to-End Workflow Examples

### Example: Automated Report Generation via `docx` Skill
An agent tasked with generating a monthly incident report previously had to make 10 round trips to pull tickets, summarize each, and format a document. With `cubeplex_context`, the agent writes a single Python script and runs it via standard `execute`:

```python
import asyncio
from cubeplex_context import tools, llm
from docx import Document

async def main():
    # 1. Fetch raw data from Linear MCP
    issues = await tools.linear__list_issues(team="INFRA", state="closed")
    
    # 2. Parallel LLM summarization of root causes
    summaries = await asyncio.gather(*[
        llm.generate(f"Summarize root cause in 1 sentence: {item['description']}")
        for item in issues[:10]
    ])
    
    # 3. Create formatted Word document directly on the workspace volume
    doc = Document()
    doc.add_heading("Monthly Incident Report", level=1)
    for issue, summary in zip(issues[:10], summaries):
        doc.add_heading(issue["title"], level=2)
        doc.add_paragraph(summary)
    
    doc.save("/workspace/projects/incident-report.docx")
    print("Report generated successfully at /workspace/projects/incident-report.docx")

asyncio.run(main())
```

After `execute` finishes, the agent calls `save_artifact(path="/workspace/projects/incident-report.docx")` to register the deliverable.

---

## 6. Observability & User Experience (SSE & UI)

When a script running inside `execute` invokes `cubeplex_context.tools` or `cubeplex_context.llm`:
1. The frontend receives standard `ToolExecutionStartEvent` for `execute`.
2. As the script invokes `tools.*` or `llm.*`, the Bridge emits nested progress events:
   - Nested tool calls and sub-model queries can be streamed over a planned nested SSE event shape (e.g. `nested_tool_call` with `parent_tool_call_id`) or attached to the execution command's structured status details.
3. The Chat UI renders an expandable nested execution tree inside the command card:
   ```
   ▼ ⚙️ execute: python generate_report.py
     ├─ ↳ [Tool] linear__list_issues (fetched 10 issues)
     ├─ ↳ [LLM] 10 parallel root-cause summaries completed (1.8s)
     ├─ ↳ [FS] Saved /workspace/projects/incident-report.docx
     └─ ✔ Command completed (exit_code: 0, wall_time: 3.2s)
   ```

---

## 7. Implementation Roadmap

### Phase 1: SDK & Exchange Bridge Service
- Implement `cubeplex_context` Python client package with `tools` and `llm` interfaces.
- Mount `/api/v1/internal/context/dispatch` on `ExchangeListener` (port 8443).
- Implement local HTTP forwarding endpoint on the egress addon (in mitmproxy sidecar using standard library `http.client`), forwarding local requests over mTLS to the backend.
- Generate `command_nonce` and persist to Redis prior to `sandbox.start` (passed via `opts.envs`); accept nonce in `starting` and `running` states, backfill `provider_ref` on `persist_running`, and purge on `_terminalize` as well as `_persist_start_failed`.
- Validate mTLS client certificate CN and `sandbox_commands` status (`starting` or `running`).
- Pre-install `cubeplex_context` into the OpenSandbox container image.

### Phase 2: Tool Stubs & System Prompt Integration
- Add static SDK usage instruction to the agent system prompt (byte-stable).
- Implement `Sandbox.upload`-based synchronization of `/workspace/.codemode/tools.pyi` on sandbox startup and MCP updates.
- Provide runtime introspection (`tools.list()`, `tools.describe()`).

### Phase 3: Sub-Model Delegation, Billing & UI Visualization
- Connect `cubeplex_context.llm` to the workspace's configured model preset with strict concurrency limiting (max 4).
- Record separate `llm_call` rows with `parent_run_id` and track costs via `billing_llm_events`.
- Implement nested event serialization in `agents/stream.py` and UI nested progress tree rendering.
