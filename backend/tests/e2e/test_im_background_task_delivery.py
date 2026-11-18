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
from cubeplex.im import resume as resume_mod
from cubeplex.im.bot_settings import IMBotSettings, store_bot_settings
from cubeplex.im.conversation_resolver import reset_im_conversation, resolve_im_conversation
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
    SteeringMessage,
    Topic,
    TopicParticipant,
    UserSandbox,
)
from cubeplex.models.im_connector import (
    IMConnectorAccount,
    IMIdentityLink,
    IMRunQueueItem,
    IMThreadLink,
    IMWebhookReceipt,
)
from cubeplex.services.background_task_delivery import BackgroundTaskDeliveryCoordinator
from cubeplex.services.background_tasks import (
    BackgroundTaskService,
    CommandExecutionDetails,
    TaskSpec,
)
from cubeplex.services.conversation_execution import (
    ConversationExecutionService,
    UserMessageIntent,
)
from cubeplex.streams.run_events import _run_meta_key, create_run, get_active_run
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

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def im_bg_env(
    db_session: AsyncSession,
) -> AsyncIterator[dict[str, str]]:
    suffix = uuid4().hex[:8]
    ids = {
        "org_id": f"org-imbg{suffix}",
        "ws_id": f"ws-imbg{suffix}",
        "user_id": f"usr-imbg{suffix}",
        "user_b_id": f"usr-imbb{suffix}",
        "cred_id": f"cred-imbg{suffix}",
        "feishu_account_id": f"imac-fs{suffix}",
        "wecom_account_id": f"imac-wc{suffix}",
        "slack_account_id": f"imac-sl{suffix}",
        "feishu_ext_id": f"cli_imbg_{suffix}",
        "wecom_ext_id": f"wc_imbg_{suffix}",
        "slack_ext_id": f"T_imbg_{suffix}",
    }
    async with async_session_maker() as session:
        await im_seed_org_ws_user(
            session,
            org_id=ids["org_id"],
            ws_id=ids["ws_id"],
            user_id=ids["user_id"],
        )
        await im_seed_org_ws_user(
            session,
            org_id=ids["org_id"],
            ws_id=ids["ws_id"],
            user_id=ids["user_b_id"],
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
        await im_seed_account(
            session,
            account_id=ids["slack_account_id"],
            org_id=ids["org_id"],
            ws_id=ids["ws_id"],
            user_id=ids["user_id"],
            credential_id=ids["cred_id"],
            external_account_id=ids["slack_ext_id"],
            delivery_mode="gateway",
            platform="slack",
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
                    account_id=ids["feishu_account_id"],
                    im_user_id="on_bg_user_b",
                    user_id=ids["user_b_id"],
                ),
                IMIdentityLink(
                    org_id=ids["org_id"],
                    workspace_id=ids["ws_id"],
                    account_id=ids["wecom_account_id"],
                    im_user_id="zhangsan",
                    user_id=ids["user_id"],
                ),
                IMIdentityLink(
                    org_id=ids["org_id"],
                    workspace_id=ids["ws_id"],
                    account_id=ids["slack_account_id"],
                    im_user_id="U_SLACK_1",
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
            await session.execute(
                delete(SteeringMessage).where(col(SteeringMessage.workspace_id) == ids["ws_id"])
            )
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
                user_ids=[ids["user_id"], ids["user_b_id"]],
                credential_ids=[ids["cred_id"]],
                account_ids=[
                    ids["feishu_account_id"],
                    ids["wecom_account_id"],
                    ids["slack_account_id"],
                ],
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
        sandbox = (
            await session.execute(
                select(UserSandbox).where(
                    col(UserSandbox.org_id) == org_id,
                    col(UserSandbox.workspace_id) == ws_id,
                    col(UserSandbox.scope_type) == target.scope_type,
                    col(UserSandbox.scope_id) == target.scope_id,
                )
            )
        ).scalar_one_or_none()
        if sandbox is None:
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
    assert event.id in routed
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
    assert bg_item.execution_admission_id is None
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
    routed = await coordinator.deliver_once(now=NOW, lease_until=NOW + timedelta(seconds=15))
    assert event.id in routed
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


async def test_shared_flat_group_and_upgraded_topic_preserve_originating_quote_and_creator(
    im_bg_env: dict[str, str],
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = snapshot()
    provider = FauxProvider(provider_id="provider")
    provider.set_responses(
        [
            faux_assistant_message([faux_text("User A ack")]),
            faux_assistant_message([faux_text("User B started task")]),
            faux_assistant_message([faux_text("User A follow-up")]),
            faux_assistant_message([faux_text("User B task finished in upgraded topic!")]),
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
            IMBotSettings(routing_mode="shared", topic_mode="flat"),
        )
        session.add(account)
        await session.commit()
        await session.refresh(account)

    # M1: User A creates the shared+flat group conversation.
    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id=im_bg_env["feishu_ext_id"],
            platform_event_id="ev-shared-m1",
            channel_id="oc_shared_group",
            scope_key="ch",
            scope_kind="channel",
            reply_to_id="om_user_a_m1",
            inbound_message_id="om_user_a_m1",
            sender_ref="on_bg_user",
            sender_open_id="ou_user_a",
            text="hello team",
        ),
        account=account,
        session_maker=async_session_maker,
    )
    _, m1_item = await _drain_initial_inbound(
        account_id=account.id, run_manager=run_manager, snap=snap
    )

    # M2: User B starts a background task in the same shared+flat conversation.
    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id=im_bg_env["feishu_ext_id"],
            platform_event_id="ev-shared-m2",
            channel_id="oc_shared_group",
            scope_key="ch",
            scope_kind="channel",
            reply_to_id="om_user_b_m2",
            inbound_message_id="om_user_b_m2",
            sender_ref="on_bg_user_b",
            sender_open_id="ou_user_b",
            text="run benchmark",
        ),
        account=account,
        session_maker=async_session_maker,
    )
    m2_run_id, m2_item = await _drain_initial_inbound(
        account_id=account.id, run_manager=run_manager, snap=snap
    )
    assert m2_item.conversation_id == m1_item.conversation_id
    assert m2_item.execution_admission_id is not None

    # M3: User A sends another message before M2's background task finishes.
    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id=im_bg_env["feishu_ext_id"],
            platform_event_id="ev-shared-m3",
            channel_id="oc_shared_group",
            scope_key="ch",
            scope_kind="channel",
            reply_to_id="om_user_a_m3",
            inbound_message_id="om_user_a_m3",
            sender_ref="on_bg_user",
            sender_open_id="ou_user_a",
            text="any update?",
        ),
        account=account,
        session_maker=async_session_maker,
    )
    await _drain_initial_inbound(account_id=account.id, run_manager=run_manager, snap=snap)

    # Upgrade the flat IM conversation to a Web Topic (topic.attributes == {},
    # conversation.attributes["im"] preserved).
    async with async_session_maker() as session:
        web_topic = Topic(
            org_id=im_bg_env["org_id"],
            workspace_id=im_bg_env["ws_id"],
            creator_user_id=im_bg_env["user_id"],
            title="Upgraded Web Topic",
            sandbox_mode="dedicated",
            attributes={},
        )
        session.add(web_topic)
        await session.flush()
        session.add_all(
            [
                TopicParticipant(topic_id=web_topic.id, user_id=im_bg_env["user_id"], role="owner"),
                TopicParticipant(
                    topic_id=web_topic.id, user_id=im_bg_env["user_b_id"], role="member"
                ),
            ]
        )
        conv = await session.get(Conversation, m2_item.conversation_id)
        assert conv is not None
        conv.topic_id = web_topic.id
        link = (
            await session.execute(
                select(IMThreadLink).where(
                    col(IMThreadLink.conversation_id) == m2_item.conversation_id
                )
            )
        ).scalar_one()
        link.topic_id = web_topic.id
        await session.commit()

    # Verify IM resume gate also accepts the upgraded IM conversation.
    resume_rm = AsyncMock()
    resume_rm.resume_run_with_answer = AsyncMock(return_value=None)

    async def _fake_resolve(_rid: str) -> tuple[str, str, str, str, str | None, bool]:
        return (
            m2_item.conversation_id,
            im_bg_env["user_id"],
            im_bg_env["org_id"],
            im_bg_env["ws_id"],
            web_topic.id,
            True,
        )

    monkeypatch.setattr(resume_mod, "_resolve_run_context", _fake_resolve)
    assert await resume_mod.resume_paused_run(
        run_id="run-paused-upgraded",
        input_kind="sandbox_confirm",
        choice="approve",
        operator_open_id="ou_user_b",
        run_manager=resume_rm,
    )
    assert (
        resume_rm.resume_run_with_answer.await_args.kwargs["ctx"].conversation_creator_user_id
        == im_bg_env["user_id"]
    )

    event = await _stage_ready_background_event(
        org_id=im_bg_env["org_id"],
        ws_id=im_bg_env["ws_id"],
        user_id=im_bg_env["user_b_id"],
        conversation_id=m2_item.conversation_id,
        originating_admission_id=m2_item.execution_admission_id,
        originating_run_id=m2_run_id,
    )
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager,
        redis=run_manager._redis,
        redis_key_prefix=run_manager._key_prefix,
    )
    assert event.id in await coordinator.deliver_once(
        now=NOW, lease_until=NOW + timedelta(seconds=15)
    )
    await run_manager.drain(timeout_seconds=30)

    async with async_session_maker() as session:
        bg_item = (
            await session.execute(
                select(IMRunQueueItem).where(
                    col(IMRunQueueItem.account_id) == account.id,
                    col(IMRunQueueItem.status) == "pending",
                )
            )
        ).scalar_one()
    # Must quote M2 (User B's originating message), NOT M3 (User A's later message)!
    assert bg_item.reply_to_id == "om_user_b_m2"
    assert bg_item.sender_open_id == "ou_user_b"

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
    assert "User B task finished in upgraded topic!" in str(tailed_cards[0])


async def test_pre_checkpoint_crash_never_enqueues_and_active_run_steering_has_no_fk_conflict(
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

    # Attempt 1 crashes BEFORE initial checkpoint: ZERO IM queue items may be created.
    real_execute_session = execution_adapter.execute_session
    monkeypatch.setattr(
        execution_adapter,
        "execute_session",
        AsyncMock(side_effect=RuntimeError("transient crash before initial checkpoint")),
    )
    assert event.id in await coordinator.deliver_once(
        now=NOW, lease_until=NOW + timedelta(seconds=15)
    )
    await run_manager.drain(timeout_seconds=30)

    async with async_session_maker() as session:
        pre_checkpoint_receipts = list(
            await session.scalars(
                select(IMWebhookReceipt).where(
                    col(IMWebhookReceipt.account_id) == account.id,
                    col(IMWebhookReceipt.platform_event_id).like("background_notice:%"),
                )
            )
        )
    assert pre_checkpoint_receipts == []

    # Attempt 2 succeeds and enqueues exactly one IM tailer handoff.
    monkeypatch.setattr(execution_adapter, "execute_session", real_execute_session)
    t2 = NOW + timedelta(seconds=20)
    assert event.id in await coordinator.deliver_once(
        now=t2, lease_until=t2 + timedelta(seconds=15)
    )
    await run_manager.drain(timeout_seconds=30)

    started_runs: list[str] = []

    async def _on_bg_started(rid: str, qitem: IMRunQueueItem) -> None:
        started_runs.append(rid)
        _, ck = await _tail_feishu_run(run_manager, rid, qitem)
        assert "Recovered background result!" in str(ck.finalized[0])

    assert await process_one_queue_item(
        session_maker=async_session_maker,
        run_manager=run_manager,
        on_run_started=_on_bg_started,
        lease_seconds=300,
        load_execution_snapshot=AsyncMock(return_value=snap),
        deliverable_connection_ids=lambda: {account.id},
    )
    assert len(started_runs) == 1

    # Also verify that if a second notice's initial admission was created while
    # idle and then yielded to a live run, enqueue_for_active_run deletes the
    # unstarted admission cleanly without any FK violation.
    event_steered = await _stage_ready_background_event(
        org_id=im_bg_env["org_id"],
        ws_id=im_bg_env["ws_id"],
        user_id=im_bg_env["user_id"],
        conversation_id=first_item.conversation_id,
        originating_admission_id=first_item.execution_admission_id,
        originating_run_id=originating_run_id,
    )
    async with async_session_maker() as session:
        live_adm = await ConversationExecutionService(
            session,
            org_id=im_bg_env["org_id"],
            workspace_id=im_bg_env["ws_id"],
        ).admit_user_message(
            conversation_id=first_item.conversation_id,
            actor_user_id=im_bg_env["user_id"],
            namespace="web",
            source_id=f"web-live-{uuid4().hex[:8]}",
            intent=UserMessageIntent(content="live user run"),
            snapshot=snap,
            now=t2,
        )
        assert live_adm.admission.run_id is not None
        await ConversationExecutionService(
            session,
            org_id=im_bg_env["org_id"],
            workspace_id=im_bg_env["ws_id"],
        ).claim_run_start(
            admission_id=live_adm.admission.id,
            attempt_id="live-attempt-1",
            now=t2,
        )
        await session.commit()
    assert live_adm.admission.run_id is not None
    await create_run(
        run_manager._redis,
        prefix=run_manager._key_prefix,
        run_id=live_adm.admission.run_id,
        conversation_id=first_item.conversation_id,
        status="running",
        started_at=t2.isoformat(),
        user_message="live user run",
        ttl_seconds=60,
        claim_token="live-attempt-1",
    )
    t3 = t2 + timedelta(seconds=20)
    routed_steer = await coordinator.deliver_once(now=t3, lease_until=t3 + timedelta(seconds=15))
    assert event_steered.id in routed_steer


async def test_stopped_or_deleted_background_notice_skips_tailer_without_retry_storm(
    im_bg_env: dict[str, str],
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = snapshot()
    provider = FauxProvider(provider_id="provider")
    provider.set_responses(
        [
            faux_assistant_message([faux_text("Initial reply")]),
            faux_assistant_message([faux_text("Notice reply 1")]),
            faux_assistant_message([faux_text("Notice reply 2")]),
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
            platform_event_id="ev-stop-1",
            channel_id="oc_stop_chat",
            scope_key="dm",
            scope_kind="dm",
            reply_to_id=None,
            inbound_message_id="om_stop_1",
            sender_ref="on_bg_user",
            sender_open_id="ou_bg_user",
            text="start two tasks",
        ),
        account=account,
        session_maker=async_session_maker,
    )
    originating_run_id, first_item = await _drain_initial_inbound(
        account_id=account.id, run_manager=run_manager, snap=snap
    )
    assert first_item.execution_admission_id is not None

    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager,
        redis=run_manager._redis,
        redis_key_prefix=run_manager._key_prefix,
    )

    # Notice 1: run gets marked cancelled in Redis before IM worker drains.
    ev1 = await _stage_ready_background_event(
        org_id=im_bg_env["org_id"],
        ws_id=im_bg_env["ws_id"],
        user_id=im_bg_env["user_id"],
        conversation_id=first_item.conversation_id,
        originating_admission_id=first_item.execution_admission_id,
        originating_run_id=originating_run_id,
    )
    assert ev1.id in await coordinator.deliver_once(
        now=NOW, lease_until=NOW + timedelta(seconds=15)
    )
    await run_manager.drain(timeout_seconds=30)
    async with async_session_maker() as session:
        ev1_db = await session.get(BackgroundTaskEvent, ev1.id)
        assert ev1_db is not None and ev1_db.checkpoint_run_id is not None
    await run_manager._redis.hset(  # type: ignore[misc]
        _run_meta_key(run_manager._key_prefix, ev1_db.checkpoint_run_id),
        "status",
        "cancelled",
    )

    tailer_calls: list[str] = []

    async def _should_not_fire(rid: str, _q: IMRunQueueItem) -> None:
        tailer_calls.append(rid)

    assert await process_one_queue_item(
        session_maker=async_session_maker,
        run_manager=run_manager,
        on_run_started=_should_not_fire,
        lease_seconds=300,
        load_execution_snapshot=AsyncMock(return_value=snap),
        deliverable_connection_ids=lambda: {account.id},
    )
    assert tailer_calls == []

    # Notice 2: conversation soft-deleted after notice commits, before IM worker drains.
    ev2 = await _stage_ready_background_event(
        org_id=im_bg_env["org_id"],
        ws_id=im_bg_env["ws_id"],
        user_id=im_bg_env["user_id"],
        conversation_id=first_item.conversation_id,
        originating_admission_id=first_item.execution_admission_id,
        originating_run_id=originating_run_id,
    )
    assert ev2.id in await coordinator.deliver_once(
        now=NOW, lease_until=NOW + timedelta(seconds=15)
    )
    await run_manager.drain(timeout_seconds=30)
    async with async_session_maker() as session:
        conv = await session.get(Conversation, first_item.conversation_id)
        assert conv is not None
        conv.deleted_at = NOW
        await session.commit()

    assert await process_one_queue_item(
        session_maker=async_session_maker,
        run_manager=run_manager,
        on_run_started=_should_not_fire,
        lease_seconds=300,
        load_execution_snapshot=AsyncMock(return_value=snap),
        deliverable_connection_ids=lambda: {account.id},
    )
    assert tailer_calls == []


async def test_wecom_and_slack_thread_fallback_and_non_im_topic_child_isolation(
    im_bg_env: dict[str, str],
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = snapshot()
    provider = FauxProvider(provider_id="provider")
    provider.set_responses(
        [
            faux_assistant_message([faux_text("WeCom turn 1")]),
            faux_assistant_message([faux_text("WeCom bg reply")]),
            faux_assistant_message([faux_text("Slack thread fallback reply")]),
            faux_assistant_message([faux_text("Disabled account web reply")]),
            faux_assistant_message([faux_text("Non-IM child under IM topic reply")]),
        ]
    )
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(
        "cubeplex.llm.snapshot.load_llm_snapshot",
        AsyncMock(return_value=snap),
    )

    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager,
        redis=run_manager._redis,
        redis_key_prefix=run_manager._key_prefix,
    )

    # 1) WeCom inbound -> background notice clears one-shot callback reply_to_id.
    async with async_session_maker() as session:
        wecom_account = await session.get(IMConnectorAccount, im_bg_env["wecom_account_id"])
        slack_account = await session.get(IMConnectorAccount, im_bg_env["slack_account_id"])
        assert wecom_account is not None and slack_account is not None

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
    wc_run_id, wc_item = await _drain_initial_inbound(
        account_id=wecom_account.id, run_manager=run_manager, snap=snap
    )
    assert wc_item.execution_admission_id is not None

    ev_wc = await _stage_ready_background_event(
        org_id=im_bg_env["org_id"],
        ws_id=im_bg_env["ws_id"],
        user_id=im_bg_env["user_id"],
        conversation_id=wc_item.conversation_id,
        originating_admission_id=wc_item.execution_admission_id,
        originating_run_id=wc_run_id,
    )
    assert ev_wc.id in await coordinator.deliver_once(
        now=NOW, lease_until=NOW + timedelta(seconds=15)
    )
    await run_manager.drain(timeout_seconds=30)

    async with async_session_maker() as session:
        wc_bg_item = (
            await session.execute(
                select(IMRunQueueItem).where(
                    col(IMRunQueueItem.account_id) == wecom_account.id,
                    col(IMRunQueueItem.status) == "pending",
                )
            )
        ).scalar_one()
        wc_bg_item.status = "completed"
        await session.commit()
    assert wc_bg_item.reply_to_id is None
    assert wc_bg_item.sender_open_id == "zhangsan"

    # 2) Slack thread link without prior IMRunQueueItem extracts thread_ts from scope_key
    # and hydrates sender_open_id from IMIdentityLink.
    async with async_session_maker() as session:
        slack_live = await session.get(IMConnectorAccount, im_bg_env["slack_account_id"])
        assert slack_live is not None
        resolved_slack = await resolve_im_conversation(
            session,
            slack_live,
            channel_id="C_SLACK_CHAN",
            scope_key="u:U_SLACK_1|t:1700999.123",
            scope_kind="thread",
            effective_user_id=im_bg_env["user_id"],
            title_hint="slack thread",
            origin="inbound",
        )
        adm_slack = await ConversationExecutionService(
            session,
            org_id=im_bg_env["org_id"],
            workspace_id=im_bg_env["ws_id"],
        ).admit_user_message(
            conversation_id=resolved_slack.conversation_id,
            actor_user_id=im_bg_env["user_id"],
            namespace="web",
            source_id=f"web-sl-{uuid4().hex[:8]}",
            intent=UserMessageIntent(content="slack task"),
            snapshot=snap,
            now=NOW,
        )
        await session.commit()
    assert adm_slack.admission.run_id is not None

    ev_slack = await _stage_ready_background_event(
        org_id=im_bg_env["org_id"],
        ws_id=im_bg_env["ws_id"],
        user_id=im_bg_env["user_id"],
        conversation_id=resolved_slack.conversation_id,
        originating_admission_id=adm_slack.admission.id,
        originating_run_id=adm_slack.admission.run_id,
    )
    assert ev_slack.id in await coordinator.deliver_once(
        now=NOW, lease_until=NOW + timedelta(seconds=15)
    )
    await run_manager.drain(timeout_seconds=30)

    async with async_session_maker() as session:
        sl_bg_item = (
            await session.execute(
                select(IMRunQueueItem).where(
                    col(IMRunQueueItem.account_id) == im_bg_env["slack_account_id"],
                    col(IMRunQueueItem.status) == "pending",
                )
            )
        ).scalar_one()
        sl_bg_item.status = "completed"
        sl_acct = await session.get(IMConnectorAccount, im_bg_env["slack_account_id"])
        assert sl_acct is not None
        sl_acct.enabled = False
        await session.commit()
    assert sl_bg_item.reply_to_id == "1700999.123"
    assert sl_bg_item.sender_open_id == "U_SLACK_1"

    # 3) Disabled account still delivers background notice in-app without IM enqueue.
    ev_disabled = await _stage_ready_background_event(
        org_id=im_bg_env["org_id"],
        ws_id=im_bg_env["ws_id"],
        user_id=im_bg_env["user_id"],
        conversation_id=resolved_slack.conversation_id,
        originating_admission_id=adm_slack.admission.id,
        originating_run_id=adm_slack.admission.run_id,
    )
    assert ev_disabled.id in await coordinator.deliver_once(
        now=NOW, lease_until=NOW + timedelta(seconds=15)
    )
    await run_manager.drain(timeout_seconds=30)

    # 4) Pure Web / new_each_run conversation placed under an IM Topic (conv.attributes == {})
    # must NOT leak its background notice to the IM channel!
    async with async_session_maker() as session:
        feishu_acct = await session.get(IMConnectorAccount, im_bg_env["feishu_account_id"])
        assert feishu_acct is not None
        im_resolved = await resolve_im_conversation(
            session,
            feishu_acct,
            channel_id="oc_topic_leak_check",
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=im_bg_env["user_id"],
            title_hint="im topic anchor",
            origin="inbound",
        )
        assert im_resolved.topic_id is not None
        non_im_child = Conversation(
            org_id=im_bg_env["org_id"],
            workspace_id=im_bg_env["ws_id"],
            creator_user_id=im_bg_env["user_id"],
            topic_id=im_resolved.topic_id,
            title="Scheduled new_each_run child",
            attributes={},
        )
        session.add(non_im_child)
        await session.flush()
        adm_child = await ConversationExecutionService(
            session,
            org_id=im_bg_env["org_id"],
            workspace_id=im_bg_env["ws_id"],
        ).admit_user_message(
            conversation_id=non_im_child.id,
            actor_user_id=im_bg_env["user_id"],
            namespace="web",
            source_id=f"web-child-{uuid4().hex[:8]}",
            intent=UserMessageIntent(content="web child task"),
            snapshot=snap,
            now=NOW,
        )
        await session.commit()
    assert adm_child.admission.run_id is not None

    ev_child = await _stage_ready_background_event(
        org_id=im_bg_env["org_id"],
        ws_id=im_bg_env["ws_id"],
        user_id=im_bg_env["user_id"],
        conversation_id=non_im_child.id,
        originating_admission_id=adm_child.admission.id,
        originating_run_id=adm_child.admission.run_id,
    )
    assert ev_child.id in await coordinator.deliver_once(
        now=NOW, lease_until=NOW + timedelta(seconds=15)
    )
    await run_manager.drain(timeout_seconds=30)

    async with async_session_maker() as session:
        refreshed_disabled = await session.get(BackgroundTaskEvent, ev_disabled.id)
        refreshed_child = await session.get(BackgroundTaskEvent, ev_child.id)
        pending = list(
            await session.scalars(
                select(IMRunQueueItem).where(
                    col(IMRunQueueItem.workspace_id) == im_bg_env["ws_id"],
                    col(IMRunQueueItem.status) == "pending",
                )
            )
        )
    assert refreshed_disabled is not None and refreshed_disabled.state == "delivered"
    assert refreshed_child is not None and refreshed_child.state == "delivered"
    assert pending == []
