"""LazySandbox — defers sandbox creation until first actual use.

Tools are registered immediately so the LLM knows they exist,
but the sandbox container is only created/connected when a tool
is actually invoked (execute, write_file, edit_file, save_artifact).

If the underlying sandbox becomes unhealthy, the next call will
transparently create a new one.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from loguru import logger

from cubebox.sandbox.base import ExecuteResult, Sandbox, SandboxError
from cubebox.skills.sandbox_paths import SKILLS_ROOT, safe_skill_name
from cubebox.skills.sync_diff import ResolvedLike, compute_skill_sync_diff
from cubebox.skills.sync_manifest import MANIFEST_PATH, build_manifest, parse_manifest
from cubebox.skills.sync_tar import (
    SKILLS_DELTA_TGZ_PATH,
    build_extract_and_remove_cmd,
    build_tarball,
)

if TYPE_CHECKING:
    from cubebox.sandbox.manager import SandboxManager
    from cubebox.skills.service import SkillCatalogService


async def _collect_files_for_push(
    catalog: SkillCatalogService, to_push: Sequence[ResolvedLike]
) -> list[tuple[str, bytes]]:
    """Flatten per-skill file lists into tar-relative ``(rel, bytes)`` pairs.

    Each skill version contributes
    ``<safe_name>/<version>/<rel-inside-bundle>`` paths — no leading slash, so
    sandbox-side ``tar -xzf -C /workspace/.skills`` puts them at the right
    place.
    """
    result: list[tuple[str, bytes]] = []
    for s in to_push:
        per_skill = await catalog.list_files_for_sandbox_sync(
            s.skill_version_id, storage_prefix=s.storage_prefix
        )
        for rel, data in per_skill:
            tar_rel = f"{safe_skill_name(s.name)}/{s.version}/{rel}"
            result.append((tar_rel, data))
    return result


async def _sync_skills(
    *,
    catalog: SkillCatalogService,
    workspace_id: str,
    org_id: str,
    sandbox: Sandbox,
) -> None:
    """Sync enabled skills into the sandbox via persistent PVC manifest + diff.

    Hot path (manifest matches desired): one ``download`` + one DB query, no
    file transfer. Cold path: one tar.gz upload + one extract command.
    The final manifest write happens after extract; if any step before it
    fails, the manifest is not updated, and the next sync re-runs the diff
    from whatever state the PVC is in (cold or prior-warm).
    """
    # 1. read manifest. OpenSandbox.download maps "not found" to
    # FileNotFoundError, but other backends (LocalSandbox) and non-404 errors
    # bubble up as SandboxError. Both → treat as "no usable manifest, cold".
    # Defensive unpack: if a backend returns [] instead of raising on missing
    # file, the single-element destructure would raise ValueError uncaught.
    try:
        download_result = await sandbox.download([MANIFEST_PATH])
        if not download_result:
            manifest: dict[str, Any] = {"skills": {}}
        else:
            _, raw = download_result[0]
            manifest = parse_manifest(raw)
    except FileNotFoundError:
        manifest = {"skills": {}}
    except SandboxError:
        manifest = {"skills": {}}

    # 2. desired
    enabled = await catalog.list_enabled_for_workspace(workspace_id, org_id=org_id)

    # 3. diff
    diff = compute_skill_sync_diff(manifest, enabled)
    if diff.is_empty():
        return

    # 4. push + remove
    # files=[] is possible even when to_push is non-empty (catalog returned no
    # files for a skill_version_id — bad storage_prefix, race with delete...).
    # has_push must reflect "we actually uploaded a tarball", not "diff said to
    # push", or tar -xzf will fail looking for a file we never sent (F2).
    desired_push_count = len(diff.to_push)
    files: list[tuple[str, bytes]] = []
    if diff.to_push:
        files = await _collect_files_for_push(catalog, diff.to_push)
    files_uploaded = bool(files)
    if files_uploaded:
        tarball = await asyncio.to_thread(build_tarball, files)
        await sandbox.upload([(SKILLS_DELTA_TGZ_PATH, tarball)])

    repush_names = [safe_skill_name(s.name) for s in diff.to_push] if files_uploaded else []
    cmd = build_extract_and_remove_cmd(
        skills_root=SKILLS_ROOT,
        has_push=files_uploaded,
        to_repush_names=repush_names,
        to_remove=diff.to_remove,
    )
    if cmd:
        await sandbox.execute(cmd)

    # 5. manifest last (so partial failures are healed by next sync).
    # If to_push was non-empty but no files came back (storage inconsistency),
    # skip the manifest write so the next sync will retry the push — writing
    # the manifest here would mark those skills as synced and suppress retries.
    if desired_push_count > 0 and not files_uploaded:
        logger.warning(
            "Skill sync: {} skill(s) queued to push but no files returned; "
            "skipping manifest write so next sync retries",
            desired_push_count,
        )
        return
    new_manifest = build_manifest(enabled)
    blob = json.dumps(new_manifest, ensure_ascii=False).encode("utf-8")
    await sandbox.upload([(MANIFEST_PATH, blob)])


class LazySandbox(Sandbox):
    """Sandbox proxy that defers creation until first use.

    Args:
        manager: SandboxManager instance for get_or_create / release.
        scope_type: Polymorphic scope discriminator (``'user'`` /
            ``'conversation'`` / ``'topic'``).
        scope_id: The corresponding scope id.
        user_id: Audit + egress owner for the underlying sandbox row.
        org_id: Active org scope for sandbox persistence.
        workspace_id: Active workspace scope for sandbox persistence.
        workdir: Working directory (used for prompt injection before sandbox exists).
    """

    def __init__(
        self,
        *,
        manager: SandboxManager,
        scope_type: str,
        scope_id: str,
        user_id: str,
        org_id: str,
        workspace_id: str,
        workdir: str = "/workspace",
        catalog: SkillCatalogService | None = None,
        op_timeout_seconds: int | None = None,
    ) -> None:
        self._manager = manager
        self._scope_type = scope_type
        self._scope_id = scope_id
        self._user_id = user_id
        self._org_id = org_id
        self._workspace_id = workspace_id
        self._workdir = workdir
        self._catalog = catalog
        # Sizes the in-use lease window passed to ``manager.renew_lease``. None
        # falls back to the manager's default (``sandbox.lease_seconds``).
        self._op_timeout_seconds = op_timeout_seconds
        self._sandbox: Sandbox | None = None
        self._lock = asyncio.Lock()
        self._synced_for_this_run = False
        self._sync_lock = asyncio.Lock()  # independent of _lock; serialises _sync_skills

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        if self._sandbox is None:
            return "<not-created>"
        return self._sandbox.id

    @property
    def workdir(self) -> str:
        return self._workdir

    @property
    def initialized(self) -> bool:
        """Whether the underlying sandbox has been created."""
        return self._sandbox is not None

    # ------------------------------------------------------------------
    # Internal: ensure a live sandbox exists
    # ------------------------------------------------------------------

    async def _ensure(self) -> Sandbox:
        """Return the underlying sandbox, creating it on first call."""
        if self._sandbox is not None:
            return self._sandbox

        async with self._lock:
            # Double-check after acquiring the lock
            if self._sandbox is not None:
                return self._sandbox

            logger.info(
                "Lazy sandbox: creating sandbox for scope {}/{}",
                self._scope_type,
                self._scope_id,
            )
            sandbox = await self._manager.get_or_create(
                scope_type=self._scope_type,
                scope_id=self._scope_id,
                user_id=self._user_id,
                org_id=self._org_id,
                workspace_id=self._workspace_id,
            )
            self._sandbox = sandbox
            logger.info("Lazy sandbox: ready (id={})", self._sandbox.id)
            return self._sandbox

    async def _ensure_skills_synced(self, sandbox: Sandbox) -> None:
        """Sync skills into sandbox at most once per run.

        Double-check pattern: fast path avoids lock acquisition after first sync.
        Failure does NOT set the flag so the next tool call retries (F4).
        """
        if self._catalog is None or self._synced_for_this_run:
            return
        async with self._sync_lock:
            # Second concurrent call may have already completed the sync.
            if self._synced_for_this_run:
                return
            try:
                await _sync_skills(
                    catalog=self._catalog,
                    workspace_id=self._workspace_id,
                    org_id=self._org_id,
                    sandbox=sandbox,
                )
            except Exception:
                logger.exception(
                    "Skill sync failed for ws {}; sandbox usable without skills",
                    self._workspace_id,
                )
                return  # do NOT set flag — next tool call retries (F4)
            self._synced_for_this_run = True

    async def _ensure_with_retry(self) -> Sandbox:
        """Ensure sandbox, retrying once if the existing one is broken."""
        try:
            sandbox = await self._ensure()
        except Exception:
            # First attempt failed — reset and try creating a fresh one
            async with self._lock:
                self._sandbox = None
                self._synced_for_this_run = False  # new sandbox must re-sync (F5)
            logger.warning(
                "Lazy sandbox: first attempt failed for scope {}/{}, retrying",
                self._scope_type,
                self._scope_id,
            )
            sandbox = await self._ensure()

        await self._ensure_skills_synced(sandbox)

        # Refresh last_activity so cleanup_expired won't kill an in-use
        # sandbox mid-turn. Throttled inside the manager.
        try:
            await self._manager.touch(
                sandbox.id,
                org_id=self._org_id,
                workspace_id=self._workspace_id,
            )
        except Exception:
            logger.exception("Lazy sandbox: touch failed (non-fatal)")

        # Renew the in-use lease so the idle-pause reaper skips this sandbox
        # while a tool call is in flight. Sized to op timeout when known;
        # otherwise the manager falls back to its default lease window.
        try:
            await self._manager.renew_lease(
                sandbox.id,
                org_id=self._org_id,
                workspace_id=self._workspace_id,
                lease_seconds=self._op_timeout_seconds,
            )
        except Exception:
            logger.exception("Lazy sandbox: lease renew failed (non-fatal)")
        return sandbox

    # ------------------------------------------------------------------
    # Sandbox interface
    # ------------------------------------------------------------------

    def set_run_env(self, env: dict[str, str]) -> None:
        """Forward to the underlying backend if it has been resolved already.

        Called by SandboxManager after get_or_create returns; at that point the
        backend is always resolved (manager creates it before returning).  If for
        any reason the backend is not yet resolved, the env is silently dropped —
        the manager will set it again on the next get_or_create call.
        """
        if self._sandbox is not None:
            self._sandbox.set_run_env(env)

    def supports_pause(self) -> bool:
        return self._sandbox.supports_pause() if self._sandbox is not None else False

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
    ) -> ExecuteResult:
        sandbox = await self._ensure_with_retry()
        try:
            return await sandbox.execute(command, timeout=timeout, envs=envs)
        except Exception:
            # Sandbox may have died — invalidate and retry once
            async with self._lock:
                self._sandbox = None
                self._synced_for_this_run = False  # new sandbox must re-sync (F5)
            logger.warning("Lazy sandbox: execute failed, recreating sandbox")
            sandbox = await self._ensure()
            await self._ensure_skills_synced(sandbox)
            return await sandbox.execute(command, timeout=timeout, envs=envs)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        sandbox = await self._ensure_with_retry()
        try:
            await sandbox.upload(files)
        except Exception:
            async with self._lock:
                self._sandbox = None
                self._synced_for_this_run = False  # new sandbox must re-sync (F5)
            logger.warning("Lazy sandbox: upload failed, recreating sandbox")
            sandbox = await self._ensure()
            await self._ensure_skills_synced(sandbox)
            await sandbox.upload(files)

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        sandbox = await self._ensure_with_retry()
        # A download failure is almost always a missing / unreadable path (e.g.
        # the agent guessed a wrong skill-file path), NOT a dead sandbox.
        # Recreating the sandbox here would wipe /workspace AND still not
        # produce the file (a fresh sandbox can't hold work it never ran), so
        # the recreate is pure data loss. Surface the error to the caller
        # instead — the file_read tool turns it into a corrigible error the
        # agent can act on. A genuinely dead sandbox is detected and recreated
        # by the next execute/upload call.
        return await sandbox.download(paths)

    async def close(self) -> None:
        if self._sandbox is not None:
            await self._sandbox.close()
