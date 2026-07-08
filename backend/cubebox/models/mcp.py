"""MCP connector models — four-layer schema.

These models intentionally do NOT inherit ``OrgScopedMixin``: installs and
grants carry nullable scope columns (``workspace_id``, ``user_id``) that the
mixin's NOT NULL contract forbids. Each model declares its own scope FKs
explicitly.
"""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    event,
    text,
)
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase
from cubebox.models.public_id import PREFIX_MCP_CONNECTOR


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
    # How the runtime injects the static credential into outbound requests.
    # ``bearer`` → ``Authorization: Bearer <token>`` (default, matches legacy
    # behaviour). ``header`` → custom request header named
    # ``static_auth_header_name`` carrying the raw token (e.g. ``x-api-key``).
    # ``query`` → key/value appended to the connector URL (e.g.
    # ``?tavilyApiKey=<token>``) — only honoured for streamable_http/sse
    # transports.
    static_auth_style: str = Field(
        default="bearer",
        max_length=16,
        sa_column_kwargs={"server_default": text("'bearer'")},
    )
    static_auth_header_name: str | None = Field(default=None, max_length=64)
    static_auth_query_param: str | None = Field(default=None, max_length=64)

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
        # Partial unique indexes: only ACTIVE installs are unique per org. These
        # match the DB created by migration 3fcdfc800664 — declared here (not
        # migration-only) so autogenerate sees no drift.
        Index(
            "uq_mcp_connector_install_slug_per_org",
            "org_id",
            "slug_name",
            unique=True,
            postgresql_where="install_state = 'active'",
        ),
        Index(
            "uq_mcp_connector_install_template_per_org",
            "org_id",
            "template_id",
            unique=True,
            postgresql_where="install_state = 'active' AND template_id IS NOT NULL",
        ),
        Index(
            "uq_mcp_connector_install_url_per_org",
            "org_id",
            "server_url_hash",
            unique=True,
            postgresql_where="install_state = 'active'",
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
    # Canonical namespace slug — the LLM-facing tool name prefix
    # ``{slug}__{tool_name}``. Same algorithm as
    # :func:`cubebox.mcp._constants.slugify_for_namespace`. Uniqueness is
    # enforced on this column, not ``name``, so display names differing
    # only by characters the runtime strips/replaces (``Web Tools`` vs
    # ``Web-Tools``) still collide. Populated by a ``before_insert`` /
    # ``before_update`` event listener (see bottom of this module) so
    # the value never drifts from ``name`` on ORM writes. A
    # server-side default of ``'mcp'`` keeps the column NOT NULL on
    # historical inserts that bypassed the ORM.
    slug_name: str = Field(
        default="mcp",
        sa_column=Column(
            String(length=72),
            nullable=False,
            server_default=text("'mcp'"),
        ),
    )
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
    # Display metadata captured from the MCP ``initialize`` handshake +
    # per-tool ``Tool.icons`` (MCP spec rev 2025-11-25). Shape:
    # ``{"server": MCPServerInfo dict | None, "tool_icons": {tool_name: [icon_dict, ...]}}``.
    # Separate from ``tools_cache`` so citation editing (which reads
    # ``input_schema`` / ``output_schema`` from ``tools_cache``) stays
    # decoupled from icon metadata; the frontend tool registry endpoint
    # reads this column directly.
    discovery_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )

    last_error: str | None = Field(default=None, max_length=2048)
    last_discovered_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    timeout: float = Field(default=30.0)
    sse_read_timeout: float = Field(default=300.0)

    # Snapshot of the template's static-auth style at install time. The
    # workspace can override per-install (e.g. switch ``x-api-key`` to a
    # custom header name) without touching the catalog row. Same snapshot
    # pattern as ``tool_citations``.
    static_auth_style: str = Field(
        default="bearer",
        max_length=16,
        sa_column_kwargs={"server_default": text("'bearer'")},
    )
    static_auth_header_name: str | None = Field(default=None, max_length=64)
    static_auth_query_param: str | None = Field(default=None, max_length=64)

    auto_enroll_new_workspaces: bool = Field(
        default=True, sa_column_kwargs={"server_default": text("true")}
    )
    created_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )


class MCPConnector(CubeboxBase, table=True):
    """Organization-owned connector identity, independent of credentials."""

    _PREFIX: ClassVar[str] = PREFIX_MCP_CONNECTOR
    __tablename__ = "mcp_connectors"
    __table_args__ = (
        CheckConstraint(
            "auth_method IN ('oauth','static','none')",
            name="ck_mcp_connectors_auth_method",
        ),
        Index(
            "uq_mcp_connector_slug_per_org",
            "org_id",
            "slug_name",
            unique=True,
            postgresql_where="status = 'active'",
        ),
        Index(
            "uq_mcp_connector_template_per_org",
            "org_id",
            "template_id",
            unique=True,
            postgresql_where="status = 'active' AND template_id IS NOT NULL",
        ),
        Index(
            "uq_mcp_connector_url_per_org",
            "org_id",
            "server_url_hash",
            unique=True,
            postgresql_where="status = 'active'",
        ),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    template_id: str | None = Field(
        default=None, foreign_key="mcp_connector_templates.id", max_length=20, index=True
    )

    name: str = Field(max_length=64)
    slug_name: str = Field(
        default="mcp",
        sa_column=Column(
            String(length=72),
            nullable=False,
            server_default=text("'mcp'"),
        ),
    )
    server_url: str = Field(max_length=2048)
    server_url_hash: str = Field(max_length=64)
    transport: str = Field(max_length=16)

    auth_method: str = Field(max_length=16)
    oauth_client_config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )
    static_auth_style: str = Field(
        default="bearer",
        max_length=16,
        sa_column_kwargs={"server_default": text("'bearer'")},
    )
    static_auth_header_name: str | None = Field(default=None, max_length=64)
    static_auth_query_param: str | None = Field(default=None, max_length=64)

    tools_cache: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default=text("'[]'")),
    )
    tool_citations: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default=text("'{}'")),
    )
    discovery_status: str = Field(default="not_run", max_length=16)
    last_error: str | None = Field(default=None, max_length=2048)
    status: str = Field(
        default="active",
        max_length=16,
        sa_column_kwargs={"server_default": text("'active'")},
    )
    created_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )


class MCPWorkspaceConnectorState(CubeboxBase, table=True):
    """Per-workspace enablement and credential policy override for an install."""

    _PREFIX: ClassVar[str] = "mcwcs"
    __tablename__ = "mcp_workspace_connector_states"
    __table_args__ = (
        UniqueConstraint("workspace_id", "connector_id", name="uq_mcp_workspace_connector_state"),
        CheckConstraint(
            "credential_policy IN ('org','workspace','user','none')",
            name="ck_mcp_workspace_connector_states_policy",
        ),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", max_length=20, index=True)
    install_id: str = Field(foreign_key="mcp_connector_installs.id", max_length=20, index=True)
    connector_id: str = Field(foreign_key="mcp_connectors.id", max_length=20, index=True)
    enabled: bool = Field(default=True, sa_column_kwargs={"server_default": text("true")})
    credential_policy: str = Field(max_length=16)
    enablement_source: str = Field(max_length=32)
    updated_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )


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
        # Partial unique indexes (one grant per install per scope). These match
        # the DB created by migration 3fcdfc800664 — declared here (not
        # migration-only) so autogenerate sees no drift.
        Index(
            "uq_mcp_credential_grant_org",
            "connector_id",
            unique=True,
            postgresql_where="grant_scope = 'org'",
        ),
        Index(
            "uq_mcp_credential_grant_workspace",
            "connector_id",
            "workspace_id",
            unique=True,
            postgresql_where="grant_scope = 'workspace'",
        ),
        Index(
            "uq_mcp_credential_grant_user",
            "connector_id",
            "workspace_id",
            "user_id",
            unique=True,
            postgresql_where="grant_scope = 'user'",
        ),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    install_id: str = Field(foreign_key="mcp_connector_installs.id", max_length=20, index=True)
    connector_id: str = Field(foreign_key="mcp_connectors.id", max_length=20, index=True)
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
    expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    grant_status: str = Field(
        default="valid",
        max_length=16,
        sa_column_kwargs={"server_default": text("'valid'")},
    )
    created_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )


# ---------------------------------------------------------------------------
# slug_name invariant — set/refresh on every ORM write
# ---------------------------------------------------------------------------


@event.listens_for(MCPConnectorInstall, "before_insert")
@event.listens_for(MCPConnectorInstall, "before_update")
@event.listens_for(MCPConnector, "before_insert")
@event.listens_for(MCPConnector, "before_update")
def _populate_slug_name(
    _mapper: Any, _connection: Any, target: MCPConnectorInstall | MCPConnector
) -> None:
    """Mirror connector display names into ``slug_name``.

    Keeps the canonical namespace slug in sync with the display name so
    the org-wide ``uq_mcp_connector_install_slug_per_org`` partial unique
    index catches any pair of installs whose names slugify to the same
    value (e.g. ``Web Tools`` vs ``Web-Tools``). The matching service
    preflight queries this column too — both layers stay correct by
    using one shared helper.

    Cross-database: SQLite (used by some unit tests) doesn't accept the
    PG regex expression a generated column would need, so the slug
    invariant is enforced in Python rather than via ``Computed``.
    Production writes always go through the ORM so this is a sound
    place for the invariant.
    """
    from cubebox.mcp._constants import slugify_for_namespace

    if target.name is not None:
        target.slug_name = slugify_for_namespace(target.name)
