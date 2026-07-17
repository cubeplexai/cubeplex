"""E2E: cold start sync writes files + manifest in one round-trip.

If sync regresses to per-file upload OR fails to write manifest, this test fails.

Two complementary assertion blocks:
  1. Direct ``_sync_skills`` call into a ``MemSandbox`` — asserts manifest +
     file content without any LazySandbox overhead (fast, readable).
  2. ``LazySandbox.execute("true")`` gate — exercises ``_ensure_skills_synced``
     / ``_synced_for_this_run`` / ``_sync_lock`` end-to-end with a
     ``_CountingCatalog`` wrapper to verify the second execute call short-circuits.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# The private function under test — importing directly keeps the test focused
# on the sync function itself (not the LazySandbox wrapper).
from cubeplex.sandbox.lazy import LazySandbox, _sync_skills
from cubeplex.sandbox.manager import SandboxManager
from cubeplex.skills.cache import SkillCache
from cubeplex.skills.sandbox_paths import SKILLS_ROOT
from cubeplex.skills.service import SkillCatalogService
from cubeplex.skills.sync_manifest import MANIFEST_PATH
from tests.e2e.conftest import MemSandbox

# ---------------------------------------------------------------------------
# _CountingCatalog: thin wrapper that counts list_enabled_for_workspace calls
# ---------------------------------------------------------------------------


class _CountingCatalog:
    """Delegates every call to the real SkillCatalogService and counts list calls."""

    def __init__(self, inner: SkillCatalogService) -> None:
        self._inner = inner
        self.list_enabled_calls: int = 0

    async def list_enabled_for_workspace(self, workspace_id: str, *, org_id: str) -> Any:
        self.list_enabled_calls += 1
        return await self._inner.list_enabled_for_workspace(workspace_id, org_id=org_id)

    # Forward every other attribute access to the real service.
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cold_start_writes_files_and_manifest(
    fresh_workspace_and_sandbox: SimpleNamespace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Cold-start: empty sandbox + enabled skill → sync pushes tar + writes manifest.

    Invariant: "if _sync_skills regresses to per-file upload OR fails to write
    the manifest, this test fails."
    """
    # 1. Install a skill so list_enabled_for_workspace returns something.
    from tests.e2e.conftest import install_skill_for_workspace, uninstall_skill_for_workspace

    ns = fresh_workspace_and_sandbox
    async with session_factory() as session:
        skill_id = await install_skill_for_workspace(
            session,
            org_id=ns.org_id,
            org_slug=ns.org_slug,
            workspace_id=ns.workspace_id,
            user_id=ns.user_id,
            slug="probe-cold",
        )

    # 2. Build the catalog service for this workspace.
    cache_dir = Path(tempfile.mkdtemp())
    async with session_factory() as catalog_session:
        catalog = SkillCatalogService(
            session=catalog_session, cache=SkillCache(cache_root=cache_dir)
        )

        # 3. Run the first sync into a fresh in-memory sandbox (cold start).
        sandbox = MemSandbox()
        await _sync_skills(
            catalog=catalog,
            workspace_id=ns.workspace_id,
            org_id=ns.org_id,
            sandbox=sandbox,
        )

        # 4. Manifest must exist and be well-formed.
        [(_, raw)] = await sandbox.download([MANIFEST_PATH])
        manifest: dict[str, Any] = json.loads(raw)
        assert manifest.get("schema_version") == 1, f"unexpected manifest: {manifest}"
        skills: dict[str, Any] = manifest.get("skills", {})
        assert skills, "manifest.skills is empty after cold-start sync"

        # 5. At least one skill's SKILL.md must be present in the sandbox FS.
        assert len(sandbox._files) > 0, "sandbox has no files after _sync_skills — tar no-op?"
        sample_name = next(iter(skills))
        sample_version: str = skills[sample_name]["version"]
        skill_md_path = f"{SKILLS_ROOT}/{sample_name}/{sample_version}/SKILL.md"
        [(_, skill_md_bytes)] = await sandbox.download([skill_md_path])
        # SKILL.md starts with YAML front-matter (---) or contains the name field.
        assert skill_md_bytes.startswith(b"---") or b"name:" in skill_md_bytes, (
            f"SKILL.md does not look like a valid skill file: {skill_md_bytes[:80]!r}"
        )

    # -----------------------------------------------------------------------
    # Block 2: Drive sync through the LazySandbox gate.
    #
    # Exercises _ensure_skills_synced / _synced_for_this_run / _sync_lock —
    # code paths that the direct _sync_skills call above bypasses.
    # -----------------------------------------------------------------------
    from cryptography.fernet import Fernet

    from cubeplex.credentials.encryption import FernetBackend

    cache_dir2 = Path(tempfile.mkdtemp())
    enc_backend = FernetBackend([Fernet.generate_key()])
    mgr = SandboxManager(session_factory, enc_backend)

    async with session_factory() as gate_session:
        inner_catalog = SkillCatalogService(
            session=gate_session, cache=SkillCache(cache_root=cache_dir2)
        )
        counting_catalog = _CountingCatalog(inner_catalog)

        lazy2 = LazySandbox(
            manager=mgr,
            scope_type="user",
            scope_id=ns.user_id,
            user_id=ns.user_id,
            org_id=ns.org_id,
            workspace_id=ns.workspace_id,
            catalog=counting_catalog,  # type: ignore[arg-type]
        )

        # First execute: _ensure_skills_synced runs → list_enabled called once.
        await lazy2.execute("true")
        assert lazy2._synced_for_this_run is True, (
            "_synced_for_this_run should be True after first execute"
        )
        assert counting_catalog.list_enabled_calls == 1, (
            f"expected 1 list_enabled call after first execute, got {counting_catalog.list_enabled_calls}"
        )

        # Second execute: short-circuit — _synced_for_this_run already True.
        await lazy2.execute("true")
        assert lazy2._synced_for_this_run is True
        assert counting_catalog.list_enabled_calls == 1, (
            "second execute must NOT trigger another sync — "
            f"list_enabled_calls={counting_catalog.list_enabled_calls}"
        )

        await lazy2.close()

    # Cleanup: remove the test skill row.
    async with session_factory() as cleanup_session:
        await uninstall_skill_for_workspace(
            cleanup_session,
            workspace_id=ns.workspace_id,
            org_id=ns.org_id,
            skill_id=skill_id,
        )
