"""Local catalog as a SkillSource: own-org-visible skills, not yet enabled here."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.repositories.skill import SkillRepository
from cubebox.skills.service import SkillCatalogService
from cubebox.skills.sources.base import (
    SkillCandidate,
    SourceKind,
    TrustTier,
    encode_candidate_id,
)


class LocalCatalogSource:
    kind: SourceKind = "local"

    def __init__(
        self,
        *,
        session: AsyncSession,
        catalog: SkillCatalogService,
        org_id: str,
        workspace_id: str,
    ) -> None:
        self._session = session
        self._catalog = catalog
        self._org_id = org_id
        self._workspace_id = workspace_id

    async def search(self, query: str, *, limit: int) -> list[SkillCandidate]:
        del query, limit  # the discovery service ranks/filters/limits the merged set
        visible = await SkillRepository(self._session).list_visible_for_org(self._org_id)
        enabled = await self._catalog.list_enabled_for_workspace(
            self._workspace_id, org_id=self._org_id
        )
        enabled_names = {r.name for r in enabled}
        out: list[SkillCandidate] = []
        for s in visible:
            out.append(
                SkillCandidate(
                    candidate_id=encode_candidate_id("local", s.id),
                    name=s.name,
                    canonical_name=s.name,  # local: catalog name IS the canonical name
                    description=s.description,
                    source_kind="local",
                    source_ref=s.id,
                    keywords=list(s.keywords),
                    version=s.current_version,
                    trust=TrustTier.official,  # already in our trust boundary
                    install_state="enabled" if s.name in enabled_names else "in_catalog",
                    source_name="catalog",
                    repo=None,
                )
            )
        return out

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        del source_ref  # no-op: local files already in our object store
        return {}
