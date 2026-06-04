"""CostMiddleware — cubepi port of CostMiddleware (M3.d.1).

Records per-LLM-call billing events after each model response.

Hook (per Spec B): ``after_model_response`` only.
No request wrapping — the response is already available when the hook fires.

Usage fields are read from ``response.usage`` (a ``cubepi.providers.base.Usage``
object with ``input_tokens``, ``output_tokens``, ``cache_read_tokens``,
``cache_write_tokens``).  Provider and model are read from ``response.provider_id``
and ``response.model_id`` (populated by the cubepi LLM adapter).

Attribution fields:
    ``_org_id``, ``_workspace_id``, ``_user_id``, ``_conversation_id`` — billing scope
    ``_parent_billing_id`` — links child subagent events to their parent
    ``_subagent_depth`` — billing hierarchy depth (0 = top-level agent)
    ``_last_billing_id`` — updated after each write so child subagents can chain

Subagent runs receive their own ``CostMiddleware`` instance from run_manager's
cubepi SubagentMiddleware configuration.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from cubepi.middleware.base import Middleware
from cubepi.providers.base import AssistantMessage
from loguru import logger

from cubebox.db.engine import async_session_maker
from cubebox.llm.config import ModelCost
from cubebox.models.billing import BillingEvent, LlmBillingEvent
from cubebox.models.public_id import generate_public_id
from cubebox.repositories.billing import BillingRepository

# Callable that resolves (provider, model_id) → ModelCost (or None if unknown).
PriceLookup = Callable[[str, str], ModelCost | None]


class CostMiddleware(Middleware):
    """Records one billing_events + billing_llm_events row per LLM call.

    Hooks:
    - ``after_model_response``: fires after the cubepi Agent receives the
      AssistantMessage; reads usage fields and writes billing rows
      asynchronously (fire-and-forget task).  Returns ``None`` so the
      agent continues normally.
    """

    def __init__(
        self,
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
        conversation_id: str,
        price_lookup: PriceLookup | None = None,
        parent_billing_id: str | None = None,
        subagent_depth: int = 0,
    ) -> None:
        self._org_id = org_id
        self._workspace_id = workspace_id
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._price_lookup = price_lookup
        self._parent_billing_id = parent_billing_id
        self._subagent_depth = subagent_depth
        self._last_billing_id: str | None = None

    async def after_model_response(
        self,
        response: AssistantMessage,
        ctx: Any,
        *,
        signal: Any = None,
    ) -> None:
        """Fire-and-forget billing write; always returns None."""
        run_id = generate_public_id("bill")
        self._last_billing_id = run_id
        started_at = datetime.now(UTC)

        asyncio.create_task(self._write(response, run_id, started_at, "success", None))
        return None

    async def _write(
        self,
        response: AssistantMessage,
        run_id: str,
        started_at: datetime,
        status: str,
        error_class: str | None,
    ) -> None:
        try:
            ended_at = datetime.now(UTC)
            usage = _extract_usage(response)
            provider = response.provider_id or "unknown"
            model_id = response.model_id or "unknown"
            duration_ms = max(0, int((ended_at - started_at).total_seconds() * 1000))

            cost_amount_micro = _compute_cost_micro(
                usage=usage,
                provider=provider,
                model_id=model_id,
                price_lookup=self._price_lookup,
            )

            be = BillingEvent(
                id=run_id,
                org_id=self._org_id,
                workspace_id=self._workspace_id,
                user_id=self._user_id,
                conversation_id=self._conversation_id,
                event_type="llm_call",
                cost_amount_micro=cost_amount_micro,
                currency="USD",
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=duration_ms,
                status=status,
            )
            le = LlmBillingEvent(
                billing_event_id=run_id,
                provider=provider,
                model_id=model_id,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cache_read_tokens=usage["cache_read_tokens"],
                cache_write_tokens=usage["cache_write_tokens"],
                parent_run_id=self._parent_billing_id,
                subagent_depth=self._subagent_depth,
                error_class=error_class,
            )

            async with async_session_maker() as session:
                repo = BillingRepository(session, org_id=self._org_id)
                await repo.insert_llm_event(be, le)

        except Exception as exc:
            logger.warning("billing write failed (run_id={}): {}", run_id, exc)


def _compute_cost_micro(
    *,
    usage: dict[str, int],
    provider: str,
    model_id: str,
    price_lookup: PriceLookup | None,
) -> int:
    """Compute the per-call cost in micro-dollars.

    ``ModelCost`` values are ``$ / million tokens`` (e.g. 3.0 = $3 per
    million input tokens).  For ``T`` tokens at price ``P``:

        dollars      = T * P / 1_000_000
        micro_dollar = dollars * 1_000_000 = T * P

    So summing ``tokens * price`` across the four buckets gives the cost
    already expressed in micro-dollars.  When no price_lookup is supplied or
    the lookup returns None (unknown model), we fall back to 0 so billing
    rows are still written for analytics — same defensive posture as the
    old hardcoded zero.
    """
    if price_lookup is None:
        return 0
    price = price_lookup(provider, model_id)
    if price is None:
        return 0
    return int(
        usage["input_tokens"] * price.input
        + usage["output_tokens"] * price.output
        + usage["cache_read_tokens"] * price.cache_read
        + usage["cache_write_tokens"] * price.cache_write
    )


def _extract_usage(response: AssistantMessage) -> dict[str, int]:
    """Extract token counts from a cubepi AssistantMessage."""
    usage = response.usage
    if usage is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
    }
