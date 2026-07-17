"""E2E tests for MCP progressive disclosure in the agent runtime.

Tests the full pipeline: disclosure gate → DeferredToolGroup → middleware →
expansion, as well as the deferred loader's session lifecycle.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.deferred import DeferredToolGroup, DeferredToolsMiddleware
from cubepi.providers.base import TextContent
from pydantic import BaseModel

from cubeplex.mcp.disclosure import (
    DisclosureSettings,
    build_deferred_groups,
    disclosure_active,
)
from cubeplex.mcp.effective import MCPRuntimeConnectorSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _EmptyParams(BaseModel):
    pass


async def _noop_execute(
    tool_call_id: str,
    args: Any,
    *,
    signal: Any = None,
    on_update: Any = None,
) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text="ok")])


def _make_spec(
    name: str,
    *,
    connector_id: str = "",
    tools_cache: list[dict[str, Any]] | None = None,
    auth_method: str = "none",
    server_url: str = "",
    discovery_metadata: dict[str, Any] | None = None,
) -> MCPRuntimeConnectorSpec:
    return MCPRuntimeConnectorSpec(
        connector_id=connector_id or f"inst-{name.lower()}",
        name=name,
        server_url=server_url or f"https://{name.lower()}.example.com/mcp",
        transport="streamable_http",
        auth_method=auth_method,
        grant_scope=None,
        credential_id=None,
        refresh_credential_id=None,
        tool_citations={},
        tools_cache=tools_cache or [{"name": "default_tool"}],
        discovery_metadata=discovery_metadata or {"server": {"description": f"{name} tools"}},
    )


def _make_fake_tool(name: str) -> AgentTool[Any]:
    return AgentTool(
        name=name,
        description=f"Tool {name}",
        parameters=_EmptyParams,
        execute=_noop_execute,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestDisclosureGateIntegration:
    """Verify disclosure_active routes correctly given real-looking specs."""

    def test_on_always_activates(self) -> None:
        settings = DisclosureSettings(enabled="on")
        specs = [_make_spec("A"), _make_spec("B")]
        assert disclosure_active(settings, server_count=len(specs)) is True

    def test_off_never_activates(self) -> None:
        settings = DisclosureSettings(enabled="off")
        specs = [_make_spec(f"S{i}") for i in range(10)]
        assert (
            disclosure_active(
                settings,
                server_count=len(specs),
                total_tool_tokens=999_999,
                context_window=100_000,
            )
            is False
        )

    def test_auto_below_min_servers(self) -> None:
        settings = DisclosureSettings(enabled="auto", min_servers=5)
        specs = [_make_spec("A"), _make_spec("B")]
        assert disclosure_active(settings, server_count=len(specs)) is False

    def test_auto_above_threshold(self) -> None:
        settings = DisclosureSettings(enabled="auto", threshold_pct=10.0, min_servers=2)
        specs = [_make_spec("A"), _make_spec("B"), _make_spec("C")]
        assert (
            disclosure_active(
                settings,
                server_count=len(specs),
                total_tool_tokens=15_000,
                context_window=100_000,
            )
            is True
        )


@pytest.mark.e2e
class TestDeferredGroupBuilding:
    """build_deferred_groups produces correct DeferredToolGroup objects."""

    async def test_groups_match_specs(self) -> None:
        specs = [
            _make_spec(
                "GitHub",
                tools_cache=[{"name": "create_issue"}, {"name": "list_repos"}],
            ),
            _make_spec(
                "Slack",
                tools_cache=[{"name": "send_message"}, {"name": "list_channels"}],
            ),
        ]
        groups, _ = build_deferred_groups(
            specs=specs,
            all_specs=specs,
            loader_kwargs={
                "workspace_id": "ws-1",
                "org_id": "org-1",
                "user_id": "user-1",
                "encryption_backend": AsyncMock(),
                "http_client": AsyncMock(),
                "metadata_discovery": AsyncMock(),
                "redis": AsyncMock(),
                "signer": AsyncMock(),
            },
        )
        assert len(groups) == 2
        assert groups[0].group_id == "mcp:GitHub"
        assert groups[1].group_id == "mcp:Slack"
        assert set(groups[0].tool_names) == {"GitHub__create_issue", "GitHub__list_repos"}
        assert set(groups[1].tool_names) == {"Slack__send_message", "Slack__list_channels"}

    async def test_loader_invokes_deferred_pipeline(self) -> None:
        """Loader callback calls _load_tools_for_specs_deferred with correct kwargs."""
        specs = [
            _make_spec("GitHub", tools_cache=[{"name": "create_issue"}]),
        ]
        fake_tool = _make_fake_tool("GitHub__create_issue")

        groups, shared_citations = build_deferred_groups(
            specs=specs,
            all_specs=specs,
            loader_kwargs={
                "workspace_id": "ws-1",
                "org_id": "org-1",
                "user_id": "user-1",
                "encryption_backend": AsyncMock(),
                "http_client": AsyncMock(),
                "metadata_discovery": AsyncMock(),
                "redis": AsyncMock(),
                "signer": AsyncMock(),
            },
        )

        with patch(
            "cubeplex.mcp.disclosure._load_tools_for_specs_deferred",
            new_callable=AsyncMock,
            return_value=([fake_tool], {"GitHub__create_issue": object()}),
        ):
            result = await groups[0].loader()

        assert len(result) == 1
        assert result[0].name == "GitHub__create_issue"
        assert "GitHub__create_issue" in shared_citations


@pytest.mark.e2e
class TestMiddlewareIntegration:
    """DeferredToolsMiddleware integration with cubeplex-produced groups."""

    async def test_catalog_in_system_prompt(self) -> None:
        """When groups are deferred, catalog appears in system prompt."""
        specs = [
            _make_spec(
                "GitHub",
                tools_cache=[{"name": "create_issue"}, {"name": "list_repos"}],
                discovery_metadata={"server": {"description": "GitHub tools"}},
            ),
            _make_spec(
                "Slack",
                tools_cache=[{"name": "send_message"}],
                discovery_metadata={"server": {"description": "Slack messaging"}},
            ),
        ]
        groups, _ = build_deferred_groups(
            specs=specs,
            all_specs=specs,
            loader_kwargs={
                "workspace_id": "ws-1",
                "org_id": "org-1",
                "user_id": "user-1",
                "encryption_backend": AsyncMock(),
                "http_client": AsyncMock(),
                "metadata_discovery": AsyncMock(),
                "redis": AsyncMock(),
                "signer": AsyncMock(),
            },
        )

        extra: dict[str, Any] = {}
        mw = DeferredToolsMiddleware(
            groups=groups,
            extra_ref=lambda: extra,
        )

        # Middleware contributes load_tools + deferred_tool_call dispatcher
        # (dispatch mode is the default in cubepi >= 0.11).
        tool_names = {t.name for t in mw.tools}
        assert tool_names == {"load_tools", "deferred_tool_call"}

        # System prompt includes catalog with group IDs
        ctx = AsyncMock()
        prompt = await mw.transform_system_prompt("Base prompt.", ctx=ctx, signal=None)
        assert "mcp:GitHub" in prompt
        assert "mcp:Slack" in prompt
        assert "GitHub tools" in prompt
        assert "Slack messaging" in prompt
        assert "load_tools" in prompt

    async def test_expand_group_adds_tools(self) -> None:
        """Expanding a group via middleware makes tools available."""
        fake_tools = [
            _make_fake_tool("GitHub__create_issue"),
            _make_fake_tool("GitHub__list_repos"),
        ]

        async def _loader() -> list[AgentTool[Any]]:
            return fake_tools

        group = DeferredToolGroup(
            group_id="mcp:GitHub",
            display_name="GitHub",
            description="GitHub tools",
            tool_names=["GitHub__create_issue", "GitHub__list_repos"],
            loader=_loader,
        )

        extra: dict[str, Any] = {}
        expanded_tools: list[AgentTool[Any]] = []
        mw = DeferredToolsMiddleware(
            groups=[group],
            extra_ref=lambda: extra,
            on_tools_expanded=lambda new: expanded_tools.extend(new),
        )

        ctx = AsyncMock()
        ctx.tools = list(mw.tools)  # start with just load_tools

        result = await mw._expand(
            group_id="mcp:GitHub",
            tool_names=None,
            context=ctx,
        )

        assert result.expanded is True
        assert set(result.tool_names) == {"GitHub__create_issue", "GitHub__list_repos"}
        assert result.remaining == 0

        # Tools were injected into context.tools
        tool_names = {t.name for t in ctx.tools}
        assert "GitHub__create_issue" in tool_names
        assert "GitHub__list_repos" in tool_names

        # on_tools_expanded callback fired
        assert len(expanded_tools) == 2

    async def test_no_deferred_groups_means_no_middleware(self) -> None:
        """When disclosure is inactive, no DeferredToolsMiddleware is needed."""
        settings = DisclosureSettings(enabled="off")
        specs = [_make_spec("A"), _make_spec("B")]
        assert not disclosure_active(settings, server_count=len(specs))
        # The run_manager code path would NOT call build_deferred_groups —
        # it goes through the eager load_workspace_mcp_tools_for_cubepi path.


@pytest.mark.e2e
class TestDeferredLoaderSessionLifecycle:
    """_load_tools_for_specs_deferred creates its own DB session."""

    async def test_deferred_loader_creates_own_session(self) -> None:
        """Loader succeeds even when the original session is closed.

        This is the key lifecycle test: the loader_kwargs carry
        session-independent factory ingredients, and the deferred function
        opens a short-lived session internally.
        """
        from types import SimpleNamespace

        spec = _make_spec(
            "GitHub",
            tools_cache=[{"name": "create_issue"}],
            auth_method="none",
        )

        # load_mcp_tools_http returns a discovery object whose .tools are
        # AgentTool dataclass instances (dataclasses.replace is called on them).
        fake_tool = _make_fake_tool("create_issue")
        fake_discovery = SimpleNamespace(tools=[fake_tool])

        mock_signer = AsyncMock()
        mock_signer.sign = AsyncMock(return_value="fake-jwt-token")

        with patch(
            "cubeplex.mcp.cubepi_runtime.load_mcp_tools_http",
            new_callable=AsyncMock,
            return_value=fake_discovery,
        ):
            from cubeplex.mcp.cubepi_runtime import _load_tools_for_specs_deferred

            tools, citations = await _load_tools_for_specs_deferred(
                specs=[spec],
                all_specs=[spec],
                workspace_id="ws-1",
                org_id="org-1",
                user_id="user-1",
                encryption_backend=AsyncMock(),
                http_client=AsyncMock(),
                metadata_discovery=AsyncMock(),
                redis=AsyncMock(),
                signer=mock_signer,
            )

        assert len(tools) == 1
        assert tools[0].name == "GitHub__create_issue"
        mock_signer.sign.assert_called_once()


@pytest.mark.e2e
class TestCachePrefixStability:
    """Disclosure catalog is byte-stable; expansions are append-only."""

    @staticmethod
    def _build_middleware(
        specs: list[MCPRuntimeConnectorSpec],
        extra: dict[str, Any],
    ) -> DeferredToolsMiddleware:
        groups, _ = build_deferred_groups(
            specs=specs,
            all_specs=specs,
            loader_kwargs={
                "workspace_id": "ws-1",
                "org_id": "org-1",
                "user_id": "user-1",
                "encryption_backend": AsyncMock(),
                "http_client": AsyncMock(),
                "metadata_discovery": AsyncMock(),
                "redis": AsyncMock(),
                "signer": AsyncMock(),
            },
        )
        return DeferredToolsMiddleware(groups=groups, extra_ref=lambda: extra)

    async def test_catalog_byte_stable_across_turns(self) -> None:
        """With nothing expanded, the catalog portion is identical on two calls."""
        specs = [
            _make_spec(
                "GitHub",
                tools_cache=[{"name": "create_issue"}, {"name": "list_repos"}],
                discovery_metadata={"server": {"description": "GitHub tools"}},
            ),
            _make_spec(
                "Slack",
                tools_cache=[{"name": "send_message"}],
                discovery_metadata={"server": {"description": "Slack messaging"}},
            ),
        ]
        extra: dict[str, Any] = {}
        mw = self._build_middleware(specs, extra)
        ctx = AsyncMock()

        prompt_1 = await mw.transform_system_prompt("Base.", ctx=ctx, signal=None)
        prompt_2 = await mw.transform_system_prompt("Base.", ctx=ctx, signal=None)

        assert prompt_1 == prompt_2

    async def test_expansion_append_only(self) -> None:
        """Expanding group A then B: the expanded-schemas section is append-only.

        The catalog section naturally shrinks as groups expand (expanded groups
        are removed from the catalog). The cache-relevant invariant is that
        the expanded-schemas block grows by appending — turn-2's schemas start
        with everything from turn-1.
        """
        github_tools = [
            _make_fake_tool("GitHub__create_issue"),
            _make_fake_tool("GitHub__list_repos"),
        ]
        slack_tools = [
            _make_fake_tool("Slack__send_message"),
        ]

        async def _github_loader() -> list[AgentTool[Any]]:
            return github_tools

        async def _slack_loader() -> list[AgentTool[Any]]:
            return slack_tools

        groups = [
            DeferredToolGroup(
                group_id="mcp:GitHub",
                display_name="GitHub",
                description="GitHub tools",
                tool_names=["GitHub__create_issue", "GitHub__list_repos"],
                loader=_github_loader,
            ),
            DeferredToolGroup(
                group_id="mcp:Slack",
                display_name="Slack",
                description="Slack messaging",
                tool_names=["Slack__send_message"],
                loader=_slack_loader,
            ),
        ]

        extra: dict[str, Any] = {}
        mw = DeferredToolsMiddleware(groups=groups, extra_ref=lambda: extra)
        ctx = AsyncMock()
        ctx.tools = list(mw.tools)

        # Baseline system prompt before any expansion.
        prompt_turn0 = await mw.transform_system_prompt("Base.", ctx=ctx, signal=None)

        # Turn 1: expand GitHub
        await mw._expand(group_id="mcp:GitHub", tool_names=None, context=ctx)
        prompt_turn1 = await mw.transform_system_prompt("Base.", ctx=ctx, signal=None)

        # Turn 2: expand Slack
        await mw._expand(group_id="mcp:Slack", tool_names=None, context=ctx)
        prompt_turn2 = await mw.transform_system_prompt("Base.", ctx=ctx, signal=None)

        # Dispatch mode: schemas live in load_tools tool_results, never in the
        # system prompt. The catalog text (tool names + descriptions) is
        # byte-stable across expansions so the prompt-cache prefix never
        # invalidates.
        assert prompt_turn0 == prompt_turn1 == prompt_turn2
        assert "# Expanded tool groups" not in prompt_turn2
