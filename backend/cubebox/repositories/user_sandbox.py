"""UserSandbox repository."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

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

    async def list_expired(self) -> list[UserSandbox]:
        """List sandboxes that have exceeded their TTL since last activity."""
        stmt = (
            self._scoped_select()
            .where(UserSandbox.status == "running")
            .where(text("TIMESTAMPADD(SECOND, ttl_seconds, last_activity_at) < UTC_TIMESTAMP()"))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
