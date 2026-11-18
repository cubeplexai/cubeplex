"""E2E tests: idle background task notices in IM conversations deliver to IM."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from cubeloop.providers.base import ReasoningControl
from cubeloop.providers.faux import FauxProvider, faux_assistant_message, faux_text
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.db.engine import async_session_maker
from cubeplex.im.bot_settings import IMBotSettings, store_bot_settings
from cubeplex.im.conversation_resolver import reset_im_conversation
from cubeplex.im.feishu.op_dispatcher import FeishuOpDispatcher
from cubeplex.im.inbound import ingest_inbound_event
from cubeplex.im.outbound import OutboundRunTailer
from cubeplex.im.types import InboundEvent, RenderState
from cubeplex.im.worker import process_one_queue_item
from cubeplex.models import (
    BackgroundTask,
    BackgroundTaskEvent,
    Conversation,
    SandboxCommand,
    UserSandbox,
)
from cubeplex.models.im_connector import (
    IMConnectorAccount,
    IMIdentityLink,
    IMRunQueueItem,
    IMWebhookReceipt,
)
from cubeplex.services.background_task_delivery import BackgroundTaskDeliveryCoordinator
from cubeplex.services.background_tasks import (
    BackgroundTaskService,
    CommandExecutionDetails,
    TaskSpec,
)
from cubeplex.streams.run_events import get_active_run
from cubeplex.streams.run_manager import RunManager
from tests.e2e import test_admitted_run_execution as run_fixtures
from tests.e2e.im_fixtures import (
    im_cleanup,
    im_seed_account,
    im_seed_org_ws_user,
    im_seed_stub_credential,
)
from tests.e2e.test_conversation_execution_control import snapshot
from tests.e2e.test_im_end_to_end import _RecordingCardKit, _RecordingConnector

pytestmark = pytest.mark.asyncio

run_manager = run_fixtures.run_manager

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def im_bg_env(
    db_session: AsyncSession,
) -> AsyncIterator[dict[str, str]]:
    suffix = uuid4().hex[:8]
    ids = {
        "org_id": f"org-imbg{suffix}",
        "ws_id": f"ws-imbg{suffix}",
        "user_id": f"usr-imbg{suffix}",
        "cred_id": f"cred-imbg{suffix}",
        "feishu_account_id": f"imac-fs{suffix}",
        "wecom_account_id": f"imac-wc{suffix}",
        "feishu_ext_id": f"cli_imbg_{suffix}",
        "wecom_ext_id": f"wc_imbg_{suffix}",
    }
    async with async_session_maker() as session:
        await im_seed_org_ws_user(
            session,
            org_id=ids["org_id"],
            ws_id=ids["ws_id"],
            user_id=ids["user_id"],
        )
        await im_seed_stub_credential(
            session,
            credential_id=ids["cred_id"],
            org_id=ids["org_id"],
            user_id=ids["user_id"],
            name=f"imbg:{suffix}",
        )
        await im_seed_account(
            session,
            account_id=ids["feishu_account_id"],
            org_id=ids["org_id"],
            ws_id=ids["ws_id"],
            user_id=ids["user_id"],
            credential_id=ids["cred_id"],
            external_account_id=ids["feishu_ext_id"],
            delivery_mode="long_connection",
            platform="feishu",
        )
        await im_seed_account(
            session,
            account_id=ids["wecom_account_id"],
            org_id=ids["org_id"],
            ws_id=ids["ws_id"],
            user_id=ids["user_id"],
            credential_id=ids["cred_id"],
            external_account_id=ids["wecom_ext_id"],
            delivery_mode="stream",
            platform="wecom",
        )
        session.add_all(
            [
                IMIdentityLink(
                    org_id=ids["org_id"],
                    workspace_id=ids["ws_id"],
                    account_id=ids["feishu_account_id"],
                    im_user_id="on_bg_user",
                    user_id=ids["user_id"],
                ),
                IMIdentityLink(
                    org_id=ids["org_id"],
                    workspace_id=ids["ws_id"],
                    account_id=ids["wecom_account_id"],
                    im_user_id="zhangsan",
                    user_id=ids["user_id"],
                ),
            ]
        )
        await session.commit()

    try:
        yield ids
    finally:
        await db_session.rollback()
        async with async_session_maker() as session:
            conv_ids = list(
                await session.scalars(
                    select(col(Conversation.id)).where(
                        col(Conversation.workspace_id) == ids["ws_id"]
                    )
                )
            )
        for conv_id in conv_ids:
            await run_fixtures.cleanup_run_rows(db_session, conv_id)
        async with async_session_maker() as session:
            for model in (BackgroundTaskEvent, SandboxCommand, BackgroundTask):
                await session.execute(delete(model).where(col(model.workspace_id) == ids["ws_id"]))
            await session.execute(
                delete(UserSandbox).where(col(UserSandbox.workspace_id) == ids["ws_id"])
            )
            await session.execute(
                delete(IMIdentityLink).where(col(IMIdentityLink.workspace_id) == ids["ws_id"])
            )
            await im_cleanup(
                session,
                org_ids=[ids["org_id"]],
                ws_ids=[ids["ws_id"]],
                user_ids=[ids["user_id"]],
                credential_ids=[ids["cred_id"]],
                account_ids=[ids["feishu_account_id"], ids["wecom_account_id"]],
                cleanup_conversations_in_ws=True,
            )
            await session.commit()


async def _stage_ready_background_event(
    *,
    org_id: str,
    ws_id: str,
    user_id: str,
    conversation_id: str,
    originating_admission_id: str,
    originating_run_id: str,
) -> BackgroundTaskEvent:
    from cubeplex.models.topic import Topic
    from cubeplex.sandbox.scope import get_workspace_sandbox_mode, resolve_sandbox_target_pure

    async with async_session_maker() as session:
        conv = await session.get(Conversation, conversation_id)
        assert conv is not None
        topic = await session.get(Topic, conv.topic_id) if conv.topic_id is not None else None
        ws_mode = await get_workspace_sandbox_mode(session, ws_id)
        target = resolve_sandbox_target_pure(
            workspace_id=ws_id,
            workspace_sandbox_mode=ws_mode,
            caller_user_id=user_id,
            conversation_id=conv.id,
            is_group_chat=conv.is_group_chat,
            conversation_creator_user_id=conv.creator_user_id,
            topic_id=topic.id if topic is not None else None,
            topic_sandbox_mode=topic.sandbox_mode if topic is not None else None,
            topic_creator_user_id=topic.creator_user_id if topic is not None else None,
        )
        sandbox = UserSandbox(
            org_id=org_id,
            workspace_id=ws_id,
            user_id=target.owner_user_id,
            scope_type=target.scope_type,
            scope_id=target.scope_id,
            sandbox_id=f"sbx-{uuid4()}",
            status="running",
            provider="local",
            image="test",
        )
        session.add(sandbox)
        await session.flush()
        assert sandbox.sandbox_id is not None

        reserved = await BackgroundTaskService(
            session, org_id=org_id, workspace_id=ws_id
        ).reserve_task(
            admission_id=originating_admission_id,
            task_spec=TaskSpec(
                originating_run_id=originating_run_id,
                tool_call_id=f"call_{uuid4().hex[:8]}",
            ),
            execution_details=CommandExecutionDetails(
                user_sandbox_id=sandbox.id,
                sandbox_instance_id=sandbox.sandbox_id,
                provider="local",
                command="pytest -q",
                log_path="/workspace/.cubeplex/pytest.log",
            ),
            owner_token=str(uuid4()),
            owner_until=NOW + timedelta(seconds=30),
            now=NOW,
        )
        reserved.task.backgrounded_at = NOW
        reserved.task.state = "succeeded"
        reserved.task.result_readiness = "ready"
        reserved.task.result_summary = "12 passed in 3.2s"
        reserved.task.result_ref = "/workspace/.cubeplex/pytest.log"
        event = BackgroundTaskEvent(
            org_id=org_id,
            workspace_id=ws_id,
            conversation_id=conversation_id,
            task_id=reserved.task.id,
            execution_generation=reserved.task.execution_generation,
            reason="completion",
            dedupe_key="completion",
        )
        session.add(event)
        await session.commit()
        return event


async def _drain_initial_inbound(
    *,
    account_id: str,
    run_manager: RunManager,
    snap: Any,
) -> tuple[str, IMRunQueueItem]:
    first_runs: list[tuple[str, str]] = []

    async def _capture_first(rid: str, qitem: IMRunQueueItem) -> None:
        first_runs.append((rid, qitem.id))

    assert await process_one_queue_item(
        session_maker=async_session_maker,
        run_manager=run_manager,
        on_run_started=_capture_first,
        lease_seconds=300,
        load_execution_snapshot=AsyncMock(return_value=snap),
        deliverable_connection_ids=lambda: {account_id},
    )
    await run_manager.drain(timeout_seconds=30)
    assert len(first_runs) == 1
    originating_run_id, item_id = first_runs[0]
    async with async_session_maker() as session:
        persisted = await session.get(IMRunQueueItem, item_id)
        assert persisted is not None
        assert persisted.execution_admission_id is not None
        return originating_run_id, persisted


async def _tail_feishu_run(
    run_manager: RunManager,
    run_id: str,
    item: IMRunQueueItem,
) -> tuple[_RecordingConnector, _RecordingCardKit]:
    connector = _RecordingConnector()
    cardkit = _RecordingCardKit()
    state = RenderState(
        bot_name="CubePlex",
        run_id=run_id,
        reply_to_id=item.reply_to_id,
        inbound_message_id=item.inbound_message_id,
    )
    dispatcher = FeishuOpDispatcher(connector=connector, state=state, cardkit=cardkit)
    tailer = OutboundRunTailer(
        redis=run_manager._redis,
        key_prefix=run_manager._key_prefix,
        run_id=run_id,
        connector=connector,
        state=state,
        dispatcher=dispatcher,
        responder_open_id=item.sender_open_id,
        block_ms=100,
    )
    await asyncio.wait_for(tailer.run(), timeout=10.0)
    return connector, cardkit


async def test_idle_im_background_notice_delivers_reply_via_im_worker_and_tailer(
    im_bg_env: dict[str, str],
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = snapshot()
    provider = FauxProvider(provider_id="provider")
    provider.set_responses(
        [
            faux_assistant_message([faux_text("Started background task, waiting...")]),
            faux_assistant_message([faux_text("Task finished: all 12 tests passed!")]),
        ]
    )
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(
        "cubeplex.llm.snapshot.load_llm_snapshot",
        AsyncMock(return_value=snap),
    )

    async with async_session_maker() as session:
        account = await session.get(IMConnectorAccount, im_bg_env["feishu_account_id"])
        assert account is not None

    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id=im_bg_env["feishu_ext_id"],
            platform_event_id="ev-imbg-1",
            channel_id="oc_bg_chat",
            scope_key="u:on_bg_user",
            scope_kind="participant",
            reply_to_id="om_inbound_1",
            inbound_message_id="om_inbound_1",
            sender_ref="on_bg_user",
            sender_open_id="ou_bg_user",
            text="run the test suite in background",
        ),
        account=account,
        session_maker=async_session_maker,
    )

    originating_run_id, first_item = await _drain_initial_inbound(
        account_id=account.id, run_manager=run_manager, snap=snap
    )
    conversation_id = first_item.conversation_id
    assert first_item.execution_admission_id is not None

    # Set non-null conversation reasoning so fingerprint matching on background
    # notices exercises the real conversation-preset path too.
    async with async_session_maker() as session:
        conv = await session.get(Conversation, conversation_id)
        assert conv is not None
        conv.reasoning = ReasoningControl(mode="off").model_dump(mode="json")
        await session.commit()

    assert (
        await get_active_run(
            run_manager._redis,
            prefix=run_manager._key_prefix,
            conversation_id=conversation_id,
        )
        is None
    )

    event = await _stage_ready_background_event(
        org_id=im_bg_env["org_id"],
        ws_id=im_bg_env["ws_id"],
        user_id=im_bg_env["user_id"],
        conversation_id=conversation_id,
        originating_admission_id=first_item.execution_admission_id,
        originating_run_id=originating_run_id,
    )

    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager,
        redis=run_manager._redis,
        redis_key_prefix=run_manager._key_prefix,
    )
    routed = await coordinator.deliver_once(now=NOW, lease_until=NOW + timedelta(seconds=15))
    assert routed == [event.id]
    await run_manager.drain(timeout_seconds=30)

    async with async_session_maker() as session:
        refreshed_event = await session.get(BackgroundTaskEvent, event.id)
        assert refreshed_event is not None
        assert refreshed_event.state == "delivered"
        assert refreshed_event.delivery_run_id is not None
        pending_items = list(
            await session.scalars(
                select(IMRunQueueItem).where(
                    col(IMRunQueueItem.account_id) == account.id,
                    col(IMRunQueueItem.status) == "pending",
                )
            )
        )
    assert len(pending_items) == 1
    bg_item = pending_items[0]
    assert bg_item.conversation_id == conversation_id
    assert bg_item.channel_id == "oc_bg_chat"
    assert bg_item.scope_key == "u:on_bg_user"
    assert bg_item.scope_kind == "participant"
    assert bg_item.reply_to_id == "om_inbound_1"
    assert bg_item.inbound_message_id is None
    assert bg_item.sender_open_id == "ou_bg_user"

    # Non-owner instance must not drain connection-bound IM handoff.
    assert not await process_one_queue_item(
        session_maker=async_session_maker,
        run_manager=run_manager,
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=AsyncMock(return_value=snap),
        deliverable_connection_ids=lambda: set(),
    )

    tailed_results: list[tuple[str, _RecordingConnector, _RecordingCardKit]] = []

    async def _on_bg_started(rid: str, qitem: IMRunQueueItem) -> None:
        conn, ck = await _tail_feishu_run(run_manager, rid, qitem)
        tailed_results.append((rid, conn, ck))

    assert await process_one_queue_item(
        session_maker=async_session_maker,
        run_manager=run_manager,
        on_run_started=_on_bg_started,
        lease_seconds=300,
        load_execution_snapshot=AsyncMock(return_value=snap),
        deliverable_connection_ids=lambda: {account.id},
    )
    assert len(tailed_results) == 1
    tailed_run_id, connector, cardkit = tailed_results[0]
    assert tailed_run_id == refreshed_event.delivery_run_id
    assert connector.processing_completed
    assert not connector.processing_failed
    assert len(cardkit.finalized) == 1
    assert "Task finished: all 12 tests passed!" in str(cardkit.finalized[0])
    assert provider.call_count == 2


@pytest.mark.parametrize("topic_mode", ["topic", "flat"])
async def test_background_notice_delivers_to_im_after_new_rotation(
    im_bg_env: dict[str, str],
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
    topic_mode: str,
) -> None:
    snap = snapshot()
    provider = FauxProvider(provider_id="provider")
    provider.set_responses(
        [
            faux_assistant_message([faux_text("Running deploy in background...")]),
            faux_assistant_message([faux_text("Deploy finished after /new!")]),
        ]
    )
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(
        "cubeplex.llm.snapshot.load_llm_snapshot",
        AsyncMock(return_value=snap),
    )

    async with async_session_maker() as session:
        account = await session.get(IMConnectorAccount, im_bg_env["feishu_account_id"])
        assert account is not None
        account.config = store_bot_settings(
            account.config,
            IMBotSettings(routing_mode="isolated", topic_mode=topic_mode),  # type: ignore[arg-type]
        )
        session.add(account)
        await session.commit()
        await session.refresh(account)

    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id=im_bg_env["feishu_ext_id"],
            platform_event_id=f"ev-rot-{topic_mode}",
            channel_id="oc_rot_chat",
            scope_key="dm",
            scope_kind="dm",
            reply_to_id=None,
            inbound_message_id=f"om_rot_{topic_mode}",
            sender_ref="on_bg_user",
            sender_open_id="ou_bg_user",
            text="deploy staging",
        ),
        account=account,
        session_maker=async_session_maker,
    )

    originating_run_id, first_item = await _drain_initial_inbound(
        account_id=account.id, run_manager=run_manager, snap=snap
    )
    assert first_item.execution_admission_id is not None

    # User sends /new before the background task completes.
    async with async_session_maker() as session:
        outcome = await reset_im_conversation(
            session,
            account_id=account.id,
            channel_id="oc_rot_chat",
            scope_key="dm",
        )
        await session.commit()
    assert outcome == ("rotated" if topic_mode == "topic" else "flat")

    event = await _stage_ready_background_event(
        org_id=im_bg_env["org_id"],
        ws_id=im_bg_env["ws_id"],
        user_id=im_bg_env["user_id"],
        conversation_id=first_item.conversation_id,
        originating_admission_id=first_item.execution_admission_id,
        originating_run_id=originating_run_id,
    )

    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager,
        redis=run_manager._redis,
        redis_key_prefix=run_manager._key_prefix,
    )
    assert await coordinator.deliver_once(now=NOW, lease_until=NOW + timedelta(seconds=15)) == [
        event.id
    ]
    await run_manager.drain(timeout_seconds=30)

    tailed_cards: list[dict[str, Any]] = []

    async def _on_bg_started(rid: str, qitem: IMRunQueueItem) -> None:
        _, ck = await _tail_feishu_run(run_manager, rid, qitem)
        tailed_cards.extend(ck.finalized)

    assert await process_one_queue_item(
        session_maker=async_session_maker,
        run_manager=run_manager,
        on_run_started=_on_bg_started,
        lease_seconds=300,
        load_execution_snapshot=AsyncMock(return_value=snap),
        deliverable_connection_ids=lambda: {account.id},
    )
    assert len(tailed_cards) == 1
    assert "Deploy finished after /new!" in str(tailed_cards[0])


async def test_wecom_background_notice_clears_one_shot_reply_to_id(
    im_bg_env: dict[str, str],
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = snapshot()
    provider = FauxProvider(provider_id="provider")
    provider.set_responses(
        [
            faux_assistant_message([faux_text("Working...")]),
            faux_assistant_message([faux_text("WeCom task complete")]),
        ]
    )
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(
        "cubeplex.llm.snapshot.load_llm_snapshot",
        AsyncMock(return_value=snap),
    )

    async with async_session_maker() as session:
        wecom_account = await session.get(IMConnectorAccount, im_bg_env["wecom_account_id"])
        assert wecom_account is not None

    await ingest_inbound_event(
        InboundEvent(
            platform="wecom",
            account_external_id=im_bg_env["wecom_ext_id"],
            platform_event_id="ev-wc-1",
            channel_id="zhangsan",
            scope_key="dm",
            scope_kind="dm",
            reply_to_id="req_one_shot_callback",
            inbound_message_id="ev-wc-1",
            sender_ref="zhangsan",
            sender_open_id="zhangsan",
            text="run job",
        ),
        account=wecom_account,
        session_maker=async_session_maker,
    )

    originating_run_id, first_item = await _drain_initial_inbound(
        account_id=wecom_account.id, run_manager=run_manager, snap=snap
    )
    assert first_item.execution_admission_id is not None

    event = await _stage_ready_background_event(
        org_id=im_bg_env["org_id"],
        ws_id=im_bg_env["ws_id"],
        user_id=im_bg_env["user_id"],
        conversation_id=first_item.conversation_id,
        originating_admission_id=first_item.execution_admission_id,
        originating_run_id=originating_run_id,
    )

    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager,
        redis=run_manager._redis,
        redis_key_prefix=run_manager._key_prefix,
    )
    assert await coordinator.deliver_once(now=NOW, lease_until=NOW + timedelta(seconds=15)) == [
        event.id
    ]
    await run_manager.drain(timeout_seconds=30)

    async with async_session_maker() as session:
        bg_item = (
            await session.execute(
                select(IMRunQueueItem).where(
                    col(IMRunQueueItem.account_id) == wecom_account.id,
                    col(IMRunQueueItem.status) == "pending",
                )
            )
        ).scalar_one()
    assert bg_item.reply_to_id is None
    assert bg_item.channel_id == "zhangsan"
    assert bg_item.sender_open_id == "zhangsan"


async def test_superseded_uncommitted_background_notice_attempt_tails_only_live_attempt(
    im_bg_env: dict[str, str],
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cubeplex.streams.execution_adapter as execution_adapter

    snap = snapshot()
    provider = FauxProvider(provider_id="provider")
    provider.set_responses(
        [
            faux_assistant_message([faux_text("Starting task...")]),
            faux_assistant_message([faux_text("Recovered background result!")]),
        ]
    )
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(
        "cubeplex.llm.snapshot.load_llm_snapshot",
        AsyncMock(return_value=snap),
    )

    async with async_session_maker() as session:
        account = await session.get(IMConnectorAccount, im_bg_env["feishu_account_id"])
        assert account is not None

    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id=im_bg_env["feishu_ext_id"],
            platform_event_id="ev-retry-1",
            channel_id="oc_retry_chat",
            scope_key="dm",
            scope_kind="dm",
            reply_to_id=None,
            inbound_message_id="om_retry_1",
            sender_ref="on_bg_user",
            sender_open_id="ou_bg_user",
            text="run flaky start",
        ),
        account=account,
        session_maker=async_session_maker,
    )

    originating_run_id, first_item = await _drain_initial_inbound(
        account_id=account.id, run_manager=run_manager, snap=snap
    )
    assert first_item.execution_admission_id is not None

    event = await _stage_ready_background_event(
        org_id=im_bg_env["org_id"],
        ws_id=im_bg_env["ws_id"],
        user_id=im_bg_env["user_id"],
        conversation_id=first_item.conversation_id,
        originating_admission_id=first_item.execution_admission_id,
        originating_run_id=originating_run_id,
    )

    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager,
        redis=run_manager._redis,
        redis_key_prefix=run_manager._key_prefix,
    )

    # First delivery attempt crashes before committing the initial checkpoint,
    # while the IM connection is temporarily offline (queue not yet drained).
    real_execute_session = execution_adapter.execute_session
    monkeypatch.setattr(
        execution_adapter,
        "execute_session",
        AsyncMock(side_effect=RuntimeError("transient crash before initial checkpoint")),
    )
    assert await coordinator.deliver_once(now=NOW, lease_until=NOW + timedelta(seconds=15)) == [
        event.id
    ]
    await run_manager.drain(timeout_seconds=30)

    # Reconcile and retry with working session execution.
    monkeypatch.setattr(execution_adapter, "execute_session", real_execute_session)
    t2 = NOW + timedelta(seconds=20)
    assert await coordinator.deliver_once(now=t2, lease_until=t2 + timedelta(seconds=15)) == [
        event.id
    ]
    await run_manager.drain(timeout_seconds=30)

    async with async_session_maker() as session:
        refreshed_event = await session.get(BackgroundTaskEvent, event.id)
        assert refreshed_event is not None
        assert refreshed_event.state == "delivered"
        receipts = list(
            await session.scalars(
                select(IMWebhookReceipt).where(
                    col(IMWebhookReceipt.account_id) == account.id,
                    col(IMWebhookReceipt.platform_event_id).like(f"background_notice:{event.id}:%"),
                )
            )
        )
    assert len(receipts) == 2

    started_runs: list[str] = []

    async def _on_bg_started(rid: str, qitem: IMRunQueueItem) -> None:
        started_runs.append(rid)
        _, ck = await _tail_feishu_run(run_manager, rid, qitem)
        assert len(ck.finalized) == 1
        assert "Recovered background result!" in str(ck.finalized[0])

    while await process_one_queue_item(
        session_maker=async_session_maker,
        run_manager=run_manager,
        on_run_started=_on_bg_started,
        lease_seconds=300,
        load_execution_snapshot=AsyncMock(return_value=snap),
        deliverable_connection_ids=lambda: {account.id},
    ):
        pass

    assert started_runs == [refreshed_event.delivery_run_id]
