"""MCP credential layering API invariants.

The old tests used POST /admin/mcp/installs and POST /ws/{ws}/mcp/installs to
test cross-scope coexistence. Both routes are gone in the template-centric model
(Task 9). The invariants they protected:

  R1: workspace credentials and org provisioning are separate layers —
      equivalent today: distribute() is idempotent; calling it twice never 409s.
  R2: workspace enablement is state over the org connector identity —
      workspace enable/disable is now a workspace-route concern; test deferred
      to Task 10 (ws_mcp rewrite).

Tests that depend on ws_mcp are left commented out to preserve the original
intent as a reference for the Task 10 author.
"""

from __future__ import annotations

import secrets

import httpx
import pytest

pytestmark = pytest.mark.usefixtures("stub_discover_tools")


async def test_distribute_is_idempotent_no_409(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Calling distribute twice on the same template never raises a conflict.

    This replaces the old R1 test (workspace install + org install same template)
    with the equivalent template-centric invariant: the service's idempotency
    guard means the second distribute is a no-op rather than a 409.
    """
    client, _ws = admin_client
    suffix = secrets.token_hex(4)

    tpl_resp = await client.post(
        "/api/v1/admin/mcp/templates",
        json={
            "name": f"Layering Idempotent {suffix}",
            "server_url": f"https://layering-idem-{suffix}.example.com/mcp",
            "transport": "streamable_http",
            "auth_method": "none",
            "default_credential_policy": "none",
        },
    )
    assert tpl_resp.status_code == 201, tpl_resp.text
    template_id = tpl_resp.json()["template_id"]

    first = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": True},
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        f"/api/v1/admin/mcp/templates/{template_id}/distribute",
        json={"enable_existing": True, "auto_enroll": True},
    )
    assert second.status_code == 200, second.text
    # Same connector returned both times — no duplication.
    assert first.json()["connector"]["connector_id"] == second.json()["connector"]["connector_id"]
