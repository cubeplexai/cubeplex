"""Local catalog as a SkillRegistryAdapter: own-org-visible skills, not yet enabled here."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.repositories.skill import (
    OrgPreinstalledTombstoneRepository,
    OrgSkillInstallRepository,
    SkillRepository,
)
from cubeplex.skills.service import SkillCatalogService
from cubeplex.skills.sources.base import (
    SkillCandidate,
    SourceKind,
    TrustTier,
    encode_candidate_id,
)


class LocalCatalogAdapter:
    kind: SourceKind = "local"

    def __init__(
        self,
        *,
        session: AsyncSession,
        catalog: SkillCatalogService,
        org_id: str,
        workspace_id: str | None,
    ) -> None:
        self._session = session
        self._catalog = catalog
        self._org_id = org_id
        self._workspace_id = workspace_id

    async def search(self, query: str, *, limit: int) -> list[SkillCandidate]:
        del query, limit  # the discovery service ranks/filters/limits the merged set
        visible = await SkillRepository(self._session).list_visible_for_org(self._org_id)
        tombstones = await OrgPreinstalledTombstoneRepository(self._session).list_for_org(
            self._org_id
        )
        tombstoned_ids = {t.skill_id for t in tombstones}

        if self._workspace_id is not None:
            # Workspace mode: install_state reflects this workspace's enablement.
            enabled = await self._catalog.list_enabled_for_workspace(
                self._workspace_id, org_id=self._org_id
            )
            enabled_names = {r.name for r in enabled}
            installed_ids: set[str] = set()
        else:
            # Org mode (admin discover): install_state reflects org-level install.
            installs = await OrgSkillInstallRepository(self._session).list_for_org(self._org_id)
            enabled_names = set()
            installed_ids = {i.skill_id for i in installs}

        out: list[SkillCandidate] = []
        for s in visible:
            if s.id in tombstoned_ids:
                continue
            if self._workspace_id is not None:
                install_state = "enabled" if s.name in enabled_names else "in_catalog"
            else:
                install_state = "in_catalog" if s.id in installed_ids else "available"
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
                    install_state=install_state,  # type: ignore[arg-type]
                    source_name="catalog",
                    repo=None,
                )
            )
        return out

    def trust_for_ref(self, source_ref: str) -> TrustTier:
        return TrustTier.official  # local catalog skills are always trusted

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        del source_ref  # no-op: local files already in our object store
        return {}
