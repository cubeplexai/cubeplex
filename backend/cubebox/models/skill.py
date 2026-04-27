"""Skill catalog models."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel
from uuid_utils import uuid7

from cubebox.models.mixins import OrgScopedMixin


class Skill(SQLModel, table=True):
    """Global catalog row.

    source='preinstalled' → owner_org_id=NULL; name is bare slug.
    source='uploaded'     → owner_org_id=<publisher org>; name is '<org-slug>:<skill-slug>'.
    """

    __tablename__ = "skills"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    name: str = Field(max_length=128)
    source: str = Field(max_length=16)  # "preinstalled" | "uploaded"
    owner_org_id: str | None = Field(default=None, max_length=36, index=True)
    current_version: str = Field(max_length=32)
    description: str = Field(max_length=1024)
    keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deprecated_at: datetime | None = Field(default=None, nullable=True)

    __table_args__ = (
        UniqueConstraint("name", name="uq_skill_name"),
        Index("ix_skill_source_owner", "source", "owner_org_id"),
    )


class SkillVersion(SQLModel, table=True):
    """Immutable version row. New versions append; old rows never modified."""

    __tablename__ = "skill_versions"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    skill_id: str = Field(max_length=36, index=True)  # refs skills.id
    version: str = Field(max_length=32)
    description: str = Field(max_length=1024)
    keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    raw_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    storage_prefix: str = Field(max_length=512)
    entry_file: str = Field(max_length=128, default="SKILL.md")
    uploaded_by_user_id: str | None = Field(default=None, max_length=36)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skill_version"),)


class OrgSkillInstall(SQLModel, table=True):
    """Org-level install — admin promoted a skill into the org marketplace."""

    __tablename__ = "org_skill_installs"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)  # refs organizations.id
    skill_id: str = Field(max_length=36, index=True)  # refs skills.id
    installed_version: str = Field(max_length=32)
    installed_by_user_id: str = Field(max_length=36)  # refs users.id
    installed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # If True, skill is automatically active in all workspaces (no explicit binding needed).
    # If False, workspace admins must explicitly enable it per workspace.
    auto_bind: bool = Field(default=False)

    __table_args__ = (UniqueConstraint("org_id", "skill_id", name="uq_org_skill_install"),)


class WorkspaceSkillBinding(SQLModel, OrgScopedMixin, table=True):
    """Workspace-level enablement of an org-installed skill."""

    __tablename__ = "workspace_skill_bindings"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_skill_install_id: str = Field(max_length=36, index=True)  # refs org_skill_installs.id
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("workspace_id", "org_skill_install_id", name="uq_workspace_skill_binding"),
        Index("ix_wsb_org_ws", "org_id", "workspace_id"),
    )


class OrgPreinstalledTombstone(SQLModel, table=True):
    """Records that an org admin uninstalled a preinstalled skill; blocks reseed-restore."""

    __tablename__ = "org_preinstalled_tombstones"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True, max_length=36)
    org_id: str = Field(max_length=36, index=True)  # refs organizations.id
    skill_id: str = Field(max_length=36, index=True)  # refs skills.id
    hidden_by_user_id: str = Field(max_length=36)  # refs users.id
    hidden_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (UniqueConstraint("org_id", "skill_id", name="uq_org_preinstalled_tombstone"),)
