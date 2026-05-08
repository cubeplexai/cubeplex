"""MCP connector models."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, Index, UniqueConstraint, text
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin


class MCPCatalogConnector(CubeboxBase, table=True):
    """System-level catalog of installable remote MCP connectors.

    Templates only; no credentials and no runtime tools live here. Installs
    materialize as ``MCPServer`` rows referencing ``catalog_connector_id``.
    """

    _PREFIX: ClassVar[str] = "mctlg"
    __tablename__ = "mcp_catalog_connectors"
    __table_args__ = (UniqueConstraint("slug", name="uq_mcp_catalog_slug"),)

    slug: str = Field(max_length=64, index=True)
    name: str = Field(max_length=128)
    description: str = Field(max_length=2048)
    provider: str = Field(max_length=64)
    server_url: str = Field(max_length=2048)
    transport: str = Field(max_length=16)
    supported_auth_methods: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    default_credential_scope: str = Field(max_length=16)

    # OAuth-specific fields (NULL when 'oauth' is not in supported_auth_methods)
    oauth_dcr_supported: bool | None = Field(default=None)
    oauth_default_scope: str | None = Field(default=None, max_length=512)
    oauth_static_client_id: str | None = Field(default=None, max_length=256)
    oauth_static_client_secret_credential_id: str | None = Field(
        default=None, foreign_key="credentials.id", max_length=20
    )

    # Static-specific fields (NULL when 'static' is not in supported_auth_methods)
    static_form_fields: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    static_auth_header_template: str | None = Field(default=None, max_length=256)

    cred_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )
    status: str = Field(default="active", max_length=16)


class MCPServer(CubeboxBase, table=True):
    """MCP server registration. owner_workspace_id=None means org-wide."""

    _PREFIX: ClassVar[str] = "mcp"
    __tablename__ = "mcp_servers"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "owner_workspace_id", "server_url_hash", name="uq_mcp_server_url"
        ),
        UniqueConstraint("org_id", "owner_workspace_id", "name", name="uq_mcp_server_name"),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    owner_workspace_id: str | None = Field(
        default=None, foreign_key="workspaces.id", max_length=20, index=True
    )
    catalog_connector_id: str | None = Field(
        default=None, foreign_key="mcp_catalog_connectors.id", max_length=20, index=True
    )
    name: str = Field(max_length=64)
    server_url: str = Field(max_length=2048)
    server_url_hash: str = Field(max_length=64)
    transport: str = Field(max_length=16)
    auth_method: str = Field(max_length=16)
    credential_scope: str = Field(max_length=16)
    credential_id: str | None = Field(default=None, foreign_key="credentials.id", max_length=20)
    oauth_client_config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    tools_cache: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    authed: bool = Field(default=False)
    last_error: str | None = Field(default=None, max_length=2048)
    last_discovered_at: datetime | None = None
    timeout: float = Field(default=30.0)
    sse_read_timeout: float = Field(default=300.0)
    created_by_user_id: str = Field(foreign_key="users.id", max_length=20)


class WorkspaceMCPCredential(CubeboxBase, table=True):
    """credential_scope=workspace: one row per workspace using the server."""

    _PREFIX: ClassVar[str] = "wmc"
    __tablename__ = "workspace_mcp_credentials"
    __table_args__ = (UniqueConstraint("workspace_id", "mcp_server_id", name="uq_ws_mcp_cred"),)

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", max_length=20, index=True)
    mcp_server_id: str = Field(foreign_key="mcp_servers.id", max_length=20, index=True)
    credential_id: str = Field(foreign_key="credentials.id", max_length=20)
    created_by_user_id: str = Field(foreign_key="users.id", max_length=20)


class UserMCPCredential(CubeboxBase, table=True):
    """credential_scope=user: one row per user and server."""

    _PREFIX: ClassVar[str] = "umc"
    __tablename__ = "user_mcp_credentials"
    __table_args__ = (UniqueConstraint("user_id", "mcp_server_id", name="uq_user_mcp_cred"),)

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    user_id: str = Field(foreign_key="users.id", max_length=20, index=True)
    mcp_server_id: str = Field(foreign_key="mcp_servers.id", max_length=20, index=True)
    credential_id: str = Field(foreign_key="credentials.id", max_length=20)
    oauth_refresh_token_credential_id: str | None = Field(
        default=None, foreign_key="credentials.id", max_length=20
    )
    oauth_expires_at: datetime | None = None


class WorkspaceMCPOverride(CubeboxBase, OrgScopedMixin, table=True):
    """Workspace-level override of an org-wide install.

    A row exists only when the workspace explicitly disables an inherited
    org-wide MCP server. ``enabled=False`` is the only meaningful value
    today; ``enabled=True`` is reserved for future per-workspace overrides
    (e.g. enabling a server that's been org-soft-disabled).
    """

    _PREFIX: ClassVar[str] = "wmov"
    __tablename__ = "workspace_mcp_overrides"
    __table_args__ = (UniqueConstraint("workspace_id", "mcp_server_id", name="uq_ws_mcp_override"),)

    mcp_server_id: str = Field(foreign_key="mcp_servers.id", max_length=20, index=True)
    enabled: bool = Field(default=False)
    updated_by_user_id: str = Field(foreign_key="users.id", max_length=20)


_MCP_SERVER_TABLE = MCPServer.__table__  # type: ignore[attr-defined]

Index(
    "ix_mcp_server_org_wide_name_unique",
    _MCP_SERVER_TABLE.c.org_id,
    _MCP_SERVER_TABLE.c.name,
    unique=True,
    postgresql_where=_MCP_SERVER_TABLE.c.owner_workspace_id.is_(None),
    sqlite_where=_MCP_SERVER_TABLE.c.owner_workspace_id.is_(None),
)
Index(
    "ix_mcp_server_org_wide_url_unique",
    _MCP_SERVER_TABLE.c.org_id,
    _MCP_SERVER_TABLE.c.server_url_hash,
    unique=True,
    postgresql_where=_MCP_SERVER_TABLE.c.owner_workspace_id.is_(None),
    sqlite_where=_MCP_SERVER_TABLE.c.owner_workspace_id.is_(None),
)
# NOTE: the uniqueness constraint
#   (org_id, COALESCE(owner_workspace_id, '_org'), catalog_connector_id)
# WHERE catalog_connector_id IS NOT NULL
# is added via a hand-edited alembic migration (see
# alembic/versions/*_mcp_catalog_*) — SQLAlchemy can't express COALESCE
# in a standard Index, and we need NULL owner_workspace_id values to
# collide on the same catalog connector at the org-wide level.
