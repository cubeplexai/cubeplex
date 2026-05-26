"""Sandbox Env Vault entry.

One entry per (env_name, scope). Secret entries carry hosts + a credential_id
(value in the vault, kind 'sandbox_env'); plain entries carry plain_value.
Scope shape and value shape are enforced by CHECK constraints; per-scope
uniqueness by partial unique indexes (NULL scope columns must collide, which
plain UNIQUE does not do in Postgres).
"""

from typing import ClassVar

from sqlalchemy import JSON, CheckConstraint, Column, Index, text
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase
from cubebox.models.public_id import PREFIX_SANDBOX_ENV


class SandboxEnvVar(CubeboxBase, table=True):
    _PREFIX: ClassVar[str] = PREFIX_SANDBOX_ENV
    __tablename__ = "sandbox_env_vars"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('org','workspace','user')",
            name="ck_sandbox_env_scope",
        ),
        CheckConstraint(
            "(scope='org' AND workspace_id IS NULL AND user_id IS NULL)"
            " OR (scope='workspace' AND workspace_id IS NOT NULL AND user_id IS NULL)"
            " OR (scope='user' AND workspace_id IS NOT NULL AND user_id IS NOT NULL)",
            name="ck_sandbox_env_scope_columns",
        ),
        CheckConstraint(
            "(is_secret AND credential_id IS NOT NULL AND plain_value IS NULL"
            " AND hosts IS NOT NULL)"
            " OR (NOT is_secret AND plain_value IS NOT NULL AND credential_id IS NULL"
            " AND hosts IS NULL)",
            name="ck_sandbox_env_value_shape",
        ),
        Index(
            "uq_sandbox_env_org",
            "org_id",
            "env_name",
            unique=True,
            postgresql_where="scope = 'org'",
            sqlite_where=text("scope = 'org'"),
        ),
        Index(
            "uq_sandbox_env_workspace",
            "workspace_id",
            "env_name",
            unique=True,
            postgresql_where="scope = 'workspace'",
            sqlite_where=text("scope = 'workspace'"),
        ),
        Index(
            "uq_sandbox_env_user",
            "workspace_id",
            "user_id",
            "env_name",
            unique=True,
            postgresql_where="scope = 'user'",
            sqlite_where=text("scope = 'user'"),
        ),
    )

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    env_name: str = Field(max_length=128)
    is_secret: bool = Field(default=True)
    scope: str = Field(max_length=16)
    workspace_id: str | None = Field(
        default=None, foreign_key="workspaces.id", max_length=20, index=True, nullable=True
    )
    user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, index=True, nullable=True
    )
    hosts: list[str] | None = Field(default=None, sa_column=Column(JSON(none_as_null=True)))
    header_names: list[str] | None = Field(default=None, sa_column=Column(JSON(none_as_null=True)))
    credential_id: str | None = Field(
        default=None, foreign_key="credentials.id", max_length=20, nullable=True
    )
    plain_value: str | None = Field(default=None, max_length=4096, nullable=True)
    status: str = Field(default="valid", max_length=16)
    created_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )
