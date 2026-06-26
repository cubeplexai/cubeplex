"""Integration tests for the IM queue worker (Task 6)."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cubebox.im.inbound import ingest_inbound_event
from cubebox.im.types import InboundEvent
from cubebox.im.worker import process_one_queue_item
from cubebox.models.im_connector import (
    IMConnectorAccount,
    IMRunQueueItem,
    IMWebhookReceipt,
)
from cubebox.streams.run_manager import RunContext
from tests.e2e.conftest import _build_database_url
from tests.e2e.im_fixtures import (
    im_cleanup,
    im_seed_account,
    im_seed_org_ws_user,
    im_seed_stub_credential,
)

pytestmark = pytest.mark.asyncio


_ORG_ID = "org-imwkrA"
_WS_ID = "ws-imwkrA"
_USER_ID = "usr-imwkrA"
_CRED_ID = "cred-imwkrA"
_ACCOUNT_ID = "imac-imwkrA"


@pytest_asyncio.fixture
async def _seeded() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], IMConnectorAccount]]:
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            await im_seed_org_ws_user(session, org_id=_ORG_ID, ws_id=_WS_ID, user_id=_USER_ID)
            await im_seed_stub_credential(
                session,
                credential_id=_CRED_ID,
                org_id=_ORG_ID,
                user_id=_USER_ID,
                name="feishu:T-wkrA",
            )
            await im_seed_account(
                session,
                account_id=_ACCOUNT_ID,
                org_id=_ORG_ID,
                ws_id=_WS_ID,
                user_id=_USER_ID,
                credential_id=_CRED_ID,
                external_account_id="cli_wkrA",
                delivery_mode="long_connection",
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
                await im_cleanup(
                    session,
                    account_ids=[_ACCOUNT_ID],
                    ws_ids=[_WS_ID],
                    cleanup_conversations_in_ws=True,
                )
                await session.commit()
    finally:
        await engine.dispose()


class _FakeRunManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def start_run(
        self,
        *,
        conversation_id: str,
        content: str,
        attachments: list[str] | None,
        ctx: RunContext,
        cancel_pending_hitl: bool = False,
    ) -> str:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "content": content,
                "user_id": ctx.user_id,
                "org_id": ctx.org_id,
                "workspace_id": ctx.workspace_id,
                "trigger": ctx.trigger,
                "sender_display_name": ctx.sender_display_name,
                "cancel_pending_hitl": cancel_pending_hitl,
            }
        )
        return f"run-fake-{len(self.calls)}"


async def test_worker_processes_one_item_and_completes_receipt(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded
    ev = InboundEvent(
        platform="feishu",
        account_external_id="cli_wkrA",
        platform_event_id="evW1",
        channel_id="oc_chat",
        scope_key="u:on_user1",
        scope_kind="participant",
        reply_to_id="om_msg1",
        inbound_message_id="om_msg1",
        sender_ref="on_user1",
        sender_open_id="ou_user1",
        text="do it",
    )
    await ingest_inbound_event(ev, account=account, session_maker=maker)

    rm = _FakeRunManager()
    captured_runs: list[tuple[str, str]] = []

    async def on_started(run_id: str, item: IMRunQueueItem) -> None:
        captured_runs.append((run_id, item.conversation_id))

    did_run = await process_one_queue_item(
        session_maker=maker,
        run_manager=rm,
        on_run_started=on_started,
        lease_seconds=300,
    )

    assert did_run is True
    assert len(rm.calls) == 1
    assert rm.calls[0]["content"] == "do it"
    assert rm.calls[0]["user_id"] == account.acting_user_id
    assert rm.calls[0]["org_id"] == account.org_id
    assert rm.calls[0]["workspace_id"] == account.workspace_id
    assert rm.calls[0]["trigger"] == "im"
    # Sender identity is derived from the effective user (here the acting user,
    # seeded with no display_name → falls back to email) so cubepi attribution
    # and the group-chat SenderBadge fire for IM messages.
    assert rm.calls[0]["sender_display_name"] == f"{account.acting_user_id}@example.com"

    assert captured_runs and captured_runs[0][0] == "run-fake-1"

    async with maker() as s:
        rcpt = (
            await s.execute(
                select(IMWebhookReceipt).where(
                    IMWebhookReceipt.account_id == account.id  # type: ignore[arg-type]
                )
            )
        ).scalar_one()
        item = (
            await s.execute(
                select(IMRunQueueItem).where(
                    IMRunQueueItem.account_id == account.id  # type: ignore[arg-type]
                )
            )
        ).scalar_one()
        assert rcpt.status == "completed"
        # Both receipt AND queue row must flip to a terminal state. If the
        # queue row stayed in 'started', claim_pending_queue_item would
        # re-fire start_run every lease_seconds (default 300s) up to
        # max_attempts=5 times — duplicate runs per inbound message.
        assert item.status == "completed"
        assert item.claim_lease_expires_at is None
        assert item.attempts == 1


async def test_worker_returns_false_when_queue_empty(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, _ = _seeded
    rm = _FakeRunManager()
    ran = await process_one_queue_item(
        session_maker=maker, run_manager=rm, on_run_started=None, lease_seconds=300
    )
    assert ran is False
    assert rm.calls == []


async def test_worker_leaves_row_for_reclaim_on_start_run_failure(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    """If start_run raises, the row stays as 'started' with the lease set, and
    the receipt does NOT flip to completed."""
    maker, account = _seeded
    ev = InboundEvent(
        platform="feishu",
        account_external_id="cli_wkrA",
        platform_event_id="evW_fail",
        channel_id="oc_chat",
        scope_key="u:on_userF",
        scope_kind="participant",
        reply_to_id="om_msgF",
        inbound_message_id="om_msgF",
        sender_ref="on_userF",
        sender_open_id="ou_userF",
        text="fail",
    )
    await ingest_inbound_event(ev, account=account, session_maker=maker)

    class _BrokenRM:
        async def start_run(
            self,
            *,
            conversation_id: str,
            content: str,
            attachments: list[str] | None,
            ctx: RunContext,
            cancel_pending_hitl: bool = False,
        ) -> str:
            raise RuntimeError("LLM exploded")

    did_run = await process_one_queue_item(
        session_maker=maker, run_manager=_BrokenRM(), on_run_started=None, lease_seconds=300
    )
    # process_one_queue_item now returns False on the failure path so the
    # worker loop's idle-sleep branch fires — prevents the thundering
    # herd of immediately re-claiming the rewound row.
    assert did_run is False
    async with maker() as s:
        item = (
            await s.execute(
                select(IMRunQueueItem).where(
                    IMRunQueueItem.account_id == account.id  # type: ignore[arg-type]
                )
            )
        ).scalar_one()
        rcpt = (
            await s.execute(
                select(IMWebhookReceipt).where(
                    IMWebhookReceipt.account_id == account.id  # type: ignore[arg-type]
                )
            )
        ).scalar_one()
        # Failure path on FIRST attempt: rewind to 'pending' so the next
        # poll re-claims (transient errors must not become permanent
        # silent drops); the receipt stays 'pending' too because no run
        # actually started. After max_attempts the row would park as
        # 'failed' — covered by a separate assertion below if we ever add
        # that scenario to this suite.
        assert item.status == "pending"
        assert item.claim_lease_expires_at is None
        assert item.attempts == 1
        assert rcpt.status == "pending"
