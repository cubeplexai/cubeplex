"""Sanitization and rendering helpers for conversation topic context."""

from __future__ import annotations

import re


def sanitize_topic_title(title: str | None) -> str:
    """Sanitize and escape a topic title for safe inclusion in turn context.

    Enforces:
    1. Whitespace normalization: collapses any sequence of whitespace
       characters (including newlines and tabs) to a single space.
    2. Length constraint: hard truncates to 255 characters (aligning with
       the Topic.title database column).
    3. Strict XML entity escaping: escapes &, <, >, ", and ' so the title
       cannot break out of XML attribute quotes or inject tags.
    """
    if not title:
        return ""
    # Collapse newlines, tabs, and multiple spaces into a single space
    cleaned = re.sub(r"\s+", " ", str(title)).strip()
    # Hard truncate to 255 characters
    cleaned = cleaned[:255]
    # XML entity escaping (ampersand first to avoid double-escaping)
    return (
        cleaned.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def sanitize_topic_id(topic_id: str | None) -> str:
    """Sanitize a topic ID, ensuring it conforms to expected public ID characters."""
    if not topic_id:
        return ""
    return re.sub(r"[^a-zA-Z0-9_\-]", "", str(topic_id))


def render_conversation_context(topic_id: str | None, topic_title: str | None) -> str:
    """Render the self-describing conversation context XML tag.

    Returns an empty string if either topic_id or topic_title is missing.
    """
    safe_id = sanitize_topic_id(topic_id)
    safe_title = sanitize_topic_title(topic_title)
    if not safe_id or not safe_title:
        return ""
    return f'<conversation_context topic_id="{safe_id}" topic_title="{safe_title}" />'
