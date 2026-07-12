# MCP Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add MCP server support so that tools from external MCP servers are loaded at startup and made available to the agent alongside built-in tools.

**Architecture:** Config-driven `MCPManager` wraps `langchain-mcp-adapters`' `MultiServerMCPClient`, fetches tools at module init, filters to the configured tool list, and registers them into the existing `ToolRegistry`. Per-server failures are caught and logged as warnings — they never prevent the system from starting.

**Tech Stack:** `langchain-mcp-adapters`, `nest-asyncio` (already in deps), `dynaconf` config, LangChain `BaseTool` / `StructuredTool`.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/pyproject.toml` | Modify | Add `langchain-mcp-adapters` dependency |
| `backend/config.yaml` | Modify | Update `mcp` section: `servers: {}` (dict, not list) |
| `backend/config.development.yaml` | Modify | Add webtools example server config |
| `backend/cubeplex/mcp/client.py` | Rewrite | `MCPManager` — build connection params, load & filter tools, per-server graceful errors |
| `backend/cubeplex/tools/registry.py` | Modify | Accept `BaseTool` instead of `StructuredTool` (MCP tools are `BaseTool` subclasses) |
| `backend/cubeplex/tools/__init__.py` | Modify | Call `MCPManager.load_tools()` at init if MCP enabled; wrap in try/except |
| `backend/tests/e2e/test_mcp.py` | Create | Tests: disabled MCP skips loading; unreachable server fails gracefully; tool filtering works |

---

### Task 1: Add `langchain-mcp-adapters` dependency

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add `langchain-mcp-adapters` to the `dependencies` list after `langchain-openai`:

```toml
    "langchain-openai>=1.1.10",
    "langchain-mcp-adapters>=0.1.0",
```

- [ ] **Step 2: Install the new dependency**

```bash
cd backend
uv sync --all-extras
```

Expected: resolves and installs `langchain-mcp-adapters` and its transitive deps (includes the `mcp` SDK).

- [ ] **Step 3: Verify import works**

```bash
uv run python -c "from langchain_mcp_adapters.client import MultiServerMCPClient; print('ok')"
```

Expected output: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add langchain-mcp-adapters dependency"
```

---

### Task 2: Update config files

**Files:**
- Modify: `backend/config.yaml`
- Modify: `backend/config.development.yaml`

- [ ] **Step 1: Update `config.yaml` base MCP section**

Replace the existing MCP block (currently `mcp: enabled: false, servers: []`) with:

```yaml
  # MCP Configuration
  mcp:
    enabled: false
    servers: {}
```

The `servers` value changes from `[]` (list) to `{}` (dict) — each server is a named key.

- [ ] **Step 2: Add webtools example to `config.development.yaml`**

Append to the end of `config.development.yaml`:

```yaml
  mcp:
    enabled: true
    servers:
      webtools:
        url: "http://localhost:8020/api/webtools"
        transport: streamable_http
        key: "Pu9bKu1h9yGd9slVf9c9ugxCYd7f3f0V=="
        tools:
          - web_search
          - web_fetch
        enabled: true
```

The `dynaconf_merge: true` at the top of `config.development.yaml` ensures this merges with base config rather than replacing it.

- [ ] **Step 3: Verify config loads**

```bash
cd backend
uv run python -c "
from cubeplex.config import config
import os; os.environ['ENV_FOR_DYNACONF'] = 'development'
print(config.get('mcp.enabled'))
print(list(config.get('mcp.servers', {}).keys()))
"
```

Expected output:
```
True
['webtools']
```

- [ ] **Step 4: Commit**

```bash
git add config.yaml config.development.yaml
git commit -m "config: update MCP section format and add webtools example server"
```

---

### Task 3: Update `ToolRegistry` to accept `BaseTool`

**Files:**
- Modify: `backend/cubeplex/tools/registry.py`

MCP tools returned by `langchain-mcp-adapters` are `BaseTool` instances (the base class). The registry currently accepts `StructuredTool` which is a subclass, causing a type mismatch.

- [ ] **Step 1: Update the registry to use `BaseTool`**

Replace the entire content of `backend/cubeplex/tools/registry.py` with:

```python
"""Tool Registry

Manages registration and retrieval of tools for agents.
Supports both built-in tools and MCP-provided tools.
"""

from langchain_core.tools import BaseTool


class ToolRegistry:
    """Registry for managing agent tools"""

    def __init__(self) -> None:
        """Initialize the tool registry"""
        self._tools: dict[str, BaseTool] = {}

    def register_tool(self, tool: BaseTool) -> None:
        """
        Register a tool.

        Args:
            tool: BaseTool instance to register
        """
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> BaseTool | None:
        """
        Get a tool by name.

        Args:
            name: Tool name

        Returns:
            BaseTool instance or None if not found
        """
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        """
        List all registered tools.

        Returns:
            List of BaseTool instances
        """
        return list(self._tools.values())

    def list_tool_names(self) -> list[str]:
        """
        List all registered tool names.

        Returns:
            List of tool names
        """
        return list(self._tools.keys())
```

- [ ] **Step 2: Run type check**

```bash
cd backend
uv run mypy cubeplex/
```

Expected: `Success: no issues found in N source files`

- [ ] **Step 3: Commit**

```bash
git add cubeplex/tools/registry.py
git commit -m "refactor: ToolRegistry accepts BaseTool to support MCP tools"
```

---

### Task 4: Implement `MCPManager`

**Files:**
- Rewrite: `backend/cubeplex/mcp/client.py`

- [ ] **Step 1: Write the failing test first**

Create `backend/tests/e2e/test_mcp.py`:

```python
"""E2E tests for MCP tool loading."""

import pytest

from cubeplex.mcp.client import MCPManager


class TestMCPManager:
    """Tests for MCPManager tool loading behavior."""

    def test_load_tools_disabled_server_skipped(self) -> None:
        """A server with enabled=false is not connected."""
        manager = MCPManager(
            servers={
                "disabled_server": {
                    "url": "http://localhost:9999/unreachable",
                    "transport": "streamable_http",
                    "enabled": False,
                }
            }
        )
        # Should have no server configs loaded
        assert manager._server_configs == {}

    @pytest.mark.asyncio
    async def test_load_tools_unreachable_server_fails_gracefully(self) -> None:
        """An unreachable server logs a warning and returns empty list."""
        manager = MCPManager(
            servers={
                "bad_server": {
                    "url": "http://localhost:19999/nonexistent",
                    "transport": "streamable_http",
                    "enabled": True,
                }
            }
        )
        # Should not raise — graceful failure
        tools = await manager.load_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_load_tools_filters_by_tool_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When tools list is configured, only listed tools are returned."""
        from unittest.mock import AsyncMock, MagicMock

        # Create two fake tools
        tool_a = MagicMock()
        tool_a.name = "tool_a"
        tool_b = MagicMock()
        tool_b.name = "tool_b"

        # Patch MultiServerMCPClient.get_tools to return both tools
        mock_client = MagicMock()
        mock_client.get_tools = AsyncMock(return_value=[tool_a, tool_b])

        import cubeplex.mcp.client as mcp_module
        monkeypatch.setattr(mcp_module, "MultiServerMCPClient", lambda params: mock_client)

        manager = MCPManager(
            servers={
                "test_server": {
                    "url": "http://localhost:8020/api",
                    "transport": "streamable_http",
                    "enabled": True,
                    "tools": ["tool_a"],  # only tool_a requested
                }
            }
        )
        tools = await manager.load_tools()
        assert len(tools) == 1
        assert tools[0].name == "tool_a"

    @pytest.mark.asyncio
    async def test_load_tools_no_filter_returns_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no tools list configured, all tools from the server are returned."""
        from unittest.mock import AsyncMock, MagicMock

        tool_a = MagicMock()
        tool_a.name = "tool_a"
        tool_b = MagicMock()
        tool_b.name = "tool_b"

        mock_client = MagicMock()
        mock_client.get_tools = AsyncMock(return_value=[tool_a, tool_b])

        import cubeplex.mcp.client as mcp_module
        monkeypatch.setattr(mcp_module, "MultiServerMCPClient", lambda params: mock_client)

        manager = MCPManager(
            servers={
                "test_server": {
                    "url": "http://localhost:8020/api",
                    "transport": "streamable_http",
                    "enabled": True,
                    # no 'tools' key → load all
                }
            }
        )
        tools = await manager.load_tools()
        assert len(tools) == 2
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd backend
uv run pytest tests/e2e/test_mcp.py -v
```

Expected: `ImportError` or `AttributeError` — `MCPManager` doesn't accept `servers` param yet.

- [ ] **Step 3: Implement `MCPManager`**

Replace the entire content of `backend/cubeplex/mcp/client.py` with:

```python
"""MCP (Model Context Protocol) Client

Manages connections to MCP servers and tool integration.
Uses langchain-mcp-adapters MultiServerMCPClient to connect to servers
and expose their tools as LangChain BaseTool instances.
"""

from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from loguru import logger


def _build_connection_params(
    server_name: str, server_config: dict[str, Any]
) -> dict[str, Any] | None:
    """
    Build MultiServerMCPClient connection params for one server.

    Returns None if the transport is unsupported.
    """
    transport = server_config.get("transport")

    if transport in ("streamable_http", "sse"):
        params: dict[str, Any] = {
            "url": server_config["url"],
            "transport": transport,
        }
        key = server_config.get("key")
        if key:
            params["headers"] = {"Authorization": f"Bearer {key}"}
        return params

    elif transport == "stdio":
        params = {
            "command": server_config["command"],
            "args": server_config.get("args", []),
            "transport": "stdio",
        }
        env = server_config.get("env")
        if env:
            params["env"] = env
        return params

    else:
        logger.warning("MCP server '{}': unsupported transport '{}', skipping", server_name, transport)
        return None


class MCPManager:
    """
    Manager for MCP server connections.

    Wraps MultiServerMCPClient to connect to configured MCP servers,
    fetch their tools, and apply optional per-server tool filtering.
    """

    def __init__(self, servers: dict[str, Any] | None = None) -> None:
        """
        Initialize MCPManager.

        Args:
            servers: Dict of server configs keyed by server name.
                     If None, loads from dynaconf config.
        """
        self._server_configs: dict[str, Any] = {}

        if servers is not None:
            self._load_from_dict(servers)
        else:
            self._load_from_config()

    def _load_from_dict(self, servers: dict[str, Any]) -> None:
        """Load server configs from a dict (used in tests)."""
        for server_name, server_config in servers.items():
            if not server_config.get("enabled", True):
                logger.debug("MCP server '{}' is disabled, skipping", server_name)
                continue
            self._server_configs[server_name] = server_config

    def _load_from_config(self) -> None:
        """Load server configs from dynaconf config."""
        from cubeplex.config import config

        servers = config.get("mcp.servers", {})
        if not servers:
            return
        self._load_from_dict(servers)

    async def load_tools(self) -> list[BaseTool]:
        """
        Connect to all enabled MCP servers and return their tools.

        Per-server failures are caught and logged as warnings — the method
        always returns whatever tools were successfully loaded.

        Returns:
            List of BaseTool instances from all reachable servers.
        """
        if not self._server_configs:
            return []

        all_tools: list[BaseTool] = []

        for server_name, server_config in self._server_configs.items():
            try:
                params = _build_connection_params(server_name, server_config)
                if params is None:
                    continue

                client = MultiServerMCPClient({server_name: params})
                tools: list[BaseTool] = await client.get_tools()

                # Filter to requested tool names if specified
                allowed: list[str] | None = server_config.get("tools")
                if allowed:
                    allowed_set = set(allowed)
                    tools = [t for t in tools if t.name in allowed_set]

                logger.info(
                    "MCP server '{}': loaded {} tool(s): {}",
                    server_name,
                    len(tools),
                    [t.name for t in tools],
                )
                all_tools.extend(tools)

            except Exception as e:
                logger.warning(
                    "MCP server '{}' failed to load tools: {}. Skipping.",
                    server_name,
                    str(e),
                )

        return all_tools
```

- [ ] **Step 4: Run the tests**

```bash
cd backend
uv run pytest tests/e2e/test_mcp.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Run type check**

```bash
cd backend
uv run mypy cubeplex/
```

Expected: `Success: no issues found in N source files`

- [ ] **Step 6: Commit**

```bash
git add cubeplex/mcp/client.py tests/e2e/test_mcp.py
git commit -m "feat: implement MCPManager with per-server graceful error handling"
```

---

### Task 5: Load MCP tools at startup

**Files:**
- Modify: `backend/cubeplex/tools/__init__.py`

- [ ] **Step 1: Update `tools/__init__.py` to load MCP tools**

Replace the entire content of `backend/cubeplex/tools/__init__.py` with:

```python
"""Tool system module"""

import asyncio

import nest_asyncio
from langchain_core.tools import BaseTool
from loguru import logger

from cubeplex.tools.builtin.calculator import create_calculator_tool
from cubeplex.tools.registry import ToolRegistry

# Create global tool registry instance
_registry = ToolRegistry()

# Register built-in tools
_registry.register_tool(create_calculator_tool())


def _load_mcp_tools() -> None:
    """
    Load MCP tools into the registry at module init.

    Uses nest_asyncio to allow running async code synchronously.
    Any failure is caught and logged as a warning — MCP errors never
    prevent the system from starting.
    """
    try:
        from cubeplex.config import config

        if not config.get("mcp.enabled", False):
            logger.debug("MCP is disabled, skipping MCP tool loading")
            return

        from cubeplex.mcp.client import MCPManager

        nest_asyncio.apply()
        manager = MCPManager()
        loop = asyncio.get_event_loop()
        tools: list[BaseTool] = loop.run_until_complete(manager.load_tools())

        for tool in tools:
            _registry.register_tool(tool)

        logger.info("Loaded {} MCP tool(s) into registry", len(tools))

    except Exception as e:
        logger.warning(
            "Failed to load MCP tools: {}. Continuing without MCP tools.", str(e)
        )


_load_mcp_tools()


def get_registry() -> ToolRegistry:
    """
    Get the global tool registry instance.

    Returns:
        ToolRegistry instance with all registered tools.
    """
    return _registry


__all__ = ["ToolRegistry", "get_registry"]
```

- [ ] **Step 2: Run type check**

```bash
cd backend
uv run mypy cubeplex/
```

Expected: `Success: no issues found in N source files`

- [ ] **Step 3: Verify startup with MCP disabled (test env)**

The test environment uses `ENV_FOR_DYNACONF=test`. Confirm it imports cleanly:

```bash
cd backend
ENV_FOR_DYNACONF=test uv run python -c "
from cubeplex.tools import get_registry
r = get_registry()
print('Tools loaded:', r.list_tool_names())
"
```

Expected output includes calculator and no MCP error:
```
Tools loaded: ['calculator']
```

- [ ] **Step 4: Run full test suite**

```bash
cd backend
uv run pytest tests/e2e/test_mcp.py tests/e2e/test_conversations.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add cubeplex/tools/__init__.py
git commit -m "feat: load MCP tools into ToolRegistry at startup"
```

---

### Task 6: Final verification

- [ ] **Step 1: Run full lint + type check + tests**

```bash
cd backend
make check
```

Expected: all checks pass (format, lint, mypy, pytest).

- [ ] **Step 2: Verify development config loads MCP server list**

```bash
cd backend
ENV_FOR_DYNACONF=development uv run python -c "
from cubeplex.config import config
servers = config.get('mcp.servers', {})
for name, cfg in servers.items():
    print(f'{name}: transport={cfg[\"transport\"]}, tools={cfg.get(\"tools\", \"all\")}')
"
```

Expected output:
```
webtools: transport=streamable_http, tools=['web_search', 'web_fetch']
```

- [ ] **Step 3: Final commit if anything was changed during verification**

```bash
git add -p
git commit -m "chore: mcp support final cleanup"
```

---

## Summary of Changes

| File | What changed |
|---|---|
| `pyproject.toml` | `langchain-mcp-adapters>=0.1.0` added |
| `config.yaml` | `mcp.servers` changed from `[]` to `{}` |
| `config.development.yaml` | webtools example server with `web_search`, `web_fetch` tools |
| `cubeplex/tools/registry.py` | Uses `BaseTool` instead of `StructuredTool` |
| `cubeplex/mcp/client.py` | Full `MCPManager` implementation |
| `cubeplex/tools/__init__.py` | Calls `_load_mcp_tools()` at init |
| `tests/e2e/test_mcp.py` | 4 tests covering disabled/unreachable/filter/all-tools cases |
