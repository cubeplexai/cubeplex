"""Unit tests for admin MCP routes and their error contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from cubeplex.api.app import create_app
from cubeplex.api.routes.v1.admin_mcp import (
    _is_connector_slug_unique_violation,
    patch_admin_install,
)
from cubeplex.api.schemas.mcp import PatchInstallIn


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


def test_connector_slug_unique_violation_is_classified() -> None:
    """Only the connector slug constraint maps to the name-conflict response."""
    slug_error = IntegrityError(
        "UPDATE mcp_connectors",
        {},
        SimpleNamespace(diag=SimpleNamespace(constraint_name="uq_mcp_connector_slug_per_org")),
    )
    other_error = IntegrityError(
        "UPDATE mcp_connectors",
        {},
        SimpleNamespace(diag=SimpleNamespace(constraint_name="some_other_constraint")),
    )

    assert _is_connector_slug_unique_violation(slug_error)
    assert not _is_connector_slug_unique_violation(other_error)


def test_connector_slug_unique_violation_uses_error_text_fallback() -> None:
    """Drivers without PostgreSQL diagnostics still classify the slug conflict."""
    error = IntegrityError(
        "UPDATE mcp_connectors",
        {},
        "duplicate key violates uq_mcp_connector_slug_per_org",
    )

    assert _is_connector_slug_unique_violation(error)


def _patch_install_fakes(error: IntegrityError) -> tuple[object, object, object]:
    install = SimpleNamespace(
        id="mcpco-test",
        template_id="mcpt-test",
        name="old-name",
        auth_method="none",
        default_credential_policy="none",
        server_url_hash="hash",
    )
    session = MagicMock()
    session.no_autoflush = MagicMock()
    session.rollback = AsyncMock()
    repo = SimpleNamespace(
        session=session,
        get=AsyncMock(return_value=install),
        update=AsyncMock(side_effect=error),
    )
    service = SimpleNamespace(
        _install_repo=repo,
        _has_install_conflict=AsyncMock(return_value=False),
    )
    ctx = SimpleNamespace(org_id="org-test", user=SimpleNamespace(id="user-test"))
    return service, ctx, session


@pytest.mark.asyncio
async def test_patch_admin_install_maps_slug_integrity_error_to_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A race after preflight still returns the stable 409 conflict contract."""
    error = IntegrityError(
        "UPDATE mcp_connectors",
        {},
        SimpleNamespace(diag=SimpleNamespace(constraint_name="uq_mcp_connector_slug_per_org")),
    )
    service, ctx, session = _patch_install_fakes(error)
    monkeypatch.setattr(
        "cubeplex.api.routes.v1.admin_mcp._reject_if_template_disabled",
        AsyncMock(),
    )

    with pytest.raises(HTTPException) as raised:
        await patch_admin_install(
            "mcpco-test",
            PatchInstallIn(name="new-name"),
            service,  # type: ignore[arg-type]
            ctx,  # type: ignore[arg-type]
            MagicMock(),
        )

    assert raised.value.status_code == 409
    assert session.rollback.await_count == 1


@pytest.mark.asyncio
async def test_patch_admin_install_reraises_other_integrity_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the known slug constraint is translated; unrelated DB errors survive."""
    error = IntegrityError(
        "UPDATE mcp_connectors",
        {},
        SimpleNamespace(diag=SimpleNamespace(constraint_name="other_constraint")),
    )
    service, ctx, session = _patch_install_fakes(error)
    monkeypatch.setattr(
        "cubeplex.api.routes.v1.admin_mcp._reject_if_template_disabled",
        AsyncMock(),
    )

    with pytest.raises(IntegrityError, match="UPDATE mcp_connectors"):
        await patch_admin_install(
            "mcpco-test",
            PatchInstallIn(name="new-name"),
            service,  # type: ignore[arg-type]
            ctx,  # type: ignore[arg-type]
            MagicMock(),
        )

    assert session.rollback.await_count == 1
