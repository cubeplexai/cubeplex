"""MCP tool loading for the cubepi runtime (M2.4).

Two loaders coexist here during the four-layer migration:

* :func:`load_workspace_mcp_tools_for_cubepi` — the legacy path, reads from
  ``mcp_servers`` / ``workspace_mcp_overrides`` / ``user_mcp_credentials``.
  Kept until Task 9 of the four-layer plan removes the legacy tables.
* :func:`load_workspace_mcp_tools_from_effective` — the new path, reads from
  the four-layer model via :class:`MCPEffectiveConnectorService`. The run
  manager calls this when an :class:`MCPEffectiveConnectorService` is
  available; the legacy path remains the fallback while routes migrate.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from collections import Counter
from datetime import timedelta
from typing import Any, cast

from cubepi.agent.types import AgentTool
from cubepi.mcp import load_mcp_tools_http
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.exceptions import CredentialNotFound
from cubebox.mcp._constants import (
    CREDENTIAL_KIND_MCP,
    CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
)
from cubebox.mcp.cubepi_discovery import (
    _VALID_TRANSPORTS,
    CubepiMCPServerSpec,
    MCPTransport,
    discover_workspace_mcp_servers_for_cubepi,
)
from cubebox.mcp.effective import MCPEffectiveConnectorService, MCPRuntimeConnectorSpec
from cubebox.mcp.oauth.token_manager import OAuthTokenManager
from cubebox.mcp.user_token import MCPUserTokenSigner
from cubebox.middleware.citations.config import CitationConfig
from cubebox.services.credential import CredentialService

_USER_TOKEN_TTL = timedelta(minutes=5)

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
    Each server's transport (``sse`` or ``streamable_http``) is forwarded
    to cubepi's loader so the per-run path matches whatever wire format
    the server actually speaks.
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
                transport=spec.transport,
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


async def load_workspace_mcp_tools_from_effective(
    *,
    effective_service: MCPEffectiveConnectorService,
    token_manager: OAuthTokenManager,
    workspace_id: str,
    org_id: str,
    user_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
) -> tuple[list[AgentTool[Any]], dict[str, CitationConfig]]:
    """Four-layer loader: derive runtime specs from effective state, load tools.

    Mirrors :func:`load_workspace_mcp_tools_for_cubepi` but consumes
    :class:`MCPRuntimeConnectorSpec` instead of legacy
    :class:`CubepiMCPServerSpec`. Per-server failures (discovery, refresh,
    decrypt) are caught + logged + skipped exactly as before — one bad
    install must never crash the whole run's tool list.

    Auth method dispatch:

    * ``oauth`` → no-op for now (the legacy ``OAuthTokenManager`` takes an
      ``MCPServer`` row, not a four-layer install; once Task 9 ports the
      manager to ``MCPCredentialGrant``, this branch calls
      ``token_manager.get_access_token(...)``). Until then we fall back to
      reading the access-token credential directly out of the grant row,
      which is enough for non-refresh flows.
    * ``static`` → fetch the vault row by ``spec.credential_id``, decrypt,
      build the Authorization header.
    * ``none`` → mint a short-lived cubebox identity JWT via
      :class:`MCPUserTokenSigner` (same helper the legacy passthrough
      branch already uses).
    """
    specs = await effective_service.list_runtime_specs(workspace_id, user_id)

    # Pre-compute proposed slugs to detect cross-server collisions.
    proposed_slugs: dict[str, str] = {
        spec.install_id: _slugify_for_namespace(spec.name) for spec in specs
    }
    slug_counts: Counter[str] = Counter(proposed_slugs.values())

    all_tools: list[AgentTool[Any]] = []
    all_citations: dict[str, CitationConfig] = {}

    for spec in specs:
        try:
            headers = await _resolve_headers_from_spec(
                spec=spec,
                workspace_id=workspace_id,
                org_id=org_id,
                user_id=user_id,
                cred_service=cred_service,
                signer=signer,
                token_manager=token_manager,
            )
        except CredentialNotFound:
            logger.warning(
                "MCP install '%s' references a missing credential; skipping",
                spec.name,
            )
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "MCP install '%s' credential resolution failed: %s; skipping",
                spec.name,
                exc,
            )
            continue

        if headers is None:
            # Credential resolution returned no token for a non-passthrough
            # spec — the effective service should already have flagged this
            # spec as unusable, but defend in depth.
            continue

        if spec.transport not in _VALID_TRANSPORTS:
            logger.warning(
                "MCP install '%s' has unsupported transport %r; skipping",
                spec.name,
                spec.transport,
            )
            continue

        try:
            tools = await load_mcp_tools_http(
                spec.server_url,
                headers=headers or None,
                timeout=spec.timeout,
                transport=cast(MCPTransport, spec.transport),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to load MCP install %s (%s): %s",
                spec.name,
                spec.install_id,
                exc,
            )
            continue

        # Slug + collision suffix logic mirrors the legacy loader so the
        # cache-prefix tool name format stays identical across paths.
        slug = proposed_slugs[spec.install_id]
        explicit_collision = slug_counts[slug] > 1
        risky_truncation = len(slug) > _NS_LENGTH_DEFENCE
        if explicit_collision or risky_truncation:
            safe = spec.install_id.replace("-", "")
            suffix = f"_{safe[-4:] if len(safe) >= 4 else safe}"
        else:
            suffix = ""

        for tool in tools:
            bare_name = tool.name
            namespaced_name = _build_namespaced_name_with_prefix(slug, bare_name, suffix=suffix)
            namespaced = dataclasses.replace(tool, name=namespaced_name)
            all_tools.append(namespaced)
            raw = spec.tool_citations.get(bare_name)
            if raw is None:
                continue
            try:
                all_citations[namespaced_name] = CitationConfig(**raw)
            except ValidationError as exc:
                logger.warning(
                    "Bad tool_citations on %s/%s: %s — skipping",
                    spec.name,
                    bare_name,
                    exc,
                )

    return all_tools, all_citations


async def _resolve_headers_from_spec(
    *,
    spec: MCPRuntimeConnectorSpec,
    workspace_id: str,
    org_id: str,
    user_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    token_manager: OAuthTokenManager,
) -> dict[str, str] | None:
    """Resolve the auth header for a runtime spec by ``auth_method``.

    Returns ``None`` when a credential is required but cannot be resolved.
    The empty-dict return for passthrough spans the case where the loader
    has the identity token in hand but no other headers configured.
    """
    headers: dict[str, str] = dict(spec.headers or {})

    if spec.auth_method == "none":
        token = await signer.sign(
            user_id=user_id,
            org_id=org_id,
            workspace_id=workspace_id,
            mcp_server_id=spec.install_id,
            ttl=_USER_TOKEN_TTL,
        )
        headers["Authorization"] = f"Bearer {token}"
        return headers

    if spec.credential_id is None:
        return None

    if spec.auth_method == "static":
        plaintext = await cred_service.get_decrypted(
            credential_id=spec.credential_id,
            requesting_kind=CREDENTIAL_KIND_MCP,
        )
        headers["Authorization"] = f"Bearer {plaintext}"
        return headers

    if spec.auth_method == "oauth":
        # Token manager refresh integration with the four-layer schema is
        # tracked as part of Task 9; for now we read the access-token
        # credential directly. The effective service has already filtered
        # out grants whose status is "expired" with no refresh credential,
        # so the worst case here is a near-expiry token — acceptable for
        # the first cut of the four-layer runtime path.
        del token_manager  # tracked for future refresh wiring
        plaintext = await cred_service.get_decrypted(
            credential_id=spec.credential_id,
            requesting_kind=CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
        )
        headers["Authorization"] = f"Bearer {plaintext}"
        return headers

    logger.warning(
        "MCP install '%s' has unsupported auth_method %r; skipping",
        spec.name,
        spec.auth_method,
    )
    return None
