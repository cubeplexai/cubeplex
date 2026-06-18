"""E2E tests for shared-mode inbound lifecycle (Task 4).

Exercises the shared-mode branch in ``ingest_inbound_event``:
- First message to a shared binding creates Topic + TopicParticipant +
  Conversation(topic_id) + ConversationParticipant.
- Subsequent messages reuse the topic and auto-join new senders.
- Thread scope_key creates a separate conversation under the same topic.
- Isolated binding and no-binding cases are unchanged.
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

from cubebox.im.inbound import ingest_inbound_event
from cubebox.im.types import InboundEvent
from cubebox.models.conversation import Conversation
from cubebox.models.im_channel_binding import IMChannelBinding
from cubebox.models.im_connector import IMConnectorAccount
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
                # 1. conversation_participants (references conversations)
                await session.execute(
                    text(
                        "DELETE FROM conversation_participants "
                        "WHERE conversation_id IN "
                        "(SELECT id FROM conversations WHERE workspace_id = :ws)"
                    ),
                    {"ws": _WS},
                )
                # 2. Null out conversation.topic_id so topics can be deleted
                await session.execute(
                    text("UPDATE conversations SET topic_id = NULL WHERE workspace_id = :ws"),
                    {"ws": _WS},
                )
                # 3. topic_participants (references topics)
                await session.execute(
                    text(
                        "DELETE FROM topic_participants WHERE topic_id IN "
                        "(SELECT id FROM topics WHERE org_id = :org)"
                    ),
                    {"org": _ORG},
                )
                # 4. Null out binding.topic_id before deleting topics
                await session.execute(
                    text(
                        "UPDATE im_channel_bindings SET topic_id = NULL "
                        "WHERE account_id = ANY(:ids)"
                    ),
                    {"ids": [_ACCOUNT]},
                )
                # 5. Topics (now safe: no FK references)
                await session.execute(text("DELETE FROM topics WHERE org_id = :org"), {"org": _ORG})
                # 6. Bindings (before accounts FK)
                await session.execute(
                    text("DELETE FROM im_channel_bindings WHERE account_id = ANY(:ids)"),
                    {"ids": [_ACCOUNT]},
                )
                # 7. Standard IM cleanup (queue/receipts/links/conversations/
                #    accounts/creds/workspaces/users/orgs)
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


async def _insert_binding(
    session: AsyncSession,
    *,
    mode: str = "shared",
    channel_id: str = _CHANNEL,
    channel_name: str = "Test Group",
    sandbox_mode: str | None = None,
    topic_id: str | None = None,
) -> None:
    """Insert a binding row for the seeded account."""
    binding = IMChannelBinding(
        org_id=_ORG,
        workspace_id=_WS,
        account_id=_ACCOUNT,
        channel_id=channel_id,
        channel_name=channel_name,
        mode=mode,
        sandbox_mode=sandbox_mode,
        topic_id=topic_id,
    )
    session.add(binding)
    await session.flush()


async def test_first_shared_message_creates_topic(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """First @bot in a shared binding creates Topic + participants + Conversation."""
    maker, account = _seeded

    # Seed binding (shared, no topic yet)
    async with maker() as s:
        await _insert_binding(s, mode="shared")
        await s.commit()

    res = await ingest_inbound_event(
        _event(),
        account=account,
        session_maker=maker,
    )
    assert res.outcome == "enqueued"

    async with maker() as s:
        # Binding now has topic_id
        binding = (
            await s.execute(
                select(IMChannelBinding).where(
                    IMChannelBinding.account_id == _ACCOUNT,
                    IMChannelBinding.channel_id == _CHANNEL,
                )
            )
        ).scalar_one()
        assert binding.topic_id is not None

        # Topic exists with correct title
        topic = (await s.execute(select(Topic).where(Topic.id == binding.topic_id))).scalar_one()
        assert topic.title == "Test Group"
        assert topic.max_participants == 100

        # acting_user is TopicParticipant(owner)
        owner_tp = (
            await s.execute(
                select(TopicParticipant).where(
                    TopicParticipant.topic_id == topic.id,
                    TopicParticipant.user_id == _USER,
                )
            )
        ).scalar_one()
        assert owner_tp.role == "owner"

        # Conversation has topic_id set
        conv = (
            await s.execute(
                select(Conversation).where(
                    Conversation.id == res.conversation_id,
                )
            )
        ).scalar_one()
        assert conv.topic_id == topic.id
        assert conv.is_group_chat is True


async def test_subsequent_shared_reuses_topic(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Second message in shared mode reuses the topic; new sender auto-joins."""
    maker, account = _seeded

    async with maker() as s:
        await _insert_binding(s, mode="shared")
        await s.commit()

    # First message creates topic
    r1 = await ingest_inbound_event(
        _event(event_id="ev-sub-1"),
        account=account,
        session_maker=maker,
    )
    assert r1.outcome == "enqueued"

    # Second message, same scope_key → same conversation, same topic
    r2 = await ingest_inbound_event(
        _event(event_id="ev-sub-2"),
        account=account,
        session_maker=maker,
    )
    assert r2.outcome == "enqueued"
    assert r1.conversation_id == r2.conversation_id

    async with maker() as s:
        binding = (
            await s.execute(
                select(IMChannelBinding).where(
                    IMChannelBinding.account_id == _ACCOUNT,
                    IMChannelBinding.channel_id == _CHANNEL,
                )
            )
        ).scalar_one()
        # Topic count under this binding: exactly 1
        topic_count = (
            (
                await s.execute(
                    select(TopicParticipant).where(
                        TopicParticipant.topic_id == binding.topic_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        # Owner was added once
        owners = [tp for tp in topic_count if tp.role == "owner"]
        assert len(owners) == 1


async def test_thread_creates_separate_conversation(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Different scope_key (channel vs thread) creates separate conversations
    under the same topic."""
    maker, account = _seeded

    async with maker() as s:
        await _insert_binding(s, mode="shared")
        await s.commit()

    r_ch = await ingest_inbound_event(
        _event(event_id="ev-ch-1", scope_key="ch", scope_kind="channel"),
        account=account,
        session_maker=maker,
    )
    r_thread = await ingest_inbound_event(
        _event(event_id="ev-th-1", scope_key="t:1234", scope_kind="thread"),
        account=account,
        session_maker=maker,
    )
    assert r_ch.outcome == "enqueued"
    assert r_thread.outcome == "enqueued"
    assert r_ch.conversation_id != r_thread.conversation_id

    async with maker() as s:
        # Both conversations share the same topic_id
        conv_ch = (
            await s.execute(
                select(Conversation).where(
                    Conversation.id == r_ch.conversation_id,
                )
            )
        ).scalar_one()
        conv_th = (
            await s.execute(
                select(Conversation).where(
                    Conversation.id == r_thread.conversation_id,
                )
            )
        ).scalar_one()
        assert conv_ch.topic_id is not None
        assert conv_ch.topic_id == conv_th.topic_id


async def test_isolated_no_topic(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Isolated binding creates no topic; conversation.topic_id is None."""
    maker, account = _seeded

    async with maker() as s:
        await _insert_binding(s, mode="isolated")
        await s.commit()

    res = await ingest_inbound_event(
        _event(event_id="ev-iso-1"),
        account=account,
        session_maker=maker,
    )
    assert res.outcome == "enqueued"

    async with maker() as s:
        binding = (
            await s.execute(
                select(IMChannelBinding).where(
                    IMChannelBinding.account_id == _ACCOUNT,
                    IMChannelBinding.channel_id == _CHANNEL,
                )
            )
        ).scalar_one()
        assert binding.topic_id is None

        conv = (
            await s.execute(select(Conversation).where(Conversation.id == res.conversation_id))
        ).scalar_one()
        assert conv.topic_id is None
        assert conv.is_group_chat is False


async def test_no_binding_is_isolated(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """No binding row at all → isolated behavior (unchanged from before)."""
    maker, account = _seeded

    # No binding inserted for this channel
    res = await ingest_inbound_event(
        _event(event_id="ev-nobd-1", channel_id="oc_unbound_ch"),
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
