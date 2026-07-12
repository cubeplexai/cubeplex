import asyncio

import fakeredis.aioredis
import pytest

from cubeplex.streams.run_manager import RunManager


def _mgr(redis: fakeredis.aioredis.FakeRedis) -> RunManager:
    m = RunManager.__new__(RunManager)  # type: ignore[call-arg]
    m._redis = redis
    m._key_prefix = "t"
    m._tasks = {}
    m._agents = {}
    m._ack_waiters = {}
    m._control_channel = "t:control"
    m._ack_channel = "t:control:ack"
    m._control_stopping = False
    m._control_tasks = []
    return m


class _FakeAgent:
    def __init__(self) -> None:
        self.steered: list = []

    def steer(self, message) -> None:  # noqa: ANN001
        self.steered.append(message)


@pytest.mark.asyncio
async def test_cross_instance_steer() -> None:
    # Both managers share one FakeRedis instance — fakeredis routes pub/sub
    # across all pubsub handles created from the same client, so A's listener
    # sees B's publish without needing a real network.
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    a, b = _mgr(redis), _mgr(redis)
    agent = _FakeAgent()
    a._agents["r1"] = agent  # owner is A
    await a.start_control_listeners()
    try:
        assert await b.dispatch_steer("r1", "redirect", steer_id="s1") == "published"
        for _ in range(50):
            if agent.steered:
                break
            await asyncio.sleep(0.05)
        assert agent.steered[0].content[0].text == "redirect"
        assert agent.steered[0].metadata["steer_id"] == "s1"
    finally:
        await a.stop_control_listeners()


@pytest.mark.asyncio
async def test_cross_instance_cancel_ack() -> None:
    # Same shared-client setup: A owns the run task; B dispatches cancel and
    # waits for the ack that A publishes after calling cancel_run.
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    a, b = _mgr(redis), _mgr(redis)

    cancelled = asyncio.Event()

    async def fake_cancel(run_id: str) -> bool:
        cancelled.set()
        return True

    a._tasks["r1"] = object()  # owner A "has" the task
    a.cancel_run = fake_cancel  # type: ignore[assignment]
    await a.start_control_listeners()
    await b.start_control_listeners()  # B needs its ack listener to resolve its future
    try:
        assert await b.dispatch_cancel("r1", ack_timeout=3.0) == "cancelled"
        assert cancelled.is_set()
    finally:
        await a.stop_control_listeners()
        await b.stop_control_listeners()
