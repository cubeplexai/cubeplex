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

from cubeplex.skills.service import MAX_FILE_BYTES, MAX_TOTAL_BYTES
from cubeplex.skills.sources.base import (
    SkillCandidate,
    SourceKind,
    TrustTier,
    encode_candidate_id,
)

_BASE_URL = "https://clawhub.ai"
_TIMEOUT = 20.0


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
        # Also fetch stats (downloads) since the search response doesn't include them.
        slugs_needing_detail = [
            str(item.get("slug") or "")
            for item in results
            if isinstance(item, dict) and item.get("slug") and not item.get("version")
        ]
        # resolved: slug → (version, install_count)
        resolved: dict[str, tuple[str, int | None]] = {}
        if slugs_needing_detail:
            resolved = await self._resolve_details(slugs_needing_detail)

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
            detail = resolved.get(slug)
            version = item.get("version") or (detail[0] if detail else None)
            if not version:
                continue  # skip if version still unknown — avoids opaque @latest installs
            install_count = detail[1] if detail else None
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
                    install_count=install_count,
                    source_name=self._source_name,
                    repo=f"https://clawhub.ai/{owner_handle}/{slug}" if owner_handle else None,
                )
            )
        return out

    async def _resolve_details(
        self, slugs: list[str]
    ) -> dict[str, tuple[str, int | None]]:
        """Fetch version + install count for each slug concurrently."""

        async def _fetch_one(slug: str) -> tuple[str, tuple[str, int | None] | None]:
            try:
                version, install_count = await self._fetch_skill_detail(slug)
                return slug, (version, install_count)
            except Exception:  # noqa: BLE001
                return slug, None

        pairs = await asyncio.gather(*(_fetch_one(s) for s in slugs))
        return {slug: detail for slug, detail in pairs if detail is not None}

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
            async with client.stream(
                "GET",
                "/api/v1/download",
                params={"slug": slug, "version": version_tag},
                headers={"Accept": "application/zip"},
            ) as resp:
                resp.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes(65536):
                    total += len(chunk)
                    if total > MAX_TOTAL_BYTES:
                        raise ValueError(
                            f"Clawhub zip for {slug}@{version_tag} exceeds cap "
                            f"{MAX_TOTAL_BYTES} bytes"
                        )
                    chunks.append(chunk)
                zip_bytes = b"".join(chunks)

        return _unpack_zip(zip_bytes)

    async def _fetch_skill_detail(self, slug: str) -> tuple[str, int | None]:
        """Fetch latest version and download count from the skill detail endpoint."""
        async with self._client() as client:
            resp = await client.get(f"/api/v1/skills/{slug}")
            resp.raise_for_status()
            data = resp.json()
        skill = data.get("skill", {})
        tags = skill.get("tags") or {}
        latest = tags.get("latest")
        if not isinstance(latest, str) or not latest:
            raise ValueError(f"Clawhub skill {slug!r} has no latest version tag")
        stats = skill.get("stats") or {}
        install_count = stats.get("downloads")
        if not isinstance(install_count, int):
            install_count = None
        return latest, install_count

    async def _resolve_latest_version(self, slug: str) -> str:
        version, _ = await self._fetch_skill_detail(slug)
        return version


def _unpack_zip(zip_bytes: bytes) -> dict[str, bytes]:
    """Extract a Clawhub skill zip, returning safe rel_path → bytes mappings.

    Applies per-file and cumulative size caps matching validate_skill_files()
    to guard against zip bombs before inflating any entry.
    """
    files: dict[str, bytes] = {}
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise ValueError(f"Clawhub download is not a valid zip: {e}") from e
    total = 0
    with zf:
        for info in zf.infolist():
            name = info.filename
            # Skip directories and unsafe paths
            if name.endswith("/"):
                continue
            normalized = name.replace("\\", "/")
            if normalized.startswith("/"):
                continue
            parts = normalized.split("/")
            if any(p in (".", "..") for p in parts):
                continue
            # Guard against zip bombs: check declared uncompressed size first
            if info.file_size > MAX_FILE_BYTES:
                raise ValueError(
                    f"Clawhub zip entry {name!r} declares {info.file_size} bytes; "
                    f"cap is {MAX_FILE_BYTES}"
                )
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ValueError(
                    f"Clawhub zip bundle exceeds total cap of {MAX_TOTAL_BYTES} bytes"
                )
            files[name] = zf.read(name)
    return files
