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

from cubeplex.credentials.exceptions import CredentialNotFound
from cubeplex.mcp._constants import (
    CREDENTIAL_KIND_MCP,
    CREDENTIAL_KIND_MCP_OAUTH_ACCESS_TOKEN,
)
from cubeplex.mcp.effective import MCPEffectiveConnectorService, MCPRuntimeConnectorSpec
from cubeplex.mcp.exceptions import (
    OAuthInvalidServerState,
    OAuthRefreshContention,
    OAuthRefreshFailed,
)
from cubeplex.mcp.oauth.token_manager import OAuthTokenManager
from cubeplex.mcp.user_token import MCPUserTokenSigner
from cubeplex.middleware.citations.config import CitationConfig
from cubeplex.repositories.mcp import MCPCredentialGrantRepository
from cubeplex.services.credential import CredentialService

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


# Canonical definition lives in ``cubeplex.mcp._constants`` so the
# event-listener that populates ``MCPConnector.slug_name`` and
# this runtime namespacing math share one helper. The leading
# underscore is preserved as a private alias inside this module so
# existing in-module call sites keep their style.
from cubeplex.mcp._constants import slugify_for_namespace  # noqa: E402

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
    * ``none`` → mint a short-lived cubeplex identity JWT via
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


def _build_tools_from_cache(
    *,
    spec: MCPRuntimeConnectorSpec,
    headers: dict[str, str],
    server_url: str,
) -> list[AgentTool[Any]] | None:
    """Build executable AgentTools from the install's persisted ``tools_cache``.

    Skips the per-send ``initialize`` + ``tools/list`` round trip: the cache
    already carries every field the live loader would extract from the
    descriptor (name / description / input_schema), and cubepi MCP tools
    open a fresh session per ``tools/call`` anyway, so execution is
    identical to a live-discovered tool.

    Returns None when the cache is unusable (empty, or cubepi's private
    helpers moved) — callers fall back to the live loader.

    NOTE: reaches into ``cubepi.mcp``'s private modules for the session
    opener and result serializer the live loader uses. cubepi doesn't yet
    expose "build tool from cached descriptor" publicly; upstream that
    instead of growing this.
    """
    if not spec.tools_cache:
        return None
    try:
        from cubepi.mcp._adapter import make_mcp_agent_tool
        from cubepi.mcp.http_loader import (
            _open_session,
            _serialize_call_tool_response,
            _split_address,
        )
    except ImportError as exc:  # cubepi internals moved — live loader still works
        logger.warning("tools_cache fast path unavailable (cubepi drift): %s", exc)
        return None

    timeout = spec.timeout
    transport = cast(MCPTransport, spec.transport)

    async def _call_remote(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        # Mirrors cubepi.mcp.http_loader's per-call session semantics,
        # including W3C traceparent propagation into the MCP server.
        from cubepi.mcp._tracing import current_traceparent

        call_headers = headers or None
        tp = current_traceparent()
        if tp is not None:
            call_headers = {**(headers or {}), "traceparent": tp}
        async with _open_session(
            server_url, headers=call_headers, timeout=timeout, transport=transport
        ) as (session, _get_session_id):
            await asyncio.wait_for(session.initialize(), timeout=timeout)
            resp = await asyncio.wait_for(session.call_tool(tool_name, args), timeout=timeout)
            return _serialize_call_tool_response(resp)

    address, port = _split_address(server_url)
    tools: list[AgentTool[Any]] = []
    for entry in spec.tools_cache:
        name = entry.get("name")
        if not name:
            continue
        try:
            tools.append(
                make_mcp_agent_tool(
                    name=name,
                    description=entry.get("description") or "",
                    input_schema=entry.get("input_schema") or {"type": "object", "properties": {}},
                    call_remote=_call_remote,
                    server_address=address,
                    server_port=port,
                )
            )
        except Exception as exc:  # noqa: BLE001 — one bad cached schema must not sink the rest
            logger.warning("tools_cache entry %s/%s failed to build: %s", spec.name, name, exc)
    return tools or None


_cache_refresh_in_flight: set[str] = set()


def schedule_tools_cache_refresh(
    *,
    specs: list[MCPRuntimeConnectorSpec],
    actor_user_id: str,
    encryption_backend: Any,
    http_client: Any,
    metadata_discovery: Any,
    redis: Any,
    signer: MCPUserTokenSigner,
) -> None:
    """Kick detached re-discovery for installs whose ``tools_cache`` is stale.

    Never blocks or raises into the send path. Debounced per install via an
    in-process set; the TTL gate (``mcp.tools_cache_ttl_hours``, default 24)
    bounds cross-process stampedes to one refresh per process per TTL.
    """
    from datetime import UTC, datetime
    from datetime import timedelta as _td

    from cubeplex.config import config as _cfg

    try:
        ttl_hours = float(_cfg.get("mcp.tools_cache_ttl_hours", 24))
    except Exception:  # noqa: BLE001
        ttl_hours = 24.0
    if ttl_hours <= 0:
        return
    now = datetime.now(UTC)

    async def _refresh_one(spec: MCPRuntimeConnectorSpec) -> None:
        from cubeplex.credentials.dependencies import build_credential_service
        from cubeplex.db.engine import async_session_maker
        from cubeplex.repositories.credential import CredentialRepository
        from cubeplex.services.mcp_discovery import run_post_grant_discovery

        try:
            async with async_session_maker() as session:
                cred_service = build_credential_service(
                    session,
                    encryption_backend,
                    org_id=spec.org_id,
                    actor_user_id=actor_user_id,
                )
                token_mgr = OAuthTokenManager(
                    http_client=http_client,
                    redis=redis,
                    encryption_backend=encryption_backend,
                    credential_repo=CredentialRepository(session, org_id=spec.org_id),
                    metadata=metadata_discovery,
                )
                await run_post_grant_discovery(
                    connector_id=spec.connector_id,
                    workspace_id=spec.workspace_id or None,
                    actor_user_id=actor_user_id,
                    session=session,
                    cred_service=cred_service,
                    signer=signer,
                    token_mgr=token_mgr,
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 — background refresh must never surface
            logger.warning("tools_cache refresh failed for %s: %s", spec.connector_id, exc)
        finally:
            _cache_refresh_in_flight.discard(spec.connector_id)

    for spec in specs:
        if not spec.tools_cache:
            continue  # nothing cached — the send path already live-loads
        last = spec.last_discovered_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last is not None and now - last < _td(hours=ttl_hours):
            continue
        if spec.connector_id in _cache_refresh_in_flight:
            continue
        _cache_refresh_in_flight.add(spec.connector_id)
        task = asyncio.create_task(
            _refresh_one(spec), name=f"mcp-cache-refresh:{spec.connector_id}"
        )
        # Detached by design; reference kept alive by the event loop via the
        # in-flight set lifecycle inside _refresh_one.
        del task


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
        s.connector_id: _slugify_for_namespace(s.name) for s in all_specs
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

        # Cache-first: build tools from the persisted tools_cache and skip
        # the live initialize+tools/list round trip. Falls back to live
        # discovery when the cache is empty/unusable (e.g. first run before
        # discovery persisted, or cubepi internals drifted).
        tools = _build_tools_from_cache(spec=spec, headers=headers, server_url=server_url)
        if tools is None:
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
                    spec.connector_id,
                    exc,
                )
                continue

        slug = proposed_slugs[spec.connector_id]
        explicit_collision = slug_counts[slug] > 1
        risky_truncation = len(slug) > _NS_LENGTH_DEFENCE
        if explicit_collision or risky_truncation:
            safe = spec.connector_id.replace("-", "")
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
    from cubeplex.credentials.dependencies import build_credential_service
    from cubeplex.db.engine import async_session_maker
    from cubeplex.repositories.credential import CredentialRepository
    from cubeplex.repositories.mcp import MCPCredentialGrantRepository as _GrantRepo

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
            mcp_server_id=spec.connector_id,
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
