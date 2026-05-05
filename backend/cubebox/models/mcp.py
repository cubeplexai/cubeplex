"""MCP connector models."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from cubebox.models.mixins import CubeboxBase, TimestampMixin


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


class WorkspaceMCPBinding(SQLModel, TimestampMixin, table=True):
    """Org-wide server to workspace visibility binding.

    Pure association — composite PK; no public_id."""

    __tablename__ = "workspace_mcp_bindings"
    __table_args__ = (UniqueConstraint("workspace_id", "mcp_server_id", name="uq_ws_mcp_binding"),)

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    workspace_id: str = Field(
        primary_key=True, foreign_key="workspaces.id", max_length=20, index=True
    )
    mcp_server_id: str = Field(
        primary_key=True, foreign_key="mcp_servers.id", max_length=20, index=True
    )
    enabled: bool = Field(default=True)
    created_by_user_id: str = Field(foreign_key="users.id", max_length=20)


_MCP_SERVER_TABLE = SQLModel.metadata.tables["mcp_servers"]

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
