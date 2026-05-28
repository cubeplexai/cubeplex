import tempfile
from pathlib import Path

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


@pytest.mark.asyncio
async def test_installed_skill_resolves_via_find_enabled_by_name(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Post-install, find_enabled_by_name (what load_skill calls) returns the skill.

    Guards against a regression where the run-loop's per-turn recompute of the
    enabled set could miss a fresh workspace-private install.
    """
    client, ws_id = member_client

    disc = await client.get(f"/api/v1/ws/{ws_id}/skills/discover", params={"q": "research"})
    cand = next(c for c in disc.json() if c["name"] == "deep-research")
    inst = await client.post(
        f"/api/v1/ws/{ws_id}/skills/install",
        json={"candidate_id": cand["candidate_id"]},
    )
    assert inst.status_code == 201
    canonical = inst.json()["canonical_name"]

    # Open a fresh session to call find_enabled_by_name directly — the same call
    # load_skill makes on each agent turn. Verifies the DB row is visible without
    # any per-conversation cache invalidation.
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from cubebox.db.engine import _build_database_url
    from cubebox.repositories.workspace import WorkspaceRepository
    from cubebox.skills.cache import SkillCache
    from cubebox.skills.service import SkillCatalogService

    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            ws = await WorkspaceRepository(session).get(ws_id)
            assert ws is not None
            with tempfile.TemporaryDirectory() as tmp:
                catalog = SkillCatalogService(
                    session=session,
                    cache=SkillCache(cache_root=Path(tmp)),
                )
                resolved = await catalog.find_enabled_by_name(
                    ws_id, org_id=ws.org_id, name=canonical
                )
            assert resolved is not None
            assert resolved.name == canonical
    finally:
        await test_engine.dispose()
