"""Unit tests for cubebox.mcp.disclosure — config, threshold gate, deferred groups."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cubebox.mcp.disclosure import (
    DisclosureSettings,
    _compute_namespaced_tool_names,
    _spec_description,
    build_deferred_groups,
    disclosure_active,
)
from cubebox.mcp.effective import MCPRuntimeConnectorSpec


def _make_spec(
    *,
    install_id: str = "inst-001",
    name: str = "GitHub",
    server_url: str = "https://mcp.example.com/github",
    transport: str = "streamable_http",
    auth_method: str = "none",
    tools_cache: list[dict[str, Any]] | None = None,
    discovery_metadata: dict[str, Any] | None = None,
    tool_citations: dict[str, dict[str, Any]] | None = None,
) -> MCPRuntimeConnectorSpec:
    return MCPRuntimeConnectorSpec(
        install_id=install_id,
        name=name,
        server_url=server_url,
        transport=transport,
        auth_method=auth_method,
        grant_scope=None,
        credential_id=None,
        refresh_credential_id=None,
        tool_citations=tool_citations or {},
        tools_cache=tools_cache or [],
        discovery_metadata=discovery_metadata or {},
    )


# ---------------------------------------------------------------------------
# Config + threshold gate (existing)
# ---------------------------------------------------------------------------


class TestDisclosureSettings:
    def test_defaults(self) -> None:
        s = DisclosureSettings()
        assert s.enabled == "auto"
        assert s.threshold_pct == 10.0
        assert s.min_servers == 2


class TestDisclosureActive:
    def test_disabled_never_active(self) -> None:
        s = DisclosureSettings(enabled="off")
        assert (
            disclosure_active(s, server_count=100, total_tool_tokens=50_000, context_window=100_000)
            is False
        )

    def test_on_always_active(self) -> None:
        s = DisclosureSettings(enabled="on")
        assert disclosure_active(s, server_count=0) is True

    def test_auto_below_min_servers(self) -> None:
        s = DisclosureSettings(enabled="auto", min_servers=3)
        assert (
            disclosure_active(
                s,
                server_count=2,
                total_tool_tokens=50_000,
                context_window=100_000,
            )
            is False
        )

    def test_auto_below_threshold_pct(self) -> None:
        s = DisclosureSettings(enabled="auto", threshold_pct=10.0, min_servers=2)
        assert (
            disclosure_active(
                s,
                server_count=3,
                total_tool_tokens=5_000,
                context_window=100_000,
            )
            is False
        )

    def test_auto_above_both_thresholds(self) -> None:
        s = DisclosureSettings(enabled="auto", threshold_pct=10.0, min_servers=2)
        assert (
            disclosure_active(
                s,
                server_count=3,
                total_tool_tokens=15_000,
                context_window=100_000,
            )
            is True
        )

    def test_auto_no_context_window(self) -> None:
        s = DisclosureSettings(enabled="auto", min_servers=2)
        assert disclosure_active(s, server_count=3, context_window=0) is False
        assert disclosure_active(s, server_count=1, context_window=0) is False


# ---------------------------------------------------------------------------
# _spec_description
# ---------------------------------------------------------------------------


class TestSpecDescription:
    def test_extracts_from_discovery_metadata(self) -> None:
        spec = _make_spec(
            discovery_metadata={
                "server": {"description": "Manage GitHub repositories and issues"},
            },
        )
        assert _spec_description(spec) == "Manage GitHub repositories and issues"

    def test_falls_back_to_summary(self) -> None:
        spec = _make_spec(
            discovery_metadata={"server": {"summary": "GitHub tools"}},
        )
        assert _spec_description(spec) == "GitHub tools"

    def test_falls_back_to_name(self) -> None:
        spec = _make_spec(name="Slack", discovery_metadata={})
        assert _spec_description(spec) == "Slack"

    def test_falls_back_to_name_when_no_metadata(self) -> None:
        spec = _make_spec(name="Slack", discovery_metadata=None)
        assert _spec_description(spec) == "Slack"

    def test_truncates_long_description(self) -> None:
        long_desc = "A" * 200
        spec = _make_spec(
            discovery_metadata={"server": {"description": long_desc}},
        )
        result = _spec_description(spec)
        assert len(result) == 140
        assert result.endswith("…")

    def test_collapses_whitespace(self) -> None:
        spec = _make_spec(
            discovery_metadata={"server": {"description": "  foo   bar  baz  "}},
        )
        assert _spec_description(spec) == "foo bar baz"


# ---------------------------------------------------------------------------
# _compute_namespaced_tool_names
# ---------------------------------------------------------------------------


class TestComputeNamespacedToolNames:
    def test_simple_namespace(self) -> None:
        spec = _make_spec(
            name="GitHub",
            tools_cache=[{"name": "create_issue"}, {"name": "list_repos"}],
        )
        names = _compute_namespaced_tool_names(spec, all_specs=[spec])
        assert names == ["GitHub__create_issue", "GitHub__list_repos"]

    def test_slug_collision_appends_suffix(self) -> None:
        spec_a = _make_spec(
            install_id="inst-aaaa",
            name="GitHub",
            tools_cache=[{"name": "create_issue"}],
        )
        spec_b = _make_spec(
            install_id="inst-bbbb",
            name="GitHub",
            tools_cache=[{"name": "list_repos"}],
        )
        all_specs = [spec_a, spec_b]
        names_a = _compute_namespaced_tool_names(spec_a, all_specs=all_specs)
        names_b = _compute_namespaced_tool_names(spec_b, all_specs=all_specs)
        assert names_a == ["GitHub_aaaa__create_issue"]
        assert names_b == ["GitHub_bbbb__list_repos"]

    def test_skips_entries_without_name(self) -> None:
        spec = _make_spec(
            name="Slack",
            tools_cache=[{"name": "post_message"}, {}, {"description": "no name"}],
        )
        names = _compute_namespaced_tool_names(spec, all_specs=[spec])
        assert names == ["Slack__post_message"]


# ---------------------------------------------------------------------------
# _load_tools_for_specs (extracted helper)
# ---------------------------------------------------------------------------


class TestLoadToolsForSpecs:
    @pytest.mark.asyncio
    async def test_returns_namespaced_tools_and_citations(self) -> None:
        from cubepi.agent.types import AgentTool

        from cubebox.mcp.cubepi_runtime import _load_tools_for_specs

        fake_tool = AgentTool(
            name="create_issue",
            description="Create an issue",
            parameters=type("P", (), {"model_json_schema": staticmethod(lambda: {})}),
            execute=AsyncMock(),
        )
        discovery = type("D", (), {"tools": [fake_tool]})()

        spec = _make_spec(
            name="GitHub",
            tool_citations={
                "create_issue": {
                    "content_type": "json",
                    "source_type": "web",
                    "content_field": "results",
                    "mapping": {"url": "link"},
                },
            },
        )

        with (
            patch(
                "cubebox.mcp.cubepi_runtime._resolve_auth_from_spec",
                new_callable=AsyncMock,
                return_value=({}, spec.server_url),
            ),
            patch(
                "cubebox.mcp.cubepi_runtime.load_mcp_tools_http",
                new_callable=AsyncMock,
                return_value=discovery,
            ),
        ):
            tools, citations = await _load_tools_for_specs(
                specs=[spec],
                all_specs=[spec],
                workspace_id="ws-1",
                org_id="org-1",
                user_id="user-1",
                cred_service=AsyncMock(),
                signer=AsyncMock(),
                token_manager=AsyncMock(),
                grant_repo=None,
            )

        assert len(tools) == 1
        assert tools[0].name == "GitHub__create_issue"
        assert "GitHub__create_issue" in citations

    @pytest.mark.asyncio
    async def test_handles_auth_failure_gracefully(self) -> None:
        from cubebox.mcp.cubepi_runtime import _load_tools_for_specs

        spec = _make_spec(name="BadServer")
        with patch(
            "cubebox.mcp.cubepi_runtime._resolve_auth_from_spec",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            tools, citations = await _load_tools_for_specs(
                specs=[spec],
                all_specs=[spec],
                workspace_id="ws-1",
                org_id="org-1",
                user_id="user-1",
                cred_service=AsyncMock(),
                signer=AsyncMock(),
                token_manager=AsyncMock(),
                grant_repo=None,
            )
        assert tools == []
        assert citations == {}


# ---------------------------------------------------------------------------
# build_deferred_groups
# ---------------------------------------------------------------------------


def _deferred_loader_kwargs() -> dict[str, Any]:
    return {
        "workspace_id": "ws-1",
        "org_id": "org-1",
        "user_id": "user-1",
        "encryption_backend": AsyncMock(),
        "http_client": AsyncMock(),
        "metadata_discovery": AsyncMock(),
        "redis": AsyncMock(),
        "signer": AsyncMock(),
    }


class TestBuildDeferredGroups:
    def test_produces_correct_group_metadata(self) -> None:
        spec = _make_spec(
            name="GitHub",
            tools_cache=[{"name": "create_issue"}, {"name": "list_repos"}],
            discovery_metadata={"server": {"description": "GitHub integration"}},
        )
        groups, citations = build_deferred_groups(
            specs=[spec],
            all_specs=[spec],
            loader_kwargs=_deferred_loader_kwargs(),
        )
        assert len(groups) == 1
        g = groups[0]
        assert g.group_id == "mcp:GitHub"
        assert g.display_name == "GitHub"
        assert g.description == "GitHub integration"
        assert g.tool_names == ["GitHub__create_issue", "GitHub__list_repos"]
        assert callable(g.loader)
        assert citations == {}

    def test_multiple_specs_produce_multiple_groups(self) -> None:
        spec_a = _make_spec(
            install_id="inst-a",
            name="GitHub",
            tools_cache=[{"name": "create_issue"}],
        )
        spec_b = _make_spec(
            install_id="inst-b",
            name="Slack",
            tools_cache=[{"name": "post_message"}],
        )
        groups, _ = build_deferred_groups(
            specs=[spec_a, spec_b],
            all_specs=[spec_a, spec_b],
            loader_kwargs=_deferred_loader_kwargs(),
        )
        assert len(groups) == 2
        assert groups[0].group_id == "mcp:GitHub"
        assert groups[1].group_id == "mcp:Slack"

    def test_slug_collision_disambiguates_group_id(self) -> None:
        spec_a = _make_spec(
            install_id="inst-aaaa",
            name="GitHub",
            tools_cache=[{"name": "create_issue"}],
        )
        spec_b = _make_spec(
            install_id="inst-bbbb",
            name="GitHub",
            tools_cache=[{"name": "list_repos"}],
        )
        groups, _ = build_deferred_groups(
            specs=[spec_a, spec_b],
            all_specs=[spec_a, spec_b],
            loader_kwargs=_deferred_loader_kwargs(),
        )
        assert len(groups) == 2
        assert groups[0].group_id == "mcp:GitHub_aaaa"
        assert groups[1].group_id == "mcp:GitHub_bbbb"

    @pytest.mark.asyncio
    async def test_loader_calls_load_tools_for_specs(self) -> None:
        from cubepi.agent.types import AgentTool

        fake_tool = AgentTool(
            name="GitHub__create_issue",
            description="Create an issue",
            parameters=type("P", (), {"model_json_schema": staticmethod(lambda: {})}),
            execute=AsyncMock(),
        )

        spec = _make_spec(
            name="GitHub",
            tools_cache=[{"name": "create_issue"}],
        )
        groups, shared_citations = build_deferred_groups(
            specs=[spec],
            all_specs=[spec],
            loader_kwargs=_deferred_loader_kwargs(),
        )

        with patch(
            "cubebox.mcp.disclosure._load_tools_for_specs_deferred",
            new_callable=AsyncMock,
            return_value=([fake_tool], {"GitHub__create_issue": object()}),
        ) as mock_load:
            result = await groups[0].loader()

        mock_load.assert_called_once()
        assert len(result) == 1
        assert result[0].name == "GitHub__create_issue"
        assert "GitHub__create_issue" in shared_citations
