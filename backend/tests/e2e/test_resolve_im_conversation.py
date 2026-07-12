"""E2E tests for the resolve_im_conversation helper.

Routing/topic behavior is now driven by account-level ``IMBotSettings``
(``account.config["bot_settings"]``) — the per-channel ``IMChannelBinding``
is gone. These tests exercise:

- Shared routing → lazy ``Topic`` (owned by the bot's acting user) + link +
  conversation, with ``attributes.im`` stamped.
- Isolated + topic mode → a per-sender ``Topic`` owned by the sender.
- Flat mode → link reuse and soft-delete repoint with no Topic.
- ``/new`` (``reset_im_conversation``) rotates the conversation under the
  same Topic while leaving the old one as history.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cubeplex.im.bot_settings import IMBotSettings, store_bot_settings
from cubeplex.im.conversation_resolver import (
    ResolvedIMConversation,
    reset_im_conversation,
    resolve_im_conversation,
)
from cubeplex.im.types import is_shared_mode_for_tailer
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_participant import ConversationParticipant
from cubeplex.models.im_connector import IMConnectorAccount, IMThreadLink
from cubeplex.models.topic import Topic, TopicParticipant
from tests.e2e.conftest import _build_database_url
from tests.e2e.im_fixtures import (
    im_cleanup,
    im_seed_account,
    im_seed_org_ws_user,
    im_seed_stub_credential,
)

pytestmark = pytest.mark.asyncio


_ORG = "org-resolve-im-001"
_WS = "ws-resolve-im-001"
_USER = "usr-resolve-im-001"
_CRED = "cred-resolve-im-001"
_ACCOUNT = "imac-resolve-im-001"
_EXT_ACCT = "cli_resolve_im"
_CHANNEL = "oc_resolve_ch1"


def _with_settings(account: IMConnectorAccount, settings: IMBotSettings) -> IMConnectorAccount:
    """Mutate the in-memory account's config; resolve reads it directly."""
    account.config = store_bot_settings(account.config, settings)
    return account


@pytest_asyncio.fixture
async def _seeded() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], IMConnectorAccount]]:
    """Seed org / ws / user / credential / account; yield session_maker + account."""
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            await im_seed_org_ws_user(session, org_id=_ORG, ws_id=_WS, user_id=_USER)
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
                    select(IMConnectorAccount).where(IMConnectorAccount.id == _ACCOUNT)
                )
            ).scalar_one()

        try:
            yield maker, account
        finally:
            async with maker() as session:
                await session.execute(
                    text(
                        "DELETE FROM conversation_participants WHERE conversation_id IN "
                        "(SELECT id FROM conversations WHERE workspace_id = :ws)"
                    ),
                    {"ws": _WS},
                )
                await session.execute(
                    text("UPDATE conversations SET topic_id = NULL WHERE workspace_id = :ws"),
                    {"ws": _WS},
                )
                # im_thread_links.topic_id FKs topics — null it before deleting
                # topics, otherwise the topic DELETE below trips the FK.
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
                await session.execute(
                    text("DELETE FROM topics WHERE org_id = :org"),
                    {"org": _ORG},
                )
                await im_cleanup(
                    session,
                    account_ids=[_ACCOUNT],
                    credential_ids=[_CRED],
                    ws_ids=[_WS],
                    user_ids=[_USER],
                    org_ids=[_ORG],
                    cleanup_conversations_in_ws=True,
                )
                await session.commit()
    finally:
        await engine.dispose()


async def test_shared_creates_topic_and_link(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Shared routing: mint a Conversation + link and lazily create the Topic
    (owned by the bot's acting user) stamped with attributes.im."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="shared"))
    # Avatar is hydrated into config at connect time → surfaces on the topic.
    account.config = {**account.config, "bot_avatar_url": "https://x/avatar.png"}

    async with maker() as session:
        resolved = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="ch",
            scope_kind="channel",
            effective_user_id=_USER,
            title_hint="hello world",
            origin="schedule",
        )
        await session.commit()

    assert isinstance(resolved, ResolvedIMConversation)
    assert resolved.conversation_id.startswith("conv")
    assert resolved.is_group_chat is True
    assert resolved.topic_id is not None

    async with maker() as session:
        link = (
            await session.execute(select(IMThreadLink).where(IMThreadLink.account_id == _ACCOUNT))
        ).scalar_one()
        assert link.conversation_id == resolved.conversation_id
        # The Topic anchor now lives on the link itself (survives /new).
        assert link.topic_id == resolved.topic_id

        topic = (
            await session.execute(select(Topic).where(Topic.id == resolved.topic_id))
        ).scalar_one()
        # Without a platform-supplied channel_name, title stays empty so the
        # UI can localize (never the opaque channel id, never a frozen phrase).
        assert topic.title == ""
        assert topic.attributes.get("im", {}).get("channel_name") is None
        assert topic.creator_user_id == _USER  # acting user owns shared topics
        assert topic.attributes.get("im", {}).get("account_id") == _ACCOUNT
        assert topic.attributes["im"]["scope_kind"] == "channel"
        assert topic.attributes["im"]["bot_avatar_url"] == "https://x/avatar.png"

        owner_tp = (
            await session.execute(
                select(TopicParticipant).where(
                    TopicParticipant.topic_id == topic.id,
                    TopicParticipant.user_id == _USER,
                )
            )
        ).scalar_one()
        assert owner_tp.role == "owner"

        conv = (
            await session.execute(
                select(Conversation).where(Conversation.id == resolved.conversation_id)
            )
        ).scalar_one()
        assert conv.topic_id == topic.id
        assert conv.is_group_chat is True
        assert conv.attributes.get("im", {}).get("account_id") == _ACCOUNT

        cp = (
            await session.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == resolved.conversation_id,
                    ConversationParticipant.user_id == _USER,
                )
            )
        ).scalar_one_or_none()
        assert cp is not None


async def test_shared_topic_uses_channel_name_as_title(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """When the platform supplies a group display name, use it as Topic title."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="shared"))

    async with maker() as session:
        resolved = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="ch",
            scope_kind="channel",
            effective_user_id=_USER,
            title_hint="hello",
            origin="inbound",
            channel_name="项目 Alpha",
        )
        await session.commit()

    assert resolved.topic_id is not None
    async with maker() as session:
        topic = (
            await session.execute(select(Topic).where(Topic.id == resolved.topic_id))
        ).scalar_one()
        assert topic.title == "项目 Alpha"
        assert topic.attributes["im"]["channel_name"] == "项目 Alpha"
        assert topic.attributes["im"]["channel_id"] == _CHANNEL


async def test_shared_topic_refreshes_legacy_channel_id_title(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """A subsequent resolve with a real name rewrites a legacy channel-id title."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="shared"))

    async with maker() as session:
        r1 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="ch",
            scope_kind="channel",
            effective_user_id=_USER,
            title_hint="first",
            origin="inbound",
            # no channel_name → empty title (UI localizes)
        )
        await session.commit()
        topic_id = r1.topic_id
        assert topic_id is not None
        # Simulate pre-fix rows that used channel_id as the title.
        topic = (await session.execute(select(Topic).where(Topic.id == topic_id))).scalar_one()
        topic.title = _CHANNEL
        attrs = dict(topic.attributes or {})
        im = dict(attrs.get("im") or {})
        im["channel_name"] = _CHANNEL
        attrs["im"] = im
        topic.attributes = attrs
        session.add(topic)
        await session.commit()

    async with maker() as session:
        account = (
            await session.execute(
                select(IMConnectorAccount).where(IMConnectorAccount.id == _ACCOUNT)
            )
        ).scalar_one()
        _with_settings(account, IMBotSettings(routing_mode="shared"))
        await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="ch",
            scope_kind="channel",
            effective_user_id=_USER,
            title_hint="second",
            origin="inbound",
            channel_name="研发大群",
        )
        await session.commit()

    async with maker() as session:
        topic = (await session.execute(select(Topic).where(Topic.id == topic_id))).scalar_one()
        assert topic.title == "研发大群"
        assert topic.attributes["im"]["channel_name"] == "研发大群"


async def test_shared_topic_clears_legacy_channel_id_without_name(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """When name lookup fails, still clear legacy channel-id titles to empty."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="shared"))

    async with maker() as session:
        r1 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="ch",
            scope_kind="channel",
            effective_user_id=_USER,
            title_hint="first",
            origin="inbound",
        )
        await session.commit()
        topic_id = r1.topic_id
        assert topic_id is not None
        topic = (await session.execute(select(Topic).where(Topic.id == topic_id))).scalar_one()
        topic.title = _CHANNEL
        attrs = dict(topic.attributes or {})
        im = dict(attrs.get("im") or {})
        im["channel_name"] = _CHANNEL
        attrs["im"] = im
        topic.attributes = attrs
        session.add(topic)
        await session.commit()

    async with maker() as session:
        account = (
            await session.execute(
                select(IMConnectorAccount).where(IMConnectorAccount.id == _ACCOUNT)
            )
        ).scalar_one()
        _with_settings(account, IMBotSettings(routing_mode="shared"))
        await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="ch",
            scope_kind="channel",
            effective_user_id=_USER,
            title_hint="second",
            origin="inbound",
            # no channel_name — lookup failed / scope missing
        )
        await session.commit()

    async with maker() as session:
        topic = (await session.execute(select(Topic).where(Topic.id == topic_id))).scalar_one()
        assert topic.title == ""
        assert topic.attributes["im"]["channel_name"] is None


async def test_isolated_topic_mode_creates_per_sender_topic(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Isolated + topic mode (the default): per-sender Topic owned by the
    sender, conversation stays personal (is_group_chat False)."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="isolated", topic_mode="topic"))

    async with maker() as session:
        resolved = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="hi",
            origin="inbound",
        )
        await session.commit()

    assert resolved.is_group_chat is False
    assert resolved.topic_id is not None

    async with maker() as session:
        topic = (
            await session.execute(select(Topic).where(Topic.id == resolved.topic_id))
        ).scalar_one()
        assert topic.creator_user_id == _USER  # sender owns their own topic
        # DM title is the bot's display name.
        assert topic.title == "cubeplex"
        assert topic.attributes["im"]["scope_kind"] == "dm"
        owner_tp = (
            await session.execute(
                select(TopicParticipant).where(TopicParticipant.topic_id == topic.id)
            )
        ).scalar_one()
        assert owner_tp.user_id == _USER
        assert owner_tp.role == "owner"

        link = (
            await session.execute(select(IMThreadLink).where(IMThreadLink.account_id == _ACCOUNT))
        ).scalar_one()
        assert link.topic_id == resolved.topic_id


async def test_flat_mode_reuses_link_no_topic(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Flat mode: no Topic; the live link is reused on the second resolve."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="isolated", topic_mode="flat"))

    async with maker() as session:
        r1 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="first",
            origin="inbound",
        )
        await session.commit()

    async with maker() as session:
        r2 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="second",
            origin="schedule",
        )
        await session.commit()

    assert r1.conversation_id == r2.conversation_id
    assert r1.topic_id is None and r2.topic_id is None

    async with maker() as session:
        link_count = (
            await session.execute(
                select(func.count())
                .select_from(IMThreadLink)
                .where(IMThreadLink.account_id == _ACCOUNT)
            )
        ).scalar()
        assert link_count == 1
        conv_count = (
            await session.execute(
                select(func.count())
                .select_from(Conversation)
                .where(Conversation.workspace_id == _WS)
            )
        ).scalar()
        assert conv_count == 1


async def test_flat_mode_mints_new_conv_when_soft_deleted(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Flat mode: link exists but the bound Conversation is soft-deleted →
    mint a fresh conv and repoint the link."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="isolated", topic_mode="flat"))

    async with maker() as session:
        r1 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="initial",
            origin="inbound",
        )
        await session.commit()
    original_conv_id = r1.conversation_id

    async with maker() as session:
        await session.execute(
            text("UPDATE conversations SET deleted_at = :now WHERE id = :id"),
            {"now": datetime.now(UTC), "id": original_conv_id},
        )
        await session.commit()

    async with maker() as session:
        r2 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="after /new",
            origin="schedule",
        )
        await session.commit()

    assert r2.conversation_id != original_conv_id

    async with maker() as session:
        link = (
            await session.execute(
                select(IMThreadLink).where(
                    IMThreadLink.account_id == _ACCOUNT,
                    IMThreadLink.channel_id == _CHANNEL,
                    IMThreadLink.scope_key == "dm",
                )
            )
        ).scalar_one()
        assert link.conversation_id == r2.conversation_id


async def test_mode_change_adopts_existing_conversation_into_topic(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """flat→topic settings change: the next message must adopt the existing
    (flat, topicless) conversation into the new Topic instead of orphaning the
    Topic and leaving the conversation ungrouped."""
    maker, account = _seeded

    # 1) First message in flat mode → topicless conversation + link.
    _with_settings(account, IMBotSettings(routing_mode="isolated", topic_mode="flat"))
    async with maker() as session:
        r1 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="flat first",
            origin="inbound",
        )
        await session.commit()
    assert r1.topic_id is None

    # 2) Admin flips to topic mode; next message reuses the live conversation.
    _with_settings(account, IMBotSettings(routing_mode="isolated", topic_mode="topic"))
    async with maker() as session:
        r2 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="topic now",
            origin="inbound",
        )
        await session.commit()

    # Same conversation, now grouped under the new Topic — no orphan, no split.
    assert r2.conversation_id == r1.conversation_id
    assert r2.topic_id is not None

    async with maker() as session:
        conv = (
            await session.execute(select(Conversation).where(Conversation.id == r2.conversation_id))
        ).scalar_one()
        assert conv.topic_id == r2.topic_id  # conversation adopted into the topic
        assert "im" in conv.attributes

        link = (
            await session.execute(select(IMThreadLink).where(IMThreadLink.account_id == _ACCOUNT))
        ).scalar_one()
        assert link.topic_id == r2.topic_id  # link, conv, topic all agree

        # Exactly one topic — no orphan was created.
        topic_count = (
            await session.execute(
                select(func.count()).select_from(Topic).where(Topic.workspace_id == _WS)
            )
        ).scalar()
        assert topic_count == 1


async def test_dm_on_shared_account_stays_isolated(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """A DM on a shared-routing bot is never a group chat — its Topic is owned
    by the sender and the conversation stays personal."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="shared", sandbox_mode="dedicated"))

    async with maker() as session:
        resolved = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="dm hi",
            origin="inbound",
        )
        await session.commit()

    assert resolved.is_group_chat is False
    assert resolved.topic_id is not None
    async with maker() as session:
        topic = (
            await session.execute(select(Topic).where(Topic.id == resolved.topic_id))
        ).scalar_one()
        assert topic.creator_user_id == _USER  # sender owns it, not the bot
        assert topic.title == "cubeplex"  # DM title is the bot name
        conv = (
            await session.execute(
                select(Conversation).where(Conversation.id == resolved.conversation_id)
            )
        ).scalar_one()
        assert conv.is_group_chat is False


async def test_topic_to_flat_detaches_existing_scope(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """topic→flat settings change: the next message detaches the existing
    conversation from its Topic and clears the link anchor, so the scope
    becomes standalone instead of staying grouped forever."""
    maker, account = _seeded

    _with_settings(account, IMBotSettings(routing_mode="isolated", topic_mode="topic"))
    async with maker() as session:
        r1 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="topic first",
            origin="inbound",
        )
        await session.commit()
    assert r1.topic_id is not None

    _with_settings(account, IMBotSettings(routing_mode="isolated", topic_mode="flat"))
    async with maker() as session:
        r2 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="flat now",
            origin="inbound",
        )
        await session.commit()

    # Same conversation, now standalone.
    assert r2.conversation_id == r1.conversation_id
    assert r2.topic_id is None
    async with maker() as session:
        conv = (
            await session.execute(select(Conversation).where(Conversation.id == r2.conversation_id))
        ).scalar_one()
        assert conv.topic_id is None
        link = (
            await session.execute(select(IMThreadLink).where(IMThreadLink.account_id == _ACCOUNT))
        ).scalar_one()
        assert link.topic_id is None  # anchor cleared → /new deletes, not rotates


async def test_new_honors_flat_mode_after_switch(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """A topic→flat settings change followed immediately by /new (before any
    normal message clears the link's stale anchor) must delete the link, not
    rotate under the old Topic. reset reads the account's current mode."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="isolated", topic_mode="topic"))
    async with maker() as session:
        r1 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="topic first",
            origin="inbound",
        )
        await session.commit()
    assert r1.topic_id is not None

    # Persist flat mode to the DB row (reset loads settings from the row).
    async with maker() as session:
        row = await session.get(IMConnectorAccount, _ACCOUNT)
        assert row is not None
        row.config = store_bot_settings(
            row.config, IMBotSettings(routing_mode="isolated", topic_mode="flat")
        )
        await session.commit()

    async with maker() as session:
        outcome = await reset_im_conversation(
            session, account_id=_ACCOUNT, channel_id=_CHANNEL, scope_key="dm"
        )
        await session.commit()
    assert outcome == "flat"
    async with maker() as session:
        link = (
            await session.execute(select(IMThreadLink).where(IMThreadLink.account_id == _ACCOUNT))
        ).scalar_one_or_none()
        assert link is None  # deleted → next message starts a fresh standalone conv


async def test_archived_topic_is_replaced(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """If the linked Topic is archived (user removed it from the UI), the next
    message mints a fresh, visible Topic instead of appending under the dead one."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="isolated", topic_mode="topic"))
    async with maker() as session:
        r1 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="first",
            origin="inbound",
        )
        await session.commit()
    first_topic = r1.topic_id
    assert first_topic is not None

    async with maker() as session:
        topic = await session.get(Topic, first_topic)
        assert topic is not None
        topic.is_archived = True
        await session.commit()

    async with maker() as session:
        r2 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="after archive",
            origin="inbound",
        )
        await session.commit()

    assert r2.topic_id is not None
    assert r2.topic_id != first_topic
    # The fresh Topic must start CLEAN: a brand-new conversation, with the old
    # (archived-topic) conversation soft-deleted so its hidden history isn't
    # re-homed/resurrected under the new Topic.
    assert r2.conversation_id != r1.conversation_id
    async with maker() as session:
        link = (
            await session.execute(select(IMThreadLink).where(IMThreadLink.account_id == _ACCOUNT))
        ).scalar_one()
        assert link.topic_id == r2.topic_id
        assert link.conversation_id == r2.conversation_id
        fresh = await session.get(Topic, r2.topic_id)
        assert fresh is not None and fresh.is_archived is False
        old_conv = await session.get(Conversation, r1.conversation_id)
        assert old_conv is not None and old_conv.deleted_at is not None
        new_conv = await session.get(Conversation, r2.conversation_id)
        assert new_conv is not None and new_conv.topic_id == r2.topic_id


async def test_shared_to_flat_drops_participants(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Flattening a shared scope must drop the conversation's participant rows;
    a topicless conversation is visible via those rows, so leaving them would
    let former channel members keep reading the now-standalone chat."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="shared", sandbox_mode="dedicated"))
    async with maker() as session:
        r1 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="ch",
            scope_kind="channel",
            effective_user_id=_USER,
            title_hint="shared first",
            origin="inbound",
        )
        await session.commit()
    conv_id = r1.conversation_id
    async with maker() as session:
        n = (
            await session.execute(
                select(func.count())
                .select_from(ConversationParticipant)
                .where(ConversationParticipant.conversation_id == conv_id)
            )
        ).scalar()
        assert n and n >= 1  # shared seeded a participant

    # Switch to flat; the next message in the same scope detaches + clears rows.
    _with_settings(account, IMBotSettings(routing_mode="isolated", topic_mode="flat"))
    async with maker() as session:
        r2 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="ch",
            scope_kind="channel",
            effective_user_id=_USER,
            title_hint="flat now",
            origin="inbound",
        )
        await session.commit()
    assert r2.conversation_id == conv_id
    assert r2.topic_id is None
    async with maker() as session:
        conv = await session.get(Conversation, conv_id)
        assert conv is not None and conv.topic_id is None and conv.is_group_chat is False
        left = (
            await session.execute(
                select(func.count())
                .select_from(ConversationParticipant)
                .where(ConversationParticipant.conversation_id == conv_id)
            )
        ).scalar()
        assert left == 0  # former shared participants removed


async def test_isolated_topic_rejoins_missing_participant(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Topic visibility is gated on TopicParticipant. If the sender's row goes
    missing on their own isolated topic, the next message re-adds it so their
    replies stay visible."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="isolated", topic_mode="topic"))
    async with maker() as session:
        r1 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="first",
            origin="inbound",
        )
        await session.commit()
    topic_id = r1.topic_id
    assert topic_id is not None

    async with maker() as session:
        await session.execute(
            text("DELETE FROM topic_participants WHERE topic_id = :t AND user_id = :u"),
            {"t": topic_id, "u": _USER},
        )
        await session.commit()

    async with maker() as session:
        await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="second",
            origin="inbound",
        )
        await session.commit()

    async with maker() as session:
        tp = (
            await session.execute(
                select(TopicParticipant).where(
                    TopicParticipant.topic_id == topic_id,
                    TopicParticipant.user_id == _USER,
                )
            )
        ).scalar_one_or_none()
        assert tp is not None  # re-added → replies stay visible


async def test_tailer_uses_conversation_not_account_routing(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """is_shared_mode_for_tailer must reflect the resolved conversation, so a
    DM on a shared-routing bot is NOT treated as shared (otherwise the tailer
    skips HITL responder registration and the sender's clicks get rejected)."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="shared", sandbox_mode="dedicated"))
    async with maker() as session:
        r = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="dm",
            origin="inbound",
        )
        await session.commit()
    assert r.is_group_chat is False
    shared = await is_shared_mode_for_tailer(maker, _ACCOUNT, _CHANNEL, r.conversation_id)
    assert shared is False


async def test_new_rotates_conversation_under_same_topic(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """/new in topic mode keeps the Topic and the old conversation, repointing
    the link to a fresh conversation under that same Topic."""
    maker, account = _seeded
    _with_settings(account, IMBotSettings(routing_mode="isolated", topic_mode="topic"))

    async with maker() as session:
        r1 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="before /new",
            origin="inbound",
        )
        await session.commit()
    first_conv, topic_id = r1.conversation_id, r1.topic_id
    assert topic_id is not None

    async with maker() as session:
        outcome = await reset_im_conversation(
            session, account_id=_ACCOUNT, channel_id=_CHANNEL, scope_key="dm"
        )
        await session.commit()
    assert outcome == "rotated"

    async with maker() as session:
        link = (
            await session.execute(
                select(IMThreadLink).where(
                    IMThreadLink.account_id == _ACCOUNT,
                    IMThreadLink.channel_id == _CHANNEL,
                    IMThreadLink.scope_key == "dm",
                )
            )
        ).scalar_one()
        # New conversation, same topic, link still anchored to the topic.
        assert link.conversation_id != first_conv
        assert link.topic_id == topic_id

        new_conv = (
            await session.execute(
                select(Conversation).where(Conversation.id == link.conversation_id)
            )
        ).scalar_one()
        assert new_conv.topic_id == topic_id
        # Must not inherit the previous conversation's title (that also blocked
        # web auto-title, which skips when title is non-empty).
        assert new_conv.title == ""

        # The old conversation survives as history (not soft-deleted).
        old_conv = (
            await session.execute(select(Conversation).where(Conversation.id == first_conv))
        ).scalar_one()
        assert old_conv.deleted_at is None
        assert old_conv.topic_id == topic_id
        assert old_conv.title == "before /new"

    # First real message after /new stamps a provisional title from title_hint.
    async with maker() as session:
        r2 = await resolve_im_conversation(
            session,
            account,
            channel_id=_CHANNEL,
            scope_key="dm",
            scope_kind="dm",
            effective_user_id=_USER,
            title_hint="hello after /new",
            origin="inbound",
        )
        await session.commit()
    async with maker() as session:
        after = (
            await session.execute(select(Conversation).where(Conversation.id == r2.conversation_id))
        ).scalar_one()
        assert after.title == "hello after /new"
        assert r2.conversation_id != first_conv
        assert r2.topic_id == topic_id
