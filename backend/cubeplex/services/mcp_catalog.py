"""Pure catalog composition for admin and workspace MCP views.

Pure functions; the route layer wires repos and serializes.
Accepts duck-typed inputs so unit tests use dataclass stand-ins
instead of ORM rows — no session, no I/O in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AdminCatalogRow:
    template: Any  # MCPConnectorTemplate (duck-typed for tests)
    connector: Any | None
    disabled: bool
    enabled_workspace_count: int
    eligible_workspace_count: int
    auto_enroll_new_workspaces: bool
    org_grant_status: str | None  # 'valid' | 'expired' | None
    org_grant_auth_method: str | None  # 'oauth' | 'static' | None (None when no org grant)
    in_use: bool  # connector is not None
    needs_attention: bool  # expired org grant OR connector.discovery_status=='error'


@dataclass(frozen=True)
class WorkspaceCatalogRow:
    template: Any
    connector: Any | None
    enabled: bool  # this workspace's state row says enabled


def _derive_org_grant_status(grant: Any | None) -> str | None:
    """None when no grant; 'expired' when expired; else 'valid'."""
    if grant is None:
        return None
    if grant.grant_status == "expired":
        return "expired"
    return "valid"


def build_admin_catalog_rows(
    *,
    templates: list[Any],
    connectors_by_template_id: dict[str, Any],
    disabled_template_ids: set[str],
    enabled_counts_by_connector_id: dict[str, int],
    org_grants_by_connector_id: dict[str, Any],
    eligible_workspace_count: int,
) -> list[AdminCatalogRow]:
    """Build one AdminCatalogRow per template.

    Ordering: in-use rows first, then by template.name.lower().
    """
    rows: list[AdminCatalogRow] = []
    for template in templates:
        connector = connectors_by_template_id.get(template.id)
        in_use = connector is not None

        org_grant = org_grants_by_connector_id.get(connector.id) if connector is not None else None
        org_grant_status = _derive_org_grant_status(org_grant)
        org_grant_auth_method: str | None = (
            getattr(org_grant, "auth_method", None) if org_grant is not None else None
        )

        enabled_count = (
            enabled_counts_by_connector_id.get(connector.id, 0) if connector is not None else 0
        )

        needs_attention = (org_grant_status == "expired") or (
            connector is not None and connector.discovery_status == "error"
        )

        auto_enroll = (
            getattr(connector, "auto_enroll_new_workspaces", False)
            if connector is not None
            else False
        )

        rows.append(
            AdminCatalogRow(
                template=template,
                connector=connector,
                disabled=template.id in disabled_template_ids,
                enabled_workspace_count=enabled_count,
                eligible_workspace_count=eligible_workspace_count,
                auto_enroll_new_workspaces=auto_enroll,
                org_grant_status=org_grant_status,
                org_grant_auth_method=org_grant_auth_method,
                in_use=in_use,
                needs_attention=needs_attention,
            )
        )

    rows.sort(key=lambda r: (not r.in_use, r.template.name.lower()))
    return rows


def build_workspace_catalog_rows(
    *,
    templates: list[Any],  # already visibility-filtered (Task 4 query)
    connectors_by_template_id: dict[str, Any],
    states_by_connector_id: dict[str, Any],
    disabled_template_ids: set[str],
) -> list[WorkspaceCatalogRow]:
    """Build one WorkspaceCatalogRow per non-disabled template.

    Org-disabled templates are EXCLUDED entirely.
    Ordering: enabled rows first, then by template.name.lower().
    """
    rows: list[WorkspaceCatalogRow] = []
    for template in templates:
        if template.id in disabled_template_ids:
            continue

        connector = connectors_by_template_id.get(template.id)
        state = states_by_connector_id.get(connector.id) if connector is not None else None
        enabled = state.enabled if state is not None else False

        rows.append(
            WorkspaceCatalogRow(
                template=template,
                connector=connector,
                enabled=enabled,
            )
        )

    rows.sort(key=lambda r: (not r.enabled, r.template.name.lower()))
    return rows
