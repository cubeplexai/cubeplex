"""Unit tests for UserEventBus in-process pub/sub."""

from __future__ import annotations

import asyncio

import pytest

from cubeplex.models.user_event import UserEventType
from cubeplex.services.user_event_bus import UserEventBus, UserEventDTO


@pytest.mark.asyncio
async def test_subscriber_receives_published_event() -> None:
    bus = UserEventBus()
    received: list[UserEventDTO] = []

    async def consume() -> None:
        q, unsubscribe = bus.subscribe("usr_x")
        try:
            ev = await q.get()
            received.append(ev)
        finally:
            unsubscribe()

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
        q, unsubscribe = bus.subscribe("usr_x")
        try:
            # Wait briefly; nothing for this user should arrive.
            ev = await asyncio.wait_for(q.get(), timeout=0.05)
            out.append(ev)
        except TimeoutError:
            pass
        finally:
            unsubscribe()
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
    result = await consumer
    assert result == []


@pytest.mark.asyncio
async def test_subscriber_cleanup_on_unsubscribe() -> None:
    """Verify that calling unsubscribe() removes the queue from _subscribers."""
    bus = UserEventBus()
    received: list[UserEventDTO] = []

    async def consume_one() -> None:
        q, unsubscribe = bus.subscribe("usr_z")
        try:
            ev = await q.get()
            received.append(ev)
        finally:
            unsubscribe()

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
    await asyncio.sleep(0)  # let finally block run

    # After unsubscribe, the subscriber bucket should be cleaned up.
    assert "usr_z" not in bus._subscribers
    assert len(received) == 1


@pytest.mark.asyncio
async def test_multiple_subscribers_same_user() -> None:
    """Both subscribers for the same user_id receive the event."""
    bus = UserEventBus()
    received_a: list[UserEventDTO] = []
    received_b: list[UserEventDTO] = []

    async def consume_a() -> None:
        q, unsubscribe = bus.subscribe("usr_multi")
        try:
            ev = await q.get()
            received_a.append(ev)
        finally:
            unsubscribe()

    async def consume_b() -> None:
        q, unsubscribe = bus.subscribe("usr_multi")
        try:
            ev = await q.get()
            received_b.append(ev)
        finally:
            unsubscribe()

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
