"""Unit tests for workspace MCP route registration.

Covers both the legacy ``/ws/{ws}/mcp/servers/...`` surface (which remains
mounted until Task 9 of the four-layer plan) and the new four-layer
routes added in Task 4.
"""

from cubebox.api.app import create_app


def _route_pairs(app: object) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for route in app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or not methods:
            continue
        for method in methods:
            pairs.add((method.upper(), path))
    return pairs


def test_workspace_mcp_legacy_routes_are_registered() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}

    # Legacy surface — keep until Task 9.
    assert "/api/v1/ws/{workspace_id}/mcp/servers" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/test-connection" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/promote-to-org" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/workspace-credential" in paths
    assert "/api/v1/ws/{workspace_id}/mcp/servers/{server_id}/my-credential" in paths


def test_workspace_mcp_four_layer_routes_are_registered() -> None:
    app = create_app()
    pairs = _route_pairs(app)

    expected: list[tuple[str, str]] = [
        ("GET", "/api/v1/ws/{workspace_id}/mcp/templates"),
        ("GET", "/api/v1/ws/{workspace_id}/mcp/connectors"),
        ("POST", "/api/v1/ws/{workspace_id}/mcp/installs"),
        ("DELETE", "/api/v1/ws/{workspace_id}/mcp/installs/{install_id}"),
        ("PATCH", "/api/v1/ws/{workspace_id}/mcp/connectors/{install_id}/state"),
        ("POST", "/api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/me"),
        ("DELETE", "/api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/me"),
        ("POST", "/api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/me/oauth/start"),
        ("POST", "/api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/workspace"),
        ("DELETE", "/api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/workspace"),
        (
            "POST",
            "/api/v1/ws/{workspace_id}/mcp/installs/{install_id}/grants/workspace/oauth/start",
        ),
    ]
    for method, path in expected:
        assert (method, path) in pairs, f"missing route: {method} {path}"
