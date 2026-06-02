"""Unit tests for UserEventBus in-process pub/sub."""

from __future__ import annotations

import asyncio

import pytest

from cubebox.models.user_event import UserEventType
from cubebox.services.user_event_bus import UserEventBus, UserEventDTO


@pytest.mark.asyncio
async def test_subscriber_receives_published_event() -> None:
    bus = UserEventBus()
    received: list[UserEventDTO] = []

    async def consume() -> None:
        async for ev in bus.subscribe("usr_x"):
            received.append(ev)
            if len(received) == 1:
                break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)  # let consumer subscribe

    await bus.publish_local(
        UserEventDTO(
            id="uev_1",
            user_id="usr_x",
            workspace_id=None,
            type=UserEventType.MEMORY_UPDATED,
            payload={"items": []},
            created_at_iso="2026-06-02T00:00:00+00:00",
        )
    )

    await asyncio.wait_for(consumer, timeout=1.0)
    assert received[0].id == "uev_1"


@pytest.mark.asyncio
async def test_other_user_events_not_delivered() -> None:
    bus = UserEventBus()

    async def consume() -> list[UserEventDTO]:
        out: list[UserEventDTO] = []
        async for ev in bus.subscribe("usr_x"):
            out.append(ev)
            return out
        return out

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)

    await bus.publish_local(
        UserEventDTO(
            id="uev_2",
            user_id="usr_y",
            workspace_id=None,
            type=UserEventType.MEMORY_UPDATED,
            payload={},
            created_at_iso="",
        )
    )
    await asyncio.sleep(0.05)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer


@pytest.mark.asyncio
async def test_subscriber_cleanup_on_break() -> None:
    """Verify the try/finally in subscribe removes the queue correctly."""
    bus = UserEventBus()
    received: list[UserEventDTO] = []

    async def consume_one() -> None:
        async for ev in bus.subscribe("usr_z"):
            received.append(ev)
            break  # break triggers finally in the generator

    consumer = asyncio.create_task(consume_one())
    await asyncio.sleep(0)  # let subscribe register

    await bus.publish_local(
        UserEventDTO(
            id="uev_3",
            user_id="usr_z",
            workspace_id=None,
            type=UserEventType.MEMORY_UPDATED,
            payload={},
            created_at_iso="2026-06-02T00:00:00+00:00",
        )
    )
    await asyncio.wait_for(consumer, timeout=1.0)
    await asyncio.sleep(0)  # let finally block acquire lock and clean up

    # After break, the subscriber bucket should be cleaned up
    assert "usr_z" not in bus._subscribers
    assert len(received) == 1


@pytest.mark.asyncio
async def test_multiple_subscribers_same_user() -> None:
    """Both subscribers for the same user_id receive the event."""
    bus = UserEventBus()
    received_a: list[UserEventDTO] = []
    received_b: list[UserEventDTO] = []

    async def consume_a() -> None:
        async for ev in bus.subscribe("usr_multi"):
            received_a.append(ev)
            break

    async def consume_b() -> None:
        async for ev in bus.subscribe("usr_multi"):
            received_b.append(ev)
            break

    task_a = asyncio.create_task(consume_a())
    task_b = asyncio.create_task(consume_b())
    await asyncio.sleep(0)

    await bus.publish_local(
        UserEventDTO(
            id="uev_4",
            user_id="usr_multi",
            workspace_id=None,
            type=UserEventType.MEMORY_UPDATED,
            payload={},
            created_at_iso="2026-06-02T00:00:00+00:00",
        )
    )

    await asyncio.wait_for(task_a, timeout=1.0)
    await asyncio.wait_for(task_b, timeout=1.0)
    assert received_a[0].id == "uev_4"
    assert received_b[0].id == "uev_4"
