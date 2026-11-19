"""E2E tests for IM /stop and /steer control commands."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.credentials.dependencies import build_credential_service
from cubeplex.im.bot_settings import IMBotSettings, store_bot_settings
from cubeplex.im.control_commands import (
    STEER_ACTOR_CONFLICT_REPLY,
    STEER_SUCCESS_REPLY,
    STEER_USAGE_REPLY,
    STOP_NO_ACTIVE_REPLY,
    STOP_SUCCESS_REPLY,
    maybe_handle_control_command,
)
from cubeplex.im.inbound import ingest_inbound_event
from cubeplex.im.types import InboundEvent, make_channel_scope
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.models.im_connector import (
    IMConnectorAccount,
    IMIdentityLink,
    IMRunQueueItem,
    IMThreadLink,
)
from cubeplex.models.steering_message import SteeringMessage
from cubeplex.models.topic import Topic
from cubeplex.streams.run_events import create_run
from cubeplex.utils.time import utc_isoformat
from tests.e2e.conftest import _build_database_url
from tests.e2e.im_fixtures import im_seed_account, im_seed_org_ws_user

pytestmark = pytest.mark.asyncio

_ORG_ID = "org-imctrl"
_WS_ID = "ws-imctrl"
_USER_1 = "usr-imctrl-1"
_USER_2 = "usr-imctrl-2"
_APP_ID = "cli_imctrl"
_BOT_OPEN_ID = "ou_imctrl_bot"
_VERIFICATION_TOKEN = "vt-imctrl"
_ENCRYPT_KEY = "ek-imctrl-32-chars-aaaaaaaaaaaaaaaa"


def _sign(*, ts: str, nonce: str, body: bytes) -> str:
    return hashlib.sha256(f"{ts}{nonce}{_ENCRYPT_KEY}{body.decode()}".encode()).hexdigest()


class _FakeIdentityResolver:
    def __init__(self, mapping: dict[str, str | None]) -> None:
        self._mapping = mapping

    async def resolve_email(self, open_id: str) -> str | None:
        return self._mapping.get(open_id)


class _FakeRejectionNotifier:
    def __init__(self) -> None:
        self.rejections: list[tuple[str, str]] = []

    async def send_to_chat(self, chat_id: str, reply_to_id: str | None, text: str) -> None:
        self.rejections.append(("text", text))

    async def send_link_required(self, chat_id: str, reply_to_id: str | None, open_id: str) -> None:
        self.rejections.append(("link_required", open_id))

    async def send_not_member(self, chat_id: str, reply_to_id: str | None, open_id: str) -> None:
        self.rejections.append(("not_member", open_id))


@pytest_asyncio.fixture
async def _im_env(
    async_client: httpx.AsyncClient,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], IMConnectorAccount, Any]]:
    transport = getattr(async_client, "_transport", None)
    app = getattr(transport, "app", None)
    if app is None:
        raise RuntimeError("async_client transport missing app")
    backend = app.state.encryption_backend

    tag = uuid4().hex[:6]
    org_id = f"org-imc-{tag}"
    ws_id = f"ws-imc-{tag}"
    user_1 = f"usr-imc1-{tag}"
    user_2 = f"usr-imc2-{tag}"
    app_id = f"cli_imc_{tag}"

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    cred_id: str | None = None
    account_id: str | None = None
    try:
        async with maker() as session:
            await im_seed_org_ws_user(
                session, org_id=org_id, ws_id=ws_id, user_id=user_1, email=f"{user_1}@example.com"
            )
            await im_seed_org_ws_user(
                session, org_id=org_id, ws_id=ws_id, user_id=user_2, email=f"{user_2}@example.com"
            )
            await session.commit()

        secret_payload = {
            "app_id": app_id,
            "app_secret": "secret",
            "encrypt_key": _ENCRYPT_KEY,
            "verification_token": _VERIFICATION_TOKEN,
            "domain": "feishu",
            "bot_open_id": _BOT_OPEN_ID,
        }
        async with maker() as session:
            svc = build_credential_service(session, backend, org_id=org_id, actor_user_id=user_1)
            cred_id = await svc.create(
                kind="im_bot",
                name=f"feishu:{app_id}",
                plaintext=json.dumps(secret_payload),
            )
            account_id = f"imac-imc-{tag}"
            await im_seed_account(
                session,
                account_id=account_id,
                org_id=org_id,
                ws_id=ws_id,
                user_id=user_1,
                platform="feishu",
                external_account_id=app_id,
                credential_id=cred_id,
            )
            acct = await session.get(IMConnectorAccount, account_id)
            assert acct is not None
            acct.config = store_bot_settings({}, IMBotSettings(routing_mode="shared"))
            session.add(
                IMIdentityLink(
                    org_id=org_id,
                    workspace_id=ws_id,
                    account_id=acct.id,
                    im_user_id="ou_u1",
                    user_id=user_1,
                )
            )
            session.add(
                IMIdentityLink(
                    org_id=org_id,
                    workspace_id=ws_id,
                    account_id=acct.id,
                    im_user_id="ou_u2",
                    user_id=user_2,
                )
            )
            await session.commit()
            await session.refresh(acct)

        yield maker, acct, app
    finally:
        await engine.dispose()


async def _seed_active_conversation(
    maker: async_sessionmaker[AsyncSession],
    acct: IMConnectorAccount,
    app: Any,
    *,
    channel_id: str,
    scope_key: str,
    conv_id: str,
    run_id: str,
    actor_user_id: str,
) -> None:
    async with maker() as session:
        session.add(
            Conversation(
                id=conv_id,
                org_id=acct.org_id,
                workspace_id=acct.workspace_id,
                creator_user_id=actor_user_id,
                title="IM chat",
            )
        )
        await session.flush()
        session.add(
            IMThreadLink(
                org_id=acct.org_id,
                workspace_id=acct.workspace_id,
                account_id=acct.id,
                channel_id=channel_id,
                scope_key=scope_key,
                scope_kind="channel",
                conversation_id=conv_id,
            )
        )
        session.add(
            ConversationExecutionAdmission(
                id=f"adm-{run_id}",
                org_id=acct.org_id,
                workspace_id=acct.workspace_id,
                conversation_id=conv_id,
                actor_user_id=actor_user_id,
                source_kind="user_message",
                source_id=f"src-{run_id}",
                execution_kind="run",
                run_id=run_id,
                execution_generation=0,
                run_started_at=datetime.now(UTC),
            )
        )
        await session.commit()

    await create_run(
        app.state.redis,
        prefix=app.state.redis_key_prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="running",
        started_at=utc_isoformat(datetime.now(UTC)),
        ttl_seconds=300,
    )


async def test_stop_when_idle_creates_no_conversation_or_topic(
    _im_env: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount, Any],
) -> None:
    maker, acct, app = _im_env
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    event = InboundEvent(
        platform="feishu",
        platform_event_id="evt-stop-idle",
        account_external_id=acct.external_account_id,
        channel_id="oc_idle",
        scope_key="channel",
        scope_kind="channel",
        sender_ref="ou_u1",
        sender_open_id="ou_u1",
        text="/stop",
        reply_to_id="om_1",
        inbound_message_id="om_1",
    )
    handled = await maybe_handle_control_command(
        event,
        account=acct,
        session_maker=maker,
        run_manager=app.state.run_manager,
        reply=reply,
        identity_resolver=_FakeIdentityResolver({"ou_u1": None}),
        rejection_notifier=_FakeRejectionNotifier(),
    )
    assert handled is True
    assert replies == [STOP_NO_ACTIVE_REPLY]

    async with maker() as session:
        convs = (
            (await session.execute(select(Conversation).where(Conversation.org_id == acct.org_id)))
            .scalars()
            .all()
        )
        topics = (
            (await session.execute(select(Topic).where(Topic.org_id == acct.org_id)))
            .scalars()
            .all()
        )
        assert convs == []
        assert topics == []


async def test_stop_active_run_marks_admission_stopped_and_is_idempotent(
    _im_env: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount, Any],
) -> None:
    maker, acct, app = _im_env
    await _seed_active_conversation(
        maker,
        acct,
        app,
        channel_id="oc_stop",
        scope_key="channel",
        conv_id=f"conv-stop-{acct.id[-6:]}",
        run_id=f"run-stop-{acct.id[-6:]}",
        actor_user_id=acct.acting_user_id,
    )

    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    event = InboundEvent(
        platform="feishu",
        platform_event_id="evt-stop-1",
        account_external_id=acct.external_account_id,
        channel_id="oc_stop",
        scope_key="channel",
        scope_kind="channel",
        sender_ref="ou_u1",
        sender_open_id="ou_u1",
        text="/stop",
        reply_to_id="om_s1",
        inbound_message_id="om_s1",
    )
    with patch.object(app.state.run_manager, "notify_run_stop", new=AsyncMock()) as mock_notify:
        handled = await maybe_handle_control_command(
            event,
            account=acct,
            session_maker=maker,
            run_manager=app.state.run_manager,
            reply=reply,
            identity_resolver=_FakeIdentityResolver({"ou_u1": None}),
            rejection_notifier=_FakeRejectionNotifier(),
        )
        assert handled is True
        assert replies == [STOP_SUCCESS_REPLY]
        mock_notify.assert_awaited_once_with(f"run-stop-{acct.id[-6:]}")

        # Duplicate redelivery is a no-op
        handled_dup = await maybe_handle_control_command(
            event,
            account=acct,
            session_maker=maker,
            run_manager=app.state.run_manager,
            reply=reply,
            identity_resolver=_FakeIdentityResolver({"ou_u1": None}),
            rejection_notifier=_FakeRejectionNotifier(),
        )
        assert handled_dup is True
        assert replies == [STOP_SUCCESS_REPLY]

    async with maker() as session:
        adm = await session.get(ConversationExecutionAdmission, f"adm-run-stop-{acct.id[-6:]}")
        assert adm is not None
        assert adm.run_stop_requested_at is not None


async def test_steer_active_run_and_shared_actor_conflict(
    _im_env: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount, Any],
) -> None:
    maker, acct, app = _im_env
    conv_id = f"conv-steer-{acct.id[-6:]}"
    run_id = f"run-steer-{acct.id[-6:]}"
    await _seed_active_conversation(
        maker,
        acct,
        app,
        channel_id="oc_steer",
        scope_key="channel",
        conv_id=conv_id,
        run_id=run_id,
        actor_user_id=acct.acting_user_id,
    )

    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    # 1. Bare /steer returns usage hint
    bare = InboundEvent(
        platform="feishu",
        platform_event_id="evt-steer-bare",
        account_external_id=acct.external_account_id,
        channel_id="oc_steer",
        scope_key="channel",
        scope_kind="channel",
        sender_ref="ou_u1",
        sender_open_id="ou_u1",
        text="/steer   ",
        reply_to_id="om_b",
        inbound_message_id="om_b",
    )
    assert (
        await maybe_handle_control_command(
            bare,
            account=acct,
            session_maker=maker,
            run_manager=app.state.run_manager,
            reply=reply,
            identity_resolver=_FakeIdentityResolver({"ou_u1": None}),
            rejection_notifier=_FakeRejectionNotifier(),
        )
        is True
    )
    assert replies[-1] == STEER_USAGE_REPLY

    # 2. Second member in shared chat cannot /steer first member's run
    other = InboundEvent(
        platform="feishu",
        platform_event_id="evt-steer-conflict",
        account_external_id=acct.external_account_id,
        channel_id="oc_steer",
        scope_key="channel",
        scope_kind="channel",
        sender_ref="ou_u2",
        sender_open_id="ou_u2",
        text="/steer hijack",
        reply_to_id="om_c",
        inbound_message_id="om_c",
    )
    assert (
        await maybe_handle_control_command(
            other,
            account=acct,
            session_maker=maker,
            run_manager=app.state.run_manager,
            reply=reply,
            identity_resolver=_FakeIdentityResolver({"ou_u2": None}),
            rejection_notifier=_FakeRejectionNotifier(),
        )
        is True
    )
    assert replies[-1] == STEER_ACTOR_CONFLICT_REPLY

    # 3. Originating member can /steer mid-run without creating IMRunQueueItem
    owner = InboundEvent(
        platform="feishu",
        platform_event_id="evt-steer-ok",
        account_external_id=acct.external_account_id,
        channel_id="oc_steer",
        scope_key="channel",
        scope_kind="channel",
        sender_ref="ou_u1",
        sender_open_id="ou_u1",
        text="/steer focus on tests",
        reply_to_id="om_ok",
        inbound_message_id="om_ok",
    )
    with patch.object(app.state.run_manager, "notify_durable_steer", new=AsyncMock()) as mock_wake:
        assert (
            await maybe_handle_control_command(
                owner,
                account=acct,
                session_maker=maker,
                run_manager=app.state.run_manager,
                reply=reply,
                identity_resolver=_FakeIdentityResolver({"ou_u1": None}),
                rejection_notifier=_FakeRejectionNotifier(),
            )
            is True
        )
        assert replies[-1] == STEER_SUCCESS_REPLY
        mock_wake.assert_awaited_once()

    async with maker() as session:
        steers = (
            (
                await session.execute(
                    select(SteeringMessage).where(SteeringMessage.conversation_id == conv_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(steers) == 1
        assert steers[0].content == "focus on tests"
        queue_items = (
            (
                await session.execute(
                    select(IMRunQueueItem).where(IMRunQueueItem.account_id == acct.id)
                )
            )
            .scalars()
            .all()
        )
        assert queue_items == []


async def test_steer_idle_fallback_ingests_stripped_text_and_dedupes(
    _im_env: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount, Any],
) -> None:
    maker, acct, app = _im_env
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    event = InboundEvent(
        platform="feishu",
        platform_event_id="evt-steer-idle",
        account_external_id=acct.external_account_id,
        channel_id="oc_idle_steer",
        scope_key="channel",
        scope_kind="channel",
        sender_ref="ou_u1",
        sender_open_id="ou_u1",
        text="/steer start fresh",
        reply_to_id="om_is",
        inbound_message_id="om_is",
    )
    handled = await maybe_handle_control_command(
        event,
        account=acct,
        session_maker=maker,
        run_manager=app.state.run_manager,
        reply=reply,
        identity_resolver=_FakeIdentityResolver({"ou_u1": None}),
        rejection_notifier=_FakeRejectionNotifier(),
    )
    assert handled is False
    assert event.text == "start fresh"
    assert replies == []

    ingest_res = await ingest_inbound_event(event, account=acct, session_maker=maker)
    assert ingest_res.outcome == "enqueued"

    # Redelivery with same platform_event_id is deduped by maybe_handle_control_command
    redelivery = InboundEvent(
        platform="feishu",
        platform_event_id="evt-steer-idle",
        account_external_id=acct.external_account_id,
        channel_id="oc_idle_steer",
        scope_key="channel",
        scope_kind="channel",
        sender_ref="ou_u1",
        sender_open_id="ou_u1",
        text="/steer start fresh",
        reply_to_id="om_is",
        inbound_message_id="om_is",
    )
    assert (
        await maybe_handle_control_command(
            redelivery,
            account=acct,
            session_maker=maker,
            run_manager=app.state.run_manager,
            reply=reply,
            identity_resolver=_FakeIdentityResolver({"ou_u1": None}),
            rejection_notifier=_FakeRejectionNotifier(),
        )
        is True
    )

    async with maker() as session:
        items = (
            (
                await session.execute(
                    select(IMRunQueueItem).where(IMRunQueueItem.account_id == acct.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(items) == 1
        assert items[0].content == "start fresh"


async def test_unlinked_sender_is_rejected_on_stop_and_steer(
    _im_env: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount, Any],
) -> None:
    maker, acct, app = _im_env
    notifier = _FakeRejectionNotifier()
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    event = InboundEvent(
        platform="feishu",
        platform_event_id="evt-unlinked-stop",
        account_external_id=acct.external_account_id,
        channel_id="oc_unlinked",
        scope_key="channel",
        scope_kind="channel",
        sender_ref="ou_stranger",
        sender_open_id="ou_stranger",
        text="/stop",
        reply_to_id="om_u",
        inbound_message_id="om_u",
    )
    handled = await maybe_handle_control_command(
        event,
        account=acct,
        session_maker=maker,
        run_manager=app.state.run_manager,
        reply=reply,
        identity_resolver=_FakeIdentityResolver({"ou_stranger": None}),
        rejection_notifier=notifier,
    )
    assert handled is True
    assert len(notifier.rejections) == 1
    assert replies == []


async def test_feishu_webhook_routes_steer_during_active_run(
    async_client: httpx.AsyncClient,
    _im_env: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount, Any],
) -> None:
    maker, acct, app = _im_env
    conv_id = f"conv-fwh-{acct.id[-6:]}"
    run_id = f"run-fwh-{acct.id[-6:]}"
    await _seed_active_conversation(
        maker,
        acct,
        app,
        channel_id="oc_feishu_wh",
        scope_key=make_channel_scope(),
        conv_id=conv_id,
        run_id=run_id,
        actor_user_id=acct.acting_user_id,
    )

    payload = {
        "schema": "2.0",
        "header": {
            "event_id": f"feishu-steer-evt-{acct.id[-6:]}",
            "event_type": "im.message.receive_v1",
            "token": _VERIFICATION_TOKEN,
            "app_id": acct.external_account_id,
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_u1", "union_id": "ou_u1"}},
            "message": {
                "message_id": "om_feishu_wh_1",
                "chat_id": "oc_feishu_wh",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "@_user_1 /steer check logs"}),
                "mentions": [{"key": "@_user_1", "id": {"open_id": _BOT_OPEN_ID}}],
            },
        },
    }
    raw_body = json.dumps(payload).encode()
    ts = "1700000000"
    nonce = "nonce1"
    headers = {
        "Content-Type": "application/json",
        "x-lark-request-timestamp": ts,
        "x-lark-request-nonce": nonce,
        "x-lark-signature": _sign(ts=ts, nonce=nonce, body=raw_body),
    }
    mock_gate = MagicMock()
    mock_gate.send_to_chat = AsyncMock(return_value="om_reply_1")
    with (
        patch("cubeplex.api.routes.v1.im_ingress._build_gate_connector", return_value=mock_gate),
        patch.object(app.state.run_manager, "notify_durable_steer", new=AsyncMock()),
    ):
        resp = await async_client.post(
            "/api/v1/im/feishu/events", content=raw_body, headers=headers
        )
    assert resp.status_code == 200
    mock_gate.send_to_chat.assert_awaited_once_with(
        "oc_feishu_wh", "om_feishu_wh_1", STEER_SUCCESS_REPLY
    )

    async with maker() as session:
        steers = (
            (
                await session.execute(
                    select(SteeringMessage).where(SteeringMessage.conversation_id == conv_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(steers) == 1
        assert steers[0].content == "check logs"


async def test_slack_slash_steer_idle_fallback_preserves_linked_actor(
    _im_env: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount, Any],
) -> None:
    from cubeplex.im.control_commands import STEER_IDLE_FALLBACK_SLASH_REPLY
    from cubeplex.im.slack.commands import _handle_steer_slash

    maker, acct, app = _im_env
    async with maker() as session:
        link = (
            await session.execute(
                select(IMIdentityLink).where(
                    IMIdentityLink.account_id == acct.id,
                    IMIdentityLink.im_user_id == "ou_u2",
                )
            )
        ).scalar_one()
        u2_id = link.user_id

    ephemeral_replies: list[str] = []

    async def respond(text: str, *, response_type: str = "ephemeral") -> None:
        assert response_type == "ephemeral"
        ephemeral_replies.append(text)

    await _handle_steer_slash(
        {
            "channel_id": "C_SLACK_SLASH",
            "user_id": "ou_u2",
            "text": "from slash fallback",
            "trigger_id": f"trig-{acct.id[-6:]}",
        },
        respond,
        account_id=acct.id,
        session_maker=maker,
        run_manager=app.state.run_manager,
    )
    assert ephemeral_replies == [STEER_IDLE_FALLBACK_SLASH_REPLY]

    async with maker() as session:
        items = (
            (
                await session.execute(
                    select(IMRunQueueItem).where(
                        IMRunQueueItem.account_id == acct.id,
                        IMRunQueueItem.channel_id == "C_SLACK_SLASH",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(items) == 1
        assert items[0].content == "from slash fallback"
        assert items[0].actor_user_id == u2_id
        assert items[0].actor_user_id != acct.acting_user_id


async def test_wecom_stop_signals_after_commit_and_delivers_reply(
    _im_env: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount, Any],
) -> None:
    from cubeplex.im.wecom.ingress import handle_inbound_callback

    maker, acct, app = _im_env
    conv_id = f"conv-wecom-{acct.id[-6:]}"
    run_id = f"run-wecom-{acct.id[-6:]}"
    await _seed_active_conversation(
        maker,
        acct,
        app,
        channel_id="wc_chat_1",
        scope_key=make_channel_scope(),
        conv_id=conv_id,
        run_id=run_id,
        actor_user_id=acct.acting_user_id,
    )

    observed_stop_requested_at: list[Any] = []

    async def _check_committed_stop(target_run_id: str) -> None:
        assert target_run_id == run_id
        async with maker() as session:
            adm = await session.get(ConversationExecutionAdmission, f"adm-{run_id}")
            assert adm is not None
            observed_stop_requested_at.append(adm.run_stop_requested_at)

    fake_gateway = MagicMock()
    fake_gateway.send_passive = AsyncMock(return_value={"errcode": 0})
    fake_gateway.send_proactive = AsyncMock(return_value={"errcode": 0})

    raw = {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": f"req-wecom-{acct.id[-6:]}"},
        "body": {
            "msgid": f"msg-wecom-{acct.id[-6:]}",
            "aibotid": acct.external_account_id,
            "chatid": "wc_chat_1",
            "chattype": "group",
            "from": {"userid": "ou_u1"},
            "msgtype": "text",
            "text": {"content": "/stop"},
        },
    }
    with patch.object(app.state.run_manager, "notify_run_stop", side_effect=_check_committed_stop):
        await handle_inbound_callback(
            raw,
            account=acct,
            session_maker=maker,
            gateway=fake_gateway,
            run_manager=app.state.run_manager,
        )

    assert len(observed_stop_requested_at) == 1
    assert observed_stop_requested_at[0] is not None
    fake_gateway.send_passive.assert_awaited_once()
    sent_body = fake_gateway.send_passive.call_args.args[1]
    assert STOP_SUCCESS_REPLY in sent_body["markdown"]["content"]
