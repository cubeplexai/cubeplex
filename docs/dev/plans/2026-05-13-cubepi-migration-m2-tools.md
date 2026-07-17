# cubepi Migration M2 — Tools Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port cubeplex's 5 builtin tools + MCP runtime to cubepi.AgentTool, expose them via a `registry_pi`, and wire them into the cubepi runtime path so a cubepi-backed agent can actually invoke tools. After M2, the cubepi path is callable for tool-using conversations (calculator, datetime, view_images, memory, load_skill, plus MCP tools).

**Architecture:** All new code in `*_pi.py` files alongside existing LangChain `*.py` originals. Tool implementations are mostly mechanical conversion: `StructuredTool(func=, args_schema=)` → `cubepi.AgentTool(execute=, parameters=)`. Trickier bits: `memory_pi`/`load_skill_pi` carry DB-dependency injection; MCP loading switches from `langchain-mcp-adapters` to `cubepi.mcp.load_mcp_tools_http`.

**Tech Stack:** cubepi 0.3+, pydantic 2, existing cubeplex sandbox/skill catalog/MemoryRepository abstractions.

**Spec:** `docs/superpowers/specs/2026-05-13-cubepi-main-agent-migration-design.md` § M2.

**Baseline:** M1 landed; 479 unit tests pass; cubepi runtime smoke E2E green.

**Out of scope (M3 backfill):**
- `subagent` tool (it's a middleware concept, not a builtin tool)
- The agent loop side of tool dispatch (`load_skill` injection into system prompt etc.) — M2 ports the tool body; M3 wires the middleware that uses it

---

## File Map

### Files to create

| File | Purpose |
|---|---|
| `backend/cubeplex/tools/registry_pi.py` | Registry exposing builtin + MCP tools as `cubepi.AgentTool` list |
| `backend/cubeplex/tools/builtin/calculator_pi.py` | Calculator tool ported |
| `backend/cubeplex/tools/builtin/datetime_tool_pi.py` | datetime tool ported |
| `backend/cubeplex/tools/builtin/view_images_pi.py` | view_images tool ported |
| `backend/cubeplex/tools/builtin/memory_pi.py` | memory CRUD tools ported (read/write MemoryItem table) |
| `backend/cubeplex/tools/builtin/load_skill_pi.py` | load_skill tool ported (skeleton; full integration in M3) |
| `backend/cubeplex/mcp/runtime_pi.py` | MCP servers loaded via cubepi.mcp |
| `backend/cubeplex/mcp/discovery_pi.py` | MCP discovery shim |
| Tests for each new module | Mostly unit |

### Files to modify

| File | What changes |
|---|---|
| `backend/cubeplex/agents/graph_pi.py` | Accept `tools: list[AgentTool]` and pass through to cubepi.Agent (already does — M1.4) |
| `backend/cubeplex/streams/run_manager.py` | `_run_cubepi_path` builds tool list via `registry_pi.list_tools_for_cubepi(...)` |

### Test plan

- Unit: each tool's `execute()` produces expected output
- Unit: registry_pi.list_tools_for_cubepi returns the configured set
- E2E (real_llm): cubepi runtime calls calculator tool and round-trips a tool_result through SSE

---

## Tasks

### M2.0: Pre-flight

- [ ] Confirm M1 baseline: `cd backend && uv run pytest tests/unit -q --tb=no` → 479 pass
- [ ] Confirm M1 E2E: `uv run pytest tests/e2e/test_cubepi_path_conversation.py -v -m real_llm` → 1 pass

### M2.1: Simple builtin tools (calculator + datetime + view_images) + registry_pi

Port the three simple, pure-function tools and stand up `registry_pi`.

**Files:**
- Create: `tools/registry_pi.py`, `tools/builtin/{calculator,datetime_tool,view_images}_pi.py`
- Create tests for each

**Each tool follows the same shape:**

```python
# tools/builtin/calculator_pi.py
from __future__ import annotations
from pydantic import BaseModel, Field
from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from cubeplex.tools.builtin.calculator import calculator as _calculator_impl  # reuse pure logic


class CalculatorInput(BaseModel):
    expression: str = Field(description="...")


async def _execute(tool_call_id: str, args: CalculatorInput, *, signal=None, on_update=None) -> AgentToolResult:
    del tool_call_id, signal, on_update
    result = _calculator_impl(args.expression)
    return AgentToolResult(content=[TextContent(text=result)])


calculator_tool = AgentTool(
    name="calculator",
    description="Execute mathematical calculations safely.",
    parameters=CalculatorInput,
    execute=_execute,
)
```

Pattern: reuse the existing pure Python function from the langchain version (`from cubeplex.tools.builtin.calculator import calculator as _impl`), wrap with the cubepi.AgentTool signature shim (Fix-2 signature).

**Registry stub** (`tools/registry_pi.py`):

```python
"""cubepi-side tool registry (M2).

Exposes builtin tools as cubepi.AgentTool. M3 will extend with MCP
tools and dynamic per-conversation tool sets.
"""
from __future__ import annotations
from cubepi.agent.types import AgentTool

from cubeplex.tools.builtin.calculator_pi import calculator_tool
from cubeplex.tools.builtin.datetime_tool_pi import datetime_tool
from cubeplex.tools.builtin.view_images_pi import view_images_tool


def list_builtin_tools_for_cubepi() -> list[AgentTool]:
    """Return the cubepi-tool list of cubeplex builtin tools (M2 subset).

    M3 will extend to include memory + load_skill + subagent (middleware-driven).
    """
    return [calculator_tool, datetime_tool, view_images_tool]
```

### M2.2: memory tools (memory_pi.py)

Port memory CRUD tools. They need a `MemoryRepository` factory (request-scoped). For M2 the integration is bare — the tools just write/read MemoryItem rows via the factory. M3's MemoryMiddleware will wire the read path (injecting relevance memory into prompts).

**Files:**
- Create: `tools/builtin/memory_pi.py`
- Tests using a fake repo

Function signature: `create_memory_tools_pi(repo_factory) -> list[AgentTool]`. Mirrors existing `create_memory_tools(repo_factory)` returning langchain tools — replace with cubepi.AgentTool.

### M2.3: load_skill_pi.py (skeleton)

Port `load_skill` tool. M2 lands the tool body (looks up a skill by name from `SkillCatalogService`, returns content). M3's SkillsMiddleware will then take that content and inject into the system prompt for subsequent turns. For M2 the tool can return content but the system prompt isn't yet mutated — that's M3's job.

Cubepi mechanism: M3 will use `transform_system_prompt` hook reading from `ctx.extra["loaded_skills"]`. M2's load_skill_pi just records the skill into `ctx.extra` via the `on_update` hook — wait, that's not quite right. M2 just returns the content; storage in ctx.extra is M3's coordination.

For M2 the tool returns skill content as the tool result. The cubepi.Agent's loop appends this as a ToolResultMessage. M3 will add middleware that watches for this ToolResultMessage and injects the skill content into subsequent system prompts.

**Simpler M2 scope**: just port the tool body so it returns skill content; defer ctx coordination to M3.

### M2.4: MCP runtime_pi + discovery_pi

Replace `langchain-mcp-adapters` usage with `cubepi.mcp.load_mcp_tools_http`.

**Files:**
- Create: `mcp/runtime_pi.py`, `mcp/discovery_pi.py`
- Tests against existing MCP test infrastructure if any (or skip integration test if needs live MCP server)

Likely shape:

```python
# mcp/runtime_pi.py
from cubepi.mcp import load_mcp_tools_http
from cubepi.agent.types import AgentTool

async def load_mcp_tools_for_workspace(workspace_id: str, ...) -> list[AgentTool]:
    """Resolve enabled MCP servers for the workspace; load all tools."""
    # iterate enabled server configs from DB → call load_mcp_tools_http per
    # → concatenate results
```

Existing `cubeplex.mcp.runtime` is 172 lines; mirror its workspace-resolution logic, swap the `langchain-mcp-adapters` invocation for `cubepi.mcp.load_mcp_tools_http`.

### M2.5: Wire tools into run_manager._run_cubepi_path

`run_manager._run_cubepi_path` currently constructs the agent with no tools (`tools=[]`). Update to:

```python
from cubeplex.tools.registry_pi import list_builtin_tools_for_cubepi
from cubeplex.mcp.runtime_pi import load_mcp_tools_for_workspace

builtin = list_builtin_tools_for_cubepi()
mcp_tools = await load_mcp_tools_for_workspace(ctx.workspace_id, ...)
all_tools = builtin + mcp_tools

agent = create_cubeplex_cubepi_agent(..., tools=all_tools, ...)
```

For DB-dependent tools (memory_pi) — wire their repo_factory at construction.

### M2.6: E2E — cubepi path calls a builtin tool

Add an E2E test that issues a prompt requesting calculation; verify:
- A `tool_call` SSE event for calculator is emitted
- A `tool_result` SSE event with the calculator's output follows
- The model produces a final `text_delta` integrating the result
- `done` terminates the stream

### M2.7: Final verify + push

Full check + push.

---

## Self-review checklist

- [ ] 5 builtin tools each accept the production tool.execute signature
- [ ] registry_pi exposes them as a list of AgentTool
- [ ] MCP runtime_pi loads tools via cubepi.mcp; works against the same MCP server config the langgraph runtime uses
- [ ] run_manager._run_cubepi_path wires tools into the agent
- [ ] real-LLM E2E confirms a tool call → tool result → final text round-trips
- [ ] No regressions in existing langgraph runtime tests
