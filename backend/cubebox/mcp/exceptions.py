"""MCP domain exceptions. Each maps to a specific HTTP error code in routes."""


class OAuthError(Exception):
    """Base class for MCP OAuth-related failures."""


class OAuthStateInvalid(OAuthError):
    """OAuth state token failed format/HMAC validation (likely tampered)."""


class OAuthStateExpired(OAuthError):
    """OAuth state token TTL elapsed or has already been consumed."""


class OAuthMetadataNotFound(OAuthError):
    """Well-known metadata endpoint 404 or missing required field."""


class OAuthMetadataFetchError(OAuthError):
    """HTTP error while fetching OAuth metadata from a well-known endpoint."""

    def __init__(self, url: str, status: int) -> None:
        super().__init__(f"OAuth metadata fetch failed for {url}: HTTP {status}")
        self.url = url
        self.status = status


class DCRError(OAuthError):
    """Dynamic Client Registration (RFC 7591) request failed."""

    def __init__(
        self,
        status: int,
        error: str | None = None,
        error_description: str | None = None,
    ) -> None:
        msg_parts = [f"DCR failed: HTTP {status}"]
        if error:
            msg_parts.append(f"error={error}")
        if error_description:
            msg_parts.append(f"error_description={error_description}")
        super().__init__("; ".join(msg_parts))
        self.status = status
        self.error = error
        self.error_description = error_description


class MCPServerNotFound(Exception):
    """MCP server id does not exist or is outside the current org/workspace scope."""


class MCPServerURLConflict(Exception):
    """MCP server URL hash conflicts with an existing server in the same scope."""


class MCPServerNameConflict(Exception):
    """MCP server name conflicts with an existing server in the same scope."""


class MCPCredentialRequired(Exception):
    """credential_scope=org/workspace requires plaintext credential."""


class MCPUserScopeCredentialForbidden(Exception):
    """credential_scope=user/none must not carry plaintext credential."""


class MCPOAuthNotImplemented(Exception):
    """auth_method=oauth is reserved enum but not implemented in v1."""


class MCPServerNotOwnedByWorkspace(Exception):
    """Workspace route attempted to mutate a server owned by a different workspace."""


class MCPWorkspaceOwnedNoOverride(Exception):
    """Workspace overrides only apply to org-wide installs, not workspace-private ones."""


class MCPServerAlreadyOrgWide(Exception):
    """Promote was called on a server that is already org-wide."""


class MCPShareCredentialOnlyForWorkspaceScope(Exception):
    """share_credential is only meaningful for credential_scope=workspace."""


class MCPCredentialPathMismatch(Exception):
    """Credential route was used on a server with the wrong credential scope."""


class MCPCatalogConnectorNotFound(Exception):
    """Catalog connector id does not exist (or is deprecated/disabled)."""


class MCPCatalogAuthMethodUnsupported(Exception):
    """Requested auth_method is not in catalog.supported_auth_methods."""


class MCPCatalogInstallExists(Exception):
    """An install for (org, owner_workspace_id, catalog_connector_id) already exists."""
