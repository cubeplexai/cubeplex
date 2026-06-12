"""Hybrid search: lexical leg + vector leg → RRF → snippet + offsets."""

import asyncio
import logging
import unicodedata
from dataclasses import dataclass
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.exceptions import InvalidInputError
from cubebox.config import config
from cubebox.db.engine import async_session_maker
from cubebox.models.conversation import Conversation
from cubebox.search.embedding import EmbeddingProvider
from cubebox.search.lexical import build_lexical_backend
from cubebox.search.lexical.base import LexicalSearchBackend
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
    def __init__(
        self,
        session: AsyncSession,
        provider: EmbeddingProvider | None,
        *,
        lexical_backend: LexicalSearchBackend | None = None,
    ) -> None:
        self._session = session
        # provider=None is the lexical-only degraded mode — vector leg is
        # skipped and RRF degenerates to "lexical scores only".
        self._provider = provider
        # In production the lexical backend is built once at lifespan startup
        # and passed in; tests construct the service directly and may rely on
        # the fallback for convenience.
        self._lexical = lexical_backend if lexical_backend is not None else build_lexical_backend()
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
        # Reject control characters (other than common whitespace) — they
        # serve no search purpose and slip through PGroonga / pg_bigm
        # normalization unchanged.
        if any(ord(c) < 32 and c not in "\t\n\r" for c in q):
            raise InvalidInputError("query contains control characters")
        # NFC-normalize so the same visible string indexes the same way the
        # snippet extractor casefolds it later.
        q = unicodedata.normalize("NFC", q)
        # Each leg opens its own session: SQLAlchemy AsyncSession is a
        # single-task unit of work, so running both legs against
        # ``self._session`` concurrently raises MissingGreenlet / transaction
        # errors. Sequential _hydrate_chunks / _titles below keep using
        # ``self._session`` because they run after the gather.
        lex_list, vec_list = await asyncio.gather(
            self._lexical_leg(org_id, workspace_id, creator_user_id, q),
            self._vector_leg(org_id, workspace_id, creator_user_id, q),
        )
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
        # Resolve titles first; _titles only returns live (non-soft-deleted)
        # conversations, so it's the single point where soft-deletion is
        # enforced when emitting results. Filter ordered against titles.keys()
        # so deleted convs never appear as 'Untitled'.
        ordered_all = sorted(seen.items(), key=lambda kv: kv[1][0], reverse=True)
        titles = await self._titles([cid for cid, _ in ordered_all])
        ordered = [(cid, val) for cid, val in ordered_all if cid in titles][:limit]
        results: list[SearchResult] = []
        for conv_id, (score, ch) in ordered:
            snip: Snippet = extract_snippet(ch["text"], q=q, window=160)
            # v1: navigate to the chunk's first message. Precise per-match
            # resolution would need per-message text-offset metadata on the
            # chunk; deferred (see spec §9.1 step 6).
            results.append(
                SearchResult(
                    conversation_id=conv_id,
                    title=titles[conv_id],
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

    # _lexical_leg / _vector_leg / _hydrate_chunks embed (org_id, ws_id,
    # user_id) directly in textual SQL templates rather than going through
    # ConversationChunkRepository._scoped_select. They have to: the templates
    # are typed text() with named binds (single-tenant, single-DB), and the
    # operator they need (vector <=>, pgroonga &@~, bigm_similarity) requires
    # raw SQL anyway. Scope still comes from the route's RequestContext.
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
        try:
            async with async_session_maker() as session:
                result = await session.execute(text(bundle.sql), binds)
                return [(row[0], float(row[1])) for row in result.fetchall()]
        except Exception:
            # Degrade to vector-only rather than fail the whole search.
            logger.warning("Lexical leg failed", exc_info=True)
            return []

    async def _vector_leg(
        self, org_id: str, ws_id: str, user_id: str, q: str
    ) -> list[tuple[str, float]]:
        # Lexical-only mode: nothing to compare against.
        if self._provider is None:
            return []
        try:
            vectors = await self._provider.embed([q])
            if not vectors:
                return []
            # Filter by embed_model so an operator rotation of
            # search.embedding.model / base_url (same dimension) doesn't
            # mix the new query vector with stale chunks in an
            # incompatible embedding space. The worker re-indexes on
            # rotation; until then, the vector leg silently returns
            # nothing and search degrades to lexical-only.
            # `embedding IS NOT NULL` is defensive — lexical-only chunks
            # written when no provider is configured carry NULL embeddings
            # and would otherwise break the cosine operator.
            sql = text(
                """
                SELECT cc.id, 1.0 - (cc.embedding <=> :v) AS score
                FROM conversation_chunks cc
                JOIN conversations c ON c.id = cc.conversation_id AND c.deleted_at IS NULL
                WHERE cc.org_id = :org_id
                  AND cc.workspace_id = :ws_id
                  AND cc.creator_user_id = :user_id
                  AND cc.embed_model = :embed_model
                  AND cc.embedding IS NOT NULL
                ORDER BY cc.embedding <=> :v
                LIMIT :lim
                """
            ).bindparams(bindparam("v", type_=Vector(self._provider.dimensions)))
            binds: dict[str, Any] = {
                "org_id": org_id,
                "ws_id": ws_id,
                "user_id": user_id,
                "v": vectors[0],
                "embed_model": self._provider.model_id,
                "lim": self._prefetch,
            }
            async with async_session_maker() as session:
                result = await session.execute(sql, binds)
                return [(row[0], float(row[1])) for row in result.fetchall()]
        except Exception:
            # Degrade to lexical-only rather than fail the whole search.
            logger.warning("Vector leg failed", exc_info=True)
            return []

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
