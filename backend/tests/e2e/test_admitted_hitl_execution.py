"""A paused run keeps its original execution authority across human responses."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from cubeloop.providers.base import AssistantMessage, Message, Model
from cubeloop.providers.faux import FauxProvider, faux_assistant_message, faux_text, faux_tool_call
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.agents.checkpointer import shared_checkpointer
from cubeplex.llm.snapshot import LLMSnapshot
from cubeplex.models import ConversationParticipant, Membership, User
from cubeplex.models.billing import BillingEvent
from cubeplex.services.conversation_execution import AdmittedExecution, UserMessageIntent
from cubeplex.streams.run_events import _active_run_key, _run_meta_key, get_active_run, get_run_meta
from cubeplex.streams.run_manager import ResumeConflict, RunContext, RunManager
from tests.e2e import test_admitted_run_execution as run_fixtures
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID
from tests.e2e.test_admitted_run_execution import cleanup_run_rows
from tests.e2e.test_background_task_reservation import ReservationContext
from tests.e2e.test_conversation_execution_control import actor_id, service, snapshot

run_manager = run_fixtures.run_manager
reservation_context = reservation_fixtures.reservation_context


@dataclass
class PausedExecution:
    admitted: AdmittedExecution
    snapshot: LLMSnapshot
    provider: FauxProvider
    models: list[str]
    ctx: RunContext
    run_id: str
    question_id: str


@pytest_asyncio.fixture
async def paused_execution(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[PausedExecution]:
    actor = await actor_id(db_session, reservation_context)
    conversation_id = reservation_context.conversation_id
    current = snapshot()
    provider_config = current.providers["provider"].model_copy(deep=True)
    for model in provider_config.models:
        model.context_window = 128_000
        model.max_tokens = 4096
    current = replace(current, providers={"provider": provider_config})
    admitted = await service(db_session).admit_user_message(
        conversation_id=conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="ask before proceeding"),
        snapshot=current,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    provider = FauxProvider(provider_id="provider")
    provider.set_responses(
        [
            faux_assistant_message(
                faux_tool_call(
                    "ask_user", {"questions": [{"key": "choice", "prompt": "Continue?"}]}
                ),
                stop_reason="tool_use",
            ),
            faux_assistant_message([faux_text("answered once")], stop_reason="stop"),
        ]
    )
    models: list[str] = []

    def capture_request(payload: dict[str, object], model: Model) -> None:
        models.append(model.id)

    provider.subscribe_request(capture_request)
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    ctx = RunContext(
        user_id=actor,
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=conversation_id,
        is_group_chat=True,
    )
    try:
        run_id = await run_manager.start_run(
            conversation_id=conversation_id,
            content="ask before proceeding",
            ctx=ctx,
            run_id=admitted.admission.run_id,
            admission_id=admitted.admission.id,
            llm_snapshot=current,
        )
        await run_manager.drain(timeout_seconds=15)
        meta = await get_run_meta(run_manager._redis, prefix=run_manager._key_prefix, run_id=run_id)
        assert meta is not None and meta.status == "paused_hitl"
        async with shared_checkpointer() as cp:
            pending = await cp.load_pending(conversation_id)
        assert pending is not None
        yield PausedExecution(
            admitted, current, provider, models, ctx, run_id, pending[0].question_id
        )
    finally:
        await run_manager.cancel_all()
        await cleanup_run_rows(db_session, conversation_id)


async def respond(manager: RunManager, paused: PausedExecution, action: str = "answer") -> None:
    changed_default = replace(paused.snapshot, model_presets=snapshot("next").model_presets)
    if action == "answer":
        await manager.resume_run_with_answer(
            conversation_id=paused.ctx.conversation_id,
            run_id=paused.run_id,
            question_id=paused.question_id,
            answer={"choice": "yes"},
            ctx=paused.ctx,
            llm_snapshot=changed_default,
        )
    else:
        await manager.cancel_paused_run(
            conversation_id=paused.ctx.conversation_id,
            run_id=paused.run_id,
            ctx=paused.ctx,
            llm_snapshot=changed_default,
        )


@pytest.mark.parametrize("action", ["answer", "cancel"])
@pytest.mark.parametrize("stopped", [False, True])
async def test_resume_keeps_original_authority_and_model_after_redis_pause_expires(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
    action: str,
    stopped: bool,
) -> None:
    paused = paused_execution
    conversation_id = paused.ctx.conversation_id
    async with shared_checkpointer() as cp:
        pending = await cp.load_pending(conversation_id)
    if stopped:
        await service(db_session).close_generation(
            conversation_id=conversation_id,
            actor_user_id=paused.ctx.user_id,
            execution_generation=0,
            now=datetime.now(UTC),
        )
        await db_session.commit()
    keys = [key async for key in run_manager._redis.scan_iter(match=f"{run_manager._key_prefix}:*")]
    if keys:
        await run_manager._redis.delete(*keys)
    if stopped:
        with pytest.raises(ResumeConflict):
            await respond(run_manager, paused, action)
        assert not run_manager._tasks and paused.provider.call_count == 1
    else:
        await respond(run_manager, paused, action)
        await run_manager.drain(timeout_seconds=15)
        meta = await get_run_meta(
            run_manager._redis, prefix=run_manager._key_prefix, run_id=paused.run_id
        )
        assert meta is not None and meta.status == "completed", meta
        assert paused.models == ["first", "first"]
        await db_session.refresh(paused.admitted.admission)
        assert paused.admitted.admission.run_finished_at is not None
    assert (
        await get_active_run(
            run_manager._redis, prefix=run_manager._key_prefix, conversation_id=conversation_id
        )
        is None
    )
    async with shared_checkpointer() as cp:
        assert await cp.load_pending(conversation_id) == (pending if stopped else None)


@pytest.mark.parametrize("action", ["answer", "cancel"])
@pytest.mark.parametrize("mismatch", ["run", "context"])
async def test_resume_rejects_a_question_bound_to_another_identity(
    run_manager: RunManager, paused_execution: PausedExecution, action: str, mismatch: str
) -> None:
    paused = paused_execution
    async with shared_checkpointer() as cp:
        original = await cp.load_pending(paused.ctx.conversation_id)
    kwargs = {
        "conversation_id": paused.ctx.conversation_id,
        "run_id": str(uuid4()) if mismatch == "run" else paused.run_id,
        "ctx": replace(paused.ctx, conversation_id="wrong")
        if mismatch == "context"
        else paused.ctx,
    }
    with pytest.raises(ResumeConflict):
        if action == "answer":
            await run_manager.resume_run_with_answer(
                **kwargs, question_id=paused.question_id, answer={"choice": "yes"}
            )
        else:
            await run_manager.cancel_paused_run(**kwargs)
    assert not run_manager._tasks and paused.provider.call_count == 1
    async with shared_checkpointer() as cp:
        assert await cp.load_pending(paused.ctx.conversation_id) == original


@pytest.mark.parametrize("loss", ["stop", "claim", "slot"])
async def test_resume_losing_authority_cannot_execute_the_next_tool(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
    loss: str,
) -> None:
    paused = paused_execution

    async def model_response(messages: list[Message], model: Model) -> AssistantMessage:
        if loss == "stop":
            await service(db_session).close_generation(
                conversation_id=paused.ctx.conversation_id,
                actor_user_id=paused.ctx.user_id,
                execution_generation=0,
                now=datetime.now(UTC),
            )
            await db_session.commit()
        elif loss == "claim":
            await run_manager._redis.hset(
                _run_meta_key(run_manager._key_prefix, paused.run_id), "claim_token", "replacement"
            )
        else:
            await run_manager._redis.delete(
                _active_run_key(run_manager._key_prefix, paused.ctx.conversation_id)
            )
        return faux_assistant_message(
            faux_tool_call(
                "write_todos", {"todos": [{"content": "must not execute", "status": "completed"}]}
            ),
            stop_reason="tool_use",
        )

    paused.provider.set_responses([model_response])
    await respond(run_manager, paused)
    await run_manager.drain(timeout_seconds=15)
    assert paused.provider.call_count == 2
    async with shared_checkpointer() as cp:
        checkpoint = await cp.load(paused.ctx.conversation_id)
    assert checkpoint is not None and not checkpoint.extra.get("todos")
    meta = await get_run_meta(
        run_manager._redis, prefix=run_manager._key_prefix, run_id=paused.run_id
    )
    assert meta is not None and meta.status == ("cancelled" if loss == "stop" else "running"), meta
    await db_session.refresh(paused.admitted.admission)
    assert (paused.admitted.admission.run_finished_at is not None) == (loss == "stop")


async def test_second_hitl_pause_keeps_the_original_receipt_unfinished(
    db_session: AsyncSession, run_manager: RunManager, paused_execution: PausedExecution
) -> None:
    paused = paused_execution
    await db_session.refresh(paused.admitted.admission)
    original_token = paused.admitted.admission.run_start_token
    original_started_at = paused.admitted.admission.run_started_at
    paused.provider.set_responses(
        [
            faux_assistant_message(
                faux_tool_call(
                    "ask_user", {"questions": [{"key": "second", "prompt": "One more?"}]}
                ),
                stop_reason="tool_use",
            ),
            faux_assistant_message([faux_text("finished after both answers")], stop_reason="stop"),
        ]
    )
    await respond(run_manager, paused)
    await run_manager.drain(timeout_seconds=15)
    await db_session.refresh(paused.admitted.admission)
    assert paused.admitted.admission.run_finished_at is None
    async with shared_checkpointer() as cp:
        second = await cp.load_pending(paused.ctx.conversation_id)
    assert second is not None and second[1] == paused.run_id
    assert second[0].question_id != paused.question_id
    await run_manager.resume_run_with_answer(
        conversation_id=paused.ctx.conversation_id,
        run_id=paused.run_id,
        question_id=second[0].question_id,
        answer={"second": "yes"},
        ctx=paused.ctx,
        llm_snapshot=paused.snapshot,
    )
    await run_manager.drain(timeout_seconds=15)
    await db_session.refresh(paused.admitted.admission)
    assert paused.admitted.admission.run_finished_at is not None
    assert paused.admitted.admission.run_start_token == original_token
    assert paused.admitted.admission.run_started_at == original_started_at
    assert paused.provider.call_count == 3


@pytest.mark.parametrize("failure", ["reply_lost", "cancelled"])
async def test_terminal_resume_committed_before_interruption_releases_its_slot(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    paused = paused_execution
    original_eval = run_manager._redis.eval
    interrupted = False

    async def interrupt_terminal_reply(script: str, numkeys: int, *args: Any) -> Any:
        nonlocal interrupted
        result = await original_eval(script, numkeys, *args)
        if not interrupted and "completed" in args:
            interrupted = True
            if failure == "cancelled":
                raise asyncio.CancelledError("cancel after committed resume")
            raise ConnectionError("committed resume reply lost")
        return result

    monkeypatch.setattr(run_manager._redis, "eval", interrupt_terminal_reply)
    await respond(run_manager, paused)
    await run_manager.drain(timeout_seconds=15)
    assert interrupted and paused.provider.call_count == 2
    meta = await get_run_meta(
        run_manager._redis, prefix=run_manager._key_prefix, run_id=paused.run_id
    )
    assert meta is not None and meta.status == "completed"
    assert (
        await get_active_run(
            run_manager._redis,
            prefix=run_manager._key_prefix,
            conversation_id=paused.ctx.conversation_id,
        )
        is None
    )
    await db_session.refresh(paused.admitted.admission)
    assert paused.admitted.admission.run_finished_at is not None
    async with shared_checkpointer() as cp:
        assert await cp.load_pending(paused.ctx.conversation_id) is None


@pytest.mark.parametrize("stopped", [False, True])
async def test_another_participant_answer_cannot_replace_the_execution_actor(
    db_session: AsyncSession,
    run_manager: RunManager,
    paused_execution: PausedExecution,
    stopped: bool,
) -> None:
    paused = paused_execution
    responder = User(email=f"hitl-{uuid4()}@example.invalid", hashed_password="not-a-login")
    db_session.add(responder)
    await db_session.flush()
    responder_id = responder.id
    db_session.add_all(
        [
            Membership(user_id=responder_id, workspace_id=DEFAULT_WS_ID, role="member"),
            ConversationParticipant(
                org_id=DEFAULT_ORG_ID,
                workspace_id=DEFAULT_WS_ID,
                conversation_id=paused.ctx.conversation_id,
                user_id=responder_id,
            ),
        ]
    )
    await db_session.commit()
    try:
        if stopped:
            await service(db_session).close_generation(
                conversation_id=paused.ctx.conversation_id,
                actor_user_id=paused.ctx.user_id,
                execution_generation=0,
                now=datetime.now(UTC),
            )
            await db_session.commit()
        other = replace(paused, ctx=replace(paused.ctx, user_id=responder_id))
        if stopped:
            with pytest.raises(ResumeConflict):
                await respond(run_manager, other)
            assert paused.provider.call_count == 1
        else:
            await respond(run_manager, other)
            await run_manager.drain(timeout_seconds=15)
            assert paused.provider.call_count == 2
            billing_actors = list(
                (
                    await db_session.scalars(
                        select(BillingEvent.user_id).where(
                            BillingEvent.conversation_id == paused.ctx.conversation_id
                        )
                    )
                ).all()
            )
            assert billing_actors and set(billing_actors) == {paused.ctx.user_id}
    finally:
        await run_manager.cancel_all()
        await cleanup_run_rows(db_session, paused.ctx.conversation_id)
        await db_session.execute(
            delete(ConversationParticipant).where(ConversationParticipant.user_id == responder_id)
        )
        await db_session.execute(delete(Membership).where(Membership.user_id == responder_id))
        await db_session.execute(delete(User).where(User.id == responder_id))
        await db_session.commit()
