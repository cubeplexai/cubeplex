"""Hybrid search: lexical leg + vector leg → RRF → snippet + offsets."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.config import config
from cubebox.models.conversation import Conversation
from cubebox.search.embedding import EmbeddingProvider
from cubebox.search.lexical import build_lexical_backend
from cubebox.search.rrf import rrf_fuse
from cubebox.search.snippet import Snippet, extract_snippet
from cubebox.utils.time import utc_isoformat

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    conversation_id: str
    title: str
    snippet: str
    match_offsets: list[tuple[int, int]]
    matched_message_seq: int | None
    matched_at: str | None
    score: float


@dataclass(frozen=True)
class SearchResponse:
    results: list[SearchResult]
    lexical_count: int
    vector_count: int
    fused_count: int


class ConversationSearchService:
    def __init__(self, session: AsyncSession, provider: EmbeddingProvider) -> None:
        self._session = session
        self._provider = provider
        self._lexical = build_lexical_backend()
        self._k = int(config.get("search.rrf.k", 60))
        self._prefetch = int(config.get("search.rrf.prefetch_per_leg", 20))

    async def search(
        self,
        *,
        org_id: str,
        workspace_id: str,
        creator_user_id: str,
        q: str,
        limit: int,
    ) -> SearchResponse:
        q = q.strip()
        if not q:
            return SearchResponse([], 0, 0, 0)
        lex_hits, vec_hits = await asyncio.gather(
            self._lexical_leg(org_id, workspace_id, creator_user_id, q),
            self._vector_leg(org_id, workspace_id, creator_user_id, q),
            return_exceptions=True,
        )
        lex_list: list[tuple[str, float]] = lex_hits if isinstance(lex_hits, list) else []
        vec_list: list[tuple[str, float]] = vec_hits if isinstance(vec_hits, list) else []
        if isinstance(lex_hits, BaseException):
            logger.warning("Lexical leg failed: %s", lex_hits)
        if isinstance(vec_hits, BaseException):
            logger.warning("Vector leg failed: %s", vec_hits)
        fused = rrf_fuse(
            lexical=[r[0] for r in lex_list],
            vector=[r[0] for r in vec_list],
            k=self._k,
        )
        if not fused:
            return SearchResponse([], len(lex_list), len(vec_list), 0)
        # Hydrate chunks + their conversation; aggregate to conversation.
        chunk_ids = [doc_id for doc_id, _ in fused]
        chunks_by_id = await self._hydrate_chunks(chunk_ids)
        # Group by conversation; keep highest-scoring chunk per conversation.
        seen: dict[str, tuple[float, dict[str, Any]]] = {}
        for doc_id, score in fused:
            ch = chunks_by_id.get(doc_id)
            if ch is None:
                continue
            conv_id = ch["conversation_id"]
            if conv_id in seen and seen[conv_id][0] >= score:
                continue
            seen[conv_id] = (score, ch)
        # Resolve titles + build snippets, truncate to limit.
        ordered = sorted(seen.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
        titles = await self._titles([cid for cid, _ in ordered])
        results: list[SearchResult] = []
        for conv_id, (score, ch) in ordered:
            snip: Snippet = extract_snippet(ch["text"], q=q, window=160)
            # v1: navigate to the chunk's first message. Precise per-match
            # resolution would need per-message text-offset metadata on the
            # chunk; deferred (see spec §9.1 step 6).
            results.append(
                SearchResult(
                    conversation_id=conv_id,
                    title=titles.get(conv_id, ""),
                    snippet=snip.text,
                    match_offsets=list(snip.match_offsets),
                    matched_message_seq=int(ch["seq_lo"]),
                    matched_at=ch.get("created_at_iso"),
                    score=score,
                )
            )
        return SearchResponse(
            results=results,
            lexical_count=len(lex_list),
            vector_count=len(vec_list),
            fused_count=len(fused),
        )

    async def _lexical_leg(
        self, org_id: str, ws_id: str, user_id: str, q: str
    ) -> list[tuple[str, float]]:
        bundle = self._lexical.search_sql(limit=self._prefetch)
        binds = {
            "org_id": org_id,
            "ws_id": ws_id,
            "user_id": user_id,
            "q": self._lexical.normalize_query(q),
        }
        result = await self._session.execute(text(bundle.sql), binds)
        return [(row[0], float(row[1])) for row in result.fetchall()]

    async def _vector_leg(
        self, org_id: str, ws_id: str, user_id: str, q: str
    ) -> list[tuple[str, float]]:
        vectors = await self._provider.embed([q])
        if not vectors:
            return []
        sql = text(
            """
            SELECT id, 1.0 - (embedding <=> :v) AS score
            FROM conversation_chunks
            WHERE org_id = :org_id AND workspace_id = :ws_id AND creator_user_id = :user_id
            ORDER BY embedding <=> :v
            LIMIT :lim
            """
        ).bindparams(bindparam("v", type_=Vector(self._provider.dimensions)))
        binds: dict[str, Any] = {
            "org_id": org_id,
            "ws_id": ws_id,
            "user_id": user_id,
            "v": vectors[0],
            "lim": self._prefetch,
        }
        result = await self._session.execute(sql, binds)
        return [(row[0], float(row[1])) for row in result.fetchall()]

    async def _hydrate_chunks(self, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not chunk_ids:
            return {}
        sql = text(
            """
            SELECT id, conversation_id, seq_lo, seq_hi, text, created_at
            FROM conversation_chunks
            WHERE id = ANY(:ids)
            """
        )
        result = await self._session.execute(sql, {"ids": chunk_ids})
        out: dict[str, dict[str, Any]] = {}
        for r in result.mappings().all():
            row: dict[str, Any] = dict(r)
            # Project rule: DB → frontend datetimes go through utc_isoformat().
            row["created_at_iso"] = utc_isoformat(row["created_at"])
            out[row["id"]] = row
        return out

    async def _titles(self, conversation_ids: list[str]) -> dict[str, str]:
        if not conversation_ids:
            return {}
        stmt = select(Conversation).where(
            Conversation.id.in_(conversation_ids),  # type: ignore[attr-defined]
            Conversation.deleted_at.is_(None),  # type: ignore[union-attr]
        )
        result = await self._session.execute(stmt)
        return {c.id: c.title for c in result.scalars().all()}
