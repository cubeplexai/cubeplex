# MCP Progressive Disclosure Implementation Plan

> **⚠ PENDING REVISION (2026-06-09):** The spec has been updated with three
> significant design changes from the hermes-agent research pass:
> 1. **cubepi/cubebox layering** — core mechanism (`DeferredToolGroup`, `expand_tools`,
>    middleware) moves into cubepi as a generic primitive; cubebox provides MCP → group mapping.
> 2. **Catalog includes tool names** — not just server slug + description.
> 3. **Threshold = context-window token %** (default 10%) + `min_servers` secondary guard.
>
> The tasks below still reference the old names (`expand_mcp_server`,
> `MCPDisclosureMiddleware`, `min_servers`-only threshold). A plan revision is needed
> before implementation begins. The cubepi-side work is tracked as a separate cubepi
> issue and should land first.

> For agentic workers: execute tasks top to bottom. Each task is TDD — write the
> test first, watch it fail, implement, watch it pass. Stay on branch
> `feat/mcp-progressive-disclosure`; never switch to main or merge mid-execution.
> First command in the worktree is always `cat .worktree.env` (slot 89, backend
> `:8089`, DB `cubebox_feat_mcp_progressive_disclosure`). Run the per-module tests
> shown under each task during dev; reserve the full suite for the pre-PR sweep.
> **Task 0 (the spike) gates the whole plan** — its answer selects the deferral
> mode for Task 6. Do not skip or reorder it.

Date: 2026-05-27 (spec revised 2026-06-09)
Spec: `docs/dev/specs/2026-05-27-mcp-progressive-disclosure-design.md`
Issue: #143

---

## Goal

Stop injecting every enabled MCP server's full tool schemas into the prompt on
every turn. Instead inject a **compact catalog** (server slug + one-line
description + tool count + trigger hint) as a stable system-prompt suffix, and
let the model call a new `expand_mcp_server(server)` builtin to make a server's
real, callable tools available for the rest of the conversation. Collapsed
servers contribute **zero** tool definitions to `tools=` and zero schema text —
that is the only thing that actually saves cache/attention cost. Expanded
servers' callable tools come from the **real runtime loader**
(`load_workspace_mcp_tools_for_cubepi` → `load_mcp_tools_http`) **filtered to the
expanded set**, never synthesized from `tools_cache` (cache is index/preview
only — cached JSON schemas are not executable). The prompt-cache prefix must stay
byte-stable: catalog sorted by slug, expanded-schema text appended in **expansion
order** (append-only, never re-sorted). Feature is config-gated and off below a
server-count threshold so small workspaces keep today's exact behavior.

## Architecture

Mirror the **skills** subsystem at server granularity:

| Skills (existing) | MCP progressive disclosure (this plan) |
|---|---|
| `SKILLS_PROMPT_TEMPLATE` index in system-prompt suffix, sorted by name | MCP catalog renderer, suffix, sorted by **slug** |
| `load_skill(name)` builtin returns SKILL.md JSON | `expand_mcp_server(server)` builtin returns server tool summary JSON |
| `SkillsMiddleware`: `after_tool_call` writes `extra["loaded_skills"]` (dict); `transform_system_prompt` appends bodies sorted by name | `MCPDisclosureMiddleware`: `after_tool_call` appends slug to `extra["expanded_mcp_servers"]` (**ordered list**, dedup, first-expanded-first); `transform_system_prompt` appends each expanded server's schema text in **expansion order** |

Key divergence from skills: the **enabled** skill set is fixed up front, so
skills can sort by name. MCP servers expand **incrementally mid-conversation**, so
slug-sorting the expanded blocks could insert a later expansion *before* an
already-cached block and invalidate the prefix. Therefore expanded-schema text is
ordered by **expansion order**, persisted as an **ordered list** in `extra`, and
replayed unchanged across turns.

The load-bearing unknown — can cubepi add callable tools to a *live* agent mid-run
and re-mark the cache boundary? — is resolved by **Task 0 (spike)**. The plan
branches there:

- **True deferral** (cubepi supports mid-run tool-set change): a just-expanded
  server is callable within the same turn.
- **Next-turn fallback** (cubepi cannot, current pinned rev): the expanded
  server's tools enter `tools=` on the **next** user turn; the tool set is frozen
  per run. Still saves all the cost (collapsed servers never in `tools=`); costs a
  one-turn delay before a just-expanded server is callable.

Either way **pre-register-all is rejected** — shipping every collapsed server's
schema in `tools=` while hiding it from the catalog prose saves nothing.

## Tech Stack

- Backend: FastAPI + cubepi agent runtime, Postgres (SQLModel + Alembic),
  Python 3.13, mypy strict, line length 100.
- Tests: pytest + pytest-asyncio. E2E under `tests/e2e/`, unit under
  `tests/unit/` (directory marker is auto-applied by `tests/conftest.py`).
- MCP E2E uses the existing `stub_discover_tools` fixture
  (`tests/e2e/conftest.py:696`) **only to avoid the post-grant discovery network
  probe** — it is a no-op that patches `run_post_grant_discovery`, nothing more.
  It does **not** seed `tools_cache` and does **not** simulate a runtime MCP
  server returning callable tools. The disclosure tests therefore additionally
  need: (a) installs whose `tools_cache` is seeded directly (for catalog + expand
  previews + schema text), and (b) a fake `load_mcp_tools_http` (or equivalent
  monkeypatch in `cubepi_runtime`) that returns callable tools for the expanded
  set so an expanded server's tools actually execute. Build these as part of
  Task 6d; do not assume `stub_discover_tools` provides them.
- Touch points (all confirmed by reading the code):
  - `backend/cubebox/streams/run_manager.py` — tool assembly (~1034), middleware
    stack (~1136–1353), final tool merge (~1359), `create_cubebox_agent` call
    (~1384), system-prompt suffix assembly (~1799).
  - `backend/cubebox/mcp/cubepi_runtime.py` — `load_workspace_mcp_tools_for_cubepi`.
  - `backend/cubebox/mcp/effective.py` — `list_runtime_specs`,
    `MCPRuntimeConnectorSpec` (carries `install_id`, `name`, `tools_cache`,
    `discovery_metadata`; **note: no `description` field** — see Task 2).
  - `backend/cubebox/models/mcp.py` — `MCPConnectorInstall.tools_cache`,
    `.discovery_metadata`, `.slug_name`, `.description` (via template).
  - `backend/cubebox/middleware/skills.py`,
    `backend/cubebox/tools/builtin/load_skill.py`,
    `backend/cubebox/prompts/skills.py` — the analog to copy.
  - `backend/cubebox/config.py` — `config.get("mcp.progressive_disclosure...")`.

---

## Task 0 — SPIKE: does cubepi support mid-run tool-set change? (GATES THE PLAN)

**This task produces a written decision, not shippable code.** It selects the
Task 6 branch (true deferral vs next-turn fallback).

What we already know from reading the **pinned** cubepi
(`backend/.venv/.../cubepi/agent/agent.py`, `loop.py`):

- `Agent._state.tools` has a property setter (`agent.state.tools = [...]`).
- BUT `Agent.prompt()` calls `_create_context_snapshot()` **once** and the loop
  runs against that single `current_context`; `run_agent_loop` reads
  `context.tools` to build `tools_defs` (`loop.py` ~381) and never re-reads
  `agent.state.tools` between iterations. So mutating `agent.state.tools` from a
  middleware hook during a run does **not** change the tools the model sees this
  run.

Steps:

1. Write a throwaway probe `backend/scripts/dev/spike_mcp_live_tools.py` that
   builds a minimal cubepi `Agent` with a tool whose `after_tool_call` (or a
   middleware) appends a second `AgentTool` to `agent.state.tools`, then checks
   whether the model is offered the new tool in the **same** `prompt()` call.
   Drive it with a stub provider that records the `tools_defs` passed on each
   model call.
2. Inspect cubepi for any hook to mutate `current_context.tools` mid-loop (e.g. a
   `transform_context` that returns tools, or a documented "register tool"
   surface). Grep `~/cubepi` source, not just the installed wheel — but remember
   runtime uses the **pinned** wheel (`reference_cubepi_pinned_dep`), so any new
   capability must be released + the pin bumped before it reaches runtime.

Run:
```bash
cd backend && uv run python scripts/dev/spike_mcp_live_tools.py
```
Expected output (one of):
```
SPIKE RESULT: MID_RUN_SUPPORTED   # new tool appears in same-run tools_defs
SPIKE RESULT: MID_RUN_UNSUPPORTED # snapshot frozen; next-turn fallback required
```

Record the result and the chosen branch in
`docs/dev/notes/2026-05-27-mcp-disclosure-cubepi-spike.md` (one new note file is
allowed — it is the decision record this plan depends on). Delete the probe
script after.

**Decision rule:**
- `MID_RUN_UNSUPPORTED` → build **next-turn fallback** in v1 (expected, given the
  snapshot finding). File a cubepi upstream follow-up (`~/cubepi`, upstream-first)
  to add mid-run tool-set change + cache re-establishment; do **not** hand-edit
  cubepi vendor behavior in cubebox.
- `MID_RUN_SUPPORTED` → build **true deferral** (Task 6 variant B).

Verify: the note exists and states the branch explicitly.

---

## Task 1 — Config flags + threshold gate (unit)

Add the config surface and a pure helper deciding when disclosure is active.

Files:
- `backend/cubebox/mcp/disclosure.py` (new) — pure functions, no I/O.
- `backend/config.development.yaml` and `backend/config.yaml` (whichever holds the
  `mcp` block; add the `progressive_disclosure` subkey).
- `backend/tests/unit/test_mcp_disclosure_gate.py` (new).

`disclosure.py`:
```python
"""Progressive-disclosure gating + catalog/schema rendering (pure, no I/O)."""

from __future__ import annotations

from dataclasses import dataclass

from cubebox.config import config


@dataclass(frozen=True)
class DisclosureSettings:
    enabled: bool
    min_servers: int


def load_disclosure_settings() -> DisclosureSettings:
    return DisclosureSettings(
        enabled=bool(config.get("mcp.progressive_disclosure.enabled", False)),
        min_servers=int(config.get("mcp.progressive_disclosure.min_servers", 3)),
    )


def disclosure_active(settings: DisclosureSettings, usable_server_count: int) -> bool:
    """True when the catalog/expand machinery replaces eager tool loading."""
    return settings.enabled and usable_server_count >= settings.min_servers
```

Test (write first, must fail before `disclosure.py` exists):
```python
from cubebox.mcp.disclosure import DisclosureSettings, disclosure_active


def test_disabled_never_active() -> None:
    s = DisclosureSettings(enabled=False, min_servers=2)
    assert disclosure_active(s, 10) is False


def test_below_threshold_inactive() -> None:
    s = DisclosureSettings(enabled=True, min_servers=3)
    assert disclosure_active(s, 2) is False


def test_at_threshold_active() -> None:
    s = DisclosureSettings(enabled=True, min_servers=3)
    assert disclosure_active(s, 3) is True
```

Config block (add under existing `mcp:`):
```yaml
mcp:
  progressive_disclosure:
    enabled: false      # off by default — small workspaces keep today's behavior
    min_servers: 3      # only collapse when this many usable servers connected
```

Run:
```bash
cd backend && uv run pytest tests/unit/test_mcp_disclosure_gate.py -q
```
Expected: `3 passed`.

---

## Task 2 — Catalog renderer (unit, determinism + cache-stability)

Render the compact catalog from data already in Postgres
(`MCPRuntimeConnectorSpec.tools_cache` + spec `name`/`install_id`) — **no live
discovery**. Sorted by slug → byte-identical every turn.

**Description source (important):** `MCPRuntimeConnectorSpec` has **no
`description` field** (confirmed at `effective.py:206`). Do not call
`spec.description`. Derive the one-line description from data the spec already
carries: prefer a value pulled from `spec.discovery_metadata` (server
description/summary captured at discovery), else fall back to the slug itself.
If a richer source is wanted, widen `MCPRuntimeConnectorSpec` /
`list_runtime_specs` to carry a `description` from the template/discovery row —
but that is an explicit extra step, not assumed by the renderer. Pick one and
make `render_catalog` take the resolved description string (or `None`).

Files:
- `backend/cubebox/mcp/disclosure.py` (extend).
- `backend/cubebox/prompts/mcp_catalog.py` (new) — the template, mirroring
  `prompts/skills.py`.
- `backend/tests/unit/test_mcp_catalog_render.py` (new).

`prompts/mcp_catalog.py`:
```python
"""MCP catalog template — injected by run_manager when disclosure is active."""

MCP_CATALOG_HEADER = """\

# Connected tool servers (collapsed)

These servers are connected but their tools are not loaded yet. Call
`expand_mcp_server(server)` with a name below to load that server's tools for the
rest of this conversation.
"""
```

`disclosure.py` (add):
```python
from cubebox.mcp.effective import MCPRuntimeConnectorSpec
from cubebox.mcp._constants import slugify_for_namespace
from cubebox.prompts.mcp_catalog import MCP_CATALOG_HEADER


def _one_line(text: str | None, limit: int = 140) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _spec_description(spec: MCPRuntimeConnectorSpec) -> str | None:
    """No `description` field on the spec — derive from discovery_metadata."""
    meta = spec.discovery_metadata or {}
    return meta.get("description") or meta.get("summary")


def render_catalog(specs: list[MCPRuntimeConnectorSpec]) -> str:
    """Compact, slug-sorted catalog. Never contains per-tool JSON schemas."""
    lines: list[str] = []
    for spec in sorted(specs, key=lambda s: slugify_for_namespace(s.name)):
        slug = slugify_for_namespace(spec.name)
        count = len(spec.tools_cache)
        desc = _one_line(_spec_description(spec) or slug)
        lines.append(f"- `{slug}` — {desc} ({count} tools)")
    return MCP_CATALOG_HEADER + "\n" + "\n".join(lines) + "\n"
```
(Confirm the actual `discovery_metadata` key by reading a real install row; the
keys above are a guess. Trigger-hint line is derived from description for v1 —
the spec leaves an authored `trigger_hints` field as a Later item; do **not**
add a migration now.)

Test (write first):
```python
from cubebox.mcp.disclosure import render_catalog
from cubebox.mcp.effective import MCPRuntimeConnectorSpec


def _spec(name: str, desc: str, n_tools: int) -> MCPRuntimeConnectorSpec:
    # No `description` field — push the one-liner through discovery_metadata.
    return MCPRuntimeConnectorSpec(
        install_id=f"inst-{name}", name=name,
        discovery_metadata={"description": desc},
        tools_cache=[{"name": f"t{i}"} for i in range(n_tools)],
        # ...remaining required fields filled with neutral defaults...
    )


def test_catalog_sorted_by_slug_and_byte_stable() -> None:
    # slugify_for_namespace PRESERVES case (`Alpha` → `Alpha`, not `alpha`):
    # use lowercase names so expected slugs match, or assert on the actual
    # slug. Sorting is by the slug as produced — confirm by reading
    # _constants.py:38, do not assume lowercasing.
    specs = [_spec("zeta", "z server", 2), _spec("alpha", "a server", 5)]
    out1 = render_catalog(specs)
    out2 = render_catalog(list(reversed(specs)))  # input order must not matter
    assert out1 == out2
    assert out1.index("`alpha`") < out1.index("`zeta`")
    assert "(5 tools)" in out1 and "(2 tools)" in out1


def test_catalog_has_no_input_schemas() -> None:
    specs = [_spec("Alpha", "a", 1)]
    assert "input_schema" not in render_catalog(specs)
```
(Inspect `MCPRuntimeConnectorSpec` field list at `effective.py:207` to fill the
constructor; do not guess.)

Run:
```bash
cd backend && uv run pytest tests/unit/test_mcp_catalog_render.py -q
```
Expected: `2 passed`.

---

## Task 3 — Expanded-schema renderer (unit, expansion-order stability)

Render the full tool definitions of expanded servers, from `tools_cache`, in
**expansion order** (never sorted). Adding one expansion must only **append**.

**Names must match the real callable tools.** The runtime loader namespaces and
may suffix/truncate each tool name via `_build_namespaced_name_with_prefix` +
the collision/truncation logic in `cubepi_runtime.py` (~113–199): explicit slug
collisions and risky truncations get a `_{last4}` suffix. The schema text the
model reads here, the `tool_names` from Task 4, and the catalog must all use the
**same** namespaced names the model will actually call — otherwise the model
sees one name in the schema and a different name in `tools=`. Compute namespaced
names by reusing that loader logic (extract a pure helper, or call the same
namespacing pass over the spec's `tools_cache`), not by emitting raw
`tools_cache["name"]` values.

Files:
- `backend/cubebox/mcp/disclosure.py` (extend).
- `backend/tests/unit/test_mcp_expanded_render.py` (new).

`disclosure.py` (add):
```python
import json

EXPANDED_SECTION_HEADER = "[Expanded MCP servers]"


def render_expanded_schemas(
    expanded_slugs: list[str],
    specs_by_slug: dict[str, MCPRuntimeConnectorSpec],
) -> str:
    """Append-only schema text, ordered by EXPANSION ORDER (not slug).

    A newly expanded server's block always lands after every already-rendered
    block → earlier cache segments stay byte-identical.
    """
    blocks: list[str] = []
    for slug in expanded_slugs:  # expansion order, as-stored
        spec = specs_by_slug.get(slug)
        if spec is None:
            continue
        tools_json = json.dumps(spec.tools_cache, sort_keys=True, ensure_ascii=False)
        blocks.append(f"## Server: {slug}\n\n{tools_json}")
    if not blocks:
        return ""
    return f"\n\n{EXPANDED_SECTION_HEADER}\n\n" + "\n\n".join(blocks)
```

Test (write first):
```python
def test_expansion_order_preserved_not_sorted() -> None:
    specs = {"zeta": _spec("zeta",...), "alpha": _spec("alpha",...)}
    out = render_expanded_schemas(["zeta", "alpha"], specs)
    assert out.index("## Server: zeta") < out.index("## Server: alpha")


def test_adding_expansion_only_appends() -> None:
    specs = {"a": _spec("a",...), "b": _spec("b",...)}
    first = render_expanded_schemas(["a"], specs)
    second = render_expanded_schemas(["a", "b"], specs)
    assert second.startswith(first)  # prefix preserved → cache-safe


def test_json_keys_sorted_for_byte_stability() -> None:
    out = render_expanded_schemas(["a"], {"a": _spec("a", tools=[{"b":1,"a":2}])})
    assert out == render_expanded_schemas(["a"], {"a": _spec("a", tools=[{"a":2,"b":1}])})
```

Run:
```bash
cd backend && uv run pytest tests/unit/test_mcp_expanded_render.py -q
```
Expected: `3 passed`.

---

## Task 4 — `expand_mcp_server` builtin (unit)

A sibling of `load_skill`. Validates the slug against the workspace's usable
installs, returns a JSON summary (namespaced tool names + descriptions, **no
schemas in the result** — middleware injects schema text). Placed in the fixed
tool order **where MCP tools used to go** — as the last builtin, immediately
before the MCP tools (after `generate_image`); see Task 6b for exact placement.

**`tool_names` must be the namespaced, collision-resolved names** the model will
actually call (see Task 3) — not bare `tools_cache["name"]`. `list_usable_slugs`
therefore returns the post-namespacing names per slug, computed with the same
loader logic.

Files:
- `backend/cubebox/tools/builtin/expand_mcp_server.py` (new).
- `backend/tests/unit/test_expand_mcp_server_tool.py` (new).

```python
"""expand_mcp_server builtin — sibling of load_skill.

Returns a summary of a connected MCP server's tools so the model knows what it
just unlocked. Schema text is injected into the system prompt by
MCPDisclosureMiddleware (mirrors how SkillsMiddleware injects skill bodies),
NOT re-emitted in this tool result.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field


class ExpandMCPServerInput(BaseModel):
    server: str = Field(description="The server slug from your 'Connected tool servers' list.")


class ExpandMCPServerOutput(BaseModel):
    server: str
    expanded: bool
    tool_names: list[str]
    error: str | None = None

    def __str__(self) -> str:
        return self.model_dump_json()


def create_expand_mcp_server_tool(
    *,
    list_usable_slugs: Callable[[], Awaitable[dict[str, list[str]]]],
) -> AgentTool[ExpandMCPServerInput]:
    """list_usable_slugs() → {slug: [bare_tool_name, ...]} for usable installs."""

    async def _execute(tool_call_id, args, *, signal=None, on_update=None):
        del tool_call_id, signal, on_update
        usable = await list_usable_slugs()
        if args.server not in usable:
            out = ExpandMCPServerOutput(
                server=args.server, expanded=False, tool_names=[],
                error=f"Server '{args.server}' is not connected to this workspace",
            )
            return AgentToolResult(content=[TextContent(text=out.model_dump_json())], is_error=True)
        out = ExpandMCPServerOutput(
            server=args.server, expanded=True, tool_names=usable[args.server],
        )
        return AgentToolResult(content=[TextContent(text=out.model_dump_json())])

    return AgentTool(
        name="expand_mcp_server",
        description=(
            "Load a connected MCP server's tools for the rest of this conversation. "
            "Pass the exact server slug from your 'Connected tool servers' list."
        ),
        parameters=ExpandMCPServerInput,
        execute=_execute,
    )
```

Test (write first): valid slug → `expanded=True` with tool names; unknown slug →
`is_error=True`, `expanded=False`.

Run:
```bash
cd backend && uv run pytest tests/unit/test_expand_mcp_server_tool.py -q
```
Expected: `2 passed`.

---

## Task 5 — `MCPDisclosureMiddleware` (unit)

Port of `SkillsMiddleware`. `after_tool_call` for a successful `expand_mcp_server`
appends the slug to `extra["expanded_mcp_servers"]` (ordered list, dedup,
first-expanded-first; **never sort**). `transform_system_prompt` appends the
expanded-schema text via `render_expanded_schemas` in stored order.

Files:
- `backend/cubebox/middleware/mcp_disclosure.py` (new).
- `backend/tests/unit/test_mcp_disclosure_middleware.py` (new).

```python
"""MCPDisclosureMiddleware — server-granularity port of SkillsMiddleware."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from cubepi.agent.types import AfterToolCallContext
from cubepi.middleware.base import Middleware

from cubebox.mcp.disclosure import render_expanded_schemas
from cubebox.mcp.effective import MCPRuntimeConnectorSpec
from cubebox.tools.builtin.expand_mcp_server import ExpandMCPServerOutput

EXPANDED_KEY = "expanded_mcp_servers"


class MCPDisclosureMiddleware(Middleware):
    def __init__(
        self,
        *,
        extra_ref: Callable[[], dict[str, Any]],
        specs_by_slug: dict[str, MCPRuntimeConnectorSpec],
    ) -> None:
        self._extra_ref = extra_ref
        self._specs_by_slug = specs_by_slug

    async def after_tool_call(self, ctx: AfterToolCallContext, *, signal: Any = None) -> None:
        del signal
        if ctx.tool_call.name != "expand_mcp_server" or ctx.is_error or not ctx.result.content:
            return None
        raw = next((b.text for b in ctx.result.content if hasattr(b, "text")), "")
        if not raw:
            return None
        try:
            out = ExpandMCPServerOutput.model_validate_json(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        if not out.expanded:
            return None
        extra = self._extra_ref()
        ordered: list[str] = extra.setdefault(EXPANDED_KEY, [])
        if out.server not in ordered:           # dedup, preserve first-expanded order
            ordered.append(out.server)
        return None

    async def transform_system_prompt(self, system_prompt: str, *, signal: Any = None) -> str:
        del signal
        ordered: list[str] = self._extra_ref().get(EXPANDED_KEY, [])
        if not ordered:
            return system_prompt
        return system_prompt + render_expanded_schemas(ordered, self._specs_by_slug)
```

Test (write first):
- Calling `after_tool_call` twice for the same slug stores it once.
- Two distinct slugs are stored in call order; `transform_system_prompt` output
  reflects that order and is append-only (the Task 3 invariant, exercised through
  the middleware).
- A non-`expand_mcp_server` tool call is a no-op.

Run:
```bash
cd backend && uv run pytest tests/unit/test_mcp_disclosure_middleware.py -q
```
Expected: `3 passed`.

---

## Task 6 — Wire into run_manager: filtered loader + catalog suffix + middleware (integration)

This is where the spec's true-deferral commitment lands. **Branch on Task 0.**

Files:
- `backend/cubebox/mcp/cubepi_runtime.py` — add `only_install_ids` filter.
- `backend/cubebox/streams/run_manager.py` — tool load (~1034), middleware
  append (after SkillsMiddleware ~1267), system-prompt suffix (~1818).
- `backend/tests/e2e/test_mcp_disclosure_runtime.py` (new, uses
  `stub_discover_tools`).

### 6a. Filter the live loader (not a tools_cache synthesizer)

In `load_workspace_mcp_tools_for_cubepi`, add a keyword-only
`only_install_ids: set[str] | None = None`. When provided, after
`specs = await effective_service.list_runtime_specs(...)`, filter:
```python
specs = await effective_service.list_runtime_specs(workspace_id, user_id)
if only_install_ids is not None:
    specs = [s for s in specs if s.install_id in only_install_ids]
```
Everything downstream (auth resolve, `load_mcp_tools_http`, namespacing,
citations) is unchanged — these are the **real callable** tools, filtered to the
expanded set. `tools_cache` is never a tool source.

Also export a cheap helper used by Task 4's `list_usable_slugs` and the catalog:
return `list_runtime_specs(...)` already gives `install_id`, `name`, `tools_cache`
— map slug→bare tool names from `tools_cache` for the summary (preview only),
slug→install_id for the filter, slug→spec for the renderers.

### 6b. run_manager assembly

At the MCP block (~1034), compute `specs = list_runtime_specs(...)` once, build
`specs_by_slug` and `usable_count`. Then:

```python
from cubebox.mcp.disclosure import load_disclosure_settings, disclosure_active

settings = load_disclosure_settings()
active = disclosure_active(settings, usable_count)

if active:
    expanded: list[str] = []  # populated from agent._extra on replay (see 6c)
    only_ids = {specs_by_slug[s].install_id for s in expanded if s in specs_by_slug}
    _new_tools, _new_citations = await load_workspace_mcp_tools_for_cubepi(
        ..., only_install_ids=only_ids,
    )
    _builtin_tools.append(create_expand_mcp_server_tool(list_usable_slugs=...))
    # catalog suffix appended to effective_system_prompt below
else:
    # today's path: load ALL servers, no catalog, no expand tool
    _new_tools, _new_citations = await load_workspace_mcp_tools_for_cubepi(...)
```

`expand_mcp_server` is appended to `_builtin_tools` **immediately before the MCP
tools and after every existing builtin** — do not insert it right after
`load_skill`, which would shift `view_images`/`generate_image`. The actual
current order (confirmed in `run_manager.py` ~927–1032) is: memory → load_skill →
view_images → generate_image → mcp_tools. Insert `expand_mcp_server` as the last
builtin so the prefix becomes: … → memory → load_skill → view_images →
generate_image → **expand_mcp_server** → mcp_tools. Everything before it stays
byte-identical.

Append `MCPDisclosureMiddleware(extra_ref=_extra_ref, specs_by_slug=specs_by_slug)`
to `cubepi_middleware` immediately after `SkillsMiddleware` (~1267). The
`extra_ref` closure resolves to `agent._extra` once it's late-bound at ~1403,
exactly like skills/compaction/todo.

When `active`, append the catalog suffix to `effective_system_prompt`. The suffix
is built in `_run_cubepi_path` (where `specs` are loaded), not at ~1799 (which
runs before the run path and has no specs). Move/duplicate the catalog injection
into the run path right after the MCP load, mirroring how skills append to the
prompt — append `render_catalog(specs)` to `effective_system_prompt` before
`create_cubebox_agent`.

### 6c. The deferral branch (from Task 0)

- **Next-turn fallback (`MID_RUN_UNSUPPORTED`, expected):** on each run, build
  `only_install_ids` from the **persisted prior-run** expanded set
  (`expanded_mcp_servers` ordered list) before the MCP tools are loaded.
  **Ordering problem to fix:** the MCP tool load is at ~1098 but the existing
  `init_checkpointer()`/`cp.load(conversation_id)` (citation seeding) is at ~1375
  — *after* the tool load. The live `agent._extra` is also only available after
  `create_cubebox_agent` (~1384). So the fallback needs an **earlier** load of the
  persisted `_extra` (an `init_checkpointer()`/`cp.load` call hoisted above the MCP
  block at ~1041, or a small helper that reads just the persisted
  `expanded_mcp_servers` for the conversation) so `only_install_ids` is known when
  `load_workspace_mcp_tools_for_cubepi(..., only_install_ids=...)` runs. Do not
  read the live `_extra` for this — it is empty until the agent is built and only
  reflects this run. A server expanded on turn N becomes callable on turn N+1: the
  `expand_mcp_server` call on turn N records the slug into the live `_extra`, which
  is persisted (`save_extra` at `agent_end`, same as `loaded_skills`) and read back
  on turn N+1's early load. **Document this one-turn delay in the
  `expand_mcp_server` tool description** so the model expects it ("the server's
  tools become available on your next turn"). Confirm `save_extra` actually
  persists arbitrary `_extra` keys (verify `loaded_skills` round-trips today)
  before relying on it for `expanded_mcp_servers`.
- **True deferral (`MID_RUN_SUPPORTED`):** after `after_tool_call` records the
  slug, also load that server's callable tools via the filtered loader and add
  them to the live agent through cubepi's mid-run mechanism (whatever Task 0
  found), and re-mark the cache boundary. Tool description drops the "next turn"
  caveat. This depends on the released+pinned cubepi capability.

> Confirm which branch you are on by re-reading the Task 0 note. Do not build both.

### 6d. E2E test (write first)

`tests/e2e/test_mcp_disclosure_runtime.py`, using `stub_discover_tools` (to skip
the discovery probe) plus the install-creation pattern from
`tests/e2e/test_mcp_four_layer_runtime.py`. Additionally seed each install's
`tools_cache` directly and monkeypatch the runtime tool loader
(`load_mcp_tools_http` / `cubepi_runtime`) to return callable tools for the
expanded set — `stub_discover_tools` alone supplies neither (see Tech Stack):

1. With disclosure enabled and ≥ `min_servers` usable servers installed: assert
   the assembled `tools=` contains `expand_mcp_server` and **no** namespaced MCP
   tools from collapsed servers; assert the system prompt contains the catalog
   (server slugs, tool counts) and **no** `input_schema`.
2. Drive a run that calls `expand_mcp_server("<slug>")`; assert the slug is in
   `agent._extra["expanded_mcp_servers"]` and (per branch) the expanded server's
   namespaced tools are callable (same run for true deferral / next run for
   fallback) and produce a result.
3. Assert citations still attach for the expanded server (`mcp_citation_configs`
   populated for it; `CitationMiddleware` unchanged).

Run:
```bash
cd backend && uv run pytest tests/e2e/test_mcp_disclosure_runtime.py -q
```
Expected: all pass (real simulated MCP discovery via `stub_discover_tools`).

---

## Task 7 — Threshold below-min byte-identical guard (E2E)

Prove the small-workspace path is unchanged: below `min_servers`, no catalog, no
`expand_mcp_server`, all servers' tools eagerly loaded exactly as today.

Files:
- `backend/tests/e2e/test_mcp_disclosure_runtime.py` (add a case).

Assert: with `min_servers=3` and 2 usable servers, the assembled prompt has no
catalog header and `tools=` contains every server's namespaced tools (status
quo), and `expand_mcp_server` is **absent**.

Run:
```bash
cd backend && uv run pytest tests/e2e/test_mcp_disclosure_runtime.py -q -k threshold
```
Expected: pass.

---

## Task 8 — Cache-prefix stability E2E (THE GATE)

Extend the existing real-LLM cache regression. This is the single most important
test per the spec.

Files:
- `backend/tests/e2e/memory/test_prompt_cache.py` (extend, mirroring
  `test_cache_hit_rate_meets_bar`).

Assert, with disclosure active and ≥ `min_servers` servers:
1. **Catalog stability:** the cache-eligible prefix is byte-stable across turns
   with the catalog present and nothing expanded (catalog derived purely from DB,
   slug-sorted → identical every turn).
2. **Append-only after expansion:** after the model expands one server, the next
   turn's prefix **starts with** the previous turn's expanded-schema text and only
   appends the new block — no mid-prefix mutation, no reorder. Expanding a second
   server appends after the first (expansion order, not slug order).
3. Reuse the existing `CUBEBOX_E2E_LLM_CACHE_CAPABLE` skip semantics so the test
   discriminates "endpoint doesn't honor cache" from "we broke the prefix".

Run:
```bash
cd backend && CUBEBOX_E2E_LLM_CACHE_CAPABLE=true uv run pytest \
  tests/e2e/memory/test_prompt_cache.py -q
```
Expected: pass (or principled SKIP if the endpoint can't cache — never weaken the
bar to make it pass; per `prompt-cache-discipline.md` find the dynamic content
instead).

---

## Task 9 — Pre-PR sweep + self-review

Files: none (verification only).

1. Full backend suite on the worktree slot DB:
   ```bash
   cd backend && uv run pytest -q
   ```
   Expected: green (the worktree conftest auto-routes to
   `cubebox_test_feat_mcp_progressive_disclosure`).
2. Type + lint:
   ```bash
   cd backend && uv run mypy cubebox && uv run ruff check cubebox tests
   ```
   Expected: no errors; lines ≤ 100.
3. Confirm: collapsed servers contribute zero entries to `tools=` (grep the E2E
   assertions); `tools_cache` is used only by the catalog/expand-preview/schema
   renderers, never as an `AgentTool` source; expanded state is an **ordered
   list** end to end (model → tool → middleware `after_tool_call` → `_extra` →
   checkpointer persist → next-run replay) and never re-sorted.
4. Confirm the Task 0 note records the chosen branch and that only that branch was
   built.

---

## Open items carried from the spec (not blocking v1)

- Authored `trigger_hints` field + editing UI — Later (no migration in v1; v1
  derives the hint from description).
- Per-tool (sub-server) disclosure, semantic retrieval, code-mode,
  provider-native deferred tools — Later.
- Subagent expanded-set inheritance — v1: subagents start collapsed (their own
  assembly); revisit if needed.
- Re-collapse mid-conversation — explicitly **not** in v1 (monotonic growth is
  what keeps the cache safe).
- Stale `tools_cache`: callable tools always come from live discovery on expand,
  so a stale cache cannot make the model call a non-existent tool; only the
  catalog/preview text can drift — accepted for v1, refresh `tools_cache` out of
  band.
