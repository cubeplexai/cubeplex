"""E2E: pause + resume keeps manifest; second sync after resume is no-op.

Invariant protected:
  If pause/resume regresses to a full re-push (e.g. someone re-introduces an
  in-memory sync cache that doesn't survive sandbox handle replacement), this
  test fails. The new design reads manifest.json from the PVC and short-circuits
  when its contents match the desired state — so a resumed sandbox using the
  SAME storage should upload exactly 0 files.

Approach (option b — MemSandbox with shared _files dict):
  We simulate "PVC persists, sandbox object is fresh" by creating a second
  MemSandbox whose ``_files`` dict is the SAME object reference as the first.
  This is exactly what a pause/resume does: the provider allocates a new handle
  attached to the original volume. The ``_sync_skills`` function under test reads
  manifest.json from ``_files``; it cannot tell the difference between a resumed
  sandbox and an in-process replacement, which is precisely the invariant we are
  protecting.

  We do NOT use ``fake_opensandbox`` (option a) because ``_FakeRaw.connect``
  allocates a fresh ``_FakeFiles`` store — it does not share state with the
  pre-pause sandbox, so it would always look cold and the test would prove
  nothing about the manifest-hit path.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.sandbox.lazy import _sync_skills
from cubebox.skills.cache import SkillCache
from cubebox.skills.service import SkillCatalogService
from cubebox.skills.sync_manifest import MANIFEST_PATH
from tests.e2e.conftest import MemSandbox


@pytest.mark.asyncio
async def test_pause_resume_no_push(
    fresh_workspace_and_sandbox: SimpleNamespace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Resumed sandbox reuses PVC manifest → second sync uploads nothing.

    Steps:
      1. Install a skill so list_enabled_for_workspace returns a non-empty result.
      2. Cold-start sync on sandbox-1: writes files + manifest into _files.
      3. Capture the manifest bytes written.
      4. Create sandbox-2 sharing the SAME _files dict (simulates resumed PVC).
      5. Spy on sandbox-2.upload.
      6. Run _sync_skills on sandbox-2 — must read the existing manifest and
         return early (0 uploads).
      7. Assert spy saw 0 upload calls.
      8. Assert manifest bytes are unchanged.
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
            slug="probe-pause-resume",
        )

    cache_dir = Path(tempfile.mkdtemp())
    try:
        async with session_factory() as catalog_session:
            catalog = SkillCatalogService(
                session=catalog_session, cache=SkillCache(cache_root=cache_dir)
            )

            # 2. Cold-start sync on sandbox-1.
            sandbox1 = MemSandbox()
            await _sync_skills(
                catalog=catalog,
                workspace_id=ns.workspace_id,
                org_id=ns.org_id,
                sandbox=sandbox1,
            )

            # 3. Capture manifest bytes after cold sync.
            [(_, manifest_bytes_before)] = await sandbox1.download([MANIFEST_PATH])
            manifest_before: dict[str, Any] = json.loads(manifest_bytes_before)
            assert manifest_before.get("schema_version") == 1, (
                f"unexpected manifest after cold sync: {manifest_before}"
            )
            assert manifest_before.get("skills"), "manifest.skills empty after cold sync"

            # 4. Simulate pause/resume: sandbox-2 shares the SAME _files dict.
            #    The sandbox *handle* is new (as after a real provider resume),
            #    but the storage is identical (as a PVC would be).
            sandbox2 = MemSandbox()
            sandbox2._files = sandbox1._files  # shared PVC state

            # 5. Spy on sandbox-2.upload.
            upload_calls: list[list[tuple[str, bytes]]] = []
            original_upload = sandbox2.upload

            async def _spy_upload(files: list[tuple[str, bytes]]) -> None:
                upload_calls.append(list(files))
                await original_upload(files)

            sandbox2.upload = _spy_upload  # type: ignore[method-assign]

            # 6. Second sync on sandbox-2 — must hit the hot path (manifest match).
            await _sync_skills(
                catalog=catalog,
                workspace_id=ns.workspace_id,
                org_id=ns.org_id,
                sandbox=sandbox2,
            )

            # 7. No uploads.
            assert upload_calls == [], (
                f"resume should be no-push: expected 0 upload calls, "
                f"got {len(upload_calls)}: {upload_calls}"
            )

            # 8. Manifest bytes are unchanged (hot path must not rewrite manifest).
            [(_, manifest_bytes_after)] = await sandbox2.download([MANIFEST_PATH])
            assert manifest_bytes_after == manifest_bytes_before, (
                "manifest changed after resume sync — hot path must not rewrite manifest"
            )

    finally:
        # Cleanup: remove the test skill row.
        async with session_factory() as cleanup_session:
            await uninstall_skill_for_workspace(
                cleanup_session,
                workspace_id=ns.workspace_id,
                org_id=ns.org_id,
                skill_id=skill_id,
            )
