"""Skill catalog repositories."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import (
    OrgPreinstalledTombstone,
    OrgSkillInstall,
    Skill,
    SkillVersion,
    WorkspaceSkillBinding,
)
from cubebox.repositories.base import ScopedRepository


class SkillRepository:
    """Global catalog. Not org-scoped (rows can be NULL-org for preinstalled)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, skill_id: str) -> Skill | None:
        return await self.session.get(Skill, skill_id)

    async def find_by_name(self, name: str) -> Skill | None:
        result = await self.session.execute(
            select(Skill).where(Skill.name == name)  # type: ignore[arg-type]
        )
        return result.scalar_one_or_none()

    async def create_preinstalled(
        self,
        *,
        name: str,
        description: str,
        keywords: list[str],
        current_version: str,
    ) -> Skill:
        skill = Skill(
            name=name,
            source="preinstalled",
            owner_org_id=None,
            current_version=current_version,
            description=description,
            keywords=keywords,
        )
        self.session.add(skill)
        await self.session.commit()
        await self.session.refresh(skill)
        return skill

    async def create_uploaded(
        self,
        *,
        canonical_name: str,
        owner_org_id: str,
        description: str,
        keywords: list[str],
        current_version: str,
    ) -> Skill:
        skill = Skill(
            name=canonical_name,
            source="uploaded",
            owner_org_id=owner_org_id,
            current_version=current_version,
            description=description,
            keywords=keywords,
        )
        self.session.add(skill)
        await self.session.commit()
        await self.session.refresh(skill)
        return skill

    async def update_current_version(
        self, skill_id: str, version: str, description: str, keywords: list[str]
    ) -> None:
        skill = await self.get(skill_id)
        if skill is None:
            return
        skill.current_version = version
        skill.description = description
        skill.keywords = keywords
        skill.updated_at = datetime.now(UTC)
        await self.session.commit()

    async def list_visible_for_org(self, org_id: str, *, source: str | None = None) -> list[Skill]:
        """Catalog visible to org_id: preinstalled (any) + uploaded (own org)."""
        from sqlalchemy import or_

        stmt = select(Skill).where(
            or_(
                Skill.source == "preinstalled",  # type: ignore[arg-type]
                (Skill.source == "uploaded") & (Skill.owner_org_id == org_id),  # type: ignore[arg-type]
            )
        )
        if source is not None:
            stmt = stmt.where(Skill.source == source)  # type: ignore[arg-type]
        stmt = stmt.order_by(Skill.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class SkillVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, version_id: str) -> SkillVersion | None:
        return await self.session.get(SkillVersion, version_id)

    async def find(self, skill_id: str, version: str) -> SkillVersion | None:
        result = await self.session.execute(
            select(SkillVersion).where(
                SkillVersion.skill_id == skill_id,  # type: ignore[arg-type]
                SkillVersion.version == version,  # type: ignore[arg-type]
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        skill_id: str,
        version: str,
        description: str,
        keywords: list[str],
        raw_metadata: dict[str, Any],
        storage_prefix: str,
        entry_file: str,
        uploaded_by_user_id: str | None,
    ) -> SkillVersion:
        sv = SkillVersion(
            skill_id=skill_id,
            version=version,
            description=description,
            keywords=keywords,
            raw_metadata=raw_metadata,
            storage_prefix=storage_prefix,
            entry_file=entry_file,
            uploaded_by_user_id=uploaded_by_user_id,
        )
        self.session.add(sv)
        await self.session.commit()
        await self.session.refresh(sv)
        return sv

    async def list_for_skill(self, skill_id: str) -> list[SkillVersion]:
        result = await self.session.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill_id)  # type: ignore[arg-type]
            .order_by(SkillVersion.created_at.desc())  # type: ignore[attr-defined]
        )
        return list(result.scalars().all())


class OrgSkillInstallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, org_id: str, skill_id: str) -> OrgSkillInstall | None:
        result = await self.session.execute(
            select(OrgSkillInstall).where(
                OrgSkillInstall.org_id == org_id,  # type: ignore[arg-type]
                OrgSkillInstall.skill_id == skill_id,  # type: ignore[arg-type]
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        org_id: str,
        skill_id: str,
        installed_version: str,
        installed_by_user_id: str,
    ) -> OrgSkillInstall:
        existing = await self.get(org_id, skill_id)
        if existing is not None:
            existing.installed_version = installed_version
            existing.installed_by_user_id = installed_by_user_id
            existing.installed_at = datetime.now(UTC)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        row = OrgSkillInstall(
            org_id=org_id,
            skill_id=skill_id,
            installed_version=installed_version,
            installed_by_user_id=installed_by_user_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete(self, org_id: str, skill_id: str) -> bool:
        row = await self.get(org_id, skill_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.commit()
        return True

    async def list_for_org(self, org_id: str) -> list[OrgSkillInstall]:
        result = await self.session.execute(
            select(OrgSkillInstall).where(OrgSkillInstall.org_id == org_id)  # type: ignore[arg-type]
        )
        return list(result.scalars().all())


class WorkspaceSkillBindingRepository(ScopedRepository[WorkspaceSkillBinding]):
    model = WorkspaceSkillBinding

    async def get_by_install(self, org_skill_install_id: str) -> WorkspaceSkillBinding | None:
        stmt = self._scoped_select().where(
            WorkspaceSkillBinding.org_skill_install_id == org_skill_install_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def enable(self, org_skill_install_id: str) -> WorkspaceSkillBinding:
        existing = await self.get_by_install(org_skill_install_id)
        if existing is not None:
            existing.enabled = True
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        row = WorkspaceSkillBinding(
            org_skill_install_id=org_skill_install_id,
            enabled=True,
        )
        return await self.add(row)

    async def disable(self, org_skill_install_id: str) -> bool:
        existing = await self.get_by_install(org_skill_install_id)
        if existing is None:
            return False
        await self.session.delete(existing)
        await self.session.commit()
        return True

    async def list_enabled(self) -> list[WorkspaceSkillBinding]:
        stmt = self._scoped_select().where(WorkspaceSkillBinding.enabled.is_(True))  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class OrgPreinstalledTombstoneRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, org_id: str, skill_id: str) -> OrgPreinstalledTombstone | None:
        result = await self.session.execute(
            select(OrgPreinstalledTombstone).where(
                OrgPreinstalledTombstone.org_id == org_id,  # type: ignore[arg-type]
                OrgPreinstalledTombstone.skill_id == skill_id,  # type: ignore[arg-type]
            )
        )
        return result.scalar_one_or_none()

    async def add_tombstone(
        self, *, org_id: str, skill_id: str, hidden_by_user_id: str
    ) -> OrgPreinstalledTombstone:
        row = OrgPreinstalledTombstone(
            org_id=org_id,
            skill_id=skill_id,
            hidden_by_user_id=hidden_by_user_id,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def remove_tombstone(self, org_id: str, skill_id: str) -> bool:
        row = await self.get(org_id, skill_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.commit()
        return True

    async def list_for_org(self, org_id: str) -> list[OrgPreinstalledTombstone]:
        result = await self.session.execute(
            select(OrgPreinstalledTombstone).where(
                OrgPreinstalledTombstone.org_id == org_id  # type: ignore[arg-type]
            )
        )
        return list(result.scalars().all())
