"""Assembles the live SkillSource set for an (org, workspace)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.repositories.skill_registry import SkillRegistryRepository
from cubebox.skills.service import SkillCatalogService
from cubebox.skills.sources.base import SkillSource, TrustTier
from cubebox.skills.sources.local import LocalCatalogSource
from cubebox.skills.sources.remote import RemoteRegistrySource


class SkillSourceRegistry:
    def __init__(self, sources: list[SkillSource]) -> None:
        self._sources = sources

    @property
    def sources(self) -> list[SkillSource]:
        return self._sources

    def remote_source_by_id(self, source_id: str) -> SkillSource | None:
        """Return the enabled remote source with this row id, or None.

        Preview/install decode the candidate_id's source_id and look the exact
        source up here — never "first remote", which would fetch from the wrong
        registry when an org has multiple remote sources (or none, if the
        source was disabled/deleted between discover and install → caller maps
        to 404).
        """
        for s in self._sources:
            if s.kind == "remote" and getattr(s, "source_id", None) == source_id:
                return s
        return None

    @classmethod
    async def build(
        cls,
        *,
        session: AsyncSession,
        catalog: SkillCatalogService,
        org_id: str,
        org_slug: str,
        workspace_id: str,
    ) -> SkillSourceRegistry:
        sources: list[SkillSource] = [
            LocalCatalogSource(
                session=session,
                catalog=catalog,
                org_id=org_id,
                workspace_id=workspace_id,
            )
        ]
        rows = await SkillRegistryRepository(session).list_for_org(
            org_id, enabled_only=True
        )
        for row in rows:
            sources.append(
                RemoteRegistrySource(
                    source_id=row.id,
                    base_url=row.base_url,
                    trust_tier=TrustTier(row.trust_tier),
                    org_slug=org_slug,
                    source_name=row.name,
                    repo=row.repo,
                )
            )
        return cls(sources)
