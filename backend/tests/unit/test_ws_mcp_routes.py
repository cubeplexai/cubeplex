"""Unit tests for workspace MCP route registration."""

from cubebox.api.app import create_app


def test_workspace_mcp_routes_are_registered() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/api/v1/ws/{workspace_id}/mcp/servers" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/test-connection" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/promote-to-org" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/workspace-credential" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/my-credential" in paths
