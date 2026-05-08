"""Unit tests for admin MCP route registration."""

from cubebox.api.app import create_app


def test_admin_mcp_routes_are_registered() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/api/v1/admin/mcp/servers" in paths
    assert "/api/v1/admin/mcp/test-connection" in paths
    assert "/api/v1/admin/mcp/servers/{server_id}/overrides" in paths
