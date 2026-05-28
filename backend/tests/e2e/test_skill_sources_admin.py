import httpx
import pytest


@pytest.mark.asyncio
async def test_admin_can_register_and_disable_remote_source(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    create = await client.post(
        "/api/v1/admin/skill-sources",
        json={
            "name": "skills.sh",
            "base_url": "https://www.skills.sh",
            "repo": "vercel-labs/skills",
            "trust_tier": "official",
        },
    )
    assert create.status_code == 201
    sid = create.json()["id"]

    listed = await client.get("/api/v1/admin/skill-sources")
    assert any(s["id"] == sid for s in listed.json())

    disabled = await client.patch(f"/api/v1/admin/skill-sources/{sid}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False


@pytest.mark.asyncio
async def test_member_cannot_reach_admin_source_routes(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = member_client
    resp = await client.get("/api/v1/admin/skill-sources")
    assert resp.status_code in (401, 403)
