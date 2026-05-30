"""SkillsShAdapter — connects skill discovery to the skills.sh public registry."""

from __future__ import annotations

import httpx
from pathlib import PurePosixPath

from cubebox.skills.sources.base import (
    SkillCandidate,
    SourceKind,
    TrustTier,
    encode_candidate_id,
)

_SKILLS_SH_BASE = "https://skills.sh"
_GITHUB_API_BASE = "https://api.github.com"
_GITHUB_RAW_BASE = "https://raw.githubusercontent.com"

_RAW_FILE_MAX_BYTES = 10 * 1024 * 1024   # 10 MB
_BUNDLE_MAX_BYTES = 50 * 1024 * 1024     # 50 MB


class SkillsShAdapter:
    """Adapter that searches skills.sh and fetches skill files from GitHub."""

    kind: SourceKind = "remote"

    def __init__(
        self,
        *,
        source_id: str,
        trust_tier: TrustTier,
        source_name: str,
        github_token: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.source_id = source_id
        self._trust = trust_tier
        self._source_name = source_name
        self._github_token = github_token
        self._transport = transport

    def _skills_sh_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=_SKILLS_SH_BASE,
            transport=self._transport,
            timeout=15.0,
        )

    def _github_client(self) -> httpx.AsyncClient:
        headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
        if self._github_token:
            headers["Authorization"] = f"Bearer {self._github_token}"
        return httpx.AsyncClient(
            base_url=_GITHUB_API_BASE,
            headers=headers,
            transport=self._transport,
            timeout=15.0,
        )

    def _raw_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=_GITHUB_RAW_BASE,
            transport=self._transport,
            timeout=30.0,
        )

    async def _resolve_default_branch(
        self, client: httpx.AsyncClient, owner: str, repo: str
    ) -> str:
        resp = await client.get(f"/repos/{owner}/{repo}")
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("default_branch") or "main")

    async def search(self, query: str, *, limit: int) -> list[SkillCandidate]:
        try:
            return await self._search(query, limit=limit)
        except Exception:  # noqa: BLE001
            return []

    async def _search(self, query: str, *, limit: int) -> list[SkillCandidate]:
        async with self._skills_sh_client() as sh_client:
            resp = await sh_client.get(
                "/api/search", params={"q": query, "limit": limit}
            )
            if not resp.is_success:
                return []
            data = resp.json()

        skills = data.get("skills", [])
        if not isinstance(skills, list):
            return []

        # Resolve default branch once per unique owner/repo
        repos: dict[str, str] = {}
        async with self._github_client() as gh_client:
            for item in skills:
                source = item.get("source", "")
                if source and source not in repos:
                    try:
                        parts = source.split("/", 1)
                        if len(parts) == 2:
                            repos[source] = await self._resolve_default_branch(
                                gh_client, parts[0], parts[1]
                            )
                    except Exception:  # noqa: BLE001
                        repos[source] = "main"

        out: list[SkillCandidate] = []
        for item in skills:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("id") or item.get("name") or "")
            source = str(item.get("source") or "")
            # source must be "{owner}/{repo}" — exactly one slash, no path traversal
            if not slug or not source or source.count("/") != 1 or ".." in source:
                continue
            branch = repos.get(source, "main")
            source_ref = f"{source}/{branch}/{slug}"
            out.append(
                SkillCandidate(
                    candidate_id=encode_candidate_id(
                        "remote", source_ref, source_id=self.source_id
                    ),
                    name=slug,
                    canonical_name=slug,
                    description=str(item.get("description") or ""),
                    source_kind="remote",
                    source_ref=source_ref,
                    trust=self._trust,
                    install_state="available",
                    install_count=item.get("installs"),
                    source_name=self._source_name,
                    repo=f"https://github.com/{source}",
                )
            )
        return out

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        parts = source_ref.split("/", 3)
        if len(parts) != 4:
            raise ValueError(f"invalid skills-sh source_ref: {source_ref!r}")
        owner, repo, branch, slug = parts
        # Reject slug values that could escape the intended subpath
        if slug.startswith("/") or ".." in PurePosixPath(slug).parts:
            raise ValueError(f"unsafe slug in skills-sh source_ref: {slug!r}")

        async with self._github_client() as gh_client:
            tree_resp = await gh_client.get(
                f"/repos/{owner}/{repo}/git/trees/{branch}",
                params={"recursive": "1"},
            )
            tree_resp.raise_for_status()
            tree_data = tree_resp.json()

        entries = tree_data.get("tree", [])
        prefix = f"{slug}/"
        rel_paths = [
            e["path"][len(prefix):]
            for e in entries
            if isinstance(e, dict)
            and e.get("type") == "blob"
            and str(e.get("path", "")).startswith(prefix)
        ]

        files: dict[str, bytes] = {}
        bundle_total = 0

        async with self._raw_client() as raw_client:
            for rel in rel_paths:
                # Mirror the path-safety check in RemoteRegistryAdapter
                if rel.startswith("/") or ".." in PurePosixPath(rel).parts:
                    raise ValueError(f"unsafe path in skills-sh tree: {rel!r}")
                resp = await raw_client.get(f"/{owner}/{repo}/{branch}/{slug}/{rel}")
                resp.raise_for_status()
                content = resp.content
                if len(content) > _RAW_FILE_MAX_BYTES:
                    raise ValueError(
                        f"file {rel!r} exceeds {_RAW_FILE_MAX_BYTES} byte limit"
                    )
                bundle_total += len(content)
                if bundle_total > _BUNDLE_MAX_BYTES:
                    raise ValueError(
                        f"skill bundle exceeds {_BUNDLE_MAX_BYTES} byte limit"
                    )
                files[rel] = content

        if "SKILL.md" not in files:
            raise ValueError(f"skills-sh skill {slug!r} has no SKILL.md")
        return files

