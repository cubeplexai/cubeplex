"""Derivation helpers for GET /admin/mcp/connectors.

Pure functions + small data-only helpers; the route layer wires repos
and serializes. Tests target this module directly without spinning up
FastAPI.
"""

from __future__ import annotations

from typing import Any

from cubebox.api.schemas.mcp_admin_connector import (
    AdminOrgEffectiveOut,
    WorkspaceDistributionOut,
)
from cubebox.models.mcp import (
    MCPConnector,
    MCPWorkspaceConnectorState,
)


def derive_admin_org_effective(
    install: Any,
    org_grant: Any,
) -> AdminOrgEffectiveOut:
    """Spec §3.1: org_effective branches by default_credential_policy.

    - ``auth_method='none'`` → usable, credential_availability='not_required'
    - ``default_credential_policy in {'workspace','user'}`` → admin can't
      supply credentials; reason ∈ {usable, discovery_failed} only,
      credential_availability=None.
    - ``default_credential_policy='org'`` → existing org-grant decision
      table (same ordering as the now-deleted
      ``_derive_admin_org_effective`` in admin_mcp.py rule 1–6).

    Accepts duck-typed install + grant so unit tests can use dataclasses;
    the route always passes real ORM rows.
    """
    if install.auth_method == "none":
        return AdminOrgEffectiveOut(
            usable=True, reason="usable", credential_availability="not_required"
        )

    if install.default_credential_policy in {"workspace", "user"}:
        if install.discovery_status == "error":
            return AdminOrgEffectiveOut(
                usable=False,
                reason="discovery_failed",
                credential_availability=None,
            )
        return AdminOrgEffectiveOut(usable=True, reason="usable", credential_availability=None)

    # default_credential_policy == "org"
    if org_grant is None:
        if install.auth_method == "oauth" and install.auth_status == "pending":
            return AdminOrgEffectiveOut(
                usable=False,
                reason="pending_oauth",
                credential_availability="missing",
            )
        return AdminOrgEffectiveOut(
            usable=False,
            reason="missing_org_grant",
            credential_availability="missing",
        )

    if org_grant.grant_status == "expired" and org_grant.refresh_credential_id is None:
        return AdminOrgEffectiveOut(
            usable=False, reason="grant_expired", credential_availability="missing"
        )

    if install.discovery_status == "error":
        return AdminOrgEffectiveOut(
            usable=False,
            reason="discovery_failed",
            credential_availability="available",
        )

    return AdminOrgEffectiveOut(usable=True, reason="usable", credential_availability="available")


def build_workspace_distribution(
    *,
    install: MCPConnector,
    state_rows: list[MCPWorkspaceConnectorState],
    eligible_workspace_count: int,
) -> WorkspaceDistributionOut:
    """Roll state rows for one install into the admin row's aggregate.

    ``state_rows`` is the output of
    ``MCPWorkspaceConnectorStateRepository.list_for_install(install.id)``;
    ``eligible_workspace_count`` is the total workspace count for the org
    (so the UI can render "5/12"). Computed once per request, not per row.
    """
    enabled = sum(1 for r in state_rows if r.enabled)
    disabled = sum(1 for r in state_rows if not r.enabled)
    return WorkspaceDistributionOut(
        enabled_count=enabled,
        disabled_count=disabled,
        eligible_count=eligible_workspace_count,
        auto_enroll_new_workspaces=install.auto_enroll_new_workspaces,
    )
