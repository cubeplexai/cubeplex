"""Convert LangChain message types to the API wire format."""

import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage


def _format_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"


def render_attachments_hint(blocks: list[dict[str, object]]) -> str:
    """Render file_attachment blocks as a [Attachments] text section."""
    if not blocks:
        return ""
    lines = ["", "[Attachments]"]
    for b in blocks:
        kind = b.get("kind")
        filename = b.get("filename", "(unnamed)")
        size_raw = b.get("size_bytes", 0)
        size = int(size_raw) if isinstance(size_raw, (int, float)) else 0
        path = b.get("sandbox_path", "")
        if kind == "image":
            w = b.get("width")
            h = b.get("height")
            lines.append(
                f"- {filename} (image, {w}x{h}, {_format_size(size)})\n"
                f"  path: {path}\n"
                f"  hint: call view_images(paths=[...]) to inspect"
            )
        elif kind == "document":
            lines.append(
                f"- {filename} (document, {_format_size(size)})\n"
                f"  path: {path}\n"
                f"  hint: call file_read(path) to inspect"
            )
        else:
            lines.append(f"- {filename} ({_format_size(size)})\n  path: {path}")
    return "\n".join(lines)


def _attachment_to_api_block(block: dict[str, object]) -> dict[str, object]:
    """Render a stored file_attachment block as a hydrated API DTO.

    URLs are RELATIVE to the conversation route. The frontend resolves them
    via `client.resolvePath` after prefixing with workspace + conversation.
    """
    file_id = str(block.get("file_id", ""))
    base = f"./attachments/{file_id}"
    return {
        "id": file_id,
        "filename": block.get("filename"),
        "kind": block.get("kind"),
        "size_bytes": block.get("size_bytes"),
        "width": block.get("width"),
        "height": block.get("height"),
        "thumbnail_url": f"{base}/thumbnail",
        "download_url": f"{base}/content",
    }


def _unwrap_mcp_content(content: Any) -> str:
    """Extract text from MCP content blocks format.

    MCP tools return content as list[{"type": "text", "text": "..."}].
    This extracts and concatenates the text values.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    texts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(str(block.get("text", "")))
    return "\n".join(texts) if texts else str(content)


def _consolidate_subagent_events(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate raw per-token subagent events into a consolidated summary.

    Returns:
        {"text": str, "tool_calls": list[dict], "tool_results": list[dict], "reasoning": str}
    """
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []

    for evt in events:
        evt_type = evt.get("type")
        data = evt.get("data", {})
        if evt_type == "text_delta":
            text_parts.append(data.get("content", ""))
        elif evt_type == "reasoning":
            reasoning_parts.append(data.get("content", ""))
        elif evt_type == "tool_call":
            tc_entry: dict[str, Any] = {
                "name": data.get("name", ""),
                "arguments": data.get("arguments", {}),
                "tool_call_id": data.get("tool_call_id", ""),
            }
            if data.get("started_at"):
                tc_entry["started_at"] = data["started_at"]
            elif evt.get("timestamp"):
                tc_entry["started_at"] = evt["timestamp"]
            tool_calls.append(tc_entry)
        elif evt_type == "tool_result":
            tr_entry: dict[str, Any] = {
                "tool_name": data.get("tool_name", ""),
                "tool_call_id": data.get("tool_call_id", ""),
                "content": data.get("content", ""),
                "content_type": data.get("content_type"),
                "started_at": data.get("started_at"),
                "completed_at": evt.get("timestamp"),
            }
            tool_results.append(tr_entry)

    return {
        "text": "".join(text_parts),
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "reasoning": "".join(reasoning_parts),
    }


def _get_tool_call_started_at(
    response_metadata: dict[str, Any] | None,
    *,
    index: int,
    tool_call_id: str | None,
) -> str | None:
    timestamps = (response_metadata or {}).get("tool_call_started_at_by_index")
    if isinstance(timestamps, dict):
        raw = timestamps.get(str(index), timestamps.get(index))
        if isinstance(raw, str):
            return raw
    if tool_call_id:
        timestamps_by_id = (response_metadata or {}).get("tool_call_started_at_by_id")
        if isinstance(timestamps_by_id, dict):
            raw = timestamps_by_id.get(tool_call_id)
            if isinstance(raw, str):
                return raw
    return None


def convert_to_api_messages(lc_messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Convert a list of LangChain messages to the API response format.

    LangChain type -> API role mapping:
      HumanMessage  -> "user"
      AIMessage     -> "assistant"
      ToolMessage   -> "tool"
    """
    result: list[dict[str, Any]] = []
    prev_timestamp: str | None = None

    for msg in lc_messages:
        if isinstance(msg, HumanMessage):
            ts = _get_timestamp(msg)
            text_parts: list[str] = []
            attachments: list[dict[str, object]] = []
            raw = msg.content
            if isinstance(raw, list):
                # Legacy list-content shape: [{type:"text",...}, {type:"file_attachment",...}]
                for block in raw:
                    if not isinstance(block, dict):
                        continue
                    t = block.get("type")
                    if t == "text":
                        text_parts.append(str(block.get("text", "")))
                    elif t == "file_attachment":
                        attachments.append(_attachment_to_api_block(block))
            else:
                # New shape: plain string content + attachments_meta in additional_kwargs.
                # AttachmentHintMiddleware injects the [Attachments] hint at model-call
                # time, so the checkpoint stays equal to what the user typed.
                text_parts.append(str(raw))
                meta_blocks = (msg.additional_kwargs or {}).get("attachments_meta") or []
                for block in meta_blocks:
                    if isinstance(block, dict):
                        attachments.append(_attachment_to_api_block(block))
            result.append(
                {
                    "id": getattr(msg, "id", None) or str(uuid.uuid4()),
                    "role": "user",
                    "content": "\n".join(text_parts),
                    "attachments": attachments,
                    "tool_calls": None,
                    "reasoning": None,
                    "name": None,
                    "created_at": ts,
                }
            )
            prev_timestamp = ts

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
                    {
                        "name": tc["name"],
                        "arguments": tc["args"],
                        "tool_call_id": tc.get("id", ""),
                        "started_at": _get_tool_call_started_at(
                            msg.response_metadata,
                            index=i,
                            tool_call_id=tc.get("id"),
                        ),
                    }
                    for i, tc in enumerate(msg.tool_calls)
                ] or None

            reasoning = (msg.additional_kwargs or {}).get("reasoning_content")

            ts = _get_timestamp(msg)

            # Prefer precise reasoning_duration_ms from LLM streaming metadata,
            # fall back to estimating from message timestamp gap.
            reasoning_duration_ms: int | None = None
            if reasoning:
                precise = (msg.response_metadata or {}).get("reasoning_duration_ms")
                if isinstance(precise, int) and precise > 0:
                    reasoning_duration_ms = precise
                elif prev_timestamp:
                    try:
                        prev_dt = datetime.fromisoformat(prev_timestamp)
                        curr_dt = datetime.fromisoformat(ts)
                        delta_ms = int((curr_dt - prev_dt).total_seconds() * 1000)
                        if delta_ms > 0:
                            reasoning_duration_ms = delta_ms
                    except (ValueError, TypeError):
                        pass

            result.append(
                {
                    "id": getattr(msg, "id", None) or str(uuid.uuid4()),
                    "role": "assistant",
                    "content": text_content,
                    "tool_calls": tool_calls,
                    "reasoning": reasoning or None,
                    "reasoning_duration_ms": reasoning_duration_ms,
                    "name": None,
                    "created_at": ts,
                }
            )
            prev_timestamp = ts

        elif isinstance(msg, ToolMessage):
            raw_events = (msg.additional_kwargs or {}).get("subagent_events")
            subagent_events = _consolidate_subagent_events(raw_events) if raw_events else None
            citations = (msg.additional_kwargs or {}).get("citations")
            ts = _get_timestamp(msg)
            # Unwrap MCP content blocks: list[{"type": "text", "text": "..."}] -> text
            # Prefer original_content if CitationMiddleware rewrote the content
            original_content = (msg.additional_kwargs or {}).get("original_content")
            tool_content = (
                original_content if original_content else _unwrap_mcp_content(msg.content)
            )
            result.append(
                {
                    "id": getattr(msg, "id", None) or str(uuid.uuid4()),
                    "role": "tool",
                    "content": tool_content,
                    "tool_calls": None,
                    "reasoning": None,
                    "name": msg.name,
                    "tool_call_id": getattr(msg, "tool_call_id", None),
                    "started_at": (msg.response_metadata or {}).get("tool_started_at"),
                    "subagent_events": subagent_events,
                    "citations": citations,
                    "created_at": ts,
                }
            )
            prev_timestamp = ts

    return result


def convert_to_lc_messages(api_messages: list[dict[str, Any]]) -> list[BaseMessage]:
    """Convert a list of API wire-format messages to LangChain message types.

    API role mapping -> LangChain type:
      "user"      -> HumanMessage  (attachments rendered as [Attachments] hint)
      "assistant" -> AIMessage
      "tool"      -> ToolMessage
    """
    result: list[BaseMessage] = []
    for msg in api_messages:
        role = msg.get("role")
        if role == "user":
            text = msg.get("content", "") or ""
            attachments_meta = msg.get("attachments") or []
            if attachments_meta:
                blocks: list[dict[str, object]] = [
                    {
                        "kind": a.get("kind"),
                        "filename": a.get("filename"),
                        "sandbox_path": a.get("sandbox_path"),
                        "size_bytes": a.get("size_bytes"),
                        "width": a.get("width"),
                        "height": a.get("height"),
                    }
                    for a in attachments_meta
                    if isinstance(a, dict)
                ]
                text = text + render_attachments_hint(blocks)
            result.append(HumanMessage(content=text))
        elif role == "assistant":
            content = msg.get("content") or ""
            result.append(AIMessage(content=content))
        elif role == "tool":
            result.append(
                ToolMessage(
                    content=msg.get("content") or "",
                    tool_call_id=msg.get("tool_call_id") or "",
                    name=msg.get("name") or "",
                )
            )
    return result


def _get_timestamp(msg: BaseMessage) -> str:
    ts = (msg.response_metadata or {}).get("created_at")
    return ts or datetime.now(UTC).isoformat()
