"""LazySandbox — defers sandbox creation until first actual use.

Tools are registered immediately so the LLM knows they exist,
but the sandbox container is only created/connected when a tool
is actually invoked (execute, write_file, edit_file, save_artifact).

If the underlying sandbox becomes unhealthy, the next call will
transparently create a new one.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from cubebox.sandbox.base import ExecuteResult, Sandbox
from cubebox.skills.sandbox_paths import sandbox_skill_dir

if TYPE_CHECKING:
    from cubebox.sandbox.manager import SandboxManager
    from cubebox.skills.service import SkillCatalogService


async def _sync_skills(
    *,
    catalog: SkillCatalogService,
    workspace_id: str,
    org_id: str,
    sandbox: Sandbox,
) -> None:
    """Push enabled skills' files into the freshly-created sandbox.

    Idempotent per skill_version_id: a sandbox tracks what it has already
    received via ``Sandbox.has_synced``/``mark_synced``.
    """
    skills = await catalog.list_enabled_for_workspace(workspace_id, org_id=org_id)
    for s in skills:
        if sandbox.has_synced(s.skill_version_id):
            continue
        per_skill = await catalog.list_files_for_sandbox_sync(
            s.skill_version_id, storage_prefix=s.storage_prefix
        )
        target_root = sandbox_skill_dir(s.name, s.version) + "/"
        files = [(target_root + rel, data) for rel, data in per_skill]
        if files:
            await sandbox.upload(files)
        # Mark only after a successful upload so a failed upload is retried
        # the next time this sandbox instance is used.
        sandbox.mark_synced(s.skill_version_id)


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
            if self._catalog is not None:
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
            self._sandbox = sandbox
            logger.info("Lazy sandbox: ready (id={})", self._sandbox.id)
            return self._sandbox

    async def _ensure_with_retry(self) -> Sandbox:
        """Ensure sandbox, retrying once if the existing one is broken."""
        try:
            sandbox = await self._ensure()
        except Exception:
            # First attempt failed — reset and try creating a fresh one
            async with self._lock:
                self._sandbox = None
            logger.warning(
                "Lazy sandbox: first attempt failed for scope {}/{}, retrying",
                self._scope_type,
                self._scope_id,
            )
            sandbox = await self._ensure()

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
            logger.warning("Lazy sandbox: execute failed, recreating sandbox")
            sandbox = await self._ensure()
            return await sandbox.execute(command, timeout=timeout, envs=envs)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        sandbox = await self._ensure_with_retry()
        try:
            await sandbox.upload(files)
        except Exception:
            async with self._lock:
                self._sandbox = None
            logger.warning("Lazy sandbox: upload failed, recreating sandbox")
            sandbox = await self._ensure()
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
