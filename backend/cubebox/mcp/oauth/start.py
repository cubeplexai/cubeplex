"""Mint authorize URLs for the four-layer MCP OAuth flow.

Spec: docs/superpowers/specs/2026-05-16-mcp-install-auth-handoff-spec.md §6.
The per-scope start route handlers in admin_mcp.py / ws_mcp.py call
``OAuthStartService.start_oauth_flow`` and serialize the returned
``OAuthStartResult`` into ``MCPOAuthStartOut``.

The service:
1. Looks up the install row (must exist, be active) and verifies its template
   supports OAuth.
2. Discovers / refreshes AS metadata via OAuthMetadataDiscovery.
3. Performs DCR if the AS supports it and the install has no client_id yet
   (snapshots client_id / client_secret onto the install row); otherwise reuses
   the install's existing static client credentials.
4. Generates a PKCE challenge.
5. Issues a state token via OAuthStateStore (carries grant_scope + workspace_id +
   user_id so the callback can write the right grant without a session lookup).
6. Builds the authorize URL with response_type=code, client_id, redirect_uri,
   code_challenge=<S256>, code_challenge_method=S256, state, scope.
7. Returns OAuthStartResult(authorize_url, state, expires_at).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from urllib.parse import urlencode

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.config import config
from cubebox.credentials.dependencies import build_credential_service
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.mcp._constants import CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET
from cubebox.mcp.exceptions import DCRError, OAuthMetadataFetchError, OAuthMetadataNotFound
from cubebox.mcp.oauth.dcr import DCRClient, DCRRequest
from cubebox.mcp.oauth.metadata import (
    AuthorizationServerMetadata,
    OAuthMetadataDiscovery,
)
from cubebox.mcp.oauth.pkce import generate_pkce
from cubebox.mcp.oauth.state import OAuthStateStore
from cubebox.models.mcp import MCPConnector, MCPConnectorTemplate
from cubebox.repositories.mcp import (
    MCPConnectorRepository,
    MCPConnectorTemplateRepository,
)
from cubebox.services.credential import CredentialService

_REDIRECT_PATH: Final[str] = "/api/v1/oauth/mcp/callback"
_OAUTH_AS_METADATA_URL_KEY: Final[str] = "oauth_authorization_server_metadata_url"


@dataclass(frozen=True)
class OAuthStartResult:
    """What the route handler serializes back to the client."""

    authorize_url: str
    state: str
    expires_at: datetime  # UTC; matches the state-token TTL


class OAuthStartError(ValueError):
    """Surface-friendly error type. Route layer maps to HTTPException."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.message)


class OAuthStartService:
    """Stateless orchestrator. One per request via DI.

    Credential service / install repo are built INSIDE ``start_oauth_flow``
    after the install row reveals ``org_id`` — the route surface that
    mounts this service may be admin-only (no workspace path) so we cannot
    rely on the workspace-scoped ``get_credential_service`` factory.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        backend: EncryptionBackend,
        state_store: OAuthStateStore,
        metadata: OAuthMetadataDiscovery,
        dcr: DCRClient,
        http_client: httpx.AsyncClient,
        state_ttl_seconds: int = 300,
    ) -> None:
        self._session = session
        self._backend = backend
        self._state_store = state_store
        self._metadata = metadata
        self._dcr = dcr
        self._http = http_client
        self._state_ttl_seconds = state_ttl_seconds

    async def start_oauth_flow(
        self,
        *,
        connector_id: str,
        actor_user_id: str,
        actor_org_id: str,
        grant_scope: str,
        workspace_id: str | None,
        user_id: str | None,
        frontend_origin: str | None = None,
    ) -> OAuthStartResult:
        _validate_grant_identity(
            grant_scope=grant_scope,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        # Org-scoped install lookup. The unauthenticated callback later
        # builds repos and credential service from ``install.org_id``, so
        # an install id alone is enough to direct grant writes into ANY
        # org. Without an org-scoped filter here, a caller in org A who
        # knows org B's connector_id could mint a valid state token and
        # the callback would honor it, persisting credentials in B.
        # Cross-org and truly-missing collapse to the same error so
        # OAuth start cannot be used as an org-existence oracle.
        install = (
            await self._session.execute(
                select(MCPConnector).where(
                    MCPConnector.id == connector_id,  # type: ignore[arg-type]
                    MCPConnector.org_id == actor_org_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if install is None:
            raise OAuthStartError("connector_install_not_found")
        # Belt + suspenders: even if a future refactor drops the where()
        # clause above, this guard keeps the boundary.
        if install.org_id != actor_org_id:
            raise OAuthStartError("connector_install_not_found")
        if install.status != "active":
            raise OAuthStartError("connector_install_not_active")
        # Validate that the template supports OAuth (replaces the old connector-level
        # auth_method field which no longer exists on MCPConnector).
        _tpl_repo = MCPConnectorTemplateRepository(self._session)
        _tpl = await _tpl_repo.get(install.template_id)
        if _tpl is None or "oauth" not in (_tpl.supported_auth_methods or []):
            raise OAuthStartError("auth_method_not_supported_by_template")
        connector_id = install.id

        # Build org-scoped service surface AFTER install reveals org_id.
        cred_service = build_credential_service(
            self._session,
            self._backend,
            org_id=install.org_id,
            actor_user_id=actor_user_id,
        )
        install_repo = MCPConnectorRepository(self._session, org_id=install.org_id)
        template: MCPConnectorTemplate | None = None
        if install.template_id is not None:
            tpl_repo = MCPConnectorTemplateRepository(self._session)
            template = await tpl_repo.get(install.template_id)

        # AS metadata: discovery is internally cached per server_url, so no
        # snapshot is persisted on the install row (the install model has
        # NO authorization_endpoint / token_endpoint columns — OAuth fields
        # all live in the ``oauth_client_config`` JSON).
        #
        # Network errors are common at this hop: the backend has to reach
        # the MCP server's ``.well-known/oauth-protected-resource`` and
        # the AS metadata URL, both over WAN. A proxy outage, DNS
        # failure, or unreachable AS would otherwise bubble out as a
        # 500 + unhandled exception (httpx leaks the raw ConnectError
        # which is meaningless to the operator). Wrap it as a clean
        # OAuthStartError so the route maps to 400 with a typed code.
        resource_scopes_supported: list[str] | None = None
        try:
            pr_meta, as_meta = await self._metadata.discover_for_resource(install.server_url)
            resource_scopes_supported = pr_meta.scopes_supported
        except OAuthMetadataNotFound as exc:
            as_metadata_url = _template_metadata_str(template, _OAUTH_AS_METADATA_URL_KEY)
            if as_metadata_url is None:
                raise OAuthStartError(
                    "oauth_metadata_not_found",
                    f"oauth_metadata_not_found: {exc}",
                ) from exc
            logger.info(
                "OAuth start: using template AS metadata URL fallback for {}: {}",
                install.server_url,
                as_metadata_url,
            )
            try:
                as_meta = await self._metadata.fetch_authorization_server_metadata_url(
                    as_metadata_url
                )
            except OAuthMetadataNotFound as fallback_exc:
                raise OAuthStartError(
                    "oauth_metadata_not_found",
                    f"oauth_metadata_not_found: {fallback_exc}",
                ) from fallback_exc
            except OAuthMetadataFetchError as fallback_exc:
                raise OAuthStartError(
                    "oauth_metadata_fetch_failed",
                    f"oauth_metadata_fetch_failed: {fallback_exc}",
                ) from fallback_exc
        except httpx.HTTPError as exc:
            logger.warning(
                "OAuth start: AS metadata fetch failed for {}: {}",
                install.server_url,
                exc,
            )
            raise OAuthStartError(f"as_metadata_unreachable: {type(exc).__name__}") from exc
        except OAuthMetadataFetchError as exc:
            raise OAuthStartError(
                "oauth_metadata_fetch_failed",
                f"oauth_metadata_fetch_failed: {exc}",
            ) from exc
        try:
            client_id, _client_secret_id = await self._ensure_client(
                install,
                as_meta,
                cred_service,
                install_repo,
                frontend_origin=frontend_origin,
            )
        except DCRError as exc:
            code = exc.error or "dcr_failed"
            message = f"{code}: {exc.error_description}" if exc.error_description else code
            logger.warning(
                "OAuth start: DCR failed for {}: {}",
                install.server_url,
                exc,
            )
            raise OAuthStartError(code, message) from exc

        pkce = generate_pkce()
        state = await self._state_store.issue(
            connector_id=connector_id,
            actor_user_id=actor_user_id,
            grant_scope=grant_scope,
            workspace_id=workspace_id,
            user_id=user_id,
            frontend_origin=frontend_origin,
        )

        # Persist the per-flow PKCE verifier alongside the state token so
        # the callback can complete the exchange without re-issuing PKCE.
        await self._state_store.attach_pkce(state=state, verifier=pkce.verifier)

        # Scope resolution priority:
        # 1. install.oauth_client_config['default_scope'] — set by past
        #    runs of this method when DCR / template scope wrote it back.
        # 2. template.oauth_default_scope — seeded catalog connectors
        #    (GitHub, Slack, Notion, ...) declare required scopes here
        #    via the v1 template seed. If we don't read it and fall
        #    through to AS metadata, providers that don't publish
        #    `scopes_supported` in their well-known doc would hit the
        #    authorize endpoint without `scope=`, triggering invalid_scope
        #    or returning a token without the perms the catalog expects.
        # 3. PR metadata `scopes_supported` (resource-specific OAuth 2.1 MCP
        #    servers such as Atlassian Rovo publish scopes here, not on AS
        #    metadata).
        # 4. AS metadata `scopes_supported` (best-effort, all of them).
        scope_param: str | None = None
        cfg_default = install.oauth_client_config.get("default_scope")
        if isinstance(cfg_default, str) and cfg_default:
            scope_param = cfg_default
        elif install.template_id is not None:
            if template is not None and template.oauth_default_scope:
                scope_param = template.oauth_default_scope
        if scope_param is None and resource_scopes_supported:
            scope_param = " ".join(resource_scopes_supported)
        if scope_param is None and as_meta.scopes_supported:
            scope_param = " ".join(as_meta.scopes_supported)

        authorize_url = _build_authorize_url(
            authorize_endpoint=as_meta.authorization_endpoint,
            client_id=client_id,
            redirect_uri=_redirect_uri(frontend_origin),
            code_challenge=pkce.challenge,
            state=state,
            scope=scope_param,
            resource=install.server_url,
        )

        expires_at = datetime.now(tz=UTC) + timedelta(seconds=self._state_ttl_seconds)
        return OAuthStartResult(
            authorize_url=authorize_url,
            state=state,
            expires_at=expires_at,
        )

    async def _connector_id_for_install(self, install: MCPConnector) -> str | None:
        repo = MCPConnectorRepository(self._session, org_id=install.org_id)
        return await repo.get_connector_id_for_install(install)

    async def _ensure_client(
        self,
        install: MCPConnector,
        as_meta: AuthorizationServerMetadata,
        cred_service: CredentialService,
        install_repo: MCPConnectorRepository,
        frontend_origin: str | None = None,
    ) -> tuple[str, str | None]:
        """Read or create OAuth client.

        Resolution order (first match wins):
        1. ``install.oauth_client_config['client_id']`` (already provisioned).
        2. ``template.oauth_static_client_id`` (catalog connector ships its
           own pre-registered confidential client). Copy onto the install on
           first use.
        3. DCR via ``as_meta.registration_endpoint``.
        """
        cfg = dict(install.oauth_client_config or {})
        current_redirect = _redirect_uri(frontend_origin)
        existing_client_id = cfg.get("client_id")
        if isinstance(existing_client_id, str) and existing_client_id:
            registered_redirect = cfg.get("registered_redirect_uri")
            if registered_redirect == current_redirect:
                secret = cfg.get("client_secret_credential_id")
                return existing_client_id, secret if isinstance(secret, str) else None
            # redirect_uri changed since DCR — re-register so the AS
            # accepts the new callback origin.
            if as_meta.registration_endpoint:
                logger.info(
                    "Re-registering OAuth client for install {} (redirect_uri changed: {} → {})",
                    install.id,
                    registered_redirect,
                    current_redirect,
                )
                cfg.pop("client_id", None)
                cfg.pop("client_secret_credential_id", None)
                cfg.pop("registered_redirect_uri", None)
            else:
                # Static client or no DCR — keep the existing client_id.
                secret = cfg.get("client_secret_credential_id")
                return existing_client_id, secret if isinstance(secret, str) else None

        # Step 2: try the template's static OAuth client.
        if install.template_id is not None:
            tpl_repo = MCPConnectorTemplateRepository(self._session)
            template = await tpl_repo.get(install.template_id)
            if template is not None and template.oauth_static_client_id:
                org_secret_id: str | None = None
                if template.oauth_static_client_secret_credential_id:
                    # Template's client secret is a SYSTEM-scope credential
                    # (org_id=NULL). Clone into org scope on first use so the
                    # callback / refresh paths can read it via the org-scoped
                    # CredentialRepository.
                    sys_cred_service = build_credential_service(
                        self._session,
                        self._backend,
                        org_id=None,
                        actor_user_id=None,
                    )
                    plaintext = await sys_cred_service.get_decrypted(
                        credential_id=template.oauth_static_client_secret_credential_id,
                        requesting_kind=CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,
                    )
                    org_secret_id = await cred_service.upsert_by_kind_name(
                        kind=CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,
                        name=f"mcp:{install.id}:client_secret",
                        plaintext=plaintext,
                    )
                cfg["client_id"] = template.oauth_static_client_id
                if org_secret_id is not None:
                    cfg["client_secret_credential_id"] = org_secret_id
                install.oauth_client_config = cfg
                await install_repo.update(install)
                return template.oauth_static_client_id, org_secret_id

        # Step 3: DCR.
        if not as_meta.registration_endpoint:
            raise OAuthStartError("dcr_unsupported_and_no_static_client")
        dcr_resp = await self._dcr.register(
            as_meta.registration_endpoint,
            DCRRequest(
                redirect_uris=[_redirect_uri(frontend_origin)],
                client_name=f"cubebox:{install.id}",
            ),
        )
        secret_id: str | None = None
        if dcr_resp.client_secret:
            secret_id = await cred_service.upsert_by_kind_name(
                kind=CREDENTIAL_KIND_MCP_OAUTH_CLIENT_SECRET,
                name=f"mcp:{install.id}:client_secret",
                plaintext=dcr_resp.client_secret,
            )
        cfg["client_id"] = dcr_resp.client_id
        cfg["registered_redirect_uri"] = current_redirect
        if secret_id is not None:
            cfg["client_secret_credential_id"] = secret_id
        install.oauth_client_config = cfg
        await install_repo.update(install)
        return dcr_resp.client_id, secret_id


def _template_metadata_str(
    template: MCPConnectorTemplate | None,
    key: str,
) -> str | None:
    if template is None:
        return None
    value = template.template_metadata.get(key)
    if not isinstance(value, str) or not value:
        return None
    return value


def _redirect_uri(frontend_origin: str | None = None) -> str:
    if frontend_origin:
        return f"{frontend_origin.rstrip('/')}{_REDIRECT_PATH}"
    base = str(config.get("public_base_url", "http://localhost:8000")).rstrip("/")
    return f"{base}{_REDIRECT_PATH}"


def _validate_grant_identity(
    *,
    grant_scope: str,
    workspace_id: str | None,
    user_id: str | None,
) -> None:
    if grant_scope == "org":
        if workspace_id is not None or user_id is not None:
            raise OAuthStartError("invalid_org_grant_identity")
        return
    if grant_scope == "workspace":
        if workspace_id is None or user_id is not None:
            raise OAuthStartError("invalid_workspace_grant_identity")
        return
    if grant_scope == "user":
        if workspace_id is None or user_id is None:
            raise OAuthStartError("invalid_user_grant_identity")
        return
    raise OAuthStartError("invalid_grant_scope")


def _build_authorize_url(
    *,
    authorize_endpoint: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scope: str | None,
    resource: str,
) -> str:
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        # RFC 8707 audience binding — the MCP authorization spec
        # (https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
        # requires `resource` on authorize AND token requests, identifying
        # the target MCP server. ASes that enforce audience binding will
        # otherwise reject the code exchange or issue a token whose
        # audience is not the MCP server.
        "resource": resource,
    }
    if scope:
        params["scope"] = scope
    sep = "&" if "?" in authorize_endpoint else "?"
    return f"{authorize_endpoint}{sep}{urlencode(params)}"
