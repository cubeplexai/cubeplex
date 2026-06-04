"""Per-conversation background memory consolidation (Layer 2).

A cheap Redis gate (per-conversation run counter + last-consolidated timestamp +
lock) decides when to run a single OneShotLLM pass that distills the
conversation's recent history into the user's personal memory.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Awaitable
from typing import Any, cast

from loguru import logger
from redis.asyncio import Redis

_TTL_S = 7 * 24 * 3600  # keep gate keys ~a week of inactivity


def _k(prefix: str, kind: str, conversation_id: str) -> str:
    return f"{prefix}:memcons:{kind}:{conversation_id}"


async def _counter(redis: Redis, prefix: str, conversation_id: str) -> int:
    raw = await redis.get(_k(prefix, "runs", conversation_id))
    return int(raw) if raw else 0


async def get_last(redis: Redis, prefix: str, conversation_id: str) -> float:
    raw = await redis.get(_k(prefix, "last", conversation_id))
    return float(raw) if raw else 0.0


async def note_run(redis: Redis, prefix: str, conversation_id: str) -> None:
    """Count one finished run for this conversation."""
    key = _k(prefix, "runs", conversation_id)
    await redis.incr(key)
    await redis.expire(key, _TTL_S)


async def should_consolidate(
    redis: Redis,
    prefix: str,
    conversation_id: str,
    *,
    min_hours: float,
    min_runs: int,
) -> bool:
    counter = await _counter(redis, prefix, conversation_id)
    if counter < min_runs:
        return False
    last = await get_last(redis, prefix, conversation_id)
    return (time.time() - last) >= min_hours * 3600


async def acquire_lock(
    redis: Redis, prefix: str, conversation_id: str, *, ttl_s: int
) -> str | None:
    """SET NX a holder token. Returns the token, or None if held."""
    token = uuid.uuid4().hex
    ok = await redis.set(_k(prefix, "lock", conversation_id), token, nx=True, ex=ttl_s)
    return token if ok else None


# Atomic compare-and-delete: only delete the lock if WE still hold the token.
# A separate GET+DELETE would race — if the TTL expired between them and another
# worker took a fresh token, the stale DELETE would drop the new holder's lock.
# Comparing raw stored bytes to the passed token works regardless of
# decode_responses.
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) "
    "else return 0 end"
)


async def release_lock(redis: Redis, prefix: str, conversation_id: str, token: str) -> None:
    """Release the lock atomically, only if this caller still holds the token."""
    key = _k(prefix, "lock", conversation_id)
    # redis-py types eval() as a sync/async union; the async client returns an
    # awaitable at runtime.
    await cast("Awaitable[Any]", redis.eval(_RELEASE_LUA, 1, key, token))


async def mark_consolidated(
    redis: Redis,
    prefix: str,
    conversation_id: str,
    *,
    cutoff: float,
    consumed: int,
) -> None:
    """High-water-mark: advance last to cutoff and DECRBY the consumed count
    (never reset-to-0), so runs that arrived during the pass stay counted."""
    last_key = _k(prefix, "last", conversation_id)
    await redis.set(last_key, repr(cutoff), ex=_TTL_S)
    if consumed > 0:
        await redis.decrby(_k(prefix, "runs", conversation_id), consumed)


from cubebox.models.memory import (  # noqa: E402
    MemoryScope,
    MemorySourceType,
    MemoryStatus,
    MemoryType,
)
from cubebox.services.memory import CreateMemoryInput, MemoryService  # noqa: E402

_VALID_TYPES = {t.value for t in MemoryType}


def parse_ops(raw: str, *, max_ops: int) -> list[dict[str, Any]] | None:
    """Parse + validate the LLM's JSON op envelope. Returns valid ops, or None to
    reject the whole batch (bad JSON / wrong shape / over cap)."""
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return None
    ops = doc.get("ops") if isinstance(doc, dict) else None
    if not isinstance(ops, list) or len(ops) > max_ops:
        return None

    valid: list[dict[str, Any]] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        action = op.get("action")
        if action == "extract":
            if (
                op.get("type") in _VALID_TYPES
                and isinstance(op.get("content"), str)
                and op["content"].strip()
            ):
                valid.append(op)
        elif action == "merge":
            if (
                isinstance(op.get("id"), str)
                and isinstance(op.get("content"), str)
                and op["content"].strip()
            ):
                valid.append(op)
        elif action == "archive":
            if isinstance(op.get("id"), str):
                valid.append(op)
    return valid


async def apply_ops(
    service: MemoryService,
    ops: list[dict[str, Any]],
    *,
    conversation_id: str,
    run_id: str | None,
) -> None:
    """Apply ops. Scope hard-coded PERSONAL on create; merge/archive verify the
    target is the user's PERSONAL item (via repo.get) before mutating, so a
    hallucinated id can't touch a shared item. Source stamped CONSOLIDATION."""
    for op in ops:
        action = op["action"]
        try:
            if action == "extract":
                await service.create(
                    CreateMemoryInput(
                        scope=MemoryScope.PERSONAL,
                        type=MemoryType(op["type"]),
                        content=op["content"].strip(),
                        source_type=MemorySourceType.CONSOLIDATION,
                        source_conversation_id=conversation_id,
                        source_run_id=run_id,
                    )
                )
            elif action in ("merge", "archive"):
                target = await service.repo.get(op["id"])
                if target is None or target.scope != MemoryScope.PERSONAL:
                    continue
                if action == "merge":
                    await service.update(op["id"], content=op["content"].strip())
                else:
                    await service.archive(op["id"])
        except Exception:
            logger.warning("consolidation op failed: {}", op, exc_info=True)


DEFAULT_MIN_HOURS = 6.0
DEFAULT_MIN_RUNS = 5
MAX_OPS = 20
HISTORY_MSG_CAP = 40
LOCK_TTL_S = 120
EXTRACT_MODEL_MAX_TOKENS = 1500

CONSOLIDATION_SYSTEM = (
    "You distill a conversation into durable PERSONAL memory for one user. Output ONLY\n"
    'a JSON object: {"ops": [...]}. Each op is one of:\n'
    '- {"action":"extract","type":<preference|correction|procedure|project_fact|decision|org_policy>,"content":"..."}\n'
    '- {"action":"merge","id":"<existing memory id>","content":"<updated text>"}\n'
    '- {"action":"archive","id":"<existing memory id>"}\n'
    "Rules: only durable facts worth recalling in FUTURE conversations; never secrets\n"
    "or transient task state; prefer merge over a contradictory new extract; dedup\n"
    f"against the existing items provided; at most {MAX_OPS} ops. If nothing is worth saving,\n"
    'return {"ops": []}.\n'
)


def _render_history(messages: list[Any]) -> str:
    lines: list[str] = []
    for m in messages:
        role = getattr(m, "role", "?")
        content = getattr(m, "content", None)
        text = ""
        if isinstance(content, list):
            text = " ".join(
                getattr(b, "text", "") for b in content if getattr(b, "type", "") == "text"
            )
        line = f"{role}: {text}".strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


async def run_consolidation(
    *,
    redis: Redis,
    prefix: str,
    conversation_id: str,
    user_id: str,
    org_id: str | None,
    workspace_id: str | None,
    provider: Any,
    model: Any,
    session_maker: Any,
    tracer: Any | None = None,
    min_hours: float = DEFAULT_MIN_HOURS,
    min_runs: int = DEFAULT_MIN_RUNS,
) -> None:
    """Best-effort per-conversation consolidation. Never raises into the caller.

    When ``tracer`` is provided, the consolidation LLM call is wrapped in
    ``tracer.oneshot(operation="consolidate_memory", metadata={...})`` so the
    run appears in ``cubepi trace ls`` alongside agent runs, with
    ``conversation_id`` / ``user_id`` metadata searchable via ``--meta``.
    """
    from cubepi.providers.base import Message, TextContent, UserMessage

    from cubebox.agents.checkpointer import init_checkpointer
    from cubebox.llm.oneshot import OneShotLLM
    from cubebox.repositories.memory import MemoryRepository

    token = await acquire_lock(redis, prefix, conversation_id, ttl_s=LOCK_TTL_S)
    if token is None:
        return
    cutoff = time.time()
    consumed = await _counter(redis, prefix, conversation_id)
    try:
        async with init_checkpointer() as cp:
            data = await cp.load(conversation_id)
        if data is None or not data.messages:
            await mark_consolidated(
                redis, prefix, conversation_id, cutoff=cutoff, consumed=consumed
            )
            return
        history_text = _render_history(data.messages[-HISTORY_MSG_CAP:])

        async with session_maker() as s:
            repo = MemoryRepository(s, user_id=user_id, org_id=org_id, workspace_id=workspace_id)
            existing = await repo.list(
                scope=MemoryScope.PERSONAL, status=MemoryStatus.ACTIVE, limit=200
            )
        existing_text = "\n".join(f"- [{m.id}] ({m.type.value}) {m.content}" for m in existing)

        prompt = (
            f"Existing personal memory items:\n{existing_text or '(none)'}\n\n"
            f"Conversation transcript:\n{history_text}"
        )
        messages: list[Message] = [UserMessage(content=[TextContent(text=prompt)])]
        meta: dict[str, str | int | float | bool] = {
            "conversation_id": conversation_id,
            "user_id": user_id,
        }
        if workspace_id is not None:
            meta["workspace_id"] = workspace_id
        if org_id is not None:
            meta["org_id"] = org_id

        if tracer is not None:
            async with tracer.oneshot(
                provider=provider,
                model=model,
                operation="consolidate_memory",
                metadata=meta,
            ) as session:
                raw = await session.generate(
                    system=CONSOLIDATION_SYSTEM,
                    messages=messages,
                    max_output_tokens=EXTRACT_MODEL_MAX_TOKENS,
                )
        else:
            raw = await OneShotLLM(provider, model).generate_once(
                system=CONSOLIDATION_SYSTEM,
                messages=messages,
                max_output_tokens=EXTRACT_MODEL_MAX_TOKENS,
            )
        ops = parse_ops(raw, max_ops=MAX_OPS)
        if ops is None:
            # Malformed / over-cap LLM output = a FAILED pass. Do NOT advance the
            # high-water-mark — leave last/counter so this window retries next
            # eligible run (otherwise those turns' memories are lost forever).
            logger.warning(
                "memory consolidation produced malformed ops for {}; leaving for retry",
                conversation_id,
            )
            return
        if ops:  # ops == [] is a valid "nothing worth saving" → advance below
            async with session_maker() as s:
                repo = MemoryRepository(
                    s, user_id=user_id, org_id=org_id, workspace_id=workspace_id
                )
                service = MemoryService(
                    repo, user_id=user_id, org_id=org_id, workspace_id=workspace_id
                )
                await apply_ops(service, ops, conversation_id=conversation_id, run_id=None)

        await mark_consolidated(redis, prefix, conversation_id, cutoff=cutoff, consumed=consumed)
    except Exception:
        logger.warning("memory consolidation failed for {}", conversation_id, exc_info=True)
        # Leave last/counter unchanged → retries next eligible run.
    finally:
        await release_lock(redis, prefix, conversation_id, token)
