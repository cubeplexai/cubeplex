"""E2E: sandbox with an up-to-date manifest → second sync uploads nothing.

Invariant protected:
  If manifest hit detection regresses and triggers uploads on unchanged content,
  this test fails — a content_hash / version mismatch would trigger a needless
  tarball push that this spy catches.
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
from cubeplex.skills.service import SkillCatalogService
from cubeplex.skills.sync_manifest import MANIFEST_PATH
from tests.e2e.conftest import MemSandbox

# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warm_sandbox_no_upload(
    fresh_workspace_and_sandbox: SimpleNamespace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Second sync into a sandbox that already has the correct manifest → 0 uploads.

    Steps:
      1. Install a skill so list_enabled_for_workspace returns a non-empty result.
      2. First sync (cold): populates the in-memory sandbox FS + writes manifest.
      3. Snapshot manifest bytes from the sandbox.
      4. Wrap sandbox.upload with a spy.
      5. Second sync (hot): diff detects manifest matches desired → early return.
      6. Assert spy saw 0 upload calls.
      7. Assert manifest bytes are unchanged.
    """
    from tests.e2e.conftest import install_skill_for_workspace, uninstall_skill_for_workspace

    ns = fresh_workspace_and_sandbox

    # 1. Install a skill so the catalog returns something for this workspace.
    async with session_factory() as session:
        skill_id = await install_skill_for_workspace(
            session,
            org_id=ns.org_id,
            org_slug=ns.org_slug,
            workspace_id=ns.workspace_id,
            user_id=ns.user_id,
            slug="probe-manifest-hit",
        )

    # 2. Build the catalog service and run the first (cold) sync.
    cache_dir = Path(tempfile.mkdtemp())
    async with session_factory() as catalog_session:
        catalog = SkillCatalogService(
            session=catalog_session, cache=SkillCache(cache_root=cache_dir)
        )

        sandbox = MemSandbox()

        # Cold sync — populates the sandbox FS and writes the manifest.
        await _sync_skills(
            catalog=catalog,
            workspace_id=ns.workspace_id,
            org_id=ns.org_id,
            sandbox=sandbox,
        )

        # 3. Snapshot the manifest written by the cold sync.
        [(_, manifest_bytes_before)] = await sandbox.download([MANIFEST_PATH])
        manifest_before: dict[str, Any] = json.loads(manifest_bytes_before)
        assert manifest_before.get("schema_version") == 1, (
            f"unexpected manifest after cold sync: {manifest_before}"
        )
        assert manifest_before.get("skills"), "manifest.skills empty after cold sync"

        # 4. Spy: wrap sandbox.upload to record calls, but still perform uploads.
        upload_calls: list[list[tuple[str, bytes]]] = []
        original_upload = sandbox.upload

        async def _spy_upload(files: list[tuple[str, bytes]]) -> None:
            upload_calls.append(list(files))
            await original_upload(files)

        sandbox.upload = _spy_upload  # type: ignore[method-assign]

        # 5. Second sync — must hit the hot path and never call sandbox.upload.
        await _sync_skills(
            catalog=catalog,
            workspace_id=ns.workspace_id,
            org_id=ns.org_id,
            sandbox=sandbox,
        )

        # 6. No uploads should have happened.
        assert upload_calls == [], (
            f"expected 0 upload calls on manifest hit, got {len(upload_calls)}: {upload_calls}"
        )

        # 7. Manifest must be byte-identical (no re-write on hot path).
        [(_, manifest_bytes_after)] = await sandbox.download([MANIFEST_PATH])
        assert manifest_bytes_after == manifest_bytes_before, (
            "manifest changed on hot path — sync must not re-write manifest on no-op diff"
        )

    # Cleanup.
    async with session_factory() as cleanup_session:
        await uninstall_skill_for_workspace(
            cleanup_session,
            workspace_id=ns.workspace_id,
            org_id=ns.org_id,
            skill_id=skill_id,
        )
