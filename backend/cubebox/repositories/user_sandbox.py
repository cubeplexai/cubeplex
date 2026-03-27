"""UserSandbox repository."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.user_sandbox import UserSandbox


class UserSandboxRepository:
    """Repository for UserSandbox CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def get_active_by_user(self, user_id: str) -> UserSandbox | None:
        """Get the active (running) sandbox for a user."""
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.user_id == user_id)  # type: ignore[arg-type]
            .where(UserSandbox.status == "running")  # type: ignore[arg-type]
            .order_by(UserSandbox.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_sandbox_id(self, sandbox_id: str) -> UserSandbox | None:
        """Get record by OpenSandbox sandbox ID."""
        stmt = select(UserSandbox).where(
            UserSandbox.sandbox_id == sandbox_id  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_activity(self, record_id: str) -> None:
        """Update last_activity_at timestamp."""
        stmt = select(UserSandbox).where(UserSandbox.id == record_id)  # type: ignore[arg-type]
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
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
        stmt = select(UserSandbox).where(UserSandbox.id == record_id)  # type: ignore[arg-type]
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            record.status = "terminated"
            await self.session.commit()

    async def list_expired(self) -> list[UserSandbox]:
        """List sandboxes that have exceeded their TTL since last activity."""
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.status == "running")  # type: ignore[arg-type]
            .where(text("TIMESTAMPADD(SECOND, ttl_seconds, last_activity_at) < UTC_TIMESTAMP()"))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
