"""MCP progressive disclosure — config, threshold gate, deferred groups."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal, cast

from cubepi.agent.types import AgentTool
from cubepi.deferred import DeferredToolGroup

from cubebox.config import config
from cubebox.mcp._constants import slugify_for_namespace
from cubebox.mcp.cubepi_runtime import (
    _NS_LENGTH_DEFENCE,
    _build_namespaced_name_with_prefix,
    _load_tools_for_specs_deferred,
)
from cubebox.mcp.effective import MCPRuntimeConnectorSpec
from cubebox.middleware.citations.config import CitationConfig


@dataclass(frozen=True)
class DisclosureSettings:
    enabled: Literal["auto", "on", "off"] = "auto"
    threshold_pct: float = 10.0
    min_servers: int = 2


_EnabledLiteral = Literal["auto", "on", "off"]


def load_disclosure_settings() -> DisclosureSettings:
    raw_enabled = str(config.get("mcp.progressive_disclosure.enabled", "auto"))
    if raw_enabled not in ("auto", "on", "off"):
        raw_enabled = "auto"
    return DisclosureSettings(
        enabled=cast(_EnabledLiteral, raw_enabled),
        threshold_pct=float(
            config.get("mcp.progressive_disclosure.threshold_pct", 10.0),
        ),
        min_servers=int(
            config.get("mcp.progressive_disclosure.min_servers", 2),
        ),
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


# ---------------------------------------------------------------------------
# Deferred-group helpers
# ---------------------------------------------------------------------------


def _spec_description(spec: MCPRuntimeConnectorSpec) -> str:
    """One-line description from discovery metadata, falling back to name."""
    server = (spec.discovery_metadata or {}).get("server") or {}
    desc: str = server.get("description") or server.get("summary") or ""
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
) -> tuple[list[DeferredToolGroup], dict[str, CitationConfig]]:
    """Convert MCP runtime specs into cubepi DeferredToolGroup objects.

    Returns (groups, citation_configs). citation_configs is populated when
    loader callbacks run (i.e., when the model calls load_tools).

    loader_kwargs carries session-independent factory ingredients forwarded
    to _load_tools_for_specs_deferred (workspace_id, org_id, user_id,
    encryption_backend, http_client, metadata_discovery, redis, signer).
    Each loader creates its own short-lived DB session.
    """
    shared_citations: dict[str, CitationConfig] = {}
    groups: list[DeferredToolGroup] = []

    for spec in specs:
        slug = slugify_for_namespace(spec.name)
        tool_names = _compute_namespaced_tool_names(spec, all_specs)

        async def _loader(
            _s: MCPRuntimeConnectorSpec = spec,
            _all: list[MCPRuntimeConnectorSpec] = all_specs,
            _kw: dict[str, Any] = loader_kwargs,
            _cit: dict[str, CitationConfig] = shared_citations,
        ) -> list[AgentTool[Any]]:
            tools, citations = await _load_tools_for_specs_deferred(
                specs=[_s],
                all_specs=_all,
                **_kw,
            )
            _cit.update(citations)
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

    return groups, shared_citations
