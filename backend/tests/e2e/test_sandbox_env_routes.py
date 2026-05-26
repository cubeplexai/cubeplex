"""E2E tests for sandbox env vault routes.

The ``admin_client`` / ``member_client`` fixtures from tests/e2e/conftest.py
yield a tuple ``(client, workspace_id)`` — always unpack before use.
"""

# ---------------------------------------------------------------------------
# Org-scope admin routes (Task 9)
# ---------------------------------------------------------------------------


async def test_admin_create_and_delete_org_secret(admin_client):
    client, _ws = admin_client
    resp = await client.post(
        "/api/v1/admin/sandbox-env",
        json={
            "env_name": "GITHUB_TOKEN",
            "is_secret": True,
            "hosts": ["api.github.com"],
            "secret_value": "ghp_x",
        },
    )
    assert resp.status_code == 201
    entry = resp.json()
    assert entry["scope"] == "org"
    assert "secret_value" not in entry  # never leaked

    del_resp = await client.delete(f"/api/v1/admin/sandbox-env/{entry['id']}")
    assert del_resp.status_code == 204


async def test_admin_rejects_bad_host(admin_client):
    client, _ws = admin_client
    resp = await client.post(
        "/api/v1/admin/sandbox-env",
        json={"env_name": "X", "is_secret": True, "hosts": ["*.com"], "secret_value": "v"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Workspace/user-scope routes (Task 10)
# ---------------------------------------------------------------------------


async def test_member_sets_own_user_env(member_client):
    client, workspace_id = member_client
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/sandbox-env/me",
        json={
            "env_name": "GITHUB_TOKEN",
            "is_secret": True,
            "hosts": ["api.github.com"],
            "secret_value": "ghp_u",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["scope"] == "user"


async def test_member_cannot_set_workspace_env(member_client):
    client, workspace_id = member_client
    resp = await client.post(
        f"/api/v1/ws/{workspace_id}/sandbox-env/workspace",
        json={"env_name": "X", "is_secret": False, "plain_value": "v"},
    )
    assert resp.status_code == 403  # require_admin
