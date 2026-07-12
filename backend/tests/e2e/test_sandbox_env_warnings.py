"""E2E: OQ-6 symmetric warnings on the credential editor routes.

When the org has a SandboxPolicy that denies a host, creating or rotating a
credential whose ``hosts`` overlaps must surface the denied host(s) on the
response without rejecting the operation.
"""

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.db.engine import _build_database_url
from cubeplex.models.sandbox_policy import SandboxPolicy


@pytest_asyncio.fixture(autouse=True)
async def _ensure_sandbox_policy_table() -> None:
    """Provision the ``sandbox_policies`` table for this test.

    The schema is managed in production by Alembic (revision
    ``2f3d624337bd``); this fixture creates the table from SQLModel metadata
    (idempotent via ``checkfirst=True``) so the E2E suite runs against a test
    DB that hasn't had migrations applied.
    """
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: SandboxPolicy.__table__.create(sync_conn, checkfirst=True)
            )
    finally:
        await engine.dispose()


async def test_admin_create_credential_warns_on_denied_host(admin_client) -> None:
    client, _ws = admin_client
    # Install a deny policy first.
    put = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "network_rules": [{"action": "deny", "target": "api.github.com"}],
            "command_rules": None,
        },
    )
    assert put.status_code == 200, put.text

    # Create a credential that requires the denied host.
    resp = await client.post(
        "/api/v1/admin/sandbox-env",
        json={
            "env_name": "WARN_GITHUB",
            "is_secret": True,
            "hosts": ["api.github.com"],
            "secret_value": "ghp_x",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    warnings = body.get("warnings") or []
    assert any("api.github.com" in str(w) for w in warnings), warnings

    # Cleanup.
    await client.delete(f"/api/v1/admin/sandbox-env/{body['id']}")


async def test_admin_rotate_credential_keeps_warning(admin_client) -> None:
    client, _ws = admin_client
    # Create the credential before any policy is in place — no warnings yet.
    create = await client.post(
        "/api/v1/admin/sandbox-env",
        json={
            "env_name": "ROTATE_WARN",
            "is_secret": True,
            "hosts": ["api.github.com"],
            "secret_value": "first",
        },
    )
    assert create.status_code == 201, create.text
    entry_id = create.json()["id"]
    assert (create.json().get("warnings") or []) == []

    # Install a deny policy that conflicts with the existing credential.
    put = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "network_rules": [{"action": "deny", "target": "api.github.com"}],
            "command_rules": None,
        },
    )
    assert put.status_code == 200, put.text

    # Rotating the secret should now surface the warning.
    patch = await client.patch(
        f"/api/v1/admin/sandbox-env/{entry_id}",
        json={"secret_value": "rotated"},
    )
    assert patch.status_code == 200, patch.text
    warnings = patch.json().get("warnings") or []
    assert any("api.github.com" in str(w) for w in warnings), warnings

    await client.delete(f"/api/v1/admin/sandbox-env/{entry_id}")
