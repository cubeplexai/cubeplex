# MCP Progressive Disclosure Implementation Plan

> For agentic workers: use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans. Tasks use checkbox syntax for progress tracking.

**Goal:** When a workspace has many MCP servers, hide their full tool schemas
behind a compact catalog and let the model expand groups on demand — saving
cache/context cost on every turn where most servers are unused.

**Architecture:** cubepi already provides the generic `DeferredToolGroup` /
`DeferredToolsMiddleware` / `load_tools` primitive (PR #168, pinned at
`5a85696`). cubebox maps MCP servers → deferred groups and wires them into
`run_manager._run_cubepi_path`. No custom middleware, catalog renderer, or
expand-tool in cubebox — cubepi handles all of that.

**Tech Stack:** FastAPI + cubepi 0.9.0, Postgres/SQLModel, Python 3.13,
mypy strict, ruff, line length 100.

Date: 2026-05-27 (spec revised 2026-06-09, plan revised 2026-06-10)
Spec: `docs/dev/specs/2026-05-27-mcp-progressive-disclosure-design.md`
Issue: #143

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `cubebox/mcp/disclosure.py` | Create | Config settings, threshold gate, spec→group mapping |
| `cubebox/mcp/cubepi_runtime.py` | Modify | Add `load_tools_for_specs` (per-spec-list loader) |
| `cubebox/streams/run_manager.py` | Modify | Wire deferred groups into `_run_cubepi_path` |
| `config.yaml` | Modify | Add `mcp.progressive_disclosure` block |
| `tests/unit/test_mcp_disclosure.py` | Create | Config, threshold, spec→group mapping |
| `tests/e2e/test_mcp_disclosure_runtime.py` | Create | Full wiring, catalog, expand, threshold bypass |

---

## Task 1 — Config flags + threshold gate

Add the config surface and pure helpers deciding when disclosure is active.

Files:
- Create: `backend/cubebox/mcp/disclosure.py`
- Modify: `backend/config.yaml` (add `mcp.progressive_disclosure` block)
- Create: `backend/tests/unit/test_mcp_disclosure.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_mcp_disclosure.py
from __future__ import annotations


class TestDisclosureSettings:
    def test_defaults(self) -> None:
        from cubebox.mcp.disclosure import DisclosureSettings

        s = DisclosureSettings()
        assert s.enabled == "auto"
        assert s.threshold_pct == 10.0
        assert s.min_servers == 2

    def test_disabled_never_active(self) -> None:
        from cubebox.mcp.disclosure import DisclosureSettings, disclosure_active

        s = DisclosureSettings(enabled="off")
        assert disclosure_active(s, server_count=10, total_tool_tokens=9999) is False

    def test_on_always_active(self) -> None:
        from cubebox.mcp.disclosure import DisclosureSettings, disclosure_active

        s = DisclosureSettings(enabled="on")
        assert disclosure_active(s, server_count=1, total_tool_tokens=1) is True

    def test_auto_below_min_servers(self) -> None:
        from cubebox.mcp.disclosure import DisclosureSettings, disclosure_active

        s = DisclosureSettings(enabled="auto", min_servers=3)
        # Even if tokens are high, too few servers → inactive.
        assert disclosure_active(s, server_count=2, total_tool_tokens=99999) is False

    def test_auto_below_threshold_pct(self) -> None:
        from cubebox.mcp.disclosure import DisclosureSettings, disclosure_active

        s = DisclosureSettings(enabled="auto", threshold_pct=10.0, min_servers=2)
        # 3 servers but tool tokens are only 5% of context → inactive.
        assert (
            disclosure_active(
                s, server_count=3, total_tool_tokens=5_000, context_window=100_000,
            )
            is False
        )

    def test_auto_above_both_thresholds(self) -> None:
        from cubebox.mcp.disclosure import DisclosureSettings, disclosure_active

        s = DisclosureSettings(enabled="auto", threshold_pct=10.0, min_servers=2)
        # 3 servers, 15% of context → active.
        assert (
            disclosure_active(
                s, server_count=3, total_tool_tokens=15_000, context_window=100_000,
            )
            is True
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_mcp_disclosure.py -v`
Expected: ImportError — `cubebox.mcp.disclosure` does not exist yet.

- [ ] **Step 3: Implement `disclosure.py`**

```python
# cubebox/mcp/disclosure.py
"""MCP progressive disclosure — config, threshold gate, spec→group mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from cubebox.config import config


@dataclass(frozen=True)
class DisclosureSettings:
    enabled: Literal["auto", "on", "off"] = "auto"
    threshold_pct: float = 10.0
    min_servers: int = 2


def load_disclosure_settings() -> DisclosureSettings:
    return DisclosureSettings(
        enabled=str(config.get("mcp.progressive_disclosure.enabled", "auto")),
        threshold_pct=float(config.get("mcp.progressive_disclosure.threshold_pct", 10.0)),
        min_servers=int(config.get("mcp.progressive_disclosure.min_servers", 2)),
    )


def disclosure_active(
    settings: DisclosureSettings,
    *,
    server_count: int,
    total_tool_tokens: int = 0,
    context_window: int = 0,
) -> bool:
    """True when the catalog/expand machinery replaces eager tool loading."""
    if settings.enabled == "off":
        return False
    if settings.enabled == "on":
        return True
    # "auto": both guards must pass.
    if server_count < settings.min_servers:
        return False
    if context_window <= 0:
        return server_count >= settings.min_servers
    return (total_tool_tokens / context_window * 100) >= settings.threshold_pct
```

- [ ] **Step 4: Add config block to `config.yaml`**

Under the existing MCP comment (~line 198), add:

```yaml
mcp:
  progressive_disclosure:
    enabled: "auto"        # "auto" | "on" | "off"
    threshold_pct: 10.0    # collapse when deferrable schemas >= this % of context
    min_servers: 2         # never collapse below this server count
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_mcp_disclosure.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add cubebox/mcp/disclosure.py tests/unit/test_mcp_disclosure.py config.yaml
git commit -m "feat(mcp): disclosure config flags + threshold gate (#143)"
```

---

## Task 2 — Spec-to-group mapping + per-spec-list loader

Build the function that converts `MCPRuntimeConnectorSpec` objects into
`DeferredToolGroup` objects and a helper that loads tools for a subset of specs.

The loader reuses the existing auth-resolution and namespacing pipeline from
`load_workspace_mcp_tools_for_cubepi` — extracted into a shared inner function
so both the eager (all-at-once) and deferred (per-group) paths call the same
code.

Files:
- Modify: `backend/cubebox/mcp/cubepi_runtime.py` — extract `_load_tools_for_specs`
- Modify: `backend/cubebox/mcp/disclosure.py` — add `build_deferred_groups`
- Extend: `backend/tests/unit/test_mcp_disclosure.py`

- [ ] **Step 1: Write the failing tests for `_load_tools_for_specs`**

```python
# tests/unit/test_mcp_disclosure.py (append)
import pytest


class TestLoadToolsForSpecs:
    @pytest.fixture()
    def _patch_load_mcp_tools_http(self, monkeypatch: pytest.MonkeyPatch):
        """Patch cubepi.mcp.load_mcp_tools_http to return dummy tools."""
        from unittest.mock import AsyncMock
        from cubepi.agent.types import AgentTool, AgentToolResult
        from cubepi.providers.base import TextContent
        from pydantic import BaseModel

        class _E(BaseModel):
            pass

        async def _exec(tool_call_id, args, *, signal=None, on_update=None):
            return AgentToolResult(content=[TextContent(text="ok")])

        class FakeDiscovery:
            def __init__(self, names: list[str]):
                self.tools = [
                    AgentTool(name=n, description=f"Tool {n}", parameters=_E, execute=_exec)
                    for n in names
                ]

        async def _fake_load(url, *, headers=None, timeout=30.0, transport="sse"):
            return FakeDiscovery(["create_issue", "search_repos"])

        monkeypatch.setattr("cubebox.mcp.cubepi_runtime.load_mcp_tools_http", _fake_load)

    @pytest.mark.usefixtures("_patch_load_mcp_tools_http")
    async def test_loads_and_namespaces_tools(self) -> None:
        from cubebox.mcp.cubepi_runtime import _load_tools_for_specs
        from cubebox.mcp.effective import MCPRuntimeConnectorSpec

        spec = MCPRuntimeConnectorSpec(
            install_id="inst-001",
            name="GitHub",
            server_url="http://localhost:9999/mcp",
            transport="sse",
            auth_method="none",
            grant_scope=None,
            credential_id=None,
            refresh_credential_id=None,
            tool_citations={},
            tools_cache=[{"name": "create_issue"}, {"name": "search_repos"}],
        )
        # _load_tools_for_specs needs auth dependencies — pass stubs.
        tools, citations = await _load_tools_for_specs(
            specs=[spec],
            all_specs=[spec],
            workspace_id="ws-1",
            org_id="org-1",
            user_id="u-1",
            cred_service=...,    # see Step 3 note
            signer=...,
            token_manager=...,
            grant_repo=None,
        )
        assert len(tools) == 2
        assert tools[0].name.startswith("GitHub__")
```

> **Note:** The exact fixture setup for `cred_service`, `signer`, and
> `token_manager` depends on the `_resolve_auth_from_spec` call. For an
> `auth_method="none"` spec, `signer` must be a real or mocked
> `MCPUserTokenSigner`. Use the E2E fixtures from
> `tests/e2e/conftest.py` as reference. Alternatively, monkeypatch
> `_resolve_auth_from_spec` to return `({}, "http://localhost:9999/mcp")`
> for unit tests.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_mcp_disclosure.py::TestLoadToolsForSpecs -v`
Expected: ImportError — `_load_tools_for_specs` does not exist.

- [ ] **Step 3: Extract `_load_tools_for_specs` from `load_workspace_mcp_tools_for_cubepi`**

Refactor `cubepi_runtime.py`: extract the per-spec loop (lines 123–209) into a
new `_load_tools_for_specs` that takes `specs` + `all_specs` (for slug
collision detection) + the auth dependencies, and returns
`tuple[list[AgentTool], dict[str, CitationConfig]]`. Then
`load_workspace_mcp_tools_for_cubepi` becomes:

```python
async def load_workspace_mcp_tools_for_cubepi(...) -> tuple[...]:
    specs = await effective_service.list_runtime_specs(workspace_id, user_id)
    return await _load_tools_for_specs(
        specs=specs,
        all_specs=specs,
        workspace_id=workspace_id,
        org_id=org_id,
        user_id=user_id,
        cred_service=cred_service,
        signer=signer,
        token_manager=token_manager,
        grant_repo=grant_repo,
    )
```

`_load_tools_for_specs` is a module-private function with signature:

```python
async def _load_tools_for_specs(
    *,
    specs: list[MCPRuntimeConnectorSpec],
    all_specs: list[MCPRuntimeConnectorSpec],
    workspace_id: str,
    org_id: str,
    user_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    token_manager: OAuthTokenManager,
    grant_repo: MCPCredentialGrantRepository | None = None,
) -> tuple[list[AgentTool[Any]], dict[str, CitationConfig]]:
```

`all_specs` is the full set of usable specs (for slug collision pre-computation);
`specs` is the subset to actually load. This way the per-group loader can pass
`specs=[this_group_spec]` and `all_specs=all_workspace_specs` to get correct
namespacing.

- [ ] **Step 4: Run existing MCP tests to verify the refactor didn't break anything**

Run: `cd backend && uv run pytest tests/ -k "mcp" -v --timeout=60`
Expected: all existing MCP tests pass.

- [ ] **Step 5: Write failing tests for `build_deferred_groups`**

```python
# tests/unit/test_mcp_disclosure.py (append)
class TestBuildDeferredGroups:
    def test_builds_groups_from_specs(self) -> None:
        from cubebox.mcp.disclosure import build_deferred_groups
        from cubebox.mcp.effective import MCPRuntimeConnectorSpec

        spec = MCPRuntimeConnectorSpec(
            install_id="inst-001",
            name="GitHub",
            server_url="http://localhost:9999/mcp",
            transport="sse",
            auth_method="none",
            grant_scope=None,
            credential_id=None,
            refresh_credential_id=None,
            tool_citations={},
            tools_cache=[
                {"name": "create_issue", "description": "Create an issue"},
                {"name": "search_repos", "description": "Search repos"},
            ],
            discovery_metadata={"server": {"name": "GitHub"}},
        )
        groups = build_deferred_groups(
            specs=[spec],
            all_specs=[spec],
            loader_kwargs={},  # auth deps — not called in this test
        )
        assert len(groups) == 1
        g = groups[0]
        assert g.group_id == "mcp:GitHub"
        assert g.display_name == "GitHub"
        assert "create_issue" in g.tool_names[0]  # namespaced
        assert "search_repos" in g.tool_names[1]
        assert callable(g.loader)

    def test_description_from_discovery_metadata(self) -> None:
        from cubebox.mcp.disclosure import build_deferred_groups
        from cubebox.mcp.effective import MCPRuntimeConnectorSpec

        spec = MCPRuntimeConnectorSpec(
            install_id="inst-002",
            name="Linear",
            server_url="http://localhost:9999/mcp",
            transport="sse",
            auth_method="none",
            grant_scope=None,
            credential_id=None,
            refresh_credential_id=None,
            tool_citations={},
            tools_cache=[{"name": "create_issue"}],
            discovery_metadata={
                "server": {"name": "Linear", "description": "Issue tracking and PM"},
            },
        )
        groups = build_deferred_groups(
            specs=[spec], all_specs=[spec], loader_kwargs={},
        )
        assert "Issue tracking" in groups[0].description

    def test_fallback_description_when_no_metadata(self) -> None:
        from cubebox.mcp.disclosure import build_deferred_groups
        from cubebox.mcp.effective import MCPRuntimeConnectorSpec

        spec = MCPRuntimeConnectorSpec(
            install_id="inst-003",
            name="MyServer",
            server_url="http://localhost:9999/mcp",
            transport="sse",
            auth_method="none",
            grant_scope=None,
            credential_id=None,
            refresh_credential_id=None,
            tool_citations={},
            tools_cache=[{"name": "do_thing"}],
        )
        groups = build_deferred_groups(
            specs=[spec], all_specs=[spec], loader_kwargs={},
        )
        # Falls back to spec name when no discovery_metadata description.
        assert groups[0].description != ""
```

- [ ] **Step 6: Implement `build_deferred_groups`**

```python
# cubebox/mcp/disclosure.py (append)
from __future__ import annotations

from collections import Counter
from typing import Any

from cubepi.deferred import DeferredToolGroup

from cubebox.mcp._constants import slugify_for_namespace
from cubebox.mcp.cubepi_runtime import (
    _build_namespaced_name_with_prefix,
    _load_tools_for_specs,
    _NS_LENGTH_DEFENCE,
)
from cubebox.mcp.effective import MCPRuntimeConnectorSpec


def _spec_description(spec: MCPRuntimeConnectorSpec) -> str:
    """One-line description from discovery metadata, falling back to name."""
    server = (spec.discovery_metadata or {}).get("server") or {}
    desc = server.get("description") or server.get("summary") or ""
    s = " ".join(desc.split())
    if len(s) > 140:
        s = s[:139].rstrip() + "…"
    return s or spec.name


def _compute_namespaced_tool_names(
    spec: MCPRuntimeConnectorSpec,
    all_specs: list[MCPRuntimeConnectorSpec],
) -> list[str]:
    """Predict namespaced tool names using the same logic as the runtime loader."""
    proposed_slugs = {s.install_id: slugify_for_namespace(s.name) for s in all_specs}
    slug_counts: Counter[str] = Counter(proposed_slugs.values())
    slug = proposed_slugs[spec.install_id]
    explicit_collision = slug_counts[slug] > 1
    risky_truncation = len(slug) > _NS_LENGTH_DEFENCE
    if explicit_collision or risky_truncation:
        safe = spec.install_id.replace("-", "")
        suffix = f"_{safe[-4:] if len(safe) >= 4 else safe}"
    else:
        suffix = ""
    return [
        _build_namespaced_name_with_prefix(slug, tc.get("name", ""), suffix=suffix)
        for tc in spec.tools_cache
        if tc.get("name")
    ]


def build_deferred_groups(
    *,
    specs: list[MCPRuntimeConnectorSpec],
    all_specs: list[MCPRuntimeConnectorSpec],
    loader_kwargs: dict[str, Any],
) -> list[DeferredToolGroup]:
    """Convert MCP runtime specs into cubepi DeferredToolGroup objects.

    ``loader_kwargs`` carries the auth dependencies
    (workspace_id, org_id, user_id, cred_service, signer, token_manager,
    grant_repo) forwarded to ``_load_tools_for_specs`` when the model
    calls ``load_tools``.
    """
    groups: list[DeferredToolGroup] = []
    for spec in specs:
        slug = slugify_for_namespace(spec.name)
        tool_names = _compute_namespaced_tool_names(spec, all_specs)

        async def _loader(_s=spec, _all=all_specs, _kw=loader_kwargs):
            tools, _citations = await _load_tools_for_specs(
                specs=[_s], all_specs=_all, **_kw,
            )
            return tools

        groups.append(
            DeferredToolGroup(
                group_id=f"mcp:{slug}",
                display_name=spec.name,
                description=_spec_description(spec),
                tool_names=tool_names,
                loader=_loader,
            ),
        )
    return groups
```

- [ ] **Step 7: Run tests**

Run: `cd backend && uv run pytest tests/unit/test_mcp_disclosure.py -v`
Expected: all passed.

- [ ] **Step 8: Commit**

```bash
git add cubebox/mcp/disclosure.py cubebox/mcp/cubepi_runtime.py \
    tests/unit/test_mcp_disclosure.py
git commit -m "feat(mcp): spec→DeferredToolGroup mapping + per-spec-list loader (#143)"
```

---

## Task 3 — Wire into run_manager

Integrate the disclosure gate + deferred groups into
`run_manager._run_cubepi_path`. When disclosure is active, MCP servers become
deferred groups instead of eagerly loaded tools. When inactive (below threshold
or `"off"`), the existing eager-load path runs unchanged.

Files:
- Modify: `backend/cubebox/streams/run_manager.py`

- [ ] **Step 1: Add the disclosure branch to `_run_cubepi_path`**

In `_run_cubepi_path`, the MCP tool load block (~line 2155) currently:
1. Opens a session, builds auth services
2. Calls `load_workspace_mcp_tools_for_cubepi(...)` → `_new_tools`, `_new_citations`
3. Extends `_builtin_tools` and `mcp_citation_configs`

Replace with a disclosure-aware branch:

```python
from cubebox.mcp.disclosure import (
    build_deferred_groups,
    disclosure_active,
    load_disclosure_settings,
)

# ... inside the MCP load block, after building _effective_service ...

_disclosure_settings = load_disclosure_settings()
specs = await _effective_service.list_runtime_specs(
    ctx.workspace_id, ctx.user_id,
)

# Estimate tool tokens from tools_cache for the threshold gate.
import json as _json
_total_tool_tokens = sum(
    len(_json.dumps(s.tools_cache)) // 4 for s in specs  # rough token estimate
)

_deferred_groups: list = []

if disclosure_active(
    _disclosure_settings,
    server_count=len(specs),
    total_tool_tokens=_total_tool_tokens,
    context_window=_ctx_window,
):
    # Build deferred groups — loader callbacks will call
    # _load_tools_for_specs with the auth deps captured here.
    _loader_kwargs = dict(
        workspace_id=ctx.workspace_id,
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        cred_service=effective_cred_service,
        signer=self._app.state.mcp_user_token_signer,
        token_manager=_token_manager,
        grant_repo=_grant_repo,
    )
    _deferred_groups = build_deferred_groups(
        specs=specs,
        all_specs=specs,
        loader_kwargs=_loader_kwargs,
    )
    # No tools loaded eagerly — they'll come from load_tools calls.
    # Citations for expanded servers are returned by the per-group
    # loader; wire them into mcp_citation_configs at expand time.
    # (For v1, citations from the loader are added to the agent's
    # citation middleware when the group expands — handled by the
    # DeferredToolsMiddleware's on_tools_expanded callback.)
else:
    # Eager path — today's behavior, unchanged.
    (
        _new_tools,
        _new_citations,
    ) = await load_workspace_mcp_tools_for_cubepi(
        effective_service=_effective_service,
        token_manager=_token_manager,
        workspace_id=ctx.workspace_id,
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        cred_service=effective_cred_service,
        signer=self._app.state.mcp_user_token_signer,
        grant_repo=_grant_repo,
    )
    _builtin_tools.extend(_new_tools)
    mcp_citation_configs.update(_new_citations)
```

- [ ] **Step 2: Pass deferred groups to agent creation**

At the `create_cubebox_agent(...)` call (~line 2663), add
`deferred_tool_groups=_deferred_groups or None`:

```python
agent = create_cubebox_agent(
    ...
    tools=all_tools,
    deferred_tool_groups=_deferred_groups or None,
    ...
)
```

This requires `create_cubebox_agent` to accept and forward
`deferred_tool_groups` to `Agent(...)`. Find the wrapper function and add the
parameter — it likely just passes kwargs through to `Agent()`.

- [ ] **Step 3: Verify `list_runtime_specs` is available outside the session**

The `specs` call needs `_effective_service` which is built inside an
`async with async_session_maker() as effective_session:` block. Ensure the
`specs` query and `build_deferred_groups` both run inside that block. The
loader callbacks capture `_effective_service` (and the session) in their
closure — confirm the session is still open when the loader runs during the
agent loop. If the session closes when the `async with` block exits, the
loader must create its own session. Inspect the lifecycle and adjust — this
is the most likely gotcha.

> **Likely fix:** Each loader callback opens its own short-lived session
> (matching the pattern in `_resolve_auth_from_spec`), or the outer session
> block is widened to encompass the entire agent run. Check what the existing
> MCP load does and follow its pattern.

- [ ] **Step 4: Run mypy + ruff**

```bash
cd backend && uv run mypy cubebox/streams/run_manager.py cubebox/mcp/disclosure.py \
    cubebox/mcp/cubepi_runtime.py && uv run ruff check cubebox tests
```
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add cubebox/streams/run_manager.py cubebox/mcp/disclosure.py \
    cubebox/mcp/cubepi_runtime.py
git commit -m "feat(mcp): wire deferred groups into run_manager (#143)"
```

---

## Task 4 — E2E: disclosure flow + threshold bypass

Write E2E tests that prove (a) when disclosure is active, collapsed servers are
absent from `tools=` and the catalog is in the prompt; (b) the model can expand
a group and the tools become callable; (c) below threshold, behavior is exactly
today's eager load.

Files:
- Create: `backend/tests/e2e/test_mcp_disclosure_runtime.py`

- [ ] **Step 1: Write the E2E test**

Use `stub_discover_tools` to skip the discovery probe, seed `tools_cache` on
installs directly, and monkeypatch `load_mcp_tools_http` (or
`_load_tools_for_specs`) to return callable tools. Reference
`tests/e2e/test_mcp_four_layer_runtime.py` for install-creation patterns.

Tests to write:

1. **`test_disclosure_active_catalog_in_prompt`** — with `enabled="on"` and
   ≥ `min_servers` installs: the assembled `tools=` contains `load_tools` and
   **no** namespaced MCP tools; the system prompt contains the catalog (group
   ids, tool names, tool counts).

2. **`test_expand_group_makes_tools_callable`** — drive a run that calls
   `load_tools("mcp:github")`; assert the expanded server's tools appear in
   the agent's live tool set after expansion.

3. **`test_threshold_bypass_eager_load`** — with `min_servers=5` and only 2
   installs: no catalog, no `load_tools` tool, all servers' tools eagerly
   loaded (today's behavior).

4. **`test_disabled_always_eager`** — with `enabled="off"` and many servers:
   all tools loaded eagerly.

- [ ] **Step 2: Run E2E**

```bash
cd backend && uv run pytest tests/e2e/test_mcp_disclosure_runtime.py -v
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_mcp_disclosure_runtime.py
git commit -m "test(mcp): E2E for disclosure flow + threshold bypass (#143)"
```

---

## Task 5 — Cache-prefix stability E2E

Extend the cache regression tests to assert disclosure doesn't break
prompt-cache stability.

Files:
- Modify or create: `backend/tests/e2e/test_mcp_disclosure_runtime.py` (extend)

- [ ] **Step 1: Write cache stability tests**

1. **`test_catalog_byte_stable_across_turns`** — with disclosure active and
   nothing expanded, the system-prompt suffix (catalog portion) is
   byte-identical on two consecutive prompt assemblies.

2. **`test_expansion_append_only`** — expand group A on turn 1, expand group
   B on turn 2. The turn-2 system prompt starts with the turn-1 system prompt
   as a prefix (the B block is appended, never inserted before A).

These can be unit-ish (call the middleware's `transform_system_prompt` twice
with different expansion states) or full E2E — either works as long as the
byte-identity assertion is exact.

- [ ] **Step 2: Run**

```bash
cd backend && uv run pytest tests/e2e/test_mcp_disclosure_runtime.py -v -k cache
```
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_mcp_disclosure_runtime.py
git commit -m "test(mcp): cache-prefix stability for disclosure (#143)"
```

---

## Task 6 — Pre-PR sweep

Files: none (verification only).

- [ ] **Step 1: Full backend suite**

```bash
cd backend && uv run pytest -q --timeout=120
```
Expected: green.

- [ ] **Step 2: Type-check + lint**

```bash
cd backend && uv run mypy cubebox && uv run ruff check cubebox tests
```
Expected: no errors; lines ≤ 100.

- [ ] **Step 3: Manual verification checklist**

- Collapsed servers contribute zero entries to `tools=` (grep assertions).
- `tools_cache` is used only for catalog display and namespaced-name prediction,
  never as an `AgentTool` source.
- Expanded state is an ordered dict in `extra["expanded_groups"]` end to end and
  survives checkpointing (via cubepi's `DeferredToolsMiddleware`).
- Below-threshold / disabled path is byte-identical to today's behavior.

---

## Open items (not blocking v1)

- Authored `trigger_hints` field + editing UI — Later.
- Per-tool (sub-server) disclosure — Later.
- Semantic retrieval, code-mode, provider-native deferred tools — Later.
- Subagent expanded-set inheritance — v1: subagents start collapsed.
- Re-collapse mid-conversation — explicitly not in v1.
- Citation wiring for expanded groups — v1 relies on the per-group loader
  returning citations; a follow-up may wire them into `CitationMiddleware`
  dynamically.
- Stale `tools_cache`: only the catalog/preview text can drift — accepted
  for v1.
