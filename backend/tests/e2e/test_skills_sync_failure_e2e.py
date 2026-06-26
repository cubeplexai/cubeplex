"""E2E: sync failure must not block execute; manifest must not be partially written.

If failure handling regresses — sync exception propagates and breaks execute,
manifest is partially written after a failed extract, or a second sync does not
self-heal — this test fails.

Two complementary blocks:
  1. Direct ``_sync_skills`` call: monkeypatches ``MemSandbox.execute`` to raise
     on the tar-extract command.  Verifies the exception bubbles from
     ``_sync_skills`` itself and that the manifest is NOT written (no half-state).
     Subsequent successful sync heals the state.
  2. LazySandbox gate: drives the same failure through ``_ensure_skills_synced``
     so we verify the outer ``except Exception`` catch (F4 invariant) —
     ``_synced_for_this_run`` stays False on failure, then becomes True after
     the healing retry.  Uses the same outer-boundary ``MemSandbox.execute``
     patch as Block 1 — no internal function patching.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.credentials.encryption import FernetBackend
from cubebox.sandbox.lazy import LazySandbox, _sync_skills
from cubebox.sandbox.manager import SandboxManager
from cubebox.skills.cache import SkillCache
from cubebox.skills.service import SkillCatalogService
from cubebox.skills.sync_manifest import MANIFEST_PATH
from tests.e2e.conftest import MemSandbox

# ---------------------------------------------------------------------------
# Block 1: direct _sync_skills — failure + manifest integrity + self-heal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_failure_does_not_write_manifest_and_next_sync_heals(
    fresh_workspace_and_sandbox: SimpleNamespace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """_sync_skills raise on tar-extract must leave the manifest unwritten;
    a second successful call to _sync_skills must populate it.

    Invariant: if partial-write protection regresses (manifest written before
    extract completes), the first assertion fails.  If self-heal regresses,
    the second assertion fails.
    """
    from tests.e2e.conftest import install_skill_for_workspace, uninstall_skill_for_workspace

    ns = fresh_workspace_and_sandbox

    # Install a skill so list_enabled_for_workspace returns something.
    async with session_factory() as session:
        skill_id = await install_skill_for_workspace(
            session,
            org_id=ns.org_id,
            org_slug=ns.org_slug,
            workspace_id=ns.workspace_id,
            user_id=ns.user_id,
            slug="probe-failure",
        )

    cache_dir = Path(tempfile.mkdtemp())

    async with session_factory() as catalog_session:
        catalog = SkillCatalogService(
            session=catalog_session, cache=SkillCache(cache_root=cache_dir)
        )
        sandbox = MemSandbox()

        # Patch execute: fail once on the tar command, succeed otherwise.
        fail_once: dict[str, bool] = {"done": False}
        original_execute = sandbox.execute

        async def _flaky_execute(
            command: str,
            *,
            timeout: int | None = None,
            envs: dict[str, str] | None = None,
        ) -> Any:
            if "tar -xzf" in command and not fail_once["done"]:
                fail_once["done"] = True
                raise RuntimeError("simulated extract failure")
            return await original_execute(command, timeout=timeout, envs=envs)

        sandbox.execute = _flaky_execute  # type: ignore[method-assign]

        # First sync: extract fails → _sync_skills must raise (the caller,
        # _ensure_skills_synced, is the catcher; here we catch in the test).
        with pytest.raises(RuntimeError, match="simulated extract failure"):
            await _sync_skills(
                catalog=catalog,
                workspace_id=ns.workspace_id,
                org_id=ns.org_id,
                sandbox=sandbox,
            )

        # Manifest must NOT be present — it is written AFTER extract succeeds.
        try:
            [(_, raw)] = await sandbox.download([MANIFEST_PATH])
            manifest: dict[str, Any] = json.loads(raw)
            # If somehow written, it must not claim any skills are present.
            assert not manifest.get("skills"), (
                f"manifest written after failed extract — half-state detected: {manifest}"
            )
        except FileNotFoundError:
            pass  # expected — manifest write was never reached

        # Restore execute to normal; second sync must heal the state.
        sandbox.execute = original_execute  # type: ignore[method-assign]

        await _sync_skills(
            catalog=catalog,
            workspace_id=ns.workspace_id,
            org_id=ns.org_id,
            sandbox=sandbox,
        )

        [(_, healed_raw)] = await sandbox.download([MANIFEST_PATH])
        healed: dict[str, Any] = json.loads(healed_raw)
        assert healed.get("skills"), (
            f"second sync did not heal — manifest.skills still empty: {healed}"
        )

    # Cleanup.
    async with session_factory() as cleanup_session:
        await uninstall_skill_for_workspace(
            cleanup_session,
            workspace_id=ns.workspace_id,
            org_id=ns.org_id,
            skill_id=skill_id,
        )


# ---------------------------------------------------------------------------
# Block 2: LazySandbox gate — F4 invariant + self-healing via _ensure_skills_synced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lazy_sandbox_f4_synced_flag_stays_false_on_failure_then_heals(
    fresh_workspace_and_sandbox: SimpleNamespace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """F4 invariant: _ensure_skills_synced must NOT set _synced_for_this_run on failure.

    Patches ``MemSandbox.execute`` at the outer boundary to raise on the
    tar-extract command (first occurrence only), which causes the real
    ``_sync_skills`` to raise, which ``_ensure_skills_synced`` catches without
    setting the flag.  The second ``lazy.execute`` retries the real sync path
    and heals.

    Invariant: if F4 regresses (flag set on failure), the second execute would
    short-circuit and leave _synced_for_this_run True and skills stale.
    """
    from tests.e2e.conftest import install_skill_for_workspace, uninstall_skill_for_workspace

    ns = fresh_workspace_and_sandbox

    # Install a skill so the real _sync_skills has work to do on the second call.
    async with session_factory() as session:
        skill_id = await install_skill_for_workspace(
            session,
            org_id=ns.org_id,
            org_slug=ns.org_slug,
            workspace_id=ns.workspace_id,
            user_id=ns.user_id,
            slug="probe-f4",
        )

    cache_dir = Path(tempfile.mkdtemp())
    enc_backend = FernetBackend([Fernet.generate_key()])
    mgr = SandboxManager(session_factory, enc_backend)

    # Fresh MemSandbox — patched at the outer boundary to fail once on tar.
    sandbox = MemSandbox()
    original_execute = sandbox.execute
    fail_once: dict[str, bool] = {"done": False}

    async def _flaky_execute(
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
    ) -> Any:
        if "tar -xzf" in command and not fail_once["done"]:
            fail_once["done"] = True
            raise RuntimeError("simulated extract failure")
        return await original_execute(command, timeout=timeout, envs=envs)

    sandbox.execute = _flaky_execute  # type: ignore[method-assign]

    # Cleanup must run even if assertions fail so the workspace FK is satisfied
    # when fresh_workspace_and_sandbox tears down.
    lazy: LazySandbox | None = None
    try:
        async with session_factory() as gate_session:
            catalog = SkillCatalogService(
                session=gate_session, cache=SkillCache(cache_root=cache_dir)
            )

            lazy = LazySandbox(
                manager=mgr,
                scope_type="user",
                scope_id=ns.user_id,
                user_id=ns.user_id,
                org_id=ns.org_id,
                workspace_id=ns.workspace_id,
                catalog=catalog,
            )
            # Inject the MemSandbox directly so we can inspect its file store.
            lazy._sandbox = sandbox

            # First execute: _ensure_skills_synced → real _sync_skills →
            # sandbox.execute(tar cmd) raises → caught by _ensure_skills_synced
            # → _synced_for_this_run stays False.  The execute itself must NOT raise.
            result = await lazy.execute("true")
            assert result.exit_code in (0, None), (
                f"sandbox not usable after sync failure: exit={result.exit_code}"
            )

            # F4: flag must remain False so the next execute retries.
            assert lazy._synced_for_this_run is False, (
                "_synced_for_this_run was set True despite _sync_skills failure — F4 violated"
            )

            # Manifest must NOT be present (first sync failed before writing it).
            try:
                [(_, raw)] = await sandbox.download([MANIFEST_PATH])
                partial: dict[str, Any] = json.loads(raw)
                assert not partial.get("skills"), (
                    f"manifest has skills after failed sync — partial write detected: {partial}"
                )
            except FileNotFoundError:
                pass  # expected

            # Restore execute so the second sync succeeds.
            sandbox.execute = original_execute  # type: ignore[method-assign]

            # Second execute: real _sync_skills runs without interference → heals.
            await lazy.execute("true")

            # Flag becomes True after the successful retry.
            assert lazy._synced_for_this_run is True, (
                "_synced_for_this_run should be True after successful retry"
            )

            # Manifest must now be populated.
            [(_, healed_raw)] = await sandbox.download([MANIFEST_PATH])
            healed: dict[str, Any] = json.loads(healed_raw)
            assert healed.get("skills"), (
                f"self-heal failed — manifest.skills empty after successful retry: {healed}"
            )

    finally:
        if lazy is not None:
            await lazy.close()
        async with session_factory() as cleanup_session:
            await uninstall_skill_for_workspace(
                cleanup_session,
                workspace_id=ns.workspace_id,
                org_id=ns.org_id,
                skill_id=skill_id,
            )
