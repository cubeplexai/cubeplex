"""Admin connector list response shapes (GET /admin/mcp/connectors).

Lives in its own module so the existing schemas/mcp.py doesn't keep
growing. The DTOs here are admin-only: no workspace lens, per-workspace
state rolled up into one aggregate per row.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from cubebox.api.schemas.mcp import MCPConnectorOut, MCPConnectorTemplateOut

AdminOrgReason = Literal[
    "usable",
    "missing_org_grant",
    "pending_oauth",
    "grant_expired",
    "discovery_failed",
]

CredentialAvailability = Literal["available", "missing", "not_required"]


class AdminOrgEffectiveOut(BaseModel):
    """Per-org health for an org-scope install.

    ``credential_availability`` is ``None`` when the install's
    ``default_credential_policy`` is ``'workspace'`` or ``'user'`` —
    the admin doesn't supply those credentials, so the org row can't
    claim a status. The per-workspace breakdown lives in
    :class:`WorkspaceDistributionOut`.
    """

    usable: bool
    reason: AdminOrgReason
    credential_availability: CredentialAvailability | None


class WorkspaceDistributionOut(BaseModel):
    """Lightweight aggregate of per-workspace state rows for one install."""

    enabled_count: int
    disabled_count: int
    eligible_count: int
    auto_enroll_new_workspaces: bool


class AdminOrgConnectorOut(BaseModel):
    """One row of GET /admin/mcp/connectors."""

    install: MCPConnectorOut
    template: MCPConnectorTemplateOut | None
    org_effective: AdminOrgEffectiveOut
    workspace_distribution: WorkspaceDistributionOut


class AdminOrgConnectorListOut(BaseModel):
    items: list[AdminOrgConnectorOut]
