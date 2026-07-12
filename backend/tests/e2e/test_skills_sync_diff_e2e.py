"""E2E: enabled set changes → sync only pushes the delta + cleans removed.

Invariant protected: if the diff layer regresses (pushes too much, or fails to
remove stale dirs), this test fails.

Flow:
  1. Fresh workspace with no skills → manifest empty (or missing).
  2. Install ``probe-1`` → first sync → manifest contains the skill key and
     SKILL.md is readable in the sandbox FS.
  3. Uninstall ``probe-1`` → second sync → manifest no longer contains the
     skill key and the sandbox dir is gone (FileNotFoundError on download).

The manifest key is ``safe_skill_name(org_slug + ':' + slug)``
(e.g. ``sync-e2e-abc123__probe-1``), not the bare slug. Tests look for the key
by combining ``ns.org_slug + '__probe-1'`` to stay robust across name formats.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.sandbox.lazy import _sync_skills
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.sandbox_paths import SKILLS_ROOT
from cubeplex.skills.service import SkillCatalogService
from cubeplex.skills.sync_manifest import MANIFEST_PATH
from tests.e2e.conftest import MemSandbox


@pytest.mark.asyncio
async def test_install_uninstall_triggers_incremental_sync(
    fresh_workspace_and_sandbox: SimpleNamespace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Install then uninstall a skill; each sync only moves the diff.

    Step-by-step invariants:
    - After install + sync: manifest has the skill key, SKILL.md present.
    - After uninstall + sync: manifest has no skill key, SKILL.md gone.
    """
    from tests.e2e.conftest import install_skill_for_workspace, uninstall_skill_for_workspace

    ns = fresh_workspace_and_sandbox
    # Canonical manifest key: safe_skill_name("<org_slug>:probe-1")
    # safe_skill_name replaces ':' with '__'.
    manifest_key = f"{ns.org_slug}__probe-1"
    cache_dir = Path(tempfile.mkdtemp())

    # ------------------------------------------------------------------
    # Phase 1: cold start — no skills, manifest should be empty / absent.
    # ------------------------------------------------------------------
    sandbox = MemSandbox()

    async with session_factory() as catalog_session:
        catalog = SkillCatalogService(
            session=catalog_session, cache=SkillCache(cache_root=cache_dir)
        )
        await _sync_skills(
            catalog=catalog,
            workspace_id=ns.workspace_id,
            org_id=ns.org_id,
            sandbox=sandbox,
        )

    # Empty enabled set → _sync_skills returns early (diff.is_empty()).
    # Manifest may not exist yet; if it does, skills must be empty.
    try:
        [(_, raw_before)] = await sandbox.download([MANIFEST_PATH])
        manifest_before: dict[str, Any] = json.loads(raw_before)
        assert not manifest_before.get("skills"), (
            f"Expected empty manifest before any installs, got: {manifest_before}"
        )
    except FileNotFoundError:
        pass  # no manifest yet is also acceptable

    # ------------------------------------------------------------------
    # Phase 2: install probe-1 → sync → files + manifest appear.
    # ------------------------------------------------------------------
    skill_id: str | None = None
    skill_md_path: str | None = None
    try:
        async with session_factory() as install_session:
            skill_id = await install_skill_for_workspace(
                install_session,
                org_id=ns.org_id,
                org_slug=ns.org_slug,
                workspace_id=ns.workspace_id,
                user_id=ns.user_id,
                slug="probe-1",
            )

        async with session_factory() as catalog_session2:
            catalog2 = SkillCatalogService(
                session=catalog_session2, cache=SkillCache(cache_root=cache_dir)
            )
            await _sync_skills(
                catalog=catalog2,
                workspace_id=ns.workspace_id,
                org_id=ns.org_id,
                sandbox=sandbox,
            )

        # Manifest must now list the skill under its canonical key.
        [(_, manifest_bytes)] = await sandbox.download([MANIFEST_PATH])
        manifest_after_install: dict[str, Any] = json.loads(manifest_bytes)
        skills_after_install: dict[str, Any] = manifest_after_install.get("skills", {})
        assert manifest_key in skills_after_install, (
            f"Expected key '{manifest_key}' in manifest after install, "
            f"got keys: {list(skills_after_install)}"
        )
        probe_version: str = skills_after_install[manifest_key]["version"]

        # SKILL.md must be readable in the sandbox FS.
        # The sandbox dir uses safe_skill_name which maps ':' to '__' too, but
        # the name stored in the manifest is already the safe name.
        skill_md_path = f"{SKILLS_ROOT}/{manifest_key}/{probe_version}/SKILL.md"
        [(_, skill_md_bytes)] = await sandbox.download([skill_md_path])
        assert skill_md_bytes, "SKILL.md is empty after install+sync"

        # ------------------------------------------------------------------
        # Phase 3: uninstall probe-1 → sync → manifest and files gone.
        # ------------------------------------------------------------------
        async with session_factory() as uninstall_session:
            await uninstall_skill_for_workspace(
                uninstall_session,
                workspace_id=ns.workspace_id,
                org_id=ns.org_id,
                skill_id=skill_id,
            )
        skill_id = None  # mark as cleaned up

        async with session_factory() as catalog_session3:
            catalog3 = SkillCatalogService(
                session=catalog_session3, cache=SkillCache(cache_root=cache_dir)
            )
            await _sync_skills(
                catalog=catalog3,
                workspace_id=ns.workspace_id,
                org_id=ns.org_id,
                sandbox=sandbox,
            )

        # Manifest must no longer contain the skill key.
        [(_, manifest_bytes2)] = await sandbox.download([MANIFEST_PATH])
        manifest_after_uninstall: dict[str, Any] = json.loads(manifest_bytes2)
        skills_after_uninstall: dict[str, Any] = manifest_after_uninstall.get("skills", {})
        assert manifest_key not in skills_after_uninstall, (
            f"Expected '{manifest_key}' gone from manifest after uninstall, "
            f"got: {manifest_after_uninstall}"
        )

        # The sandbox dir for probe-1 must be gone.
        if skill_md_path is not None:
            with pytest.raises(FileNotFoundError):
                await sandbox.download([skill_md_path])

    finally:
        # Ensure cleanup even if assertions above failed — the fixture teardown
        # will FK-error if OrgSkillInstall rows still reference the workspace.
        if skill_id is not None:
            async with session_factory() as cleanup_session:
                await uninstall_skill_for_workspace(
                    cleanup_session,
                    workspace_id=ns.workspace_id,
                    org_id=ns.org_id,
                    skill_id=skill_id,
                )
