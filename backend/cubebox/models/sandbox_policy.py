"""SandboxPolicy — default image, egress rules, command rules.

Org-only table. It declares ``org_id`` as a direct FK and deliberately does
NOT use ``OrgScopedMixin``: that mixin adds a REQUIRED ``workspace_id`` FK,
but a per-org default has no workspace. ``scope_workspace_id`` is a separate
NULLABLE column reserved for v2 per-workspace overrides — v1 only ever writes
NULL (the org-default row). One row per (org, scope) is enforced by a unique
index on ``(org_id, scope_workspace_id)``.
"""

from typing import Any, ClassVar

from sqlalchemy import JSON, Column, Index
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase
from cubebox.models.public_id import PREFIX_SANDBOX_POLICY


class SandboxPolicy(CubeboxBase, table=True):
    _PREFIX: ClassVar[str] = PREFIX_SANDBOX_POLICY
    __tablename__ = "sandbox_policies"
    __table_args__ = (
        Index(
            "uq_sandbox_policy_scope",
            "org_id",
            "scope_workspace_id",
            unique=True,
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
    # JSON list of {action, target}; rules are inherently lists (multiple
    # allows/denies); image is a single value because v1 has no override
    # surface to pick one from a list.
    network_rules: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True))
    )
    # JSON list of {action, pattern}. ``action`` in {allow, deny, confirm};
    # confirm degrades to deny at runtime in v1 (see Task 8 + cubepi follow-up).
    command_rules: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True))
    )
