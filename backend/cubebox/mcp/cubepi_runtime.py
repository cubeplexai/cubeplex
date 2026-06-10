"""MCP tool loading for the cubepi runtime (four-layer only).

Consumes :class:`MCPRuntimeConnectorSpec` from the effective service and
produces a list of :class:`cubepi.AgentTool` plus citation configs.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections import Counter
from datetime import timedelta
from typing import Any, Literal, cast
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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

_NS_MAX_LEN = 64  # OpenAI strict function-name max length
_NS_LENGTH_DEFENCE = 32
"""Slug length threshold above which we always append an id-disambiguator,
even without an explicit slug collision. Defends against post-truncation
collisions when two long, distinct slugs share initial characters: the
length cap can otherwise collapse them to the same final prefix.
"""


# Canonical definition lives in ``cubebox.mcp._constants`` so the
# event-listener that populates ``MCPConnectorInstall.slug_name`` and
# this runtime namespacing math share one helper. The leading
# underscore is preserved as a private alias inside this module so
# existing in-module call sites keep their style.
from cubebox.mcp._constants import slugify_for_namespace  # noqa: E402

_slugify_for_namespace = slugify_for_namespace


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
    return await _load_tools_for_specs(
        specs=specs,
        all_specs=specs,
        workspace_id=workspace_id,
        org_id=org_id,
        user_id=user_id,
        cred_service=cred_service,
        signer=signer,
        token_manager=token_manager,
        grant_repo=grant_repo,
    )


async def _load_tools_for_specs(
    *,
    specs: list[MCPRuntimeConnectorSpec],
    all_specs: list[MCPRuntimeConnectorSpec],
    workspace_id: str,
    org_id: str,
    user_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    token_manager: OAuthTokenManager,
    grant_repo: MCPCredentialGrantRepository | None = None,
) -> tuple[list[AgentTool[Any]], dict[str, CitationConfig]]:
    """Load and namespace MCP tools for a subset of runtime specs.

    ``all_specs`` is the full workspace spec set used for slug collision
    detection; ``specs`` is the subset to actually load.
    """
    proposed_slugs: dict[str, str] = {
        s.install_id: _slugify_for_namespace(s.name) for s in all_specs
    }
    slug_counts: Counter[str] = Counter(proposed_slugs.values())

    all_tools: list[AgentTool[Any]] = []
    all_citations: dict[str, CitationConfig] = {}

    for spec in specs:
        try:
            resolved = await _resolve_auth_from_spec(
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

        if resolved is None:
            continue
        headers, server_url = resolved

        if spec.transport not in _VALID_TRANSPORTS:
            logger.warning(
                "MCP install '%s' has unsupported transport %r; skipping",
                spec.name,
                spec.transport,
            )
            continue

        try:
            discovery = await load_mcp_tools_http(
                server_url,
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
            namespaced_name = _build_namespaced_name_with_prefix(
                slug,
                bare_name,
                suffix=suffix,
            )
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


async def _load_tools_for_specs_deferred(
    *,
    specs: list[MCPRuntimeConnectorSpec],
    all_specs: list[MCPRuntimeConnectorSpec],
    workspace_id: str,
    org_id: str,
    user_id: str,
    encryption_backend: Any,
    http_client: Any,
    metadata_discovery: Any,
    redis: Any,
    signer: MCPUserTokenSigner,
) -> tuple[list[AgentTool[Any]], dict[str, CitationConfig]]:
    """Load tools in a self-contained DB session — for deferred group loaders.

    The eager path holds a session open for the duration of the MCP load
    block.  Deferred loaders run later during the agent loop, long after
    that session has closed.  This wrapper creates a short-lived session
    per invocation so each group expansion is self-contained.
    """
    from cubebox.credentials.dependencies import build_credential_service
    from cubebox.db.engine import async_session_maker
    from cubebox.repositories.credential import CredentialRepository
    from cubebox.repositories.mcp import MCPCredentialGrantRepository as _GrantRepo

    async with async_session_maker() as session:
        cred_service = build_credential_service(
            session,
            encryption_backend,
            org_id=org_id,
            actor_user_id=user_id,
        )
        token_manager = OAuthTokenManager(
            http_client=http_client,
            redis=redis,
            encryption_backend=encryption_backend,
            credential_repo=CredentialRepository(session, org_id=org_id),
            metadata=metadata_discovery,
        )
        grant_repo = _GrantRepo(session, org_id=org_id)
        return await _load_tools_for_specs(
            specs=specs,
            all_specs=all_specs,
            workspace_id=workspace_id,
            org_id=org_id,
            user_id=user_id,
            cred_service=cred_service,
            signer=signer,
            token_manager=token_manager,
            grant_repo=grant_repo,
        )


def _inject_query_param(server_url: str, name: str, value: str) -> str:
    """Append ``name=value`` to ``server_url``'s query string.

    Existing query params are preserved; an existing param with the same
    ``name`` is replaced rather than duplicated. The streamable_http
    transport sends every JSON-RPC request to this URL, so the param rides
    along on every call — exactly what Tavily/Bocha-style URL-key auth
    expects.

    ``parse_qsl`` decodes existing pairs before ``urlencode`` re-encodes
    them, so a pre-encoded existing value (``foo=a%20b``) round-trips
    intact instead of double-encoding the ``%``. ``keep_blank_values``
    preserves valueless flags (``?debug``) as empty-string values.
    """
    parts = urlparse(server_url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != name]
    pairs.append((name, value))
    return urlunparse(parts._replace(query=urlencode(pairs)))


def _apply_static_credential(
    *,
    spec: MCPRuntimeConnectorSpec,
    headers: dict[str, str],
    server_url: str,
    plaintext: str,
) -> tuple[dict[str, str], str]:
    """Place ``plaintext`` on ``headers`` or ``server_url`` per ``spec``."""
    style = spec.static_auth_style or "bearer"
    if style == "bearer":
        headers["Authorization"] = f"Bearer {plaintext}"
        return headers, server_url
    if style == "header":
        name = spec.static_auth_header_name
        if not name:
            logger.warning(
                "MCP install '%s' has static_auth_style='header' but no header name; "
                "falling back to Authorization: Bearer",
                spec.name,
            )
            headers["Authorization"] = f"Bearer {plaintext}"
            return headers, server_url
        headers[name] = plaintext
        return headers, server_url
    if style == "query":
        name = spec.static_auth_query_param
        if not name:
            logger.warning(
                "MCP install '%s' has static_auth_style='query' but no param name; "
                "falling back to Authorization: Bearer",
                spec.name,
            )
            headers["Authorization"] = f"Bearer {plaintext}"
            return headers, server_url
        return headers, _inject_query_param(server_url, name, plaintext)
    logger.warning(
        "MCP install '%s' has unsupported static_auth_style %r; "
        "falling back to Authorization: Bearer",
        spec.name,
        style,
    )
    headers["Authorization"] = f"Bearer {plaintext}"
    return headers, server_url


async def _resolve_auth_from_spec(
    *,
    spec: MCPRuntimeConnectorSpec,
    workspace_id: str,
    org_id: str,
    user_id: str,
    cred_service: CredentialService,
    signer: MCPUserTokenSigner,
    token_manager: OAuthTokenManager,
    grant_repo: MCPCredentialGrantRepository | None = None,
) -> tuple[dict[str, str], str] | None:
    """Resolve auth headers AND the (possibly rewritten) server URL.

    Returns ``None`` when a credential is required but cannot be resolved.
    The static path branches on ``spec.static_auth_style`` so query-param
    auth can rewrite the URL while header-style auth only touches the
    header dict — see :func:`_apply_static_credential`.
    """
    headers: dict[str, str] = dict(spec.headers or {})
    server_url = spec.server_url

    if spec.auth_method == "none":
        token = await signer.sign(
            user_id=user_id,
            org_id=org_id,
            workspace_id=workspace_id,
            mcp_server_id=spec.install_id,
            ttl=_USER_TOKEN_TTL,
        )
        headers["Authorization"] = f"Bearer {token}"
        return headers, server_url

    if spec.credential_id is None:
        return None

    if spec.auth_method == "static":
        plaintext = await cred_service.get_decrypted(
            credential_id=spec.credential_id,
            requesting_kind=CREDENTIAL_KIND_MCP,
        )
        return _apply_static_credential(
            spec=spec,
            headers=headers,
            server_url=server_url,
            plaintext=plaintext,
        )

    if spec.auth_method == "oauth":
        access_token = await _resolve_oauth_access_token(
            spec=spec,
            cred_service=cred_service,
            token_manager=token_manager,
            grant_repo=grant_repo,
        )
        headers["Authorization"] = f"Bearer {access_token}"
        return headers, server_url

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
