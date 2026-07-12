"""Unit tests for workspace MCP route registration (four-layer surface only)."""

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


def test_workspace_mcp_four_layer_routes_are_registered() -> None:
    """Verify the workspace MCP template-centric API surface is fully registered."""
    app = create_app()
    pairs = _route_pairs(app)

    expected: list[tuple[str, str]] = [
        # Template catalog / management
        ("GET", "/api/v1/ws/{workspace_id}/mcp/catalog"),
        ("POST", "/api/v1/ws/{workspace_id}/mcp/templates"),
        ("PUT", "/api/v1/ws/{workspace_id}/mcp/templates/{template_id}/state"),
        ("POST", "/api/v1/ws/{workspace_id}/mcp/templates/{template_id}/promote"),
        # Connector / active-tools
        ("GET", "/api/v1/ws/{workspace_id}/mcp/connectors"),
        ("GET", "/api/v1/ws/{workspace_id}/mcp/active-tools"),
        # Grant endpoints (connector-keyed)
        ("POST", "/api/v1/ws/{workspace_id}/mcp/installs/{connector_id}/grants/me"),
        ("DELETE", "/api/v1/ws/{workspace_id}/mcp/installs/{connector_id}/grants/me"),
        (
            "POST",
            "/api/v1/ws/{workspace_id}/mcp/installs/{connector_id}/grants/me/oauth/start",
        ),
        ("POST", "/api/v1/ws/{workspace_id}/mcp/installs/{connector_id}/grants/workspace"),
        (
            "DELETE",
            "/api/v1/ws/{workspace_id}/mcp/installs/{connector_id}/grants/workspace",
        ),
        (
            "POST",
            "/api/v1/ws/{workspace_id}/mcp/installs/{connector_id}/grants/workspace/oauth/start",
        ),
    ]
    for method, path in expected:
        assert (method, path) in pairs, f"missing route: {method} {path}"
