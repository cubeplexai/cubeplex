"""Workspace 'available connectors' response shape.

GET /api/v1/ws/{workspace_id}/mcp/available returns rows the workspace
can opt into (org installs not yet enabled in this workspace + templates
the workspace doesn't already have).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

from cubebox.api.schemas.mcp import MCPConnectorOut, MCPConnectorTemplateOut

WsAvailableSource = Literal["org_install", "template"]
WsAvailableReason = Literal[
    "no_state_row",
    "state_disabled",
    "not_installed_at_org",
]


class WsAvailableOut(BaseModel):
    """One row of GET /ws/{ws}/mcp/available.

    Cross-field invariants (enforced by the validator):

    - ``source='org_install'`` requires ``install`` set; the install is
      org-scope.
    - ``source='template'`` requires ``install`` null and ``template``
      set.
    - ``template`` may be null only when ``source='org_install'`` and the
      install was created as custom (no template id).
    """

    source: WsAvailableSource
    install: MCPConnectorOut | None
    template: MCPConnectorTemplateOut | None
    reason: WsAvailableReason

    @model_validator(mode="after")
    def _validate_shape(self) -> WsAvailableOut:
        if self.source == "org_install":
            if self.install is None:
                raise ValueError("source='org_install' requires install")
        else:
            if self.install is not None:
                raise ValueError("source='template' must not carry install")
            if self.template is None:
                raise ValueError("source='template' requires template")
        return self


class WsAvailableListOut(BaseModel):
    items: list[WsAvailableOut]
