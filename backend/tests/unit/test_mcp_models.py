"""Tests for MCP connector SQLModels."""

from sqlalchemy import UniqueConstraint


def _unique_constraint_columns(model: type, name: str) -> list[str] | None:
    for constraint in model.__table__.constraints:
        if isinstance(constraint, UniqueConstraint) and constraint.name == name:
            return [column.name for column in constraint.columns]
    return None


def test_mcp_models_are_registered() -> None:
    from cubebox.models import (
        MCPServer,
        UserMCPCredential,
        WorkspaceMCPBinding,
        WorkspaceMCPCredential,
    )

    assert MCPServer.__tablename__ == "mcp_servers"
    assert WorkspaceMCPCredential.__tablename__ == "workspace_mcp_credentials"
    assert UserMCPCredential.__tablename__ == "user_mcp_credentials"
    assert WorkspaceMCPBinding.__tablename__ == "workspace_mcp_bindings"


def test_mcp_server_json_columns_and_constraints() -> None:
    from cubebox.models import MCPServer

    assert MCPServer.__table__.c.oauth_client_config.type.python_type is dict
    assert MCPServer.__table__.c.headers.type.python_type is dict
    assert MCPServer.__table__.c.tools_cache.type.python_type is dict
    assert _unique_constraint_columns(MCPServer, "uq_mcp_server_url") == [
        "org_id",
        "owner_workspace_id",
        "server_url_hash",
    ]
    assert _unique_constraint_columns(MCPServer, "uq_mcp_server_name") == [
        "org_id",
        "owner_workspace_id",
        "name",
    ]


def test_mcp_credential_and_binding_constraints() -> None:
    from cubebox.models import UserMCPCredential, WorkspaceMCPBinding, WorkspaceMCPCredential

    assert _unique_constraint_columns(WorkspaceMCPCredential, "uq_ws_mcp_cred") == [
        "workspace_id",
        "mcp_server_id",
    ]
    assert _unique_constraint_columns(UserMCPCredential, "uq_user_mcp_cred") == [
        "user_id",
        "mcp_server_id",
    ]
    assert _unique_constraint_columns(WorkspaceMCPBinding, "uq_ws_mcp_binding") == [
        "workspace_id",
        "mcp_server_id",
    ]
