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


def _require_str(d: dict[str, object], key: str) -> str:
    v = d.get(key)
    if isinstance(v, str):
        return v
    return ""


def _str_or(v: object, default: str | None) -> str | None:
    if isinstance(v, str):
        return v
    return default


def _int_or(v: object, default: int | None) -> int | None:
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    return default


def _str_list(v: object) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if isinstance(x, str)]
    return []


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
            if not isinstance(item, dict):
                continue
            slug = _require_str(item, "name")
            ref = _require_str(item, "ref")
            if not slug or not ref:
                continue
            out.append(
                SkillCandidate(
                    candidate_id=encode_candidate_id(
                        "remote", ref, source_id=self.source_id
                    ),
                    name=slug,
                    canonical_name=f"{self._org_slug}:{slug}",
                    description=_str_or(item.get("description"), ""),  # type: ignore[arg-type]
                    source_kind="remote",
                    source_ref=ref,
                    keywords=_str_list(item.get("keywords")),
                    version=_str_or(item.get("version"), None),
                    trust=self._trust,
                    install_state="available",
                    stars=_int_or(item.get("stars"), None),
                    install_count=_int_or(item.get("installs"), None),
                    source_name=self._source_name,
                    repo=_str_or(item.get("repo"), None) or self._repo,
                )
            )
        return out

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        """Import the WHOLE skill subpath: list the tree, then pull every safe file."""
        files: dict[str, bytes] = {}
        async with self._client() as client:
            tree = await client.get(f"/tree/{source_ref}")
            tree.raise_for_status()
            tree_data = tree.json()
            if not isinstance(tree_data, dict):
                raise ValueError("remote registry returned non-object tree")
            entries = tree_data.get("files", [])
            if not isinstance(entries, list) or len(entries) > _MAX_TREE_ENTRIES:
                raise ValueError(
                    f"skill tree has {len(entries)} files; cap {_MAX_TREE_ENTRIES}"
                )
            for rel in entries:
                if not isinstance(rel, str):
                    continue
                parts = PurePosixPath(rel).parts
                if rel.startswith("/") or ".." in parts:
                    raise ValueError(f"unsafe path in remote skill tree: {rel!r}")
                resp = await client.get(f"/raw/{source_ref}/{rel}")
                resp.raise_for_status()
                files[rel] = resp.content
        if "SKILL.md" not in files:
            raise ValueError("remote skill subpath has no SKILL.md")
        return files
