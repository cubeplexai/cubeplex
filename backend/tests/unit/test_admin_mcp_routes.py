"""Unit tests for admin MCP route registration.

Covers both the legacy ``/admin/mcp/servers/...`` surface (which remains
mounted until Task 9 of the four-layer plan) and the new four-layer
routes added in Task 4.
"""

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


def test_admin_mcp_legacy_routes_are_registered() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}

    # Legacy surface — keep until Task 9.
    assert "/api/v1/admin/mcp/servers" in paths
    assert "/api/v1/admin/mcp/test-connection" in paths
    assert "/api/v1/admin/mcp/servers/{server_id}/overrides" in paths


def test_admin_mcp_four_layer_routes_are_registered() -> None:
    app = create_app()
    pairs = _route_pairs(app)

    expected: list[tuple[str, str]] = [
        ("GET", "/api/v1/admin/mcp/templates"),
        ("GET", "/api/v1/admin/mcp/installs"),
        ("POST", "/api/v1/admin/mcp/installs"),
        ("GET", "/api/v1/admin/mcp/installs/{install_id}"),
        ("PATCH", "/api/v1/admin/mcp/installs/{install_id}"),
        ("DELETE", "/api/v1/admin/mcp/installs/{install_id}"),
        ("POST", "/api/v1/admin/mcp/installs/{install_id}/grants/org"),
        ("DELETE", "/api/v1/admin/mcp/installs/{install_id}/grants/org"),
        ("POST", "/api/v1/admin/mcp/installs/{install_id}/grants/org/oauth/start"),
    ]
    for method, path in expected:
        assert (method, path) in pairs, f"missing route: {method} {path}"


def test_public_template_route_is_registered() -> None:
    app = create_app()
    pairs = _route_pairs(app)
    assert ("GET", "/api/v1/mcp/templates") in pairs
