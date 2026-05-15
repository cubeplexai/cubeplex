"""Tests for MCP connector SQLModels."""

from sqlalchemy import Index, UniqueConstraint


def _unique_constraint_columns(model: type, name: str) -> list[str] | None:
    for constraint in model.__table__.constraints:
        if isinstance(constraint, UniqueConstraint) and constraint.name == name:
            return [column.name for column in constraint.columns]
    return None


def _index_columns(model: type, name: str) -> list[str] | None:
    for index in model.__table__.indexes:
        if isinstance(index, Index) and index.name == name:
            return [column.name for column in index.columns]
    return None


def test_mcp_models_are_registered() -> None:
    from cubebox.models import (
        MCPCatalogConnector,
        MCPServer,
        UserMCPCredential,
        WorkspaceMCPCredential,
        WorkspaceMCPOverride,
    )

    assert MCPServer.__tablename__ == "mcp_servers"
    assert WorkspaceMCPCredential.__tablename__ == "workspace_mcp_credentials"
    assert UserMCPCredential.__tablename__ == "user_mcp_credentials"
    assert WorkspaceMCPOverride.__tablename__ == "workspace_mcp_overrides"
    assert MCPCatalogConnector.__tablename__ == "mcp_catalog_connectors"


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
    assert _index_columns(MCPServer, "ix_mcp_server_org_wide_name_unique") == [
        "org_id",
        "name",
    ]
    assert _index_columns(MCPServer, "ix_mcp_server_org_wide_url_unique") == [
        "org_id",
        "server_url_hash",
    ]


def test_mcp_credential_and_override_constraints() -> None:
    from cubebox.models import UserMCPCredential, WorkspaceMCPCredential, WorkspaceMCPOverride

    assert _unique_constraint_columns(WorkspaceMCPCredential, "uq_ws_mcp_cred") == [
        "workspace_id",
        "mcp_server_id",
    ]
    assert _unique_constraint_columns(UserMCPCredential, "uq_user_mcp_cred") == [
        "user_id",
        "mcp_server_id",
    ]
    assert _unique_constraint_columns(WorkspaceMCPOverride, "uq_ws_mcp_override") == [
        "workspace_id",
        "mcp_server_id",
    ]


def test_mcp_catalog_connector_unique_slug() -> None:
    from cubebox.models import MCPCatalogConnector

    assert _unique_constraint_columns(MCPCatalogConnector, "uq_mcp_catalog_slug") == ["slug"]


def test_mcp_four_layer_table_names() -> None:
    from cubebox.models import (
        MCPConnectorInstall,
        MCPConnectorTemplate,
        MCPCredentialGrant,
        MCPWorkspaceConnectorState,
    )

    assert MCPConnectorTemplate.__tablename__ == "mcp_connector_templates"
    assert MCPConnectorInstall.__tablename__ == "mcp_connector_installs"
    assert MCPWorkspaceConnectorState.__tablename__ == "mcp_workspace_connector_states"
    assert MCPCredentialGrant.__tablename__ == "mcp_credential_grants"
    assert MCPConnectorTemplate._PREFIX == "mctpl"
    assert MCPConnectorInstall._PREFIX == "mcins"
    assert MCPWorkspaceConnectorState._PREFIX == "mcwcs"
    assert MCPCredentialGrant._PREFIX == "mcgrn"


def test_no_auth_install_defaults_to_none_policy() -> None:
    from cubebox.models import MCPConnectorInstall

    row = MCPConnectorInstall(
        org_id="org-1",
        name="NoAuth",
        server_url="https://noauth.example.com/mcp",
        server_url_hash="hash",
        transport="streamable_http",
        auth_method="none",
        default_credential_policy="none",
        created_by_user_id="user-1",
    )

    assert row.auth_method == "none"
    assert row.default_credential_policy == "none"
    assert row.install_state == "active"
    assert row.auth_status == "not_required"
    assert row.discovery_status == "not_run"
