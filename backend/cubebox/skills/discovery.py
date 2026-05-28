"""Discovery (fan-out + rank) and install services for conversational skills."""

from __future__ import annotations

import re

from cubebox.skills.sources.base import SkillCandidate, TrustTier
from cubebox.skills.sources.registry import SkillSourceRegistry

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
    """Dedupe by normalized display slug (local wins), then sort and truncate."""
    by_slug: dict[str, SkillCandidate] = {}
    for c in candidates:
        key = _dedupe_key(c)
        prev = by_slug.get(key)
        if prev is None or (prev.source_kind != "local" and c.source_kind == "local"):
            by_slug[key] = c
    ordered = sorted(by_slug.values(), key=lambda c: _score(c, query))
    return ordered[:limit]


class SkillDiscoveryService:
    def __init__(self, registry: SkillSourceRegistry) -> None:
        self._registry = registry

    async def discover(self, query: str, *, limit: int = 5) -> list[SkillCandidate]:
        merged: list[SkillCandidate] = []
        for source in self._registry.sources:
            try:
                merged.extend(await source.search(query, limit=limit * 2))
            except Exception:  # noqa: BLE001 — one bad remote must not kill discovery
                continue
        return rank_candidates(merged, query=query, limit=limit)
