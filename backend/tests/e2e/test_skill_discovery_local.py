import httpx
import pytest


@pytest.mark.asyncio
async def test_discover_then_install_local_skill_becomes_enabled(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = member_client

    disc = await client.get(f"/api/v1/ws/{ws_id}/skills/discover", params={"q": "research"})
    assert disc.status_code == 200
    cands = disc.json()
    cand = next(c for c in cands if c["name"] == "deep-research")
    assert cand["install_state"] == "in_catalog"
    assert cand["canonical_name"] == "deep-research"
    assert "candidate_id" in cand and "/" not in cand["candidate_id"]

    install = await client.post(
        f"/api/v1/ws/{ws_id}/skills/install",
        json={"candidate_id": cand["candidate_id"]},
    )
    assert install.status_code == 201
    body = install.json()
    assert body["canonical_name"] == "deep-research"

    enabled = await client.get(f"/api/v1/ws/{ws_id}/skills", params={"scope": "workspace"})
    assert any(s["name"] == "deep-research" for s in enabled.json())


@pytest.mark.asyncio
async def test_install_is_workspace_private_not_visible_in_other_ws(
    member_client_two_workspaces: tuple[httpx.AsyncClient, str, str],
) -> None:
    client, ws_a, ws_b = member_client_two_workspaces
    disc = await client.get(f"/api/v1/ws/{ws_a}/skills/discover", params={"q": "research"})
    cand = next(c for c in disc.json() if c["name"] == "deep-research")
    await client.post(
        f"/api/v1/ws/{ws_a}/skills/install", json={"candidate_id": cand["candidate_id"]}
    )
    a = await client.get(f"/api/v1/ws/{ws_a}/skills", params={"scope": "workspace"})
    b = await client.get(f"/api/v1/ws/{ws_b}/skills", params={"scope": "workspace"})
    assert any(s["name"] == "deep-research" for s in a.json())
    assert not any(s["name"] == "deep-research" for s in b.json())
