"""Paginated read access to a cubepi conversation's checkpoint history.

cubepi's ``PostgresCheckpointer.load()`` returns the entire history with no
LIMIT — for long conversations the wire payload, msgpack/Pydantic decode,
and JSON re-serialization are the dominant bootstrap cost.

This helper queries ``cubepi_messages`` directly through cubebox's existing
SQLAlchemy pool (no extra asyncpg pool), pages by ``seq``, and skips the
Pydantic round-trip — the stored payload is already
``Message.model_dump(mode="json")`` shape, so we can hand it to the frontend
verbatim after metadata patching and deferred-tool unwrapping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import msgpack
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.agents.stream import unwrap_deferred_in_message_dicts


@dataclass(frozen=True)
class HistoryWindow:
    """Result of a paginated history read.

    ``messages`` is in chronological (oldest-first) order so callers can append
    in render order. ``oldest_seq`` is the ``seq`` of the first message in
    the slice (or ``None`` when empty) — pass it back as ``before_seq`` on
    the next call to fetch the older page. ``has_more`` is true iff at
    least one message exists with ``seq < oldest_seq``.
    """

    messages: list[dict[str, Any]]
    oldest_seq: int | None
    has_more: bool


async def load_history_window(
    session: AsyncSession,
    thread_id: str,
    *,
    before_seq: int | None = None,
    limit: int,
) -> HistoryWindow:
    """Load up to ``limit`` messages ending at ``before_seq`` (exclusive).

    ``before_seq=None`` returns the most recent ``limit`` messages, which is
    what conversation bootstrap wants. A subsequent backscroll request passes
    the previous slice's ``oldest_seq`` as ``before_seq``.
    """
    if limit <= 0:
        return HistoryWindow(messages=[], oldest_seq=None, has_more=False)

    # +1 probe row tells us whether older messages exist without a COUNT(*)
    params: dict[str, Any] = {"tid": thread_id, "limit": limit + 1}
    sql = "SELECT seq, role, metadata, payload FROM cubepi_messages WHERE thread_id = :tid"
    if before_seq is not None:
        sql += " AND seq < :before"
        params["before"] = before_seq
    sql += " ORDER BY seq DESC LIMIT :limit"

    rows = (await session.execute(text(sql), params)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    decoded: list[dict[str, Any]] = []
    for row in reversed(rows):  # DB returned newest-first; flip to chronological
        seq, _role, raw_meta, payload = row
        data = msgpack.unpackb(bytes(payload), raw=False)
        if isinstance(raw_meta, str):
            data["metadata"] = json.loads(raw_meta)
        elif raw_meta is not None:
            data["metadata"] = raw_meta
        else:
            data["metadata"] = {}
        # seq is the stable per-message cursor the frontend uses for the
        # ``#msg-<seq>`` anchor (search deep-links rely on this), and the
        # caller uses ``oldest_seq`` to drive backscroll pagination.
        data["seq"] = int(seq)
        decoded.append(data)

    messages = unwrap_deferred_in_message_dicts(decoded)
    oldest_seq = int(rows[-1][0]) if rows else None
    return HistoryWindow(messages=messages, oldest_seq=oldest_seq, has_more=has_more)


# Walk this many recent assistant messages when looking up todo state. Agents
# typically rewrite the todo list every few turns; the bound caps decode work
# on long conversations and matches the practical depth where todos go stale.
_TODOS_LOOKBACK_ASSISTANTS = 100


async def find_latest_todos(
    session: AsyncSession,
    thread_id: str,
    *,
    limit: int = _TODOS_LOOKBACK_ASSISTANTS,
) -> list[dict[str, Any]] | None:
    """Return the parsed todo list from the most recent ``write_todos`` call.

    The conversation bootstrap returns only a tail of the history, so a
    naive client-side walk over the tail can miss a still-current todo
    list whose ``write_todos`` call lives further back. We scan the
    newest ``limit`` assistant rows directly; ``None`` means no todos
    were ever written (or none within the bound).
    """
    sql = (
        "SELECT payload FROM cubepi_messages "
        "WHERE thread_id = :tid AND role = 'assistant' "
        "ORDER BY seq DESC LIMIT :limit"
    )
    rows = (await session.execute(text(sql), {"tid": thread_id, "limit": limit})).all()
    for (payload,) in rows:
        msg = msgpack.unpackb(bytes(payload), raw=False)
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not (
                isinstance(block, dict)
                and block.get("type") == "tool_call"
                and block.get("name") == "write_todos"
            ):
                continue
            args = block.get("arguments")
            raw_todos = args.get("todos") if isinstance(args, dict) else None
            if not isinstance(raw_todos, list):
                # Malformed call (e.g. mid-stream tool args truncated, or a
                # future schema with a different field name): fall through to
                # an older assistant row rather than clobbering a still-valid
                # todo list with an empty panel.
                continue
            out: list[dict[str, Any]] = []
            for t in raw_todos:
                if not isinstance(t, dict):
                    continue
                description = t.get("content")
                if not isinstance(description, str) or not description.strip():
                    continue
                status = t.get("status")
                if status not in ("in_progress", "completed"):
                    status = "pending"
                out.append({"id": None, "description": description.strip(), "status": status})
            return out
    return None
