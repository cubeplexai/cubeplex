"""SkillsShAdapter — connects skill discovery to the skills.sh public registry."""

from __future__ import annotations

import re

import httpx

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

# GitHub owner/repo/branch/skill-slug names: alphanumeric plus .-_
# No percent-sign — rejects all URL-encoded bypass forms (%2e%2e, %2f, etc.)
_GITHUB_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _safe_name(s: str) -> bool:
    """True when s is a valid GitHub path component with no encoding tricks.

    Rejects empty strings, dot-segment traversal ("." / ".."), and any
    character outside [a-zA-Z0-9._-] (which blocks percent-encoded bypasses).
    """
    return bool(s) and s not in {".", ".."} and _GITHUB_NAME_RE.match(s) is not None


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

        # Resolve default branch once per unique owner/repo.
        # Validate source format BEFORE any GitHub API call so malformed
        # values (including percent-encoded traversal) never reach the network.
        repos: dict[str, str] = {}
        async with self._github_client() as gh_client:
            for item in skills:
                source = str(item.get("source") or "")
                if not source or source in repos:
                    continue
                src_parts = source.split("/", 1)
                if len(src_parts) != 2 or not _safe_name(src_parts[0]) or not _safe_name(src_parts[1]):
                    continue  # skip malformed / unsafe source before any network call
                try:
                    repos[source] = await self._resolve_default_branch(
                        gh_client, src_parts[0], src_parts[1]
                    )
                except Exception:  # noqa: BLE001
                    repos[source] = "main"

        out: list[SkillCandidate] = []
        for item in skills:
            if not isinstance(item, dict):
                continue
            # Use skillId for the slug (actual skill name), fallback to id/name for compatibility
            slug = str(item.get("skillId") or item.get("id") or item.get("name") or "")
            source = str(item.get("source") or "")
            # Whitelist both slug and source components against the regex so
            # URL-encoded bypass forms (%2e, %2f) are rejected along with
            # literal traversal sequences.
            src_parts = source.split("/", 1)
            if (
                not _safe_name(slug)
                or len(src_parts) != 2
                or not _safe_name(src_parts[0])
                or not _safe_name(src_parts[1])
            ):
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
        # Whitelist all URL path components — rejects percent-encoded traversal
        if not (_safe_name(owner) and _safe_name(repo) and _safe_name(branch) and _safe_name(slug)):
            raise ValueError(f"unsafe component in skills-sh source_ref: {source_ref!r}")

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
                # Validate each component of the relative path from GitHub's tree.
                # These come from GitHub's API, but we still guard against any
                # unexpected traversal component.
                if not rel or any(not _safe_name(part) for part in rel.split("/")):
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
