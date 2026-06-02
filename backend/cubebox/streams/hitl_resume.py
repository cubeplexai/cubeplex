"""Single-flight resume claim for paused HITL conversations.

See docs/dev/specs/2026-06-02-hitl-checkpointed-respond-design.md §5.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from redis.asyncio import Redis

from cubebox.streams.run_events import _active_run_key, _run_meta_key


class ClaimResumeOutcome(StrEnum):
    OK = "ok"
    ALREADY_RUNNING = "already_running"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class ClaimResumeResult:
    outcome: ClaimResumeOutcome
    claim_token: str | None


# KEYS[1] = active_key, KEYS[2] = meta_key
# ARGV[1] = expected_run_id, ARGV[2] = new_claim_token,
# ARGV[3] = ttl_seconds, ARGV[4] = last_event_at_iso,
# ARGV[5] = conversation_id, ARGV[6] = started_at_iso
#
# Returns: "ok" | "already_running" | "conflict"
#
# CRITICAL: when meta does not exist (long-pause TTL recovery), we MUST
# rebuild it with all the fields _meta_from_hash requires (run_id,
# conversation_id, status, started_at). Otherwise the next get_active_run
# crashes with KeyError on the half-built hash.
_CLAIM_RESUME_LUA = """
-- KEYS[1] = active_key, KEYS[2] = meta_key
-- ARGV[1] = expected_run_id, ARGV[2] = new_claim_token,
-- ARGV[3] = ttl_seconds, ARGV[4] = last_event_at_iso,
-- ARGV[5] = conversation_id, ARGV[6] = started_at_iso
--
-- Returns: "ok" | "already_running" | "conflict"
--
-- CRITICAL: when meta does not exist (long-pause TTL recovery), we MUST
-- rebuild it with all the fields _meta_from_hash requires (run_id,
-- conversation_id, status, started_at). Otherwise the next get_active_run
-- crashes with KeyError on the half-built hash.
local current = redis.call('GET', KEYS[1])
if current and current ~= ARGV[1] then
  return 'conflict'
end
local meta_exists = redis.call('EXISTS', KEYS[2]) == 1
if meta_exists then
  local status = redis.call('HGET', KEYS[2], 'status')
  if status == 'running' then
    return 'already_running'
  end
  if status ~= 'paused_hitl' and status ~= 'stale' then
    return 'conflict'
  end
  redis.call('HSET', KEYS[2],
    'status', 'running',
    'claim_token', ARGV[2],
    'last_event_at', ARGV[4]
  )
else
  -- Rebuild path: meta TTL aged out. Write ALL required RunMeta fields.
  redis.call('HSET', KEYS[2],
    'run_id', ARGV[1],
    'conversation_id', ARGV[5],
    'status', 'running',
    'started_at', ARGV[6],
    'claim_token', ARGV[2],
    'last_event_at', ARGV[4]
  )
end
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]))
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[3]))
return 'ok'
"""


async def claim_resume(
    redis: Redis,
    *,
    prefix: str,
    conversation_id: str,
    expected_run_id: str,
    started_at: str,
    ttl_seconds: int,
) -> ClaimResumeResult:
    """Atomically claim a paused/stale/missing active-run slot for resume.

    started_at is needed because the rebuild path (long pause beyond
    Redis TTL) has to re-populate the meta hash with all the fields
    _meta_from_hash requires. Callers get started_at from the
    pending_hitl.requested_at payload (which is itself derived from
    the DB pending).
    """
    new_token = uuid.uuid4().hex
    now_iso = datetime.now(UTC).isoformat()
    outcome = await redis.eval(  # type: ignore[misc]
        _CLAIM_RESUME_LUA,
        2,
        _active_run_key(prefix, conversation_id),
        _run_meta_key(prefix, expected_run_id),
        expected_run_id,
        new_token,
        str(ttl_seconds),
        now_iso,
        conversation_id,
        started_at,
    )
    outcome_str = outcome.decode() if isinstance(outcome, bytes) else outcome
    return ClaimResumeResult(
        outcome=ClaimResumeOutcome(outcome_str),
        claim_token=new_token if outcome_str == "ok" else None,
    )
