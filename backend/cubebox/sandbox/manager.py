"""SandboxManager — manages sandbox lifecycle per user.

Core responsibilities:
- Get or create a sandbox for a user (reuse existing running sandbox)
- Health-check existing sandboxes before reuse
- Build user-specific PVC volumes
- Clean up expired sandboxes in the background

Note: skill sync no longer happens here. After M3 it is handled by
``LazySandbox._ensure()`` via the SkillCatalogService — only the skills
that are enabled for the request's workspace get pushed, and they get
versioned paths under ``/.skills/<name>/<version>/``.
"""

import hashlib
import re
from datetime import UTC, datetime, timedelta

import opensandbox
from loguru import logger
from opensandbox.config import ConnectionConfig
from opensandbox.models.sandboxes import PVC, Volume
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.config import config
from cubebox.repositories.user_sandbox import UserSandboxRepository
from cubebox.sandbox.base import Sandbox
from cubebox.sandbox.opensandbox import OpenSandbox


class SandboxManager:
    """Manages sandbox lifecycle: create, reuse, and cleanup."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

        # Read config
        self._domain: str = config.get("sandbox.domain", "localhost:8090")
        self._image: str = config.get("sandbox.image", "ubuntu:22.04")
        self._api_key: str | None = config.get("sandbox.api_key", None)
        self._request_timeout: int = config.get("sandbox.request_timeout", 60)
        # Separate, longer budget for the synchronous create call: the server holds
        # the POST /sandboxes open until the pod is ready, so a cold image pull can
        # take minutes — far longer than the per-command request_timeout.
        self._create_timeout: int = config.get("sandbox.create_timeout", 300)
        self._ttl: int = config.get("sandbox.ttl", 600)
        self._touch_interval: int = config.get("sandbox.touch_interval", 60)
        self._ready_timeout: int = config.get("sandbox.ready_timeout", 60)
        self._use_server_proxy: bool = config.get("sandbox.use_server_proxy", False)

        # In-process cache of (sandbox_id -> last_touch_at) used to throttle
        # mid-turn activity bumps so chatty tool loops don't hammer the DB.
        self._touch_cache: dict[str, datetime] = {}

        # Sandbox workdir
        self._workdir: str = config.get("sandbox.workdir", "/workspace")

        # Resource config
        self._resource_cpu: str = config.get("sandbox.resource.cpu", "100m")
        self._resource_memory: str = config.get("sandbox.resource.memory", "100Mi")

        # Volume config
        self._volume_enabled: bool = config.get("sandbox.volume.enabled", False)
        self._volume_mount_path: str = config.get("sandbox.volume.mount_path", "/workspace")
        self._volume_pvc_prefix: str = config.get("sandbox.volume.pvc_prefix", "cubebox-user")

    def _build_connection_config(self, *, request_timeout: int | None = None) -> ConnectionConfig:
        """Build OpenSandbox ConnectionConfig from app config.

        ``request_timeout`` overrides the per-command HTTP timeout — used to give
        the synchronous create call a longer budget than ordinary commands.
        """
        return ConnectionConfig(
            domain=self._domain,
            api_key=self._api_key,
            request_timeout=timedelta(seconds=request_timeout or self._request_timeout),
            use_server_proxy=self._use_server_proxy,
        )

    def _build_user_volume(self, user_id: str) -> Volume:
        """Build a PVC Volume for the given user."""
        sanitized = re.sub(r"[^a-z0-9-]+", "-", user_id.lower()).strip("-")
        if not sanitized:
            sanitized = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]

        max_suffix_len = 63 - len(self._volume_pvc_prefix) - 1
        if len(sanitized) > max_suffix_len:
            sanitized = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]

        pvc_name = f"{self._volume_pvc_prefix}-{sanitized}"
        return Volume(
            name="user-workspace",
            pvc=PVC(claimName=pvc_name),
            mountPath=self._volume_mount_path,
            readOnly=False,
        )

    async def get_or_create(
        self,
        user_id: str,
        *,
        org_id: str,
        workspace_id: str,
    ) -> Sandbox:
        """Get the user's active sandbox for this workspace, or create a new one.

        Flow:
        1. Query DB for an existing RUNNING sandbox for this user in this workspace
        2. If found, try to connect and health-check it
        3. If healthy, return it; otherwise mark terminated and create new
        4. Sync skills to newly created sandboxes

        Args:
            user_id: The user identifier
            org_id: The active org scope
            workspace_id: The active workspace scope

        Returns:
            An OpenSandbox backend instance ready for use
        """
        conn_config = self._build_connection_config()

        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_active_by_user(user_id)

            if record:
                logger.info(
                    "Found existing sandbox {} for user {}",
                    record.sandbox_id,
                    user_id,
                )
                try:
                    raw_sandbox = await opensandbox.Sandbox.connect(
                        record.sandbox_id,
                        connection_config=conn_config,
                    )
                    if await raw_sandbox.is_healthy():
                        await repo.update_activity(record.id)
                        logger.info("Reusing healthy sandbox {}", record.sandbox_id)
                        return OpenSandbox(sandbox=raw_sandbox, workdir=self._workdir)
                    else:
                        logger.warning(
                            "Sandbox {} is not healthy, will recreate",
                            record.sandbox_id,
                        )
                except Exception as e:
                    logger.warning(
                        "Failed to connect to sandbox {}: {}",
                        record.sandbox_id,
                        e,
                    )
                # Mark the unhealthy/unreachable sandbox as terminated
                await repo.mark_terminated(record.id)

            # Create a new sandbox
            volumes: list[Volume] | None = None
            if self._volume_enabled:
                volume = self._build_user_volume(user_id)
                volumes = [volume]
                logger.info(
                    "Creating new sandbox for user {} with PVC {}",
                    user_id,
                    volume.pvc.claim_name,  # type: ignore[union-attr]
                )
            else:
                logger.info("Creating new sandbox for user {}", user_id)

            # Give only the create call the longer budget: the create POST is held
            # open server-side until the pod is ready, so it must survive a cold
            # image pull. ``create_conn_config`` is otherwise identical to the
            # default.
            create_conn_config = self._build_connection_config(request_timeout=self._create_timeout)
            raw_sandbox = await opensandbox.Sandbox.create(
                self._image,
                connection_config=create_conn_config,
                timeout=None,
                ready_timeout=timedelta(seconds=self._ready_timeout),
                volumes=volumes,
                resource={"cpu": self._resource_cpu, "memory": self._resource_memory},
            )
            sandbox_id = raw_sandbox.id
            logger.info("Sandbox created: {}", sandbox_id)

            # Persist before rebinding so a reconnect failure can't orphan the
            # sandbox — the reuse path will find and health-check it next turn.
            # Skill sync is the LazySandbox's responsibility post-M3.
            await repo.create(
                user_id=user_id,
                sandbox_id=sandbox_id,
                image=self._image,
                ttl_seconds=self._ttl,
            )

            # Rebind to the default per-command timeout: the create call's adapters
            # captured the longer create_timeout, but ordinary commands on this
            # sandbox must use request_timeout, not create_timeout. Reconnecting
            # rebuilds the HTTP clients with the default budget. Skip the health
            # check — create already gated on readiness (ready_timeout), so a second
            # readiness probe here would only add a redundant failure path.
            raw_sandbox = await opensandbox.Sandbox.connect(
                sandbox_id,
                connection_config=conn_config,
                skip_health_check=True,
            )
            return OpenSandbox(sandbox=raw_sandbox, workdir=self._workdir)

    async def release(
        self,
        sandbox_id: str,
        *,
        org_id: str,
        workspace_id: str,
    ) -> None:
        """Mark a sandbox as idle (update last activity time).

        Called after a request finishes. Does NOT kill the sandbox.

        Args:
            sandbox_id: The OpenSandbox sandbox ID
            org_id: The active org scope
            workspace_id: The active workspace scope
        """
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            await repo.update_activity_by_sandbox_id(sandbox_id)
            logger.debug("Released sandbox {}", sandbox_id)

    async def touch(
        self,
        sandbox_id: str,
        *,
        org_id: str,
        workspace_id: str,
        force: bool = False,
    ) -> None:
        """Refresh `last_activity_at` for an in-use sandbox.

        Called from `LazySandbox` before each tool invocation so that
        cleanup_expired won't kill a sandbox in active use mid-turn.
        Throttled by `sandbox.touch_interval` to avoid one DB write per
        execute call. Pass ``force=True`` to bypass the throttle — used by the
        browser keepalive so every ping reliably extends the TTL regardless of
        the client cadence vs. ``touch_interval``.
        """
        now = datetime.now(UTC)
        if not force:
            last = self._touch_cache.get(sandbox_id)
            if last is not None and (now - last).total_seconds() < self._touch_interval:
                return
        self._touch_cache[sandbox_id] = now

        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            await repo.update_activity_by_sandbox_id(sandbox_id)

    async def touch_active(
        self,
        user_id: str,
        *,
        org_id: str,
        workspace_id: str,
    ) -> bool:
        """Refresh activity for the user's *existing* active sandbox, if any.

        Unlike :meth:`touch` (keyed by sandbox_id) this never creates a sandbox —
        used by the browser keepalive so a dead/reaped sandbox isn't silently
        re-provisioned on every ping while the panel stays open. Returns whether
        an active sandbox was found. Bypasses the touch throttle.
        """
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
            record = await repo.get_active_by_user(user_id)
            if record is None:
                return False
            await repo.update_activity(record.id)
            self._touch_cache[record.sandbox_id] = datetime.now(UTC)
            return True

    async def cleanup_expired(self) -> None:
        """Find and terminate sandboxes that exceeded their TTL.

        This is meant to be called periodically by a background task.
        Runs in system scope (across all workspaces) via the unscoped
        `list_expired_system` classmethod, then re-instantiates a scoped
        repo per record to mark it terminated.
        """
        conn_config = self._build_connection_config()

        async with self._session_factory() as session:
            expired = await UserSandboxRepository.list_expired_system(session)

            if not expired:
                return

            logger.info("Found {} expired sandbox(es) to clean up", len(expired))

            for record in expired:
                try:
                    raw_sandbox = await opensandbox.Sandbox.connect(
                        record.sandbox_id,
                        connection_config=conn_config,
                        skip_health_check=True,
                    )
                    await raw_sandbox.kill()
                    await raw_sandbox.close()
                    logger.info("Killed expired sandbox {}", record.sandbox_id)
                except Exception as e:
                    logger.warning(
                        "Failed to kill sandbox {} (may already be gone): {}",
                        record.sandbox_id,
                        e,
                    )

                scoped_repo = UserSandboxRepository(
                    session,
                    org_id=record.org_id,
                    workspace_id=record.workspace_id,
                )
                await scoped_repo.mark_terminated(record.id)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_sandbox_manager: SandboxManager | None = None


def init_sandbox_manager(session_factory: async_sessionmaker[AsyncSession]) -> SandboxManager:
    """Initialize the global SandboxManager singleton.

    Called once during application startup.

    Args:
        session_factory: SQLAlchemy async session factory

    Returns:
        The initialized SandboxManager instance
    """
    global _sandbox_manager
    _sandbox_manager = SandboxManager(session_factory)
    return _sandbox_manager


def get_sandbox_manager() -> SandboxManager:
    """Get the global SandboxManager instance.

    Returns:
        The SandboxManager singleton

    Raises:
        RuntimeError: If the manager hasn't been initialized
    """
    if _sandbox_manager is None:
        raise RuntimeError("SandboxManager not initialized. Call init_sandbox_manager() first.")
    return _sandbox_manager
