"""E2E: catalog-only remote skill refresh (install pointers stay put)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest

from tests.e2e.conftest import _FAKE_SKILL_MD


async def _install_remote_private(
    member: httpx.AsyncClient,
    ws_id: str,
    *,
    admin: httpx.AsyncClient,
    admin_uid: str,
    fake_registry_url: str,
    seed_remote_source: Callable[..., Awaitable[str]],
) -> dict[str, str]:
    await seed_remote_source(
        workspace_id=ws_id,
        created_by_user_id=admin_uid,
        base_url=fake_registry_url,
        name="fake",
        trust_tier="community",
        repo="acme/skills",
    )
    disc = await member.get(f"/api/v1/ws/{ws_id}/skills/discover", params={"q": "slides"})
    assert disc.status_code == 200, disc.text
    cand = next(c for c in disc.json() if c["name"] == "slide-deck")
    install = await member.post(
        f"/api/v1/ws/{ws_id}/skills/install",
        json={"candidate_id": cand["candidate_id"]},
    )
    assert install.status_code == 201, install.text
    return install.json()


@pytest.mark.asyncio
async def test_ws_refresh_same_content_no_change_install_unchanged(
    four_layer_admin_and_member: tuple[
        tuple[httpx.AsyncClient, str, str],
        tuple[httpx.AsyncClient, str, str],
    ],
    fake_registry_url: str,
    seed_remote_source: Callable[..., Awaitable[str]],
) -> None:
    (admin, _admin_ws, admin_uid), (member, ws_id, _member_uid) = four_layer_admin_and_member
    installed = await _install_remote_private(
        member,
        ws_id,
        admin=admin,
        admin_uid=admin_uid,
        fake_registry_url=fake_registry_url,
        seed_remote_source=seed_remote_source,
    )
    skill_id = installed["skill_id"]
    pinned = installed["installed_version"]

    r1 = await member.post(f"/api/v1/ws/{ws_id}/skills/{skill_id}/refresh")
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["changed"] is False
    assert body["current_version"] == pinned
    assert body["previous_version"] == pinned
    assert body["assigned_version"] is None

    # Private install pin unchanged
    settings = await member.get(f"/api/v1/ws/{ws_id}/settings/skills")
    assert settings.status_code == 200, settings.text
    priv = next(s for s in settings.json()["workspace_skills"] if s["skill_id"] == skill_id)
    assert priv["installed_version"] == pinned


@pytest.mark.asyncio
async def test_ws_refresh_new_content_advances_catalog_not_install(
    four_layer_admin_and_member: tuple[
        tuple[httpx.AsyncClient, str, str],
        tuple[httpx.AsyncClient, str, str],
    ],
    fake_registry_url: str,
    seed_remote_source: Callable[..., Awaitable[str]],
) -> None:
    (admin, _admin_ws, admin_uid), (member, ws_id, _member_uid) = four_layer_admin_and_member
    installed = await _install_remote_private(
        member,
        ws_id,
        admin=admin,
        admin_uid=admin_uid,
        fake_registry_url=fake_registry_url,
        seed_remote_source=seed_remote_source,
    )
    skill_id = installed["skill_id"]
    pinned = installed["installed_version"]

    # Upstream content changes (same frontmatter version → auto patch).
    _FAKE_SKILL_MD["content"] = (
        "---\nname: slide-deck\ndescription: Build better slide decks\nversion: 1.0.0\n"
        "---\n# Slide deck v2 content\n"
    )

    r1 = await member.post(f"/api/v1/ws/{ws_id}/skills/{skill_id}/refresh")
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["changed"] is True
    assert body["previous_version"] == pinned
    assert body["current_version"] != pinned
    assert body["assigned_version"] == body["current_version"]

    settings = await member.get(f"/api/v1/ws/{ws_id}/settings/skills")
    priv = next(s for s in settings.json()["workspace_skills"] if s["skill_id"] == skill_id)
    assert priv["installed_version"] == pinned

    detail = await admin.get(f"/api/v1/admin/skills/{skill_id}")
    assert detail.status_code == 200, detail.text
    d = detail.json()
    assert d["current_version"] == body["current_version"]
    assert d["imported_from_registry_id"] is not None


@pytest.mark.asyncio
async def test_ws_refresh_identity_mismatch(
    four_layer_admin_and_member: tuple[
        tuple[httpx.AsyncClient, str, str],
        tuple[httpx.AsyncClient, str, str],
    ],
    fake_registry_url: str,
    seed_remote_source: Callable[..., Awaitable[str]],
) -> None:
    (admin, _admin_ws, admin_uid), (member, ws_id, _member_uid) = four_layer_admin_and_member
    installed = await _install_remote_private(
        member,
        ws_id,
        admin=admin,
        admin_uid=admin_uid,
        fake_registry_url=fake_registry_url,
        seed_remote_source=seed_remote_source,
    )
    skill_id = installed["skill_id"]
    _FAKE_SKILL_MD["content"] = (
        "---\nname: other-skill\ndescription: renamed\nversion: 2.0.0\n---\n# Other\n"
    )
    r1 = await member.post(f"/api/v1/ws/{ws_id}/skills/{skill_id}/refresh")
    assert r1.status_code == 422, r1.text
    assert r1.json()["detail"] == "SKILL_IDENTITY_MISMATCH"


@pytest.mark.asyncio
async def test_member_cannot_refresh_org_wide_only(
    four_layer_admin_and_member: tuple[
        tuple[httpx.AsyncClient, str, str],
        tuple[httpx.AsyncClient, str, str],
    ],
    fake_registry_url: str,
    seed_remote_source: Callable[..., Awaitable[str]],
) -> None:
    (admin, _admin_ws, admin_uid), (member, ws_id, _member_uid) = four_layer_admin_and_member
    await seed_remote_source(
        workspace_id=ws_id,
        created_by_user_id=admin_uid,
        base_url=fake_registry_url,
        name="fake",
        trust_tier="community",
        repo="acme/skills",
    )
    disc = await admin.get("/api/v1/admin/skills/discover", params={"q": "slides"})
    assert disc.status_code == 200, disc.text
    cand = next(c for c in disc.json() if c["name"] == "slide-deck")
    inst = await admin.post(
        "/api/v1/admin/skills/install-candidate",
        json={"candidate_id": cand["candidate_id"]},
    )
    assert inst.status_code in (200, 201), inst.text
    skill_id = inst.json()["skill_id"]

    refused = await member.post(f"/api/v1/ws/{ws_id}/skills/{skill_id}/refresh")
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == "REFRESH_PRIVATE_ONLY"

    ok = await admin.post(f"/api/v1/admin/skills/{skill_id}/refresh")
    assert ok.status_code == 200, ok.text
    assert ok.json()["changed"] is False
    assert ok.json()["skill_id"] == skill_id
