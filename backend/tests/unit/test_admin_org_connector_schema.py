from cubebox.api.schemas.mcp_admin_connector import (
    AdminOrgConnectorOut,
    AdminOrgEffectiveOut,
    WorkspaceDistributionOut,
)


def test_admin_org_connector_serializes_minimum_set():
    eff = AdminOrgEffectiveOut(
        usable=True,
        reason="usable",
        credential_availability="available",
    )
    dist = WorkspaceDistributionOut(
        enabled_count=2,
        disabled_count=1,
        eligible_count=4,
        auto_enroll_new_workspaces=False,
    )
    out = AdminOrgConnectorOut.model_validate(
        {
            "install": {
                "install_id": "mcins-1",
                "connector_id": "mcpco-1",
                "template_id": "mctpl-1",
                "install_scope": "org",
                "workspace_id": None,
                "name": "Notion",
                "server_url": "https://example.com/mcp",
                "transport": "streamable_http",
                "auth_method": "oauth",
                "default_credential_policy": "org",
                "auth_status": "authorized",
                "discovery_status": "ok",
                "install_state": "active",
                "tool_count": 3,
                "tools": [],
                "tool_citations": {},
                "last_error": None,
                "auto_enroll_new_workspaces": False,
            },
            "template": None,
            "org_effective": eff.model_dump(),
            "workspace_distribution": dist.model_dump(),
        }
    )
    assert out.org_effective.reason == "usable"
    assert out.workspace_distribution.eligible_count == 4


def test_admin_org_connector_allows_null_credential_availability():
    eff = AdminOrgEffectiveOut(
        usable=True,
        reason="usable",
        credential_availability=None,
    )
    assert eff.credential_availability is None
