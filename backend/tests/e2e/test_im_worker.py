"""Integration tests for the IM queue worker (Task 6)."""

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
from cubebox.im.worker import process_one_queue_item
from cubebox.models.im_connector import (
    IMConnectorAccount,
    IMRunQueueItem,
    IMWebhookReceipt,
)
from cubebox.streams.run_manager import RunContext
from tests.e2e.conftest import _build_database_url

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
            await session.execute(
                text(
                    "INSERT INTO organizations (id, name, slug, created_at)"
                    " VALUES (:id, :id, :id, NOW()) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": _ORG_ID},
            )
            await session.execute(
                text(
                    "INSERT INTO workspaces (id, org_id, name, created_at)"
                    " VALUES (:id, :org, :id, NOW()) ON CONFLICT (id) DO NOTHING"
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
                    " VALUES (:id, :org, 'im_bot', 'feishu:T-wkrA', '\\x00'::bytea,"
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
                    " VALUES (:id, :org, :ws, 'feishu', 'cli_wkrA', :uid, :cred,"
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
                    text("DELETE FROM conversations WHERE workspace_id = :id"),
                    {"id": _WS_ID},
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
    ) -> str:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "content": content,
                "user_id": ctx.user_id,
                "org_id": ctx.org_id,
                "workspace_id": ctx.workspace_id,
                "trigger": ctx.trigger,
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
        assert item.status == "started"
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
        ) -> str:
            raise RuntimeError("LLM exploded")

    did_run = await process_one_queue_item(
        session_maker=maker, run_manager=_BrokenRM(), on_run_started=None, lease_seconds=300
    )
    assert did_run is True  # we processed (and failed) one row
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
        assert item.status == "started"
        assert item.claim_lease_expires_at is not None
        assert rcpt.status == "pending"
