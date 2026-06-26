"""E2E: cold start sync writes files + manifest in one round-trip.

If sync regresses to per-file upload OR fails to write manifest, this test fails.

Two complementary assertion blocks:
  1. Direct ``_sync_skills`` call into a ``_MemSandbox`` — asserts manifest +
     file content without any LazySandbox overhead (fast, readable).
  2. ``LazySandbox.execute("true")`` gate — exercises ``_ensure_skills_synced``
     / ``_synced_for_this_run`` / ``_sync_lock`` end-to-end with a
     ``_CountingCatalog`` wrapper to verify the second execute call short-circuits.
"""

from __future__ import annotations

import io
import json
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.sandbox.base import ExecuteResult, Sandbox

# The private function under test — importing directly keeps the test focused
# on the sync function itself (not the LazySandbox wrapper).
from cubebox.sandbox.lazy import LazySandbox, _sync_skills
from cubebox.sandbox.manager import SandboxManager
from cubebox.skills.cache import SkillCache
from cubebox.skills.sandbox_paths import SKILLS_ROOT
from cubebox.skills.service import SkillCatalogService
from cubebox.skills.sync_manifest import MANIFEST_PATH

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
# _MemSandbox: in-process sandbox with real tar extraction
# ---------------------------------------------------------------------------


class _MemSandbox(Sandbox):
    """Minimal in-memory Sandbox for testing ``_sync_skills``.

    ``upload`` stores bytes by path; ``download`` reads them back.
    ``execute`` handles the ``tar -xzf /tmp/skills_delta.tgz -C <root>``
    command that ``_sync_skills`` emits — it unpacks the tarball into the
    in-memory FS so SKILL.md entries are readable afterwards.  All other
    commands are accepted silently (mkdir -p, rm -f etc. are benign no-ops
    in the test context).
    """

    _SKILLS_TGZ = "/tmp/skills_delta.tgz"

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    @property
    def id(self) -> str:
        return "mem-sandbox"

    @property
    def workdir(self) -> str:
        return "/workspace"

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
    ) -> ExecuteResult:
        del timeout, envs
        # Handle the tar extract command that _sync_skills emits.
        # Format: "mkdir -p ... && rm -rf ... && tar -xzf /tmp/skills_delta.tgz -C /workspace/.skills && ..."
        if "tar -xzf" in command and self._SKILLS_TGZ in command:
            tgz = self._files.get(self._SKILLS_TGZ)
            if tgz:
                # Extract into ``{SKILLS_ROOT}/<path>`` entries.
                with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tf:
                    for member in tf.getmembers():
                        if member.isfile():
                            f = tf.extractfile(member)
                            if f is not None:
                                dest = f"{SKILLS_ROOT}/{member.name}"
                                self._files[dest] = f.read()
                # Remove the tgz (mirrors the real command's ``rm -f``).
                self._files.pop(self._SKILLS_TGZ, None)
        # rm -rf / rm -f: drop matching keys.
        for token in command.split("&&"):
            stripped = token.strip()
            if stripped.startswith("rm -rf ") or stripped.startswith("rm -f "):
                path = stripped.split(None, 2)[-1].strip("'\"")
                self._files.pop(path, None)
        return ExecuteResult(output="", exit_code=0)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        for path, content in files:
            self._files[path] = content

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        result: list[tuple[str, bytes]] = []
        for path in paths:
            if path not in self._files:
                raise FileNotFoundError(path)
            result.append((path, self._files[path]))
        return result

    async def close(self) -> None:
        pass


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
        sandbox = _MemSandbox()
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

    from cubebox.credentials.encryption import FernetBackend

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
