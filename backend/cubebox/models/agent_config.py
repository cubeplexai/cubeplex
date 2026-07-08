"""AgentConfig — 1:1 with Workspace.

Holds the per-workspace persona prompt (and, post-M4, the model selection).
The skill set and MCP connector set are *not* stored here — skills live in
WorkspaceSkillBinding / OrgSkillInstall, and MCP connectors live in the
four-layer ``MCPConnector`` + ``MCPWorkspaceConnectorState`` rows.
"""

from typing import ClassVar

from sqlalchemy import Column, Index, Text, UniqueConstraint
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
    model_id: str = Field(default="", max_length=128)
