import httpx
import pytest


@pytest.mark.asyncio
async def test_admin_can_register_and_disable_remote_registry(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    create = await client.post(
        "/api/v1/admin/skill-registries",
        json={
            "name": "skills.sh",
            "base_url": "https://www.skills.sh",
            "repo": "vercel-labs/skills",
            "trust_tier": "official",
        },
    )
    assert create.status_code == 201
    sid = create.json()["id"]

    listed = await client.get("/api/v1/admin/skill-registries")
    assert any(s["id"] == sid for s in listed.json())

    disabled = await client.patch(f"/api/v1/admin/skill-registries/{sid}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False


@pytest.mark.parametrize(
    "bad_url",
    [
        "ftp://reg.example.com",  # non-http scheme
        "http://localhost/skills",  # loopback hostname
        "http://127.0.0.1:8080",  # loopback IP
        "http://169.254.169.254/latest/meta-data",  # link-local (AWS IMDS)
        "http://10.0.0.5/skills",  # RFC1918 private
        "http://meta.local/skills",  # .local suffix
        "http://service.internal/x",  # .internal suffix
        "http://metadata/skills",  # bare metadata host
        "http://[::1]/skills",  # IPv6 loopback
        "http://2130706433/skills",  # decimal-int IPv4 → 127.0.0.1
        "http://0x7f000001/skills",  # hex IPv4 → 127.0.0.1
        "http://127.1/skills",  # short-dot IPv4 → 127.0.0.1
        "http://127.0.0.1\x00/skills",  # embedded NUL → inet_aton ValueError
        "not a url",  # unparseable
    ],
)
@pytest.mark.asyncio
async def test_create_rejects_ssrf_base_urls(
    admin_client: tuple[httpx.AsyncClient, str],
    bad_url: str,
) -> None:
    client, _ = admin_client
    resp = await client.post(
        "/api/v1/admin/skill-registries",
        json={
            "name": "ssrf-test",
            "base_url": bad_url,
            "trust_tier": "untrusted",
        },
    )
    assert resp.status_code == 400, f"expected 400 for {bad_url!r}, got {resp.status_code}"
    assert resp.json()["detail"] == "BAD_BASE_URL"


@pytest.mark.asyncio
async def test_patch_with_invalid_trust_tier_does_not_flip_enabled(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Invalid trust_tier must reject the PATCH before any field is mutated."""
    client, _ = admin_client
    create = await client.post(
        "/api/v1/admin/skill-registries",
        json={
            "name": "atomic-registry",
            "base_url": "https://atomic.test",
            "repo": "atomic/repo",
            "trust_tier": "official",
        },
    )
    assert create.status_code == 201
    sid = create.json()["id"]
    initial_enabled = create.json()["enabled"]

    bad = await client.patch(
        f"/api/v1/admin/skill-registries/{sid}",
        json={"enabled": not initial_enabled, "trust_tier": "bogus-tier"},
    )
    assert bad.status_code == 400
    assert bad.json()["detail"] == "BAD_TRUST_TIER"

    after = await client.get("/api/v1/admin/skill-registries")
    row = next(s for s in after.json() if s["id"] == sid)
    assert row["enabled"] == initial_enabled
    assert row["trust_tier"] == "official"


@pytest.mark.asyncio
async def test_admin_can_delete_registry(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    create = await client.post(
        "/api/v1/admin/skill-registries",
        json={
            "name": "to-delete",
            "base_url": "https://delete-me.example.com",
            "trust_tier": "untrusted",
        },
    )
    assert create.status_code == 201
    sid = create.json()["id"]

    deleted = await client.delete(f"/api/v1/admin/skill-registries/{sid}")
    assert deleted.status_code == 204

    listed = await client.get("/api/v1/admin/skill-registries")
    assert not any(s["id"] == sid for s in listed.json())


@pytest.mark.asyncio
async def test_delete_nonexistent_registry_returns_404(
    admin_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = admin_client
    resp = await client.delete("/api/v1/admin/skill-registries/reg_does_not_exist")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "REGISTRY_NOT_FOUND"


@pytest.mark.asyncio
async def test_member_cannot_reach_admin_registry_routes(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, _ = member_client
    resp = await client.get("/api/v1/admin/skill-registries")
    assert resp.status_code in (401, 403)
