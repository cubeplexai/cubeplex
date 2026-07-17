"""OrgSettings repository."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.org_settings import OrgSettings


class OrgSettingsRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self, key: str) -> OrgSettings | None:
        stmt = select(OrgSettings).where(
            OrgSettings.org_id == self.org_id,  # type: ignore[arg-type]
            OrgSettings.key == key,  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def set(self, key: str, value: dict[str, Any]) -> OrgSettings:
        existing = await self.get(key)
        if existing:
            existing.value = value
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        setting = OrgSettings(org_id=self.org_id, key=key, value=value)
        self.session.add(setting)
        await self.session.commit()
        await self.session.refresh(setting)
        return setting
