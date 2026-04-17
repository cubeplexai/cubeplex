"""SandboxManager — manages sandbox lifecycle per user.

Core responsibilities:
- Get or create a sandbox for a user (reuse existing running sandbox)
- Health-check existing sandboxes before reuse
- Build user-specific PVC volumes
- Sync skills to newly created sandboxes
- Clean up expired sandboxes in the background
"""

import hashlib
import re
from datetime import timedelta
from pathlib import Path

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
        self._ttl: int = config.get("sandbox.ttl", 600)
        self._ready_timeout: int = config.get("sandbox.ready_timeout", 60)

        # Sandbox workdir
        self._workdir: str = config.get("sandbox.workdir", "/workspace")

        # Volume config
        self._volume_enabled: bool = config.get("sandbox.volume.enabled", False)
        self._volume_mount_path: str = config.get("sandbox.volume.mount_path", "/workspace")
        self._volume_pvc_prefix: str = config.get("sandbox.volume.pvc_prefix", "cubebox-user")

    def _build_connection_config(self) -> ConnectionConfig:
        """Build OpenSandbox ConnectionConfig from app config."""
        return ConnectionConfig(
            domain=self._domain,
            api_key=self._api_key,
            request_timeout=timedelta(seconds=self._request_timeout),
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

    async def get_or_create(self, user_id: str) -> Sandbox:
        """Get the user's active sandbox, or create a new one.

        Flow:
        1. Query DB for an existing RUNNING sandbox for this user
        2. If found, try to connect and health-check it
        3. If healthy, return it; otherwise mark terminated and create new
        4. Sync skills to newly created sandboxes

        Args:
            user_id: The user identifier

        Returns:
            An OpenSandbox backend instance ready for use
        """
        conn_config = self._build_connection_config()

        async with self._session_factory() as session:
            repo = UserSandboxRepository(session)  # type: ignore[call-arg]
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

            raw_sandbox = await opensandbox.Sandbox.create(
                self._image,
                connection_config=conn_config,
                timeout=None,
                ready_timeout=timedelta(seconds=self._ready_timeout),
                volumes=volumes,
            )

            backend = OpenSandbox(sandbox=raw_sandbox, workdir=self._workdir)
            logger.info("Sandbox created: {}", backend.id)

            # Sync skills
            await self._sync_skills(backend)

            # Persist to DB
            await repo.create(
                user_id=user_id,
                sandbox_id=raw_sandbox.id,
                image=self._image,
                ttl_seconds=self._ttl,
            )

            return backend

    async def release(self, sandbox_id: str) -> None:
        """Mark a sandbox as idle (update last activity time).

        Called after a request finishes. Does NOT kill the sandbox.

        Args:
            sandbox_id: The OpenSandbox sandbox ID
        """
        async with self._session_factory() as session:
            repo = UserSandboxRepository(session)  # type: ignore[call-arg]
            await repo.update_activity_by_sandbox_id(sandbox_id)
            logger.debug("Released sandbox {}", sandbox_id)

    async def cleanup_expired(self) -> None:
        """Find and terminate sandboxes that exceeded their TTL.

        This is meant to be called periodically by a background task.
        """
        conn_config = self._build_connection_config()

        async with self._session_factory() as session:
            repo = UserSandboxRepository(session)  # type: ignore[call-arg]
            expired = await repo.list_expired()

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

                await repo.mark_terminated(record.id)

    async def _sync_skills(self, backend: Sandbox) -> None:
        """Sync builtin skills to the sandbox container.

        Loads skills from the local filesystem and uploads them to the
        container's /.skills directory.

        Args:
            backend: Sandbox backend instance
        """
        from cubebox.sandbox.skills import SkillLoader

        skills_enabled: bool = config.get("sandbox.skills.enabled", True)
        if not skills_enabled:
            logger.info("Skills sync disabled in config")
            return

        skills_dir_str: str = config.get("sandbox.skills.builtin_dir", "skills/builtin")
        backend_dir = Path(__file__).parent.parent.parent
        skills_dir = backend_dir / skills_dir_str

        if not skills_dir.exists():
            logger.warning("Skills directory not found: {}", skills_dir)
            return

        loader = SkillLoader(skills_dir)
        files = loader.load_builtin()

        if not files:
            logger.info("No skill files to sync")
            return

        # Create parent directories
        dirs = set()
        for path, _ in files:
            parent = path.rsplit("/", 1)[0]
            if parent:
                dirs.add(parent)
        if dirs:
            mkdir_cmd = "mkdir -p " + " ".join(f'"{d}"' for d in dirs)
            await backend.execute(mkdir_cmd)

        await backend.upload(files)
        logger.info("Synced {} skill files to sandbox", len(files))


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
