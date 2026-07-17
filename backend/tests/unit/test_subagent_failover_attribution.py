"""Subagent receives chain model without on_failover (Fix-6).

When the main agent runs with a ``FallbackBoundModel`` carrying an
``on_failover`` callback (which publishes ``model_failover`` SSE events),
the subagent middleware must NOT see that callback — otherwise subagent
chain failovers fire the main-agent publisher and the SSE event is
misattributed to the top-level conversation.

The fix lives in :func:`cubeplex.streams.run_manager._subagent_model_for`:
on ``FallbackBoundModel`` it returns ``dataclasses.replace(model,
on_failover=None)``; on a plain ``BoundModel`` it passes the model
through unchanged.

These tests pin both behaviours so a future refactor of ``run_manager``
cannot silently regress attribution.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from cubepi.providers.fallback import FallbackBoundModel
from cubepi.providers.faux import FauxProvider

from cubeplex.streams.run_manager import _subagent_model_for


def _make_fallback_with_callback() -> tuple[FallbackBoundModel, Any]:
    primary = FauxProvider(provider_id="p1").model("m1")
    secondary = FauxProvider(provider_id="p2").model("m2")

    async def cb(failed: Any, nxt: Any, err: Any) -> None:  # pragma: no cover
        return None

    fb = FallbackBoundModel(chain=(primary, secondary), on_failover=cb)
    return fb, cb


def test_replace_strips_on_failover() -> None:
    """dataclasses.replace contract: copy with on_failover=None, original intact."""
    fb, cb = _make_fallback_with_callback()

    stripped = replace(fb, on_failover=None)

    assert stripped.on_failover is None
    # Frozen original is unchanged.
    assert fb.on_failover is cb
    # Chain is preserved (failover still works at the chain level, just silently).
    assert stripped.chain == fb.chain


def test_subagent_model_for_fallback_strips_callback() -> None:
    """_subagent_model_for(FallbackBoundModel) returns a copy with on_failover=None."""
    fb, cb = _make_fallback_with_callback()

    sub = _subagent_model_for(fb)

    assert isinstance(sub, FallbackBoundModel)
    assert sub.on_failover is None
    # Original main-agent model still carries the publisher closure.
    assert fb.on_failover is cb
    assert sub.chain == fb.chain


def test_subagent_model_for_plain_boundmodel_unchanged() -> None:
    """_subagent_model_for(BoundModel) passes plain BoundModel through unchanged."""
    bound = FauxProvider(provider_id="p1").model("m1")

    sub = _subagent_model_for(bound)

    # Plain BoundModel has no on_failover field; pass-through (identity) is fine.
    assert sub is bound
