"""API key repository."""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.api_key import ApiKey


class ApiKeyRepository:
    """Repository for personal-access API keys.

    Looked up either by hashed_key (auth path, must be fast — covered by the
    unique index on ``hashed_key``) or by user_id (settings page list / quota
    check).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_by_hash(self, hashed_key: str) -> ApiKey | None:
        stmt = select(ApiKey).where(ApiKey.hashed_key == hashed_key)  # type: ignore[arg-type]
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, key_id: str) -> ApiKey | None:
        stmt = select(ApiKey).where(ApiKey.id == key_id)  # type: ignore[arg-type]
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_user(self, user_id: str) -> list[ApiKey]:
        stmt = (
            select(ApiKey)
            .where(ApiKey.user_id == user_id)  # type: ignore[arg-type]
            .order_by(cast(Any, ApiKey.created_at).desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_by_user(self, user_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(ApiKey)
            .where(
                ApiKey.user_id == user_id  # type: ignore[arg-type]
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def add(self, key: ApiKey) -> ApiKey:
        self.session.add(key)
        await self.session.commit()
        await self.session.refresh(key)
        return key

    async def delete(self, key_id: str, user_id: str) -> bool:
        """Delete a key only if it belongs to the given user (ownership guard)."""
        stmt = select(ApiKey).where(
            ApiKey.id == key_id,  # type: ignore[arg-type]
            ApiKey.user_id == user_id,  # type: ignore[arg-type]
        )
        key = (await self.session.execute(stmt)).scalar_one_or_none()
        if key is None:
            return False
        await self.session.delete(key)
        await self.session.commit()
        return True

    async def touch_last_used(self, key_id: str, now: datetime | None = None) -> None:
        """Update ``last_used_at`` without commit/refresh storms.

        Callers should debounce: skip the write if the existing value is
        within the debounce window. This method is the unconditional bump.
        """
        key = await self.get_by_id(key_id)
        if key is None:
            return
        key.last_used_at = now or datetime.now(UTC)
        await self.session.commit()
