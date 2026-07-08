"""Workspace 'available connectors' computation.

Pure function: in goes (workspace id, org installs, ws installs, ws state
rows, templates), out goes the list of rows that the workspace can opt
into. Spec §3.2 invariants enforced here, not at the route layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cubebox.api.schemas.mcp_ws_available import WsAvailableReason, WsAvailableSource


@dataclass(frozen=True)
class WsAvailableRow:
    """Service-layer row; route serializes into ``WsAvailableOut``."""

    source: WsAvailableSource
    connector_id: str | None
    template_id: str | None
    reason: WsAvailableReason


def compute_available_rows(
    *,
    ws_id: str,
    org_installs: list[Any],
    ws_installs: list[Any],
    ws_states: list[Any],
    templates: list[Any],
) -> list[WsAvailableRow]:
    """Spec §3.2 list composition.

    - org_installs: all active org-scope installs in this org.
    - ws_installs: workspace-scope installs owned by this workspace
      (includes tombstones so the active-only filter is explicit here).
    - ws_states: this workspace's state rows.
    - templates: all active templates in the catalog.

    Output ordering: org rows first (by connector_id), then template rows
    (by template_id). Stable so the frontend can compare against the
    previous fetch.
    """
    state_by_install = {s.connector_id: s for s in ws_states}

    org_rows: list[WsAvailableRow] = []
    for install in org_installs:
        state = state_by_install.get(install.id)
        if state is not None and state.enabled:
            continue
        reason: WsAvailableReason = "state_disabled" if state is not None else "no_state_row"
        org_rows.append(
            WsAvailableRow(
                source="org_install",
                connector_id=install.id,
                template_id=install.template_id,
                reason=reason,
            )
        )

    # Templates reachable via an org install are surfaced under
    # source='org_install' above; templates the workspace already owns
    # (active workspace-scope install) cannot install again — exclude.
    org_template_ids = {i.template_id for i in org_installs if i.template_id is not None}
    active_ws_template_ids = {
        i.template_id
        for i in ws_installs
        if i.install_state == "active" and i.template_id is not None
    }

    template_rows: list[WsAvailableRow] = []
    for template in templates:
        if template.status != "active":
            continue
        if template.id in org_template_ids:
            continue
        if template.id in active_ws_template_ids:
            continue
        template_rows.append(
            WsAvailableRow(
                source="template",
                connector_id=None,
                template_id=template.id,
                reason="not_installed_at_org",
            )
        )

    org_rows.sort(key=lambda r: r.connector_id or "")
    template_rows.sort(key=lambda r: r.template_id or "")
    return org_rows + template_rows
