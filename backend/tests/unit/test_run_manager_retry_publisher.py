"""_make_retry_publisher payload contract for model_retry SSE events."""

from __future__ import annotations

from typing import Any

from cubepi.errors import RateLimited
from cubepi.providers.faux import FauxProvider

from cubeplex.streams.run_manager import _make_retry_publisher


async def test_retry_publisher_emits_model_ref_attempt_and_wait() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def publish(rid: str, data: dict[str, Any]) -> None:
        calls.append((rid, data))

    cb = _make_retry_publisher("run-1", publish)
    bound = FauxProvider(provider_id="ark").model("glm-5.2")
    await cb(bound, RateLimited("simulated 429", retry_after=2.0), 2, 2.0)

    assert calls == [
        (
            "run-1",
            {
                "model_ref": "ark/glm-5.2",
                "reason": "simulated 429",
                "attempt": 2,
                "wait_s": 2.0,
            },
        )
    ]
