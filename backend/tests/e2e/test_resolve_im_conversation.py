"""E2E tests for the resolve_im_conversation helper (Phase 2 / Task 4).

The helper factors the shared-mode + thread-link conversation resolution
out of ``im/inbound.py`` so schedule/trigger dispatchers can reuse it. The
three tests below exercise the three branches of ``get_or_create_thread_link``
that matter for callers:

- Link exists and the underlying ``Conversation`` is alive → reuse it,
  no new rows.
- No link → mint a fresh ``Conversation`` and ``IMThreadLink``; in shared
  mode also lazily create the ``Topic`` and seed participants.
- Link exists but the ``Conversation`` has been soft-deleted (``deleted_at``
  set) → mint a fresh conversation and repoint the existing link.
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

from cubebox.im.conversation_resolver import (
    ResolvedIMConversation,
    resolve_im_conversation,
)
from cubebox.models.conversation import Conversation
from cubebox.models.conversation_participant import ConversationParticipant
from cubebox.models.im_channel_binding import IMChannelBinding
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


_ORG = "org-resolve-im-001"
_WS = "ws-resolve-im-001"
_USER = "usr-resolve-im-001"
_CRED = "cred-resolve-im-001"
_ACCOUNT = "imac-resolve-im-001"
_EXT_ACCT = "cli_resolve_im"
_CHANNEL = "oc_resolve_ch1"


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
                await session.execute(
                    text(
                        "DELETE FROM topic_participants WHERE topic_id IN "
                        "(SELECT id FROM topics WHERE org_id = :org)"
                    ),
                    {"org": _ORG},
                )
                await session.execute(
                    text(
                        "UPDATE im_channel_bindings SET topic_id = NULL "
                        "WHERE account_id = ANY(:ids)"
                    ),
                    {"ids": [_ACCOUNT]},
                )
                await session.execute(
                    text("DELETE FROM topics WHERE org_id = :org"),
                    {"org": _ORG},
                )
                await session.execute(
                    text("DELETE FROM im_channel_bindings WHERE account_id = ANY(:ids)"),
                    {"ids": [_ACCOUNT]},
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


async def test_creates_fresh_when_link_missing(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """No prior IMThreadLink: helper mints a new Conversation + link, and in
    shared mode also lazily creates the Topic + participant rows."""
    maker, account = _seeded

    async with maker() as session:
        binding = IMChannelBinding(
            org_id=_ORG,
            workspace_id=_WS,
            account_id=_ACCOUNT,
            channel_id=_CHANNEL,
            channel_name="Resolve Test Group",
            mode="shared",
            sandbox_mode=None,
            topic_id=None,
        )
        session.add(binding)
        await session.commit()

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

        binding_row = (
            await session.execute(
                select(IMChannelBinding).where(IMChannelBinding.account_id == _ACCOUNT)
            )
        ).scalar_one()
        assert binding_row.topic_id == resolved.topic_id

        topic = (
            await session.execute(select(Topic).where(Topic.id == binding_row.topic_id))
        ).scalar_one()
        assert topic.title == "Resolve Test Group"

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

        cp = (
            await session.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == resolved.conversation_id,
                    ConversationParticipant.user_id == _USER,
                )
            )
        ).scalar_one_or_none()
        assert cp is not None


async def test_reuses_link_when_present(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Second resolution with the same (account, channel, scope_key)
    reuses the live IMThreadLink → same conversation_id, no extra links."""
    maker, account = _seeded

    async with maker() as session:
        binding = IMChannelBinding(
            org_id=_ORG,
            workspace_id=_WS,
            account_id=_ACCOUNT,
            channel_id=_CHANNEL,
            channel_name="Reuse Test",
            mode="isolated",
            sandbox_mode=None,
            topic_id=None,
        )
        session.add(binding)
        await session.commit()

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
    assert r1.is_group_chat is False
    assert r2.is_group_chat is False

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


async def test_mints_new_conv_when_underlying_soft_deleted(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """Link exists but the bound Conversation is soft-deleted (deleted_at
    set) → helper mints a fresh conv and repoints the link."""
    maker, account = _seeded

    async with maker() as session:
        binding = IMChannelBinding(
            org_id=_ORG,
            workspace_id=_WS,
            account_id=_ACCOUNT,
            channel_id=_CHANNEL,
            channel_name="Soft-Delete Test",
            mode="isolated",
            sandbox_mode=None,
            topic_id=None,
        )
        session.add(binding)
        await session.commit()

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
