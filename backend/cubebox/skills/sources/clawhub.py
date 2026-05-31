"""ClawhubAdapter — connects skill discovery to the Clawhub public registry.

API (https://clawhub.ai):
  GET /api/v1/search?q=...&limit=...
      {"results": [{"slug", "displayName", "summary", "version", "ownerHandle",
                    "owner": {"handle", "displayName"}, ...}]}

  GET /api/v1/skills/{slug}
      {"skill": {"slug", "displayName", "summary", "tags": {"latest": "x.y.z"},
                 "stats": {"downloads", "installsAllTime", "stars", ...}}, ...}

  GET /api/v1/download?slug={slug}&version={version}
      → application/zip containing SKILL.md, supporting files, and _meta.json
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx

from cubebox.skills.sources.base import (
    SkillCandidate,
    SourceKind,
    TrustTier,
    encode_candidate_id,
)

_BASE_URL = "https://clawhub.ai"
_TIMEOUT = 20.0
_MAX_ZIP_BYTES = 50 * 1024 * 1024  # 50 MB, same cap as validate_skill_files


class ClawhubAdapter:
    """Search and fetch skills from the Clawhub registry."""

    kind: SourceKind = "remote"

    def __init__(
        self,
        *,
        source_id: str,
        trust_tier: TrustTier,
        source_name: str = "Clawhub",
    ) -> None:
        self.source_id = source_id
        self._trust = trust_tier
        self._source_name = source_name

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[httpx.AsyncClient, None]:
        async with httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=_TIMEOUT,
            headers={"Accept": "application/json"},
            follow_redirects=True,
        ) as client:
            yield client

    async def search(self, query: str, *, limit: int) -> list[SkillCandidate]:
        try:
            return await self._search(query, limit=limit)
        except Exception:  # noqa: BLE001 — one bad remote must not kill discovery
            return []

    async def _search(self, query: str, *, limit: int) -> list[SkillCandidate]:
        async with self._client() as client:
            resp = await client.get(
                "/api/v1/search", params={"q": query, "limit": limit}
            )
            if not resp.is_success:
                return []
            data = resp.json()

        results = data.get("results", [])
        if not isinstance(results, list):
            return []

        # Collect slugs whose version is null — resolve them concurrently.
        slugs_needing_version = [
            str(item.get("slug") or "")
            for item in results
            if isinstance(item, dict) and item.get("slug") and not item.get("version")
        ]
        resolved: dict[str, str] = {}
        if slugs_needing_version:
            resolved = await self._resolve_versions(slugs_needing_version)

        out: list[SkillCandidate] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug") or "")
            if not slug:
                continue
            display_name = str(item.get("displayName") or slug)
            summary = str(item.get("summary") or "")
            owner_handle = str(item.get("ownerHandle") or "")
            version = item.get("version") or resolved.get(slug)
            if not version:
                continue  # skip if version still unknown — avoids opaque @latest installs
            source_ref = f"{slug}@{version}"

            out.append(
                SkillCandidate(
                    candidate_id=encode_candidate_id(
                        "remote", source_ref, source_id=self.source_id
                    ),
                    name=display_name,
                    canonical_name=slug,
                    description=summary,
                    source_kind="remote",
                    source_ref=source_ref,
                    version=version,
                    trust=self._trust,
                    install_state="available",
                    source_name=self._source_name,
                    repo=f"https://clawhub.ai/{owner_handle}/{slug}" if owner_handle else None,
                )
            )
        return out

    async def _resolve_versions(self, slugs: list[str]) -> dict[str, str]:
        """Fetch the latest version tag for each slug concurrently."""

        async def _fetch_one(slug: str) -> tuple[str, str | None]:
            try:
                version = await self._resolve_latest_version(slug)
                return slug, version
            except Exception:  # noqa: BLE001
                return slug, None

        pairs = await asyncio.gather(*(_fetch_one(s) for s in slugs))
        return {slug: ver for slug, ver in pairs if ver is not None}

    def trust_for_ref(self, source_ref: str) -> TrustTier:
        return self._trust

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        """Download the skill zip and return {rel_path: bytes}.

        source_ref format: "{slug}@{version}" or "{slug}@latest"
        """
        slug, _, version_tag = source_ref.partition("@")
        if not slug:
            raise ValueError(f"invalid Clawhub source_ref: {source_ref!r}")

        # Resolve "latest" by fetching skill metadata
        if not version_tag or version_tag == "latest":
            version_tag = await self._resolve_latest_version(slug)

        async with self._client() as client:
            resp = await client.get(
                "/api/v1/download",
                params={"slug": slug, "version": version_tag},
                headers={"Accept": "application/zip"},
            )
            resp.raise_for_status()
            zip_bytes = resp.content

        if len(zip_bytes) > _MAX_ZIP_BYTES:
            raise ValueError(
                f"Clawhub zip for {slug}@{version_tag} exceeds cap {_MAX_ZIP_BYTES} bytes"
            )

        return _unpack_zip(zip_bytes)

    async def _resolve_latest_version(self, slug: str) -> str:
        async with self._client() as client:
            resp = await client.get(f"/api/v1/skills/{slug}")
            resp.raise_for_status()
            data = resp.json()
        skill = data.get("skill", {})
        tags = skill.get("tags") or {}
        latest = tags.get("latest")
        if not isinstance(latest, str) or not latest:
            raise ValueError(f"Clawhub skill {slug!r} has no latest version tag")
        return latest


def _unpack_zip(zip_bytes: bytes) -> dict[str, bytes]:
    """Extract a Clawhub skill zip, returning safe rel_path → bytes mappings."""
    files: dict[str, bytes] = {}
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise ValueError(f"Clawhub download is not a valid zip: {e}") from e
    with zf:
        for name in zf.namelist():
            # Skip directories and unsafe paths
            if name.endswith("/"):
                continue
            normalized = name.replace("\\", "/")
            if normalized.startswith("/"):
                continue
            parts = normalized.split("/")
            if any(p in (".", "..") for p in parts):
                continue
            files[name] = zf.read(name)
    return files
