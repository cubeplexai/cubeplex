"""Skill catalog models."""

from datetime import UTC, datetime
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy import JSON, Column, DateTime, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from cubebox.models.mixins import CubeboxBase, TimestampMixin


class Skill(CubeboxBase, table=True):
    """Global catalog row.

    source='preinstalled' → owner_org_id=NULL; name is bare slug.
    source='uploaded'     → owner_org_id=<publisher org>; name is '<org-slug>:<skill-slug>'.

    imported_from_registry_id is set when the skill was installed via a remote
    registry (e.g. skills.sh); NULL for preinstalled and manually uploaded skills.
    """

    _PREFIX: ClassVar[str] = "skl"
    __tablename__ = "skills"

    name: str = Field(max_length=128)
    source: str = Field(max_length=16)  # "preinstalled" | "uploaded"
    owner_org_id: str | None = Field(
        default=None, foreign_key="organizations.id", max_length=20, index=True
    )
    imported_from_registry_id: str | None = Field(
        default=None, foreign_key="skill_registries.id", max_length=20, nullable=True
    )
    imported_from_source_ref: str | None = Field(default=None, max_length=512, nullable=True)
    current_version: str = Field(max_length=32)
    description: str = Field(max_length=1024)
    keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    deprecated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_skill_name"),
        Index("ix_skill_source_owner", "source", "owner_org_id"),
    )


class SkillVersion(CubeboxBase, table=True):
    """Immutable version row. New versions append; old rows never modified."""

    _PREFIX: ClassVar[str] = "sklv"
    __tablename__ = "skill_versions"

    skill_id: str = Field(foreign_key="skills.id", max_length=20, index=True)
    version: str = Field(max_length=32)
    description: str = Field(max_length=1024)
    keywords: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    raw_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    storage_prefix: str = Field(max_length=512)
    entry_file: str = Field(max_length=128, default="SKILL.md")
    uploaded_by_user_id: str | None = Field(default=None, foreign_key="users.id", max_length=20)

    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skill_version"),)


class OrgSkillInstall(CubeboxBase, table=True):
    """Org-level install — admin promoted a skill into the org marketplace.

    workspace_id=None → org-wide install (visible to all workspaces).
    workspace_id=<id>  → workspace-private install (visible only to that workspace).
    """

    _PREFIX: ClassVar[str] = "osi"
    __tablename__ = "org_skill_installs"

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    skill_id: str = Field(foreign_key="skills.id", max_length=20, index=True)
    workspace_id: str | None = Field(
        default=None, foreign_key="workspaces.id", max_length=20, nullable=True, index=True
    )
    installed_version: str = Field(max_length=32)
    installed_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )
    installed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    auto_bind: bool = Field(default=False)

    __table_args__ = (
        # Org-wide installs: unique per (org, skill) where workspace is null
        Index(
            "uq_org_skill_install_org_wide",
            "org_id",
            "skill_id",
            unique=True,
            postgresql_where=sa.text("workspace_id IS NULL"),
        ),
        # Workspace-private installs: unique per (org, workspace, skill)
        UniqueConstraint("org_id", "workspace_id", "skill_id", name="uq_org_skill_install_ws"),
        Index("ix_osi_org_workspace", "org_id", "workspace_id"),
    )


class WorkspaceSkillBinding(SQLModel, TimestampMixin, table=True):
    """Workspace-level enablement of an org-installed skill.

    Pure association — composite PK; no public_id."""

    __allow_unmapped__ = True
    __tablename__ = "workspace_skill_bindings"
    __table_args__ = (Index("ix_wsb_org_ws", "org_id", "workspace_id"),)

    org_id: str = Field(foreign_key="organizations.id", max_length=20, index=True)
    workspace_id: str = Field(
        primary_key=True, foreign_key="workspaces.id", max_length=20, index=True
    )
    org_skill_install_id: str = Field(
        primary_key=True, foreign_key="org_skill_installs.id", max_length=20, index=True
    )
    enabled: bool = Field(default=True)


class OrgPreinstalledTombstone(SQLModel, TimestampMixin, table=True):
    """Records that an org admin uninstalled a preinstalled skill; blocks reseed-restore.

    Pure state marker — composite PK; no public_id."""

    __allow_unmapped__ = True
    __tablename__ = "org_preinstalled_tombstones"

    org_id: str = Field(primary_key=True, foreign_key="organizations.id", max_length=20, index=True)
    skill_id: str = Field(primary_key=True, foreign_key="skills.id", max_length=20, index=True)
    hidden_by_user_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=20, nullable=True
    )
    hidden_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
