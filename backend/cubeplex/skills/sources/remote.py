"""Remote GitHub-backed skill registry as a SkillRegistryAdapter.

search() hits the registry directory; fetch() lists the chosen skill's subpath
TREE then downloads every safe file under it (vercel-labs/skills issue #1015:
pulling the bare repo grabs every skill — we pin the subpath so we import
exactly the chosen skill, but ALL of it: references/, scripts/, assets — not
just SKILL.md + a guessed handful). Files are stored, never executed at
install time.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath

import httpx

from cubeplex.skills.sources.base import (
    SkillCandidate,
    SourceKind,
    TrustTier,
    encode_candidate_id,
)

_MAX_TREE_ENTRIES = 200
_TREE_MAX_BYTES = 1 * 1024 * 1024  # 1 MB is plenty for a 200-entry path manifest
_SEARCH_MAX_BYTES = 2 * 1024 * 1024  # 2 MB covers description + keywords for many hits
_RAW_FILE_MAX_BYTES = 10 * 1024 * 1024  # same cap as validate_skill_files
_BUNDLE_MAX_BYTES = 50 * 1024 * 1024  # mirrors MAX_TOTAL_BYTES in skills.service


async def _stream_capped(
    client: httpx.AsyncClient, url: str, *, cap: int, what: str
) -> bytes:
    """GET ``url`` streaming, bailing with ValueError once cap bytes are seen."""
    chunks: list[bytes] = []
    total = 0
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        async for chunk in resp.aiter_bytes(65536):
            chunks.append(chunk)
            total += len(chunk)
            if total > cap:
                raise ValueError(f"{what} exceeds cap {cap} bytes")
    return b"".join(chunks)


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


class RemoteRegistryAdapter:
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
            # Stream + cap so a malicious/broken registry can't exhaust worker
            # memory just from the discovery path.
            url = str(httpx.URL("/search", params={"q": query, "limit": limit}))
            body = await _stream_capped(
                client, url, cap=_SEARCH_MAX_BYTES, what="remote search response"
            )
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                raise ValueError(f"remote search response is not valid JSON: {exc}") from exc
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

    def trust_for_ref(self, source_ref: str) -> TrustTier:
        return self._trust

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        """Import the WHOLE skill subpath: list the tree, then pull every safe file.

        All exceptions raised by httpx (including ``httpx.InvalidURL`` for
        malformed source_refs/URL paths) are converted to ``ValueError`` so
        callers can catch one type and map to a controlled HTTP response.
        """
        try:
            return await self._fetch(source_ref)
        except Exception as exc:
            if isinstance(exc, ValueError):
                raise
            raise ValueError(str(exc)) from exc

    async def _fetch(self, source_ref: str) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        bundle_total = 0
        async with self._client() as client:
            # Stream the tree manifest with a byte cap so a malicious registry
            # can't exhaust worker memory before we even reach the entry-count
            # guard. 1 MB easily fits 200 path strings.
            tree_body = await _stream_capped(
                client,
                f"/tree/{source_ref}",
                cap=_TREE_MAX_BYTES,
                what="remote tree manifest",
            )
            try:
                tree_data = json.loads(tree_body)
            except json.JSONDecodeError as exc:
                raise ValueError(f"remote tree manifest is not valid JSON: {exc}") from exc
            if not isinstance(tree_data, dict):
                raise ValueError("remote registry returned non-object tree")
            entries = tree_data.get("files", [])
            entry_count = len(entries) if isinstance(entries, list) else 0
            if not isinstance(entries, list) or entry_count > _MAX_TREE_ENTRIES:
                raise ValueError(
                    f"skill tree has {entry_count} entries; cap {_MAX_TREE_ENTRIES}"
                )
            for rel in entries:
                if not isinstance(rel, str):
                    continue
                parts = PurePosixPath(rel).parts
                if rel.startswith("/") or ".." in parts:
                    raise ValueError(f"unsafe path in remote skill tree: {rel!r}")
                # Stream the raw response so a malicious/broken registry can't
                # exhaust worker memory — accumulate chunks and stop at both
                # the per-file cap and the cumulative bundle cap (same limits
                # validate_skill_files enforces later).
                chunks: list[bytes] = []
                file_total = 0
                async with client.stream("GET", f"/raw/{source_ref}/{rel}") as raw:
                    raw.raise_for_status()
                    async for chunk in raw.aiter_bytes(65536):
                        chunks.append(chunk)
                        file_total += len(chunk)
                        if file_total > _RAW_FILE_MAX_BYTES:
                            raise ValueError(
                                f"remote file {rel!r} exceeds cap "
                                f"{_RAW_FILE_MAX_BYTES} bytes"
                            )
                        if bundle_total + file_total > _BUNDLE_MAX_BYTES:
                            raise ValueError(
                                f"remote skill bundle exceeds cap "
                                f"{_BUNDLE_MAX_BYTES} bytes"
                            )
                files[rel] = b"".join(chunks)
                bundle_total += file_total
        return files
