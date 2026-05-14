"""Unit tests for GET/PATCH /ws/{wsId}/mcp/servers/{serverId}/tool-citations routes."""

from cubebox.api.app import create_app


def test_tool_citations_routes_are_registered() -> None:
    """Both tool-citations routes must be registered in the app."""
    app = create_app()
    paths = {route.path for route in app.routes}

    assert "/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/tool-citations" in paths
