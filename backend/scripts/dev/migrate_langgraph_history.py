"""Backfill pre-cutover LangGraph history into ``cubepi_messages``.

Conversations created on or before 2026-05-14 stored their history in the
LangGraph checkpointer (``checkpoints`` / ``checkpoint_blobs``). The cutover to
cubepi's own ``cubepi_messages`` table did not migrate them, so those threads
render as empty in the UI even though ``conversations.has_messages`` is true.

This reads the newest ``messages`` blob per thread (the LangGraph reducer keeps
the full list there), converts each LangChain message to its cubepi equivalent,
and writes ``cubepi_messages`` rows with ``seq`` starting at 1.

Idempotent: a thread that already has ``cubepi_messages`` rows is skipped, so a
re-run only picks up what is still missing.

``run_id`` is left NULL — the legacy format has no run concept. History renders
fine; fork/replay-from-run is not available on migrated threads.

Usage:
    cd backend
    uv run python scripts/dev/migrate_langgraph_history.py --dry-run
    uv run python scripts/dev/migrate_langgraph_history.py
    uv run python scripts/dev/migrate_langgraph_history.py --thread conv-1dgoY0WvqI5lab
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

import msgpack
from cubepi.providers.base import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.db.engine import async_session_maker
from cubeplex.utils.time import utc_isoformat

logger = logging.getLogger("migrate-langgraph")

# LangGraph's JsonPlusSerializer encodes reconstructable objects as msgpack ext
# type 5, whose payload is [module, class_name, kwargs, factory_method].
_LC_EXT_CODE = 5

_ROLE_OF = {
    "user": "user",
    "assistant": "assistant",
    "tool_result": "tool",
}


def _epoch(created_at: Any) -> float | None:
    if not isinstance(created_at, str):
        return None
    try:
        return datetime.fromisoformat(created_at).timestamp()
    except ValueError:
        return None


def _text_blocks(content: Any) -> list[TextContent | ImageContent]:
    """Normalize LangChain content (str or block list) to cubepi content."""
    if isinstance(content, str):
        return [TextContent(text=content)] if content else []
    if not isinstance(content, list):
        return []
    out: list[TextContent | ImageContent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            body = block.get("text") or ""
            if body:
                out.append(TextContent(text=body))
        elif block.get("type") == "image":
            src = block.get("source")
            if isinstance(src, dict):
                out.append(
                    ImageContent(
                        source=src.get("data") or "",
                        media_type=src.get("media_type") or "",
                    )
                )
            elif isinstance(src, str):
                out.append(ImageContent(source=src, media_type=block.get("media_type") or ""))
    return out


def _usage(usage_metadata: Any) -> Usage | None:
    if not isinstance(usage_metadata, dict):
        return None
    details = usage_metadata.get("input_token_details") or {}
    cache_read = int(details.get("cache_read") or 0)
    cache_write = int(details.get("cache_creation") or 0)
    # LangChain reports input_tokens inclusive of cached tokens; cubepi keeps
    # them disjoint (see cubepi providers/openai.py — it subtracts on the way in).
    total_input = int(usage_metadata.get("input_tokens") or 0)
    return Usage(
        input_tokens=max(total_input - cache_read - cache_write, 0),
        output_tokens=int(usage_metadata.get("output_tokens") or 0),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


def _assistant(kwargs: dict[str, Any]) -> AssistantMessage | None:
    meta = kwargs.get("response_metadata") or {}
    extra = kwargs.get("additional_kwargs") or {}
    content: list[Any] = []

    # Reasoning arrives either as native thinking blocks or, on OpenAI-shaped
    # providers, as a flat reasoning_content string. Only one is ever set.
    raw_content = kwargs.get("content")
    if isinstance(raw_content, list):
        for block in raw_content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                thinking = block.get("thinking") or ""
                if thinking:
                    content.append(ThinkingContent(thinking=thinking))
    reasoning = extra.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip():
        content.append(ThinkingContent(thinking=reasoning))

    content.extend(_text_blocks(raw_content))

    # tool_calls is the parsed, provider-agnostic view; the tool_use blocks in
    # content carry unparsed partial_json, so they are deliberately ignored.
    for call in kwargs.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        args = call.get("args")
        content.append(
            ToolCall(
                id=call.get("id") or "",
                name=call.get("name") or "",
                arguments=args if isinstance(args, dict) else {},
            )
        )

    if not content:
        return None

    return AssistantMessage(
        content=content,
        stop_reason=meta.get("finish_reason") or meta.get("stop_reason") or "stop",
        usage=_usage(kwargs.get("usage_metadata")),
        timestamp=_epoch(meta.get("created_at")),
        provider_id=meta.get("model_provider") or "",
        model_id=meta.get("model_name") or "",
        response_id=kwargs.get("id"),
    )


def _convert(cls_name: str, kwargs: dict[str, Any]) -> Message | None:
    """Map one LangChain message to its cubepi equivalent (None = drop)."""
    meta = kwargs.get("response_metadata") or {}
    if cls_name == "HumanMessage":
        content = _text_blocks(kwargs.get("content"))
        if not content:
            return None
        return UserMessage(content=content, timestamp=_epoch(meta.get("created_at")))
    if cls_name == "AIMessage":
        return _assistant(kwargs)
    if cls_name == "ToolMessage":
        return ToolResultMessage(
            tool_call_id=kwargs.get("tool_call_id") or "",
            tool_name=kwargs.get("name") or "",
            content=_text_blocks(kwargs.get("content")),
            details=kwargs.get("artifact"),
            is_error=kwargs.get("status") == "error",
            timestamp=_epoch(meta.get("created_at")),
        )
    # SystemMessage has no cubepi history equivalent — the system prompt is
    # rebuilt per run and never replayed from storage.
    return None


def _decode_blob(blob: bytes) -> list[tuple[str, dict[str, Any]]]:
    """Unpack a LangGraph messages blob to [(class_name, kwargs), ...]."""
    decoded: list[tuple[str, dict[str, Any]]] = []
    for item in msgpack.unpackb(blob, raw=False, strict_map_key=False):
        if not isinstance(item, msgpack.ExtType) or item.code != _LC_EXT_CODE:
            continue
        _module, cls_name, kwargs, _method = msgpack.unpackb(
            item.data, raw=False, strict_map_key=False
        )
        decoded.append((cls_name, kwargs))
    return decoded


async def _pending_threads(session: AsyncSession, only: str | None) -> list[str]:
    sql = (
        "SELECT DISTINCT k.thread_id FROM checkpoints k "
        "LEFT JOIN (SELECT DISTINCT thread_id FROM cubepi_messages) m "
        "  ON m.thread_id = k.thread_id "
        "WHERE m.thread_id IS NULL"
    )
    params: dict[str, Any] = {}
    if only:
        sql += " AND k.thread_id = :tid"
        params["tid"] = only
    sql += " ORDER BY 1"
    return [r[0] for r in (await session.execute(text(sql), params)).all()]


async def _latest_blob(session: AsyncSession, thread_id: str) -> bytes | None:
    row = (
        await session.execute(
            text(
                "SELECT blob FROM checkpoint_blobs WHERE thread_id = :tid "
                "AND channel = 'messages' AND blob IS NOT NULL "
                "ORDER BY version DESC LIMIT 1"
            ),
            {"tid": thread_id},
        )
    ).first()
    return bytes(row[0]) if row else None


async def _write(session: AsyncSession, thread_id: str, messages: list[Message]) -> None:
    await session.execute(
        text(
            "INSERT INTO cubepi_threads (thread_id) VALUES (:tid) "
            "ON CONFLICT (thread_id) DO NOTHING"
        ),
        {"tid": thread_id},
    )
    for seq, msg in enumerate(messages, start=1):
        data = msg.model_dump(mode="json")
        metadata: dict[str, Any] = {}
        if msg.timestamp is not None:
            metadata["created_at"] = utc_isoformat(datetime.fromtimestamp(msg.timestamp, tz=UTC))
        await session.execute(
            text(
                "INSERT INTO cubepi_messages "
                "(thread_id, seq, role, metadata, payload) "
                "VALUES (:tid, :seq, :role, CAST(:meta AS jsonb), :payload)"
            ),
            {
                "tid": thread_id,
                "seq": seq,
                "role": _ROLE_OF[msg.role],
                "meta": json.dumps(metadata),
                "payload": msgpack.packb(data, use_bin_type=True),
            },
        )


async def main(dry_run: bool, only: str | None) -> None:
    async with async_session_maker() as session:
        threads = await _pending_threads(session, only)
    logger.info("threads to migrate: %d", len(threads))

    total_written = 0
    total_dropped = 0
    for thread_id in threads:
        async with async_session_maker() as session:
            blob = await _latest_blob(session, thread_id)
            if blob is None:
                logger.warning("thread=%s has no messages blob; skipping", thread_id)
                continue

            raw = _decode_blob(blob)
            messages = [m for m in (_convert(cls, kw) for cls, kw in raw) if m is not None]
            dropped = len(raw) - len(messages)
            total_dropped += dropped

            if not messages:
                logger.warning("thread=%s decoded to 0 messages; skipping", thread_id)
                continue

            if dry_run:
                logger.info(
                    "[dry-run] thread=%s would write %d messages (dropped %d)",
                    thread_id,
                    len(messages),
                    dropped,
                )
            else:
                await _write(session, thread_id, messages)
                await session.commit()
                logger.info(
                    "thread=%s wrote %d messages (dropped %d)", thread_id, len(messages), dropped
                )
            total_written += len(messages)

    verb = "would write" if dry_run else "wrote"
    logger.info("done — %s %d messages, dropped %d", verb, total_written, total_dropped)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="report without writing")
    p.add_argument("--thread", default=None, help="migrate a single thread_id")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(main(args.dry_run, args.thread))
