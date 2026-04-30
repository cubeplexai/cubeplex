"""MCP connector models."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class MCPServer(SQLModel, table=True):
    """MCP server registration. owner_workspace_id=None means org-wide."""

    __tablename__ = "mcp_servers"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "owner_workspace_id", "server_url_hash", name="uq_mcp_server_url"
        ),
        UniqueConstraint("org_id", "owner_workspace_id", "name", name="uq_mcp_server_name"),
    )

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    owner_workspace_id: str | None = Field(default=None, max_length=36, index=True)
    name: str = Field(max_length=64)
    server_url: str = Field(max_length=2048)
    server_url_hash: str = Field(max_length=64)
    transport: str = Field(max_length=16)
    auth_method: str = Field(max_length=16)
    credential_scope: str = Field(max_length=16)
    credential_id: str | None = Field(default=None, max_length=36)
    oauth_client_config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    tools_cache: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    authed: bool = Field(default=False)
    last_error: str | None = Field(default=None, max_length=2048)
    last_discovered_at: datetime | None = None
    timeout: float = Field(default=30.0)
    sse_read_timeout: float = Field(default=300.0)
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkspaceMCPCredential(SQLModel, table=True):
    """credential_scope=workspace: one row per workspace using the server."""

    __tablename__ = "workspace_mcp_credentials"
    __table_args__ = (UniqueConstraint("workspace_id", "mcp_server_id", name="uq_ws_mcp_cred"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    workspace_id: str = Field(max_length=36, index=True)
    mcp_server_id: str = Field(max_length=36, index=True)
    credential_id: str = Field(max_length=36)
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserMCPCredential(SQLModel, table=True):
    """credential_scope=user: one row per user and server."""

    __tablename__ = "user_mcp_credentials"
    __table_args__ = (UniqueConstraint("user_id", "mcp_server_id", name="uq_user_mcp_cred"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    user_id: str = Field(max_length=36, index=True)
    mcp_server_id: str = Field(max_length=36, index=True)
    credential_id: str = Field(max_length=36)
    oauth_refresh_token_credential_id: str | None = Field(default=None, max_length=36)
    oauth_expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WorkspaceMCPBinding(SQLModel, table=True):
    """Org-wide server to workspace visibility binding."""

    __tablename__ = "workspace_mcp_bindings"
    __table_args__ = (UniqueConstraint("workspace_id", "mcp_server_id", name="uq_ws_mcp_binding"),)

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)
    workspace_id: str = Field(max_length=36, index=True)
    mcp_server_id: str = Field(max_length=36, index=True)
    enabled: bool = Field(default=True)
    created_by_user_id: str = Field(max_length=36)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
