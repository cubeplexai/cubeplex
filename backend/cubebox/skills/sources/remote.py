"""Remote GitHub-backed skill registry as a SkillSource.

search() hits the registry directory; fetch() lists the chosen skill's subpath
TREE then downloads every safe file under it (vercel-labs/skills issue #1015:
pulling the bare repo grabs every skill — we pin the subpath so we import
exactly the chosen skill, but ALL of it: references/, scripts/, assets — not
just SKILL.md + a guessed handful). Files are stored, never executed at
install time.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import httpx

from cubebox.skills.sources.base import (
    SkillCandidate,
    SourceKind,
    TrustTier,
    encode_candidate_id,
)

_MAX_TREE_ENTRIES = 200


class RemoteRegistrySource:
    kind: SourceKind = "remote"

    def __init__(
        self,
        *,
        source_id: str,
        base_url: str,
        trust_tier: TrustTier,
        org_slug: str,
        source_name: str = "remote",
        repo: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.source_id = source_id
        self._base_url = base_url.rstrip("/")
        self._trust = trust_tier
        self._org_slug = org_slug
        self._source_name = source_name
        self._repo = repo
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, transport=self._transport, timeout=15.0
        )

    async def search(self, query: str, *, limit: int) -> list[SkillCandidate]:
        async with self._client() as client:
            resp = await client.get("/search", params={"q": query, "limit": limit})
            resp.raise_for_status()
            data = resp.json()
        out: list[SkillCandidate] = []
        for item in data.get("skills", []):
            slug = item["name"]
            out.append(
                SkillCandidate(
                    candidate_id=encode_candidate_id(
                        "remote", item["ref"], source_id=self.source_id
                    ),
                    name=slug,
                    canonical_name=f"{self._org_slug}:{slug}",
                    description=item.get("description", ""),
                    source_kind="remote",
                    source_ref=item["ref"],
                    keywords=list(item.get("keywords", [])),
                    version=item.get("version"),
                    trust=self._trust,
                    install_state="available",
                    stars=item.get("stars"),
                    install_count=item.get("installs"),
                    source_name=self._source_name,
                    repo=item.get("repo") or self._repo,
                )
            )
        return out

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        """Import the WHOLE skill subpath: list the tree, then pull every safe file."""
        files: dict[str, bytes] = {}
        async with self._client() as client:
            tree = await client.get(f"/tree/{source_ref}")
            tree.raise_for_status()
            entries = tree.json().get("files", [])
            if len(entries) > _MAX_TREE_ENTRIES:
                raise ValueError(
                    f"skill tree has {len(entries)} files; cap {_MAX_TREE_ENTRIES}"
                )
            for rel in entries:
                parts = PurePosixPath(rel).parts
                if rel.startswith("/") or ".." in parts:
                    raise ValueError(f"unsafe path in remote skill tree: {rel!r}")
                resp = await client.get(f"/raw/{source_ref}/{rel}")
                resp.raise_for_status()
                files[rel] = resp.content
        if "SKILL.md" not in files:
            raise ValueError("remote skill subpath has no SKILL.md")
        return files
