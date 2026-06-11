"""Extract human-readable, search-worthy text from a cubepi message."""

from cubepi.providers.base import (
    AssistantMessage,
    Message,
    TextContent,
    ToolResultMessage,
    UserMessage,
)


def extract_searchable_text(message: Message) -> str:
    """Return a one-line, prefixed representation, or empty string when nothing
    search-worthy is present (tool calls, empty messages, attachments-only).
    """
    if isinstance(message, UserMessage):
        text = _flatten_text_parts(message.content)
        return f"[user] {text}" if text else ""
    if isinstance(message, AssistantMessage):
        text = _flatten_text_parts(message.content)
        return f"[assistant] {text}" if text else ""
    if isinstance(message, ToolResultMessage):
        text = _flatten_text_parts(message.content)
        return f"[tool_result] {text}" if text else ""
    return ""


def _flatten_text_parts(parts: object) -> str:
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for p in parts:
        if isinstance(p, TextContent) and p.text:
            out.append(p.text)
    return " ".join(out).strip()
