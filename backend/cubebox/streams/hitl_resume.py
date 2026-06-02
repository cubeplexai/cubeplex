"""Single-flight resume claim for paused HITL conversations.

See docs/dev/specs/2026-06-02-hitl-checkpointed-respond-design.md §5.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

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


@dataclass(frozen=True)
class TerminalClassification:
    status: str  # "completed" | "paused_hitl"
    clear_pending: bool  # caller should cp.save_pending_request(cid, None)


def classify_terminal_status(
    *,
    final_pending: Any | None,  # HitlRequest or None
    answered_question_id: str | None,  # None on prompt path
    saw_hitl_request_event: bool,
) -> TerminalClassification:
    """Decide the run's terminal status after agent.prompt() / agent.respond()
    returns. See docs/dev/specs/2026-06-02-hitl-checkpointed-respond-design.md §6.

    Truth table:
      final_pending  | answered_qid | saw_event | -> status   | clear_pending
      ---------------|--------------|-----------|-------------|---------------
      None           | any          | any       | completed   | False
      non-null       | any          | False     | completed   | True  (stale leftover)
      same as ans qid| answered     | any       | completed   | True  (respond dangling — T8)
      new qid        | None         | True      | paused_hitl | False (prompt new pause)
      new qid        | answered     | True      | paused_hitl | False (respond follow-up pause)
    """
    if final_pending is None:
        return TerminalClassification(status="completed", clear_pending=False)
    if not saw_hitl_request_event:
        # Pending in DB but this turn never emitted a HitlRequestEvent ->
        # leftover from prior session. Clear and treat as completed.
        return TerminalClassification(status="completed", clear_pending=True)
    if answered_question_id is not None and final_pending.question_id == answered_question_id:
        # Respond path dangling (will only fire in T8's respond path; harmless on prompt
        # path where answered_qid is None).
        return TerminalClassification(status="completed", clear_pending=True)
    # Genuine new pending — the auto-detach hook converted it into a real pause.
    return TerminalClassification(status="paused_hitl", clear_pending=False)
