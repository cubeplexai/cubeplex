"""Unit tests for MCP domain exceptions."""

from cubebox.mcp.exceptions import (
    MCPCredentialPathMismatch,
    MCPCredentialRequired,
    MCPOAuthNotImplemented,
    MCPServerAlreadyOrgWide,
    MCPServerNameConflict,
    MCPServerNotFound,
    MCPServerNotOwnedByWorkspace,
    MCPServerURLConflict,
    MCPShareCredentialOnlyForWorkspaceScope,
    MCPUserScopeCredentialForbidden,
    MCPWorkspaceOwnedNoBinding,
)


def test_mcp_domain_exceptions_are_exception_types() -> None:
    exception_types = [
        MCPCredentialPathMismatch,
        MCPCredentialRequired,
        MCPOAuthNotImplemented,
        MCPServerAlreadyOrgWide,
        MCPServerNameConflict,
        MCPServerNotFound,
        MCPServerNotOwnedByWorkspace,
        MCPServerURLConflict,
        MCPShareCredentialOnlyForWorkspaceScope,
        MCPUserScopeCredentialForbidden,
        MCPWorkspaceOwnedNoBinding,
    ]

    for exception_type in exception_types:
        assert issubclass(exception_type, Exception)
