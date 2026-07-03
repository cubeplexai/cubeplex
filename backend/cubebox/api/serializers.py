"""Shared route-layer serializers.

Single source of truth for conversation / topic / participant dict shapes
returned by API routes. Avoids duplication between
``conversations.py`` and ``ws_topics.py``.
"""

from __future__ import annotations

from typing import Any

from cubebox.models import Conversation
from cubebox.utils.time import utc_isoformat

DEFAULT_REASONING: dict[str, str] = {
    "mode": "off",
    "effort": "medium",
    "summary": "none",
}


def serialize_conversation(c: Conversation) -> dict[str, Any]:
    """Serialize a conversation row for the API.

    Exposed fields are the union of what both ``conversations.py`` and
    ``ws_topics.py`` previously serialized.
    """
    return {
        "id": c.id,
        "title": c.title,
        "topic_id": c.topic_id,
        "is_pinned": c.is_pinned,
        "is_group_chat": c.is_group_chat,
        "created_at": utc_isoformat(c.created_at),
        "updated_at": utc_isoformat(c.updated_at),
        "model_key": c.model_key,
        "reasoning": c.reasoning or DEFAULT_REASONING,
    }
