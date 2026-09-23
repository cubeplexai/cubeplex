"""Legacy sandbox-command wake delivery contracts."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from cubeloop.providers.base import TextContent, UserMessage
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.agents.checkpointer import _build_dsn, init_checkpointer
from cubeplex.models import (
    Conversation,
    SandboxCommand,
    SandboxCommandWake,
    SteeringMessage,
    User,
    UserSandbox,
)
from cubeplex.models.sandbox_command import SandboxCommandWakeState
from cubeplex.sandbox.command_coordinator import _claim_wakes, deliver_wakes_once
from cubeplex.streams.run_events import clear_active_run, create_run, get_run_meta
from tests.e2e.conftest import (
    DEFAULT_ORG_ID,
    DEFAULT_TEST_EMAIL,
    DEFAULT_WS_ID,
    _ensure_default_user_and_membership,
)

pytestmark = pytest.mark.e2e


async def _seed_wake(
    session: AsyncSession,
) -> tuple[SandboxCommand, SandboxCommandWake, Conversation]:
    await _ensure_default_user_and_membership()
    user = (
        await session.execute(select(User).where(User.email == DEFAULT_TEST_EMAIL))
    ).scalar_one()
    conversation = Conversation(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        creator_user_id=user.id,
        title="sandbox wake delivery",
    )
    session.add(conversation)
    await session.flush()
    sandbox = UserSandbox(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_id=user.id,
        scope_type="conversation",
        scope_id=conversation.id,
        status="running",
        image="test",
        provider="local",
    )
    session.add(sandbox)
    await session.flush()
    command = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=sandbox.id,
        conversation_id=conversation.id,
        run_id="run-wake-source",
        tool_call_id="tc-wake-source",
        started_by_user_id=user.id,
        command="watch predicate",
        description="watch predicate",
        provider="local",
        status="running",
        kind="monitor",
        lifetime="conversation",
    )
    session.add(command)
    await session.flush()
    wake = SandboxCommandWake(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        command_id=command.id,
        conversation_id=conversation.id,
        reason="line",
        dedupe_key=f"{command.id}:line:test",
        text_tail="FAILED",
        started_by_user_id=user.id,
    )
    session.add(wake)
    await session.commit()
    return command, wake, conversation


@pytest_asyncio.fixture
async def seeded_wake(
    db_session: AsyncSession,
) -> AsyncIterator[tuple[SandboxCommand, SandboxCommandWake, Conversation]]:
    command, wake, conversation = await _seed_wake(db_session)
    try:
        yield command, wake, conversation
    finally:
        # Updating the conversation may enqueue search-index work while the
        # delivery app is running. Remove those derived rows before their
        # parent conversation so this fixture stays repeatable.
        await db_session.execute(
            text("DELETE FROM embedding_jobs WHERE conversation_id = :conversation_id"),
            {"conversation_id": conversation.id},
        )
        await db_session.execute(
            text("DELETE FROM conversation_chunks WHERE conversation_id = :conversation_id"),
            {"conversation_id": conversation.id},
        )
        await db_session.execute(
            delete(SteeringMessage).where(
                SteeringMessage.conversation_id == conversation.id  # type: ignore[arg-type]
            )
        )
        await db_session.execute(
            delete(SandboxCommandWake).where(
                SandboxCommandWake.command_id == command.id  # type: ignore[arg-type]
            )
        )
        await db_session.execute(
            delete(SandboxCommand).where(SandboxCommand.id == command.id)  # type: ignore[arg-type]
        )
        await db_session.execute(
            delete(UserSandbox).where(UserSandbox.id == command.user_sandbox_id)  # type: ignore[arg-type]
        )
        await db_session.execute(
            delete(Conversation).where(Conversation.id == conversation.id)  # type: ignore[arg-type]
        )
        await db_session.commit()


async def _delete_checkpoint_thread(thread_id: str) -> None:
    import asyncpg

    connection = await asyncpg.connect(_build_dsn())
    try:
        await connection.execute("DELETE FROM cubepi_threads WHERE thread_id = $1", thread_id)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_wake_without_active_run_starts_once_then_reconciles_checkpoint(
    async_client: httpx.AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    seeded_wake: tuple[SandboxCommand, SandboxCommandWake, Conversation],
) -> None:
    command, wake, conversation = seeded_wake
    conversation.model_key = "wake-model"
    conversation.reasoning = {"mode": "on", "effort": "high"}
    db_session.add(conversation)
    await db_session.commit()
    app = async_client._transport.app  # type: ignore[attr-defined]
    run_manager = app.state.run_manager
    redis = app.state.redis
    prefix = app.state.redis_key_prefix
    checkpoint_written = asyncio.Event()
    release_provider = asyncio.Event()
    execution_kwargs: dict[str, Any] = {}

    async def _provider_boundary(**kwargs: Any) -> None:
        execution_kwargs.update(kwargs)
        metadata = kwargs["input_metadata"]
        async with init_checkpointer() as checkpointer:
            await checkpointer.append(
                kwargs["conversation_id"],
                [
                    UserMessage(
                        content=[TextContent(text=kwargs["content"])],
                        metadata=metadata,
                    )
                ],
            )
        checkpoint_written.set()
        await release_provider.wait()

    monkeypatch.setattr(run_manager, "_execute_run", _provider_boundary)
    started = datetime.now(UTC)
    try:
        first = await deliver_wakes_once(
            session_factory,
            run_manager=run_manager,
            redis=redis,
            redis_key_prefix=prefix,
            now=started,
        )
        await asyncio.wait_for(checkpoint_written.wait(), timeout=5)
        await db_session.refresh(command)
        assert command.notify_run_id is not None
        delivery_run_id = command.notify_run_id
        task = run_manager._tasks[delivery_run_id]
        release_provider.set()
        await task
        second = await deliver_wakes_once(
            session_factory,
            run_manager=run_manager,
            redis=redis,
            redis_key_prefix=prefix,
            now=started + timedelta(seconds=31),
        )
    finally:
        release_provider.set()
        if command.notify_run_id is not None:
            await clear_active_run(
                redis,
                prefix=prefix,
                conversation_id=conversation.id,
                run_id=command.notify_run_id,
            )
        await _delete_checkpoint_thread(conversation.id)

    assert first == []
    assert second == [wake.id]
    assert execution_kwargs["model_key"] == "wake-model"
    assert execution_kwargs["reasoning"].model_dump() == {
        "mode": "on",
        "effort": "high",
        "summary": "none",
    }
    assert execution_kwargs["ctx"].sender_display_name is None
    await db_session.refresh(wake)
    assert wake.state == SandboxCommandWakeState.delivered.value


@pytest.mark.asyncio
async def test_paused_hitl_defers_wakes_without_starving_newer(
    async_client: httpx.AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_wake: tuple[SandboxCommand, SandboxCommandWake, Conversation],
) -> None:
    _command, wake, conversation = seeded_wake
    app = async_client._transport.app  # type: ignore[attr-defined]
    run_manager = app.state.run_manager
    redis = app.state.redis
    prefix = app.state.redis_key_prefix
    tasks_before = set(run_manager._tasks)
    extra_wakes = [
        SandboxCommandWake(
            org_id=wake.org_id,
            workspace_id=wake.workspace_id,
            command_id=wake.command_id,
            conversation_id=wake.conversation_id,
            reason="line",
            dedupe_key=f"{wake.command_id}:line:paused-{index}",
            text_tail=f"paused-{index}",
            started_by_user_id=wake.started_by_user_id,
        )
        for index in range(20)
    ]
    db_session.add_all(extra_wakes)
    await db_session.commit()
    try:
        await create_run(
            redis,
            prefix=prefix,
            run_id="run-paused-wake",
            conversation_id=conversation.id,
            status="paused_hitl",
            started_at=datetime.now(UTC).isoformat(),
            ttl_seconds=60,
        )
        delivered = await deliver_wakes_once(
            session_factory,
            run_manager=run_manager,
            redis=redis,
            redis_key_prefix=prefix,
        )
        redelivered = await deliver_wakes_once(
            session_factory,
            run_manager=run_manager,
            redis=redis,
            redis_key_prefix=prefix,
        )
    finally:
        await clear_active_run(
            redis,
            prefix=prefix,
            conversation_id=conversation.id,
            run_id="run-paused-wake",
        )

    await db_session.refresh(wake)
    assert delivered == []
    assert redelivered == []
    assert wake.state == SandboxCommandWakeState.claimed.value
    wake_states = list(
        (
            await db_session.execute(
                select(SandboxCommandWake.state).where(
                    SandboxCommandWake.command_id == wake.command_id  # type: ignore[arg-type]
                )
            )
        ).scalars()
    )
    assert wake_states == [SandboxCommandWakeState.claimed.value] * 21
    assert set(run_manager._tasks) == tasks_before


@pytest.mark.asyncio
async def test_completion_wake_deduplicates_checkpointed_command_notice(
    async_client: httpx.AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_wake: tuple[SandboxCommand, SandboxCommandWake, Conversation],
) -> None:
    command, wake, conversation = seeded_wake
    command.notice_state = "pending"
    wake.reason = "completion"
    db_session.add_all([command, wake])
    await db_session.commit()
    async with init_checkpointer() as checkpointer:
        await checkpointer.append(
            conversation.id,
            [
                UserMessage(
                    content=[TextContent(text="completion already delivered")],
                    metadata={"notice_id": command.id},
                )
            ],
        )

    app = async_client._transport.app  # type: ignore[attr-defined]
    run_manager = app.state.run_manager
    tasks_before = set(run_manager._tasks)
    try:
        delivered = await deliver_wakes_once(
            session_factory,
            run_manager=run_manager,
            redis=app.state.redis,
            redis_key_prefix=app.state.redis_key_prefix,
        )
    finally:
        await _delete_checkpoint_thread(conversation.id)

    await db_session.refresh(command)
    await db_session.refresh(wake)
    assert delivered == [wake.id]
    assert wake.state == SandboxCommandWakeState.delivered.value
    assert command.notice_state == "delivered"
    assert set(run_manager._tasks) == tasks_before


@pytest.mark.asyncio
@pytest.mark.parametrize("track_delivery_run", [False, True])
async def test_stale_active_run_is_replaced_without_waiting_for_redis_ttl(
    async_client: httpx.AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    seeded_wake: tuple[SandboxCommand, SandboxCommandWake, Conversation],
    track_delivery_run: bool,
) -> None:
    _command, wake, conversation = seeded_wake
    app = async_client._transport.app  # type: ignore[attr-defined]
    run_manager = app.state.run_manager
    redis = app.state.redis
    prefix = app.state.redis_key_prefix
    stale_run_id = "run-stale-sandbox-wake"
    if track_delivery_run:
        wake.delivery_run_id = stale_run_id
    db_session.add(wake)
    await db_session.commit()
    await create_run(
        redis,
        prefix=prefix,
        run_id=stale_run_id,
        conversation_id=conversation.id,
        status="running",
        started_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        ttl_seconds=3600,
    )
    replacement_started = asyncio.Event()
    release_provider = asyncio.Event()

    async def _provider_boundary(**_kwargs: Any) -> None:
        replacement_started.set()
        await release_provider.wait()

    monkeypatch.setattr(run_manager, "_execute_run", _provider_boundary)
    replacement_run_id: str | None = None
    try:
        delivered = await deliver_wakes_once(
            session_factory,
            run_manager=run_manager,
            redis=redis,
            redis_key_prefix=prefix,
        )
        await asyncio.wait_for(replacement_started.wait(), timeout=5)
        await db_session.refresh(wake)
        replacement_run_id = wake.delivery_run_id
        stale_meta = await get_run_meta(redis, prefix=prefix, run_id=stale_run_id)
        assert stale_meta is not None
        assert stale_meta.status == "stale"
        assert replacement_run_id not in (None, stale_run_id)
    finally:
        release_provider.set()
        if replacement_run_id is not None:
            task = run_manager._tasks.get(replacement_run_id)
            if task is not None:
                await task
            await clear_active_run(
                redis,
                prefix=prefix,
                conversation_id=conversation.id,
                run_id=replacement_run_id,
            )

    assert delivered == []


@pytest.mark.asyncio
async def test_wake_is_dropped_after_conversation_access_is_revoked(
    async_client: httpx.AsyncClient,
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_wake: tuple[SandboxCommand, SandboxCommandWake, Conversation],
) -> None:
    _command, wake, conversation = seeded_wake
    replacement_creator = User(
        email=f"replacement-{wake.id}@example.com",
        hashed_password="not-used",
    )
    db_session.add(replacement_creator)
    await db_session.flush()
    conversation.creator_user_id = replacement_creator.id
    db_session.add(conversation)
    await db_session.commit()

    app = async_client._transport.app  # type: ignore[attr-defined]
    run_manager = app.state.run_manager
    tasks_before = set(run_manager._tasks)
    delivered = await deliver_wakes_once(
        session_factory,
        run_manager=run_manager,
        redis=app.state.redis,
        redis_key_prefix=app.state.redis_key_prefix,
    )

    await db_session.refresh(wake)
    assert delivered == [wake.id]
    assert wake.state == SandboxCommandWakeState.delivered.value
    assert set(run_manager._tasks) == tasks_before
    conversation.creator_user_id = wake.started_by_user_id
    db_session.add(conversation)
    await db_session.commit()
    await db_session.delete(replacement_creator)
    await db_session.commit()


@pytest.mark.asyncio
async def test_two_coordinators_cannot_claim_same_wake(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_wake: tuple[SandboxCommand, SandboxCommandWake, Conversation],
) -> None:
    _command, wake, _conversation = seeded_wake
    now = datetime.now(UTC)

    async def _claim(owner_id: str) -> list[SandboxCommandWake]:
        async with session_factory() as session:
            return await _claim_wakes(
                session,
                owner_id=owner_id,
                owner_until=now + timedelta(seconds=30),
                now=now,
            )

    first, second = await asyncio.gather(_claim("coordinator-a"), _claim("coordinator-b"))
    assert [row.id for row in first + second] == [wake.id]
