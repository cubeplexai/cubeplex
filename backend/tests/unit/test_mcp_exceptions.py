"""Unit tests for MCP domain exceptions."""

from cubebox.mcp.exceptions import (
    MCPCredentialPathMismatch,
    MCPCredentialRequired,
    MCPServerAlreadyOrgWide,
    MCPServerNameConflict,
    MCPServerNotFound,
    MCPServerNotOwnedByWorkspace,
    MCPServerURLConflict,
    MCPShareCredentialOnlyForWorkspaceScope,
    MCPUserScopeCredentialForbidden,
    MCPWorkspaceOwnedNoOverride,
)


def test_mcp_domain_exceptions_are_exception_types() -> None:
    exception_types = [
        MCPCredentialPathMismatch,
        MCPCredentialRequired,
        MCPServerAlreadyOrgWide,
        MCPServerNameConflict,
        MCPServerNotFound,
        MCPServerNotOwnedByWorkspace,
        MCPServerURLConflict,
        MCPShareCredentialOnlyForWorkspaceScope,
        MCPUserScopeCredentialForbidden,
        MCPWorkspaceOwnedNoOverride,
    ]

    for exception_type in exception_types:
        assert issubclass(exception_type, Exception)
