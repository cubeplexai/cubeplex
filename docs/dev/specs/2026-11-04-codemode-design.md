# In-Sandbox Agent Delegation via `cubeplex_context`

**Status:** Draft / Architectural Redesign (Rev. 4)

**Date:** 2026-11-04

**Scope:** OpenSandbox Environment, In-Sandbox SDK (`cubeplex_context`), Tool & Model Delegation

**Issue:** [#724](https://github.com/cubeplexai/cubeplex/issues/724)

**Pull Request:** [#729](https://github.com/cubeplexai/cubeplex/pull/729)

**Related Designs:**
- [MCP Progressive Disclosure](2026-05-27-mcp-progressive-disclosure-design.md)
- [Runtime Session Contracts](2026-09-13-runtime-session-design.md)
- [Sandbox Background Execute](2026-09-18-sandbox-background-execute-design.md)
- [Command Lifecycle](2026-09-20-command-lifecycle-design.md)

---

## 1. Executive Summary & Vision

Existing "Code Mode" implementations in single-process CLI tools (e.g., `pi.dev`, Cloudflare Agents, Docker Agent) treat code execution as an isolated in-memory "tool calling macro". In those systems, the model writes JavaScript inside a synthetic, memory-only QuickJS VM with zero filesystem access purely to avoid network round-trips for API tool calls.

In CubePlex, this design is both **redundant** and **counter-productive**:
1. **OpenSandbox is Already the Isolation Boundary:** CubePlex already provisions an enterprise-grade, kernel-isolated **OpenSandbox container** for each workspace. Running a stripped QuickJS sandbox *inside* an already isolated container adds fragile nested sandboxing ("sandbox-in-a-sandbox") while discarding the rich Linux/Python runtime.
2. **Filesystem Access is Essential:** The agent already has unrestricted read/write/exec permissions in its workspace. Heavy productivity skills (`docx`, `pptx`, `xlsx`, `pdf`, data science with `pandas`/`matplotlib`) fundamentally rely on reading and writing real files on `/workspace` (PVC).
3. **No Redundant Tool Concept:** Adding a separate `codemode` tool causes cognitive conflict for the model (forcing it to choose between `execute` and `codemode`) and fragments execution pipelines.

### The Architectural Breakthrough: In-Sandbox Delegation
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
│  - Reads available tools via prompt stubs / tools.pyi        │
│  - Writes Python script using `from cubeplex_context ...`   │
│  - Executes script via standard `execute(command="...")`    │
└──────────────────────────────┬──────────────────────────────┘
                               │ tool_call: execute("python script.py")
                               ▼
═════════════════════════════════════════════════════════════════
                      CUBEPLEX BACKEND
 ┌───────────────────────────────────────────────────────────┐
 │ Sandbox Service / Run Manager                             │
 │  - Injects `CUBEPLEX_COMMAND_NONCE` into `opts.envs`      │
 │  - Launches command via OpenSandbox `commands.run`        │
 │                                                           │
 │ Reverse Tool & LLM Dispatch Bridge:                       │
 │  - Listens on internal endpoint (e.g. /api/v1/context)    │
 │  - Validates Ephemeral Command Nonce from shared Redis    │
 │  - Enforces Default-Deny whitelist on delegated tools     │
 │  - Executes MCP / Native tools on behalf of the script    │
 │  - Executes single-turn model completions via LLM provider│
 │  - Emits real-time nested SSE events for UI rendering     │
 └─────────────────────────────▲─────────────────────────────┘
                               │
            HTTP / JSON-RPC    │ Internal Service Route
        (X-CodeMode-Nonce)     │ (force_allow_hosts FQDN, no port match)
                               │ (General egress remains default-deny)
═════════════════════════════════════════════════════════════════
                 WORKSPACE OPENSANDBOX CONTAINER
 ┌───────────────────────────────────────────────────────────┐
 │ OpenSandbox Environment (Python 3.12+ / Linux)            │
 │                                                           │
 │  - Full native access to `/workspace` filesystem (PVC)    │
 │  - Pre-installed `cubeplex_context` Python library        │
 │                                                           │
 │ In-Sandbox Script Execution:                              │
 │   from cubeplex_context import tools, llm                 │
 │                                                           │
 │   * `tools.<name>(**kwargs)` ──> calls Reverse Bridge     │
 │   * `llm.generate(prompt)`   ──> calls Reverse Bridge     │
 │   * `llm.classify(data)`     ──> calls Reverse Bridge     │
 │                                                           │
 │ Script Output & Artifacts:                                │
 │   - Writes deliverables directly to `/workspace/projects/`│
 │   - Standard stdout/stderr returned to Agent as normal    │
 └───────────────────────────────────────────────────────────┘
```

---

## 3. Trust Boundary & Security Architecture

### 3.1 Network Egress & Service Routing
- The OpenSandbox container operates under a **default-deny outbound network policy**.
- Access to the CubePlex Backend Reverse Bridge is enabled by injecting the Bridge's internal FQDN into `build_network_policy` (`force_allow_hosts`), matching CubePlex's established egress pattern (identical to `credential-exchange proxy`).
- General outbound internet access remains strictly blocked unless explicitly permitted by workspace egress policy.

### 3.2 Ephemeral Command Nonce Lifecycle
To isolate privileges to active command turns and prevent unauthorized reuse:
1. When `commands.run` is called, the backend generates a cryptographically secure, single-use `command_nonce`.
2. The nonce is passed via `opts.envs` as `CUBEPLEX_COMMAND_NONCE` along with `CUBEPLEX_BRIDGE_URL` and `CUBEPLEX_RUN_ID`.
3. The nonce record is saved in **cluster-shared Redis**, keyed by `execution_id` and `run_id` with a strict TTL matching the command timeout (default 60s, max 300s). Local process memory is never used, ensuring load-balanced multi-replica backend pods can validate calls seamlessly.
4. The moment `commands.run` finishes (exit, cancellation, or timeout), the backend immediately purges the nonce from Redis. Any lookup miss on any backend pod fails-closed with HTTP 401 Unauthorized.

### 3.3 Server-Side Default-Deny Tool Whitelist
Processes sharing the container under UID 1000 can observe `/proc/<pid>/environ`. Therefore, the true security boundary is enforced strictly on the server-side Bridge:
- **Whitelisted for Delegation (`allowed_callers: ["sandbox", "direct"]`):**
  - All workspace-configured external MCP tools (e.g. `linear`, `notion`, `github`, `jira`).
  - Read-only native utility tools: `read`, `calculator`, `datetime`, `memory_search`.
- **Forbidden from Delegation (`allowed_callers: ["direct"]`):**
  - Interactive tools: `ask_user`.
  - Process/shell execution: `execute`, `monitor`.
  - Task and workspace control: `write_todos`, `persona_update`, `scheduled_tasks`, `subagent`.
  - Any unauthorized tool call made via `cubeplex_context.tools` is immediately rejected with HTTP 403 / actionable error message.

---

## 4. The `cubeplex_context` SDK Contract

The client SDK `cubeplex_context` is pre-installed in the OpenSandbox image as a lightweight Python package (relying solely on `httpx` and `pydantic`).

### 4.1 Invoking Tools (`cubeplex_context.tools`)

Tools are accessed via dynamic attribute access or dictionary lookups:

```python
from cubeplex_context import tools

# Normalized attribute access
issues = await tools.linear__list_issues(team="CORE", limit=50)

# Exact namespaced name lookup
result = await tools["notion__query_database"](database_id="db_123")
```

#### How the Agent Discovers Available Tools
1. **System Prompt Type Stubs:** CubeLoop's middleware injects compact Python type signatures of available tools into the agent's system prompt:
   ```text
   ## Available in `cubeplex_context.tools`:
   - `tools.linear__list_issues(team: str, limit: int = 50) -> list[dict]`
   - `tools.notion__query_database(database_id: str) -> dict`
   ```
2. **Local Type Hint File (`/workspace/.codemode/tools.pyi`):**
   When a session initializes or MCP servers update, the backend writes a complete `.pyi` stub file to `/workspace/.codemode/tools.pyi`. Agents can inspect this file with `read` if detailed parameter schemas are needed.
3. **Runtime Reflection API:**
   For dynamic discovery inside scripts:
   ```python
   # List all tool names and descriptions
   all_tools = tools.list()

   # Inspect detailed schema and documentation for a tool
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

#### Sub-Model Anti-Recursion & Cost Guardrails
1. **Non-Agentic Single-Turn Only:** Sub-calls directly invoke provider completion APIs. Sub-calls **cannot use tools** and cannot spawn sub-agents. Recursion depth is strictly capped at 1.
2. **Concurrency Ceiling:** At most 4 concurrent sub-model requests are allowed per script execution. Additional calls are queued.
3. **Token Attribution:** Tokens consumed by `llm.*` sub-calls are metered by the Bridge and merged into the parent run's `usage` counters in PostgreSQL.
4. **Execution Deadline:** In-sandbox execution remains bounded by the command timeout (default 60s, max 300s).

### 4.3 Context Metadata

The SDK also exposes ambient execution metadata:

```python
import cubeplex_context as ctx

print(ctx.run_id)          # Current Run ID
print(ctx.workspace_id)    # Current Workspace ID
```

---

## 5. End-to-End Workflow Examples

### Example: Automated Report Generation via `docx` Skill
An agent tasked with generating a monthly incident report previously had to make 10 round trips to pull tickets, summarize each, and format a document. With `cubeplex_context`, the agent writes a single Python script and runs it via `execute`:

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
    
    # 3. Create formatted Word document directly in the workspace
    doc = Document()
    doc.add_heading("Monthly Incident Report", level=1)
    for issue, summary in zip(issues[:10], summaries):
        doc.add_heading(issue["title"], level=2)
        doc.add_paragraph(summary)
    
    doc.save("/workspace/projects/incident-report.docx")
    print("Report generated successfully at /workspace/projects/incident-report.docx")

asyncio.run(main())
```

After `execute` completes, the agent immediately calls `save_artifact(path="/workspace/projects/incident-report.docx")` to deliver the result.

---

## 6. Observability & User Experience (SSE & UI)

When an agent executes a script that uses `cubeplex_context`:
1. The frontend receives standard `ToolExecutionStartEvent` for `execute`.
2. As the script invokes `tools.linear__list_issues()` or `llm.generate()`, the Reverse Bridge emits nested SSE events:
   - `ToolExecutionStartEvent(tool_name="linear__list_issues", is_nested=True, parent_tool_call_id=...)`
   - `LLMExecutionStartEvent(model="claude-sonnet-4-5", is_nested=True, parent_tool_call_id=...)`
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

### Phase 1: SDK & Reverse Bridge Service
- Implement `cubeplex_context` Python client package with `tools` and `llm` interfaces.
- Build the Backend Reverse Bridge endpoint (`/api/v1/context/dispatch`), Redis-backed Ephemeral Nonce validation, and Default-Deny whitelist enforcement.
- Inject Bridge FQDN into sandbox `build_network_policy` (`force_allow_hosts`).
- Pre-install `cubeplex_context` into the OpenSandbox container image.

### Phase 2: Tool Stubs & System Prompt Integration
- Implement type stub generation in CubeLoop/CubePlex for active workspace tools.
- Auto-generate `/workspace/.codemode/tools.pyi` on session startup.
- Inject SDK guidance and Python signatures into the agent system prompt.

### Phase 3: Sub-Model Delegation & Nested UI Observability
- Connect `cubeplex_context.llm` to the workspace's configured model preset with strict concurrency limiting (max 4).
- Aggregate in-sandbox token usage into the parent run's billing records.
- Update `agents/stream.py` and Chat UI components to render nested execution trees for `execute` commands.
