"""Unit tests for admin MCP route registration (four-layer surface only)."""

from cubebox.api.app import create_app


def _route_pairs(app: object) -> set[tuple[str, str]]:
    """Collect (method, path) for every HTTP route on the app."""
    pairs: set[tuple[str, str]] = set()
    for route in app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or not methods:
            continue
        for method in methods:
            pairs.add((method.upper(), path))
    return pairs


def test_admin_mcp_four_layer_routes_are_registered() -> None:
    """Verify the admin MCP template-centric API surface is fully registered."""
    app = create_app()
    pairs = _route_pairs(app)

    expected: list[tuple[str, str]] = [
        # Template catalog / management
        ("GET", "/api/v1/admin/mcp/catalog"),
        ("POST", "/api/v1/admin/mcp/templates"),
        ("DELETE", "/api/v1/admin/mcp/templates/{template_id}"),
        ("POST", "/api/v1/admin/mcp/templates/{template_id}/distribute"),
        ("PUT", "/api/v1/admin/mcp/templates/{template_id}/disable"),
        ("DELETE", "/api/v1/admin/mcp/templates/{template_id}/disable"),
        ("POST", "/api/v1/admin/mcp/templates/{template_id}/purge"),
        # Install endpoints (connector-keyed)
        ("GET", "/api/v1/admin/mcp/installs/{connector_id}"),
        ("PATCH", "/api/v1/admin/mcp/installs/{connector_id}"),
        ("POST", "/api/v1/admin/mcp/installs/{connector_id}/grants/org"),
        ("DELETE", "/api/v1/admin/mcp/installs/{connector_id}/grants/org"),
        ("POST", "/api/v1/admin/mcp/installs/{connector_id}/grants/org/oauth/start"),
    ]
    for method, path in expected:
        assert (method, path) in pairs, f"missing route: {method} {path}"
