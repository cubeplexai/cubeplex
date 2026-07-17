# cubepi Cleanup Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the cubepi migration by removing every remaining `langchain*` / `langgraph*` / `langsmith` reference from the backend — production code, tests, configs, deps, and stale documentation.

**Architecture:** M6 finished the agent-runtime migration but left five concrete leftovers: (1) the admin/OAuth MCP path still uses `langchain-mcp-adapters`; (2) `CompactionMiddleware` summarizer + token counter still routes through LangChain `BaseChatModel` / `count_tokens_approximately`; (3) `LLMFactory.create()` still builds `ChatOpenAI` / `ChatAnthropic` for the summarizer; (4) `ProviderService` connection-test uses LangChain; (5) `LangSmith` plumbing is dead weight (cubepi doesn't trace through it). After this plan: zero `langchain` / `langgraph` / `langsmith` imports, four PyPI deps removed, `uv lock` regenerated, archival docs marked superseded.

**Tech Stack:** cubepi >=0.3.0 (existing path dep), `mcp` SDK (already transitive via cubepi.mcp.http_loader), pytest + pytest-asyncio for tests.

---

## Pre-flight

- [ ] **Confirm working branch and clean tree.**

  Run: `git status && git rev-parse --abbrev-ref HEAD`
  Expected: clean tree on a feature branch (e.g. `feat/cubepi-cleanup`). If on `main`, create branch first.

- [ ] **Confirm baseline tests pass on current HEAD.**

  Run from `backend/`: `make check`
  Expected: PASS (format, lint, type-check, test).

  If anything fails on current HEAD, stop and fix before starting. Don't tangle pre-existing failures with this cleanup.

- [ ] **Verify cubepi.mcp surface area.**

  Run: `uv run python -c "from cubepi.mcp import load_mcp_tools_http; from mcp import ClientSession; from mcp.client.sse import sse_client; print('ok')"`
  Expected: prints `ok`. (Confirms the raw `mcp` SDK is importable — we'll use it directly for the discovery-without-execution path in Phase 1.)

---

## Phase 1 — Migrate admin/OAuth MCP path off langchain-mcp-adapters

Current state: `cubeplex/mcp/runtime.py` and `cubeplex/mcp/discovery.py` use `MultiServerMCPClient` + LangChain `BaseTool`. They are called from `services/mcp.py`, `services/mcp_catalog.py`, and `mcp/oauth/callback.py` for admin tool discovery and OAuth-driven refresh. The per-run cubepi runtime path (`cubepi_runtime.py`) already bypasses these — we only need to replace the discovery/refresh helpers.

The cubepi runtime hits the MCP server fresh per run, so `MCPServer.tools_cache` is now **admin-UI-only metadata**: it shows operators which tools a server exposes and tracks `authed` / `last_error` / `last_discovered_at`. We need a pure listing helper, not a BaseTool builder.

### Task 1.1: Add `cubepi_admin_discovery.discover_tools_metadata`

**Files:**
- Create: `backend/cubeplex/mcp/cubepi_admin_discovery.py`
- Test: `backend/tests/unit/test_cubepi_admin_discovery.py`

- [ ] **Step 1: Write the failing test.**

  Create `backend/tests/unit/test_cubepi_admin_discovery.py`:

  ```python
  """Unit tests for cubepi_admin_discovery — list tools via raw mcp SDK."""

  from __future__ import annotations

  from typing import Any
  from unittest.mock import AsyncMock, MagicMock, patch

  import pytest

  from cubeplex.mcp.cubepi_admin_discovery import discover_tools_metadata
  from cubeplex.models import MCPServer


  @pytest.fixture
  def http_server() -> MCPServer:
      return MCPServer(
          id="mcp-test1",
          org_id="org-test",
          name="test-server",
          server_url="https://mcp.example.com/sse",
          transport="http",
          auth_method="bearer",
          credential_scope="org",
          owner_workspace_id=None,
          credential_id=None,
          authed=False,
          tools_cache=[],
          headers={},
      )


  @pytest.mark.asyncio
  async def test_returns_serialized_tools_on_success(http_server: MCPServer) -> None:
      fake_tool = MagicMock(name="echo", description="Echo input", inputSchema={"type": "object"})
      fake_tool.name = "echo"
      fake_tool.description = "Echo input"
      fake_tool.inputSchema = {"type": "object", "properties": {"text": {"type": "string"}}}

      fake_resp = MagicMock(tools=[fake_tool])
      fake_session = AsyncMock()
      fake_session.initialize = AsyncMock()
      fake_session.list_tools = AsyncMock(return_value=fake_resp)

      with patch("cubeplex.mcp.cubepi_admin_discovery.sse_client") as m_sse, \
           patch("cubeplex.mcp.cubepi_admin_discovery.ClientSession") as m_cs:
          m_sse.return_value.__aenter__.return_value = ("r", "w")
          m_cs.return_value.__aenter__.return_value = fake_session

          ok, tools, err = await discover_tools_metadata(http_server, credential_or_token="tok-abc")

      assert ok is True
      assert err is None
      assert tools == [
          {
              "name": "echo",
              "description": "Echo input",
              "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
          }
      ]


  @pytest.mark.asyncio
  async def test_returns_error_on_exception(http_server: MCPServer) -> None:
      with patch("cubeplex.mcp.cubepi_admin_discovery.sse_client") as m_sse:
          m_sse.side_effect = RuntimeError("connection refused")
          ok, tools, err = await discover_tools_metadata(http_server, credential_or_token=None)

      assert ok is False
      assert tools is None
      assert err is not None
      assert "connection refused" in err


  @pytest.mark.asyncio
  async def test_authorization_header_set_when_token_given(http_server: MCPServer) -> None:
      fake_resp = MagicMock(tools=[])
      fake_session = AsyncMock()
      fake_session.initialize = AsyncMock()
      fake_session.list_tools = AsyncMock(return_value=fake_resp)

      with patch("cubeplex.mcp.cubepi_admin_discovery.sse_client") as m_sse, \
           patch("cubeplex.mcp.cubepi_admin_discovery.ClientSession") as m_cs:
          m_sse.return_value.__aenter__.return_value = ("r", "w")
          m_cs.return_value.__aenter__.return_value = fake_session

          await discover_tools_metadata(http_server, credential_or_token="tok-abc")

      # First positional arg = url; kwargs include headers.
      _, kwargs = m_sse.call_args
      assert kwargs["headers"]["Authorization"] == "Bearer tok-abc"
  ```

- [ ] **Step 2: Run test to verify it fails (module does not exist yet).**

  Run from `backend/`: `uv run pytest tests/unit/test_cubepi_admin_discovery.py -v`
  Expected: collection error — `ModuleNotFoundError: No module named 'cubeplex.mcp.cubepi_admin_discovery'`.

- [ ] **Step 3: Create the module.**

  Create `backend/cubeplex/mcp/cubepi_admin_discovery.py`:

  ```python
  """Admin-side MCP discovery (list-tools only) for cubepi runtime.

  Replaces the langchain-mcp-adapters MultiServerMCPClient path in the old
  cubeplex.mcp.discovery module. Uses the raw `mcp` SDK to call
  `session.list_tools()` and serialize the result to the same
  ``{name, description, input_schema}`` shape persisted in
  ``MCPServer.tools_cache``.

  The cubepi per-run path uses ``cubepi.mcp.load_mcp_tools_http`` directly
  and does NOT consult this cache; tools_cache is admin-UI metadata.
  """

  from __future__ import annotations

  import asyncio
  from typing import Any

  from loguru import logger
  from mcp import ClientSession
  from mcp.client.sse import sse_client

  from cubeplex.mcp.connection_params import build_connection_params
  from cubeplex.models import MCPServer

  _TIMEOUT = 30.0


  async def discover_tools_metadata(
      server: MCPServer,
      *,
      credential_or_token: str | None,
  ) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
      """Connect, list tools, return (success, serialized tools | None, error | None).

      Same return contract as the deprecated ``cubeplex.mcp.discovery.discover_tools``.
      """
      try:
          params = build_connection_params(server, credential_or_token=credential_or_token)
      except ValueError as exc:
          return False, None, f"params build failed: {exc}"

      url = params.get("url")
      headers = params.get("headers", {})
      if not isinstance(url, str) or not url:
          return False, None, "missing or invalid url in connection params"

      try:
          async with sse_client(url, headers=headers, timeout=_TIMEOUT) as streams:
              async with ClientSession(*streams) as session:
                  await asyncio.wait_for(session.initialize(), timeout=_TIMEOUT)
                  resp = await asyncio.wait_for(session.list_tools(), timeout=_TIMEOUT)
      except BaseExceptionGroup as exc:
          causes = "; ".join(str(sub) for sub in exc.exceptions)
          return False, None, f"{exc}; causes: {causes}"
      except Exception as exc:
          return False, None, str(exc)

      tools: list[dict[str, Any]] = []
      for desc in resp.tools or []:
          tools.append(
              {
                  "name": desc.name,
                  "description": desc.description or "",
                  "input_schema": desc.inputSchema or {"type": "object", "properties": {}},
              }
          )

      logger.debug("MCP discovery: server={}, tools={}", server.name, len(tools))
      return True, tools, None
  ```

- [ ] **Step 4: Run tests to verify they pass.**

  Run: `uv run pytest tests/unit/test_cubepi_admin_discovery.py -v`
  Expected: 3 PASSED.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/cubeplex/mcp/cubepi_admin_discovery.py backend/tests/unit/test_cubepi_admin_discovery.py
  git commit -m "feat(mcp): add cubepi_admin_discovery for langchain-free tools listing"
  ```

### Task 1.2: Add `cubepi_admin_refresh.refresh_tools_for_server_with_token`

**Files:**
- Create: `backend/cubeplex/mcp/cubepi_admin_refresh.py`
- Test: `backend/tests/unit/test_cubepi_admin_refresh.py`

- [ ] **Step 1: Write the failing test.**

  Create `backend/tests/unit/test_cubepi_admin_refresh.py`:

  ```python
  """Unit tests for cubepi_admin_refresh — persist discovery result to the DB row."""

  from __future__ import annotations

  from datetime import UTC, datetime
  from unittest.mock import AsyncMock, patch

  import pytest

  from cubeplex.mcp.cubepi_admin_refresh import refresh_tools_for_server_with_token
  from cubeplex.models import MCPServer


  @pytest.fixture
  def server() -> MCPServer:
      return MCPServer(
          id="mcp-1",
          org_id="org-1",
          name="srv",
          server_url="https://srv/sse",
          transport="http",
          auth_method="bearer",
          credential_scope="org",
          owner_workspace_id=None,
          credential_id=None,
          authed=False,
          tools_cache=[],
          headers={},
      )


  @pytest.mark.asyncio
  async def test_success_updates_cache_and_authed(server: MCPServer) -> None:
      tools = [{"name": "t", "description": "", "input_schema": {}}]
      with patch(
          "cubeplex.mcp.cubepi_admin_refresh.discover_tools_metadata",
          AsyncMock(return_value=(True, tools, None)),
      ):
          server_repo = AsyncMock()
          await refresh_tools_for_server_with_token(
              server, server_repo=server_repo, credential_or_token="x"
          )

      assert server.authed is True
      assert server.tools_cache == tools
      assert server.last_error is None
      assert isinstance(server.last_discovered_at, datetime)
      server_repo.update.assert_awaited_once_with(server)


  @pytest.mark.asyncio
  async def test_failure_persists_error(server: MCPServer) -> None:
      with patch(
          "cubeplex.mcp.cubepi_admin_refresh.discover_tools_metadata",
          AsyncMock(return_value=(False, None, "boom")),
      ):
          server_repo = AsyncMock()
          await refresh_tools_for_server_with_token(
              server, server_repo=server_repo, credential_or_token=None
          )

      assert server.authed is False
      assert server.tools_cache == []
      assert server.last_error == "boom"
      server_repo.update.assert_awaited_once_with(server)
  ```

- [ ] **Step 2: Run test to verify it fails.**

  Run: `uv run pytest tests/unit/test_cubepi_admin_refresh.py -v`
  Expected: collection error — module missing.

- [ ] **Step 3: Create the module.**

  Create `backend/cubeplex/mcp/cubepi_admin_refresh.py`:

  ```python
  """Persist MCP discovery result back to the DB row (admin/OAuth path)."""

  from __future__ import annotations

  from datetime import UTC, datetime

  from cubeplex.mcp.cubepi_admin_discovery import discover_tools_metadata
  from cubeplex.models import MCPServer
  from cubeplex.repositories.mcp import MCPServerRepository


  async def refresh_tools_for_server_with_token(
      server: MCPServer,
      *,
      server_repo: MCPServerRepository,
      credential_or_token: str | None,
  ) -> None:
      """Run tool discovery against ``server`` and persist the result.

      Updates ``authed`` / ``tools_cache`` / ``last_error`` / ``last_discovered_at``
      and commits via the repository. Same contract as the deprecated
      ``cubeplex.mcp.runtime.refresh_tools_for_server_with_token``.
      """
      success, tools, error = await discover_tools_metadata(
          server, credential_or_token=credential_or_token
      )
      server.authed = success
      server.tools_cache = tools or []
      server.last_error = None if success else error
      server.last_discovered_at = datetime.now(UTC)
      await server_repo.update(server)
  ```

- [ ] **Step 4: Run tests to verify they pass.**

  Run: `uv run pytest tests/unit/test_cubepi_admin_refresh.py -v`
  Expected: 2 PASSED.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/cubeplex/mcp/cubepi_admin_refresh.py backend/tests/unit/test_cubepi_admin_refresh.py
  git commit -m "feat(mcp): add cubepi_admin_refresh to persist discovery results"
  ```

### Task 1.3: Switch `services/mcp.py` and `services/mcp_catalog.py` to cubepi helpers

**Files:**
- Modify: `backend/cubeplex/services/mcp.py`
- Modify: `backend/cubeplex/services/mcp_catalog.py`
- Test: existing tests `backend/tests/unit/test_mcp_service_invariants.py` (will need monkeypatch path updates) and `backend/tests/unit/mcp/test_oauth_callback*.py`.

- [ ] **Step 1: Update `services/mcp.py` imports and call sites.**

  In `backend/cubeplex/services/mcp.py`:

  - Replace `from cubeplex.mcp.discovery import discover_tools` (line 13) with
    `from cubeplex.mcp.cubepi_admin_discovery import discover_tools_metadata as discover_tools`.
  - Replace the deferred `from cubeplex.mcp.runtime import refresh_tools_for_server_with_token` (line 692) with
    `from cubeplex.mcp.cubepi_admin_refresh import refresh_tools_for_server_with_token`.
  - No call-site changes needed: argument and return shapes match.

- [ ] **Step 2: Update `services/mcp_catalog.py`.**

  In `backend/cubeplex/services/mcp_catalog.py` (line 34): replace
  `from cubeplex.mcp.discovery import discover_tools` with
  `from cubeplex.mcp.cubepi_admin_discovery import discover_tools_metadata as discover_tools`.

- [ ] **Step 3: Update monkeypatch paths in `tests/unit/test_mcp_service_invariants.py`.**

  Replace every occurrence of `"cubeplex.mcp.runtime.discover_tools"` with
  `"cubeplex.services.mcp.discover_tools"` (we monkeypatch the binding in the consumer module now, because the `as discover_tools` import in `services/mcp.py` creates a fresh local name).

  Also update the comment at line 86 (`# moved to cubeplex.mcp.runtime. Patch both bindings.`) to reflect the new location.

- [ ] **Step 4: Run affected test suites.**

  Run: `uv run pytest tests/unit/test_mcp_service_invariants.py -v`
  Expected: PASS.

  Run: `uv run pytest tests/unit/mcp/ -v`
  Expected: PASS (these tests patch `cubeplex.mcp.runtime.discover_tools` — we'll fix them in Task 1.4).

  If the mcp/ tests fail, that's expected — they're addressed next.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/cubeplex/services/mcp.py backend/cubeplex/services/mcp_catalog.py \
          backend/tests/unit/test_mcp_service_invariants.py
  git commit -m "refactor(mcp): switch services to cubepi_admin_discovery/refresh"
  ```

### Task 1.4: Update OAuth callback + its tests

**Files:**
- Modify: `backend/cubeplex/mcp/oauth/callback.py:255-257`
- Modify: `backend/tests/unit/mcp/test_oauth_callback.py`
- Modify: `backend/tests/unit/mcp/test_oauth_callback_route.py`

- [ ] **Step 1: Update `oauth/callback.py`.**

  In `backend/cubeplex/mcp/oauth/callback.py` around line 257, replace the deferred import:

  ```python
  from cubeplex.mcp.runtime import refresh_tools_for_server_with_token
  ```

  with:

  ```python
  from cubeplex.mcp.cubepi_admin_refresh import refresh_tools_for_server_with_token
  ```

  Also update the comment above it (line 255) from `# Deferred import to break the cubeplex.mcp.runtime ↔ oauth.callback` to describe the cubepi module instead.

- [ ] **Step 2: Update test monkeypatch / patch paths.**

  In `backend/tests/unit/mcp/test_oauth_callback.py` and `backend/tests/unit/mcp/test_oauth_callback_route.py`:
  Replace every `patch("cubeplex.mcp.runtime.discover_tools", ...)` with
  `patch("cubeplex.mcp.cubepi_admin_discovery.discover_tools_metadata", ...)`.

  Verify the patched function's return signature: both old and new return `(bool, list[dict] | None, str | None)` — same contract, no test logic changes needed.

- [ ] **Step 3: Run OAuth callback tests.**

  Run: `uv run pytest tests/unit/mcp/ -v`
  Expected: PASS.

- [ ] **Step 4: Commit.**

  ```bash
  git add backend/cubeplex/mcp/oauth/callback.py backend/tests/unit/mcp/
  git commit -m "refactor(mcp): switch OAuth callback to cubepi_admin_refresh"
  ```

### Task 1.5: Delete old `mcp/runtime.py`, `mcp/discovery.py`, and their tests

**Files:**
- Delete: `backend/cubeplex/mcp/runtime.py`
- Delete: `backend/cubeplex/mcp/discovery.py`
- Delete: `backend/tests/unit/test_mcp_runtime.py`
- Delete: `backend/tests/unit/test_discovery_serialize.py`
- Delete: `backend/tests/e2e/test_mcp_passthrough_jwt.py`

- [ ] **Step 1: Verify no remaining references.**

  Run: `git grep -nE "from cubeplex\.mcp\.(runtime|discovery) import|cubeplex\.mcp\.(runtime|discovery)\." backend/`
  Expected: only matches inside the two files themselves and the three tests being deleted. If anything else still imports them, fix that consumer first.

- [ ] **Step 2: Delete the files.**

  ```bash
  git rm backend/cubeplex/mcp/runtime.py \
         backend/cubeplex/mcp/discovery.py \
         backend/tests/unit/test_mcp_runtime.py \
         backend/tests/unit/test_discovery_serialize.py \
         backend/tests/e2e/test_mcp_passthrough_jwt.py
  ```

  Note: `test_mcp_passthrough_jwt.py` tests the legacy langchain runtime's user-token JWT path. The cubepi runtime exercises this through `cubepi_discovery._resolve_token_for_cubepi` (effective_scope == "none" branch); existing cubepi unit tests cover it. Confirm by:

  Run: `git grep -nE "test_mcp_passthrough|MCPUserTokenSigner" backend/tests/`
  Expected: matches in cubepi_discovery's own unit tests, NOT in any test that imports the deleted file.

- [ ] **Step 3: Run full test suite to catch any indirect breakage.**

  Run from `backend/`: `make test`
  Expected: PASS.

- [ ] **Step 4: Update backend/CLAUDE.md.**

  In `backend/CLAUDE.md` line 135, remove the sentence `admin/OAuth tool-refresh paths (cubeplex/mcp/runtime.py + discovery.py) still use langchain-mcp-adapters (port pending).` — replace with a one-line mention that admin tool discovery is `cubeplex.mcp.cubepi_admin_discovery`.

  Concrete edit: change the bullet to:
  ```
  - MCP integration: per-run discovery via
    ``cubeplex.mcp.cubepi_runtime.load_workspace_mcp_tools_for_cubepi``;
    admin tool refresh via ``cubeplex.mcp.cubepi_admin_refresh``.
  ```

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/cubeplex/mcp/ backend/tests/unit/ backend/tests/e2e/ backend/CLAUDE.md
  git commit -m "refactor(mcp): delete langchain-mcp-adapters runtime/discovery (M6.5 closeout)"
  ```

---

## Phase 2 — Move CompactionMiddleware fully to cubepi types

Current state: `compaction/__init__.py` builds a `_to_langchain_messages()` bridge so the summarizer can consume LC messages; `summarizer.py` requires `BaseChatModel`; `tokens.py` uses `count_tokens_approximately`; `boundary.py` does isinstance checks on `AIMessage`/`HumanMessage`/`ToolMessage`. Goal: every module operates directly on `cubepi.providers.base` types.

### Task 2.1: Rewrite `boundary.py` to operate on cubepi messages

**Files:**
- Modify: `backend/cubeplex/middleware/compaction/boundary.py`
- Test: `backend/tests/unit/middleware/compaction/test_boundary.py`

- [ ] **Step 1: Rewrite the test to use cubepi messages.**

  Replace `backend/tests/unit/middleware/compaction/test_boundary.py` contents:

  ```python
  """Unit tests for safe_boundary — operates on cubepi messages."""

  from __future__ import annotations

  from cubepi.providers.base import (
      AssistantMessage,
      Message,
      TextContent,
      ToolCall,
      ToolResultContent,
      ToolResultMessage,
      UserMessage,
  )

  from cubeplex.middleware.compaction.boundary import safe_boundary


  def _user(text: str) -> UserMessage:
      return UserMessage(content=[TextContent(text=text)])


  def _assistant(text: str = "", tool_calls: list[ToolCall] | None = None) -> AssistantMessage:
      content: list = []
      if text:
          content.append(TextContent(text=text))
      if tool_calls:
          content.extend(tool_calls)
      return AssistantMessage(content=content)


  def _tool_result(call_id: str, text: str = "ok") -> ToolResultMessage:
      return ToolResultMessage(
          tool_call_id=call_id,
          content=[ToolResultContent(text=text)],
      )


  def test_returns_none_when_too_few_messages() -> None:
      msgs: list[Message] = [_user("hi"), _assistant("hello")]
      assert safe_boundary(msgs, keep_recent=4, min_compact=1) is None


  def test_returns_boundary_at_human_message_start() -> None:
      msgs: list[Message] = [
          _user("q1"),
          _assistant("a1"),
          _user("q2"),
          _assistant("a2"),
          _user("q3"),
          _assistant("a3"),
      ]
      # keep_recent=2 → candidate idx=4 (UserMessage) ✓
      assert safe_boundary(msgs, keep_recent=2, min_compact=1) == 4


  def test_skips_orphan_tool_results_in_suffix() -> None:
      tc = ToolCall(id="c1", name="f", arguments={})
      msgs: list[Message] = [
          _user("q1"),
          _assistant("", [tc]),
          _tool_result("c1"),
          _user("q2"),
          _tool_result("orphan"),  # orphan: no matching tool_call in suffix from idx=4
          _assistant("done"),
      ]
      # candidate=4 is not a UserMessage; candidate=3 is UserMessage but suffix has orphan
      # candidate=0 not allowed (no min_compact prefix)
      assert safe_boundary(msgs, keep_recent=2, min_compact=1) is None


  def test_min_compact_enforced() -> None:
      msgs: list[Message] = [
          _user("q1"),
          _assistant("a1"),
          _user("q2"),
          _assistant("a2"),
      ]
      # candidate=2 is UserMessage with self-contained suffix, but min_compact=3 → None
      assert safe_boundary(msgs, keep_recent=2, min_compact=3) is None
      assert safe_boundary(msgs, keep_recent=2, min_compact=1) == 2
  ```

- [ ] **Step 2: Run the test to verify it fails (old boundary.py still uses LC types).**

  Run: `uv run pytest tests/unit/middleware/compaction/test_boundary.py -v`
  Expected: FAIL — type mismatch / isinstance check fails because the old code does `isinstance(msg, HumanMessage)` and our test passes `UserMessage`.

- [ ] **Step 3: Rewrite `boundary.py`.**

  Replace `backend/cubeplex/middleware/compaction/boundary.py` contents:

  ```python
  """Boundary selection for compaction — picks a safe split point.

  Operates on cubepi message types from cubepi.providers.base.
  """

  from __future__ import annotations

  from cubepi.providers.base import (
      AssistantMessage,
      Message,
      ToolCall,
      ToolResultMessage,
      UserMessage,
  )


  def safe_boundary(
      messages: list[Message],
      *,
      keep_recent: int,
      min_compact: int = 1,
  ) -> int | None:
      """Return an index `b` such that messages[:b] is summarizable and messages[b:] is kept.

      Constraints:
        1. messages[b:] must contain >= keep_recent items.
        2. messages[b] must be a UserMessage (start of a turn).
        3. messages[b:] must not contain a ToolResultMessage whose tool_call_id
           has no matching ToolCall in an AssistantMessage within messages[b:].
        4. If no boundary satisfies all and leaves at least min_compact messages
           in the prefix, return None (caller skips compaction this round).
      """
      n = len(messages)
      if n <= keep_recent:
          return None

      candidate = n - keep_recent
      while candidate > 0:
          msg = messages[candidate]
          if not isinstance(msg, UserMessage):
              candidate -= 1
              continue
          if not _suffix_is_self_contained(messages[candidate:]):
              candidate -= 1
              continue
          if candidate < min_compact:
              return None
          return candidate

      return None


  def _suffix_is_self_contained(suffix: list[Message]) -> bool:
      """Every ToolResultMessage in the suffix must have its parent ToolCall in the suffix."""
      available_call_ids: set[str] = set()
      for msg in suffix:
          if isinstance(msg, AssistantMessage):
              for block in msg.content:
                  if isinstance(block, ToolCall) and block.id:
                      available_call_ids.add(block.id)
          elif isinstance(msg, ToolResultMessage):
              if msg.tool_call_id and msg.tool_call_id not in available_call_ids:
                  return False
      return True
  ```

- [ ] **Step 4: Run tests.**

  Run: `uv run pytest tests/unit/middleware/compaction/test_boundary.py -v`
  Expected: 4 PASSED.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/cubeplex/middleware/compaction/boundary.py \
          backend/tests/unit/middleware/compaction/test_boundary.py
  git commit -m "refactor(compaction): rewrite boundary.py on cubepi message types"
  ```

### Task 2.2: Replace `tokens.py` with cubepi-native estimator

The existing `_cubepi_approx_tokens` in `compaction/__init__.py` already implements the right algorithm. We promote it to `tokens.py` and delete the LC variant.

**Files:**
- Modify: `backend/cubeplex/middleware/compaction/tokens.py`
- Test: `backend/tests/unit/middleware/compaction/test_tokens.py`

- [ ] **Step 1: Rewrite the test to use cubepi messages.**

  Replace `backend/tests/unit/middleware/compaction/test_tokens.py` contents:

  ```python
  """Unit tests for approx_tokens (cubepi-native)."""

  from __future__ import annotations

  from cubepi.providers.base import (
      AssistantMessage,
      MessageUsage,
      TextContent,
      ToolResultContent,
      ToolResultMessage,
      UserMessage,
  )

  from cubeplex.middleware.compaction.tokens import _CHARS_PER_TOKEN, approx_tokens


  def test_empty_returns_zero() -> None:
      assert approx_tokens([]) == 0


  def test_text_chars_divided_by_chars_per_token() -> None:
      # 200 chars / 2.0 = 100 tokens
      msgs = [UserMessage(content=[TextContent(text="x" * 200)])]
      assert approx_tokens(msgs) == 100


  def test_usage_metadata_scales_up_when_assistant_has_input_tokens() -> None:
      # Establish 1000 chars across messages, then assistant reports input_tokens=400.
      # raw_factor = 1000 / (400 * 2.0) = 1.25 → scale clamped to [1.0, 1.25] → 1.25.
      # final estimate = (1000 / 2.0) * 1.25 = 625.
      msgs = [
          UserMessage(content=[TextContent(text="x" * 900)]),
          AssistantMessage(
              content=[TextContent(text="y" * 100)],
              usage=MessageUsage(input_tokens=400, output_tokens=10),
          ),
      ]
      assert approx_tokens(msgs) == 625


  def test_tool_result_text_counted() -> None:
      msgs = [
          ToolResultMessage(
              tool_call_id="c1",
              content=[ToolResultContent(text="x" * 200)],
          ),
      ]
      assert approx_tokens(msgs) == 100
  ```

- [ ] **Step 2: Run the test to verify it fails.**

  Run: `uv run pytest tests/unit/middleware/compaction/test_tokens.py -v`
  Expected: FAIL — import error or signature mismatch (existing `approx_tokens` takes LC messages).

- [ ] **Step 3: Rewrite `tokens.py`.**

  Replace `backend/cubeplex/middleware/compaction/tokens.py` contents:

  ```python
  """Approximate token counting for cubepi messages.

  IMPORTANT: callers must pass the view they intend to send to the LLM
  (i.e. the post-compaction projection [summary, *recent]), NOT the raw
  message history. Passing raw history breaks scaling accuracy because
  historical AssistantMessage.usage reflects the compressed view the
  LLM actually saw — comparing it against an approx walked over the full
  history yields a scale_factor < 1 (clamped to 1.0, scaling disabled).
  """

  from __future__ import annotations

  import json

  from cubepi.providers.base import (
      AssistantMessage,
      Message,
      TextContent,
      ToolCall,
      UserMessage,
  )

  # 2.0 chars/token is a deliberate conservative override of the 4.0 default
  # used by langchain_core. 4.0 underestimates Chinese / CJK by 3-4x; with
  # our threshold of context_window * 0.7, underestimating means compacting
  # too late → overflow. Once usage scaling kicks in (turn 2+), the value
  # self-corrects — this just protects the cold start.
  _CHARS_PER_TOKEN = 2.0

  # Minimum input_tokens before we trust usage metadata for scaling.
  _SCALE_MIN_TOKENS = 100


  def approx_tokens(messages: list[Message]) -> int:
      """Approximate total tokens for a list of cubepi messages.

      For AssistantMessages with ``usage.input_tokens >= _SCALE_MIN_TOKENS``,
      derives a chars-per-token scale factor (clamped to [1.0, 1.25]) so
      historical real token counts auto-calibrate the estimate.
      """
      if not messages:
          return 0

      total_chars = 0
      scale_factor: float | None = None

      for msg in messages:
          if isinstance(msg, UserMessage):
              for block in msg.content:
                  if isinstance(block, TextContent):
                      total_chars += len(block.text)
          elif isinstance(msg, AssistantMessage):
              for block in msg.content:
                  if isinstance(block, TextContent):
                      total_chars += len(block.text)
                  elif isinstance(block, ToolCall):
                      total_chars += len(json.dumps(block.arguments or {}))
              usage = msg.usage
              if usage and usage.input_tokens >= _SCALE_MIN_TOKENS and scale_factor is None:
                  chars_estimate = usage.input_tokens * _CHARS_PER_TOKEN
                  if chars_estimate > 0:
                      raw_factor = total_chars / chars_estimate
                      scale_factor = max(1.0, min(raw_factor, 1.25))
          else:
              # ToolResultMessage
              for block in getattr(msg, "content", []):
                  if isinstance(block, TextContent) or hasattr(block, "text"):
                      total_chars += len(block.text)

      char_estimate = total_chars / _CHARS_PER_TOKEN
      if scale_factor is not None:
          return int(char_estimate * scale_factor)
      return int(char_estimate)
  ```

- [ ] **Step 4: Run tests.**

  Run: `uv run pytest tests/unit/middleware/compaction/test_tokens.py -v`
  Expected: 4 PASSED.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/cubeplex/middleware/compaction/tokens.py \
          backend/tests/unit/middleware/compaction/test_tokens.py
  git commit -m "refactor(compaction): native cubepi approx_tokens, drop langchain_core dep"
  ```

### Task 2.3: Rewrite `summarizer.py` to use `cubepi.Provider`

**Files:**
- Modify: `backend/cubeplex/middleware/compaction/summarizer.py`
- Test: existing summarizer tests, if any (`backend/tests/unit/middleware/compaction/test_summarizer*.py`); otherwise add a small unit test.

- [ ] **Step 1: Check whether summarizer has its own unit tests.**

  Run: `ls backend/tests/unit/middleware/compaction/ 2>&1`
  Expected: lists `test_boundary.py`, `test_tokens.py`, and possibly `test_compaction.py`. If `test_summarizer.py` exists, read it first to understand the interface contract being tested.

- [ ] **Step 2: Write a focused test for the cubepi-based summarizer.**

  Create `backend/tests/unit/middleware/compaction/test_summarizer.py` (or extend existing):

  ```python
  """Unit tests for summarize() — uses cubepi.Provider one-shot call."""

  from __future__ import annotations

  from typing import Any
  from unittest.mock import AsyncMock

  import pytest
  from cubepi.providers.base import (
      AssistantMessage,
      MessageUsage,
      TextContent,
      UserMessage,
  )

  from cubeplex.middleware.compaction.summarizer import (
      CompactionSummary,
      summarize,
  )


  class _FakeProvider:
      """Minimal cubepi.Provider stand-in that returns a fixed assistant reply."""

      def __init__(self, reply_text: str) -> None:
          self.reply_text = reply_text
          self.calls: list[dict[str, Any]] = []

      async def generate_once(
          self,
          *,
          system: str,
          messages: list,
          max_output_tokens: int,
      ) -> str:
          self.calls.append(
              {"system": system, "messages": messages, "max_output_tokens": max_output_tokens}
          )
          return self.reply_text


  @pytest.mark.asyncio
  async def test_creates_new_summary_from_messages() -> None:
      provider = _FakeProvider("Compressed summary of the chat.")
      msgs = [
          UserMessage(content=[TextContent(text="hello")], id="u1"),
          AssistantMessage(content=[TextContent(text="hi there")], id="a1"),
      ]
      result = await summarize(
          provider=provider,
          messages_to_summarize=msgs,
          existing=None,
          max_summary_tokens=512,
      )
      assert isinstance(result, CompactionSummary)
      assert result.summary == "Compressed summary of the chat."
      assert result.summarized_message_ids == ["u1", "a1"]
      assert result.last_summarized_message_id == "a1"
      assert provider.calls[0]["max_output_tokens"] == 512


  @pytest.mark.asyncio
  async def test_merges_with_existing_summary() -> None:
      provider = _FakeProvider("Merged summary.")
      existing = CompactionSummary(
          summary="Older context.",
          summarized_message_ids=["u0"],
          last_summarized_message_id="u0",
      )
      msgs = [UserMessage(content=[TextContent(text="newer")], id="u1")]
      result = await summarize(
          provider=provider, messages_to_summarize=msgs, existing=existing
      )
      # System prompt was extended with EXISTING_SUMMARY_SUFFIX containing prior text
      assert "Older context." in provider.calls[0]["system"]
      assert result.summarized_message_ids == ["u0", "u1"]
      assert result.last_summarized_message_id == "u1"
  ```

  Note: this test assumes `summarize()`'s new signature takes a `provider` kwarg with a `generate_once(...)` async method. That method is the cubepi-side contract we'll define next. If `cubepi.Provider` doesn't already have an equivalent one-shot helper, the summarizer module itself will own a tiny adapter that wraps `provider.stream(...)`. The test stays valid either way; the assertions check the public surface.

- [ ] **Step 3: Check what one-shot API cubepi.Provider exposes.**

  Run: `grep -n "async def" /home/chris/cubepi/cubepi/providers/base.py | head -20`
  Expected: lists methods like `stream`, `complete`, or similar. Use whichever one performs a single non-streaming completion.

  If no single-call helper exists, the summarizer wraps `provider.stream(...)` and accumulates `TextDelta` events into a string before returning. Document this choice inline.

- [ ] **Step 4: Rewrite `summarizer.py`.**

  Replace `backend/cubeplex/middleware/compaction/summarizer.py` contents:

  ```python
  """Summarizer — runs a cheap cubepi Provider to produce / update a CompactionSummary."""

  from __future__ import annotations

  from dataclasses import dataclass, field
  from typing import Any, Protocol

  from cubepi.providers.base import (
      AssistantMessage,
      Message,
      TextContent,
      ToolCall,
      UserMessage,
  )


  @dataclass
  class CompactionSummary:
      """Persisted running summary of a conversation's older turns.

      Stored on cubepi ``ctx.extra["compaction"]`` between turns. Three-field
      shape mirrors the canonical running-summary pattern: the text, which
      messages it covers, and where the rolling window currently ends.
      """

      summary: str
      summarized_message_ids: list[str] = field(default_factory=list)
      last_summarized_message_id: str | None = None


  SUMMARIZER_SYSTEM_PROMPT = """\
  You compress a chat transcript into a brief, faithful narrative for an AI assistant
  that is continuing the conversation. Rules:

  1. Preserve facts, user goals, decisions made, and unresolved questions.
  2. Preserve every 【N-K】 citation marker verbatim. Do not renumber, merge, or drop them.
  3. Do not quote long tool outputs. Reference them by their citation markers instead.
  4. Keep the language of the original conversation.
  5. Output the summary directly. No preamble, no JSON, no markdown headers.
  """

  EXISTING_SUMMARY_SUFFIX = """\
  A previous summary already covers earlier turns:

  <previous_summary>
  {prev}
  </previous_summary>

  Merge it with the new turns below. Output the updated summary."""


  class _OneShotProvider(Protocol):
      """Subset of cubepi.Provider used by the summarizer.

      The middleware passes the real provider; the summarizer only needs the
      single-shot generate path. A test fake implementing this Protocol is
      sufficient for unit tests.
      """

      async def generate_once(
          self,
          *,
          system: str,
          messages: list[Message],
          max_output_tokens: int,
      ) -> str: ...


  def _format_message_for_summary(msg: Message) -> str:
      role = msg.__class__.__name__.removesuffix("Message").lower() or "msg"
      parts: list[str] = []
      for block in getattr(msg, "content", []):
          if isinstance(block, TextContent):
              parts.append(block.text)
          elif isinstance(block, ToolCall):
              parts.append(f"[tool_call:{block.name}]")
          elif hasattr(block, "text"):
              parts.append(block.text)
      return f"[{role}] " + " ".join(parts)


  def _format_transcript(messages: list[Message]) -> str:
      return "\n\n".join(_format_message_for_summary(m) for m in messages)


  async def summarize(
      *,
      provider: _OneShotProvider,
      messages_to_summarize: list[Message],
      existing: CompactionSummary | None,
      max_summary_tokens: int = 1024,
  ) -> CompactionSummary:
      """Generate or update a CompactionSummary covering messages_to_summarize."""
      system_text = SUMMARIZER_SYSTEM_PROMPT
      if existing and existing.summary:
          system_text = system_text + "\n\n" + EXISTING_SUMMARY_SUFFIX.format(prev=existing.summary)

      transcript = _format_transcript(messages_to_summarize)
      prompt = [UserMessage(content=[TextContent(text=transcript)])]

      text = await provider.generate_once(
          system=system_text,
          messages=prompt,
          max_output_tokens=max_summary_tokens,
      )

      new_ids = [getattr(m, "id", None) or "" for m in messages_to_summarize]
      new_ids = [i for i in new_ids if i]
      prior_ids = list(existing.summarized_message_ids) if existing else []

      return CompactionSummary(
          summary=text.strip(),
          summarized_message_ids=prior_ids + new_ids,
          last_summarized_message_id=(
              new_ids[-1] if new_ids else (existing.last_summarized_message_id if existing else None)
          ),
      )
  ```

- [ ] **Step 5: Run summarizer tests.**

  Run: `uv run pytest tests/unit/middleware/compaction/test_summarizer.py -v`
  Expected: 2 PASSED.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/cubeplex/middleware/compaction/summarizer.py \
          backend/tests/unit/middleware/compaction/test_summarizer.py
  git commit -m "refactor(compaction): summarizer uses cubepi Provider, drop BaseChatModel"
  ```

### Task 2.4: Add `generate_once` helper for cubepi Provider in cubeplex

If cubepi.Provider doesn't expose a one-shot generate method, add a tiny adapter in cubeplex so the summarizer can call it uniformly.

**Files:**
- Create or modify: `backend/cubeplex/llm/oneshot.py` (new) OR add helper method on the wrapper used by `LLMFactory.build_cubepi_provider`.

- [ ] **Step 1: Check cubepi.Provider's existing single-call surface.**

  Run: `grep -nE "def (stream|complete|generate|invoke)" /home/chris/cubepi/cubepi/providers/base.py /home/chris/cubepi/cubepi/providers/anthropic.py /home/chris/cubepi/cubepi/providers/openai.py 2>&1 | head -30`
  Expected: shows what method(s) the provider offers. Most likely there's a `stream(...) -> AsyncIterator[Event]` method.

- [ ] **Step 2: Create the helper that wraps `provider.stream()` into a single string.**

  If cubepi has no built-in one-shot helper, create `backend/cubeplex/llm/oneshot.py`:

  ```python
  """One-shot text generation adapter over cubepi.Provider.

  cubepi providers are stream-first; the compaction summarizer wants a single
  prompt → single text reply. This module accumulates text deltas into a
  string and returns it. Tool calls / structured responses are ignored —
  the summarizer prompt never invites tool use.
  """

  from __future__ import annotations

  from cubepi.providers.base import Message, Provider, TextContent


  async def generate_once(
      provider: Provider,
      *,
      system: str,
      messages: list[Message],
      max_output_tokens: int,
  ) -> str:
      """Collect text deltas from a single non-tool-using completion."""
      chunks: list[str] = []
      async for event in provider.stream(
          system=system,
          messages=messages,
          tools=[],
          max_output_tokens=max_output_tokens,
      ):
          # cubepi event shape: events with `.delta` carry partial text.
          delta = getattr(event, "delta", None)
          if isinstance(delta, str):
              chunks.append(delta)
      return "".join(chunks)
  ```

  Note: adjust `event` field access to whatever cubepi's event type actually exposes (check `cubepi/providers/base.py` for the event dataclass). If `cubepi.Provider` already has a `complete()` or `generate_once()` method, skip creating this file and have the summarizer call that directly — then revise `_OneShotProvider` protocol in Task 2.3 to match.

- [ ] **Step 3: Wire `generate_once` into the summarizer call path.**

  In `cubeplex/middleware/compaction/__init__.py` (handled in Task 2.5), the middleware will pass a small wrapper:

  ```python
  from cubeplex.llm.oneshot import generate_once as _stream_oneshot

  class _ProviderOneShot:
      def __init__(self, provider): self._p = provider
      async def generate_once(self, *, system, messages, max_output_tokens):
          return await _stream_oneshot(self._p, system=system, messages=messages,
                                       max_output_tokens=max_output_tokens)
  ```

  (Or pass `provider` directly if it already implements `generate_once`. The summarizer's `_OneShotProvider` Protocol accepts either.)

- [ ] **Step 4: Smoke-test the helper against a real provider.**

  Run: `uv run pytest tests/e2e/ -k compaction -v` (will run after Task 2.5 wires it; for now just ensure no import errors).
  Expected: import succeeds.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/cubeplex/llm/oneshot.py
  git commit -m "feat(llm): add generate_once adapter over cubepi.Provider.stream"
  ```

### Task 2.5: Rewrite `compaction/__init__.py` — drop LC bridge, switch summarizer LLM type

**Files:**
- Modify: `backend/cubeplex/middleware/compaction/__init__.py`
- Modify: `backend/cubeplex/streams/run_manager.py` (constructor call)
- Test: existing `backend/tests/unit/test_compaction.py` (update fixtures) and E2E.

- [ ] **Step 1: Identify the current middleware constructor call site.**

  Run: `grep -nE "CompactionMiddleware\(" backend/cubeplex/streams/run_manager.py`
  Expected: shows the constructor invocation around line 758-770 with `summary_llm=`.

- [ ] **Step 2: Replace `compaction/__init__.py` to drop LC bridge.**

  Apply these specific edits to `backend/cubeplex/middleware/compaction/__init__.py`:

  a) Replace imports (lines 41-44) — delete the three `langchain_core.*` imports.

  b) Delete the entire `_to_langchain_messages()` function (lines 126-170).

  c) Delete the local `_cubepi_approx_tokens()` (lines 57-101). Import `approx_tokens` from `tokens.py` instead.

  d) Change the constructor type annotation:
     - `summary_llm: BaseChatModel` → `summary_llm: _SummaryLLM` (define `_SummaryLLM` Protocol matching `summarizer._OneShotProvider`, or just `Any` if simpler — but Protocol is preferred for mypy)
     - Equivalent: import `_OneShotProvider` from summarizer and re-use it.

  e) In `transform_context`, replace the two `_to_langchain_messages(messages)` calls (lines 250, 258) with direct `messages` and `messages[boundary:new_boundary]` — they are now native cubepi messages.

  f) In `transform_context`, replace `_cubepi_approx_tokens(compressed)` call (line 244) with `approx_tokens(compressed)` (imported from `.tokens`).

  g) In the call to `summarize(...)` (line 260), rename kwarg `model=self._summary_llm` → `provider=self._summary_llm`.

  Concrete updated imports block at top of `compaction/__init__.py`:

  ```python
  from __future__ import annotations

  from collections.abc import Callable
  from typing import Any, cast

  from cubepi.middleware.base import Middleware
  from cubepi.providers.base import Message
  from loguru import logger

  from cubeplex.middleware.compaction.boundary import safe_boundary
  from cubeplex.middleware.compaction.summarizer import (
      CompactionSummary,
      _OneShotProvider,
      summarize,
  )
  from cubeplex.middleware.compaction.tokens import approx_tokens
  ```

  Updated constructor signature:

  ```python
  def __init__(
      self,
      *,
      extra_ref: Callable[[], dict[str, Any]],
      summary_llm: _OneShotProvider,
      max_tokens_before_compact: int,
      keep_recent_messages: int = 8,
      max_summary_tokens: int = 1024,
      min_compact_messages: int = 4,
  ) -> None:
      self._extra_ref = extra_ref
      self._summary_llm = summary_llm
      self._max_tokens_before = max_tokens_before_compact
      self._keep_recent = keep_recent_messages
      self._max_summary_tokens = max_summary_tokens
      self._min_compact = min_compact_messages
  ```

  Updated `transform_context` body (replacing lines 219-282):

  ```python
  async def transform_context(
      self,
      messages: list[Message],
      *,
      signal: object = None,
  ) -> list[Message]:
      del signal

      extra = self._extra_ref()
      summary = cast("CompactionSummary | None", extra.get("compaction"))
      boundary = cast("int | None", extra.get("compaction_until_msg_index")) or 0

      compressed = _compressed_view(messages, summary, boundary)

      if approx_tokens(compressed) < self._max_tokens_before:
          return compressed

      boundary = boundary or 0
      new_boundary = safe_boundary(
          messages,
          keep_recent=self._keep_recent,
          min_compact=max(self._min_compact, boundary + 1),
      )
      if new_boundary is None or new_boundary <= boundary:
          return compressed

      to_summarize = messages[boundary:new_boundary]
      try:
          new_summary = await summarize(
              provider=self._summary_llm,
              messages_to_summarize=to_summarize,
              existing=summary,
              max_summary_tokens=self._max_summary_tokens,
          )
      except Exception as exc:  # noqa: BLE001
          logger.warning("CompactionMiddleware: summarizer failed, skipping: {}", exc)
          return compressed

      logger.info(
          "CompactionMiddleware: compacted msgs[{}:{}] ({} msgs)",
          boundary,
          new_boundary,
          len(to_summarize),
      )

      extra["compaction"] = new_summary
      extra["compaction_until_msg_index"] = new_boundary

      return _compressed_view(messages, new_summary, new_boundary)
  ```

  `_compressed_view` stays unchanged (already cubepi-native).

- [ ] **Step 3: Update `run_manager.py` to pass a cubepi-based summary LLM.**

  In `backend/cubeplex/streams/run_manager.py` around line 755-770 (where the summary LLM is currently built via the langchain factory):

  Before:
  ```python
  _comp_factory = _CompLLMFactory()
  _summary_llm = _comp_factory.create(
      _summary_model,
      provider_name=_summary_provider,
      max_tokens=1024,
  )
  ```

  After:
  ```python
  from cubeplex.llm.oneshot import generate_once

  class _ProviderOneShot:
      def __init__(self, provider: Any) -> None:
          self._p = provider
      async def generate_once(self, *, system, messages, max_output_tokens):
          return await generate_once(
              self._p, system=system, messages=messages,
              max_output_tokens=max_output_tokens,
          )

  _summary_provider_inst = factory.build_cubepi_provider(
      factory.llm_config.providers[_summary_provider],
      model_id=_summary_model,
      cache_policy=None,
  )
  _summary_llm = _ProviderOneShot(_summary_provider_inst)
  ```

  (Adjust the exact `build_cubepi_provider` signature to match what `LLMFactory` currently exposes; see `backend/tests/unit/test_llm_factory_cubepi.py` for the calling convention.)

  Confirm by reading `run_manager.py:506-770` to see the exact factory/config wiring in context.

- [ ] **Step 4: Update existing compaction tests.**

  Read `backend/tests/unit/test_compaction.py`. Replace fixtures that build a fake `BaseChatModel` with a fake matching the `_OneShotProvider` Protocol (a class with an async `generate_once` method that returns a fixed string).

  Replace any `from langchain_core.messages import ...` with `from cubepi.providers.base import ...`.

- [ ] **Step 5: Run all compaction tests.**

  Run: `uv run pytest tests/unit/middleware/compaction/ tests/unit/test_compaction.py -v`
  Expected: PASS.

- [ ] **Step 6: Run the compaction E2E.**

  Run: `uv run pytest tests/e2e/ -k compaction -v`
  Expected: PASS. (If a configured CUBEPLEX_E2E_LLM_* is required and not set, document the skip — don't paper over the missing env.)

- [ ] **Step 7: Commit.**

  ```bash
  git add backend/cubeplex/middleware/compaction/__init__.py \
          backend/cubeplex/streams/run_manager.py \
          backend/tests/unit/test_compaction.py
  git commit -m "refactor(compaction): drop LC bridge; summary LLM via cubepi provider"
  ```

---

## Phase 3 — Delete `LLMFactory.create()` and remaining LangChain code in factory

After Phase 2, the only thing keeping LangChain in `llm/factory.py` is `LLMFactory.create()` for the (now removed) langchain summarizer path. Delete it.

**Files:**
- Modify: `backend/cubeplex/llm/factory.py`
- Delete: `backend/tests/unit/llm/test_factory_anthropic.py`
- Modify: `backend/tests/e2e/test_admin_providers_crud.py:177` (if it calls `.create()`)

### Task 3.1: Remove `LLMFactory.create()` and langchain imports

- [ ] **Step 1: Confirm no production code still calls `LLMFactory.create()` after Phase 2.**

  Run: `git grep -nE "factory\.create\(|LLMFactory\(\)\.create" backend/cubeplex/`
  Expected: empty after Phase 2 commits (run_manager.py no longer calls it).

  If anything remains, fix it before proceeding — `create()` is about to die.

- [ ] **Step 2: Delete `LLMFactory.create()` and dependencies.**

  In `backend/cubeplex/llm/factory.py`:

  - Delete `from langchain_openai import ChatOpenAI` (line 22).
  - Delete the entire `def create(self, ...)` method (lines ~354 through ~498 — find the next method definition to know where to stop).
  - Delete any private helpers used only by `create()` (`_wrap_with_cache_markers`, etc., if present).
  - Update the module docstring (lines 6-13) — remove the "Once compaction's summary LLM goes through cubepi this method can be deleted" sentence; describe what's left.

  Final factory module top should look like:

  ```python
  """LLM Factory

  Creates cubepi.Provider instances for the agent runtime.
  All langchain code paths were removed after the cubepi migration (M6).

  Surface:
  - ``resolve_default_provider_and_config`` — resolves the active provider/model
  - ``build_cubepi_provider`` — constructs a ``cubepi.Provider`` for the agent loop
  """

  import logging
  from typing import TYPE_CHECKING, Any

  if TYPE_CHECKING:
      from cubepi.providers.anthropic import CacheMarkerPolicy

  from sqlalchemy import func, select
  from sqlalchemy.ext.asyncio import AsyncSession

  from cubeplex.config import config
  from cubeplex.credentials.encryption import EncryptionBackend
  from cubeplex.llm.config import LLMConfig, ModelConfig, ProviderConfig
  ```

- [ ] **Step 3: Delete `tests/unit/llm/test_factory_anthropic.py`.**

  ```bash
  git rm backend/tests/unit/llm/test_factory_anthropic.py
  ```

  Rationale: this file tests langchain ChatAnthropic kwargs construction — the entire codepath is gone. The cubepi-equivalent tests live in `tests/unit/test_llm_factory_cubepi.py`.

- [ ] **Step 4: Audit `tests/e2e/test_admin_providers_crud.py:177`.**

  Run: `sed -n '170,185p' backend/tests/e2e/test_admin_providers_crud.py`
  Expected: shows `factory = LLMFactory()` and what it's used for.

  If it calls `.create()`, switch to `.build_cubepi_provider()` or remove that assertion if it's redundant with cubepi-side coverage. If it only uses `resolve_default_provider_and_config`, no change needed.

- [ ] **Step 5: Run full test suite.**

  Run from `backend/`: `make check`
  Expected: PASS.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/cubeplex/llm/factory.py \
          backend/tests/unit/llm/ \
          backend/tests/e2e/test_admin_providers_crud.py
  git commit -m "refactor(llm): delete LLMFactory.create() langchain path"
  ```

---

## Phase 4 — Replace `ProviderService` connection-test with cubepi

`services/provider_service.py` uses `ChatOpenAI` / `ChatAnthropic` + `HumanMessage.content="ping"` for the admin "test connection" UI. Replace with a minimal cubepi.Provider call.

**Files:**
- Modify: `backend/cubeplex/services/provider_service.py`
- Test: existing provider tests (`backend/tests/unit/services/test_provider_service*.py` if any) and E2E `backend/tests/e2e/test_admin_providers_crud.py`.

### Task 4.1: Switch test-connection logic to cubepi

- [ ] **Step 1: Locate exact lines to change.**

  Run: `sed -n '280,360p' backend/cubeplex/services/provider_service.py`
  Expected: shows the test_connection method body using `ChatOpenAI` / `ChatAnthropic` and `llm.ainvoke([HumanMessage(content="ping")])`.

- [ ] **Step 2: Rewrite using `build_cubepi_provider` + `generate_once`.**

  In `backend/cubeplex/services/provider_service.py`:

  - Remove imports at lines 7-8: `from langchain_core.messages import HumanMessage` and `from langchain_openai import ChatOpenAI`.
  - Remove the deferred `from langchain_anthropic import ChatAnthropic` at line 324.
  - Replace the entire test-connection LLM construction + ainvoke block with:

  ```python
  from cubeplex.llm.factory import LLMFactory
  from cubeplex.llm.oneshot import generate_once
  from cubepi.providers.base import TextContent, UserMessage

  # Build a transient provider config matching the request inputs.
  test_provider_config = self._build_test_provider_config(request)  # extract existing logic
  factory = LLMFactory(llm_config=...)  # supply minimal LLMConfig wrapping test_provider_config
  provider = factory.build_cubepi_provider(test_provider_config, cache_policy=None)

  start = time.monotonic()
  try:
      reply = await generate_once(
          provider,
          system="",
          messages=[UserMessage(content=[TextContent(text="ping")])],
          max_output_tokens=8,
      )
  except Exception as exc:
      return TestResultOut(ok=False, error=str(exc), latency_ms=int((time.monotonic() - start) * 1000))

  return TestResultOut(
      ok=True,
      latency_ms=int((time.monotonic() - start) * 1000),
      sample_output=reply[:200],
  )
  ```

  Exact field names depend on `TestResultOut` schema — check `backend/cubeplex/api/schemas/provider.py` for the actual shape and adapt assignments to match.

- [ ] **Step 3: Run unit + E2E tests for ProviderService.**

  Run: `uv run pytest tests/unit/services/ -k provider -v`
  Expected: PASS.

  Run: `uv run pytest tests/e2e/test_admin_providers_crud.py -v`
  Expected: PASS. Test connection actually hits the configured E2E LLM endpoint, so this validates the cubepi path end-to-end.

- [ ] **Step 4: Commit.**

  ```bash
  git add backend/cubeplex/services/provider_service.py
  git commit -m "refactor(provider): cubepi-based connection test, drop langchain"
  ```

---

## Phase 5 — Drop final stray langchain reference in `citations/config.py`

**Files:**
- Modify: `backend/cubeplex/middleware/citations/config.py`

### Task 5.1: Replace `BaseTool` type annotation

- [ ] **Step 1: Read the file to see context.**

  Run: `sed -n '1,40p' backend/cubeplex/middleware/citations/config.py`
  Expected: shows that line 10's `BaseTool` import is used as a type annotation only (e.g., in a function signature or dataclass field).

- [ ] **Step 2: Replace with cubepi's tool type or drop annotation.**

  - If `BaseTool` is used as the annotation of a tool list, replace with `from cubepi.agent.types import AgentTool` and annotate `list[AgentTool[Any]]`.
  - If the annotation is structural (e.g., `Protocol[name, description]`), define a tiny local Protocol or use `Any`.

  Apply the minimal edit needed and remove the `from langchain_core.tools import BaseTool` line.

- [ ] **Step 3: Run tests touching citations.**

  Run: `uv run pytest tests/ -k citation -v`
  Expected: PASS.

- [ ] **Step 4: Commit.**

  ```bash
  git add backend/cubeplex/middleware/citations/config.py
  git commit -m "refactor(citations): drop BaseTool type annotation"
  ```

---

## Phase 6 — Remove LangSmith plumbing entirely

User decision: completely delete LangSmith config + env vars. cubepi doesn't use LangSmith; future observability will be wired through a different surface.

**Files:**
- Modify: `backend/cubeplex/config.py`
- Modify: `backend/config.yaml`
- Modify: `backend/config.test.yaml`
- Modify: `backend/.env.example`
- Modify: `CLAUDE.md` (root)
- Modify: `backend/CLAUDE.md` (if mentions LangSmith)

### Task 6.1: Delete config.langsmith + env var bridge

- [ ] **Step 1: Remove env-var bridge from `config.py`.**

  In `backend/cubeplex/config.py` lines 43-45:

  Delete:
  ```python
  if config.langsmith.enabled:
      os.environ["LANGSMITH_TRACING"] = "true"
      os.environ["LANGCHAIN_API_KEY"] = config.langsmith.key
  ```

  Also delete the `LangSmithConfig` class definition (search for it: `grep -n "LangSmithConfig\|langsmith" backend/cubeplex/config.py`) and remove the `langsmith: LangSmithConfig` field on the root Config class.

- [ ] **Step 2: Remove the YAML blocks.**

  In `backend/config.yaml`, delete the `langsmith:` block (lines ~156-158). In `backend/config.test.yaml`, delete the `langsmith:` block (lines ~75-77 — including the `# LangSmith: e2e 不上报` comment).

- [ ] **Step 3: Remove from `.env.example`.**

  In `backend/.env.example`, delete any `CUBEPLEX_LANGSMITH__*` lines.

- [ ] **Step 4: Remove from root `CLAUDE.md`.**

  In `/home/chris/cubeplex/CLAUDE.md`, delete the bullet listing `CUBEPLEX_LANGSMITH__KEY` under environment variables (around line 36).

- [ ] **Step 5: Run full test suite + boot smoke.**

  Run from `backend/`: `make check`
  Expected: PASS (config loads without the deleted fields).

  Run: `uv run python -c "from cubeplex.config import config; print('ok')"`
  Expected: prints `ok`. (If `pydantic` complains about extra fields, ensure the model_config in Config has `extra="ignore"` or the field is properly deleted from defaults.)

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/cubeplex/config.py backend/config.yaml backend/config.test.yaml \
          backend/.env.example CLAUDE.md
  git commit -m "chore(config): remove LangSmith plumbing"
  ```

---

## Phase 7 — Clean stale archaeology comments

Many production files carry comments like "mirroring the langgraph version" or "kept byte-identical to the langgraph". Most have outlived their usefulness; a few stay because they document non-obvious semantics.

### Task 7.1: Delete stale "compared to langgraph" comments

**Files** (apply minimal edit per file — delete only the langgraph/langchain reference; do not reflow surrounding text):

- [ ] `backend/cubeplex/tools/__init__.py:3` — the module-docstring sentence about "langgraph ToolRegistry was removed in M6".
- [ ] `backend/cubeplex/tools/builtin/memory.py:4,7` — sentences mentioning "the langchain list" / "kept byte-identical to the langchain originals".
- [ ] `backend/cubeplex/middleware/attachments.py:8` — "mirroring the langgraph version which walks all HumanMessages".
- [ ] `backend/cubeplex/middleware/skills.py:21` — "Format mirrors the langgraph ``SkillsMiddleware``::".
- [ ] `backend/cubeplex/middleware/subagents.py:143` — comment about BaseTool (langgraph) vs AgentTool (cubepi); after Phase 1-3 this should already be all cubepi types, so the comment is misleading — delete.
- [ ] `backend/cubeplex/middleware/citation.py:38` — "the langchain helper of the same name in ``citations/middleware.py``".
- [ ] `backend/cubeplex/middleware/artifacts.py:9,35,57` — "what ``ArtifactMiddleware.awrap_model_call`` does in the langgraph path" and "kept byte-identical to the langgraph version" comments.
- [ ] `backend/cubeplex/middleware/todo.py:37` — "colocated with the langgraph TodoListMiddleware".
- [ ] `backend/cubeplex/middleware/sandbox.py:9,91` — same pattern.
- [ ] `backend/cubeplex/middleware/memory.py:167` — `# "current" in the snapshot XML tag (matches langgraph behaviour).` — KEEP only the part "current in the snapshot XML tag"; drop the langgraph reference.
- [ ] `backend/cubeplex/agents/graph.py:47` — "byte-parity with the langgraph path".
- [ ] `backend/cubeplex/agents/checkpointer.py:8` — `runtime == "langgraph", cubeplex/agents/checkpointer.py (the existing` — this is stale module-docstring text; delete the whole branch about the langgraph version since checkpointer.py is now solely the cubepi one.
- [ ] `backend/cubeplex/mcp/cubepi_runtime.py:31` — "Mirrors the langchain runtime.py behavior" — langchain runtime is gone, this comment is misleading. Delete.
- [ ] `backend/cubeplex/mcp/cubepi_discovery.py:55,68,131` — similar "mirrors the langchain runtime" / "langgraph runtime.py" references. Delete the comparative phrasing; keep the substantive explanation of credential mode resolution.
- [ ] `backend/cubeplex/streams/run_manager.py:524,528,533,545,570,621,897` — the cluster of "to match langgraph tool order" / "langgraph default" / "langgraph path" comments.
  - 533 is the PR #84 fallback TODO — KEEP that line; reword to drop "the langgraph path" since the alternative no longer exists. The TODO remains valid.
  - 524, 528: keep the substantive context (`ModelConfig has no temperature`) but drop the langgraph back-reference.
  - 545, 570, 621, 897: tool-order comments — the order is preserved for byte-parity with our own prior cache state, not "langgraph". Reword to that.

- [ ] **Run after edits:** `make check` from `backend/`
  Expected: PASS.

- [ ] **Commit.**

  ```bash
  git add backend/cubeplex/
  git commit -m "chore: drop stale 'compared to langgraph' archaeology comments"
  ```

### Task 7.2: Update CLAUDE.md references that still say "LangGraph"

- [ ] **Step 1: Find them.**

  Run: `grep -nEi "langgraph|langchain|langsmith" backend/CLAUDE.md CLAUDE.md 2>/dev/null`
  Expected: any matches need updating.

- [ ] **Step 2: Replace each match.**

  Walk the matches one by one. Common rewrites:
  - "LangGraph + langchain" → "cubepi"
  - "LangGraph checkpointer" → "cubepi PostgresCheckpointer"
  - "BaseChatModel" → "cubepi.Provider"
  - Any "langchain.agents.create_agent" or similar → corresponding cubepi.Agent reference

- [ ] **Step 3: Commit.**

  ```bash
  git add backend/CLAUDE.md CLAUDE.md
  git commit -m "docs(claude-md): scrub remaining langgraph/langchain references"
  ```

---

## Phase 8 — Drop PyPI dependencies + regenerate lockfile + scrub dev scripts

After Phase 1-7, the codebase imports zero `langchain*` / `langgraph*` / `langsmith` symbols. Remove them from `pyproject.toml` and clean dev artifacts.

### Task 8.1: Verify zero langchain imports remain

- [ ] **Step 1: Final grep.**

  Run: `git grep -nE "(^|[^a-zA-Z_])(langchain|langgraph|langsmith)[a-zA-Z_]*" backend/cubeplex/ backend/tests/`
  Expected: empty (no production-code matches). Comments/docstrings may have residual mentions — that's fine for now but ideally also clean.

  If anything matches, jump back to the relevant Phase and fix.

- [ ] **Step 2: Remove from `backend/pyproject.toml`.**

  In `backend/pyproject.toml` `dependencies` list, delete these four entries:
  - `"langchain-core>=1.2.17",`
  - `"langchain-openai>=1.1.10",`
  - `"langchain-mcp-adapters>=0.1.0",`
  - `"langchain-anthropic>=1.4.3",`

- [ ] **Step 3: Regenerate the lockfile.**

  Run from `backend/`: `uv lock`
  Expected: lockfile updates; `langchain-*` and transitive `langsmith` etc. entries disappear from `uv.lock`.

- [ ] **Step 4: Reinstall and smoke-test imports.**

  Run from `backend/`: `make dev-install`
  Expected: clean reinstall, no langchain wheels pulled.

  Run: `uv run python -c "import cubeplex; print('ok')"`
  Expected: `ok`. No `ModuleNotFoundError`.

- [ ] **Step 5: Run full check.**

  Run from `backend/`: `make check`
  Expected: PASS.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/pyproject.toml backend/uv.lock
  git commit -m "chore(deps): drop langchain-core/openai/anthropic/mcp-adapters"
  ```

### Task 8.2: Clean alembic and dev scripts

- [ ] **Step 1: Update `alembic/env.py`.**

  In `backend/alembic/env.py:60`, replace the comment "Tables managed by langgraph-checkpoint-postgres — exclude from autogenerate" with "Tables managed by cubepi PostgresCheckpointer — exclude from autogenerate".

- [ ] **Step 2: Delete obsolete dev scripts.**

  ```bash
  git rm backend/scripts/dev/test_langgraph_stream_mode.py \
         backend/scripts/dev/langgraph_tool_async_research.md
  ```

- [ ] **Step 3: Audit `scripts/dev/test_token_usage.py`.**

  Run: `grep -n "langchain" backend/scripts/dev/test_token_usage.py`
  Expected: shows `from langchain_core.messages import AIMessage, AIMessageChunk` and any usage.

  Options: (a) delete the file if it's stale dev scratch (most likely); (b) rewrite using cubepi types. Choose (a) unless it's actively referenced from docs.

  Run: `git grep -nE "test_token_usage" backend/`
  Expected: only the file itself. If empty: `git rm backend/scripts/dev/test_token_usage.py`.

- [ ] **Step 4: Audit `tests/diagnostic/_capture.py` and `test_raw_*_cache.py`.**

  Run: `grep -n "langchain" backend/tests/diagnostic/*.py`
  Expected: shows any remaining imports.

  These are diagnostic tests outside the normal test path (excluded via `make check-ci`). Decide per-file:
  - If purely historical (already xfailed or documenting a closed issue) → delete.
  - If they exercise raw HTTP behavior independently of langchain → strip the langchain import and any LC-typed assertions; keep the HTTP capture logic.

- [ ] **Step 5: Final exhaustive grep.**

  Run: `git grep -nEi "langchain|langgraph|langsmith"`
  Expected: matches only in:
  - `docs/superpowers/plans/` (historical plans — handled in Phase 9)
  - `docs/superpowers/specs/` (historical specs — handled in Phase 9)
  - `docs/superpowers/notes/` if any
  - Any remaining intentional comment mentioning "M6 migrated off langgraph" as a one-line history note

  Anything else: fix it.

- [ ] **Step 6: Run full check one more time.**

  Run from `backend/`: `make check`
  Expected: PASS.

- [ ] **Step 7: Commit.**

  ```bash
  git add backend/alembic/env.py backend/scripts/dev/ backend/tests/diagnostic/
  git commit -m "chore: clean dev scripts and alembic comment of langgraph refs"
  ```

---

## Phase 9 — Mark archival specs/plans superseded

### Task 9.1: Add superseded headers to historical migration docs

**Files:**
- Modify: `docs/superpowers/specs/2026-03-31-langgraph-migration-design.md`
- Modify: `docs/superpowers/plans/2026-03-31-langgraph-migration.md`
- Modify: `docs/superpowers/plans/2026-05-14-cubepi-migration-m6-cleanup.md`

- [ ] **Step 1: Add a 1-line superseded banner at the top of each.**

  For `docs/superpowers/specs/2026-03-31-langgraph-migration-design.md` and `docs/superpowers/plans/2026-03-31-langgraph-migration.md`, prepend:

  ```markdown
  > **SUPERSEDED (2026-05-14):** The LangGraph runtime described here was fully replaced by cubepi. See `docs/superpowers/specs/2026-05-13-cubepi-main-agent-migration-design.md` and the M0–M6 cubepi-migration plans for the current state.
  ```

  For `docs/superpowers/plans/2026-05-14-cubepi-migration-m6-cleanup.md`, prepend:

  ```markdown
  > **Follow-up:** M6.5 and M6.7 were finished in `docs/superpowers/plans/2026-05-14-cubepi-cleanup-followup.md`. The M6.7 commit in the original plan only dropped the umbrella `langchain` / `langgraph` packages; the sub-packages (`langchain-core`, `langchain-openai`, `langchain-anthropic`, `langchain-mcp-adapters`) were dropped in the follow-up. M6.5 also left `cubeplex/mcp/runtime.py` and `discovery.py` in place; the follow-up replaced them with `cubepi_admin_discovery.py` / `cubepi_admin_refresh.py`.
  ```

- [ ] **Step 2: Commit.**

  ```bash
  git add docs/superpowers/
  git commit -m "docs: mark langgraph-migration spec/plan superseded; link follow-up"
  ```

---

## Final verification

- [ ] **Step 1: Run the full check from `backend/`.**

  Run: `make check`
  Expected: PASS (format, lint, type-check, full test).

- [ ] **Step 2: Boot the dev server and run a smoke conversation.**

  Run from `backend/`: `python main.py` in one terminal.
  In another terminal: `curl http://localhost:<port>/api/v1/healthz` (port from `.worktree.env` if in a worktree, else 8000).
  Expected: 200 OK.

  Then from the frontend (`pnpm dev` in `frontend/`), open the UI, send a one-turn message in a workspace, confirm SSE events stream through cleanly. Confirm tool calls work (calculator, memory, MCP if a server is configured).

- [ ] **Step 3: Run E2E suite.**

  Run from `backend/`: `uv run pytest tests/e2e/ -v`
  Expected: PASS (assuming local `.env` + `config.development.local.yaml` are present, as documented in `backend/CLAUDE.md`).

- [ ] **Step 4: Final grep for any survivors.**

  Run from repo root: `git grep -nEi "langchain|langgraph|langsmith" -- ':!docs/superpowers/' ':!*.lock'`
  Expected: empty, or only matches inside superseded-banner blocks in archival docs.

- [ ] **Step 5: Push and open PR.**

  ```bash
  git push -u origin <branch-name>
  gh pr create --title "Cleanup: remove last langchain/langgraph residue" \
    --body "$(cat <<'EOF'
  ## Summary
  - Replaces admin/OAuth MCP path with `cubepi_admin_discovery` + `cubepi_admin_refresh` (closes M6.5)
  - Moves `CompactionMiddleware` fully to cubepi message types; summarizer uses `cubepi.Provider`
  - Deletes `LLMFactory.create()` langchain path and `ProviderService` langchain test-connection
  - Removes LangSmith plumbing
  - Drops `langchain-core`, `langchain-openai`, `langchain-anthropic`, `langchain-mcp-adapters` from `pyproject.toml`
  - Scrubs stale "compared to langgraph" comments and dev scripts
  - Marks historical migration docs superseded

  ## Test plan
  - [ ] `make check` passes in `backend/`
  - [ ] Dev server boots, one-turn conversation streams correctly
  - [ ] E2E suite passes (incl. compaction, MCP, providers)
  - [ ] `git grep -nEi "langchain|langgraph|langsmith"` returns only archival-doc matches
  EOF
  )"
  ```

---

## Estimated effort

| Phase | Time | Risk |
|---|---|---|
| 1 — MCP cutover | 1 day | medium (admin/OAuth flow has subtle credential resolution) |
| 2 — Compaction migration | ~1 day | medium (token estimator + boundary logic must preserve behavior) |
| 3 — Factory cleanup | 1–2 hr | low |
| 4 — ProviderService | 2–3 hr | low |
| 5 — Citations annotation | 15 min | trivial |
| 6 — LangSmith removal | 30 min | low |
| 7 — Comment scrub | 1 hr | trivial |
| 8 — Deps + dev scripts | 1 hr | low |
| 9 — Docs | 15 min | trivial |
| Final verification | 1 hr | — |

Total ≈ 2.5–3 focused days. Each task is a single commit; each phase is a PR boundary if you want smaller PRs, otherwise one PR per several phases is reasonable.

## Rollback strategy

- Each task is atomic — `git revert <sha>` restores prior behavior.
- Phases 1, 2, 4, 6, 8 each carry the most risk; if any one regresses production, revert that phase's commits and the downstream phases that depended on it.
- Phase 8 (drop deps) cannot be reverted independently of Phases 1–5 — those phases removed the consumers. Roll back to before Phase 1 if a full retreat is needed.

## Out of scope

- The `streams/run_manager.py:533` fallback-chain TODO (PR #84 review). Unrelated feature work; tracked separately.
- Replacing LangSmith with a new tracing system. Deletion only; observability re-introduction is a future spec.
- cubepi PyPI release (cubepi remains a path/git dep).
- Refactoring cubepi-side APIs even if a cleaner interface would simplify the cubeplex call site — keep cubepi-side changes in cubepi's own repo.
