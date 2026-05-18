"""MCP tool loading for the cubepi runtime (four-layer only).

Consumes :class:`MCPRuntimeConnectorSpec` from the effective service and
produces a list of :class:`cubepi.AgentTool` plus citation configs.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from collections import Counter
from datetime import timedelta
from typing import Any, Literal, cast

from cubepi.agent.types import AgentTool
from cubepi.mcp import load_mcp_tools_http
from pydantic import ValidationError

from cubebox.credentials.exceptions import CredentialNotFound
from cubebox.mcp._constants import (
    CREDENTIAL_KIND_MCP,
    CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
)
from cubebox.mcp.effective import MCPEffectiveConnectorService, MCPRuntimeConnectorSpec
from cubebox.mcp.exceptions import (
    OAuthInvalidServerState,
    OAuthRefreshContention,
    OAuthRefreshFailed,
)
from cubebox.mcp.oauth.token_manager import OAuthTokenManager
from cubebox.mcp.user_token import MCPUserTokenSigner
from cubebox.middleware.citations.config import CitationConfig
from cubebox.repositories.mcp import MCPCredentialGrantRepository
from cubebox.services.credential import CredentialService

_USER_TOKEN_TTL = timedelta(minutes=5)

MCPTransport = Literal["sse", "streamable_http"]
_VALID_TRANSPORTS: frozenset[str] = frozenset({"sse", "streamable_http"})

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
    """Produce a function-name-safe slug from an MCP install's display name."""
    slug = _NS_SLUG_RE.sub("_", server_name).strip("_")
    return slug or "mcp"


def _build_namespaced_name_with_prefix(prefix: str, tool_name: str, suffix: str = "") -> str:
    """Combine ``{prefix}{suffix}__{tool_name}`` capped at ``_NS_MAX_LEN``."""
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


async def load_workspace_mcp_tools_for_cubepi(
    *,
    effective_service: MCPEffectiveConnectorService,
    token_manager: OAuthTokenManager,
    workspace_id: str,
    org_id: str,
    user_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    grant_repo: MCPCredentialGrantRepository | None = None,
) -> tuple[list[AgentTool[Any]], dict[str, CitationConfig]]:
    """Four-layer loader: derive runtime specs from effective state, load tools.

    Per-server failures (discovery, refresh, decrypt) are caught + logged
    + skipped — one bad install must never crash the whole run's tool list.

    Auth method dispatch:

    * ``oauth`` → if ``expires_at`` is within the manager's refresh buffer,
      ask ``OAuthTokenManager.get_access_token_for_grant`` for a fresh
      access token; the manager rotates both the access-token and
      refresh-token vault rows in place and advances ``grant.expires_at``.
      Otherwise read the cached access-token credential directly. A grant
      without a ``refresh_credential_id`` falls back to the cached token
      — the effective service's pre-filter has already dropped grants
      that are both expired and unrefreshable.
    * ``static`` → fetch the vault row by ``spec.credential_id``, decrypt,
      build the Authorization header.
    * ``none`` → mint a short-lived cubebox identity JWT via
      :class:`MCPUserTokenSigner` so the MCP server can enforce tenant
      scoping.
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
                grant_repo=grant_repo,
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
            discovery = await load_mcp_tools_http(
                spec.server_url,
                headers=headers or None,
                timeout=spec.timeout,
                transport=cast(MCPTransport, spec.transport),
            )
            tools = discovery.tools
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
    grant_repo: MCPCredentialGrantRepository | None = None,
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
        access_token = await _resolve_oauth_access_token(
            spec=spec,
            cred_service=cred_service,
            token_manager=token_manager,
            grant_repo=grant_repo,
        )
        headers["Authorization"] = f"Bearer {access_token}"
        return headers

    logger.warning(
        "MCP install '%s' has unsupported auth_method %r; skipping",
        spec.name,
        spec.auth_method,
    )
    return None


async def _resolve_oauth_access_token(
    *,
    spec: MCPRuntimeConnectorSpec,
    cred_service: CredentialService,
    token_manager: OAuthTokenManager,
    grant_repo: MCPCredentialGrantRepository | None,
) -> str:
    """Return a usable access token for a four-layer OAuth grant."""
    assert spec.credential_id is not None  # caller checked

    can_refresh = (
        spec.grant is not None
        and spec.grant.refresh_credential_id is not None
        and grant_repo is not None
        and token_manager is not None
    )
    if can_refresh:
        assert spec.grant is not None and grant_repo is not None
        try:
            return await token_manager.get_access_token_for_grant(
                grant=spec.grant,
                grant_repo=grant_repo,
                server_url=spec.server_url,
                oauth_client_config=spec.oauth_client_config,
            )
        except OAuthRefreshContention:
            logger.warning(
                "MCP install '%s' OAuth refresh contention; using cached access token",
                spec.name,
            )
        except OAuthRefreshFailed as exc:
            logger.warning(
                "MCP install '%s' OAuth refresh failed (status=%s error=%s); "
                "grant marked expired, falling back to cached token for this run",
                spec.name,
                exc.status,
                exc.error,
            )
        except OAuthInvalidServerState as exc:
            logger.warning(
                "MCP install '%s' OAuth refresh aborted: %s; using cached access token",
                spec.name,
                exc,
            )

    return await cred_service.get_decrypted(
        credential_id=spec.credential_id,
        requesting_kind=CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
    )
