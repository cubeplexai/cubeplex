"""Chained ``after_tool_call`` composer.

Cubepi's :func:`cubepi.middleware.base.compose_middleware` runs every
middleware's ``after_tool_call`` against the same untouched ``ctx`` and
returns only the **last** non-``None`` :class:`AfterToolCallResult`.
That means a middleware that rewrites ``content`` is silently discarded
the moment a later middleware (e.g. ``TimestampMiddleware`` returning
``AfterToolCallResult(details={...})``) returns its own result.

This composer threads each middleware's return through to the next one
via a derived ``ctx``: ``content`` takes the most recent non-``None``
override, ``details`` dicts merge (later wins on key conflicts).  Pass
the result into :class:`cubepi.Agent` via the ``after_tool_call=``
override so it replaces the buggy cubepi default for this stack.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from cubepi.agent.types import AfterToolCallContext, AfterToolCallResult, AgentToolResult
from cubepi.middleware.base import Middleware


def _merge_details(existing: Any, incoming: Any) -> Any:
    """Merge two ``details`` payloads — later dict wins per-key.

    Non-dict ``incoming`` simply replaces ``existing`` (mirrors cubepi's
    ``_finalize`` semantics).  ``incoming is None`` leaves ``existing``
    in place so middlewares that contribute nothing don't blank out
    earlier contributions.
    """
    if incoming is None:
        return existing
    if isinstance(existing, dict) and isinstance(incoming, dict):
        return {**existing, **incoming}
    return incoming


def _overrides_after_tool_call(mw: Middleware) -> bool:
    method = getattr(type(mw), "after_tool_call", None)
    return method is not None and method is not Middleware.after_tool_call


def compose_after_tool_call(
    middlewares: list[Middleware],
) -> Callable[..., Awaitable[AfterToolCallResult | None]] | None:
    """Return a chained ``after_tool_call`` hook or ``None`` when empty."""

    chain = [m for m in middlewares if _overrides_after_tool_call(m)]
    if not chain:
        return None

    async def composed(
        ctx: AfterToolCallContext,
        *,
        signal: asyncio.Event | None = None,
    ) -> AfterToolCallResult | None:
        accum_content = ctx.result.content
        accum_details: Any = ctx.result.details
        accum_is_error = ctx.is_error
        accum_terminate = ctx.result.terminate
        any_contribution = False

        for mw in chain:
            sub_ctx = replace(
                ctx,
                result=AgentToolResult(
                    content=accum_content,
                    details=accum_details,
                    is_error=accum_is_error,
                    terminate=accum_terminate,
                ),
                is_error=accum_is_error,
            )
            result = await mw.after_tool_call(sub_ctx, signal=signal)
            if result is None:
                continue
            any_contribution = True
            if result.content is not None:
                accum_content = result.content
            accum_details = _merge_details(accum_details, result.details)
            if result.is_error is not None:
                accum_is_error = result.is_error
            if result.terminate is not None:
                accum_terminate = result.terminate

        if not any_contribution:
            return None

        return AfterToolCallResult(
            content=accum_content,
            details=accum_details,
            is_error=accum_is_error,
            terminate=accum_terminate,
        )

    return composed
