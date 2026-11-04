# Code Mode for Programmatic Tool Orchestration

**Status:** Draft / Proposed Specification (Updated per design review)

**Date:** 2026-11-04

**Scope:** Agent Runtime, Tool Execution, OpenSandbox, Multi-tenant Server Safety

**Issue:** [#724](https://github.com/cubeplexai/cubeplex/issues/724)

**Pull Request:** [#729](https://github.com/cubeplexai/cubeplex/pull/729)

**Related Designs:**
- [MCP Progressive Disclosure](2026-05-27-mcp-progressive-disclosure-design.md)
- [Runtime Session Contracts](2026-09-13-runtime-session-design.md)
- [Sandbox Background Execute](2026-09-18-sandbox-background-execute-design.md)
- [Command Lifecycle](2026-09-20-command-lifecycle-design.md)

---

## 1. Executive Summary & Problem Statement

Currently, CubePlex Workspace Agents execute tools through standard step-by-step model function calling:
1. The model emits an individual tool call (e.g. `Linear__list_issues`).
2. The runtime validates, audits, and executes the tool on the backend.
3. The raw tool result (frequently dozens of kilobytes of JSON) is injected into the conversation context.
4. The model consumes this output, reasons, and decides whether to emit the next tool call.

### The Bottlenecks
- **Context Bloat & Token Inefficiency:** Intermediate payloads (e.g., retrieving 100 issues only to filter 3 matching tickets) flood the conversation context window, rapidly consuming token budgets and increasing inference latency.
- **Round-Trip Latency (RTT):** Chaining 5 dependent tool calls requires 5 consecutive LLM generation turns.
- **Fragile Control Flow:** Expressing branching, pagination loops, or fan-out aggregation through natural-language multi-turn prompts is non-deterministic and prone to hallucinations.

### The Solution: Code Mode
Instead of calling tools individually across multiple model turns, the model writes a JavaScript/TypeScript script inside a single `codemode` tool call. The code runs inside an isolated execution environment, orchestrates tools programmatically (`tools.<name>(args)`), filters intermediate data locally, and returns only the final synthesized text or structured outputs to the model.

---

## 2. Server-Side Multi-Tenant Reality & Trust Boundaries

Unlike single-user local CLI tools (e.g., `pi.dev`, Docker Agent) that execute code directly in a user's local host process, CubePlex is a multi-tenant cloud platform deployed on Kubernetes. This introduces strict architectural constraints:

### 2.1 Untrusted Execution Enclosure
- LLM-authored code is untrusted. Executing an embedded JS VM (such as QuickJS) directly inside the CubePlex Backend API process (`FastAPI`/Python) poses critical stability and security risks (CPU starvation, infinite loops, memory leaks, heap corruption, and cluster network access).
- **Architectural Principle:** Untrusted script execution MUST be delegated to the workspace's dedicated **OpenSandbox container**, completely isolated from the Backend control plane.
- **Runtime Engine:** The runner in the sandbox must be a strictly restricted **QuickJS** runtime with zero access to host filesystem, no access to `/proc`, and no child process spawning capabilities. Node.js is explicitly excluded from the sandbox runner due to its extensive host surface and inspection vectors.

### 2.2 Pod Topology & IPC Design Evaluation

When designing how scripts in the sandbox call back to host tools, three IPC models were evaluated:

| Model | Evaluation in CubePlex Kubernetes Deployment |
| :--- | :--- |
| **Interactive Exec Stdin/Stdout Stream** | **Incompatible & Fragile.** OpenSandbox's `Commands.run` uses HTTP POST + SSE stream; it does not expose an interactive bidirectional stdin pipe. Multiplexing RPC messages over stdout/stdin also risks corruption and deadlocks if script output or warnings (`console.log`) interleave with JSON protocol frames. |
| **Unix Domain Socket (UDS)** | **Cross-Node Failure.** `cubeplex-backend` and `OpenSandbox` pods are separate Pods, frequently scheduled across different physical Kubernetes Worker Nodes. UDS cannot cross network or physical host boundaries without a pod sidecar proxy. |
| **Scoped Internal Bridge via Controlled Egress (Adopted)** | **Robust & Kubernetes-Native.** Follows CubePlex's existing egress pattern (like `credential-exchange proxy`). Sandboxes remain default-deny for general outbound traffic. The bridge's internal FQDN is injected via `build_network_policy` (`force_allow_hosts`), without port matching, routing to the existing Backend API service. |

### 2.3 Ephemeral Command Nonce & Zero Persistent Credentials
To isolate tool invocation privileges to the active execution turn:
1. **No Static or Bearer Tokens:** The backend NEVER injects a general-purpose or persistent API token into the sandbox filesystem or process environment.
2. **Ephemeral Command Nonce Delivery:** When `commands.run` invokes the runner, it injects a single-use, high-entropy `command_nonce` via `opts.envs`. The nonce is strictly bound to the `execution_id` returned by `commands.run` and the active `run_id`.
3. **Cluster-Shared Redis Storage & Instant Invalidation:** The nonce record is stored exclusively in cluster-shared Redis (keyed by `execution_id` and `run_id` with a strict TTL matching command timeout, e.g., 60s). It is NEVER stored in local process memory, ensuring consistent validation across multi-replica backend pods. The moment the `codemode` command terminates, the backend immediately purges the nonce. Any lookup miss on any backend pod MUST fail-closed with HTTP 401 Unauthorized.
4. **Server-Side Default-Deny Boundary:** While processes sharing the container under UID 1000 could inspect `/proc/<pid>/environ`, the actual security boundary is enforced on the server-side Bridge: all invocations are validated against the strict `allowed_callers` whitelist, unconditionally blocking access to `execute`, `ask_user`, or unapproved tools.

### 2.4 Storage Hierarchy & Lifecycle
Files generated during Code Mode are categorized into three distinct lifecycles:

| Category | Storage Path | Persistence | Lifecycle & GC Rules |
| :--- | :--- | :--- | :--- |
| **Execution Ephemeral State** | `/tmp/codemode-run/` (tmpfs) | Non-persistent (Container memory) | QuickJS bytecode cache, temporary pipes. Discarded immediately on script exit. |
| **Overflow Output Logs** | `/workspace/.codemode/logs/` | Persistent (Workspace PVC) | Full output dumps when stdout exceeds `max_output_tokens`. Retained for 3 days; rolling GC capped at 50 MB. |
| **Media Outputs** | `/workspace/.codemode/media/` | Persistent (Workspace PVC) | Decoded images/files from `image()`. Preserved across sandbox recycles for multi-turn inspection (`view_images`) or artifact registration (`save_artifact`). |
| **User Deliverables** | `/workspace/projects/` | Persistent (Workspace PVC) | Explicitly registered by the user or agent via `save_artifact`. |

*Note on `/workspace/.codemode/`:* Using the dot-prefixed system directory `/workspace/.codemode/` prevents cluttering the user-visible file tree, protects internal logs from accidental `rm -rf /workspace/tmp/*` cleanup by users, and allows safe, scoped background garbage collection without touching user scratch files.

---

## 3. Architecture & Topology

```
┌─────────────────────────────────────────────────────────────┐
│                     Model Turn (LLM)                        │
│                                                             │
│  - Receives `codemode` tool with JSON object schema         │
│  - Receives Top-N inline tool stubs (TypeScript namespace)   │
│  - Generates orchestration script                           │
└──────────────────────────────┬──────────────────────────────┘
                               │ tool_call: codemode({"code": "..."})
                               ▼
═════════════════════════════════════════════════════════════════
                      CUBEPLEX BACKEND
 ┌───────────────────────────────────────────────────────────┐
 │ CodeModeMiddleware (cubeloop)                             │
 │                                                           │
 │  - Unwraps `code` string from tool call arguments         │
 │  - Generates Ephemeral Command Nonce (ttl = command time) │
 │  - Launches `cubeplex-codemode-runner` in OpenSandbox     │
 │                                                           │
 │ Reverse Tool Dispatch Bridge:                             │
 │  - Listens on internal endpoint (e.g. /api/v1/codemode)   │
 │  - Validates Ephemeral Command Nonce against Redis record │
 │  - Enforces Default-Deny Tool Whitelist & allowed_callers │
 │  - Executes real Native / MCP tools on behalf of script   │
 │  - Emits nested SSE events for UI observability           │
 │  - Atomically merges `store()` mutations into Checkpoint  │
 └─────────────────────────────▲─────────────────────────────┘
                               │ HTTP/JSON-RPC (X-CodeMode-Nonce)
          Dedicated Internal   │ force_allow_hosts FQDN
          Bridge Route         │ (General egress remains default-deny)
                               │
═════════════════════════════════════════════════════════════════
                 WORKSPACE OPENSANDBOX POD
 ┌───────────────────────────────────────────────────────────┐
 │ OpenSandbox Code Runner (`cubeplex-codemode-runner`)      │
 │                                                           │
 │  - Sandboxed QuickJS VM (Memory limit: 256 MB)            │
 │  - No access to host filesystem, /proc, or public network │
 │  - Pre-injected Globals:                                  │
 │      * `tools.<name>(args)` / `tools["name"](args)`       │
 │      * `text(val)`, `image(data)`                         │
 │      * `store(key, val)`, `load(key)`                     │
 │      * `searchTools(query)`, `describeTool(name)`         │
 │      * `ALL_TOOLS`, `console.log(...)`, `exit()`          │
 │  - Sends tool requests to Bridge via local internal route │
 │  - Writes media / overflow dumps to `/workspace/.codemode/`│
 └───────────────────────────────────────────────────────────┘
```

---

## 4. Detailed Design & Contracts

### 4.1 Tool Parameter Schema (Provider & Runtime Compatibility)

OpenAI Chat, OpenAI Responses, and Anthropic APIs strictly require tool parameters to be a JSON Schema of `type: "object"`. Root `type: "string"` schemas are rejected by model providers and cannot be represented by CubeLoop's Pydantic-backed `AgentTool.parameters`.

The tool parameter schema is therefore defined as a single-property object:

```json
{
  "type": "object",
  "properties": {
    "code": {
      "type": "string",
      "description": "JavaScript/TypeScript code to execute in the QuickJS sandbox."
    }
  },
  "required": ["code"]
}
```

The host middleware unwraps `arguments["code"]` before streaming it to the sandbox runner, avoiding multi-level escaping inside the sandbox.

An optional execution directive comment may be placed on the first line:
```javascript
// @options: {"timeout_ms": 30000, "max_output_tokens": 4000}
```

### 4.2 Script Runtime Environment & Globals

The script executes as an `async` function body. Its completion return value is ignored; data MUST be emitted via `text()` or `image()`.

| Global API | Type Signature | Description |
| :--- | :--- | :--- |
| `tools.<name>` | `(args: object) => Promise<any>` | Call an eligible tool using normalized identifier syntax. |
| `tools[name]` | `(args: object) => Promise<any>` | Canonical access using the exact un-normalized tool name (e.g. `tools["linear__create_issue"]`). |
| `text(value)` | `(value: any) => void` | Append formatted text/JSON to the model response. |
| `image(data)` | `(data: string \| object) => Promise<string>` | Decodes and saves image to `/workspace/.codemode/media/` and resolves to the relative sandbox path. |
| `store(k, v)` | `(key: string, value: any) => void` | Buffer state mutation for cross-call persistence. |
| `load(k)` | `(key: string) => any` | Read previously persisted key from session store. |
| `searchTools(q)` | `(query: string, opts?: {limit?: number}) => Promise<ToolSummary[]>` | BM25 search over all available workspace tools. |
| `describeTool(n)`| `(name: string) => Promise<string \| undefined>` | Return TypeScript declaration for an un-inlined tool. |
| `ALL_TOOLS` | `Array<{name: string, description: string}>` | Complete catalog of available tools. |
| `console.*` | `(...args: any[]) => void` | Diagnostic logging, surfaced in a separate `<console>` block. |
| `exit()` | `() => void` | Terminate script execution immediately with success. |

#### Identifier Normalization & Disambiguation Rules
- Tool names are mapped to property names by converting non-alphanumeric characters to `_`.
- If the normalized name starts with a digit, it is prefixed with `t_`.
- If multiple tools collide to the same property name (e.g., `create-issue` and `create_issue`), the first registered tool retains the property name and subsequent tools receive a deterministic suffix (`_2`, `_3`).
- `tools["Exact_Name"]` is always available and bypasses property name mapping.

### 4.3 Default-Deny Access Policy & Tool Classification

The reverse bridge enforces a strict **Default-Deny** whitelist based on each tool's `allowed_callers` attribute:

1. **Eligible in Code Mode (`allowed_callers: ["code_mode", "direct"]`):**
   - External MCP Tools (e.g. `Linear`, `Notion`, `GitHub`, `Jira`).
   - Read-only native utility tools: `read`, `calculator`, `datetime`, `memory_search`.
2. **Direct-Only / Forbidden in Code Mode (`allowed_callers: ["direct"]`):**
   - User interaction: `ask_user`.
   - Task planning: `write_todos`.
   - Host shell & process execution: `execute`, `monitor`.
   - Side-effecting / state-modifying operations: `write`, `edit`, `memory_save`, `memory_update`, `scheduled_tasks`, `save_artifact`, `present_file`, `subagent`, `persona_update`.

### 4.4 Non-Interactive Human-In-The-Loop (HITL) Model

CubeLoop's `CheckpointedChannel` raises `HitlDurabilityNotGuaranteed` when awaited inside custom tool bodies, and the sandbox runner enforces a hard execution deadline (60s default, max 300s). Therefore:
- Code Mode **does not support suspend-and-resume** of live sandbox processes.
- If a script invokes an unlisted or unauthorized tool, the bridge immediately rejects the invocation with an actionable error message (`Error: Tool '<name>' is restricted to direct calling and requires confirmation`).
- The model observes the rejection in the script output and may invoke the tool natively in the subsequent turn.

### 4.5 State Persistence (`store` & `load`)

To prevent collisions with CubeLoop's checkpointer data (which uses `cubepi_threads.extra` for compaction and pinned memory):
- All Code Mode state is isolated under a dedicated namespace key: `extra["codemode_store"]`.
- The runner buffers `store(k, v)` mutations in memory during execution.
- If the script succeeds, the buffered mutations are flushed back in the final execution payload and merged into `extra["codemode_store"]`.
- If the runner fails, times out, or crashes, pending mutations are discarded.

### 4.6 Multimodal Image Handling

Current model provider converters (`OpenAIProvider`, `OpenAIResponsesProvider`, `AnthropicProvider`) and `agents/stream.py` only forward `TextContent` on tool results (images are supported only on user messages).

- **Phase 1 & 2 Contract:** `image()` decodes image data, writes it to `/workspace/.codemode/media/img_<hash>.<ext>`, and returns the persistent sandbox path in the text output. The model can inspect the image in a subsequent turn using `view_images(paths=[...])` or register it via `save_artifact`.
- **Phase 3 Evolution:** Extend provider converters and SSE serialization to support native `ImageContent` on tool results so images display directly within the Code Mode execution card.

### 4.7 Architectural Decision: LLM Requests inside Code Mode

**Decision:** Direct / recursive LLM chat requests (`models.chat()`) are **strictly prohibited** inside Code Mode.

**Rationale:**
1. Allowing scripts to invoke conversational LLMs introduces infinite recursive execution loops.
2. Unbounded loops fan out dozens of model queries in parallel, creating severe token cost spikes.
3. Sub-queries bypass workspace persona prompts, prompt caching policies, and audit trails.

*Future Scope:* Dedicated, non-chat auxiliary models (e.g. `models.classify()` for structured batch scoring or text embeddings) may be considered in future releases, subject to strict concurrency bounds (max 4 parallel workers) and separate token budgets.

---

## 5. Observability & User Experience (SSE & UI)

To support real-time visualization of nested tool calls inside `codemode`:

### 5.1 CubeLoop Event Model Extensions

Incrementally extend CubeLoop's existing `ToolExecutionStartEvent` and `ToolExecutionEndEvent` (in `cubeloop/agent/types.py`, which inherit from Pydantic `BaseModel` with a `type: Literal[...]` discriminator) by adding nesting metadata fields, while preserving all existing fields (`tool_call_id`, `tool_name`, `args: JsonObject`, `result: StructuredValue`, `is_error`, etc.):

```python
# Incremental fields added to cubeloop/agent/types.py:
parent_tool_call_id: str | None = None  # Populated when invoked inside codemode
is_nested: bool = False                 # True for tool calls originated from a script
```

### 5.2 Chat UI Rendering
- The Chat UI renders a collapsible **Code Mode Execution Card** for the parent `codemode` call.
- Nested events (`is_nested=True`) stream in real-time under a progressive execution tree:
  ```
  ▼ Code Mode Execution (codemode)
    ├─ ↳ [Done] linear__list_issues (args: {limit: 50})
    ├─ ↳ [Done] notion__query (args: {filter: "active"})
    └─ ↳ [Running] Data aggregation & filtering...
  ```
- Debug statements (`console.log`) are isolated into a collapsible `<console>` diagnostics block.

---

## 6. Implementation Milestones

### Phase 1: Harness & Tool Engine (`cubeloop`)
- Add `allowed_callers: list[str]` to `AgentTool`.
- Implement `CodeModeMiddleware` with TypeScript stub generation and inline token budgeting (3000 tokens).
- Add `parent_tool_call_id` and `is_nested` to CubeLoop tool execution events.

### Phase 2: OpenSandbox Integration & Reverse Bridge (`cubeplex`)
- Implement `cubeplex-codemode-runner` in QuickJS for OpenSandbox image.
- Configure controlled internal FQDN route in `build_network_policy` (`force_allow_hosts`) for Bridge endpoint.
- Implement Reverse Tool Dispatch Bridge in `cubeplex` with Ephemeral Command Nonce lifecycle.
- Wire `CodeModeMiddleware` into `run_manager.py` middleware composition.
- Connect `store` / `load` to `extra["codemode_store"]` in PostgreSQL checkpointer.

### Phase 3: Frontend Visualization & Multimodal Streaming
- Update `agents/stream.py` to serialize nested tool execution events over SSE.
- Build collapsible Code Mode execution card and nested progress timeline in the Chat UI.
- Extend model provider converters to forward `ImageContent` on tool result messages.
