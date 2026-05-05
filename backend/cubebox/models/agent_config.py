"""AgentConfig — 1:1 with Workspace in M1.

Field-level CRUD lives in P5; P1 only creates the table so that a
default config can be seeded alongside the default workspace during
migration.
"""

from typing import ClassVar

from sqlalchemy import Column, Index, Text, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin


class AgentConfig(CubeboxBase, OrgScopedMixin, table=True):
    """Per-workspace agent configuration."""

    _PREFIX: ClassVar[str] = "agt"
    __tablename__ = "agent_configs"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uk_agent_configs_workspace"),
        Index("ix_agent_configs_org_ws", "org_id", "workspace_id"),
    )

    system_prompt: str = Field(default="", sa_column=Column(Text, nullable=False))
    model_id: str = Field(max_length=128)
    skill_ids: list[str] | None = Field(default=None, sa_column=Column(JSON))
    mcp_server_ids: list[str] | None = Field(default=None, sa_column=Column(JSON))
