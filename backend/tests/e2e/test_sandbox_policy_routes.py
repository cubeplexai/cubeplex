"""E2E for org-admin sandbox policy routes.

``admin_client`` yields ``(client, workspace_id)`` — unpack before use.
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


async def test_get_returns_defaults_when_unset(admin_client) -> None:
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/sandbox-policy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_image"]  # a non-empty default
    assert body["network_rules"] == []
    assert body["command_rules"] == []


async def test_put_then_get_roundtrip(admin_client) -> None:
    client, _ws = admin_client
    put = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "python:3.12",
            "network_rules": [{"action": "deny", "target": "evil.example.com"}],
            "command_rules": [{"action": "deny", "pattern": "rm *"}],
        },
    )
    assert put.status_code == 200, put.text
    assert put.json().get("warnings") == []
    got = await client.get("/api/v1/admin/sandbox-policy")
    body = got.json()
    assert body["default_image"] == "python:3.12"
    assert body["command_rules"] == [{"action": "deny", "pattern": "rm *"}]
    assert body["network_default_action"] == "deny"


async def test_put_then_get_resource_limits_roundtrip(admin_client) -> None:
    client, _ws = admin_client
    put = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "resource_cpu": "500m",
            "resource_memory": "2Gi",
            "storage": "10Gi",
        },
    )
    assert put.status_code == 200, put.text
    got = (await client.get("/api/v1/admin/sandbox-policy")).json()
    assert got["resource_cpu"] == "500m"
    assert got["resource_memory"] == "2Gi"
    assert got["storage"] == "10Gi"


async def test_put_rejects_bad_resource_quantity(admin_client) -> None:
    client, _ws = admin_client
    resp = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={"default_image": "ubuntu:22.04", "resource_memory": "2 gigs"},
    )
    assert resp.status_code == 400


async def test_put_rejects_bad_network_target(admin_client) -> None:
    client, _ws = admin_client
    resp = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "network_rules": [{"action": "allow", "target": "*"}],
            "command_rules": None,
        },
    )
    assert resp.status_code == 400


async def test_put_warns_on_credential_host_conflict(
    admin_client, seeded_credential_with_host
) -> None:
    """OQ-6: deny on a host that an installed credential requires returns a
    warnings[] entry, but the PUT is NOT rejected — the policy still saves."""
    client, _ws = admin_client
    cred = seeded_credential_with_host
    resp = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "network_rules": [{"action": "deny", "target": "api.github.com"}],
            "command_rules": None,
        },
    )
    assert resp.status_code == 200, resp.text
    warnings = resp.json().get("warnings") or []
    assert any(cred["id"] in str(w) or "api.github.com" in str(w) for w in warnings)
    # Confirm the policy DID save despite the warning.
    got = await client.get("/api/v1/admin/sandbox-policy")
    assert got.json()["network_rules"] == [{"action": "deny", "target": "api.github.com"}]


async def test_put_warns_on_wildcard_credential_host_conflict(
    admin_client, seeded_credential_with_host
) -> None:
    """Regression for codex P2 r3317630110: a wildcard deny rule that covers
    the credential's required host (e.g. *.github.com vs api.github.com) must
    surface the same warning shape as the exact-match case — otherwise the
    runtime would block egress while the UI silently says nothing."""
    client, _ws = admin_client
    cred = seeded_credential_with_host  # required_hosts includes api.github.com
    resp = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "network_rules": [{"action": "deny", "target": "*.github.com"}],
            "command_rules": None,
        },
    )
    assert resp.status_code == 200, resp.text
    warnings = resp.json().get("warnings") or []
    assert any(
        cred["id"] in str(w) or "api.github.com" in str(w) or "*.github.com" in str(w)
        for w in warnings
    ), f"Expected a wildcard-deny conflict warning, got: {warnings}"


def test_wildcard_credential_host_with_exact_deny_overlap_unit() -> None:
    """Regression for codex P2 r3317695593: the SYMMETRIC direction — a
    credential declares a wildcard host (`*.github.com`) and the admin saves
    an exact deny (`api.github.com`) — must also raise a conflict warning,
    because the runtime will block that subdomain. The previous one-way
    fnmatchcase(host, target) check missed this. Unit-level since we don't
    need to drive through HTTP to validate the overlap rule itself."""
    from types import SimpleNamespace

    from cubeplex.services.sandbox_policy_conflicts import (
        credential_conflict_warnings,
        deny_targets_for_cred,
    )

    cred = SimpleNamespace(id="cred-1", env_name="GITHUB_TOKEN", hosts=["*.github.com"])
    network_rules = [{"action": "deny", "target": "api.github.com"}]

    warnings = credential_conflict_warnings(network_rules, [cred])
    assert warnings, "Expected a conflict warning for *.github.com vs deny api.github.com"
    assert "api.github.com" in warnings[0] or "*.github.com" in warnings[0]

    # And the symmetric helper used by the credential editor route.
    blocked = deny_targets_for_cred(["*.github.com"], network_rules)
    assert blocked == ["*.github.com"]


async def test_put_roundtrips_allow_default_with_deny_rule(admin_client) -> None:
    client, _ws = admin_client
    put = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "network_default_action": "allow",
            "network_rules": [{"action": "deny", "target": "*.evil.com"}],
            "command_rules": None,
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["network_default_action"] == "allow"
    got = await client.get("/api/v1/admin/sandbox-policy")
    body = got.json()
    assert body["network_default_action"] == "allow"
    assert {"action": "deny", "target": "*.evil.com"} in body["network_rules"]


async def test_get_defaults_action_to_deny_when_unset(admin_client) -> None:
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/sandbox-policy")
    assert resp.status_code == 200
    assert resp.json()["network_default_action"] == "deny"


async def test_put_rejects_contradictory_network_rules(admin_client) -> None:
    client, _ws = admin_client
    resp = await client.put(
        "/api/v1/admin/sandbox-policy",
        json={
            "default_image": "ubuntu:22.04",
            "network_default_action": "allow",
            "network_rules": [
                {"action": "allow", "target": "api.github.com"},
                {"action": "deny", "target": "API.GITHUB.COM"},
            ],
            "command_rules": None,
        },
    )
    assert resp.status_code == 400
