"""Integration tests for the IM queue worker (Task 6)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cubeplex.im.inbound import ingest_inbound_event
from cubeplex.im.types import InboundEvent
from cubeplex.im.worker import process_one_queue_item
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.models.im_connector import (
    IMConnectorAccount,
    IMRunQueueItem,
    IMWebhookReceipt,
)
from cubeplex.services.conversation_execution import ConversationExecutionService
from cubeplex.streams.run_manager import RunContext
from tests.e2e.conftest import _build_database_url
from tests.e2e.im_fixtures import (
    im_cleanup,
    im_seed_account,
    im_seed_org_ws_user,
    im_seed_stub_credential,
    im_test_execution_snapshot,
)

pytestmark = pytest.mark.asyncio


_ORG_ID = "org-imwkrA"
_WS_ID = "ws-imwkrA"
_USER_ID = "usr-imwkrA"
_CRED_ID = "cred-imwkrA"
_ACCOUNT_ID = "imac-imwkrA"
_OTHER_USER_ID = "usr-imwkrB"


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
        run_id: str | None = None,
        model_key: str | None = None,
        reasoning: object | None = None,
        cancel_pending_hitl: bool = False,
        llm_snapshot: object | None = None,
        admission_id: str | None = None,
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
                "run_id": run_id,
                "model_key": model_key,
                "reasoning": reasoning,
                "llm_snapshot": llm_snapshot,
                "admission_id": admission_id,
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
        load_execution_snapshot=im_test_execution_snapshot,
    )

    assert did_run is True
    assert len(rm.calls) == 1
    assert rm.calls[0]["content"] == "do it"
    assert rm.calls[0]["user_id"] == account.acting_user_id
    assert rm.calls[0]["org_id"] == account.org_id
    assert rm.calls[0]["workspace_id"] == account.workspace_id
    assert rm.calls[0]["trigger"] == "im"
    # Sender identity is derived from the effective user (here the acting user,
    # seeded with no display_name → falls back to email) so cubeloop attribution
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
        admission = (
            await s.execute(
                select(ConversationExecutionAdmission).where(
                    ConversationExecutionAdmission.source_id == f"im:{rcpt.id}"
                )
            )
        ).scalar_one()
        assert admission.actor_user_id == account.acting_user_id
        assert admission.run_id == rm.calls[0]["run_id"]
        assert admission.id == rm.calls[0]["admission_id"]


async def test_reclaim_after_run_claim_reuses_admission_without_reexecution(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded
    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id="cli_wkrA",
            platform_event_id="ev-crash-after-claim",
            channel_id="oc_chat",
            scope_key="u:crash-after-claim",
            scope_kind="participant",
            reply_to_id="req-crash-after-claim",
            inbound_message_id="ev-crash-after-claim",
            sender_ref="crash-after-claim",
            sender_open_id="crash-after-claim",
            text="run once",
        ),
        account=account,
        session_maker=maker,
    )

    class _CrashAfterClaimRunManager:
        calls = 0
        executions = 0

        async def start_run(
            self,
            *,
            conversation_id: str,
            content: str,
            attachments: list[str] | None,
            ctx: RunContext,
            run_id: str | None = None,
            model_key: str | None = None,
            reasoning: object | None = None,
            cancel_pending_hitl: bool = False,
            llm_snapshot: object | None = None,
            admission_id: str | None = None,
        ) -> str:
            del conversation_id, content, attachments, model_key, reasoning
            del cancel_pending_hitl, llm_snapshot
            assert run_id is not None
            assert admission_id is not None
            self.calls += 1
            async with maker() as session:
                admission = await session.get(ConversationExecutionAdmission, admission_id)
                assert admission is not None
                if admission.run_start_token is not None:
                    return run_id
                claimed = await ConversationExecutionService(
                    session,
                    org_id=ctx.org_id,
                    workspace_id=ctx.workspace_id,
                ).claim_run_start(
                    admission_id=admission_id,
                    attempt_id="attempt-before-crash",
                    now=datetime.now(UTC),
                )
                assert claimed
                await session.commit()
            self.executions += 1
            raise RuntimeError("worker crashed after durable run claim")

    run_manager = _CrashAfterClaimRunManager()
    first = await process_one_queue_item(
        session_maker=maker,
        run_manager=run_manager,
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
    )
    second = await process_one_queue_item(
        session_maker=maker,
        run_manager=run_manager,
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
    )

    assert first is False
    assert second is True
    assert run_manager.calls == 2
    assert run_manager.executions == 1
    async with maker() as session:
        item = (
            await session.execute(
                select(IMRunQueueItem).where(IMRunQueueItem.account_id == account.id)
            )
        ).scalar_one()
        receipt = await session.get(IMWebhookReceipt, item.receipt_id)
        assert item.status == "completed"
        assert receipt is not None
        assert receipt.status == "completed"


async def test_queue_actor_does_not_follow_later_account_identity_change(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded
    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id="cli_wkrA",
            platform_event_id="ev-frozen-actor",
            channel_id="oc_chat",
            scope_key="u:frozen-actor",
            scope_kind="participant",
            reply_to_id="req-frozen-actor",
            inbound_message_id="ev-frozen-actor",
            sender_ref="frozen-actor",
            sender_open_id="frozen-actor",
            text="keep my identity",
        ),
        account=account,
        session_maker=maker,
    )
    async with maker() as session:
        await im_seed_org_ws_user(
            session,
            org_id=_ORG_ID,
            ws_id=_WS_ID,
            user_id=_OTHER_USER_ID,
        )
        stored_account = await session.get(IMConnectorAccount, account.id)
        assert stored_account is not None
        stored_account.acting_user_id = _OTHER_USER_ID
        await session.commit()

    run_manager = _FakeRunManager()
    processed = await process_one_queue_item(
        session_maker=maker,
        run_manager=run_manager,
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
    )

    assert processed is True
    assert run_manager.calls[0]["user_id"] == _USER_ID
    async with maker() as session:
        item = (
            await session.execute(
                select(IMRunQueueItem).where(IMRunQueueItem.account_id == account.id)
            )
        ).scalar_one()
        admission = await session.get(
            ConversationExecutionAdmission,
            run_manager.calls[0]["admission_id"],
        )
        assert item.actor_user_id == _USER_ID
        assert admission is not None
        assert admission.actor_user_id == _USER_ID


async def test_revoked_queued_message_completes_without_starting_or_tailing(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded
    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id="cli_wkrA",
            platform_event_id="ev-revoked-before-start",
            channel_id="oc_chat",
            scope_key="u:revoked-before-start",
            scope_kind="participant",
            reply_to_id="req-revoked-before-start",
            inbound_message_id="ev-revoked-before-start",
            sender_ref="revoked-before-start",
            sender_open_id="revoked-before-start",
            text="do not wake later",
        ),
        account=account,
        session_maker=maker,
    )

    class _BusyRunManager:
        async def start_run(self, **_kwargs: object) -> str:
            raise RuntimeError("conversation already has an active run")

    admitted = await process_one_queue_item(
        session_maker=maker,
        run_manager=_BusyRunManager(),
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
    )
    assert admitted is False

    async with maker() as session:
        item = (
            await session.execute(
                select(IMRunQueueItem).where(IMRunQueueItem.account_id == account.id)
            )
        ).scalar_one()
        admission = (
            await session.execute(
                select(ConversationExecutionAdmission).where(
                    ConversationExecutionAdmission.source_id == f"im:{item.receipt_id}"
                )
            )
        ).scalar_one()
        await ConversationExecutionService(
            session,
            org_id=account.org_id,
            workspace_id=account.workspace_id,
        ).close_generation(
            conversation_id=item.conversation_id,
            actor_user_id=_USER_ID,
            execution_generation=admission.execution_generation,
            now=datetime.now(UTC),
        )
        await session.commit()

    run_manager = _FakeRunManager()
    started_callbacks: list[str] = []

    async def on_started(run_id: str, _item: IMRunQueueItem) -> None:
        started_callbacks.append(run_id)

    completed = await process_one_queue_item(
        session_maker=maker,
        run_manager=run_manager,
        on_run_started=on_started,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
    )

    assert completed is True
    assert run_manager.calls == []
    assert started_callbacks == []
    async with maker() as session:
        item = (
            await session.execute(
                select(IMRunQueueItem).where(IMRunQueueItem.account_id == account.id)
            )
        ).scalar_one()
        receipt = await session.get(IMWebhookReceipt, item.receipt_id)
        assert item.status == "completed"
        assert receipt is not None
        assert receipt.status == "completed"


async def test_synthetic_im_queue_row_does_not_claim_user_message_identity(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded
    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id="cli_wkrA",
            platform_event_id="schedule:occurrence-1",
            channel_id="oc_chat",
            scope_key="t:scheduled",
            scope_kind="thread",
            reply_to_id="scheduled",
            inbound_message_id="temporary-ingest-value",
            sender_ref="scheduled",
            sender_open_id=None,
            text="scheduled work",
        ),
        account=account,
        session_maker=maker,
    )
    async with maker() as session:
        item = (
            await session.execute(
                select(IMRunQueueItem).where(IMRunQueueItem.account_id == account.id)
            )
        ).scalar_one()
        item.inbound_message_id = None
        await session.commit()

    run_manager = _FakeRunManager()
    processed = await process_one_queue_item(
        session_maker=maker,
        run_manager=run_manager,
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
    )

    assert processed is True
    assert run_manager.calls[0]["admission_id"] is None
    assert run_manager.calls[0]["run_id"] is None
    assert run_manager.calls[0]["cancel_pending_hitl"] is True
    async with maker() as session:
        admissions = list(
            await session.scalars(
                select(ConversationExecutionAdmission).where(
                    ConversationExecutionAdmission.org_id == account.org_id,
                    ConversationExecutionAdmission.workspace_id == account.workspace_id,
                )
            )
        )
        assert admissions == []


async def test_worker_returns_false_when_queue_empty(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, _ = _seeded
    rm = _FakeRunManager()
    ran = await process_one_queue_item(
        session_maker=maker,
        run_manager=rm,
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
    )
    assert ran is False
    assert rm.calls == []


async def test_connection_queue_requires_local_deliverability(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded
    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id="cli_wkrA",
            platform_event_id="ev-affinity",
            channel_id="oc_chat",
            scope_key="u:affinity",
            scope_kind="participant",
            reply_to_id="req-affinity",
            inbound_message_id="ev-affinity",
            sender_ref="affinity",
            sender_open_id="affinity",
            text="owner only",
        ),
        account=account,
        session_maker=maker,
    )
    rm = _FakeRunManager()

    non_owner_ran = await process_one_queue_item(
        session_maker=maker,
        run_manager=rm,
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
        deliverable_connection_ids=lambda: set(),
    )
    assert non_owner_ran is False
    assert rm.calls == []
    async with maker() as session:
        item = (
            await session.execute(
                select(IMRunQueueItem).where(IMRunQueueItem.account_id == account.id)
            )
        ).scalar_one()
        assert item.status == "pending"
        assert item.attempts == 0

    owner_ran = await process_one_queue_item(
        session_maker=maker,
        run_manager=rm,
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
        deliverable_connection_ids=lambda: {account.id},
    )
    assert owner_ran is True
    assert len(rm.calls) == 1


async def test_disabled_account_queue_is_parked_without_starting_a_run(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded
    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id="cli_wkrA",
            platform_event_id="ev-disabled",
            channel_id="oc_chat",
            scope_key="u:disabled",
            scope_kind="participant",
            reply_to_id="req-disabled",
            inbound_message_id="ev-disabled",
            sender_ref="disabled",
            sender_open_id="disabled",
            text="do not start",
        ),
        account=account,
        session_maker=maker,
    )
    async with maker() as session:
        stored_account = await session.get(IMConnectorAccount, account.id)
        assert stored_account is not None
        stored_account.enabled = False
        await session.commit()

    rm = _FakeRunManager()
    ran = await process_one_queue_item(
        session_maker=maker,
        run_manager=rm,
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
        deliverable_connection_ids=lambda: {account.id},
    )

    assert ran is True
    assert rm.calls == []
    async with maker() as session:
        item = (
            await session.execute(
                select(IMRunQueueItem).where(IMRunQueueItem.account_id == account.id)
            )
        ).scalar_one()
        assert item.status == "completed"


async def test_failed_live_lease_validation_rewinds_without_attempt_charge(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded
    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id="cli_wkrA",
            platform_event_id="ev-stale-owner",
            channel_id="oc_chat",
            scope_key="u:stale-owner",
            scope_kind="participant",
            reply_to_id="req-stale-owner",
            inbound_message_id="ev-stale-owner",
            sender_ref="stale-owner",
            sender_open_id="stale-owner",
            text="do not start",
        ),
        account=account,
        session_maker=maker,
    )
    rm = _FakeRunManager()

    ran = await process_one_queue_item(
        session_maker=maker,
        run_manager=rm,
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
        deliverable_connection_ids=lambda: {account.id},
        validate_connection_lease=lambda _account_id: _false(),
    )

    assert ran is False
    assert rm.calls == []
    async with maker() as session:
        item = (
            await session.execute(
                select(IMRunQueueItem).where(IMRunQueueItem.account_id == account.id)
            )
        ).scalar_one()
        assert item.status == "pending"
        assert item.attempts == 0


async def test_live_lease_validation_error_rewinds_without_attempt_charge(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded
    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id="cli_wkrA",
            platform_event_id="ev-validator-error",
            channel_id="oc_chat",
            scope_key="u:validator-error",
            scope_kind="participant",
            reply_to_id="req-validator-error",
            inbound_message_id="ev-validator-error",
            sender_ref="validator-error",
            sender_open_id="validator-error",
            text="retry after redis recovers",
        ),
        account=account,
        session_maker=maker,
    )

    async def broken_validator(_account_id: str) -> bool:
        raise TimeoutError("redis timed out")

    rm = _FakeRunManager()
    ran = await process_one_queue_item(
        session_maker=maker,
        run_manager=rm,
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
        deliverable_connection_ids=lambda: {account.id},
        validate_connection_lease=broken_validator,
    )

    assert ran is False
    assert rm.calls == []
    async with maker() as session:
        item = (
            await session.execute(
                select(IMRunQueueItem).where(IMRunQueueItem.account_id == account.id)
            )
        ).scalar_one()
        assert item.status == "pending"
        assert item.attempts == 0


async def test_account_disabled_during_prestart_validation_never_starts_run(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded
    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id="cli_wkrA",
            platform_event_id="ev-disable-race",
            channel_id="oc_chat",
            scope_key="u:disable-race",
            scope_kind="participant",
            reply_to_id="req-disable-race",
            inbound_message_id="ev-disable-race",
            sender_ref="disable-race",
            sender_open_id="disable-race",
            text="do not bill",
        ),
        account=account,
        session_maker=maker,
    )

    async def disable_then_validate(_account_id: str) -> bool:
        async with maker() as session:
            live_account = await session.get(IMConnectorAccount, account.id)
            assert live_account is not None
            live_account.enabled = False
            await session.commit()
        return True

    rm = _FakeRunManager()
    ran = await process_one_queue_item(
        session_maker=maker,
        run_manager=rm,
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
        deliverable_connection_ids=lambda: {account.id},
        validate_connection_lease=disable_then_validate,
    )

    assert ran is True
    assert rm.calls == []
    async with maker() as session:
        item = (
            await session.execute(
                select(IMRunQueueItem).where(IMRunQueueItem.account_id == account.id)
            )
        ).scalar_one()
        receipt = await session.get(IMWebhookReceipt, item.receipt_id)
        assert item.status == "completed"
        assert receipt is not None
        assert receipt.status == "failed"


async def test_account_deleted_during_prestart_validation_never_starts_run(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
) -> None:
    maker, account = _seeded
    await ingest_inbound_event(
        InboundEvent(
            platform="feishu",
            account_external_id="cli_wkrA",
            platform_event_id="ev-delete-race",
            channel_id="oc_chat",
            scope_key="u:delete-race",
            scope_kind="participant",
            reply_to_id="req-delete-race",
            inbound_message_id="ev-delete-race",
            sender_ref="delete-race",
            sender_open_id="delete-race",
            text="do not bill",
        ),
        account=account,
        session_maker=maker,
    )

    async def delete_then_validate(_account_id: str) -> bool:
        async with maker() as session:
            live_account = await session.get(IMConnectorAccount, account.id)
            assert live_account is not None
            await session.delete(live_account)
            await session.commit()
        return True

    rm = _FakeRunManager()
    ran = await process_one_queue_item(
        session_maker=maker,
        run_manager=rm,
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
        deliverable_connection_ids=lambda: {account.id},
        validate_connection_lease=delete_then_validate,
    )

    assert ran is False
    assert rm.calls == []
    async with maker() as session:
        assert await session.get(IMConnectorAccount, account.id) is None


async def _false() -> bool:
    return False


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
            run_id: str | None = None,
            model_key: str | None = None,
            reasoning: object | None = None,
            cancel_pending_hitl: bool = False,
            llm_snapshot: object | None = None,
            admission_id: str | None = None,
        ) -> str:
            raise RuntimeError("LLM exploded")

    did_run = await process_one_queue_item(
        session_maker=maker,
        run_manager=_BrokenRM(),
        on_run_started=None,
        lease_seconds=300,
        load_execution_snapshot=im_test_execution_snapshot,
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
