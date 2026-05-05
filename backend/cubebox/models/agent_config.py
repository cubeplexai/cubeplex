"""AgentConfig — 1:1 with Workspace in M1.

Field-level CRUD lives in P5; P1 only creates the table so that a
default config can be seeded alongside the default workspace during
migration.
"""

from datetime import UTC, datetime

from sqlalchemy import Column, Index, Text, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from cubebox.models.mixins import OrgScopedMixin
from cubebox.models.public_id import PREFIX_AGENT_CONFIG, generate_public_id


class AgentConfig(SQLModel, OrgScopedMixin, table=True):
    __tablename__ = "agent_configs"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uk_agent_configs_workspace"),
        Index("ix_agent_configs_org_ws", "org_id", "workspace_id"),
    )

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_AGENT_CONFIG),
        primary_key=True,
        max_length=20,
    )
    system_prompt: str = Field(default="", sa_column=Column(Text, nullable=False))
    model_id: str = Field(max_length=128)
    skill_ids: list[str] | None = Field(default=None, sa_column=Column(JSON))
    mcp_server_ids: list[str] | None = Field(default=None, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
