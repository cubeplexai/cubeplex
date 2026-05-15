"""Per-run MCP loader namespaces tool names and emits citation configs."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cubepi.agent.types import AgentTool
from pydantic import BaseModel


class _StubParams(BaseModel):
    """Minimal parameter model for fake AgentTool."""


def _fake_tool(name: str) -> AgentTool[_StubParams]:
    async def _exec(**_kwargs: Any) -> Any:
        from cubepi.agent.types import AgentToolResult

        return AgentToolResult(content=[])

    return AgentTool(
        name=name,
        description="",
        parameters=_StubParams,
        execute=_exec,
    )


@pytest.mark.asyncio
async def test_loader_namespaces_tool_names_and_returns_citation_configs() -> None:
    from cubebox.mcp.cubepi_discovery import CubepiMCPServerSpec
    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_for_cubepi
    from cubebox.middleware.citations.config import CitationConfig

    specs = [
        CubepiMCPServerSpec(
            server_id="mcp-1",
            server_name="webtools",
            url="http://example.com/mcp",
            headers={},
            tool_citations={
                "web_search": {
                    "content_type": "json",
                    "source_type": "web",
                    "content_field": "results",
                    "mapping": {"snippet": "description"},
                }
            },
        ),
        CubepiMCPServerSpec(
            server_id="mcp-2",
            server_name="other",
            url="http://other.example.com/mcp",
            headers={},
            tool_citations={},
        ),
    ]

    with (
        patch(
            "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
            new=AsyncMock(return_value=specs),
        ),
        patch(
            "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
            new=AsyncMock(
                side_effect=[
                    [_fake_tool("web_search"), _fake_tool("web_fetch")],
                    [_fake_tool("web_search")],
                ]
            ),
        ),
    ):
        tools, citation_configs = await load_workspace_mcp_tools_for_cubepi(
            session=None,  # type: ignore[arg-type]
            workspace_id="ws-x",
            org_id="org-x",
            user_id="usr-x",
            cred_service=None,  # type: ignore[arg-type]
            signer=None,  # type: ignore[arg-type]
        )

    names = {t.name for t in tools}
    assert names == {"webtools__web_search", "webtools__web_fetch", "other__web_search"}

    assert set(citation_configs.keys()) == {"webtools__web_search"}
    cfg = citation_configs["webtools__web_search"]
    assert isinstance(cfg, CitationConfig)
    assert cfg.source_type == "web"
    assert cfg.content_field == "results"


@pytest.mark.asyncio
async def test_loader_skips_invalid_citation_config_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Bad CitationConfig data is logged + skipped, not raised."""
    from cubebox.mcp.cubepi_discovery import CubepiMCPServerSpec
    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_for_cubepi

    specs = [
        CubepiMCPServerSpec(
            server_id="mcp-1",
            server_name="webtools",
            url="http://example.com/mcp",
            headers={},
            tool_citations={
                "bad_tool": {"this_is_not": "a_valid_citation_config"},
                "good_tool": {
                    "content_type": "json",
                    "source_type": "web",
                    "content_field": None,
                    "mapping": {"snippet": "s"},
                },
            },
        ),
    ]
    with (
        patch(
            "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
            new=AsyncMock(return_value=specs),
        ),
        patch(
            "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
            new=AsyncMock(return_value=[_fake_tool("bad_tool"), _fake_tool("good_tool")]),
        ),
    ):
        tools, citation_configs = await load_workspace_mcp_tools_for_cubepi(
            session=None,  # type: ignore[arg-type]
            workspace_id="ws-x",
            org_id="org-x",
            user_id="usr-x",
            cred_service=None,  # type: ignore[arg-type]
            signer=None,  # type: ignore[arg-type]
        )

    # Both tools are namespaced and returned.
    assert {t.name for t in tools} == {"webtools__bad_tool", "webtools__good_tool"}
    # Only the valid one ends up in citation_configs.
    assert set(citation_configs.keys()) == {"webtools__good_tool"}
    assert "Bad tool_citations" in caplog.text


@pytest.mark.asyncio
async def test_loader_continues_on_per_server_load_failure() -> None:
    """If one server fails to load tools, the other still contributes."""
    from cubebox.mcp.cubepi_discovery import CubepiMCPServerSpec
    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_for_cubepi

    specs = [
        CubepiMCPServerSpec(
            server_id="mcp-1",
            server_name="dead",
            url="http://dead/mcp",
            headers={},
            tool_citations={},
        ),
        CubepiMCPServerSpec(
            server_id="mcp-2",
            server_name="live",
            url="http://live/mcp",
            headers={},
            tool_citations={},
        ),
    ]
    with (
        patch(
            "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
            new=AsyncMock(return_value=specs),
        ),
        patch(
            "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
            new=AsyncMock(
                side_effect=[
                    RuntimeError("connection refused"),
                    [_fake_tool("ping")],
                ]
            ),
        ),
    ):
        tools, citation_configs = await load_workspace_mcp_tools_for_cubepi(
            session=None,  # type: ignore[arg-type]
            workspace_id="ws-x",
            org_id="org-x",
            user_id="usr-x",
            cred_service=None,  # type: ignore[arg-type]
            signer=None,  # type: ignore[arg-type]
        )

    assert {t.name for t in tools} == {"live__ping"}
    assert citation_configs == {}


@pytest.mark.asyncio
async def test_loader_slugifies_server_name_in_namespace() -> None:
    """Server names with spaces / punctuation are sanitized for the tool name prefix."""
    from cubebox.mcp.cubepi_discovery import CubepiMCPServerSpec
    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_for_cubepi

    specs = [
        CubepiMCPServerSpec(
            server_id="mcp-1",
            server_name="Cloudflare Workers",
            url="http://example.com/mcp",
            headers={},
            tool_citations={},
        ),
    ]
    with (
        patch(
            "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
            new=AsyncMock(return_value=specs),
        ),
        patch(
            "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
            new=AsyncMock(return_value=[_fake_tool("fetch_url")]),
        ),
    ):
        tools, _ = await load_workspace_mcp_tools_for_cubepi(
            session=None,  # type: ignore[arg-type]
            workspace_id="ws-x",
            org_id="org-x",
            user_id="usr-x",
            cred_service=None,  # type: ignore[arg-type]
            signer=None,  # type: ignore[arg-type]
        )

    assert len(tools) == 1
    # Spaces collapsed to underscore; matches OpenAI strict regex.
    assert tools[0].name == "Cloudflare_Workers__fetch_url"
    import re

    assert re.fullmatch(r"[a-zA-Z0-9_]+", tools[0].name)


@pytest.mark.asyncio
async def test_loader_truncates_long_namespaced_names() -> None:
    """Very long server names are truncated so combined name <= 64 chars."""
    from cubebox.mcp.cubepi_discovery import CubepiMCPServerSpec
    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_for_cubepi

    long_name = "X" * 100  # way over 64
    specs = [
        CubepiMCPServerSpec(
            server_id="mcp-1",
            server_name=long_name,
            url="http://example.com/mcp",
            headers={},
            tool_citations={},
        ),
    ]
    with (
        patch(
            "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
            new=AsyncMock(return_value=specs),
        ),
        patch(
            "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
            new=AsyncMock(return_value=[_fake_tool("ping")]),
        ),
    ):
        tools, _ = await load_workspace_mcp_tools_for_cubepi(
            session=None,  # type: ignore[arg-type]
            workspace_id="ws-x",
            org_id="org-x",
            user_id="usr-x",
            cred_service=None,  # type: ignore[arg-type]
            signer=None,  # type: ignore[arg-type]
        )

    assert len(tools) == 1
    assert len(tools[0].name) <= 64
    assert tools[0].name.endswith("__ping")


@pytest.mark.asyncio
async def test_loader_disambiguates_collision_with_id_suffix() -> None:
    """Two servers with the same slug get distinct namespaces via id-suffix."""
    from cubebox.mcp.cubepi_discovery import CubepiMCPServerSpec
    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_for_cubepi

    specs = [
        CubepiMCPServerSpec(
            server_id="mcp-1234567890aaaa",
            server_name="WebTools",
            url="http://example.com/mcp",
            headers={},
            tool_citations={},
        ),
        CubepiMCPServerSpec(
            server_id="mcp-1234567890bbbb",
            server_name="WebTools",  # same display name, different scope (org-wide vs ws-owned)
            url="http://other.example.com/mcp",
            headers={},
            tool_citations={},
        ),
    ]
    with (
        patch(
            "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
            new=AsyncMock(return_value=specs),
        ),
        patch(
            "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
            new=AsyncMock(
                side_effect=[
                    [_fake_tool("web_search")],
                    [_fake_tool("web_search")],
                ]
            ),
        ),
    ):
        tools, _ = await load_workspace_mcp_tools_for_cubepi(
            session=None,  # type: ignore[arg-type]
            workspace_id="ws-x",
            org_id="org-x",
            user_id="usr-x",
            cred_service=None,  # type: ignore[arg-type]
            signer=None,  # type: ignore[arg-type]
        )

    names = {t.name for t in tools}
    assert len(names) == 2  # Two DISTINCT names, not collapsed
    # Each name should end with its id-derived suffix followed by __web_search
    assert any(n.endswith("aaaa__web_search") for n in names)
    assert any(n.endswith("bbbb__web_search") for n in names)


@pytest.mark.asyncio
async def test_loader_preserves_collision_suffix_when_truncating() -> None:
    """When two long-named servers collide AND the combined name overflows
    64 chars, both still end up with distinct namespaces (the id-suffix is
    not lopped off by the length cap).
    """
    from cubebox.mcp.cubepi_discovery import CubepiMCPServerSpec
    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_for_cubepi

    long_name = "X" * 60  # slug = 60 chars; combined would overflow even with short tool name
    specs = [
        CubepiMCPServerSpec(
            server_id="mcp-1234567890aaaa",
            server_name=long_name,
            url="http://example.com/mcp",
            headers={},
            tool_citations={},
        ),
        CubepiMCPServerSpec(
            server_id="mcp-1234567890bbbb",
            server_name=long_name,
            url="http://other/mcp",
            headers={},
            tool_citations={},
        ),
    ]
    with (
        patch(
            "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
            new=AsyncMock(return_value=specs),
        ),
        patch(
            "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
            new=AsyncMock(
                side_effect=[
                    [_fake_tool("web_search")],
                    [_fake_tool("web_search")],
                ]
            ),
        ),
    ):
        tools, _ = await load_workspace_mcp_tools_for_cubepi(
            session=None,  # type: ignore[arg-type]
            workspace_id="ws-x",
            org_id="org-x",
            user_id="usr-x",
            cred_service=None,  # type: ignore[arg-type]
            signer=None,  # type: ignore[arg-type]
        )

    names = [t.name for t in tools]
    assert len(set(names)) == 2, f"Names collided after truncation: {names}"
    for n in names:
        assert len(n) <= 64
        assert n.endswith("__web_search")
        # The disambiguator suffix is preserved between the truncated slug and "__"
        assert "_aaaa__" in n or "_bbbb__" in n


@pytest.mark.asyncio
async def test_loader_disambiguates_long_distinct_slugs_with_shared_prefix() -> None:
    """Two servers with different but long display names sharing initial chars
    must NOT collide after truncation: the length-defence triggers id-suffix.
    """
    from cubebox.mcp.cubepi_discovery import CubepiMCPServerSpec
    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_for_cubepi

    # Both slugs share the first 50 chars; differ only at the tail.
    shared = "X" * 50
    name_a = f"{shared}_AAAAAA_versionone"
    name_b = f"{shared}_AAAAAA_versiontwo"

    specs = [
        CubepiMCPServerSpec(
            server_id="mcp-1234567890aaaa",
            server_name=name_a,
            url="http://example.com/mcp",
            headers={},
            tool_citations={},
        ),
        CubepiMCPServerSpec(
            server_id="mcp-1234567890bbbb",
            server_name=name_b,
            url="http://other/mcp",
            headers={},
            tool_citations={},
        ),
    ]
    with (
        patch(
            "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
            new=AsyncMock(return_value=specs),
        ),
        patch(
            "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
            new=AsyncMock(
                side_effect=[
                    [_fake_tool("web_search")],
                    [_fake_tool("web_search")],
                ]
            ),
        ),
    ):
        tools, _ = await load_workspace_mcp_tools_for_cubepi(
            session=None,
            workspace_id="ws-x",
            org_id="org-x",
            user_id="usr-x",
            cred_service=None,  # type: ignore[arg-type]
            signer=None,  # type: ignore[arg-type]
        )

    names = [t.name for t in tools]
    assert len(set(names)) == 2, f"Names collided after truncation: {names}"
    for n in names:
        assert len(n) <= 64
        assert n.endswith("__web_search")
        # Disambiguator suffix must be present despite no explicit slug collision
        assert "_aaaa__" in n or "_bbbb__" in n


@pytest.mark.asyncio
async def test_loader_keeps_clean_name_when_no_collision() -> None:
    """The id-suffix is only appended on collision; unique names stay clean."""
    from cubebox.mcp.cubepi_discovery import CubepiMCPServerSpec
    from cubebox.mcp.cubepi_runtime import load_workspace_mcp_tools_for_cubepi

    specs = [
        CubepiMCPServerSpec(
            server_id="mcp-aaa",
            server_name="Alpha",
            url="http://a/mcp",
            headers={},
            tool_citations={},
        ),
        CubepiMCPServerSpec(
            server_id="mcp-bbb",
            server_name="Beta",
            url="http://b/mcp",
            headers={},
            tool_citations={},
        ),
    ]
    with (
        patch(
            "cubebox.mcp.cubepi_runtime.discover_workspace_mcp_servers_for_cubepi",
            new=AsyncMock(return_value=specs),
        ),
        patch(
            "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
            new=AsyncMock(side_effect=[[_fake_tool("ping")], [_fake_tool("pong")]]),
        ),
    ):
        tools, _ = await load_workspace_mcp_tools_for_cubepi(
            session=None,  # type: ignore[arg-type]
            workspace_id="ws-x",
            org_id="org-x",
            user_id="usr-x",
            cred_service=None,  # type: ignore[arg-type]
            signer=None,  # type: ignore[arg-type]
        )

    names = {t.name for t in tools}
    assert names == {"Alpha__ping", "Beta__pong"}
