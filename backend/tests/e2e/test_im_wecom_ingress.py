"""WeCom inbound routing and durable command-response tests."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.im.inbound import ingest_command_response
from cubeplex.im.types import InboundEvent
from cubeplex.im.wecom.ingress import handle_inbound_callback
from cubeplex.models.im_connector import (
    IMConnectorAccount,
    IMIdentityLink,
    IMRunQueueItem,
    IMThreadLink,
    IMWebhookReceipt,
)
from cubeplex.repositories.im_connector import (
    claim_command_response,
    mark_command_response_delivered,
    release_command_response_claim,
)

pytestmark = pytest.mark.asyncio

_ORG_ID = "org-wecomcmd"
_WS_ID = "ws-wecomcmd"
_USER_ID = "usr-wecomcmd"
_CRED_ID = "cred-wecomcmd"
_ACCOUNT_ID = "imac-wecomcmd"


def _event(*, event_id: str, text_: str = "/new") -> InboundEvent:
    return InboundEvent(
        platform="wecom",
        account_external_id="bot-wecomcmd",
        platform_event_id=event_id,
        channel_id="chat-wecomcmd",
        scope_key="u:wecom-user",
        scope_kind="participant",
        reply_to_id=f"req-{event_id}",
        inbound_message_id=event_id,
        sender_ref="wecom-user",
        sender_open_id="wecom-user",
        text=text_,
    )


def _frame(*, event_id: str, text_: str, req_id: str | None = None) -> dict[str, object]:
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": req_id or f"req-{event_id}"},
        "body": {
            "msgid": event_id,
            "chattype": "single",
            "from": {"userid": "wecom-user"},
            "msgtype": "text",
            "text": {"content": text_},
        },
    }


class _Gateway:
    def __init__(
        self,
        *,
        fail_once: bool = False,
        passive_result: dict[str, object] | None = None,
    ) -> None:
        self.fail_once = fail_once
        self.passive_result = passive_result or {"errcode": 0, "errmsg": "ok"}
        self.passive: list[tuple[str, dict[str, object]]] = []
        self.proactive: list[tuple[str, dict[str, object]]] = []

    async def send_passive(
        self,
        req_id: str,
        body: dict[str, object],
        *,
        final: bool,
        skip_if_pending: bool,
    ) -> dict[str, object]:
        assert final is True
        assert skip_if_pending is False
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("send failed")
        self.passive.append((req_id, body))
        return self.passive_result

    async def send_proactive(self, chat_id: str, body: dict[str, object]) -> dict[str, object]:
        self.proactive.append((chat_id, body))
        return {"errcode": 0, "errmsg": "ok"}


@pytest_asyncio.fixture
async def wecom_account(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[IMConnectorAccount]:
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO organizations (id, name, slug, created_at) "
                "VALUES (:id, :id, :id, NOW()) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": _ORG_ID},
        )
        await session.execute(
            text(
                "INSERT INTO workspaces (id, org_id, name, created_at) "
                "VALUES (:id, :org, :id, NOW()) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": _WS_ID, "org": _ORG_ID},
        )
        await session.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, is_active, is_superuser, "
                "is_verified, created_at, language) VALUES "
                "(:id, :email, 'x', true, false, false, NOW(), 'en') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": _USER_ID, "email": "wecom@example.com"},
        )
        await session.execute(
            text(
                "INSERT INTO credentials (id, org_id, kind, name, value_encrypted, "
                "cred_metadata, created_by_user_id, created_at, updated_at) VALUES "
                "(:id, :org, 'im_bot', 'wecom:bot', '\\x00'::bytea, '{}'::jsonb, "
                ":uid, NOW(), NOW()) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": _CRED_ID, "org": _ORG_ID, "uid": _USER_ID},
        )
        await session.execute(
            text(
                "INSERT INTO memberships (user_id, workspace_id, role, created_at, updated_at) "
                "VALUES (:uid, :ws, 'member', NOW(), NOW()) ON CONFLICT DO NOTHING"
            ),
            {"uid": _USER_ID, "ws": _WS_ID},
        )
        await session.execute(
            text(
                "INSERT INTO im_connector_accounts "
                "(id, org_id, workspace_id, platform, external_account_id, acting_user_id, "
                "credential_id, delivery_mode, enabled, config, created_at, updated_at) VALUES "
                "(:id, :org, :ws, 'wecom', 'bot-wecomcmd', :uid, :cred, "
                "'websocket', true, '{}'::jsonb, NOW(), NOW()) ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": _ACCOUNT_ID,
                "org": _ORG_ID,
                "ws": _WS_ID,
                "uid": _USER_ID,
                "cred": _CRED_ID,
            },
        )
        await session.commit()
        account = await session.get(IMConnectorAccount, _ACCOUNT_ID)
        assert account is not None

    yield account

    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM im_run_queue WHERE account_id = :id"),
            {"id": _ACCOUNT_ID},
        )
        await session.execute(
            text("DELETE FROM im_thread_links WHERE account_id = :id"),
            {"id": _ACCOUNT_ID},
        )
        await session.execute(
            text("DELETE FROM im_identity_links WHERE account_id = :id"),
            {"id": _ACCOUNT_ID},
        )
        await session.execute(
            text("DELETE FROM im_webhook_receipts WHERE account_id = :id"),
            {"id": _ACCOUNT_ID},
        )
        await session.execute(
            text("DELETE FROM im_connector_accounts WHERE id = :id"),
            {"id": _ACCOUNT_ID},
        )
        await session.execute(
            text("DELETE FROM memberships WHERE user_id = :uid AND workspace_id = :ws"),
            {"uid": _USER_ID, "ws": _WS_ID},
        )
        await session.commit()


async def test_link_command_persists_claims_and_delivers_once(
    session_factory: async_sessionmaker[AsyncSession],
    wecom_account: IMConnectorAccount,
) -> None:
    gateway = _Gateway()
    frame = _frame(event_id="link-once", text_="/link Person@Example.COM")

    await handle_inbound_callback(
        frame,
        account=wecom_account,
        session_maker=session_factory,
        gateway=gateway,
    )
    await handle_inbound_callback(
        frame,
        account=wecom_account,
        session_maker=session_factory,
        gateway=gateway,
    )

    assert len(gateway.passive) == 1
    assert gateway.passive[0][0] == "req-link-once"
    assert "Link your account" in str(gateway.passive[0][1])
    async with session_factory() as session:
        receipt = (
            await session.execute(
                select(IMWebhookReceipt).where(IMWebhookReceipt.platform_event_id == "link-once")
            )
        ).scalar_one()
        assert receipt.status == "completed"
        assert receipt.response_payload == {
            "kind": "link",
            "im_user_id": "wecom-user",
            "email": "person@example.com",
            "account_id": _ACCOUNT_ID,
            "workspace_id": _WS_ID,
            "platform": "wecom",
            "chat_id": "wecom-user",
        }


async def test_command_passive_error_falls_back_to_proactive_once(
    session_factory: async_sessionmaker[AsyncSession],
    wecom_account: IMConnectorAccount,
) -> None:
    gateway = _Gateway(
        passive_result={"errcode": 846604, "errmsg": "callback expired"},
    )
    frame = _frame(event_id="link-expired", text_="/link person@example.com")

    await handle_inbound_callback(
        frame,
        account=wecom_account,
        session_maker=session_factory,
        gateway=gateway,
    )
    await handle_inbound_callback(
        frame,
        account=wecom_account,
        session_maker=session_factory,
        gateway=gateway,
    )

    assert len(gateway.passive) == 1
    assert len(gateway.proactive) == 1
    assert gateway.proactive[0][0] == "wecom-user"
    async with session_factory() as session:
        receipt = (
            await session.execute(
                select(IMWebhookReceipt).where(IMWebhookReceipt.platform_event_id == "link-expired")
            )
        ).scalar_one()
        assert receipt.status == "completed"


async def test_unlinked_message_is_rejected_without_queueing(
    session_factory: async_sessionmaker[AsyncSession],
    wecom_account: IMConnectorAccount,
) -> None:
    gateway = _Gateway()
    await handle_inbound_callback(
        _frame(event_id="normal-unlinked", text_="hello"),
        account=wecom_account,
        session_maker=session_factory,
        gateway=gateway,
    )

    assert len(gateway.passive) == 1
    assert "/link <your-email>" in str(gateway.passive[0][1])
    async with session_factory() as session:
        queue = (
            (
                await session.execute(
                    select(IMRunQueueItem).where(IMRunQueueItem.account_id == _ACCOUNT_ID)
                )
            )
            .scalars()
            .all()
        )
        assert queue == []


async def test_linked_message_enqueues_with_callback_request_id(
    session_factory: async_sessionmaker[AsyncSession],
    wecom_account: IMConnectorAccount,
) -> None:
    async with session_factory() as session:
        session.add(
            IMIdentityLink(
                org_id=_ORG_ID,
                workspace_id=_WS_ID,
                account_id=_ACCOUNT_ID,
                im_user_id="wecom-user",
                user_id=_USER_ID,
            )
        )
        await session.commit()

    await handle_inbound_callback(
        _frame(event_id="normal-linked", text_="hello", req_id="callback-42"),
        account=wecom_account,
        session_maker=session_factory,
        gateway=_Gateway(),
    )

    async with session_factory() as session:
        item = (
            await session.execute(
                select(IMRunQueueItem).where(IMRunQueueItem.inbound_message_id == "normal-linked")
            )
        ).scalar_one()
        assert item.reply_to_id == "callback-42"


async def test_reset_reply_retry_does_not_repeat_reset_mutation(
    session_factory: async_sessionmaker[AsyncSession],
    wecom_account: IMConnectorAccount,
) -> None:
    async with session_factory() as session:
        session.add(
            IMIdentityLink(
                org_id=_ORG_ID,
                workspace_id=_WS_ID,
                account_id=_ACCOUNT_ID,
                im_user_id="wecom-user",
                user_id=_USER_ID,
            )
        )
        await session.commit()

    await handle_inbound_callback(
        _frame(event_id="before-reset", text_="first"),
        account=wecom_account,
        session_maker=session_factory,
        gateway=_Gateway(),
    )
    async with session_factory() as session:
        first_link = (
            await session.execute(
                select(IMThreadLink).where(IMThreadLink.account_id == _ACCOUNT_ID)
            )
        ).scalar_one()
        first_conversation_id = first_link.conversation_id

    gateway = _Gateway(fail_once=True)
    await handle_inbound_callback(
        _frame(event_id="reset-retry", text_="/new", req_id="req-first"),
        account=wecom_account,
        session_maker=session_factory,
        gateway=gateway,
    )

    await handle_inbound_callback(
        _frame(event_id="after-reset", text_="second"),
        account=wecom_account,
        session_maker=session_factory,
        gateway=_Gateway(),
    )
    async with session_factory() as session:
        second_link = (
            await session.execute(
                select(IMThreadLink).where(IMThreadLink.account_id == _ACCOUNT_ID)
            )
        ).scalar_one()
        second_conversation_id = second_link.conversation_id
    assert second_conversation_id != first_conversation_id

    await handle_inbound_callback(
        _frame(event_id="reset-retry", text_="/new", req_id="req-replay"),
        account=wecom_account,
        session_maker=session_factory,
        gateway=gateway,
    )

    assert [request_id for request_id, _body in gateway.passive] == ["req-replay"]
    async with session_factory() as session:
        surviving_link = (
            await session.execute(
                select(IMThreadLink).where(IMThreadLink.account_id == _ACCOUNT_ID)
            )
        ).scalar_one()
        receipt = (
            await session.execute(
                select(IMWebhookReceipt).where(IMWebhookReceipt.platform_event_id == "reset-retry")
            )
        ).scalar_one()
        assert surviving_link.conversation_id == second_conversation_id
        assert receipt.status == "completed"


async def test_removed_member_cannot_reset_existing_conversation(
    session_factory: async_sessionmaker[AsyncSession],
    wecom_account: IMConnectorAccount,
) -> None:
    async with session_factory() as session:
        session.add(
            IMIdentityLink(
                org_id=_ORG_ID,
                workspace_id=_WS_ID,
                account_id=_ACCOUNT_ID,
                im_user_id="wecom-user",
                user_id=_USER_ID,
            )
        )
        await session.commit()
    await handle_inbound_callback(
        _frame(event_id="removed-before", text_="first"),
        account=wecom_account,
        session_maker=session_factory,
        gateway=_Gateway(),
    )
    async with session_factory() as session:
        link = (
            await session.execute(
                select(IMThreadLink).where(IMThreadLink.account_id == _ACCOUNT_ID)
            )
        ).scalar_one()
        conversation_id = link.conversation_id
        await session.execute(
            text("DELETE FROM memberships WHERE user_id = :uid AND workspace_id = :ws"),
            {"uid": _USER_ID, "ws": _WS_ID},
        )
        await session.commit()

    gateway = _Gateway()
    await handle_inbound_callback(
        _frame(event_id="removed-reset", text_="/reset"),
        account=wecom_account,
        session_maker=session_factory,
        gateway=gateway,
    )

    assert "isn't a member" in str(gateway.passive[0][1])
    async with session_factory() as session:
        surviving_link = (
            await session.execute(
                select(IMThreadLink).where(IMThreadLink.account_id == _ACCOUNT_ID)
            )
        ).scalar_one()
        identity = (
            await session.execute(
                select(IMIdentityLink).where(IMIdentityLink.account_id == _ACCOUNT_ID)
            )
        ).scalar_one_or_none()
        assert surviving_link.conversation_id == conversation_id
        assert identity is None


async def test_command_transaction_persists_semantic_response_once(
    session_factory: async_sessionmaker[AsyncSession],
    wecom_account: IMConnectorAccount,
) -> None:
    calls = 0

    async def build_response(_session: AsyncSession, user_id: str | None) -> dict[str, object]:
        nonlocal calls
        calls += 1
        assert user_id is None
        return {"kind": "link", "email": "person@example.com"}

    first = await ingest_command_response(
        _event(event_id="command-once", text_="/link person@example.com"),
        account=wecom_account,
        session_maker=session_factory,
        build_response=build_response,
    )
    replay = await ingest_command_response(
        _event(event_id="command-once", text_="/link person@example.com"),
        account=wecom_account,
        session_maker=session_factory,
        build_response=build_response,
    )

    assert first.outcome == "created"
    assert replay.outcome == "duplicate"
    assert replay.receipt_id == first.receipt_id
    assert replay.response_payload == {"kind": "link", "email": "person@example.com"}
    assert calls == 1


async def test_identity_required_command_rejects_unlinked_sender(
    session_factory: async_sessionmaker[AsyncSession],
    wecom_account: IMConnectorAccount,
) -> None:
    called = False

    async def build_response(_session: AsyncSession, _user_id: str | None) -> dict[str, object]:
        nonlocal called
        called = True
        return {"kind": "text", "text": "mutated"}

    result = await ingest_command_response(
        _event(event_id="command-unlinked"),
        account=wecom_account,
        session_maker=session_factory,
        build_response=build_response,
        require_current_identity=True,
    )

    assert result.outcome == "created"
    assert result.response_payload is not None
    assert "/link <your-email>" in str(result.response_payload["text"])
    assert called is False


async def test_identity_required_command_passes_current_member_to_mutation(
    session_factory: async_sessionmaker[AsyncSession],
    wecom_account: IMConnectorAccount,
) -> None:
    async with session_factory() as session:
        session.add(
            IMIdentityLink(
                org_id=wecom_account.org_id,
                workspace_id=wecom_account.workspace_id,
                account_id=wecom_account.id,
                im_user_id="wecom-user",
                user_id=_USER_ID,
            )
        )
        await session.commit()

    async def build_response(_session: AsyncSession, user_id: str | None) -> dict[str, object]:
        return {"kind": "text", "text": f"mutated:{user_id}"}

    result = await ingest_command_response(
        _event(event_id="command-linked"),
        account=wecom_account,
        session_maker=session_factory,
        build_response=build_response,
        require_current_identity=True,
    )

    assert result.response_payload == {"kind": "text", "text": f"mutated:{_USER_ID}"}


async def test_command_response_claim_is_exclusive_and_recoverable(
    session_factory: async_sessionmaker[AsyncSession],
    wecom_account: IMConnectorAccount,
) -> None:
    async with session_factory() as session:
        receipt = IMWebhookReceipt(
            org_id=wecom_account.org_id,
            workspace_id=wecom_account.workspace_id,
            account_id=wecom_account.id,
            platform_event_id="command-claim",
            status="pending",
            response_payload={"kind": "text", "text": "done"},
        )
        session.add(receipt)
        await session.commit()
        receipt_id = receipt.id

    async with session_factory() as session:
        claim = await claim_command_response(session, receipt_id=receipt_id)
        await session.commit()
    assert claim is not None
    assert claim.payload == {"kind": "text", "text": "done"}

    async with session_factory() as session:
        assert await claim_command_response(session, receipt_id=receipt_id) is None
        await session.rollback()

    async with session_factory() as session:
        stored = (
            await session.execute(select(IMWebhookReceipt).where(IMWebhookReceipt.id == receipt_id))
        ).scalar_one()
        stored.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    async with session_factory() as session:
        claim = await claim_command_response(session, receipt_id=receipt_id)
        assert claim is not None
        assert claim.payload == {"kind": "text", "text": "done"}
        assert await mark_command_response_delivered(
            session,
            receipt_id=receipt_id,
            lease_expires_at=claim.lease_expires_at,
        )
        await session.commit()

    async with session_factory() as session:
        assert await claim_command_response(session, receipt_id=receipt_id) is None


async def test_failed_command_response_send_can_release_claim(
    session_factory: async_sessionmaker[AsyncSession],
    wecom_account: IMConnectorAccount,
) -> None:
    async with session_factory() as session:
        receipt = IMWebhookReceipt(
            org_id=wecom_account.org_id,
            workspace_id=wecom_account.workspace_id,
            account_id=wecom_account.id,
            platform_event_id="command-release",
            status="pending",
            response_payload={"kind": "text", "text": "retry"},
        )
        session.add(receipt)
        await session.commit()
        receipt_id = receipt.id

    async with session_factory() as session:
        claim = await claim_command_response(session, receipt_id=receipt_id)
        assert claim is not None
        await session.commit()
    async with session_factory() as session:
        assert await release_command_response_claim(
            session,
            receipt_id=receipt_id,
            lease_expires_at=claim.lease_expires_at,
        )
        await session.commit()
    async with session_factory() as session:
        retry_claim = await claim_command_response(session, receipt_id=receipt_id)
        assert retry_claim is not None
        assert retry_claim.payload == {
            "kind": "text",
            "text": "retry",
        }


async def test_expired_command_claim_cannot_finish_or_release_new_claim(
    session_factory: async_sessionmaker[AsyncSession],
    wecom_account: IMConnectorAccount,
) -> None:
    async with session_factory() as session:
        receipt = IMWebhookReceipt(
            org_id=wecom_account.org_id,
            workspace_id=wecom_account.workspace_id,
            account_id=wecom_account.id,
            platform_event_id="command-stale-owner",
            status="pending",
            response_payload={"kind": "text", "text": "once"},
        )
        session.add(receipt)
        await session.commit()
        receipt_id = receipt.id

    async with session_factory() as session:
        stale_claim = await claim_command_response(
            session,
            receipt_id=receipt_id,
            lease_seconds=1,
        )
        assert stale_claim is not None
        await session.commit()

    async with session_factory() as session:
        stored = await session.get(IMWebhookReceipt, receipt_id)
        assert stored is not None
        stored.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    async with session_factory() as session:
        current_claim = await claim_command_response(session, receipt_id=receipt_id)
        assert current_claim is not None
        await session.commit()

    async with session_factory() as session:
        assert not await release_command_response_claim(
            session,
            receipt_id=receipt_id,
            lease_expires_at=stale_claim.lease_expires_at,
        )
        assert not await mark_command_response_delivered(
            session,
            receipt_id=receipt_id,
            lease_expires_at=stale_claim.lease_expires_at,
        )
        await session.commit()

    async with session_factory() as session:
        stored = await session.get(IMWebhookReceipt, receipt_id)
        assert stored is not None
        assert stored.status == "pending"
        assert stored.lease_expires_at == current_claim.lease_expires_at
