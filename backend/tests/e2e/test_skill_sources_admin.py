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
async def test_patch_with_invalid_trust_tier_does_not_flip_enabled(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Invalid trust_tier must reject the PATCH before any field is mutated."""
    client, _ = admin_client
    create = await client.post(
        "/api/v1/admin/skill-sources",
        json={
            "name": "atomic-source",
            "base_url": "https://atomic.test",
            "repo": "atomic/repo",
            "trust_tier": "official",
        },
    )
    assert create.status_code == 201
    sid = create.json()["id"]
    initial_enabled = create.json()["enabled"]

    bad = await client.patch(
        f"/api/v1/admin/skill-sources/{sid}",
        json={"enabled": not initial_enabled, "trust_tier": "bogus-tier"},
    )
    assert bad.status_code == 400
    assert bad.json()["detail"] == "BAD_TRUST_TIER"

    after = await client.get("/api/v1/admin/skill-sources")
    row = next(s for s in after.json() if s["id"] == sid)
    assert row["enabled"] == initial_enabled
    assert row["trust_tier"] == "official"


@pytest.mark.asyncio
async def test_member_cannot_reach_admin_source_routes(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = member_client
    resp = await client.get("/api/v1/admin/skill-sources")
    assert resp.status_code in (401, 403)
