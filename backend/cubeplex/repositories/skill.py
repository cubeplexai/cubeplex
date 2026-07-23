"""Skill catalog repositories."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import (
    OrgPreinstalledTombstone,
    OrgSkillInstall,
    Skill,
    SkillVersion,
    WorkspaceSkillBinding,
)
from cubeplex.repositories.base import ScopedRepository


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
        imported_from_registry_id: str | None = None,
        imported_from_source_ref: str | None = None,
    ) -> Skill:
        skill = Skill(
            name=canonical_name,
            source="uploaded",
            owner_org_id=owner_org_id,
            current_version=current_version,
            description=description,
            keywords=keywords,
            imported_from_registry_id=imported_from_registry_id,
            imported_from_source_ref=imported_from_source_ref,
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
        """Catalog visible to org_id: preinstalled (any) + uploaded (own org). Excludes deprecated."""
        if source == "preinstalled":
            stmt = select(Skill).where(
                Skill.source == "preinstalled",  # type: ignore[arg-type]
                Skill.deprecated_at.is_(None),  # type: ignore[union-attr]
            )
        elif source == "uploaded":
            stmt = select(Skill).where(
                Skill.source == "uploaded",  # type: ignore[arg-type]
                Skill.owner_org_id == org_id,  # type: ignore[arg-type]
                Skill.deprecated_at.is_(None),  # type: ignore[union-attr]
            )
        else:
            stmt = select(Skill).where(
                or_(
                    Skill.source == "preinstalled",  # type: ignore[arg-type]
                    (Skill.source == "uploaded") & (Skill.owner_org_id == org_id),  # type: ignore[arg-type]
                ),
                Skill.deprecated_at.is_(None),  # type: ignore[union-attr]
            )
        stmt = stmt.order_by(Skill.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_preinstalled(self) -> list[Skill]:
        """All preinstalled skills including deprecated; used by seeder for cleanup."""
        result = await self.session.execute(
            select(Skill).where(Skill.source == "preinstalled")  # type: ignore[arg-type]
        )
        return list(result.scalars().all())

    async def deprecate(self, skill_id: str) -> None:
        skill = await self.get(skill_id)
        if skill is None:
            return
        skill.deprecated_at = datetime.now(UTC)
        skill.updated_at = datetime.now(UTC)
        await self.session.commit()

    async def undeprecate(self, skill_id: str) -> None:
        skill = await self.get(skill_id)
        if skill is None:
            return
        skill.deprecated_at = None
        skill.updated_at = datetime.now(UTC)
        await self.session.commit()


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
        content_hash: str,
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
            content_hash=content_hash,
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
                OrgSkillInstall.workspace_id.is_(None),  # type: ignore[union-attr]
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
        auto_bind: bool | None = None,
    ) -> OrgSkillInstall:
        existing = await self.get(org_id, skill_id)
        if existing is not None:
            existing.installed_version = installed_version
            existing.installed_by_user_id = installed_by_user_id
            existing.installed_at = datetime.now(UTC)
            # Only update auto_bind if explicitly provided (preserve user's setting on upgrade)
            if auto_bind is not None:
                existing.auto_bind = auto_bind
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        row = OrgSkillInstall(
            org_id=org_id,
            skill_id=skill_id,
            installed_version=installed_version,
            installed_by_user_id=installed_by_user_id,
            auto_bind=auto_bind if auto_bind is not None else False,
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
            select(OrgSkillInstall).where(
                OrgSkillInstall.org_id == org_id,  # type: ignore[arg-type]
                OrgSkillInstall.workspace_id.is_(None),  # type: ignore[union-attr]
            )
        )
        return list(result.scalars().all())

    async def list_for_workspace_private(
        self, org_id: str, workspace_id: str
    ) -> list[OrgSkillInstall]:
        result = await self.session.execute(
            select(OrgSkillInstall).where(
                OrgSkillInstall.org_id == org_id,  # type: ignore[arg-type]
                OrgSkillInstall.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
        )
        return list(result.scalars().all())

    async def list_org_wide_with_bindings(
        self, org_id: str, workspace_id: str
    ) -> list[tuple[OrgSkillInstall, WorkspaceSkillBinding | None, Skill]]:
        """Return org-wide installs joined with this workspace's binding (if any) and Skill row.

        Single-query alternative to looping `get_by_install` per row — avoids N+1.
        """
        stmt = (
            select(OrgSkillInstall, WorkspaceSkillBinding, Skill)
            .join(Skill, Skill.id == OrgSkillInstall.skill_id)  # type: ignore[arg-type]
            .outerjoin(
                WorkspaceSkillBinding,
                (WorkspaceSkillBinding.org_skill_install_id == OrgSkillInstall.id)  # type: ignore[arg-type]
                & (WorkspaceSkillBinding.workspace_id == workspace_id),
            )
            .where(
                OrgSkillInstall.org_id == org_id,  # type: ignore[arg-type]
                OrgSkillInstall.workspace_id.is_(None),  # type: ignore[union-attr]
            )
            .order_by(Skill.name)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(install, binding, skill) for install, binding, skill in rows]

    async def list_workspace_private_with_skill(
        self, org_id: str, workspace_id: str
    ) -> list[tuple[OrgSkillInstall, Skill]]:
        """Workspace-private installs joined with their Skill row."""
        stmt = (
            select(OrgSkillInstall, Skill)
            .join(Skill, Skill.id == OrgSkillInstall.skill_id)  # type: ignore[arg-type]
            .where(
                OrgSkillInstall.org_id == org_id,  # type: ignore[arg-type]
                OrgSkillInstall.workspace_id == workspace_id,  # type: ignore[arg-type]
            )
            .order_by(Skill.name)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(install, skill) for install, skill in rows]

    async def get_by_id(self, install_id: str) -> OrgSkillInstall | None:
        result = await self.session.execute(
            select(OrgSkillInstall).where(OrgSkillInstall.id == install_id)  # type: ignore[arg-type]
        )
        return result.scalar_one_or_none()

    async def get_workspace_private(
        self, org_id: str, workspace_id: str, skill_id: str
    ) -> OrgSkillInstall | None:
        result = await self.session.execute(
            select(OrgSkillInstall).where(
                OrgSkillInstall.org_id == org_id,  # type: ignore[arg-type]
                OrgSkillInstall.workspace_id == workspace_id,  # type: ignore[arg-type]
                OrgSkillInstall.skill_id == skill_id,  # type: ignore[arg-type]
            )
        )
        return result.scalar_one_or_none()

    async def create_for_workspace(
        self,
        *,
        org_id: str,
        workspace_id: str,
        skill_id: str,
        installed_version: str,
        installed_by_user_id: str,
    ) -> OrgSkillInstall:
        existing = await self.get_workspace_private(org_id, workspace_id, skill_id)
        if existing is not None:
            existing.installed_version = installed_version
            existing.installed_by_user_id = installed_by_user_id
            existing.installed_at = datetime.now(UTC)
            existing.auto_bind = True
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        row = OrgSkillInstall(
            org_id=org_id,
            workspace_id=workspace_id,
            skill_id=skill_id,
            installed_version=installed_version,
            installed_by_user_id=installed_by_user_id,
            auto_bind=True,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def delete_workspace_private(
        self, install_id: str, *, org_id: str, workspace_id: str
    ) -> bool:
        row = await self.get_by_id(install_id)
        if row is None or row.org_id != org_id or row.workspace_id != workspace_id:
            return False
        await self.session.delete(row)
        await self.session.commit()
        return True


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
        if existing is not None:
            existing.enabled = False
            await self.session.commit()
            return True
        row = WorkspaceSkillBinding(
            org_skill_install_id=org_skill_install_id,
            enabled=False,
        )
        await self.add(row)
        return True

    async def list_enabled(self) -> list[WorkspaceSkillBinding]:
        stmt = self._scoped_select().where(WorkspaceSkillBinding.enabled.is_(True))  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self) -> list[WorkspaceSkillBinding]:
        result = await self.session.execute(self._scoped_select())
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
        self, *, org_id: str, skill_id: str, hidden_by_user_id: str | None = None
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
