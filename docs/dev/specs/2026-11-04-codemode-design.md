# Code Mode: SDK-Driven Sandbox Execution & Agent Capability Delegation

**Status:** Draft / Architectural Redesign (Rev. 3)

**Date:** 2026-11-04

**Scope:** Agent Runtime, OpenSandbox Environment, SDK-Driven Tool & Model Delegation

**Issue:** [#724](https://github.com/cubeplexai/cubeplex/issues/724)

**Pull Request:** [#729](https://github.com/cubeplexai/cubeplex/pull/729)

**Related Designs:**
- [MCP Progressive Disclosure](2026-05-27-mcp-progressive-disclosure-design.md)
- [Runtime Session Contracts](2026-09-13-runtime-session-design.md)
- [Sandbox Background Execute](2026-09-18-sandbox-background-execute-design.md)
- [Command Lifecycle](2026-09-20-command-lifecycle-design.md)

---

## 1. Executive Summary & Vision

Existing implementations of "Code Mode" (e.g. `pi.dev`, Cloudflare Agents, Docker Agent) treat code execution primarily as an in-memory "tool calling macro": the model writes tiny snippets of JavaScript in an isolated, memory-only QuickJS sandbox purely to chain API calls and avoid round-trips.

In CubePlex, this single-process CLI model is both **unnecessary** and **counter-productive**:
1. **Redundant Nested Sandboxing:** CubePlex already provisions an enterprise-grade, kernel-isolated **OpenSandbox container** for each workspace. Spawning an artificial, restricted QuickJS engine *inside* an already isolated Linux container creates redundant isolation layers and cuts the agent off from the rich Linux software stack.
2. **Artificial Filesystem Crippling:** Restricting filesystem access runs counter to the agent's core purpose. Agents already possess native file operations (`read`, `write`, `execute`). Many production workflows (such as `docx`, `pptx`, `xlsx`, `pdf`, data science with `pandas`/`matplotlib`, and large dataset processing) fundamentally rely on manipulating real files on the workspace volume.
3. **The Core Opportunity — SDK Delegation:** When the agent writes and executes code in the sandbox, that code should not be an isolated island. It should be a **first-class program empowered by the Agent itself**.

### The Architectural Shift
Code Mode in CubePlex is redesigned as **SDK-Driven Sandbox Program Execution**:
- The agent executes full-featured code (Python / TypeScript) directly in the OpenSandbox environment with normal workspace filesystem access.
- The environment provides a pre-installed, high-level client SDK (`cubeplex` Python/TS library).
- Through `from cubeplex.agent import tools, llm`, scripts running inside the sandbox can directly invoke the Agent's configured tools (MCP servers, native tools) and delegate sub-prompts to the Agent's available LLMs (for structured data extraction, summarization, and content generation).

---

## 2. Server-Side Architecture & Trust Model

```
┌─────────────────────────────────────────────────────────────┐
│                     Model Turn (LLM)                        │
│                                                             │
│  - Receives `codemode` tool with code execution arguments   │
│  - Emits Python/TypeScript script leveraging `cubeplex` SDK │
└──────────────────────────────┬──────────────────────────────┘
                               │ tool_call: codemode({"code": "...", "language": "python"})
                               ▼
═════════════════════════════════════════════════════════════════
                      CUBEPLEX BACKEND
 ┌───────────────────────────────────────────────────────────┐
 │ CodeModeMiddleware (cubeloop)                             │
 │  - Unwraps `code` and language options                    │
 │  - Issues Ephemeral Command Nonce (ttl = timeout)         │
 │  - Dispatches execution to OpenSandbox via `commands.run` │
 │                                                           │
 │ Reverse Tool & LLM Dispatch Bridge:                       │
 │  - Internal API endpoint (routed via force_allow_hosts)   │
 │  - Validates Ephemeral Nonce against cluster-shared Redis │
 │  - Enforces Default-Deny whitelist on delegated tools     │
 │  - Handles tool calls: executes MCP / Native tools        │
 │  - Handles LLM calls: invokes configured Workspace models │
 │    (with strict turn-level concurrency & token ceilings)  │
 │  - Emits real-time nested SSE events to the UI            │
 └─────────────────────────────▲─────────────────────────────┘
                               │
            HTTP / JSON-RPC    │ Internal Service Route
        (X-CodeMode-Nonce)     │ (General sandbox egress remains default-deny)
                               │
═════════════════════════════════════════════════════════════════
                 WORKSPACE OPENSANDBOX CONTAINER
 ┌───────────────────────────────────────────────────────────┐
 │ Native Container Environment (Python 3.12+ / Node 22+)     │
 │                                                           │
 │  - Full access to `/workspace` filesystem (PVC)           │
 │  - Full access to installed packages (pandas, docx, etc.) │
 │                                                           │
 │ Pre-installed SDK (`cubeplex`):                           │
 │   * `from cubeplex.agent import tools, llm`               │
 │   * `tools.<name>(**kwargs)` ──> calls Bridge             │
 │   * `llm.generate(prompt, **kwargs)` ──> calls Bridge     │
 │   * `llm.classify(data, schema)` ──> calls Bridge         │
 │                                                           │
 │ Script Output:                                            │
 │   - Writes deliverables directly to `/workspace/...`      │
 │   - Dumps overflow execution logs to `/workspace/.codemode`│
 │   - Standard stdout/stderr captured as tool result        │
 └───────────────────────────────────────────────────────────┘
```

### 2.1 Why Avoid Nested Sandboxing
- **Eliminate QuickJS/VM Overhead:** No need to cross-compile, distribute, or debug a separate C/WASM JavaScript engine inside the container.
- **Ecosystem Leverage:** Document creation skills (`docx`, `pptx`, `xlsx`) already produce Python code relying on libraries like `python-docx`, `python-pptx`, `openpyxl`, and `reportlab`. Forcing them through a stripped QuickJS environment broke their primary capabilities.
- **Natural File Handling:** When a tool (like an MCP database reader) returns 50 MB of data, the script can simply stream it into `/workspace/.codemode/data.json` or a SQLite database, process it with standard tools, and avoid in-memory limits entirely.

### 2.2 Security & Isolation Boundaries
1. **The Container is the Boundary:** All scripts run under UID 1000 (`cubeplex`) inside the workspace's dedicated container with cgroups-enforced memory/CPU limits.
2. **Ephemeral Nonce Lifecycle:**
   - Every `commands.run` invocation generates a cryptographic, single-use `command_nonce` passed in `opts.envs`.
   - The nonce is registered in **cluster-shared Redis** and bound to the command's `execution_id` and active `run_id`.
   - The moment the command exits (or reaches its deadline), the nonce is immediately invalidated.
   - Any lookup miss on any backend replica fails-closed (HTTP 401).
3. **Server-Side Bridge Whitelist (Default-Deny):**
   - The SDK communicates with the backend solely via the Bridge endpoint, routed through `build_network_policy` (`force_allow_hosts`).
   - The Bridge enforces an explicit whitelist: calling `execute`, `ask_user`, or shell management from inside sandbox scripts is blocked.

---

## 3. The `cubeplex` In-Sandbox SDK

The OpenSandbox image includes pre-installed Python (`cubeplex`) and TypeScript (`@cubeplex/agent`) client packages that automatically discover the backend bridge via ambient environment variables (`CUBEPLEX_BRIDGE_URL`, `CUBEPLEX_COMMAND_NONCE`, `CUBEPLEX_RUN_ID`).

### 3.1 Python SDK Surface (`cubeplex.agent`)

```python
from cubeplex.agent import tools, llm

# 1. Calling Agent Tools (MCP & Whitelisted Native Tools)
# Tools are accessible via attribute or dictionary lookup
issues = await tools.linear__list_issues(team="CORE", limit=50)
content = await tools["notion__query_database"](database_id="db_123")

# 2. Delegating to Model Capabilities
# Quick text/code generation using the workspace's active model
summary = await llm.generate(
    prompt=f"Extract top 3 blockers from these issues:\n{issues}",
    system_prompt="You are a technical project manager. Be concise.",
    temperature=0.2,
)

# Structured JSON classification / extraction
analysis = await llm.classify(
    data=issues,
    instruction="Categorize each issue by component and urgency",
    schema={
        "component": "string",
        "urgency": "low | medium | high"
    }
)
```

### 3.2 TypeScript SDK Surface (`@cubeplex/agent`)

```typescript
import { tools, llm } from "@cubeplex/agent";

// Tool execution
const issues = await tools.linear__list_issues({ team: "CORE", limit: 50 });

// LLM generation
const summary = await llm.generate({
  prompt: `Summarize:\n${JSON.stringify(issues)}`,
  temperature: 0.2,
});
```

---

## 4. Sub-Model Execution Guardrails (Anti-Recursion)

Allowing scripts to query LLMs introduces powerful sub-task parallelization, but must be strictly bounded to prevent cost explosions and infinite loops:

1. **Non-Agentic Completion Only:**
   - `llm.generate()` and `llm.classify()` call the model provider directly for single-turn completions.
   - Sub-calls **do not have access to tools** and cannot trigger sub-agents or nested `codemode` sessions. Recursion depth is capped at 1.
2. **Turn-Level Concurrency & Rate Limits:**
   - At most 4 concurrent sub-model calls per script invocation. Excess calls queue asynchronously.
   - Hard execution deadline (default 60s, max 300s configurable via `@options`).
3. **Token & Budget Attribution:**
   - Input and output tokens consumed by `llm.*` calls within the script are tracked by the Bridge and appended to the parent turn's `usage` metadata in PostgreSQL.
   - If the workspace exceeds its token rate limit or cost budget, the Bridge rejects subsequent sub-model calls with HTTP 429.

---

## 5. Tool Parameter & Return Contracts

### 5.1 Tool Schema
The `codemode` tool is exposed to the primary agent as a standard JSON object:

```json
{
  "type": "object",
  "properties": {
    "code": {
      "type": "string",
      "description": "Python (default) or TypeScript code to execute in the workspace sandbox."
    },
    "language": {
      "type": "string",
      "enum": ["python", "typescript"],
      "default": "python",
      "description": "Scripting runtime to use."
    }
  },
  "required": ["code"]
}
```

Optional execution options can still be declared on the first line:
```python
# @options: {"timeout_ms": 60000, "max_output_tokens": 8000}
```

### 5.2 Output & Deliverable Handling
- **Stdout / Result:** The tool result returns standard `stdout`/`stderr` from the script process.
- **Deliverables:** Files generated by scripts (e.g., reports, charts, CSVs) are saved directly onto `/workspace/` (e.g. `/workspace/projects/sales-report.docx`). The agent can immediately present them via `save_artifact` or `present_file` in the subsequent turn.
- **Large Log Spills:** If standard output exceeds `max_output_tokens`, the full text is dumped to `/workspace/.codemode/logs/exec_<id>.log` and truncated cleanly in the context.

---

## 6. Observability & User Experience (SSE & UI)

When an agent executes a `codemode` script that delegates tasks:
1. The frontend receives `ToolExecutionStartEvent` for `codemode`.
2. As the script calls `tools.linear__list_issues()` or `llm.generate()`, the Bridge emits nested events:
   - `ToolExecutionStartEvent(tool_name="linear__list_issues", is_nested=True, parent_tool_call_id=...)`
   - `LLMExecutionStartEvent(model="gpt-5.6", is_nested=True, parent_tool_call_id=...)`
3. The Chat UI renders a cohesive **Code Mode Pipeline Card**:
   - The main block shows the Python/TS code.
   - A nested real-time progress accordion displays sub-actions:
     ```
     ▼ 🛠️ Code Mode Execution (python)
       ├─ ↳ [Tool] linear__list_issues (fetched 50 issues)
       ├─ ↳ [LLM] Summarized blockers via claude-sonnet-4-5 (1.2s)
       ├─ ↳ [FS] Generated /workspace/projects/blockers.docx (14 KB)
       └─ ✔ Completed successfully in 4.3s
     ```

---

## 7. Implementation Roadmap

### Phase 1: SDK Packaging & Reverse Bridge Service
- Implement `cubeplex-sdk` Python package with `cubeplex.agent.tools` and `cubeplex.agent.llm`.
- Build the Backend Bridge service endpoint (`/api/v1/codemode/dispatch`), including Redis-backed Ephemeral Nonce validation and Default-Deny tool filtering.
- Inject Bridge FQDN into sandbox `build_network_policy` (`force_allow_hosts`).

### Phase 2: Agent Harness & CodeMode Middleware
- Implement `CodeModeMiddleware` in `cubeloop` / `cubeplex`.
- Generate Python type definitions and docstrings for available workspace tools into the system prompt.
- Route `codemode` invocations to `commands.run` in OpenSandbox.

### Phase 3: Observability & Production Hardening
- Extend `agents/stream.py` to stream nested tool and LLM execution events.
- Implement Chat UI pipeline cards for nested code executions.
- Add concurrency semaphores and token accounting for in-sandbox `llm.*` queries.
