"""MCP domain exceptions. Each maps to a specific HTTP error code in routes."""


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
