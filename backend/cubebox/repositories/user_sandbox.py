"""UserSandbox repository."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.user_sandbox import UserSandbox
from cubebox.repositories.base import ScopedRepository


class UserSandboxRepository(ScopedRepository[UserSandbox]):
    """Repository for UserSandbox CRUD operations."""

    model = UserSandbox

    async def create(
        self,
        *,
        user_id: str,
        sandbox_id: str,
        image: str,
        volumes_config: dict[str, Any] | None = None,
        ttl_seconds: int = 3600,
    ) -> UserSandbox:
        """Create a new user sandbox record."""
        record = UserSandbox(
            user_id=user_id,
            sandbox_id=sandbox_id,
            image=image,
            volumes_config=volumes_config,
            ttl_seconds=ttl_seconds,
        )
        return await self.add(record)

    async def get_active_by_user(self, user_id: str) -> UserSandbox | None:
        """Get the active (running) sandbox for a user in this workspace."""
        stmt = (
            self._scoped_select()
            .where(UserSandbox.user_id == user_id)
            .where(UserSandbox.status == "running")
            .order_by(UserSandbox.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_resumable_by_user(self, user_id: str) -> UserSandbox | None:
        """Return a running OR paused row for reuse; never a mid-transition row.

        Callers (the manager) decide whether to resume a ``paused`` row or
        just touch a ``running`` one. Mid-transition rows (``pausing`` /
        ``resuming``) are explicitly excluded — claiming one risks racing
        with the worker that owns the transition.
        """
        stmt = (
            self._scoped_select()
            .where(UserSandbox.user_id == user_id)
            .where(UserSandbox.status.in_(("running", "paused")))  # type: ignore[attr-defined]
            .order_by(UserSandbox.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_sandbox_id(self, sandbox_id: str) -> UserSandbox | None:
        """Get record by OpenSandbox sandbox ID."""
        stmt = self._scoped_select().where(UserSandbox.sandbox_id == sandbox_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_activity(self, record_id: str) -> None:
        """Update last_activity_at timestamp."""
        record = await self.get(record_id)
        if record:
            record.last_activity_at = datetime.now(UTC)
            await self.session.commit()

    async def update_activity_by_sandbox_id(self, sandbox_id: str) -> None:
        """Update last_activity_at by OpenSandbox sandbox ID."""
        record = await self.get_by_sandbox_id(sandbox_id)
        if record:
            record.last_activity_at = datetime.now(UTC)
            await self.session.commit()

    async def mark_terminated(self, record_id: str) -> None:
        """Mark a sandbox as terminated."""
        record = await self.get(record_id)
        if record:
            record.status = "terminated"
            await self.session.commit()

    async def claim_pausing(self, record_id: str) -> bool:
        """Atomically flip running -> pausing, re-asserting idleness + lease.

        A single conditional UPDATE: the idleness, status, and lease checks
        live in the WHERE clause so a fresh touch landing between selection
        and claim makes the claim a no-op. Returns whether a row was claimed.
        """
        stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.id == record_id,  # type: ignore[arg-type]
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                UserSandbox.status == "running",  # type: ignore[arg-type]
                text(
                    "(in_use_until IS NULL OR in_use_until < NOW()) "
                    "AND last_activity_at + ttl_seconds * INTERVAL '1 second' <= NOW()"
                ),
            )
            .values(status="pausing")
        )
        result = cast(CursorResult[Any], await self.session.execute(stmt))
        await self.session.commit()
        return bool(result.rowcount == 1)

    async def _transition(self, record_id: str, frm: str, to: str, **extra: Any) -> bool:
        stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.id == record_id,  # type: ignore[arg-type]
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                UserSandbox.status == frm,  # type: ignore[arg-type]
            )
            .values(status=to, **extra)
        )
        result = cast(CursorResult[Any], await self.session.execute(stmt))
        await self.session.commit()
        return bool(result.rowcount == 1)

    async def mark_paused(self, record_id: str, *, paused_at: datetime | None = None) -> bool:
        """Move ``pausing`` -> ``paused`` and stamp ``paused_at``."""
        return await self._transition(
            record_id,
            "pausing",
            "paused",
            paused_at=paused_at or datetime.now(UTC),
        )

    async def mark_resuming(self, record_id: str) -> bool:
        """Move ``paused`` -> ``resuming``."""
        return await self._transition(record_id, "paused", "resuming")

    async def mark_running(
        self, record_id: str, *, last_resumed_at: datetime | None = None
    ) -> bool:
        """Move to ``running`` from either ``pausing`` (pause failed -> revert)
        or ``resuming`` (resume completed)."""
        extra: dict[str, Any] = {}
        if last_resumed_at is not None:
            extra["last_resumed_at"] = last_resumed_at
        stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.id == record_id,  # type: ignore[arg-type]
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                UserSandbox.status.in_(("pausing", "resuming")),  # type: ignore[attr-defined]
            )
            .values(status="running", **extra)
        )
        result = cast(CursorResult[Any], await self.session.execute(stmt))
        await self.session.commit()
        return bool(result.rowcount == 1)

    async def mark_failed(self, record_id: str) -> None:
        """Mark a sandbox as failed (terminal)."""
        record = await self.get(record_id)
        if record:
            record.status = "failed"
            await self.session.commit()

    async def acquire_in_use(self, record_id: str, lease_seconds: int) -> None:
        """Set ``in_use_until`` to now+lease_seconds, blocking auto-pause."""
        record = await self.get(record_id)
        if record:
            record.in_use_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
            await self.session.commit()

    async def release_in_use(self, record_id: str) -> None:
        """Clear the in-use lease."""
        record = await self.get(record_id)
        if record:
            record.in_use_until = None
            await self.session.commit()

    async def list_expired(self) -> list[UserSandbox]:
        """List sandboxes that have exceeded their TTL since last activity."""
        stmt = (
            self._scoped_select()
            .where(UserSandbox.status == "running")
            .where(text("last_activity_at + ttl_seconds * INTERVAL '1 second' < NOW()"))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def list_expired_system(cls, session: AsyncSession) -> list[UserSandbox]:
        """System-scope query: find expired sandboxes across all workspaces.

        Only for background reapers — never expose to user-facing code.
        """
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.status == "running")  # type: ignore[arg-type]
            .where(text("last_activity_at + ttl_seconds * INTERVAL '1 second' < NOW()"))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def list_idle_to_pause_system(cls, session: AsyncSession) -> list[UserSandbox]:
        """System-scope query: stale-idle, unleased ``running`` rows.

        Used by the pause reaper to pick candidates before claiming each
        atomically via ``claim_pausing``.
        """
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.status == "running")  # type: ignore[arg-type]
            .where(text("last_activity_at + ttl_seconds * INTERVAL '1 second' <= NOW()"))
            .where(text("(in_use_until IS NULL OR in_use_until < NOW())"))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def list_transient_for_reconcile_system(
        cls,
        session: AsyncSession,
        *,
        claim_timeout: int = 60,
    ) -> list[UserSandbox]:
        """System-scope query: ``pausing``/``resuming`` rows due for a provider
        recheck. ``last_provider_check`` NULL or older than ``claim_timeout``
        seconds qualifies; the reconciler will then read ``get_info()`` and
        repair the row.
        """
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.status.in_(("pausing", "resuming")))  # type: ignore[attr-defined]
            .where(
                text(
                    "last_provider_check IS NULL "
                    "OR last_provider_check + :ct * INTERVAL '1 second' <= NOW()"
                )
            )
            .params(ct=claim_timeout)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def touch_provider_check(self, record_id: str) -> None:
        """Stamp ``last_provider_check`` to now after a reconcile-loop probe."""
        stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.id == record_id,  # type: ignore[arg-type]
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
            )
            .values(last_provider_check=datetime.now(UTC))
        )
        await self.session.execute(stmt)
        await self.session.commit()

    @classmethod
    async def list_paused_expired_system(cls, session: AsyncSession) -> list[UserSandbox]:
        """System-scope query: ``paused`` rows past their paused-TTL.

        Used by the reap-paused background loop to terminate stale paused
        sandboxes.
        """
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.status == "paused")  # type: ignore[arg-type]
            .where(UserSandbox.paused_at.is_not(None))  # type: ignore[union-attr]
            .where(text("paused_at + paused_ttl_seconds * INTERVAL '1 second' <= NOW()"))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
