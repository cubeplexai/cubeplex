"""Usage aggregation service — centralises billing queries for token usage."""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from loguru import logger
from sqlalchemy import func as sa_func
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.encryption import EncryptionBackend
from cubebox.models.billing import BillingEvent, LlmBillingEvent


class TurnUsage(TypedDict):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class SessionUsage(TypedDict):
    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    total_cache_write_tokens: int


class UsageSummary(TypedDict, total=False):
    turn: TurnUsage
    session: SessionUsage
    context_window: int
    # Max input_tokens across LLM calls in the last turn — each call already
    # contains the full context, so MAX approximates the actual context size
    # without double-counting like SUM would.
    context_tokens: int


_ZERO_SESSION: SessionUsage = {
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_cache_read_tokens": 0,
    "total_cache_write_tokens": 0,
}


async def get_session_usage(
    session: AsyncSession,
    conversation_id: str,
) -> SessionUsage:
    """Sum all billing tokens for a conversation."""
    stmt = (
        sa_select(
            sa_func.coalesce(sa_func.sum(LlmBillingEvent.input_tokens), 0),
            sa_func.coalesce(sa_func.sum(LlmBillingEvent.output_tokens), 0),
            sa_func.coalesce(sa_func.sum(LlmBillingEvent.cache_read_tokens), 0),
            sa_func.coalesce(sa_func.sum(LlmBillingEvent.cache_write_tokens), 0),
        )
        .join(
            BillingEvent,
            LlmBillingEvent.billing_event_id == BillingEvent.id,  # type: ignore[arg-type]
        )
        .where(
            BillingEvent.conversation_id == conversation_id,  # type: ignore[arg-type]
        )
    )
    row = (await session.execute(stmt)).one()
    return {
        "total_input_tokens": int(row[0]),
        "total_output_tokens": int(row[1]),
        "total_cache_read_tokens": int(row[2]),
        "total_cache_write_tokens": int(row[3]),
    }


async def get_turn_usage(
    session: AsyncSession,
    conversation_id: str,
    *,
    after: datetime,
) -> tuple[TurnUsage, int]:
    """Sum billing tokens for one turn; also return max input_tokens (context size).

    Returns (turn_usage, context_tokens) where context_tokens is the largest
    single-call input_tokens in the turn — each LLM call already carries the
    full context, so MAX is the context size rather than the misleading SUM.
    """
    stmt = (
        sa_select(
            sa_func.coalesce(sa_func.sum(LlmBillingEvent.input_tokens), 0),
            sa_func.coalesce(sa_func.sum(LlmBillingEvent.output_tokens), 0),
            sa_func.coalesce(sa_func.sum(LlmBillingEvent.cache_read_tokens), 0),
            sa_func.coalesce(sa_func.sum(LlmBillingEvent.cache_write_tokens), 0),
            sa_func.coalesce(sa_func.max(LlmBillingEvent.input_tokens), 0),
        )
        .join(
            BillingEvent,
            LlmBillingEvent.billing_event_id == BillingEvent.id,  # type: ignore[arg-type]
        )
        .where(
            BillingEvent.conversation_id == conversation_id,  # type: ignore[arg-type]
            BillingEvent.started_at >= after,  # type: ignore[arg-type]
        )
    )
    row = (await session.execute(stmt)).one()
    turn: TurnUsage = {
        "input_tokens": int(row[0]),
        "output_tokens": int(row[1]),
        "cache_read_tokens": int(row[2]),
        "cache_write_tokens": int(row[3]),
    }
    return turn, int(row[4])


async def build_usage_summary(
    session: AsyncSession,
    conversation_id: str,
    *,
    org_id: str,
    encryption_backend: EncryptionBackend,
    last_user_message_ts: str | None = None,
) -> UsageSummary:
    """Build a complete usage summary for the usage panel.

    Called from both the bootstrap endpoint and RunManager done event.
    """
    summary: UsageSummary = {
        "session": dict(_ZERO_SESSION),  # type: ignore[typeddict-item]
        "context_window": 0,
    }
    try:
        summary["session"] = await get_session_usage(session, conversation_id)

        if last_user_message_ts:
            ts_dt = datetime.fromisoformat(last_user_message_ts)
            turn, context_tokens = await get_turn_usage(session, conversation_id, after=ts_dt)
            summary["turn"] = turn
            summary["context_tokens"] = context_tokens

        from cubebox.llm.resolver import parse_model_ref, resolve_model_preset
        from cubebox.llm.snapshot import load_llm_snapshot

        snap = await load_llm_snapshot(session, org_id, encryption_backend)
        preset = resolve_model_preset(snap, None)
        slug, model_id = parse_model_ref(preset.chain[0])
        provider_cfg = snap.providers[slug]
        model_cfg = next(m for m in provider_cfg.models if m.id == model_id)
        summary["context_window"] = model_cfg.context_window
    except Exception:
        logger.opt(exception=True).warning(
            "Failed to build usage summary for conversation {}",
            conversation_id,
        )
    return summary
