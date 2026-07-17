"""Discovery (fan-out + rank) and install services for conversational skills."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.repositories.skill import (
    OrgPreinstalledTombstoneRepository,
    OrgSkillInstallRepository,
    SkillRepository,
    SkillVersionRepository,
)
from cubeplex.skills.frontmatter import InvalidFrontmatterError
from cubeplex.skills.service import (
    FileTooLargeError,
    InvalidSkillNameError,
    InvalidZipPathError,
    SkillMdMissingError,
    SkillPublishService,
    VersionCollisionError,
    validate_skill_files,
)
from cubeplex.skills.sources.base import (
    CandidateIdError,
    SkillCandidate,
    TrustTier,
    decode_candidate_id,
)
from cubeplex.skills.sources.registry import SkillsAdapterManager

_TRUST_RANK = {TrustTier.official: 0, TrustTier.community: 1, TrustTier.untrusted: 2}


def _dedupe_key(c: SkillCandidate) -> str:
    """Normalized display slug used to collapse the same skill across sources.

    Local canonical_name is a bare slug ("frontend-design"); remote
    canonical_name is "<org>:<slug>" ("acme:frontend-design"). Deduping on
    canonical_name would therefore NEVER match a local skill against its
    remote twin. Key on the slug AFTER stripping any "<org>:" prefix and
    lowercasing, so local and remote of the same skill collide and "local
    wins" can actually fire.
    """
    return c.name.split(":", 1)[-1].strip().lower()


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}


def _score(c: SkillCandidate, query: str) -> tuple[int, int, int, int]:
    q = query.lower().strip()
    name = c.name.lower()
    haystack = (
        f"{name} {c.description.lower()} "
        f"{' '.join(k.lower() for k in c.keywords)}"
    )
    q_tokens = _tokens(query)
    name_tokens = _tokens(c.name)
    hay_tokens = _tokens(haystack)
    if name == q:
        match = 0
    elif q and (name.startswith(q) or q in name):
        match = 1
    elif q_tokens and q_tokens <= name_tokens:
        match = 1
    elif q and q in haystack:
        match = 2
    elif q_tokens and (q_tokens & hay_tokens):
        match = 2
    else:
        match = 3
    return (
        match,
        _TRUST_RANK.get(c.trust, 9),
        -(c.stars or 0),
        -(c.install_count or 0),
    )


def rank_candidates(
    candidates: list[SkillCandidate], *, query: str, limit: int
) -> list[SkillCandidate]:
    """Dedupe by normalized display slug (local wins), then sort and truncate.

    Drop candidates with no query overlap (bucket 3) only if local — remote
    API sources (skills.sh, etc.) have already filtered for relevance, so
    bucket 3 filtering should not apply to them. LocalCatalogAdapter returns
    every visible skill regardless of query, so filtering it prevents
    spurious results like discover?q=<nonsense>.
    """
    by_slug: dict[str, SkillCandidate] = {}
    for c in candidates:
        if c.source_kind == "local" and _score(c, query)[0] >= 3:
            continue  # drop unrelated local skills only
        key = _dedupe_key(c)
        prev = by_slug.get(key)
        if prev is None:
            by_slug[key] = c
        elif prev.source_kind != "local" and c.source_kind == "local":
            # local always beats remote on the same slug
            by_slug[key] = c
        elif prev.source_kind == "remote" and c.source_kind == "remote":
            # two remotes — pick the higher-scoring one by trust/popularity
            if _score(c, query) < _score(prev, query):
                by_slug[key] = c
    ordered = sorted(by_slug.values(), key=lambda c: _score(c, query))
    return ordered[:limit]


class SkillDiscoveryService:
    def __init__(self, registry: SkillsAdapterManager) -> None:
        self._registry = registry

    async def discover(self, query: str, *, limit: int = 5) -> list[SkillCandidate]:
        merged: list[SkillCandidate] = []
        for source in self._registry.adapters:
            try:
                merged.extend(await source.search(query, limit=limit * 2))
            except Exception:  # noqa: BLE001 — one bad remote must not kill discovery
                continue
        return rank_candidates(merged, query=query, limit=limit)


class SkillInstallError(ValueError):
    pass


@dataclass(frozen=True)
class InstallResult:
    canonical_name: str
    skill_id: str
    installed_version: str


class SkillInstallService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        registry: SkillsAdapterManager,
        publisher: SkillPublishService,
        org_id: str,
        org_slug: str,
        workspace_id: str | None,
        actor_user_id: str,
    ) -> None:
        self._session = session
        self._registry = registry
        self._publisher = publisher
        self._org_id = org_id
        self._org_slug = org_slug
        self._workspace_id = workspace_id
        self._actor = actor_user_id

    async def install(self, candidate_id: str) -> InstallResult:
        try:
            kind, source_id, source_ref = decode_candidate_id(candidate_id)
        except CandidateIdError as exc:
            raise SkillInstallError(str(exc)) from exc
        if kind == "local":
            return await self._install_local(source_ref)
        return await self._install_remote(source_id, source_ref)

    async def _install_local(self, skill_id: str) -> InstallResult:
        skills = SkillRepository(self._session)
        skill = await skills.get(skill_id)
        # visible-to-org guard: preinstalled OR own-org uploaded only
        if skill is None or not (
            skill.source == "preinstalled" or skill.owner_org_id == self._org_id
        ):
            raise SkillInstallError("candidate not visible to this org")
        # If an org admin uninstalled this preinstalled skill, a tombstone row
        # was recorded so the seeder won't restore it; honor that admin decision
        # here too, otherwise a workspace member could reinstall via discovery
        # (or a stale candidate_id) and undo the uninstall.
        if skill.source == "preinstalled":
            tombstone = await OrgPreinstalledTombstoneRepository(self._session).get(
                self._org_id, skill.id
            )
            if tombstone is not None:
                raise SkillInstallError(
                    "preinstalled skill was uninstalled for this org"
                )
        if self._workspace_id is not None:
            await OrgSkillInstallRepository(self._session).create_for_workspace(
                org_id=self._org_id,
                workspace_id=self._workspace_id,
                skill_id=skill.id,
                installed_version=skill.current_version,
                installed_by_user_id=self._actor,
            )
        else:
            await OrgSkillInstallRepository(self._session).upsert(
                org_id=self._org_id,
                skill_id=skill.id,
                installed_version=skill.current_version,
                installed_by_user_id=self._actor,
                auto_bind=False,
            )
        return InstallResult(
            canonical_name=skill.name,
            skill_id=skill.id,
            installed_version=skill.current_version,
        )

    async def _install_remote(
        self, source_id: str, source_ref: str
    ) -> InstallResult:
        source = self._registry.adapter_by_id(source_id)
        if source is None:
            raise SkillInstallError("no enabled remote source for this candidate")
        # Enforce trust tier: the skill's effective trust must be at least as
        # high as the registry's configured minimum.  Prevents community/unvetted
        # skills from being installed when the registry is set to official-only.
        skill_trust = source.trust_for_ref(source_ref)
        registry_min = getattr(source, "_trust", TrustTier.untrusted)
        if _TRUST_RANK.get(skill_trust, 9) > _TRUST_RANK.get(registry_min, 9):
            raise SkillInstallError(
                f"skill trust '{skill_trust}' does not meet registry minimum '{registry_min}'"
            )
        try:
            files = await source.fetch(source_ref)
        except httpx.HTTPStatusError as e:
            raise SkillInstallError(
                f"remote source fetch failed: {e.response.status_code}"
            ) from e
        except (httpx.RequestError, ValueError) as e:
            raise SkillInstallError(f"remote source fetch failed: {e}") from e
        if "SKILL.md" not in files:
            raise SkillInstallError("remote candidate has no SKILL.md")
        # remote bundle never went through _extract_zip's checks; enforce here.
        # Wrap all publish-path errors so both the HTTP install route and the
        # chat-fallback parser return controlled errors instead of 500s.
        try:
            validate_skill_files(files)
            sv = await self._publisher._publish_from_files(
                org_id=self._org_id,
                org_slug=self._org_slug,
                actor_user_id=self._actor,
                files=files,
                workspace_id=self._workspace_id,
                imported_from_registry_id=source_id,
                imported_from_source_ref=source_ref,
            )
        except UnicodeDecodeError as e:
            raise SkillInstallError(str(e)) from e
        except (InvalidZipPathError, FileTooLargeError) as e:
            raise SkillInstallError(str(e)) from e
        except VersionCollisionError as e:
            # Remote skill was already imported at this exact version — skip
            # re-publish and just enable that version for this workspace. Bind
            # to the COLLIDING version (the one this candidate carries), not the
            # skill's current_version, which may have since advanced past it.
            canonical = e.canonical_name
            if not canonical:
                from cubeplex.skills.frontmatter import peek_skill_name

                raw_name = peek_skill_name(files["SKILL.md"].decode("utf-8"))
                if raw_name is None:
                    raise SkillInstallError(
                        "cannot resolve canonical name from SKILL.md"
                    ) from None
                canonical = f"{self._org_slug}:{raw_name}"
            existing = await SkillRepository(self._session).find_by_name(canonical)
            if existing is None:
                raise SkillInstallError(
                    f"existing skill lookup failed for {canonical}"
                ) from None
            install_version = e.version or existing.current_version
            # Defend against a candidate whose version no longer exists in the
            # catalog (e.g. it was pruned) — fall back to current_version.
            if await SkillVersionRepository(self._session).find(
                existing.id, install_version
            ) is None:
                install_version = existing.current_version
            if self._workspace_id is not None:
                await OrgSkillInstallRepository(self._session).create_for_workspace(
                    org_id=self._org_id,
                    workspace_id=self._workspace_id,
                    skill_id=existing.id,
                    installed_version=install_version,
                    installed_by_user_id=self._actor,
                )
            else:
                await OrgSkillInstallRepository(self._session).upsert(
                    org_id=self._org_id,
                    skill_id=existing.id,
                    installed_version=install_version,
                    installed_by_user_id=self._actor,
                    auto_bind=False,
                )
            return InstallResult(
                canonical_name=existing.name,
                skill_id=existing.id,
                installed_version=install_version,
            )
        except (
            InvalidFrontmatterError,
            InvalidSkillNameError,
            SkillMdMissingError,
        ) as e:
            raise SkillInstallError(str(e)) from e
        skill = await SkillRepository(self._session).get(sv.skill_id)
        assert skill is not None
        return InstallResult(
            canonical_name=skill.name,
            skill_id=skill.id,
            installed_version=sv.version,
        )
