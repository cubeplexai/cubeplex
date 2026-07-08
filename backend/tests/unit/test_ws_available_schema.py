import pytest

from cubebox.api.schemas.mcp_ws_available import WsAvailableOut


def test_ws_available_org_install_row():
    row = WsAvailableOut.model_validate(
        {
            "source": "org_install",
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
                "tool_count": 0,
                "tools": [],
                "tool_citations": {},
                "last_error": None,
                "auto_enroll_new_workspaces": False,
            },
            "template": None,
            "reason": "no_state_row",
        }
    )
    assert row.source == "org_install"
    assert row.install is not None
    assert row.reason == "no_state_row"


def test_ws_available_template_row_rejects_install():
    with pytest.raises(ValueError):
        WsAvailableOut.model_validate(
            {
                "source": "template",
                "install": {"install_id": "mcins-1"},  # forbidden
                "template": {
                    "template_id": "mctpl-1",
                    "slug": "notion",
                    "name": "Notion",
                    "provider": "Notion",
                    "description": "",
                    "server_url": "https://example.com",
                    "transport": "streamable_http",
                    "supported_auth_methods": ["oauth"],
                    "default_credential_policy": "org",
                    "static_form_schema": None,
                    "status": "active",
                },
                "reason": "not_installed_at_org",
            }
        )
