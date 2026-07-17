import asyncio
import json

import fakeredis.aioredis
import pytest

from cubeplex.streams.run_manager import RunManager


def _mgr(redis) -> RunManager:
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


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


@pytest.mark.asyncio
async def test_dispatch_steer_local_calls_agent(redis):
    m = _mgr(redis)
    agent = _FakeAgent()
    m._agents["r1"] = agent
    assert await m.dispatch_steer("r1", "go left", steer_id="s1") == "steered"
    assert agent.steered[0].content[0].text == "go left"
    assert agent.steered[0].metadata["steer_id"] == "s1"


@pytest.mark.asyncio
async def test_dispatch_steer_remote_publishes(redis):
    m = _mgr(redis)
    pubsub = redis.pubsub()
    await pubsub.subscribe("t:control")
    await asyncio.sleep(0)
    assert await m.dispatch_steer("r-remote", "hello", steer_id="s1") == "published"
    got = None
    for _ in range(20):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if msg:
            got = json.loads(msg["data"])
            break
    assert got == {
        "run_id": "r-remote",
        "type": "steer",
        "content": "hello",
        "steer_id": "s1",
    }


@pytest.mark.asyncio
async def test_handle_control_steer_dispatches_locally(redis):
    m = _mgr(redis)
    agent = _FakeAgent()
    m._agents["r1"] = agent
    await m._handle_control({"run_id": "r1", "type": "steer", "content": "x"})
    assert agent.steered[0].content[0].text == "x"


@pytest.mark.asyncio
async def test_handle_control_unknown_run_ignored(redis):
    m = _mgr(redis)
    await m._handle_control({"run_id": "ghost", "type": "steer", "content": "x"})
    await m._handle_control({"run_id": "ghost", "type": "cancel"})


@pytest.mark.asyncio
async def test_ack_resolves_waiter(redis):
    m = _mgr(redis)
    fut = asyncio.get_running_loop().create_future()
    m._ack_waiters["r1"] = [fut]
    await m._handle_ack({"run_id": "r1"})
    assert fut.done() and fut.result() is True


@pytest.mark.asyncio
async def test_dispatch_cancel_remote_times_out_to_published(redis):
    m = _mgr(redis)
    assert await m.dispatch_cancel("r-remote", ack_timeout=0.2) == "published"
    assert m._ack_waiters.get("r-remote") in (None, [])


@pytest.mark.asyncio
async def test_start_listeners_does_not_raise_when_subscribe_never_ready(redis, monkeypatch):
    # Persistent subscribe failure (e.g. Redis ACL) must NOT hang or crash boot:
    # start_control_listeners logs a warning and returns; the background loop
    # keeps retrying.
    m = _mgr(redis)

    async def _never_ready(channel, handler, ready):
        await asyncio.sleep(10)  # never sets `ready`

    monkeypatch.setattr(m, "_subscribe_loop", _never_ready)
    await m.start_control_listeners(ready_timeout=0.05)  # returns despite no readiness
    await m.stop_control_listeners()
