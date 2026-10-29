"""Contracts for the cubeloop MCP Client wrapper.

Discovery, Try It, and tools_cache all open sessions through
``open_mcp_http_client``. The yielded client is already negotiated
(auto-mode); callers must not ``initialize()``. Serialize must keep the
Try It / AgentTool dict shape (``isError`` + ``content``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from cubeplex.mcp.client import open_mcp_http_client, serialize_mcp_call_tool_response


def _tool_result(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        is_error=False,
        structured_content={"result": text},
    )


def _install_fake_open_client(
    monkeypatch: pytest.MonkeyPatch, *, client: Any
) -> list[dict[str, Any]]:
    opened: list[dict[str, Any]] = []

    @asynccontextmanager
    async def fake_open(
        server_url: str,
        *,
        headers: dict[str, str] | None,
        timeout: float,
        transport: str,
    ) -> Any:
        opened.append(
            {
                "server_url": server_url,
                "headers": headers,
                "timeout": timeout,
                "transport": transport,
            }
        )
        yield client

    monkeypatch.setattr("cubeloop.mcp.open_mcp_http_client", fake_open)
    return opened


def test_serialize_keeps_adapter_try_it_shape() -> None:
    """Try It and cache-built tools persist cubeloop's adapter dict, including
    falsy ``isError`` and structured content from SDK 2 snake_case fields."""
    out = serialize_mcp_call_tool_response(_tool_result("pong"))
    assert out["isError"] is False
    assert out["content"] == [{"type": "text", "text": "pong"}]
    assert out["structuredContent"] == {"result": "pong"}


async def test_open_mcp_http_client_forwards_to_cubeloop_opener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Client:
        async def initialize(self) -> None:
            raise AssertionError("auto-mode Client is already negotiated")

    opened = _install_fake_open_client(monkeypatch, client=_Client())
    headers = {"Authorization": "Bearer tok"}
    async with open_mcp_http_client(
        "https://mcp.example.com/mcp",
        headers=headers,
        timeout=9.0,
        transport="sse",
    ) as client:
        assert isinstance(client, _Client)
    assert opened == [
        {
            "server_url": "https://mcp.example.com/mcp",
            "headers": headers,
            "timeout": 9.0,
            "transport": "sse",
        }
    ]


async def test_list_raw_tools_uses_client_server_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery must read ``client.server_info`` (SDK 2) and the raw Tool
    list — cubeloop's AgentTool wrapper drops ``outputSchema``."""
    from cubeplex.services.mcp_discovery import _list_raw_mcp_tools

    async def _allow(_url: str) -> None:
        return None

    monkeypatch.setattr("cubeplex.services.mcp_discovery.validate_mcp_outbound_url", _allow)

    class _Client:
        server_info = SimpleNamespace(name="demo", version="2")

        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(tools=[SimpleNamespace(name="echo")])

        async def initialize(self) -> None:
            raise AssertionError("discovery must not re-initialize auto-mode")

    _install_fake_open_client(monkeypatch, client=_Client())
    raw = await _list_raw_mcp_tools(
        "https://mcp.example.com/mcp",
        headers={"Authorization": "Bearer tok"},
        timeout=5.0,
        transport="streamable_http",
    )
    assert [t.name for t in raw.tools] == ["echo"]
    assert raw.init_result.name == "demo"
    assert raw.init_result.version == "2"


async def test_invoke_serializes_call_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.api.routes.v1.ws_mcp import _invoke_tool_via_cubeloop

    class _Client:
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
            assert name == "echo"
            assert arguments == {"text": "hi"}
            return _tool_result(f"echo:{arguments['text']}")

        async def initialize(self) -> None:
            raise AssertionError("Try It must not re-initialize auto-mode")

    _install_fake_open_client(monkeypatch, client=_Client())
    out = await _invoke_tool_via_cubeloop(
        "https://mcp.example.com/mcp",
        "echo",
        {"text": "hi"},
        headers={"Authorization": "Bearer tok"},
        timeout=5.0,
        transport="streamable_http",
    )
    assert out["isError"] is False
    assert out["content"] == [{"type": "text", "text": "echo:hi"}]


async def test_invoke_rejects_unknown_transport() -> None:
    from cubeplex.api.routes.v1.ws_mcp import _invoke_tool_via_cubeloop

    with pytest.raises(ValueError, match="unsupported transport"):
        await _invoke_tool_via_cubeloop(
            "https://mcp.example.com/mcp",
            "echo",
            {},
            headers=None,
            timeout=1.0,
            transport="stdio",
        )
