"""Integration tests for the IM transactional inbound core (Task 5).

Exercises ``ingest_inbound_event`` against a real Postgres test database:
- First event for a new (account, channel, scope_key) creates a Conversation
  and IMThreadLink, enqueues an IMRunQueueItem.
- Duplicate event id is acked without a second enqueue.
- Same sender's next @ in the same group reuses the same conversation
  (chat × user session model).
- Distinct senders in the same group get distinct conversations.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cubeplex.im.inbound import ingest_inbound_event
from cubeplex.im.types import InboundEvent
from cubeplex.models.im_connector import (
    IMConnectorAccount,
    IMRunQueueItem,
    IMThreadLink,
    IMWebhookReceipt,
)
from tests.e2e.conftest import _build_database_url

pytestmark = pytest.mark.asyncio


_ORG_ID = "org-imtxnA"
_WS_ID = "ws-imtxnA"
_USER_ID = "usr-imtxnA"
_CRED_ID = "cred-imtxnA"
_ACCOUNT_ID = "imac-imtxnA"


@pytest_asyncio.fixture
async def _seeded_engine_and_account() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], IMConnectorAccount]
]:
    """Seed an org / ws / user / credential / IMConnectorAccount; yield a
    fresh session_maker bound to the test DB.

    Uses raw SQL for the dependency rows (matching tests/e2e/test_credentials_vault.py's
    pattern) so the fixture stays cheap and avoids pulling in the full auth
    bootstrap path.
    """
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            await session.execute(
                text(
                    "INSERT INTO organizations (id, name, slug, created_at)"
                    " VALUES (:id, :id, :id, NOW())"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _ORG_ID},
            )
            await session.execute(
                text(
                    "INSERT INTO workspaces (id, org_id, name, created_at)"
                    " VALUES (:id, :org, :id, NOW())"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _WS_ID, "org": _ORG_ID},
            )
            await session.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, is_active,"
                    " is_superuser, is_verified, created_at, language)"
                    " VALUES (:id, :email, 'x', true, false, false, NOW(), 'en')"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _USER_ID, "email": f"{_USER_ID}@example.com"},
            )
            await session.execute(
                text(
                    "INSERT INTO credentials (id, org_id, kind, name, value_encrypted,"
                    " cred_metadata, created_by_user_id, created_at, updated_at)"
                    " VALUES (:id, :org, 'im_bot', 'feishu:T-txnA', '\\x00'::bytea,"
                    " '{}'::jsonb, :uid, NOW(), NOW())"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _CRED_ID, "org": _ORG_ID, "uid": _USER_ID},
            )
            await session.execute(
                text(
                    "INSERT INTO im_connector_accounts (id, org_id, workspace_id,"
                    " platform, external_account_id, acting_user_id, credential_id,"
                    " delivery_mode, enabled, config, created_at, updated_at)"
                    " VALUES (:id, :org, :ws, 'feishu', 'cli_txnA', :uid, :cred,"
                    " 'long_connection', true, '{}'::jsonb, NOW(), NOW())"
                    " ON CONFLICT (id) DO NOTHING"
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

            account = (
                await session.execute(
                    select(IMConnectorAccount).where(IMConnectorAccount.id == _ACCOUNT_ID)
                )
            ).scalar_one()

        try:
            yield maker, account
        finally:
            async with maker() as session:
                await session.execute(
                    text("DELETE FROM im_run_queue WHERE account_id = :id"),
                    {"id": _ACCOUNT_ID},
                )
                await session.execute(
                    text("DELETE FROM im_webhook_receipts WHERE account_id = :id"),
                    {"id": _ACCOUNT_ID},
                )
                await session.execute(
                    text("DELETE FROM im_thread_links WHERE account_id = :id"),
                    {"id": _ACCOUNT_ID},
                )
                await session.execute(
                    text("DELETE FROM im_connector_accounts WHERE id = :id"),
                    {"id": _ACCOUNT_ID},
                )
                await session.execute(
                    text("DELETE FROM conversations WHERE workspace_id = :id"),
                    {"id": _WS_ID},
                )
                await session.commit()
    finally:
        await engine.dispose()


def _event(
    *,
    event_id: str = "ev1",
    scope_key: str = "u:on_userA",
    scope_kind: str = "participant",
    text_: str = "hello",
) -> InboundEvent:
    return InboundEvent(
        platform="feishu",
        account_external_id="cli_txnA",
        platform_event_id=event_id,
        channel_id="oc_chat1",
        scope_key=scope_key,
        scope_kind=scope_kind,
        reply_to_id="om_msg1",
        inbound_message_id="om_msg1",
        sender_ref="on_userA",
        sender_open_id="ou_userA",
        text=text_,
    )


async def test_first_event_creates_link_and_enqueues(
    _seeded_engine_and_account: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded_engine_and_account
    res = await ingest_inbound_event(_event(), account=account, session_maker=maker)
    assert res.outcome == "enqueued"
    async with maker() as s:
        item_count = (
            await s.execute(
                select(func.count())
                .select_from(IMRunQueueItem)
                .where(IMRunQueueItem.account_id == account.id)
            )
        ).scalar()
        assert item_count == 1
        link = (
            await s.execute(select(IMThreadLink).where(IMThreadLink.account_id == account.id))
        ).scalar_one()
        assert link.scope_key == "u:on_userA"
        assert link.scope_kind == "participant"
        assert res.conversation_id == link.conversation_id


async def test_duplicate_event_does_not_double_enqueue(
    _seeded_engine_and_account: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded_engine_and_account
    r1 = await ingest_inbound_event(_event(event_id="evdup"), account=account, session_maker=maker)
    r2 = await ingest_inbound_event(_event(event_id="evdup"), account=account, session_maker=maker)
    assert r1.outcome == "enqueued"
    assert r2.outcome == "duplicate"
    async with maker() as s:
        item_count = (
            await s.execute(
                select(func.count())
                .select_from(IMRunQueueItem)
                .where(IMRunQueueItem.account_id == account.id)
            )
        ).scalar()
        receipt_count = (
            await s.execute(
                select(func.count())
                .select_from(IMWebhookReceipt)
                .where(IMWebhookReceipt.account_id == account.id)
            )
        ).scalar()
        assert item_count == 1
        assert receipt_count == 1


async def test_same_sender_in_same_group_reuses_conversation(
    _seeded_engine_and_account: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Chat × user session model: A's second @ in the same group joins A's
    existing conversation, not a new one."""
    maker, account = _seeded_engine_and_account
    r1 = await ingest_inbound_event(_event(event_id="evA"), account=account, session_maker=maker)
    r2 = await ingest_inbound_event(_event(event_id="evB"), account=account, session_maker=maker)
    assert r1.conversation_id == r2.conversation_id
    async with maker() as s:
        link_count = (
            await s.execute(
                select(func.count())
                .select_from(IMThreadLink)
                .where(IMThreadLink.account_id == account.id)
            )
        ).scalar()
        assert link_count == 1


async def test_distinct_senders_get_distinct_conversations(
    _seeded_engine_and_account: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded_engine_and_account
    r_a = await ingest_inbound_event(
        _event(event_id="evA", scope_key="u:on_userA"),
        account=account,
        session_maker=maker,
    )
    r_b = await ingest_inbound_event(
        _event(event_id="evB", scope_key="u:on_userB"),
        account=account,
        session_maker=maker,
    )
    assert r_a.conversation_id != r_b.conversation_id


async def test_dm_scope_reuse(
    _seeded_engine_and_account: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """DM scope is per-chat: two DM messages from the same user land in the
    same rolling conversation."""
    maker, account = _seeded_engine_and_account
    dm_event = InboundEvent(
        platform="feishu",
        account_external_id="cli_txnA",
        platform_event_id="evdm1",
        channel_id="oc_dm1",
        scope_key="dm",
        scope_kind="dm",
        reply_to_id=None,
        inbound_message_id="om_dm1",
        sender_ref="on_userA",
        sender_open_id="ou_userA",
        text="hello",
    )
    r1 = await ingest_inbound_event(dm_event, account=account, session_maker=maker)
    dm_event2 = InboundEvent(
        platform="feishu",
        account_external_id="cli_txnA",
        platform_event_id="evdm2",
        channel_id="oc_dm1",
        scope_key="dm",
        scope_kind="dm",
        reply_to_id=None,
        inbound_message_id="om_dm2",
        sender_ref="on_userA",
        sender_open_id="ou_userA",
        text="follow up",
    )
    r2 = await ingest_inbound_event(dm_event2, account=account, session_maker=maker)
    assert r1.conversation_id == r2.conversation_id
