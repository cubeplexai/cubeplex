"""Assembles the live SkillRegistryAdapter set for an (org, workspace)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.config import config as _config
from cubeplex.repositories.skill_registry import SkillRegistryRepository
from cubeplex.skills.service import SkillCatalogService
from cubeplex.skills.sources.base import SkillRegistryAdapter, TrustTier
from cubeplex.skills.sources.clawhub import ClawhubAdapter
from cubeplex.skills.sources.local import LocalCatalogAdapter
from cubeplex.skills.sources.remote import RemoteRegistryAdapter
from cubeplex.skills.sources.skills_sh import SkillsShAdapter


class SkillsAdapterManager:
    def __init__(self, adapters: list[SkillRegistryAdapter]) -> None:
        self._adapters = adapters

    @property
    def adapters(self) -> list[SkillRegistryAdapter]:
        return self._adapters

    def adapter_by_id(self, source_id: str) -> SkillRegistryAdapter | None:
        """Return the enabled remote adapter with this registry row id, or None."""
        for a in self._adapters:
            if a.kind == "remote" and getattr(a, "source_id", None) == source_id:
                return a
        return None

    @classmethod
    async def build(
        cls,
        *,
        session: AsyncSession,
        catalog: SkillCatalogService,
        org_id: str,
        org_slug: str,
        workspace_id: str | None,
        include_local: bool = True,
    ) -> SkillsAdapterManager:
        adapters: list[SkillRegistryAdapter] = []
        if include_local:
            adapters.append(
                LocalCatalogAdapter(
                    session=session,
                    catalog=catalog,
                    org_id=org_id,
                    workspace_id=workspace_id,
                )
            )
        rows = await SkillRegistryRepository(session).list_for_org(
            org_id, enabled_only=True
        )
        for row in rows:
            if row.kind == "skills-sh":
                adapters.append(
                    SkillsShAdapter(
                        source_id=row.id,
                        trust_tier=TrustTier(row.trust_tier),
                        source_name=row.name,
                        github_token=_config.get(
                            "registry.skills_sh.github_token"
                        ) or None,
                    )
                )
            elif row.kind == "clawhub":
                adapters.append(
                    ClawhubAdapter(
                        source_id=row.id,
                        trust_tier=TrustTier(row.trust_tier),
                        source_name=row.name,
                    )
                )
            else:
                adapters.append(
                    RemoteRegistryAdapter(
                        source_id=row.id,
                        base_url=row.base_url,
                        trust_tier=TrustTier(row.trust_tier),
                        org_slug=org_slug,
                        source_name=row.name,
                        repo=row.repo,
                    )
                )
        return cls(adapters)
