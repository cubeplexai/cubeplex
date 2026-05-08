"""OAuth ``/oauth/start`` service — authorize-URL builder + DCR + state + PKCE.

Given an already-installed ``MCPServer`` row in ``auth_method=oauth``
state, this service:

1. Resolves AS metadata for the server's protected resource.
2. If the catalog declares ``oauth_dcr_supported=True`` and the install
   has no ``client_id`` yet, performs RFC 7591 dynamic client
   registration. Any returned ``client_secret`` is encrypted into the
   credential vault under kind ``mcp_oauth_client_secret``.
3. Otherwise, populates ``oauth_client_config`` from the catalog's
   static ``oauth_static_client_id`` / ``oauth_static_client_secret_credential_id``.
4. Generates a PKCE verifier+challenge and writes the verifier to
   redis under ``mcp_oauth_pkce:{install_id}`` (TTL 300s).
5. Issues a one-shot HMAC-signed state token via ``OAuthStateStore``.
6. Generates a 32-byte hex callback ticket and stores
   ``mcp_oauth_callback_ticket:{ticket} -> actor_user_id`` in redis
   (TTL 600s) — this binds the cookie set on the start response to
   the eventual GET callback.
7. Builds the AS authorize URL and returns the ticket so the route
   layer can set the cookie.

Per-install OAuth client secrets are persisted under their own
credential kind (``mcp_oauth_client_secret``) — distinct from access
and refresh tokens — so a misuse of ``CredentialService.get_decrypted``
can't pull the wrong row.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from redis.asyncio import Redis

from cubebox.mcp.exceptions import (
    MCPCatalogConnectorNotFound,
    MCPServerNotFound,
    OAuthInvalidServerState,
)
from cubebox.mcp.oauth.callback import PKCE_REDIS_KEY_PREFIX
from cubebox.mcp.oauth.dcr import DCRClient, DCRRequest
from cubebox.mcp.oauth.metadata import OAuthMetadataDiscovery
from cubebox.mcp.oauth.pkce import generate_pkce
from cubebox.mcp.oauth.state import OAuthStateStore
from cubebox.repositories.mcp import MCPServerRepository
from cubebox.repositories.mcp_catalog import MCPCatalogConnectorRepository
from cubebox.services.credential import CredentialService

CALLBACK_TICKET_REDIS_KEY_PREFIX = "mcp_oauth_callback_ticket:"
CALLBACK_TICKET_COOKIE_NAME = "cubebox_mcp_oauth_ticket"
CALLBACK_TICKET_TTL_SECONDS = 600
PKCE_REDIS_TTL_SECONDS = 300

# Distinct credential kind for per-install OAuth confidential-client
# secrets returned by RFC 7591 DCR. Kept apart from
# ``mcp_oauth_access_token`` / ``mcp_oauth_refresh_token`` so a
# kind-mismatch guard catches cross-fetch mistakes. Static catalog
# client secrets share this kind; the seeder writes them under it too
# (``catalog_seed._OAUTH_CLIENT_SECRET_KIND``).
CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET = "mcp_oauth_client_secret"


@dataclass(frozen=True)
class OAuthStartResult:
    """Output of ``OAuthStartService.start``.

    The route layer sets ``cookie_value`` as the ``cubebox_mcp_oauth_ticket``
    cookie (HttpOnly, SameSite=Lax, ``Path=/api/v1/oauth/mcp/callback``).
    """

    authorize_url: str
    state: str
    cookie_value: str


class OAuthStartService:
    """Build the authorize URL and bootstrap state for a single install."""

    def __init__(
        self,
        *,
        server_repo: MCPServerRepository,
        catalog_repo: MCPCatalogConnectorRepository,
        metadata: OAuthMetadataDiscovery,
        dcr_client: DCRClient,
        state_store: OAuthStateStore,
        credential_service: CredentialService,
        redis: Redis,
        redirect_uri: str,
        org_id: str,
    ) -> None:
        self._server_repo = server_repo
        self._catalog_repo = catalog_repo
        self._metadata = metadata
        self._dcr = dcr_client
        self._state_store = state_store
        self._cred_service = credential_service
        self._redis = redis
        self._redirect_uri = redirect_uri
        self._org_id = org_id

    @property
    def server_repo(self) -> MCPServerRepository:
        """Expose the underlying repo so route layers can pre-validate
        ownership / creator-only rules before kicking off DCR."""
        return self._server_repo

    async def start(
        self,
        *,
        install_id: str,
        actor_user_id: str,
    ) -> OAuthStartResult:
        server = await self._server_repo.get(install_id)
        if server is None or server.org_id != self._org_id:
            raise MCPServerNotFound(install_id)
        if server.auth_method != "oauth":
            raise OAuthInvalidServerState(
                f"install {install_id} auth_method={server.auth_method!r} (expected oauth)"
            )

        if server.catalog_connector_id is None:
            # OAuth installs are catalog-bound by construction (Phase 2/3).
            # Hand-rolled OAuth servers are out of scope for v1.
            raise OAuthInvalidServerState(
                f"install {install_id} has no catalog_connector_id; "
                "hand-rolled OAuth installs are not supported"
            )
        connector = await self._catalog_repo.get_by_id(server.catalog_connector_id)
        if connector is None:
            raise MCPCatalogConnectorNotFound(server.catalog_connector_id)

        _, as_meta = await self._metadata.discover_for_resource(server.server_url)

        client_config: dict[str, Any] = dict(server.oauth_client_config or {})
        scope: str | None = _opt_str(client_config.get("scope")) or connector.oauth_default_scope

        if not _opt_str(client_config.get("client_id")):
            if connector.oauth_dcr_supported and as_meta.registration_endpoint:
                await self._register_dynamic_client(
                    server_id=server.id,
                    client_config=client_config,
                    registration_endpoint=as_meta.registration_endpoint,
                    scope=scope,
                )
            elif connector.oauth_static_client_id:
                client_config["client_id"] = connector.oauth_static_client_id
                if connector.oauth_static_client_secret_credential_id is not None:
                    client_config["client_secret_credential_id"] = (
                        connector.oauth_static_client_secret_credential_id
                    )
            else:
                raise OAuthInvalidServerState(
                    f"connector {connector.id} has neither DCR nor a static "
                    "oauth_static_client_id; cannot start OAuth"
                )

        # Snapshot AS endpoints onto the install so refresh / revoke don't
        # need to re-discover. ``authorization_endpoint`` is informational
        # (the URL we're about to send the user to) — we recompute the
        # final URL with PKCE/state/etc. each call.
        client_config["authorization_endpoint"] = as_meta.authorization_endpoint
        client_config["token_endpoint"] = as_meta.token_endpoint
        if as_meta.revocation_endpoint is not None:
            client_config["revocation_endpoint"] = as_meta.revocation_endpoint
        if as_meta.registration_endpoint is not None:
            client_config["registration_endpoint"] = as_meta.registration_endpoint
        if scope is not None:
            client_config["scope"] = scope

        client_id = _opt_str(client_config.get("client_id"))
        if client_id is None:
            # Unreachable given the branches above, but keep the invariant explicit.
            raise OAuthInvalidServerState(
                f"install {server.id} oauth_client_config has no client_id after registration"
            )

        server.oauth_client_config = client_config
        await self._server_repo.update(server)

        pkce = generate_pkce()
        await self._redis.set(
            PKCE_REDIS_KEY_PREFIX + server.id,
            pkce.verifier,
            ex=PKCE_REDIS_TTL_SECONDS,
        )
        state = await self._state_store.issue(
            install_id=server.id,
            actor_user_id=actor_user_id,
        )
        ticket = secrets.token_hex(32)
        await self._redis.set(
            CALLBACK_TICKET_REDIS_KEY_PREFIX + ticket,
            actor_user_id,
            ex=CALLBACK_TICKET_TTL_SECONDS,
        )

        authorize_url = _build_authorize_url(
            authorization_endpoint=as_meta.authorization_endpoint,
            client_id=client_id,
            redirect_uri=self._redirect_uri,
            state=state,
            code_challenge=pkce.challenge,
            scope=scope,
        )

        return OAuthStartResult(
            authorize_url=authorize_url,
            state=state,
            cookie_value=ticket,
        )

    async def _register_dynamic_client(
        self,
        *,
        server_id: str,
        client_config: dict[str, Any],
        registration_endpoint: str,
        scope: str | None,
    ) -> None:
        request = DCRRequest(
            redirect_uris=[self._redirect_uri],
            client_name=f"cubebox-{self._org_id}",
            scope=scope,
        )
        response = await self._dcr.register(registration_endpoint, request)
        client_config["client_id"] = response.client_id
        if response.client_secret is not None:
            secret_id = await self._cred_service.create(
                kind=CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,
                name=f"mcp_oauth_client_secret:{server_id}",
                plaintext=response.client_secret,
            )
            client_config["client_secret_credential_id"] = secret_id


def _build_authorize_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scope: str | None,
) -> str:
    """Build the AS authorize URL, preserving any pre-existing query string."""
    parsed = urlparse(authorization_endpoint)
    base_params: list[tuple[str, str]] = list(parse_qsl(parsed.query, keep_blank_values=True))
    base_params.extend(
        [
            ("response_type", "code"),
            ("client_id", client_id),
            ("redirect_uri", redirect_uri),
            ("state", state),
            ("code_challenge", code_challenge),
            ("code_challenge_method", "S256"),
        ]
    )
    if scope:
        base_params.append(("scope", scope))
    return urlunparse(parsed._replace(query=urlencode(base_params)))


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    return str(value)
