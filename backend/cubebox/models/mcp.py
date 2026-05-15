"""MCP connector models."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, CheckConstraint, Column, Index, UniqueConstraint, text
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
    tool_citations: dict[str, dict[str, Any]] = Field(
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
        Index(
            "uq_mcp_install_per_catalog",
            "org_id",
            text("COALESCE(owner_workspace_id, '_org')"),
            "catalog_connector_id",
            unique=True,
            postgresql_where=text("catalog_connector_id IS NOT NULL"),
        ),
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
    tool_citations: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )
    authed: bool = Field(default=False)
    # Org-wide installs only: when True, new workspaces auto-inherit an enabled
    # WorkspaceMCPOverride. Ignored for workspace-private installs.
    auto_enroll_new_workspaces: bool = Field(
        default=True, sa_column_kwargs={"server_default": text("true")}
    )
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
    """Workspace-level visibility and credential override for org-wide MCP installs.

    A row with ``enabled=True`` means this workspace can see and use the
    connector. No row means the connector is not visible to this workspace
    (default-invisible semantics).

    ``credential_mode`` controls how credentials resolve for this workspace:
    - ``None``: inherit ``MCPServer.credential_scope`` (default — no override)
    - ``org``: use the org-level shared credential (MCPServer.credential_id)
    - ``workspace``: one member provides a credential shared by all workspace members
    - ``user``: each member authenticates individually
    """

    _PREFIX: ClassVar[str] = "wmov"
    __tablename__ = "workspace_mcp_overrides"
    __table_args__ = (UniqueConstraint("workspace_id", "mcp_server_id", name="uq_ws_mcp_override"),)

    mcp_server_id: str = Field(foreign_key="mcp_servers.id", max_length=20, index=True)
    enabled: bool = Field(default=False)
    credential_mode: str | None = Field(default=None, max_length=16)
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


# ---------------------------------------------------------------------------
# Four-layer connector schema (coexists with legacy classes above).
#
# These models intentionally do NOT inherit ``OrgScopedMixin``: installs and
# grants carry nullable scope columns (``workspace_id``, ``user_id``) that the
# mixin's NOT NULL contract forbids. Each model declares its own scope FKs
# explicitly.
# ---------------------------------------------------------------------------


class MCPConnectorTemplate(CubeboxBase, table=True):
    """Global catalog of installable remote MCP connectors (templates only).

    No credentials and no runtime tool state live here. Installs materialize
    as :class:`MCPConnectorInstall` rows referencing ``template_id``.
    """

    _PREFIX: ClassVar[str] = "mctpl"
    __tablename__ = "mcp_connector_templates"
    __table_args__ = (UniqueConstraint("slug", name="uq_mcp_connector_template_slug"),)

    slug: str = Field(max_length=64, index=True)
    name: str = Field(max_length=128)
    description: str = Field(max_length=2048)
    provider: str = Field(max_length=64)
    server_url: str = Field(max_length=2048)
    transport: str = Field(max_length=16)
    supported_auth_methods: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    default_credential_policy: str = Field(max_length=16)

    # OAuth-specific fields (NULL when 'oauth' is not in supported_auth_methods).
    oauth_dcr_supported: bool | None = Field(default=None)
    oauth_default_scope: str | None = Field(default=None, max_length=512)
    oauth_static_client_id: str | None = Field(default=None, max_length=256)
    oauth_static_client_secret_credential_id: str | None = Field(
        default=None, foreign_key="credentials.id", max_length=20
    )

    # Static-auth metadata (NULL when 'static' is not in supported_auth_methods).
    static_form_schema: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    static_auth_header_template: str | None = Field(default=None, max_length=256)

    template_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )
    tool_citation_defaults: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )
    status: str = Field(default="active", max_length=16)


class MCPConnectorInstall(CubeboxBase, table=True):
    """Concrete install of a connector at org or workspace scope.

    Uniqueness for nullable-scope (URL × org/ws, name × org/ws, template ×
    org/ws) is enforced via partial unique indexes that exclude
    ``install_state='uninstalled'`` rows — those are tombstones and must not
    block reinstalling the same template/URL/name. The partial indexes are
    declared in the alembic migration (postgresql_where) because
    SQLAlchemy's Index ``postgresql_where`` round-trips inconsistently from
    autogenerate reflection on some versions; the migration is the source of
    truth.
    """

    _PREFIX: ClassVar[str] = "mcins"
    __tablename__ = "mcp_connector_installs"
    __table_args__ = (
        CheckConstraint(
            "install_scope IN ('org','workspace')",
            name="ck_mcp_connector_installs_scope",
        ),
        CheckConstraint(
            "auth_method IN ('oauth','static','none')",
            name="ck_mcp_connector_installs_auth_method",
        ),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    workspace_id: str | None = Field(
        default=None, foreign_key="workspaces.id", max_length=20, index=True, nullable=True
    )
    install_scope: str = Field(max_length=16)
    template_id: str | None = Field(
        default=None, foreign_key="mcp_connector_templates.id", max_length=20, index=True
    )

    name: str = Field(max_length=64)
    server_url: str = Field(max_length=2048)
    server_url_hash: str = Field(max_length=64)
    transport: str = Field(max_length=16)

    auth_method: str = Field(max_length=16)
    default_credential_policy: str = Field(max_length=16)

    auth_status: str = Field(default="not_required", max_length=16)
    discovery_status: str = Field(default="not_run", max_length=16)
    install_state: str = Field(
        default="active",
        max_length=16,
        sa_column_kwargs={"server_default": text("'active'")},
    )

    oauth_client_config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )
    tools_cache: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default=text("'[]'")),
    )
    tool_citations: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )

    last_error: str | None = Field(default=None, max_length=2048)
    last_discovered_at: datetime | None = None
    timeout: float = Field(default=30.0)
    sse_read_timeout: float = Field(default=300.0)

    auto_enroll_new_workspaces: bool = Field(
        default=True, sa_column_kwargs={"server_default": text("true")}
    )
    created_by_user_id: str = Field(foreign_key="users.id", max_length=20)


class MCPWorkspaceConnectorState(CubeboxBase, table=True):
    """Per-workspace enablement and credential policy override for an install."""

    _PREFIX: ClassVar[str] = "mcwcs"
    __tablename__ = "mcp_workspace_connector_states"
    __table_args__ = (
        UniqueConstraint("workspace_id", "install_id", name="uq_mcp_workspace_connector_state"),
        CheckConstraint(
            "credential_policy IN ('org','workspace','user','none')",
            name="ck_mcp_workspace_connector_states_policy",
        ),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", max_length=20, index=True)
    install_id: str = Field(foreign_key="mcp_connector_installs.id", max_length=20, index=True)
    enabled: bool = Field(default=True, sa_column_kwargs={"server_default": text("true")})
    credential_policy: str = Field(max_length=16)
    enablement_source: str = Field(max_length=32)
    updated_by_user_id: str = Field(foreign_key="users.id", max_length=20)


class MCPCredentialGrant(CubeboxBase, table=True):
    """Credential binding for an install at org / workspace / user scope.

    Uniqueness across scopes (one org grant per install; one workspace grant
    per (install, workspace); one user grant per (install, workspace, user))
    is enforced via partial unique indexes declared in the alembic migration,
    because Postgres treats NULL as distinct in plain ``UNIQUE`` and we need
    NULL ``workspace_id``/``user_id`` to collide on the same scope.

    A row-shape check constraint also enforces that the nullable scope
    columns line up with ``grant_scope``.
    """

    _PREFIX: ClassVar[str] = "mcgrn"
    __tablename__ = "mcp_credential_grants"
    __table_args__ = (
        CheckConstraint(
            "grant_scope IN ('org','workspace','user')",
            name="ck_mcp_credential_grants_scope",
        ),
        CheckConstraint(
            "(grant_scope='org' AND workspace_id IS NULL AND user_id IS NULL)"
            " OR (grant_scope='workspace' AND workspace_id IS NOT NULL AND user_id IS NULL)"
            " OR (grant_scope='user' AND workspace_id IS NOT NULL AND user_id IS NOT NULL)",
            name="ck_mcp_credential_grants_scope_columns",
        ),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    install_id: str = Field(foreign_key="mcp_connector_installs.id", max_length=20, index=True)
    grant_scope: str = Field(max_length=16)
    workspace_id: str | None = Field(
        default=None, foreign_key="workspaces.id", max_length=20, index=True, nullable=True
    )
    user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, index=True, nullable=True
    )

    credential_id: str = Field(foreign_key="credentials.id", max_length=20)
    refresh_credential_id: str | None = Field(
        default=None, foreign_key="credentials.id", max_length=20
    )
    expires_at: datetime | None = None
    grant_status: str = Field(
        default="valid",
        max_length=16,
        sa_column_kwargs={"server_default": text("'valid'")},
    )
    created_by_user_id: str = Field(foreign_key="users.id", max_length=20)
