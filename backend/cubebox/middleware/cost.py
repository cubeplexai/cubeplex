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

Subagent cloning (M3.c.3): ``SubAgentMiddleware`` reads ``_last_billing_id``
and ``_subagent_depth`` from the parent ``CostMiddleware`` instance and
constructs a new child instance with ``subagent_depth + 1``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from cubepi.middleware.base import Middleware
from cubepi.providers.base import AssistantMessage
from loguru import logger

from cubebox.db.engine import async_session_maker
from cubebox.models.billing import BillingEvent, LlmBillingEvent
from cubebox.models.public_id import generate_public_id
from cubebox.repositories.billing import BillingRepository


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
        parent_billing_id: str | None = None,
        subagent_depth: int = 0,
    ) -> None:
        self._org_id = org_id
        self._workspace_id = workspace_id
        self._user_id = user_id
        self._conversation_id = conversation_id
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

            be = BillingEvent(
                id=run_id,
                org_id=self._org_id,
                workspace_id=self._workspace_id,
                user_id=self._user_id,
                conversation_id=self._conversation_id,
                event_type="llm_call",
                cost_amount_micro=0,  # no per-model pricing table yet; future M3.d.x
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
