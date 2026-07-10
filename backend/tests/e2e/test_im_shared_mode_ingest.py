"""E2E tests for inbound lifecycle across routing/topic modes.

Routing/topic is account-level (``IMBotSettings`` on ``account.config``):
- shared → one Topic + Conversation for the channel, is_group_chat True.
- isolated + topic (the default) → a per-sender Topic, is_group_chat False.
- isolated + flat → no Topic (standalone personal conversation).
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cubebox.im.bot_settings import IMBotSettings, store_bot_settings
from cubebox.im.inbound import ingest_inbound_event
from cubebox.im.types import InboundEvent
from cubebox.models.conversation import Conversation
from cubebox.models.im_connector import IMConnectorAccount, IMThreadLink
from cubebox.models.topic import Topic, TopicParticipant
from tests.e2e.conftest import _build_database_url
from tests.e2e.im_fixtures import (
    im_cleanup,
    im_seed_account,
    im_seed_org_ws_user,
    im_seed_stub_credential,
)

pytestmark = pytest.mark.asyncio

_ORG = "org-icb-ingest-001"
_WS = "ws-icb-ingest-001"
_USER = "usr-icb-ingest-001"
_USER2 = "usr-icb-ingest-002"
_CRED = "cred-icb-ingest-001"
_ACCOUNT = "imac-icb-ingest-001"
_CHANNEL = "oc_shared_ch1"
_EXT_ACCT = "cli_icb_ingest"


async def _set(
    maker: async_sessionmaker[AsyncSession],
    account: IMConnectorAccount,
    settings: IMBotSettings,
) -> None:
    """Persist account-level settings to the DB. ingest reloads the account at
    its boundary, so settings must live in the row, not just in memory."""
    account.config = store_bot_settings(account.config, settings)
    async with maker() as session:
        row = await session.get(IMConnectorAccount, account.id)
        assert row is not None
        row.config = account.config
        await session.commit()


@pytest_asyncio.fixture
async def _seeded() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], IMConnectorAccount]]:
    """Seed org/ws/users/cred/account. Yield session_maker + account."""
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            await im_seed_org_ws_user(session, org_id=_ORG, ws_id=_WS, user_id=_USER)
            await im_seed_org_ws_user(session, org_id=_ORG, ws_id=_WS, user_id=_USER2)
            await im_seed_stub_credential(
                session,
                credential_id=_CRED,
                org_id=_ORG,
                user_id=_USER,
            )
            await im_seed_account(
                session,
                account_id=_ACCOUNT,
                org_id=_ORG,
                ws_id=_WS,
                user_id=_USER,
                credential_id=_CRED,
                external_account_id=_EXT_ACCT,
            )
            await session.commit()

            account = (
                await session.execute(
                    select(IMConnectorAccount).where(
                        IMConnectorAccount.id == _ACCOUNT,
                    )
                )
            ).scalar_one()

        try:
            yield maker, account
        finally:
            async with maker() as session:
                await session.execute(
                    text(
                        "DELETE FROM conversation_participants "
                        "WHERE conversation_id IN "
                        "(SELECT id FROM conversations WHERE workspace_id = :ws)"
                    ),
                    {"ws": _WS},
                )
                await session.execute(
                    text("UPDATE conversations SET topic_id = NULL WHERE workspace_id = :ws"),
                    {"ws": _WS},
                )
                # im_thread_links.topic_id FKs topics — null before deleting topics.
                await session.execute(
                    text("UPDATE im_thread_links SET topic_id = NULL WHERE account_id = ANY(:ids)"),
                    {"ids": [_ACCOUNT]},
                )
                await session.execute(
                    text(
                        "DELETE FROM topic_participants WHERE topic_id IN "
                        "(SELECT id FROM topics WHERE org_id = :org)"
                    ),
                    {"org": _ORG},
                )
                await session.execute(text("DELETE FROM topics WHERE org_id = :org"), {"org": _ORG})
                await im_cleanup(
                    session,
                    account_ids=[_ACCOUNT],
                    credential_ids=[_CRED],
                    ws_ids=[_WS],
                    user_ids=[_USER, _USER2],
                    org_ids=[_ORG],
                    cleanup_conversations_in_ws=True,
                )
                await session.commit()
    finally:
        await engine.dispose()


def _event(
    *,
    event_id: str = "ev-shared-1",
    scope_key: str = "ch",
    scope_kind: str = "channel",
    channel_id: str = _CHANNEL,
    text_: str = "hello shared",
    sender_ref: str = "on_senderA",
    sender_open_id: str = "ou_senderA",
) -> InboundEvent:
    return InboundEvent(
        platform="feishu",
        account_external_id=_EXT_ACCT,
        platform_event_id=event_id,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        reply_to_id="om_reply1",
        inbound_message_id="om_msg1",
        sender_ref=sender_ref,
        sender_open_id=sender_open_id,
        text=text_,
    )


async def test_first_shared_message_creates_topic(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """First @bot in shared mode creates Topic + owner participant + group conv."""
    maker, account = _seeded
    await _set(maker, account, IMBotSettings(routing_mode="shared"))

    res = await ingest_inbound_event(_event(), account=account, session_maker=maker)
    assert res.outcome == "enqueued"

    async with maker() as s:
        conv = (
            await s.execute(select(Conversation).where(Conversation.id == res.conversation_id))
        ).scalar_one()
        assert conv.topic_id is not None
        assert conv.is_group_chat is True
        assert conv.attributes.get("im", {}).get("account_id") == _ACCOUNT

        topic = (await s.execute(select(Topic).where(Topic.id == conv.topic_id))).scalar_one()
        assert topic.title == ""  # no platform name → empty (UI localizes), never channel id
        assert topic.max_participants == 100
        assert topic.attributes["im"]["scope_kind"] == "channel"

        owner_tp = (
            await s.execute(
                select(TopicParticipant).where(
                    TopicParticipant.topic_id == topic.id,
                    TopicParticipant.user_id == _USER,
                )
            )
        ).scalar_one()
        assert owner_tp.role == "owner"

        link = (
            await s.execute(select(IMThreadLink).where(IMThreadLink.account_id == _ACCOUNT))
        ).scalar_one()
        assert link.topic_id == conv.topic_id


async def test_subsequent_shared_reuses_topic(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Second message in shared mode reuses the same conversation + topic."""
    maker, account = _seeded
    await _set(maker, account, IMBotSettings(routing_mode="shared"))

    r1 = await ingest_inbound_event(
        _event(event_id="ev-sub-1"), account=account, session_maker=maker
    )
    r2 = await ingest_inbound_event(
        _event(event_id="ev-sub-2"), account=account, session_maker=maker
    )
    assert r1.outcome == "enqueued" and r2.outcome == "enqueued"
    assert r1.conversation_id == r2.conversation_id

    async with maker() as s:
        conv = (
            await s.execute(select(Conversation).where(Conversation.id == r1.conversation_id))
        ).scalar_one()
        owners = (
            (
                await s.execute(
                    select(TopicParticipant).where(
                        TopicParticipant.topic_id == conv.topic_id,
                        TopicParticipant.role == "owner",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(owners) == 1


async def test_default_isolated_creates_per_sender_topic(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Default settings (empty config) = isolated + topic: a per-sender Topic
    is created, owned by the sender, conversation stays personal."""
    maker, account = _seeded
    # No _set(): account.config is empty → defaults (isolated, topic).

    res = await ingest_inbound_event(
        _event(event_id="ev-def-1", scope_key="u:on_senderA", scope_kind="participant"),
        account=account,
        session_maker=maker,
    )
    assert res.outcome == "enqueued"

    async with maker() as s:
        conv = (
            await s.execute(select(Conversation).where(Conversation.id == res.conversation_id))
        ).scalar_one()
        assert conv.topic_id is not None
        assert conv.is_group_chat is False

        topic = (await s.execute(select(Topic).where(Topic.id == conv.topic_id))).scalar_one()
        assert topic.creator_user_id == _USER  # sender owns their own topic
        assert "im" in topic.attributes


async def test_ingest_reloads_stale_account_settings(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """A long-connection transport holds the account captured at startup. When
    settings change in the DB, ingest must reload and honor them — without a
    reconnect. Here the passed account object stays stale (empty config) while
    the DB row is switched to shared."""
    maker, account = _seeded
    async with maker() as s:
        row = await s.get(IMConnectorAccount, account.id)
        assert row is not None
        row.config = store_bot_settings(row.config, IMBotSettings(routing_mode="shared"))
        await s.commit()
    # The in-memory transport object is deliberately NOT updated.
    assert (account.config or {}).get("bot_settings") is None

    res = await ingest_inbound_event(
        _event(event_id="ev-stale-1"), account=account, session_maker=maker
    )
    assert res.outcome == "enqueued"
    async with maker() as s:
        conv = (
            await s.execute(select(Conversation).where(Conversation.id == res.conversation_id))
        ).scalar_one()
        # Reloaded config took effect: shared routing → group chat + topic.
        assert conv.is_group_chat is True
        assert conv.topic_id is not None


async def test_flat_mode_no_topic(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Isolated + flat: no Topic; conversation.topic_id is None."""
    maker, account = _seeded
    await _set(maker, account, IMBotSettings(routing_mode="isolated", topic_mode="flat"))

    res = await ingest_inbound_event(
        _event(event_id="ev-flat-1", scope_key="u:on_senderA", scope_kind="participant"),
        account=account,
        session_maker=maker,
    )
    assert res.outcome == "enqueued"

    async with maker() as s:
        conv = (
            await s.execute(select(Conversation).where(Conversation.id == res.conversation_id))
        ).scalar_one()
        assert conv.topic_id is None
        assert conv.is_group_chat is False
