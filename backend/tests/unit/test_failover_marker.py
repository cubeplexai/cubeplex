"""_make_failover_publisher closure builds correct payload."""

import pytest

from cubeplex.streams.run_manager import _make_failover_publisher


class _FakeSpec:
    def __init__(self, pid, mid):
        self.provider_id = pid
        self.id = mid


class _FakeBound:
    def __init__(self, pid, mid):
        self.spec = _FakeSpec(pid, mid)


@pytest.mark.asyncio
async def test_publisher_emits_correct_shape():
    sent: list[tuple[str, dict]] = []

    async def publish(run_id, payload):
        sent.append((run_id, payload))

    cb = _make_failover_publisher("run_abc", publish)
    await cb(_FakeBound("p1", "m1"), _FakeBound("p2", "m2"), RuntimeError("boom"))
    assert sent == [
        (
            "run_abc",
            {
                "failed_ref": "p1/m1",
                "next_ref": "p2/m2",
                "reason": "boom",
            },
        )
    ]


@pytest.mark.asyncio
async def test_publisher_handles_none_next():
    sent: list = []

    async def publish(run_id, payload):
        sent.append(payload)

    cb = _make_failover_publisher("r", publish)
    await cb(_FakeBound("p1", "m1"), None, "exhausted")
    assert sent[0]["next_ref"] is None
    assert sent[0]["reason"] == "exhausted"


@pytest.mark.asyncio
async def test_publisher_truncates_reason():
    sent: list = []

    async def publish(run_id, payload):
        sent.append(payload)

    cb = _make_failover_publisher("r", publish)
    await cb(_FakeBound("p", "m"), None, "x" * 1000)
    assert len(sent[0]["reason"]) == 256
