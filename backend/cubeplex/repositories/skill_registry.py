"""Repository for org-configured skill registries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import SkillRegistry


class SkillRegistryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        org_id: str,
        name: str,
        kind: str,
        base_url: str,
        repo: str | None,
        trust_tier: str,
        created_by_user_id: str,
    ) -> SkillRegistry:
        row = SkillRegistry(
            org_id=org_id,
            name=name,
            kind=kind,
            base_url=base_url,
            repo=repo,
            trust_tier=trust_tier,
            created_by_user_id=created_by_user_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def get(self, org_id: str, registry_id: str) -> SkillRegistry | None:
        row = await self.session.get(SkillRegistry, registry_id)
        if row is None or row.org_id != org_id:
            return None
        return row

    async def list_for_org(self, org_id: str, *, enabled_only: bool = False) -> list[SkillRegistry]:
        stmt = select(SkillRegistry).where(SkillRegistry.org_id == org_id)  # type: ignore[arg-type]
        if enabled_only:
            stmt = stmt.where(SkillRegistry.enabled.is_(True))  # type: ignore[attr-defined]
        stmt = stmt.order_by(SkillRegistry.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_enabled(self, org_id: str, registry_id: str, enabled: bool) -> bool:
        row = await self.get(org_id, registry_id)
        if row is None:
            return False
        row.enabled = enabled
        await self.session.commit()
        return True

    async def set_trust_tier(self, org_id: str, registry_id: str, trust_tier: str) -> bool:
        row = await self.get(org_id, registry_id)
        if row is None:
            return False
        row.trust_tier = trust_tier
        await self.session.commit()
        return True

    async def delete(self, org_id: str, registry_id: str) -> bool:
        row = await self.get(org_id, registry_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.commit()
        return True
