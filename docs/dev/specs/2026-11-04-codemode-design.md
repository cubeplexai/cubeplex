# Code Mode for Programmatic Tool Orchestration

**Status:** Draft / Proposed Specification

**Date:** 2026-11-04

**Scope:** Agent Runtime, Tool Execution, OpenSandbox, Multi-tenant Server Safety

**Issue:** [#724](https://github.com/cubeplexai/cubeplex/issues/724)

**Related Designs:**
- [MCP Progressive Disclosure](2026-05-27-mcp-progressive-disclosure-design.md)
- [Runtime Session Contracts](2026-09-13-runtime-session-design.md)
- [Sandbox Background Execute](2026-09-18-sandbox-background-execute-design.md)
- [Command Lifecycle](2026-09-20-command-lifecycle-design.md)

---

## 1. Executive Summary & Problem Statement

Currently, CubePlex Workspace Agents execute tools through standard model function calling:
1. The model emits a tool call (e.g. `Linear__list_issues`).
2. The runtime validates, audits, and executes the tool.
3. The raw tool result (often dozens of kilobytes of JSON) is injected into the conversation context.
4. The model consumes this output, reasons, and decides whether to emit the next tool call.

### The Bottlenecks
- **Context Bloat & Token Inefficiency:** Intermediate payloads (e.g., retrieving 100 issues only to filter 3 matching tickets) flood the conversation context window, rapidly consuming token budget and increasing latency.
- **Round-Trip Latency (RTT):** Chaining 5 dependent tool calls requires 5 consecutive LLM generation turns.
- **Fragile Control Flow:** Expressing branching, pagination loops, or fan-out aggregation through natural-language multi-turn prompts is non-deterministic and prone to hallucinations.

### The Solution: Code Mode
Instead of calling tools individually across multiple model turns, the model writes a JavaScript/TypeScript script inside a single `codemode` tool call. The code runs inside an isolated execution environment, orchestrates tools programmatically (`tools.<name>(args)`), filters intermediate data locally, and returns only the final synthesized text and images to the model.

---

## 2. Server-Side Multi-Tenant Reality & Key Constraints

Unlike single-user local CLI tools (e.g., `pi.dev`, Docker Agent) that execute code directly in a local host process:

1. **Strict Multi-Tenant Isolation:**
   - LLM-authored code is untrusted. Running an embedded JS VM (such as QuickJS) directly inside the CubePlex Backend API process (`FastAPI`/Python) poses critical stability and security risks (e.g., CPU starvation, infinite loops, memory leaks, heap corruption, and cluster network access).
   - **Architectural Principle:** Untrusted script execution MUST be delegated to the workspace's dedicated **OpenSandbox container**, completely separated from the Backend control plane.
2. **Ephemeral Distributed Backend:**
   - CubePlex Backend runs across multiple auto-scaling pods.
   - Cross-call state (`store`/`load`) cannot rely on in-memory backend globals and must be durably stored in the PostgreSQL checkpoint store.
3. **Storage & Multi-Modal Alignment:**
   - Intermediate images or artifacts produced by scripts must reside directly in the workspace filesystem (`/workspace/tmp/`), where they can be accessed by the user, inspected via `view_images`, or registered with `save_artifact`.

---

## 3. Architecture & Topology

```
┌─────────────────────────────────────────────────────────────┐
│                     Model Turn (LLM)                        │
│                                                             │
│  - Receives `codemode` tool (raw JS source input)           │
│  - Receives Top-N inline tool stubs (TypeScript namespace)   │
│  - Generates orchestration script                           │
└──────────────────────────────┬──────────────────────────────┘
                               │ tool_call: codemode(raw_code)
                               ▼
═════════════════════════════════════════════════════════════════
                      CUBEPLEX BACKEND POD
 ┌───────────────────────────────────────────────────────────┐
 │ CodeModeMiddleware (cubeloop)                             │
 │                                                           │
 │  - Intercepts `codemode` execution                        │
 │  - Generates short-lived IPC Bridge Token                 │
 │  - Dispatches execution request to OpenSandbox            │
 │  - Hosts Reverse Tool Dispatch Bridge:                    │
 │      * Validates token and caller identity                │
 │      * Enforces RBAC, Tool Policy & Egress rules          │
 │      * Executes real Native/MCP tool                      │
 │      * Emits nested SSE events for UI observability       │
 │  - Persists `store()` mutations into Checkpoint DB        │
 └─────────────────────────────┬─────────────────────────────┘
                               │
            gRPC / Exec API    │ (Mutual authenticated IPC)
                               ▼
═════════════════════════════════════════════════════════════════
                 WORKSPACE OPENSANDBOX POD
 ┌───────────────────────────────────────────────────────────┐
 │ OpenSandbox Code Runner (`cubeplex-codemode-runner`)      │
 │                                                           │
 │  - Lightweight QuickJS / isolated Node VM context         │
 │  - Memory Limit: 256 MB, CPU quota enforced by cgroups    │
 │  - Pre-injected Globals:                                  │
 │      * `tools.<normalized_name>(args)`                    │
 │      * `text(val)`, `image(block)`, `return val`          │
 │      * `store(key, val)`, `load(key)`                     │
 │      * `searchTools(query)`, `describeTool(name)`         │
 │      * `ALL_TOOLS`, `console.log(...)`                    │
 │  - Saves generated images/temp files to `/workspace/tmp/` │
 └───────────────────────────────────────────────────────────┘
```

---

## 4. Detailed Design & Contracts

### 4.1 Tool Signature & Input Contract

Following modern best practices (avoiding JSON escaping overhead for multi-line code):
- **Tool Name:** `codemode`
- **Input Type:** Raw string (`type: "string"`). No wrapping JSON object, no Markdown code fences (` ``` `).
- **Execution Directives:** The script may begin with an optional options comment on the first line:
  ```javascript
  // @options: {"timeout_ms": 30000, "max_output_tokens": 4000}
  ```

### 4.2 Script Runtime Environment & Globals

Inside the sandbox runtime, the script executes as an `async` function body (top-level `await` supported):

| Global API | Type Signature | Description |
| :--- | :--- | :--- |
| `tools.<name>` | `(args: object) => Promise<any>` | Call an eligible tool. Tool names are normalized to valid JS identifiers (`[^a-zA-Z0-9_]` replaced by `_`). |
| `text(value)` | `(value: any) => void` | Append formatted text/JSON to the model response. |
| `image(value)` | `(value: ImageBlock \| string) => void` | Output an image. Automatically writes raw bytes to `/workspace/tmp/` and passes structured multimodal blocks back to the model. |
| `store(k, v)` | `(key: string, value: any) => void` | Save small JSON metadata across codemode calls in the conversation. |
| `load(k)` | `(key: string) => any` | Read previously stored value. |
| `searchTools(q)` | `(query: string, opts?: {limit?: number}) => Promise<ToolSummary[]>` | BM25 search over all available workspace tools. |
| `describeTool(n)`| `(name: string) => Promise<string \| undefined>` | Return the complete TypeScript declaration for an un-inlined tool. |
| `ALL_TOOLS` | `Array<{name: string, description: string}>` | Complete catalog of available tools. |
| `console.*` | `(...args: any[]) => void` | Diagnostic logging, surfaced in a separate `<console>` block. |
| `exit()` | `() => void` | Immediately exit script with success. |

### 4.3 Tool Partitioning & Allowed Callers

Tools registered in a workspace are categorized by execution safety and ergonomics:

1. **`Direct-Only` (Excluded from Code Mode):**
   - User interaction: `ask_user`.
   - Workspace task planning: `write_todos`.
   - Host shell interaction: `execute` (avoids nested shell quoting issues).
2. **`Code-Mode-Only` or `Dual-Mode` (Direct + Code Mode):**
   - All external MCP Tools (`Linear`, `Notion`, `GitHub`, `Jira`, etc.).
   - Pure compute and read-only native tools: `read`, `calculator`, `datetime`, `memory_search`.

### 4.4 Progressive Tool Discovery & Token Budgeting

To prevent overwhelming the system prompt when dozens of MCP servers are loaded:
- **Inline Declaration Budget:** Up to `3000` tokens of TypeScript declarations are statically embedded in the `codemode` description.
- **Dynamic On-Demand Discovery:** For tools outside the inline budget, the model uses `searchTools(query)` or `describeTool(name)` within code mode before calling them.

---

## 5. Security, Isolation & Safety Guardrails

1. **Sandbox Enclosure:**
   - All code executes in the OpenSandbox container under standard non-root privileges.
   - Resource quotas: Max 256 MB memory, max 60s execution timeout (configurable up to 300s via `// @options`).
2. **Reverse Bridge Authentication:**
   - The Backend issues an ephemeral, single-turn IPC Bearer Token when dispatching `codemode`.
   - The Sandbox Runner uses this token to communicate back to the Backend Bridge.
   - The token expires immediately when the `codemode` execution terminates.
3. **Output Defense:**
   - Output buffer capped at 16 MB.
   - Text items longer than `max_output_tokens` are head/tail truncated, with the full output retained in a temporary file under `/workspace/tmp/`.
4. **HITL & Permissions:**
   - Any sensitive tool invocation made via `tools.<name>` triggers the existing CubePlex tool approval and confirmation policies. If a call requires user approval, the execution is suspended or cleanly rejected with an actionable permission message.

---

## 6. Observability & User Experience (SSE & UI)

When an agent invokes `codemode`:
1. The frontend receives a `ToolExecutionStartEvent` for `codemode`.
2. As the script runs and calls `tools.Linear__list_issues(...)`, the Backend Bridge emits nested events:
   - `ToolExecutionStartEvent(tool_name="Linear__list_issues", is_nested=True, parent_tool_call_id=...)`
   - `ToolExecutionEndEvent(..., is_nested=True)`
3. The Chat UI renders an expandable **Code Mode Execution Card**:
   - Shows the generated code with syntax highlighting.
   - Displays real-time progress steps for each nested tool call.
   - Separates script output (`text()`, `image()`) from debug diagnostics (`<console>`).

---

## 7. Implementation Milestones

- **Phase 1 (Harness / cubeloop):**
  - Implement `CodeModeMiddleware`, TypeScript stub generator, and `allowed_callers` tool attribute in `cubeloop`.
  - Implement standard QuickJS/Node script executor runner.
- **Phase 2 (Backend & OpenSandbox Bridge):**
  - Implement reverse dispatch IPC bridge in `cubeplex.backend`.
  - Package `cubeplex-codemode-runner` into OpenSandbox image.
  - Wire `run_manager.py` to route eligible tools through `CodeModeMiddleware`.
- **Phase 3 (State & UI):**
  - Persist `store()` data in PostgreSQL checkpointer.
  - Emit nested SSE events and update frontend Chat components for nested tool execution visualization.
