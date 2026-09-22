"""Durable source retries must not run the model twice, even without Redis history."""

import asyncio
import inspect
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from cubeloop.hitl.types import AskRequest, HitlRequest, Question
from cubeloop.providers.base import AssistantMessage, Message, Model
from cubeloop.providers.faux import FauxProvider, faux_assistant_message, faux_text, faux_tool_call
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.agents.checkpointer import close_shared_checkpointer, shared_checkpointer
from cubeplex.config import config
from cubeplex.db.engine import engine
from cubeplex.models import ConversationExecutionAdmission
from cubeplex.services.conversation_execution import (
    ExecutionConflictError,
    ExecutionRevokedError,
    RunExecutionBinding,
    UserMessageIntent,
)
from cubeplex.streams.run_events import (
    _CLAIM_MATCHES_LUA,
    _active_run_key,
    _run_meta_key,
    create_run,
    get_active_run,
    get_run_meta,
    mark_run_stale,
)
from cubeplex.streams.run_manager import RunContext, RunManager
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.conftest import _SYNC_ENCRYPTION_BACKEND, DEFAULT_ORG_ID, DEFAULT_WS_ID
from tests.e2e.test_background_task_reservation import ReservationContext
from tests.e2e.test_conversation_execution_control import actor_id, service, snapshot

reservation_context = reservation_fixtures.reservation_context


async def cleanup_run_rows(session: AsyncSession, conversation_id: str) -> None:
    await session.rollback()
    await session.execute(
        text("DELETE FROM cubepi_threads WHERE thread_id = :thread"),
        {"thread": conversation_id},
    )
    await session.execute(
        text(
            "DELETE FROM billing_llm_events WHERE billing_event_id IN "
            "(SELECT id FROM billing_events WHERE conversation_id = :conversation)"
        ),
        {"conversation": conversation_id},
    )
    for table in ("billing_events", "embedding_jobs", "conversation_chunks"):
        await session.execute(
            text(f"DELETE FROM {table} WHERE conversation_id = :conversation"),
            {"conversation": conversation_id},
        )
    await session.commit()


@pytest_asyncio.fixture
async def run_manager() -> AsyncIterator[RunManager]:
    redis_client = Redis.from_url(config.get("redis.url"), decode_responses=True)
    app = FastAPI()
    app.state.encryption_backend = _SYNC_ENCRYPTION_BACKEND
    app.state.sandbox_factory = None
    prefix = f"test-admitted-run:{uuid4()}"
    manager = RunManager(app=app, redis=redis_client, key_prefix=prefix, run_event_ttl_seconds=60)
    try:
        yield manager
    finally:
        await manager.cancel_all()
        extras = [*manager._reflection_tasks, *manager._consolidation_tasks]
        for task in extras:
            task.cancel()
        await asyncio.gather(*extras, return_exceptions=True)
        keys = [key async for key in redis_client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await redis_client.delete(*keys)
        http = getattr(app.state, "_mcp_oauth_http_client", None)
        if http is not None:
            await http.aclose()
        await close_shared_checkpointer()
        await engine.dispose()
        await redis_client.aclose()


@pytest.mark.parametrize("mismatch", ["content", "run_id", "actor", "conversation"])
async def test_run_manager_rejects_changed_admitted_identity_before_claiming_redis(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    mismatch: str,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    admitted = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="once"),
        snapshot=snapshot(),
        now=datetime.now(UTC),
    )
    await db_session.commit()
    assert "admission_id" in inspect.signature(run_manager.start_run).parameters
    ctx = RunContext(
        user_id="wrong-actor" if mismatch == "actor" else actor,
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=reservation_context.conversation_id,
    )
    with pytest.raises((ExecutionConflictError, LookupError)):
        await run_manager.start_run(
            conversation_id="wrong-conversation"
            if mismatch == "conversation"
            else reservation_context.conversation_id,
            content="different" if mismatch == "content" else "once",
            ctx=ctx,
            run_id="wrong-run" if mismatch == "run_id" else admitted.admission.run_id,
            admission_id=admitted.admission.id,
            llm_snapshot=snapshot(),
        )
    assert not run_manager._tasks
    await db_session.refresh(admitted.admission)
    assert admitted.admission.run_start_token is None


@pytest.mark.parametrize(
    "scenario",
    [
        "completed",
        "stop",
        "stop_final",
        "stale_slot",
        "concurrent",
        "paused",
        "replaced_claim",
        "replaced_slot",
        "lost_slot",
        "foreign_pending",
        "terminal_reply_lost",
        "terminal_cancel",
        "next_send",
        "stop_cleanup_local",
        "stop_cleanup_remote",
    ],
)
async def test_model_runs_once_with_original_selection_after_default_changes_and_redis_expires(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    lost_ownership = scenario in ("replaced_claim", "replaced_slot", "lost_slot")
    replacement_run_id = str(uuid4())
    initial = snapshot()
    provider_config = initial.providers["provider"].model_copy(deep=True)
    for model in provider_config.models:
        model.context_window = 128_000
        model.max_tokens = 4096
    initial = replace(initial, providers={"provider": provider_config})
    current = replace(initial, model_presets=snapshot("next").model_presets)
    admitted = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="one answer only"),
        snapshot=initial,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    assert "admission_id" in inspect.signature(run_manager.start_run).parameters
    provider = FauxProvider(provider_id="provider")
    models: list[str] = []

    def capture_request(payload: dict[str, Any], model: Model) -> None:
        models.append(model.id)

    provider.subscribe_request(capture_request)

    async def model_response(messages: list[Message], model: Model) -> AssistantMessage:
        if scenario == "foreign_pending":
            async with shared_checkpointer() as cp:
                await cp.save_pending_request(
                    reservation_context.conversation_id,
                    HitlRequest(
                        question_id="replacement-question",
                        thread_id=reservation_context.conversation_id,
                        payload=AskRequest(questions=[Question(key="new", prompt="Keep this?")]),
                        created_at=datetime.now(UTC).timestamp(),
                        timeout_seconds=None,
                    ),
                    run_id=replacement_run_id,
                )
        if scenario == "paused":
            return faux_assistant_message(
                faux_tool_call(
                    "ask_user", {"questions": [{"key": "choice", "prompt": "Continue?"}]}
                ),
                stop_reason="tool_use",
            )
        if scenario in ("stop", "stop_final") or lost_ownership:
            if scenario in ("stop", "stop_final"):
                await service(db_session).close_generation(
                    conversation_id=reservation_context.conversation_id,
                    actor_user_id=actor,
                    execution_generation=0,
                    now=datetime.now(UTC),
                )
                await db_session.commit()
                if scenario == "stop_final":
                    return faux_assistant_message(
                        [faux_text("late final answer")], stop_reason="stop"
                    )
            elif scenario == "replaced_claim":
                await run_manager._redis.hset(
                    _run_meta_key(run_manager._key_prefix, admitted.admission.run_id),
                    "claim_token",
                    "replacement-worker",
                )
            elif scenario == "lost_slot":
                await run_manager._redis.delete(
                    _active_run_key(run_manager._key_prefix, reservation_context.conversation_id)
                )
            else:
                assert await mark_run_stale(
                    run_manager._redis,
                    prefix=run_manager._key_prefix,
                    conversation_id=reservation_context.conversation_id,
                    run_id=admitted.admission.run_id,
                )
                assert await create_run(
                    run_manager._redis,
                    prefix=run_manager._key_prefix,
                    conversation_id=reservation_context.conversation_id,
                    run_id=replacement_run_id,
                    claim_token="replacement-worker",
                    status="running",
                    started_at=datetime.now(UTC).isoformat(),
                    ttl_seconds=60,
                )
            return faux_assistant_message(
                faux_tool_call(
                    "write_todos",
                    {"todos": [{"content": "must not execute after Stop", "status": "completed"}]},
                ),
                stop_reason="tool_use",
            )
        return faux_assistant_message([faux_text("done once")], stop_reason="stop")

    provider.set_responses([model_response])
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    ctx = RunContext(
        user_id=actor,
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=reservation_context.conversation_id,
        is_group_chat=True,
    )
    kwargs = {
        "conversation_id": reservation_context.conversation_id,
        "content": "one answer only",
        "ctx": ctx,
        "run_id": admitted.admission.run_id,
        "admission_id": admitted.admission.id,
        "llm_snapshot": current,
    }
    reply_lost = False
    next_send_claimed = False
    cleanup_entered, release_cleanup = asyncio.Event(), asyncio.Event()
    control_tasks: list[asyncio.Task[None]] = []
    if scenario in ("stop_cleanup_local", "stop_cleanup_remote"):
        original_eval = run_manager._redis.eval

        async def hold_cleanup_claim_check(script: str, numkeys: int, *args: Any) -> Any:
            result = await original_eval(script, numkeys, *args)
            if script == _CLAIM_MATCHES_LUA and not cleanup_entered.is_set():
                meta = await get_run_meta(
                    run_manager._redis,
                    prefix=run_manager._key_prefix,
                    run_id=str(admitted.admission.run_id),
                )
                if meta is not None and meta.status == "completed":
                    cleanup_entered.set()
                    await release_cleanup.wait()
            return result

        monkeypatch.setattr(run_manager._redis, "eval", hold_cleanup_claim_check)
    if scenario in ("terminal_reply_lost", "terminal_cancel", "next_send"):
        original_eval = run_manager._redis.eval

        async def lose_terminal_reply(script: str, numkeys: int, *args: Any) -> Any:
            nonlocal reply_lost, next_send_claimed
            result = await original_eval(script, numkeys, *args)
            if not reply_lost and "completed" in args:
                reply_lost = True
                if scenario == "next_send":
                    next_send_claimed = await create_run(
                        run_manager._redis,
                        prefix=run_manager._key_prefix,
                        conversation_id=reservation_context.conversation_id,
                        run_id=replacement_run_id,
                        claim_token="next-send",
                        status="running",
                        started_at=datetime.now(UTC).isoformat(),
                        ttl_seconds=60,
                    )
                    return result
                if scenario == "terminal_cancel":
                    raise asyncio.CancelledError("worker cancelled after terminal commit")
                raise ConnectionError("terminal write committed, Redis response lost")
            return result

        monkeypatch.setattr(run_manager._redis, "eval", lose_terminal_reply)
    try:
        if scenario == "stale_slot":
            assert admitted.admission.run_id is not None
            assert await create_run(
                run_manager._redis,
                prefix=run_manager._key_prefix,
                run_id=admitted.admission.run_id,
                conversation_id=reservation_context.conversation_id,
                status="running",
                started_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
                ttl_seconds=60,
            )
        if scenario == "concurrent":
            run_id, duplicate_id = await asyncio.gather(
                run_manager.start_run(**kwargs), run_manager.start_run(**kwargs)
            )
            assert duplicate_id == run_id
        else:
            run_id = await run_manager.start_run(**kwargs)
        if scenario in ("stop_cleanup_local", "stop_cleanup_remote"):
            async with asyncio.timeout(15):
                await cleanup_entered.wait()
            await service(db_session).stop_run(
                conversation_id=reservation_context.conversation_id,
                run_id=run_id,
                actor_user_id=actor,
                now=datetime.now(UTC),
            )
            await db_session.commit()
            for _ in range(2):
                if scenario == "stop_cleanup_local":
                    await run_manager.notify_run_stop(run_id)
                else:
                    dispatched = asyncio.Event()

                    async def deliver(started: asyncio.Event) -> None:
                        started.set()
                        await run_manager._handle_control({"run_id": run_id, "type": "cancel"})

                    control_tasks.append(asyncio.create_task(deliver(dispatched)))
                    await dispatched.wait()
            release_cleanup.set()
        await run_manager.drain(timeout_seconds=30)
        await asyncio.gather(*control_tasks)
        meta = await get_run_meta(run_manager._redis, prefix=run_manager._key_prefix, run_id=run_id)
        assert meta is not None
        expected_status = {
            "stop": "cancelled",
            "stop_final": "cancelled",
            "paused": "paused_hitl",
            "replaced_claim": "running",
            "replaced_slot": "stale",
            "lost_slot": "running",
        }.get(scenario, "completed")
        assert meta.status == expected_status, meta
        if scenario in ("terminal_reply_lost", "terminal_cancel"):
            assert reply_lost
        if scenario in (
            "terminal_reply_lost",
            "terminal_cancel",
            "stop_cleanup_local",
            "stop_cleanup_remote",
        ):
            assert (
                await get_active_run(
                    run_manager._redis,
                    prefix=run_manager._key_prefix,
                    conversation_id=reservation_context.conversation_id,
                )
                is None
            )
            assert not run_manager._cleanup_tasks
        assert provider.call_count == 1
        if scenario == "foreign_pending":
            async with shared_checkpointer() as cp:
                pending = await cp.load_pending(reservation_context.conversation_id)
            assert pending is not None and pending[1] == replacement_run_id
            assert pending[0].question_id == "replacement-question"
        if scenario == "stop" or lost_ownership:
            async with shared_checkpointer() as cp:
                checkpoint = await cp.load(reservation_context.conversation_id)
            assert checkpoint is not None and not checkpoint.extra.get("todos")
        if lost_ownership:
            active = await get_active_run(
                run_manager._redis,
                prefix=run_manager._key_prefix,
                conversation_id=reservation_context.conversation_id,
            )
            if scenario == "lost_slot":
                assert active is None
            else:
                assert active is not None and active.status == "running"
                assert active.run_id == (
                    replacement_run_id if scenario == "replaced_slot" else run_id
                )
        await db_session.refresh(admitted.admission)
        if scenario == "next_send":
            assert not next_send_claimed, "next send replaced an owner before its finish receipt"
            assert reply_lost
        assert admitted.admission.run_start_requested_at is not None
        assert admitted.admission.run_started_at is not None
        assert (admitted.admission.run_finished_at is None) == (
            scenario == "paused" or lost_ownership
        )
        assert models == ["first"]
        await db_session.rollback()
        keys = [
            key async for key in run_manager._redis.scan_iter(match=f"{run_manager._key_prefix}:*")
        ]
        if keys:
            await run_manager._redis.delete(*keys)
        unavailable = snapshot("next", available=("next",))
        assert await run_manager.start_run(**{**kwargs, "llm_snapshot": unavailable}) == run_id
        assert not run_manager._tasks and provider.call_count == 1
        receipt = await db_session.get(ConversationExecutionAdmission, kwargs["admission_id"])
        assert receipt is not None
        assert (receipt.run_finished_at is None) == (scenario == "paused" or lost_ownership)
        retried = await service(db_session).admit_user_message(
            conversation_id=reservation_context.conversation_id,
            actor_user_id=actor,
            namespace="web",
            source_id=receipt.source_id.removeprefix("web:"),
            intent=UserMessageIntent(content="one answer only"),
            snapshot=unavailable,
            now=datetime.now(UTC),
        )
        assert retried.admission.run_id == run_id and not retried.created
    finally:
        release_cleanup.set()
        for control_task in control_tasks:
            control_task.cancel()
        await asyncio.gather(*control_tasks, return_exceptions=True)
        await run_manager.cancel_all()
        await cleanup_run_rows(db_session, reservation_context.conversation_id)


async def test_stop_before_worker_entry_finishes_cleanup_without_claiming_execution(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    admitted = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="cancel before entry"),
        snapshot=snapshot(),
        now=datetime.now(UTC),
    )
    assert await service(db_session).claim_run_start(
        admission_id=admitted.admission.id, attempt_id="queued-worker", now=datetime.now(UTC)
    )
    await db_session.commit()
    run_id = admitted.admission.run_id
    assert run_id is not None
    assert await create_run(
        run_manager._redis,
        prefix=run_manager._key_prefix,
        run_id=run_id,
        conversation_id=reservation_context.conversation_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
        claim_token="queued-worker",
    )
    ctx = RunContext(
        user_id=actor,
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=reservation_context.conversation_id,
        execution=RunExecutionBinding(
            admission_id=admitted.admission.id,
            attempt_id="queued-worker",
            start_token="queued-worker",
            execution_generation=0,
            execution=admitted.execution,
        ),
    )
    closed = await service(db_session).close_generation(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        execution_generation=0,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    assert closed.cleanup_pending
    provider = FauxProvider(provider_id="provider")
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    try:
        worker = asyncio.create_task(
            run_manager._execute_run(
                run_id=run_id,
                conversation_id=reservation_context.conversation_id,
                content="cancel before entry",
                attachments=[],
                ctx=ctx,
                llm_snapshot=snapshot(),
            )
        )
        await asyncio.gather(worker, return_exceptions=True)
        meta = await get_run_meta(run_manager._redis, prefix=run_manager._key_prefix, run_id=run_id)
        assert meta is not None and meta.status == "cancelled", meta
        assert provider.call_count == 0
        await db_session.refresh(admitted.admission)
        assert admitted.admission.run_started_at is None
        assert admitted.admission.run_finished_at is not None
        closed = await service(db_session).close_generation(
            conversation_id=reservation_context.conversation_id,
            actor_user_id=actor,
            execution_generation=0,
            now=datetime.now(UTC),
        )
        # The fixture also has an independent, never-started admitted run.
        assert closed.cleanup_pending
        assert closed.run_ids == (reservation_context.spec.originating_run_id,)
        assert run_id not in closed.run_ids
    finally:
        await cleanup_run_rows(db_session, reservation_context.conversation_id)


@pytest.mark.parametrize("revoked", [False, True])
async def test_admitted_start_cannot_cancel_another_runs_pending_question(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
    revoked: bool,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    conversation_id = reservation_context.conversation_id
    admitted = await service(db_session).admit_user_message(
        conversation_id=conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="delayed request"),
        snapshot=snapshot(),
        now=datetime.now(UTC),
    )
    if revoked:
        await service(db_session).close_generation(
            conversation_id=conversation_id,
            actor_user_id=actor,
            execution_generation=0,
            now=datetime.now(UTC),
        )
        replacement = await service(db_session).admit_user_message(
            conversation_id=conversation_id,
            actor_user_id=actor,
            namespace="web",
            source_id=str(uuid4()),
            intent=UserMessageIntent(content="new generation"),
            snapshot=snapshot(),
            now=datetime.now(UTC),
        )
        assert replacement.admission.execution_generation == 1
        pending_run_id = replacement.admission.run_id
    else:
        pending_run_id = str(uuid4())
    await db_session.commit()
    provider = FauxProvider(provider_id="provider")
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    try:
        async with shared_checkpointer() as cp:
            await cp.save_pending_request(
                conversation_id,
                HitlRequest(
                    question_id="keep-question",
                    thread_id=conversation_id,
                    payload=AskRequest(questions=[Question(key="choice", prompt="Continue?")]),
                    created_at=datetime.now(UTC).timestamp(),
                    timeout_seconds=None,
                ),
                run_id=pending_run_id,
            )
        result: str | None = None
        rejection: Exception | None = None
        try:
            result = await run_manager.start_run(
                conversation_id=conversation_id,
                content="delayed request",
                ctx=RunContext(
                    user_id=actor,
                    org_id=DEFAULT_ORG_ID,
                    workspace_id=DEFAULT_WS_ID,
                    conversation_id=conversation_id,
                ),
                run_id=admitted.admission.run_id,
                admission_id=admitted.admission.id,
                llm_snapshot=snapshot("next", available=("next",)) if revoked else snapshot(),
                cancel_pending_hitl=True,
            )
        except (ExecutionRevokedError, RuntimeError) as exc:
            rejection = exc
        async with shared_checkpointer() as cp:
            pending = await cp.load_pending(conversation_id)
        assert pending is not None, "starting a message must not erase another run's question"
        assert (pending[0].question_id, pending[1]) == ("keep-question", pending_run_id)
        assert not run_manager._tasks and provider.call_count == 0
        assert (
            await get_active_run(
                run_manager._redis, prefix=run_manager._key_prefix, conversation_id=conversation_id
            )
            is None
        )
        if revoked:
            assert result == admitted.admission.run_id and rejection is None
        else:
            assert isinstance(rejection, RuntimeError) and "pending HITL" in str(rejection)
    finally:
        await run_manager.cancel_all()
        await cleanup_run_rows(db_session, conversation_id)


async def test_late_model_response_cannot_overwrite_replacement_checkpoint(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    conversation_id = reservation_context.conversation_id
    current = snapshot()
    provider_config = current.providers["provider"].model_copy(deep=True)
    for model in provider_config.models:
        model.context_window = 128_000
        model.max_tokens = 4096
    current = replace(current, providers={"provider": provider_config})
    ctx = RunContext(
        user_id=actor,
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=conversation_id,
        is_group_chat=True,
    )
    provider = FauxProvider(provider_id="provider")
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def old_response(messages: list[Message], model: Model) -> AssistantMessage:
        entered.set()
        await release.wait()
        return faux_assistant_message([faux_text("late old response")], stop_reason="stop")

    provider.set_responses(
        [
            old_response,
            faux_assistant_message(
                faux_tool_call(
                    "write_todos",
                    {"todos": [{"content": "replacement work", "status": "completed"}]},
                ),
                stop_reason="tool_use",
            ),
            faux_assistant_message([faux_text("replacement complete")], stop_reason="stop"),
        ]
    )

    async def start(content: str) -> str:
        admitted = await service(db_session).admit_user_message(
            conversation_id=conversation_id,
            actor_user_id=actor,
            namespace="web",
            source_id=str(uuid4()),
            intent=UserMessageIntent(content=content),
            snapshot=current,
            now=datetime.now(UTC),
        )
        await db_session.commit()
        return await run_manager.start_run(
            conversation_id=conversation_id,
            content=content,
            ctx=ctx,
            run_id=admitted.admission.run_id,
            admission_id=admitted.admission.id,
            llm_snapshot=current,
        )

    try:
        old_run_id = await start("old work")
        old_worker = run_manager._tasks[old_run_id]
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert await mark_run_stale(
            run_manager._redis,
            prefix=run_manager._key_prefix,
            conversation_id=conversation_id,
            run_id=old_run_id,
        )
        replacement_id = await start("replacement work")
        await asyncio.wait_for(asyncio.shield(run_manager._tasks[replacement_id]), timeout=15)
        replacement_meta = await get_run_meta(
            run_manager._redis, prefix=run_manager._key_prefix, run_id=replacement_id
        )
        assert replacement_meta is not None and replacement_meta.status == "completed"
        async with shared_checkpointer() as cp:
            completed = await cp.load(conversation_id)
        assert completed is not None and completed.extra.get("todos")
        release.set()
        await asyncio.wait_for(asyncio.gather(old_worker, return_exceptions=True), timeout=10)
        async with shared_checkpointer() as cp:
            after_old_exit = await cp.load(conversation_id)
        assert after_old_exit is not None
        assert after_old_exit.extra == completed.extra
        assert after_old_exit.messages == completed.messages
        assert provider.call_count == 3
    finally:
        release.set()
        await run_manager.cancel_all()
        await cleanup_run_rows(db_session, conversation_id)
