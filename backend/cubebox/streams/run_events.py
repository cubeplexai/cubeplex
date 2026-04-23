"""Redis-backed active run metadata and ordered run event log primitives."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, cast

from redis.asyncio import Redis


@dataclass(slots=True)
class RunMeta:
    """Metadata for a single run."""

    run_id: str
    conversation_id: str
    status: str
    started_at: str
    user_message: str | None = None
    first_event_id: str | None = None
    last_event_id: str | None = None


@dataclass(slots=True)
class RunEvent:
    """A single persisted run event."""

    event_id: str
    payload: dict[str, Any]


def _active_run_key(prefix: str, conversation_id: str) -> str:
    return f"{prefix}:conversation_active_run:{conversation_id}"


def _run_meta_key(prefix: str, run_id: str) -> str:
    return f"{prefix}:run_meta:{run_id}"


def _run_events_key(prefix: str, run_id: str) -> str:
    return f"{prefix}:run_events:{run_id}"


async def create_run(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    conversation_id: str,
    status: str,
    started_at: str,
    user_message: str | None = None,
) -> RunMeta:
    """Create metadata for a new run and register it as active for the conversation."""
    meta = RunMeta(
        run_id=run_id,
        conversation_id=conversation_id,
        status=status,
        started_at=started_at,
        user_message=user_message,
    )
    payload = json.dumps(asdict(meta))
    pipe = redis.pipeline()
    pipe.set(_run_meta_key(prefix, run_id), payload)
    pipe.set(_active_run_key(prefix, conversation_id), run_id)
    await pipe.execute()
    return meta


async def get_run_meta(redis: Redis, *, prefix: str, run_id: str) -> RunMeta | None:
    """Return metadata for a run."""
    raw = await redis.get(_run_meta_key(prefix, run_id))
    if raw is None:
        return None
    data = json.loads(raw)
    return RunMeta(**data)


async def get_active_run(redis: Redis, *, prefix: str, conversation_id: str) -> RunMeta | None:
    """Return the currently active run for a conversation, if any."""
    run_id = await redis.get(_active_run_key(prefix, conversation_id))
    if run_id is None:
        return None
    return await get_run_meta(redis, prefix=prefix, run_id=run_id)


async def update_run_meta(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    status: str | None = None,
    first_event_id: str | None = None,
    last_event_id: str | None = None,
) -> RunMeta | None:
    """Patch run metadata fields."""
    meta = await get_run_meta(redis, prefix=prefix, run_id=run_id)
    if meta is None:
        return None
    if status is not None:
        meta.status = status
    if first_event_id is not None:
        meta.first_event_id = first_event_id
    if last_event_id is not None:
        meta.last_event_id = last_event_id
    await redis.set(_run_meta_key(prefix, run_id), json.dumps(asdict(meta)))
    return meta


async def clear_active_run(
    redis: Redis,
    *,
    prefix: str,
    conversation_id: str,
    run_id: str,
) -> None:
    """Clear the active-run pointer if it still points to the given run."""
    active_key = _active_run_key(prefix, conversation_id)
    current_run_id = await redis.get(active_key)
    if current_run_id == run_id:
        await redis.delete(active_key)


async def append_run_event(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    payload: dict[str, Any],
) -> str:
    """Append an event payload to the run stream and update event bounds."""
    stream_key = _run_events_key(prefix, run_id)
    event_id = cast(str, await redis.xadd(stream_key, {"payload": json.dumps(payload)}))
    meta = await get_run_meta(redis, prefix=prefix, run_id=run_id)
    if meta is not None:
        await update_run_meta(
            redis,
            prefix=prefix,
            run_id=run_id,
            first_event_id=meta.first_event_id or event_id,
            last_event_id=event_id,
        )
    return event_id


def _decode_stream_entries(entries: list[tuple[str, dict[str, str]]]) -> list[RunEvent]:
    events: list[RunEvent] = []
    for event_id, raw_fields in entries:
        payload = json.loads(raw_fields["payload"])
        events.append(RunEvent(event_id=event_id, payload=payload))
    return events


async def get_first_event_id(redis: Redis, *, prefix: str, run_id: str) -> str | None:
    """Return the first event id for the run."""
    entries = await redis.xrange(_run_events_key(prefix, run_id), count=1)
    if not entries:
        return None
    return cast(str, entries[0][0])


async def get_latest_event_id(redis: Redis, *, prefix: str, run_id: str) -> str | None:
    """Return the latest event id for the run."""
    entries = await redis.xrevrange(_run_events_key(prefix, run_id), count=1)
    if not entries:
        return None
    return cast(str, entries[0][0])


async def iter_run_events(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    start: str | None = None,
    stop: str | None = None,
) -> list[RunEvent]:
    """Return run events between start and stop, inclusive of stop and exclusive of start when requested."""
    stream_key = _run_events_key(prefix, run_id)
    min_id = start or "-"
    max_id = stop or "+"
    entries = await redis.xrange(stream_key, min=min_id, max=max_id)
    return _decode_stream_entries(entries)


async def read_run_events_after(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    last_event_id: str,
    block_ms: int,
    count: int | None = None,
) -> list[RunEvent]:
    """Block waiting for new run events after the given event id."""
    streams = await redis.xread(
        {_run_events_key(prefix, run_id): last_event_id},
        block=block_ms,
        count=count,
    )
    if not streams:
        return []
    _stream_name, entries = streams[0]
    return _decode_stream_entries(entries)


async def expire_run_data(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    ttl_seconds: int,
) -> None:
    """Expire run metadata and event log."""
    pipe = redis.pipeline()
    pipe.expire(_run_meta_key(prefix, run_id), ttl_seconds)
    pipe.expire(_run_events_key(prefix, run_id), ttl_seconds)
    await pipe.execute()
