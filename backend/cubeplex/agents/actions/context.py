"""Scoped context for agent platform actions."""

from __future__ import annotations

from dataclasses import dataclass

from cubeplex.models.membership import Role


@dataclass(frozen=True)
class ScopeContext:
    """Everything an operation needs to be scoped and authorized."""

    org_id: str
    workspace_id: str
    user_id: str
    role: Role
    conversation_id: str | None = None
