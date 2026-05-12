"""Conversation auto-title generation service.

The frontend calls ``POST /conversations/{id}/generate-title`` in parallel
with the first user message. This service:

- Skips when the conversation already has a title (durable first-turn gate
  that is immune to ordering races against ``send_message``'s synchronous
  ``mark_active`` write — once any title exists, auto or manual, further
  calls are no-ops).
- Calls the default LLM with the prompt in ``cubebox.prompts.title``.
- Persists the new title via an atomic SQL compare-and-set so a concurrent
  manual rename is never clobbered.
- Swallows LLM/provider errors and returns the conversation unchanged.
"""

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.encryption import EncryptionBackend
from cubebox.llm.factory import LLMFactory
from cubebox.models import Conversation
from cubebox.prompts.title import TITLE_GENERATION_SYSTEM_PROMPT
from cubebox.repositories import ConversationRepository

logger = logging.getLogger(__name__)

MAX_SNIPPET_CHARS: int = 1000
MAX_TITLE_CHARS: int = 255
LLM_MAX_TOKENS: int = 60


def _clean_title(raw: Any) -> str:
    """Strip whitespace, surrounding quotes, and trailing punctuation."""
    text = str(raw).strip().strip('"').strip("'").strip("。 .,").strip()
    return text[:MAX_TITLE_CHARS]


async def generate_and_apply_title(
    *,
    repo: ConversationRepository,
    session: AsyncSession,
    org_id: str,
    encryption_backend: EncryptionBackend | None,
    conversation: Conversation,
    content: str,
) -> Conversation:
    """Generate and persist an auto-title for ``conversation``.

    Best-effort and idempotent. Returns the (possibly updated) conversation.
    """
    original_title = conversation.title

    # Durable first-turn gate. The frontend already restricts the call to
    # the first turn, but a retry, a direct API call, or a race against
    # ``send_message``'s ``mark_active`` could still arrive late. Gating on
    # the title state — rather than ``has_messages`` — avoids that race
    # while still preventing repeated auto-retitles of an already-named
    # conversation.
    if original_title != "":
        return conversation

    snippet = (content or "").strip()[:MAX_SNIPPET_CHARS]
    if not snippet:
        return conversation

    factory = LLMFactory(
        session=session,
        org_id=org_id,
        encryption_backend=encryption_backend,
    )
    try:
        llm = await factory.create_default(temperature=0, max_tokens=LLM_MAX_TOKENS)
    except Exception:
        logger.warning("Auto-title skipped: no usable LLM provider")
        return conversation

    try:
        result = await llm.ainvoke(
            [
                SystemMessage(content=TITLE_GENERATION_SYSTEM_PROMPT),
                HumanMessage(content=snippet),
            ]
        )
    except Exception:
        logger.warning("Auto-title skipped: LLM call failed", exc_info=True)
        return conversation

    title = _clean_title(result.content)
    if not title:
        return conversation

    updated = await repo.update_title_if_current(conversation.id, title, original_title)
    return updated or conversation
