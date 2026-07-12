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

    Tool-result messages are skipped: the conversation page folds tool
    results into the parent assistant panel rather than rendering them as
    their own anchored row, so a hit pointing at a tool_result seq would
    scroll the deep-link to nothing.
    """
    if isinstance(message, UserMessage):
        text = _flatten_text_parts(message.content)
        return f"[user] {text}" if text else ""
    if isinstance(message, AssistantMessage):
        text = _flatten_text_parts(message.content)
        return f"[assistant] {text}" if text else ""
    if isinstance(message, ToolResultMessage):
        return ""
    return ""


def _flatten_text_parts(parts: object) -> str:
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for p in parts:
        if isinstance(p, TextContent) and p.text:
            out.append(p.text)
    return " ".join(out).strip()
