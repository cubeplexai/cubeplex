import io
import secrets
import tempfile
import zipfile
from pathlib import Path

import httpx
import pytest


def _zip_skill(name: str, version: str = "1.0.0") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "SKILL.md",
            f"---\nname: {name}\ndescription: d\nversion: {version}\n---\n# {name}\n",
        )
    return buf.getvalue()


async def _publish_uploaded(
    client: httpx.AsyncClient, ws_id: str, *, slug: str | None = None
) -> str:
    """Publish an org-uploaded skill (not auto-enabled). Returns skill name slug."""
    name = slug or f"disc-{secrets.token_hex(4)}"
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/skills/publish",
        files={"file": ("a.zip", _zip_skill(name), "application/zip")},
    )
    assert resp.status_code == 201, resp.text
    return name


@pytest.mark.asyncio
async def test_discover_then_install_local_skill_becomes_enabled(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Uploaded skills start as in_catalog; install enables them for the workspace.

    Preinstalled skills are auto-installed on seed reconcile (auto_bind=True), so
    they already appear as ``enabled``. The discover→install path is covered with
    an org-uploaded skill that is not auto-enabled.
    """
    client, ws_id = member_client
    slug = await _publish_uploaded(client, ws_id)

    disc = await client.get(f"/api/v1/ws/{ws_id}/skills/discover", params={"q": slug})
    assert disc.status_code == 200
    cands = disc.json()
    cand = next(c for c in cands if c["name"].endswith(f":{slug}") or c["name"] == slug)
    assert cand["install_state"] == "in_catalog"
    assert "candidate_id" in cand and "/" not in cand["candidate_id"]
    canonical = cand["canonical_name"]

    install = await client.post(
        f"/api/v1/ws/{ws_id}/skills/install",
        json={"candidate_id": cand["candidate_id"]},
    )
    assert install.status_code == 201
    body = install.json()
    assert body["canonical_name"] == canonical

    enabled = await client.get(f"/api/v1/ws/{ws_id}/skills", params={"scope": "workspace"})
    assert any(s["name"] == canonical for s in enabled.json())


@pytest.mark.asyncio
async def test_install_is_workspace_private_not_visible_in_other_ws(
    member_client_two_workspaces: tuple[httpx.AsyncClient, str, str],
) -> None:
    """Workspace-private install of an uploaded skill must not bleed to ws_b.

    Preinstalled skills are org-wide auto-bound, so they appear in every
    workspace; use an uploaded skill for private-install isolation.
    """
    client, ws_a, ws_b = member_client_two_workspaces
    slug = await _publish_uploaded(client, ws_a)

    disc = await client.get(f"/api/v1/ws/{ws_a}/skills/discover", params={"q": slug})
    cand = next(c for c in disc.json() if c["name"].endswith(f":{slug}") or c["name"] == slug)
    canonical = cand["canonical_name"]
    await client.post(
        f"/api/v1/ws/{ws_a}/skills/install", json={"candidate_id": cand["candidate_id"]}
    )
    a = await client.get(f"/api/v1/ws/{ws_a}/skills", params={"scope": "workspace"})
    b = await client.get(f"/api/v1/ws/{ws_b}/skills", params={"scope": "workspace"})
    assert any(s["name"] == canonical for s in a.json())
    assert not any(s["name"] == canonical for s in b.json())


@pytest.mark.asyncio
async def test_installed_skill_resolves_via_find_enabled_by_name(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Post-install, find_enabled_by_name (what load_skill calls) returns the skill.

    Guards against a regression where the run-loop's per-turn recompute of the
    enabled set could miss a fresh workspace-private install.
    """
    client, ws_id = member_client

    slug = await _publish_uploaded(client, ws_id)
    disc = await client.get(f"/api/v1/ws/{ws_id}/skills/discover", params={"q": slug})
    cand = next(c for c in disc.json() if c["name"].endswith(f":{slug}") or c["name"] == slug)
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

    from cubeplex.db.engine import _build_database_url
    from cubeplex.repositories.workspace import WorkspaceRepository
    from cubeplex.skills.cache import SkillCache
    from cubeplex.skills.service import SkillCatalogService

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


@pytest.mark.asyncio
async def test_preinstalled_skill_is_enabled_after_seed_reconcile(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """New preinstalled skills must be loadable without a manual install.

    Seed reconcile auto-installs missing preinstalled skills with auto_bind=True
    so agents can load_skill('show-widget') / deep-research after deploy.
    """
    client, ws_id = member_client
    disc = await client.get(f"/api/v1/ws/{ws_id}/skills/discover", params={"q": "research"})
    assert disc.status_code == 200
    cand = next(c for c in disc.json() if c["name"] == "deep-research")
    assert cand["install_state"] == "enabled"

    enabled = await client.get(f"/api/v1/ws/{ws_id}/skills", params={"scope": "workspace"})
    assert any(s["name"] == "deep-research" for s in enabled.json())


@pytest.mark.asyncio
async def test_tombstoned_preinstalled_cannot_be_reinstalled_via_discovery(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """An admin uninstall of a preinstalled skill writes an OrgPreinstalledTombstone.

    Discovery must hide that candidate AND the install endpoint must reject a
    stale candidate_id pointing at the tombstoned skill — otherwise a workspace
    member could undo the admin decision via the in-chat install path.
    """
    client, ws_id = member_client

    disc = await client.get(f"/api/v1/ws/{ws_id}/skills/discover", params={"q": "research"})
    cand = next(c for c in disc.json() if c["name"] == "deep-research")
    stale_candidate_id = cand["candidate_id"]
    me = await client.get("/api/v1/auth/me")
    member_user_id = me.json()["id"]

    # Add a tombstone for that skill in the member's org.
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from cubeplex.db.engine import _build_database_url
    from cubeplex.repositories.skill import (
        OrgPreinstalledTombstoneRepository,
        OrgSkillInstallRepository,
        SkillRepository,
    )
    from cubeplex.repositories.workspace import WorkspaceRepository
    from cubeplex.skills.sources.base import decode_candidate_id

    _, _, skill_id = decode_candidate_id(stale_candidate_id)

    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            ws = await WorkspaceRepository(session).get(ws_id)
            assert ws is not None
            skill = await SkillRepository(session).get(skill_id)
            assert skill is not None and skill.source == "preinstalled"
            # Mirror admin uninstall: drop install + tombstone.
            await OrgSkillInstallRepository(session).delete(ws.org_id, skill_id)
            await OrgPreinstalledTombstoneRepository(session).add_tombstone(
                org_id=ws.org_id, skill_id=skill_id, hidden_by_user_id=member_user_id
            )
    finally:
        await test_engine.dispose()

    # Discovery must no longer surface the tombstoned candidate.
    rediscovered = await client.get(f"/api/v1/ws/{ws_id}/skills/discover", params={"q": "research"})
    assert rediscovered.status_code == 200
    assert not any(c["name"] == "deep-research" for c in rediscovered.json())

    # And install with the stale candidate_id must be refused.
    install = await client.post(
        f"/api/v1/ws/{ws_id}/skills/install",
        json={"candidate_id": stale_candidate_id},
    )
    assert install.status_code == 400
    detail = install.json()["detail"]
    assert detail["code"] == "INSTALL_FAILED"
    assert "uninstalled" in detail["reason"].lower()
