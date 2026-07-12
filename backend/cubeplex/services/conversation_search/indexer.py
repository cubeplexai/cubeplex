"""Convenience helpers used by callers that want to (re)index a conversation."""

import logging

from cubeplex.db.engine import async_session_maker
from cubeplex.repositories.embedding_job import EmbeddingJobRepository

logger = logging.getLogger(__name__)


async def enqueue_index_job(
    *,
    org_id: str,
    workspace_id: str,
    creator_user_id: str,
    conversation_id: str,
) -> None:
    """Enqueue a single 'index the whole conversation' job. The worker dedupes
    by always replacing chunks for the conversation, so duplicate enqueues are
    safe and cheap.

    On failure, logs an ERROR with `event=search_index_enqueue_failed`
    and the conversation_id, then re-raises so callers can decide how to
    react. The hook in conversations.py catches and swallows (best-effort);
    other callers (backfill) let it propagate. The structured `event=` key
    lets log-based alerting fire on the first failure rather than the
    user-visible 'search results stop appearing weeks later' symptom.
    """
    try:
        async with async_session_maker() as session:
            repo = EmbeddingJobRepository(
                session,
                org_id=org_id,
                workspace_id=workspace_id,
                user_id=creator_user_id,
            )
            await repo.enqueue(conversation_id=conversation_id)
    except Exception:
        logger.error(
            "event=search_index_enqueue_failed conversation_id=%s workspace_id=%s",
            conversation_id,
            workspace_id,
            exc_info=True,
        )
        raise
