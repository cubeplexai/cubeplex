"""CostMiddleware — records per-LLM-call billing events."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from loguru import logger

from cubebox.db.engine import async_session_maker
from cubebox.models.billing import BillingEvent, LlmBillingEvent
from cubebox.models.public_id import PREFIX_BILLING_EVENT, generate_public_id
from cubebox.repositories.billing import BillingRepository


class CostMiddleware(AgentMiddleware[Any, Any, Any]):
    """Records one billing_events + billing_llm_events row per LLM call."""

    tools: Sequence[BaseTool] = []

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

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        run_id = generate_public_id(PREFIX_BILLING_EVENT)
        self._last_billing_id = run_id
        started_at = datetime.now(UTC)

        try:
            response = await handler(request)
            ended_at = datetime.now(UTC)
            asyncio.create_task(
                self._write(request, response, run_id, started_at, ended_at, "success", None)
            )
            return response
        except Exception as exc:
            ended_at = datetime.now(UTC)
            asyncio.create_task(
                self._write(
                    request, None, run_id, started_at, ended_at, "error", type(exc).__name__
                )
            )
            raise

    async def _write(
        self,
        request: Any,
        response: Any,
        run_id: str,
        started_at: datetime,
        ended_at: datetime,
        status: str,
        error_class: str | None,
    ) -> None:
        try:
            provider, model_id, model_cost = _extract_model_meta(request.model)
            usage = _extract_usage(response)
            cost_micro = _compute_cost_micro(usage, model_cost)
            duration_ms = max(0, int((ended_at - started_at).total_seconds() * 1000))

            be = BillingEvent(
                id=run_id,  # already a valid short ID from generate_public_id above
                org_id=self._org_id,
                workspace_id=self._workspace_id,
                user_id=self._user_id,
                conversation_id=self._conversation_id,
                event_type="llm_call",
                cost_amount_micro=cost_micro,
                currency=getattr(model_cost, "currency", "USD") if model_cost else "USD",
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
                price_input_per_mtok_micro=(
                    int(getattr(model_cost, "input", 0) * 1_000_000) if model_cost else 0
                ),
                price_output_per_mtok_micro=(
                    int(getattr(model_cost, "output", 0) * 1_000_000) if model_cost else 0
                ),
                price_cache_read_per_mtok_micro=(
                    int(getattr(model_cost, "cache_read", 0) * 1_000_000) if model_cost else 0
                ),
                price_cache_write_per_mtok_micro=(
                    int(getattr(model_cost, "cache_write", 0) * 1_000_000) if model_cost else 0
                ),
                parent_run_id=self._parent_billing_id,
                subagent_depth=self._subagent_depth,
                error_class=error_class,
            )

            async with async_session_maker() as session:
                repo = BillingRepository(session, org_id=self._org_id)
                await repo.insert_llm_event(be, le)

        except Exception as exc:
            logger.warning("billing write failed (run_id={}): {}", run_id, exc)


def _extract_model_meta(model: Any) -> tuple[str, str, Any]:
    provider = getattr(model, "_cubebox_provider", "unknown")
    model_id = getattr(model, "_cubebox_model_id", "unknown")
    model_cost = getattr(model, "_cubebox_model_cost", None)
    return provider, model_id, model_cost


def _extract_usage(response: Any) -> dict[str, int]:
    if response is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

    # ModelResponse.result is list[BaseMessage]; AIMessage can be returned directly.
    result = getattr(response, "result", response)
    if isinstance(result, list):
        result = next((m for m in result if isinstance(m, AIMessage)), None)

    usage = getattr(result, "usage_metadata", None) or {}
    if callable(usage):
        usage = {}

    details_in = usage.get("input_token_details") or {}
    details_out = usage.get("output_token_details") or {}

    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": details_in.get("cache_read", 0),
        "cache_write_tokens": details_out.get("cache_write", 0),
    }


def _compute_cost_micro(usage: dict[str, int], cost: Any) -> int:
    if cost is None:
        return 0
    total = (
        usage["input_tokens"] * getattr(cost, "input", 0) / 1_000_000
        + usage["output_tokens"] * getattr(cost, "output", 0) / 1_000_000
        + usage["cache_read_tokens"] * getattr(cost, "cache_read", 0) / 1_000_000
        + usage["cache_write_tokens"] * getattr(cost, "cache_write", 0) / 1_000_000
    )
    return int(total * 1_000_000)
