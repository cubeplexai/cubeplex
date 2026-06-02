"""Redis-backed active run metadata and ordered run event log primitives.

Data model:
- ``{prefix}:conversation_active_run:{conv_id}`` — string, value=run_id, TTL.
- ``{prefix}:run_meta:v2:{run_id}`` — Hash of run metadata fields, TTL.
- ``{prefix}:run_events:v2:{run_id}`` — Stream of JSON-encoded payloads,
  approximate-trimmed, TTL.

All keys are birth-TTL'd on creation so a crashed process doesn't leave
immortal keys behind. ``expire_run_data`` shortens the TTL at run end.

Active-run claim and event append are implemented as single Lua scripts
to avoid TOCTOU races between SETNX + metadata writes / between XADD +
meta field updates.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, cast

from redis.asyncio import Redis

# Known values for ``RunMeta.status``. Kept here so callers and reviewers can
# see the full state space in one place.
#
# - ``running``: a worker holds the active-run lock and is appending events.
# - ``paused_hitl``: the agent emitted a HITL pending request and the worker
#   auto-detached. The active-run lock is still held (so the conversation is
#   not "free") but there is no live worker; the run resumes when the user
#   answers. The stale-run sweeper must skip this status (no freshness
#   expectation) and ``_FORCE_CLAIM_STALE_LUA`` must refuse to overwrite it.
# - ``completed`` / ``cancelled`` / ``errored``: terminal states a worker
#   transitions into on its way out.
# - ``stale``: set by inline stale-run detection when a worker disappeared
#   mid-run without transitioning the status itself.
RUN_STATUSES = ("running", "paused_hitl", "completed", "cancelled", "errored", "stale")


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
    last_event_at: str | None = None


@dataclass(slots=True)
class RunEvent:
    """A single persisted run event."""

    event_id: str
    payload: dict[str, Any]


def _active_run_key(prefix: str, conversation_id: str) -> str:
    return f"{prefix}:conversation_active_run:{conversation_id}"


def _run_meta_key(prefix: str, run_id: str) -> str:
    return f"{prefix}:run_meta:v2:{run_id}"


def _run_events_key(prefix: str, run_id: str) -> str:
    return f"{prefix}:run_events:v2:{run_id}"


# ---------------------------------------------------------------------------
# Lua scripts. Kept as module-level strings; redis-py caches them via
# register_script() the first time they run. Passed as KEYS where possible
# so they remain cluster-safe if we ever move to Redis Cluster.
# ---------------------------------------------------------------------------

# Claim the active-run slot and write the initial meta hash atomically.
# KEYS[1] = active_key, KEYS[2] = meta_key
# ARGV[1] = run_id, ARGV[2] = ttl_seconds, ARGV[3..N] = meta field/value pairs
_CLAIM_ACTIVE_LUA = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]))
local fields = {}
for i = 3, #ARGV do
  fields[#fields + 1] = ARGV[i]
end
redis.call('HSET', KEYS[2], unpack(fields))
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[2]))
return 1
"""

# Force-claim the active-run slot only when the current active points at a
# stale run whose meta is gone or is no longer running. Used only as a
# recovery step when _CLAIM_ACTIVE_LUA returns 0.
#
# ``paused_hitl`` is treated the same as ``running`` here: a paused
# conversation is alive-but-detached (worker released, pending request
# stored), so silently force-claiming over it would lose the pending
# request and orphan the user's previous turn.
#
# KEYS[1] = active_key, KEYS[2] = new_meta_key, KEYS[3] = existing_meta_key
# ARGV[1] = expected_existing_run_id, ARGV[2] = new_run_id,
# ARGV[3] = ttl_seconds, ARGV[4..N] = meta field/value pairs
_FORCE_CLAIM_STALE_LUA = """
local current = redis.call('GET', KEYS[1])
if current ~= ARGV[1] then
  return 0
end
if redis.call('EXISTS', KEYS[3]) == 1 then
  local status = redis.call('HGET', KEYS[3], 'status')
  if status == 'running' or status == 'paused_hitl' then
    return 0
  end
end
redis.call('SET', KEYS[1], ARGV[2], 'EX', tonumber(ARGV[3]))
local fields = {}
for i = 4, #ARGV do
  fields[#fields + 1] = ARGV[i]
end
redis.call('HSET', KEYS[2], unpack(fields))
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]))
return 1
"""

# CAS-DEL: only delete the active-run key if it still points at run_id.
# KEYS[1] = active_key, ARGV[1] = expected run_id
_CLEAR_ACTIVE_IF_MATCHES_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

# XADD + meta update + TTL refresh (including active-run lock) in one round trip.
# KEYS[1] = stream_key, KEYS[2] = meta_key, KEYS[3] = active_key
# ARGV[1] = payload_json, ARGV[2] = ttl_seconds, ARGV[3] = maxlen,
# ARGV[4] = run_id, ARGV[5] = last_event_at (ISO-8601 wall-clock heartbeat)
# The active-run TTL is refreshed only if it still points at this run_id, so a
# late-arriving append from an already-superseded run can't keep a zombie lock
# alive.
_APPEND_EVENT_LUA = """
local eid = redis.call(
  'XADD', KEYS[1], 'MAXLEN', '~', tonumber(ARGV[3]), '*', 'payload', ARGV[1]
)
redis.call('HSET', KEYS[2], 'last_event_id', eid, 'last_event_at', ARGV[5])
redis.call('HSETNX', KEYS[2], 'first_event_id', eid)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[2]))
if redis.call('GET', KEYS[3]) == ARGV[4] then
  redis.call('EXPIRE', KEYS[3], tonumber(ARGV[2]))
end
return eid
"""

# Conditional status transition: only HSET status if current status is 'running'.
# Workers transition (running → completed|failed|cancelled) only when nobody else
# has flipped the run's status in the meantime — e.g. inline stale-run detection
# may have set status='stale' and DELed the active key while the worker was mid-
# tool-call. Without this guard, the worker's success path silently overwrites
# the stale flag, hiding the orphan from the operator.
# KEYS[1] = meta_key, ARGV[1] = new_status
# Returns 1 if status was 'running' (and now ARGV[1]); 0 otherwise.
_TRANSITION_STATUS_FROM_RUNNING_LUA = """
if redis.call('HGET', KEYS[1], 'status') ~= 'running' then
  return 0
end
redis.call('HSET', KEYS[1], 'status', ARGV[1])
return 1
"""

# Mark a run as stale and clear the active-run lock if it still points at it.
# KEYS[1] = meta_key, KEYS[2] = active_key
# ARGV[1] = expected_run_id
_MARK_STALE_LUA = """
if redis.call('HGET', KEYS[1], 'status') == 'running' then
  redis.call('HSET', KEYS[1], 'status', 'stale')
end
if redis.call('GET', KEYS[2]) == ARGV[1] then
  redis.call('DEL', KEYS[2])
end
return 1
"""


def _meta_hash_pairs(meta: RunMeta) -> list[str]:
    """Return a flat list of field/value pairs for HSET, skipping None values."""
    out: list[str] = []
    for field, value in asdict(meta).items():
        if value is None:
            continue
        out.extend([field, str(value)])
    return out


def _meta_from_hash(raw: dict[str, str]) -> RunMeta | None:
    if not raw:
        return None
    return RunMeta(
        run_id=raw["run_id"],
        conversation_id=raw["conversation_id"],
        status=raw["status"],
        started_at=raw["started_at"],
        user_message=raw.get("user_message"),
        first_event_id=raw.get("first_event_id"),
        last_event_id=raw.get("last_event_id"),
        last_event_at=raw.get("last_event_at"),
    )


async def create_run(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    conversation_id: str,
    status: str,
    started_at: str,
    user_message: str | None = None,
    ttl_seconds: int,
) -> RunMeta | None:
    """Atomically claim the active-run slot for a conversation.

    Returns the new RunMeta on success, or None if another run is active
    and has a still-running meta.
    """
    meta = RunMeta(
        run_id=run_id,
        conversation_id=conversation_id,
        status=status,
        started_at=started_at,
        user_message=user_message,
    )
    pairs = _meta_hash_pairs(meta)

    active_key = _active_run_key(prefix, conversation_id)
    meta_key = _run_meta_key(prefix, run_id)

    acquired = bool(
        await redis.eval(  # type: ignore[misc]
            _CLAIM_ACTIVE_LUA,
            2,
            active_key,
            meta_key,
            run_id,
            str(ttl_seconds),
            *pairs,
        )
    )
    if acquired:
        return meta

    existing_run_id = await redis.get(active_key)
    if existing_run_id is None:
        # Active key disappeared between the two calls — retry once.
        retry_acquired = bool(
            await redis.eval(  # type: ignore[misc]
                _CLAIM_ACTIVE_LUA,
                2,
                active_key,
                meta_key,
                run_id,
                str(ttl_seconds),
                *pairs,
            )
        )
        return meta if retry_acquired else None

    existing_meta_key = _run_meta_key(prefix, existing_run_id)
    forced = bool(
        await redis.eval(  # type: ignore[misc]
            _FORCE_CLAIM_STALE_LUA,
            3,
            active_key,
            meta_key,
            existing_meta_key,
            existing_run_id,
            run_id,
            str(ttl_seconds),
            *pairs,
        )
    )
    return meta if forced else None


async def get_run_meta(redis: Redis, *, prefix: str, run_id: str) -> RunMeta | None:
    """Return metadata for a run."""
    raw = await redis.hgetall(_run_meta_key(prefix, run_id))  # type: ignore[misc]
    return _meta_from_hash(raw)


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
    ttl_seconds: int | None = None,
) -> RunMeta | None:
    """Patch run metadata fields without read-modify-write races.

    ``status`` updates use CAS: the write only happens when the current status
    is ``running``. If a worker tries to flip the status to a terminal state
    after stale detection has already marked the run ``stale``, the CAS fails
    and the existing ``stale`` value is preserved. Other fields are written
    unconditionally because they are append-driven and don't conflict.
    """
    meta_key = _run_meta_key(prefix, run_id)
    if status is not None:
        wrote = await redis.eval(  # type: ignore[misc]
            _TRANSITION_STATUS_FROM_RUNNING_LUA,
            1,
            meta_key,
            status,
        )
        if not wrote:
            from loguru import logger

            logger.warning(
                "Run {} status transition to {} ignored — meta is no longer 'running' "
                "(likely supplanted by stale detection)",
                run_id,
                status,
            )
    other_updates: dict[str, str] = {}
    if first_event_id is not None:
        other_updates["first_event_id"] = first_event_id
    if last_event_id is not None:
        other_updates["last_event_id"] = last_event_id
    if other_updates:
        await redis.hset(meta_key, mapping=other_updates)  # type: ignore[misc]
    if ttl_seconds is not None:
        await redis.expire(meta_key, ttl_seconds)
    return await get_run_meta(redis, prefix=prefix, run_id=run_id)


async def clear_active_run(
    redis: Redis,
    *,
    prefix: str,
    conversation_id: str,
    run_id: str,
) -> None:
    """Clear the active-run pointer iff it still points to the given run."""
    await redis.eval(  # type: ignore[misc]
        _CLEAR_ACTIVE_IF_MATCHES_LUA,
        1,
        _active_run_key(prefix, conversation_id),
        run_id,
    )


async def append_run_event(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    conversation_id: str,
    payload: dict[str, Any],
    ttl_seconds: int,
    maxlen: int,
) -> str:
    """Append an event payload and update event bounds in a single call.

    Stamps a wall-clock ``last_event_at`` heartbeat so stale-run detection
    can spot dead workers. Also heartbeats the active-run lock for this
    conversation so runs longer than ``ttl_seconds`` don't drop the lock
    mid-execution.
    """
    last_event_at = datetime.now(UTC).isoformat()
    return cast(
        str,
        await redis.eval(  # type: ignore[misc]
            _APPEND_EVENT_LUA,
            3,
            _run_events_key(prefix, run_id),
            _run_meta_key(prefix, run_id),
            _active_run_key(prefix, conversation_id),
            json.dumps(payload),
            str(ttl_seconds),
            str(maxlen),
            run_id,
            last_event_at,
        ),
    )


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


async def iter_run_events_chunked(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    start: str | None = None,
    stop: str | None = None,
    count: int = 1000,
) -> AsyncIterator[list[RunEvent]]:
    """Yield decoded run events in ``[start, stop]`` in batches of ~``count``.

    Paginates with ``XRANGE`` using a ``(<id>`` exclusive cursor between pages,
    so each ``await`` hands control back to the event loop and memory stays
    bounded to one batch. ``start`` may be None (from the beginning), a bare
    id, or an already-exclusive ``(<id>`` form. ``stop`` is inclusive.
    """
    stream_key = _run_events_key(prefix, run_id)
    min_id = start if start is not None else "-"
    max_id = stop if stop is not None else "+"
    while True:
        entries = await redis.xrange(stream_key, min=min_id, max=max_id, count=count)
        if not entries:
            return
        yield _decode_stream_entries(entries)
        if len(entries) < count:
            return
        min_id = f"({entries[-1][0]}"


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


async def mark_run_stale(
    redis: Redis,
    *,
    prefix: str,
    run_id: str,
    conversation_id: str,
) -> None:
    """Atomically mark a run stale and release its active-run lock if held.

    Idempotent: a no-op when status is already non-running and the active
    key no longer points at this run.
    """
    await redis.eval(  # type: ignore[misc]
        _MARK_STALE_LUA,
        2,
        _run_meta_key(prefix, run_id),
        _active_run_key(prefix, conversation_id),
        run_id,
    )


def is_stale_meta(
    meta: RunMeta,
    *,
    threshold_seconds: int,
    now: datetime | None = None,
) -> bool:
    """A run is stale when status='running' and last_event_at is too old.

    A fresh run that hasn't appended its first event yet has
    ``last_event_at = None``; in that case fall back to ``started_at`` so
    the staleness window starts at run creation.
    """
    if meta.status != "running":
        return False
    current = now or datetime.now(UTC)
    reference = meta.last_event_at or meta.started_at
    last = datetime.fromisoformat(reference)
    return (current - last).total_seconds() > threshold_seconds
