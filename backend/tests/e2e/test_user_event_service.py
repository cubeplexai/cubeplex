"""E2E tests for UserEventService — persist + broadcast via DB session."""

from __future__ import annotations

import asyncio
import secrets

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.user_event import UserEventType
from cubeplex.repositories.user_event import UserEventRepository
from cubeplex.services.user_event import PublishUserEventInput, UserEventService
from cubeplex.services.user_event_bus import UserEventBus, UserEventDTO


def _unique_user_id() -> str:
    # Per-test unique id so events don't accumulate across runs in a dev DB.
    return f"usr-uev-{secrets.token_hex(4)}"


@pytest_asyncio.fixture
async def test_user_id(db_session: AsyncSession) -> str:
    """Insert a unique user row per test and return its id."""
    uid = _unique_user_id()
    await db_session.execute(
        text(
            "INSERT INTO users (id, email, hashed_password, is_active, is_superuser,"
            " is_verified, created_at, language)"
            " VALUES (:id, :email, 'x', true, false, false, NOW(), 'en')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": uid, "email": f"{uid}@test.local"},
    )
    await db_session.commit()
    return uid


@pytest.mark.asyncio
async def test_publish_writes_and_broadcasts(db_session: AsyncSession, test_user_id: str) -> None:
    bus = UserEventBus()
    repo = UserEventRepository(db_session)
    svc = UserEventService(repo=repo, bus=bus)

    received: list[UserEventDTO] = []

    async def consume() -> None:
        q, unsubscribe = bus.subscribe(test_user_id)
        try:
            dto = await q.get()
            received.append(dto)
        finally:
            unsubscribe()

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)  # let consumer register before publish

    ev = await svc.publish(
        PublishUserEventInput(
            user_id=test_user_id,
            workspace_id=None,
            type=UserEventType.MEMORY_UPDATED,
            payload={"items": []},
        )
    )

    await asyncio.wait_for(consumer, timeout=1.0)
    assert received and received[0].id == ev.id

    assert ev.id.startswith("uev-")

    # verify DB persistence
    listed = await repo.list_for_user(test_user_id, since_id=None, limit=10)
    assert any(r.id == ev.id for r in listed)


@pytest.mark.asyncio
async def test_list_since_id_filters(db_session: AsyncSession, test_user_id: str) -> None:
    bus = UserEventBus()
    repo = UserEventRepository(db_session)
    svc = UserEventService(repo=repo, bus=bus)

    e1 = await svc.publish(
        PublishUserEventInput(
            user_id=test_user_id,
            workspace_id=None,
            type=UserEventType.MEMORY_UPDATED,
            payload={"n": 1},
        )
    )
    e2 = await svc.publish(
        PublishUserEventInput(
            user_id=test_user_id,
            workspace_id=None,
            type=UserEventType.MEMORY_UPDATED,
            payload={"n": 2},
        )
    )

    rows = await repo.list_for_user(test_user_id, since_id=e1.id, limit=10)
    ids = [r.id for r in rows]
    assert e2.id in ids
    assert e1.id not in ids


@pytest.mark.asyncio
async def test_mark_read(db_session: AsyncSession, test_user_id: str) -> None:
    bus = UserEventBus()
    repo = UserEventRepository(db_session)
    svc = UserEventService(repo=repo, bus=bus)

    ev = await svc.publish(
        PublishUserEventInput(
            user_id=test_user_id,
            workspace_id=None,
            type=UserEventType.MEMORY_UPDATED,
            payload={"items": []},
        )
    )

    assert ev.read_at is None

    updated = await repo.mark_read(ev.id, test_user_id)
    assert updated is not None
    assert updated.read_at is not None
