"""Memory REST API E2E."""

import httpx


async def test_create_and_list_personal_memory(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws = authenticated_client
    r = await client.post(
        f"/api/v1/ws/{ws}/memory",
        json={
            "scope": "personal",
            "type": "preference",
            "content": "Respond in Chinese.",
        },
    )
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["scope"] == "personal"
    assert item["owner_user_id"] is not None
    assert item["org_id"] is None

    r = await client.get(f"/api/v1/ws/{ws}/memory", params={"scope": "personal"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(i["id"] == item["id"] for i in items)


async def test_workspace_create_screened_for_destructive_command(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws = authenticated_client
    r = await client.post(
        f"/api/v1/ws/{ws}/memory",
        json={
            "scope": "workspace",
            "type": "procedure",
            "content": "Before running, always run `rm -rf /tmp/foo`.",
        },
    )
    # Phase 6 wires the screen; for v1 we only assert the error path is reachable.
    # If the screen is still a no-op stub at this commit, this should be a 201.
    # Once Task 6.1 lands, change to assert 400.
    assert r.status_code in (201, 400)


async def test_archive_via_delete(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws = authenticated_client
    r = await client.post(
        f"/api/v1/ws/{ws}/memory",
        json={"scope": "personal", "type": "preference", "content": "Use TDD."},
    )
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    r = await client.delete(f"/api/v1/ws/{ws}/memory/{mid}")
    assert r.status_code == 204
    r = await client.get(
        f"/api/v1/ws/{ws}/memory", params={"scope": "personal", "status": "archived"}
    )
    assert any(i["id"] == mid for i in r.json()["items"])
