"""collect_runtime_aggregates: 3 batch queries return dict keyed by account_id.

The IM e2e tests don't rely on shared conftest fixtures for org / workspace
/ user / credential — they bootstrap each one inline (see
``backend/tests/e2e/test_im_worker.py`` for the pattern). This unit test
follows the same approach with a hand-rolled session_maker.
"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cubeplex.models.im_connector import (
    IMConnectorAccount,
    IMRunQueueItem,
    IMWebhookReceipt,
)
from cubeplex.repositories.im_connector import collect_runtime_aggregates
from tests.e2e.conftest import _build_database_url
from tests.e2e.im_fixtures import im_cleanup, im_seed_org_ws_user, im_seed_stub_credential

pytestmark = pytest.mark.asyncio

_ORG_ID = "org-rta01"
_WS_ID = "ws-rta01"
_USER_ID = "usr-rta01"
_CRED_ID = "cred-rta01"
_CONV_ID = "conv-rta01"


@pytest_asyncio.fixture
async def session_maker() -> async_sessionmaker[AsyncSession]:
    """Build a per-test session_maker against the worktree-scoped test DB."""
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await im_seed_org_ws_user(
            s, org_id=_ORG_ID, ws_id=_WS_ID, user_id=_USER_ID, email="rta@example.com"
        )
        await im_seed_stub_credential(
            s, credential_id=_CRED_ID, org_id=_ORG_ID, name="feishu:cli_rta"
        )
        await s.execute(
            text(
                "INSERT INTO conversations (id, org_id, workspace_id, "
                "creator_user_id, title, is_group_chat, reasoning, attributes,"
                " created_at, updated_at) VALUES "
                "(:id, :org, :ws, :u, 'rta', false, '{}'::jsonb, '{}'::jsonb,"
                " NOW(), NOW()) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": _CONV_ID, "org": _ORG_ID, "ws": _WS_ID, "u": _USER_ID},
        )
        await s.commit()
    yield maker
    async with maker() as s:
        # Accounts here are created per-test via _mk_account; sweep them
        # by org_id rather than ids since we don't track them centrally.
        await s.execute(text("DELETE FROM im_run_queue WHERE org_id = :o"), {"o": _ORG_ID})
        await s.execute(text("DELETE FROM im_webhook_receipts WHERE org_id = :o"), {"o": _ORG_ID})
        await s.execute(text("DELETE FROM im_connector_accounts WHERE org_id = :o"), {"o": _ORG_ID})
        await s.execute(text("DELETE FROM conversations WHERE id = :c"), {"c": _CONV_ID})
        await im_cleanup(
            s,
            credential_ids=[_CRED_ID],
            ws_ids=[_WS_ID],
            user_ids=[_USER_ID],
            org_ids=[_ORG_ID],
        )
        await s.commit()
    await engine.dispose()


async def _mk_account(session: AsyncSession, ext: str = "cli_a") -> IMConnectorAccount:
    acc = IMConnectorAccount(
        org_id=_ORG_ID,
        workspace_id=_WS_ID,
        platform="feishu",
        external_account_id=ext,
        acting_user_id=_USER_ID,
        credential_id=_CRED_ID,
        delivery_mode="long_connection",
        enabled=True,
    )
    session.add(acc)
    await session.flush()
    return acc


async def test_empty_inputs_return_empty_dict(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with session_maker() as session:
        out = await collect_runtime_aggregates(session, account_ids=[])
        assert out == {}


async def test_pending_count_includes_pending_and_started(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """im_run_queue rows in status pending + started both contribute."""
    async with session_maker() as session:
        acc = await _mk_account(session, ext="cli_a")
        receipt = IMWebhookReceipt(
            org_id=_ORG_ID,
            workspace_id=_WS_ID,
            account_id=acc.id,
            platform_event_id="e1",
            status="pending",
        )
        session.add(receipt)
        await session.flush()
        for s in ("pending", "started", "completed"):
            session.add(
                IMRunQueueItem(
                    org_id=_ORG_ID,
                    workspace_id=_WS_ID,
                    account_id=acc.id,
                    receipt_id=receipt.id,
                    conversation_id=_CONV_ID,
                    content="x",
                    channel_id="oc-1",
                    scope_key="dm",
                    scope_kind="dm",
                    status=s,
                )
            )
        await session.commit()
        out = await collect_runtime_aggregates(session, account_ids=[acc.id])
    assert out[acc.id].pending_count == 2
