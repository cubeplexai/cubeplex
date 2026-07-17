"""Sandbox Env Vault entry.

One entry per (env_name, scope). Both secret and plain entries store their
value via credential_id (in the vault, kind 'sandbox_env'). Secret entries
additionally carry hosts + header_names for injection policy. Scope shape
and value shape are enforced by CHECK constraints; per-scope uniqueness by
partial unique indexes (NULL scope columns must collide, which plain UNIQUE
does not do in Postgres).
"""

from typing import ClassVar

from sqlalchemy import JSON, CheckConstraint, Column, Index, text
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase
from cubeplex.models.public_id import PREFIX_SANDBOX_ENV


class SandboxEnvVar(CubeplexBase, table=True):
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
            "credential_id IS NOT NULL",
            name="ck_sandbox_env_vars_credential_required",
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
    status: str = Field(default="valid", max_length=16)
    created_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )
