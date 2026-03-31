"""Shared utilities for middleware implementations."""

from langchain_core.messages import SystemMessage


def append_to_system_message(
    system_message: SystemMessage | None,
    text: str,
) -> SystemMessage:
    """Append text to a system message, creating one if needed."""
    if system_message is None:
        return SystemMessage(content=text)

    existing = system_message.content
    if isinstance(existing, str):
        return SystemMessage(content=f"{existing}\n\n{text}" if existing else text)

    # Content is a list of blocks
    new_content = list(existing) if isinstance(existing, list) else [{"type": "text", "text": existing}]
    new_content.append({"type": "text", "text": f"\n\n{text}"})
    return SystemMessage(content=new_content)
