"""MCP tool loading for the cubepi runtime (M2.4)."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from collections import Counter
from typing import Any

from cubepi.agent.types import AgentTool
from cubepi.mcp import load_mcp_tools_http
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.mcp.cubepi_discovery import (
    CubepiMCPServerSpec,
    discover_workspace_mcp_servers_for_cubepi,
)
from cubebox.mcp.user_token import MCPUserTokenSigner
from cubebox.middleware.citations.config import CitationConfig
from cubebox.services.credential import CredentialService

logger = logging.getLogger(__name__)

_NS_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")
_NS_MAX_LEN = 64  # OpenAI strict function-name max length
_NS_LENGTH_DEFENCE = 32
"""Slug length threshold above which we always append an id-disambiguator,
even without an explicit slug collision. Defends against post-truncation
collisions when two long, distinct slugs share initial characters: the
length cap can otherwise collapse them to the same final prefix.
"""


def _slugify_for_namespace(server_name: str) -> str:
    """Produce a function-name-safe slug from an MCP server's display name.

    Replaces runs of non-[A-Za-z0-9] with underscore; strips leading/trailing
    underscores; falls back to "mcp" if empty.
    """
    slug = _NS_SLUG_RE.sub("_", server_name).strip("_")
    return slug or "mcp"


def _server_id_suffix(server_id: str, length: int = 4) -> str:
    """Derive a stable function-name-safe suffix from an MCP server id.

    server.id is shaped like "mcp-V1StGXR8Z5jdHi"; we take the last ``length``
    characters after stripping hyphens. Unique enough for the rare case of two
    installs sharing the same display name without bloating the namespaced tool name.
    """
    safe = server_id.replace("-", "")
    return safe[-length:] if len(safe) >= length else safe


def _build_namespaced_name_with_prefix(prefix: str, tool_name: str, suffix: str = "") -> str:
    """Combine ``{prefix}{suffix}__{tool_name}`` capped at ``_NS_MAX_LEN``.

    If the combined name overflows, only ``prefix`` is truncated; ``suffix``
    (the collision disambiguator) and the full tool name are preserved.
    """
    combined = f"{prefix}{suffix}__{tool_name}"
    if len(combined) <= _NS_MAX_LEN:
        return combined
    budget = _NS_MAX_LEN - len(tool_name) - len(suffix) - 2  # 2 for "__"
    if budget < 1:
        return tool_name[:_NS_MAX_LEN]
    return f"{prefix[:budget]}{suffix}__{tool_name}"


def _build_namespaced_name(server_name: str, tool_name: str) -> str:
    """Return ``{slug}__{tool_name}`` with total length <= 64."""
    return _build_namespaced_name_with_prefix(_slugify_for_namespace(server_name), tool_name)


def _compute_slug_and_suffix_for(
    spec: CubepiMCPServerSpec,
    slug_counts: Counter[str],
    proposed_slugs: dict[str, str],
) -> tuple[str, str]:
    """Return ``(slug, suffix)`` for a spec.

    On collision the suffix carries the id-derived disambiguator (e.g. ``_aaaa``);
    on a clean name the suffix is empty. Keeping them separate lets the truncation
    path in ``_build_namespaced_name_with_prefix`` preserve the suffix even when
    the slug must be shortened.
    """
    slug = proposed_slugs[spec.server_id]
    explicit_collision = slug_counts[slug] > 1
    risky_truncation = len(slug) > _NS_LENGTH_DEFENCE
    if explicit_collision or risky_truncation:
        return slug, f"_{_server_id_suffix(spec.server_id)}"
    return slug, ""


async def load_workspace_mcp_tools_for_cubepi(
    *,
    session: AsyncSession,
    workspace_id: str,
    org_id: str,
    user_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
) -> tuple[list[AgentTool[Any]], dict[str, CitationConfig]]:
    """Load all enabled MCP servers' tools for a workspace as cubepi.AgentTool.

    Tool names are namespaced as ``{server_name}__{tool_name}`` so two MCP
    servers can ship the same bare tool name without colliding in the
    agent's tool list. The returned ``CitationConfig`` dict uses the same
    namespaced keys.

    When two servers produce the same slug (e.g. two installs both named
    "WebTools"), a short id-derived suffix is appended to each colliding
    prefix so names remain unique. The suffix is only added when there is
    an actual collision; the common case produces clean names.

    Per-server failures are caught and logged, never aborting the load.
    Only HTTP/SSE transports are supported.
    """
    servers = await discover_workspace_mcp_servers_for_cubepi(
        session=session,
        workspace_id=workspace_id,
        org_id=org_id,
        user_id=user_id,
        cred_service=cred_service,
        signer=signer,
    )

    # Pre-compute every spec's proposed slug; detect collisions across the load.
    proposed_slugs: dict[str, str] = {
        spec.server_id: _slugify_for_namespace(spec.server_name) for spec in servers
    }
    slug_counts: Counter[str] = Counter(proposed_slugs.values())

    all_tools: list[AgentTool[Any]] = []
    all_citations: dict[str, CitationConfig] = {}
    for spec in servers:
        try:
            tools = await load_mcp_tools_http(
                spec.url,
                headers=spec.headers or None,
                timeout=30.0,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to load MCP server %s (%s): %s",
                spec.server_name,
                spec.server_id,
                exc,
            )
            continue

        slug, suffix = _compute_slug_and_suffix_for(spec, slug_counts, proposed_slugs)
        for tool in tools:
            bare_name = tool.name
            namespaced_name = _build_namespaced_name_with_prefix(slug, bare_name, suffix=suffix)
            namespaced = dataclasses.replace(tool, name=namespaced_name)
            all_tools.append(namespaced)
            raw = spec.tool_citations.get(bare_name)
            if raw is None:
                continue
            try:
                all_citations[namespaced.name] = CitationConfig(**raw)
            except ValidationError as exc:
                logger.warning(
                    "Bad tool_citations on %s/%s: %s — skipping",
                    spec.server_name,
                    bare_name,
                    exc,
                )

    return all_tools, all_citations
