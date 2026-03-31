"""Convert LangChain message types to the API wire format."""

import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage


def convert_to_api_messages(lc_messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Convert a list of LangChain messages to the API response format.

    LangChain type -> API role mapping:
      HumanMessage  -> "user"
      AIMessage     -> "assistant"
      ToolMessage   -> "tool"
    """
    result: list[dict[str, Any]] = []

    for msg in lc_messages:
        if isinstance(msg, HumanMessage):
            result.append(
                {
                    "id": getattr(msg, "id", None) or str(uuid.uuid4()),
                    "role": "user",
                    "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                    "tool_calls": None,
                    "reasoning": None,
                    "name": None,
                    "created_at": _get_timestamp(msg),
                }
            )

        elif isinstance(msg, AIMessage):
            raw_content = msg.content
            text_content: str | None
            if isinstance(raw_content, list):
                text_parts = [
                    block["text"]
                    for block in raw_content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                text_content = "".join(text_parts) or None
            else:
                text_content = raw_content or None

            tool_calls = None
            if msg.tool_calls:
                tool_calls = [
                    {"name": tc["name"], "arguments": tc["args"]} for tc in msg.tool_calls
                ] or None

            reasoning = (msg.additional_kwargs or {}).get("reasoning_content")

            result.append(
                {
                    "id": getattr(msg, "id", None) or str(uuid.uuid4()),
                    "role": "assistant",
                    "content": text_content,
                    "tool_calls": tool_calls,
                    "reasoning": reasoning or None,
                    "name": None,
                    "created_at": _get_timestamp(msg),
                }
            )

        elif isinstance(msg, ToolMessage):
            result.append(
                {
                    "id": getattr(msg, "id", None) or str(uuid.uuid4()),
                    "role": "tool",
                    "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                    "tool_calls": None,
                    "reasoning": None,
                    "name": msg.name,
                    "created_at": _get_timestamp(msg),
                }
            )

    return result


def _get_timestamp(msg: BaseMessage) -> str:
    ts = (msg.response_metadata or {}).get("created_at")
    return ts or datetime.now(UTC).isoformat()
