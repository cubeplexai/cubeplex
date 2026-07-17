"""SandboxPolicy — default image, egress rules, command rules.

Org-only table. It declares ``org_id`` as a direct FK and deliberately does
NOT use ``OrgScopedMixin``: that mixin adds a REQUIRED ``workspace_id`` FK,
but a per-org default has no workspace. ``scope_workspace_id`` is a separate
NULLABLE column reserved for v2 per-workspace overrides — v1 only ever writes
NULL (the org-default row).

Uniqueness is enforced with TWO partial indexes (Postgres and SQLite treat
``NULL`` as distinct in unique indexes, so a single ``UNIQUE (org_id,
scope_workspace_id)`` would silently allow two NULL-scope rows for the same
org):

- ``uq_sandbox_policy_org_default``  — one row per org for the org-default
  shape (``scope_workspace_id IS NULL``).
- ``uq_sandbox_policy_org_workspace`` — one row per (org, workspace) for v2
  workspace-override rows (``scope_workspace_id IS NOT NULL``).
"""

from typing import Any, ClassVar

from sqlalchemy import JSON, Column, Index, text
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase
from cubeplex.models.public_id import PREFIX_SANDBOX_POLICY


class SandboxPolicy(CubeplexBase, table=True):
    _PREFIX: ClassVar[str] = PREFIX_SANDBOX_POLICY
    __tablename__ = "sandbox_policies"
    __table_args__ = (
        Index(
            "uq_sandbox_policy_org_default",
            "org_id",
            unique=True,
            postgresql_where=text("scope_workspace_id IS NULL"),
            sqlite_where=text("scope_workspace_id IS NULL"),
        ),
        Index(
            "uq_sandbox_policy_org_workspace",
            "org_id",
            "scope_workspace_id",
            unique=True,
            postgresql_where=text("scope_workspace_id IS NOT NULL"),
            sqlite_where=text("scope_workspace_id IS NOT NULL"),
        ),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    # NULL = org-default row (only shape v1 writes). v2 will populate this for
    # per-workspace overrides without a schema migration.
    scope_workspace_id: str | None = Field(
        default=None,
        foreign_key="workspaces.id",
        max_length=20,
        index=True,
        nullable=True,
    )
    default_image: str = Field(max_length=512)
    # Egress default action. "allow" = open by default, deny rules form a
    # blacklist; "deny" = closed by default, allow rules form a whitelist.
    # The sidecar evaluates egress first-match-wins, then falls back here.
    network_default_action: str = Field(
        default="deny",
        max_length=10,
        sa_column_kwargs={"server_default": "deny"},
    )
    # JSON list of {action, target}; rules are inherently lists (multiple
    # allows/denies); image is a single value because v1 has no override
    # surface to pick one from a list.
    network_rules: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True))
    )
    # JSON list of {action, pattern}. ``action`` in {allow, deny, confirm}.
    # ``confirm`` pauses the execute tool at runtime for human approve/deny
    # (real HITL — see SandboxMiddleware.before_tool_call).
    command_rules: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True))
    )
    egress_proxy: str | None = Field(default=None, max_length=512, nullable=True)
    # Per-sandbox resource limits as Kubernetes quantity strings (e.g. cpu
    # "500m"/"2", memory "512Mi"/"2Gi", storage "10Gi"). cpu/memory feed
    # ``Sandbox.create(resource=...)`` and fall back to the static
    # ``sandbox.resource.*`` config when NULL. storage feeds the user PVC
    # capacity request and, when NULL, leaves the cluster StorageClass default
    # in place (there is no ``sandbox.volume.*`` size config).
    resource_cpu: str | None = Field(default=None, max_length=32, nullable=True)
    resource_memory: str | None = Field(default=None, max_length=32, nullable=True)
    storage: str | None = Field(default=None, max_length=32, nullable=True)
