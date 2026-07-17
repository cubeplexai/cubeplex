"""Unit tests for the tools_cache fast path in cubepi_runtime.

The eager MCP loader now builds AgentTools from the install's persisted
``tools_cache`` instead of a live ``initialize`` + ``tools/list`` round
trip per send. These tests protect the two invariants that matter:

1. **Schema parity** — a cache-built tool must expose the same name,
   description, and parameter schema the live loader would produce from
   the same descriptor, or the serialized tool payload sent to the LLM
   (and therefore the prompt-cache prefix) would drift between a
   cache-hit send and a live-load send.
2. **Fallback** — an empty/unusable cache returns None so the caller
   falls back to live discovery instead of silently dropping a server.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from cubeplex.mcp.cubepi_runtime import _build_tools_from_cache, _make_refresh_auth_callback
from cubeplex.mcp.effective import MCPRuntimeConnectorSpec

_WEATHER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "description": "City name"},
        "days": {"type": "integer"},
    },
    "required": ["city"],
}


def _make_spec(tools_cache: list[dict[str, Any]]) -> MCPRuntimeConnectorSpec:
    return MCPRuntimeConnectorSpec(
        connector_id="mcins_cache",
        name="weather",
        server_url="https://mcp.example.com/mcp",
        transport="streamable_http",
        auth_method="static",
        grant_scope="org",
        credential_id="cred_test",
        refresh_credential_id=None,
        tool_citations={},
        tools_cache=tools_cache,
    )


def test_cache_built_tool_matches_live_loader_shape() -> None:
    """If the cache-built tool schema drifted from what the live loader
    produces for the same descriptor, the LLM tool payload (and the
    prompt-cache prefix) would differ between cache-hit and live sends."""
    from cubepi.mcp._adapter import make_mcp_agent_tool

    entry = {
        "name": "get_weather",
        "description": "Look up the weather",
        "input_schema": _WEATHER_SCHEMA,
        "output_schema": None,
    }
    spec = _make_spec([entry])

    cached_tools = _build_tools_from_cache(
        spec=spec, headers={"Authorization": "Bearer t"}, server_url=spec.server_url
    )
    assert cached_tools is not None and len(cached_tools) == 1
    cached = cached_tools[0]

    async def _noop_call_remote(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [], "isError": False}

    live = make_mcp_agent_tool(
        name="get_weather",
        description="Look up the weather",
        input_schema=_WEATHER_SCHEMA,
        call_remote=_noop_call_remote,
    )

    assert cached.name == live.name
    assert cached.description == live.description
    assert cached.parameters.model_json_schema() == live.parameters.model_json_schema()


def test_cache_entry_without_schema_gets_empty_object_schema() -> None:
    spec = _make_spec([{"name": "ping", "description": None, "input_schema": None}])
    tools = _build_tools_from_cache(spec=spec, headers={}, server_url=spec.server_url)
    assert tools is not None and len(tools) == 1
    assert tools[0].name == "ping"
    assert tools[0].description == ""


def test_empty_cache_returns_none_for_live_fallback() -> None:
    spec = _make_spec([])
    assert _build_tools_from_cache(spec=spec, headers={}, server_url=spec.server_url) is None


def test_nameless_entries_are_skipped_and_all_nameless_falls_back() -> None:
    spec = _make_spec([{"description": "no name"}, {"name": ""}])
    assert _build_tools_from_cache(spec=spec, headers={}, server_url=spec.server_url) is None


# ---------------------------------------------------------------------------
# 401 → forced-refresh → retry on tool calls (spec 2026-07-17).
# ---------------------------------------------------------------------------


def _unauthorized() -> BaseException:
    request = httpx.Request("POST", "https://mcp.example.com/mcp")
    response = httpx.Response(401, request=request)
    err = httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)
    return ExceptionGroup("unhandled errors in a TaskGroup", [err])


def _install_fake_session(monkeypatch: Any, opened_auth: list[str | None]) -> None:
    """Fake cubepi's ``_open_session``: 401 unless the fresh token is sent.

    Records the Authorization header of every session-open attempt.
    """

    @asynccontextmanager
    async def fake_open_session(server_url: str, *, headers=None, timeout=None, transport=None):
        auth = (headers or {}).get("Authorization")
        opened_auth.append(auth)
        if auth != "Bearer fresh":
            raise _unauthorized()

        class _Session:
            async def initialize(self) -> None:
                return None

            async def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
                return {"content": [{"type": "text", "text": "ok"}], "isError": False}

        yield _Session(), lambda: None

    monkeypatch.setattr("cubepi.mcp.http_loader._open_session", fake_open_session)
    monkeypatch.setattr("cubepi.mcp.http_loader._serialize_call_tool_response", lambda resp: resp)


_PING_CACHE = [{"name": "ping", "description": "", "input_schema": {"type": "object"}}]


async def test_tool_call_401_refreshes_and_retries(monkeypatch: Any) -> None:
    """A revoked-but-unexpired token must not fail the whole agent turn:
    the call refreshes once, retries with the new token, and later calls
    reuse the refreshed header without another refresh."""

    opened_auth: list[str | None] = []
    _install_fake_session(monkeypatch, opened_auth)
    refresh_calls = 0

    async def refresh_auth() -> str | None:
        nonlocal refresh_calls
        refresh_calls += 1
        return "fresh"

    spec = _make_spec(_PING_CACHE)
    headers = {"Authorization": "Bearer stale"}
    tools = _build_tools_from_cache(
        spec=spec, headers=headers, server_url=spec.server_url, refresh_auth=refresh_auth
    )
    assert tools is not None

    result = await tools[0].execute("tc1", {})
    assert result.is_error is None
    assert refresh_calls == 1
    assert opened_auth == ["Bearer stale", "Bearer fresh"]
    # Shared header dict updated in place → the next call skips the refresh.
    assert headers["Authorization"] == "Bearer fresh"
    await tools[0].execute("tc2", {})
    assert refresh_calls == 1
    assert opened_auth[-1] == "Bearer fresh"


async def test_tool_call_401_without_callback_propagates(monkeypatch: Any) -> None:
    """Static/no-auth connectors keep today's behavior: no retry loop."""

    opened_auth: list[str | None] = []
    _install_fake_session(monkeypatch, opened_auth)

    spec = _make_spec(_PING_CACHE)
    tools = _build_tools_from_cache(
        spec=spec, headers={"Authorization": "Bearer stale"}, server_url=spec.server_url
    )
    assert tools is not None
    with pytest.raises(BaseExceptionGroup):
        await tools[0].execute("tc1", {})
    assert opened_auth == ["Bearer stale"]


async def test_tool_call_refresh_returning_none_propagates_original(monkeypatch: Any) -> None:
    opened_auth: list[str | None] = []
    _install_fake_session(monkeypatch, opened_auth)

    async def refresh_auth() -> str | None:
        return None

    spec = _make_spec(_PING_CACHE)
    tools = _build_tools_from_cache(
        spec=spec,
        headers={"Authorization": "Bearer stale"},
        server_url=spec.server_url,
        refresh_auth=refresh_auth,
    )
    assert tools is not None
    with pytest.raises(BaseExceptionGroup):
        await tools[0].execute("tc1", {})
    assert opened_auth == ["Bearer stale"]


async def test_tool_call_refresh_raising_propagates_original(monkeypatch: Any) -> None:
    opened_auth: list[str | None] = []
    _install_fake_session(monkeypatch, opened_auth)

    async def refresh_auth() -> str | None:
        raise RuntimeError("refresh infra down")

    spec = _make_spec(_PING_CACHE)
    tools = _build_tools_from_cache(
        spec=spec,
        headers={"Authorization": "Bearer stale"},
        server_url=spec.server_url,
        refresh_auth=refresh_auth,
    )
    assert tools is not None
    with pytest.raises(BaseExceptionGroup):
        await tools[0].execute("tc1", {})


def test_make_refresh_auth_callback_gating() -> None:
    """Only OAuth specs with a refresh credential get a callback — the
    retry loop must be unreachable for static/none auth."""
    static_spec = _make_spec(_PING_CACHE)  # auth_method="static"
    assert _make_refresh_auth_callback(spec=static_spec, token_manager=object()) is None

    oauth_no_refresh = replace(
        _make_spec(_PING_CACHE),
        auth_method="oauth",
        grant=SimpleNamespace(refresh_credential_id=None),
    )
    assert _make_refresh_auth_callback(spec=oauth_no_refresh, token_manager=object()) is None

    oauth_ok = replace(oauth_no_refresh, grant=SimpleNamespace(refresh_credential_id="cred_r"))
    assert _make_refresh_auth_callback(spec=oauth_ok, token_manager=object()) is not None
