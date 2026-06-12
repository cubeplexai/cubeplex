"""Search subsystem lifespan wiring.

Owns the embedding provider + worker lifecycle and the three-way dim
verification (schema vs config vs provider) that catches drift between the
DDL emitted by the migration, the operator's current config, and the
embedding model the provider actually talks to. If any of those disagree,
HNSW inserts would silently break, so we refuse to start the worker and
log a recovery path.

The subsystem is best-effort: failures here must not stop the rest of the
API from coming up. The search route guards on `app.state.embedding_provider`
being None and returns 503 in that case.
"""

import asyncio
import re

from fastapi import FastAPI
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.config import config
from cubebox.db.engine import async_session_maker
from cubebox.search.embedding import EmbeddingProvider
from cubebox.search.lexical import build_lexical_backend
from cubebox.search.worker import EmbeddingWorker

_VECTOR_TYPE_RE = re.compile(r"vector\((\d+)\)")


async def _read_schema_dim(session: AsyncSession) -> int | None:
    """Read the actual `vector(N)` width from the conversation_chunks table.

    Returns None when the table or column is missing (migration not run yet);
    callers treat that as a hard verification failure.
    """
    result = await session.execute(
        text(
            "SELECT format_type(a.atttypid, a.atttypmod) "
            "FROM pg_attribute a "
            "JOIN pg_class c ON a.attrelid = c.oid "
            "WHERE c.relname = 'conversation_chunks' "
            "AND a.attname = 'embedding' "
            "AND NOT a.attisdropped"
        )
    )
    row = result.first()
    if row is None:
        return None
    type_str = row[0]
    match = _VECTOR_TYPE_RE.search(type_str)
    if match is None:
        return None
    return int(match.group(1))


async def _verify_dim_alignment(provider: EmbeddingProvider) -> bool:
    """Schema ↔ config ↔ provider must all agree on vector dim.

    Returns False on any mismatch (with a CRITICAL log naming each value and
    the recovery steps) or when the schema is missing entirely.
    """
    config_dim = int(config.get("search.embedding.dimensions", 1024))
    provider_dim = provider.dimensions
    async with async_session_maker() as session:
        schema_dim = await _read_schema_dim(session)

    if schema_dim is None:
        logger.critical(
            "conversation_chunks.embedding not found — has alembic upgrade head been run?"
        )
        return False

    if schema_dim == config_dim == provider_dim:
        return True

    logger.critical(
        "vector dim mismatch (schema={}, config={}, provider={}). To change dim:\n"
        "  1) drop the conversation_chunks table\n"
        "  2) set search.embedding.dimensions to the desired value\n"
        "  3) alembic upgrade head\n"
        "  4) backfill via scripts/dev/backfill_search_index.py",
        schema_dim,
        config_dim,
        provider_dim,
    )
    return False


async def start_search_subsystem(app: FastAPI) -> None:
    """Build the embedding provider + worker, gate on three-way dim check.

    Always leaves the following attributes on app.state (None when search is
    disabled or initialization fails partway):
      - embedding_provider
      - embedding_worker
      - embedding_worker_task
      - lexical_backend
    """
    app.state.embedding_provider = None
    app.state.embedding_worker = None
    app.state.embedding_worker_task = None
    app.state.lexical_backend = None

    if not config.get("search.enabled", True):
        logger.info("Search subsystem disabled via config; skipping startup")
        return

    try:
        provider = EmbeddingProvider.from_config()
    except RuntimeError as exc:
        # Missing api key etc. — the rest of the API must still come up.
        logger.error("Embedding provider not started: {}", exc)
        return

    if not await _verify_dim_alignment(provider):
        await provider.aclose()
        return

    app.state.lexical_backend = build_lexical_backend()
    app.state.embedding_provider = provider
    worker = EmbeddingWorker(provider)
    app.state.embedding_worker = worker
    app.state.embedding_worker_task = asyncio.create_task(worker.run(), name="embedding-worker")


async def stop_search_subsystem(app: FastAPI) -> None:
    """Stop the worker, close the provider's connection pool.

    Reads from app.state via getattr because tests may have cleared the
    state references between start and stop.
    """
    worker = getattr(app.state, "embedding_worker", None)
    task: asyncio.Task[None] | None = getattr(app.state, "embedding_worker_task", None)
    if worker is not None and task is not None:
        worker.stop()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except TimeoutError:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    provider = getattr(app.state, "embedding_provider", None)
    if provider is not None:
        await provider.aclose()
