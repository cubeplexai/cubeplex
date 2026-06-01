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

# Official skills.sh sources from https://www.skills.sh/official
# These are verified makers/organizations that create official skills
_OFFICIAL_SOURCES = frozenset({
    "anthropics/skills",
    "vercel-labs/agent-skills",
})


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

    def _get_trust_for_source(self, source: str) -> TrustTier:
        """Determine trust tier based on whether source is in the official whitelist.

        Official status is only granted to sources in _OFFICIAL_SOURCES — never
        inherited from the registry's configured trust tier. The registry trust
        tier controls community vs untrusted for unknown sources only.
        """
        if source in _OFFICIAL_SOURCES:
            return TrustTier.official
        # Cap unknown sources at community: admin cannot grant official status
        # to arbitrary GitHub repos by setting the registry tier to official.
        if self._trust == TrustTier.official:
            return TrustTier.community
        return self._trust

    def _index_skill_paths(
        self,
        tree_data: dict,
        source: str,
        skill_paths: dict[tuple[str, str], str],
    ) -> None:
        """Index skill directory locations from GitHub tree.

        Scans for SKILL.md blobs and infers the containing directory rather than
        hard-coding path patterns. This handles any repo layout:
          - {slug}/SKILL.md              (top-level, depth 1)
          - skills/{slug}/SKILL.md       (skills/ subdirectory, depth 2)
          - .claude/skills/{slug}/SKILL.md  (Claude canonical layout, depth 3)
          - any other nesting

        When the same slug appears at multiple depths, the shallower path wins
        (simpler structures are more intentional).
        """
        entries = tree_data.get("tree", [])
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") != "blob":
                continue
            path = str(entry.get("path", ""))
            if not path.endswith("/SKILL.md"):
                continue
            skill_dir = path[: -len("/SKILL.md")]
            slug = skill_dir.split("/")[-1]
            if not slug or not all(_safe_name(c) for c in skill_dir.split("/")):
                continue
            existing = skill_paths.get((source, slug))
            if existing is None or skill_dir.count("/") < existing.count("/"):
                skill_paths[(source, slug)] = skill_dir

    def _resolve_skill_path(
        self, source: str, slug: str, skill_paths: dict[tuple[str, str], str]
    ) -> str:
        """Resolve the actual GitHub directory path for a skillId.

        skills.sh sometimes prefixes skillIds with an owner alias
        (e.g. "sleek-design-mobile-apps" for dir "design-mobile-apps").
        If no exact match, progressively strip the leading dash-segment
        until a match is found or the slug is exhausted.
        """
        if (source, slug) in skill_paths:
            return skill_paths[(source, slug)]
        parts = slug.split("-")
        for i in range(1, len(parts)):
            candidate = "-".join(parts[i:])
            if (source, candidate) in skill_paths:
                return skill_paths[(source, candidate)]
        return slug

    def trust_for_ref(self, source_ref: str) -> TrustTier:
        # source_ref = "owner/repo/branch/..."
        parts = source_ref.split("/", 2)
        if len(parts) >= 2:
            return self._get_trust_for_source(f"{parts[0]}/{parts[1]}")
        return TrustTier.untrusted

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

        # Resolve default branch and detect skill paths once per unique owner/repo.
        repos: dict[str, str] = {}
        skill_paths: dict[tuple[str, str], str] = {}  # (source, slug) -> "skills/slug" or "slug"
        async with self._github_client() as gh_client:
            for item in skills:
                source = str(item.get("source") or "")
                if not source or source in repos:
                    continue
                src_parts = source.split("/", 1)
                if len(src_parts) != 2 or not _safe_name(src_parts[0]) or not _safe_name(src_parts[1]):
                    continue
                try:
                    repos[source] = await self._resolve_default_branch(
                        gh_client, src_parts[0], src_parts[1]
                    )
                    # Also fetch the tree to detect skill directory structure
                    tree_resp = await gh_client.get(
                        f"/repos/{src_parts[0]}/{src_parts[1]}/git/trees/{repos[source]}",
                        params={"recursive": "1"},
                    )
                    if tree_resp.is_success:
                        tree_data = tree_resp.json()
                        self._index_skill_paths(tree_data, source, skill_paths)
                except Exception:  # noqa: BLE001
                    repos[source] = "main"

        out: list[SkillCandidate] = []
        for item in skills:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("skillId") or item.get("id") or item.get("name") or "")
            source = str(item.get("source") or "")
            src_parts = source.split("/", 1)
            if (
                not _safe_name(slug)
                or len(src_parts) != 2
                or not _safe_name(src_parts[0])
                or not _safe_name(src_parts[1])
            ):
                continue
            branch = repos.get(source, "main")
            # Detect if skill is in "skills/{slug}/" or "{slug}/" directory.
            # skills.sh may prefix the skillId with an owner alias (e.g.
            # "sleek-design-mobile-apps" for dir "design-mobile-apps"), so if
            # no exact match, try progressively stripping the leading dash-segment.
            skill_rel_path = self._resolve_skill_path(source, slug, skill_paths)
            source_ref = f"{source}/{branch}/{skill_rel_path}"
            # Determine trust tier: official if from official source, else use adapter's tier
            trust = self._get_trust_for_source(source)
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
                    trust=trust,
                    install_state="available",
                    install_count=item.get("installs"),
                    source_name=self._source_name,
                    repo=f"https://github.com/{source}",
                )
            )
        return out

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        # Parse source_ref: owner/repo/branch/{skill_rel_path}
        # skill_rel_path may contain "/" (e.g., "skills/frontend-design")
        parts = source_ref.split("/", 3)
        if len(parts) < 4:
            raise ValueError(f"invalid skills-sh source_ref: {source_ref!r}")
        owner, repo, branch = parts[:3]
        skill_rel_path = parts[3]  # May contain "/" for "skills/slug" pattern
        # Whitelist owner/repo/branch components — skill path validated below
        if not (_safe_name(owner) and _safe_name(repo) and _safe_name(branch)):
            raise ValueError(f"unsafe component in skills-sh source_ref: {source_ref!r}")
        # Validate skill_rel_path: all components must be safe (no traversal)
        if not skill_rel_path or any(
            not _safe_name(c) for c in skill_rel_path.split("/")
        ):
            raise ValueError(
                f"unsafe skill path in skills-sh source_ref: {skill_rel_path!r}"
            )

        async with self._github_client() as gh_client:
            tree_resp = await gh_client.get(
                f"/repos/{owner}/{repo}/git/trees/{branch}",
                params={"recursive": "1"},
            )
            tree_resp.raise_for_status()
            tree_data = tree_resp.json()

        entries = tree_data.get("tree", [])
        prefix = f"{skill_rel_path}/"
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
                if not rel or any(not _safe_name(part) for part in rel.split("/")):
                    raise ValueError(f"unsafe path in skills-sh tree: {rel!r}")
                resp = await raw_client.get(
                    f"/{owner}/{repo}/{branch}/{skill_rel_path}/{rel}"
                )
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

        return files
