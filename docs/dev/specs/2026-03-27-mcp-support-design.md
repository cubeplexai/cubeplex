# MCP Support Design

**Date:** 2026-03-27
**Scope:** Backend only — MCP tools are transparent to the frontend and agent executor.

## Summary

Add Model Context Protocol (MCP) support to cubeplex so that external MCP servers can provide additional tools to the agent. MCP tools are loaded at startup, registered into the existing `ToolRegistry`, and consumed by `DeepAgentExecutor` exactly like built-in tools. MCP failures are isolated and never crash the system.

## Architecture

```
config.yaml (mcp.servers)
    → MCPManager (cubeplex/mcp/client.py)
        wraps langchain-mcp-adapters MultiServerMCPClient
        per-server graceful error handling
    → cubeplex/tools/__init__.py
        calls MCPManager.load_tools() at module init
        failures log warning, do not raise
    → ToolRegistry
        register_tool() for each MCP-provided StructuredTool
    → DeepAgentExecutor (unchanged)
        reads from ToolRegistry as before
```

## Components

### 1. Dependency: `langchain-mcp-adapters`

Add to `pyproject.toml` dependencies. This library provides `MultiServerMCPClient` which connects to MCP servers and returns LangChain `StructuredTool` instances — no manual protocol implementation needed. Matches the pattern used in cubemanus.

### 2. Config format (`config.yaml`)

```yaml
mcp:
  enabled: true
  servers:
    webtools:
      url: "http://localhost:8020/api/webtools"
      transport: streamable_http   # streamable_http | sse | stdio
      key: "xxx"                   # Bearer token for http transports (optional)
      enabled: true
```

Each server entry supports:
- `transport`: `streamable_http`, `sse`, or `stdio`
- For http transports: `url`, optionally `key` (added as `Authorization: Bearer`)
- For stdio: `command`, `args`
- `enabled`: skip server without removing config
- `tools` (optional): list of tool names to load from this server; if omitted, all tools are loaded. Useful when a server exposes many tools but only a subset are needed.

### 3. `MCPManager` (`cubeplex/mcp/client.py`)

Replaces the existing stub. Responsibilities:
- Read enabled servers from config
- Build `MultiServerMCPClient` connection params (same structure as cubemanus `load_config_file()`)
- `async load_tools() -> list[StructuredTool]`: connect and fetch tools from all servers; if server config has `tools` list, filter to only those names; catch per-server exceptions and log warning, return whatever tools were successfully loaded

### 4. `cubeplex/tools/__init__.py`

At module init (after registering built-in tools), call `MCPManager.load_tools()` if `mcp.enabled` is true. Use `nest_asyncio` + `asyncio.run()` to run the async call synchronously at import time (same pattern as cubemanus). Wrap in try/except — MCP failure logs warning, does not raise.

## Error Handling

| Failure scenario | Behavior |
|---|---|
| MCP disabled in config | Skip entirely |
| Server `enabled: false` | Skip that server |
| Network/connection error per server | Log warning, skip server, continue |
| All MCP servers fail | Log warning, zero MCP tools registered, system starts normally |
| Tool call fails at runtime | Propagated by LangChain tool error handling (existing behavior) |

## Example Config (webtools)

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

`tools` is optional. When specified, only the listed tools are registered from that server. When omitted, all tools the server exposes are loaded.

## Files Changed

| File | Change |
|---|---|
| `backend/pyproject.toml` | Add `langchain-mcp-adapters` dependency |
| `backend/config.yaml` | Update `mcp` section with server config format |
| `backend/config.development.yaml` | Add webtools example config |
| `backend/cubeplex/mcp/client.py` | Implement `MCPManager` |
| `backend/cubeplex/tools/__init__.py` | Load MCP tools at startup |

No frontend changes. No database changes. No API changes.

## Out of Scope

- Frontend MCP configuration UI
- Per-request MCP tool loading
- MCP server health monitoring / retry
- Tool filtering per server (load all tools from each enabled server)
