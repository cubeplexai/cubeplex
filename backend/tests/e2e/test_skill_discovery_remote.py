"""E2E tests for remote skill discover → preview → install via a faithful fake registry.

The fake registry is a real HTTP server (uvicorn) spun up by the ``fake_registry_url``
fixture in conftest.py. That means RemoteRegistryAdapter goes through its full httpx
code path, not a mock transport.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest


@pytest.mark.asyncio
async def test_remote_discover_preview_install_then_loadable(
    four_layer_admin_and_member: tuple[
        tuple[httpx.AsyncClient, str, str],
        tuple[httpx.AsyncClient, str, str],
    ],
    fake_registry_url: str,
    seed_remote_source: Callable[..., Awaitable[str]],
) -> None:
    (admin, _admin_ws, admin_uid), (member, ws_id, _member_uid) = four_layer_admin_and_member

    # Seed the fake registry as a remote source directly (the admin route's
    # SSRF guard rejects the loopback test-server host; see seed_remote_source).
    await seed_remote_source(
        workspace_id=ws_id,
        created_by_user_id=admin_uid,
        base_url=fake_registry_url,
        name="fake",
        trust_tier="community",
        repo="acme/skills",
    )

    # Member discovers skills — should find slide-deck from the remote source.
    disc = await member.get(f"/api/v1/ws/{ws_id}/skills/discover", params={"q": "slides"})
    assert disc.status_code == 200, disc.text
    cand = next(c for c in disc.json() if c["name"] == "slide-deck")
    assert cand["source_kind"] == "remote"
    assert cand["unvetted"] is True
    assert cand["canonical_name"].endswith(":slide-deck")

    # Member previews the candidate — should return SKILL.md content.
    preview = await member.get(
        f"/api/v1/ws/{ws_id}/skills/discover/preview",
        params={"candidate_id": cand["candidate_id"]},
    )
    assert preview.status_code == 200, preview.text
    assert "slide-deck" in preview.json()["content"]

    # Member installs the candidate.
    install = await member.post(
        f"/api/v1/ws/{ws_id}/skills/install",
        json={"candidate_id": cand["candidate_id"]},
    )
    assert install.status_code == 201, install.text
    canonical = install.json()["canonical_name"]
    assert canonical.endswith(":slide-deck")

    # The installed skill appears in the workspace-scoped skill list.
    enabled = await member.get(f"/api/v1/ws/{ws_id}/skills", params={"scope": "workspace"})
    assert any(s["name"] == canonical for s in enabled.json())


@pytest.mark.asyncio
async def test_disabled_source_returns_no_remote_candidates(
    four_layer_admin_and_member: tuple[
        tuple[httpx.AsyncClient, str, str],
        tuple[httpx.AsyncClient, str, str],
    ],
    fake_registry_url: str,
    seed_remote_source: Callable[..., Awaitable[str]],
) -> None:
    (admin, _admin_ws, admin_uid), (member, ws_id, _member_uid) = four_layer_admin_and_member

    # Seed the source directly (loopback host is SSRF-rejected by the admin
    # route), then disable it via the admin PATCH route under test.
    sid = await seed_remote_source(
        workspace_id=ws_id,
        created_by_user_id=admin_uid,
        base_url=fake_registry_url,
        name="fake",
        trust_tier="community",
    )
    patch = await admin.patch(f"/api/v1/admin/skill-registries/{sid}", json={"enabled": False})
    assert patch.status_code == 200, patch.text

    # Discovery must return zero remote candidates.
    disc = await member.get(f"/api/v1/ws/{ws_id}/skills/discover", params={"q": "slides"})
    assert disc.status_code == 200, disc.text
    assert not any(c["source_kind"] == "remote" for c in disc.json())
