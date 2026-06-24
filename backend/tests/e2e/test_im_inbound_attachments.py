"""Inbound IM file attachments: ingest → worker resolve → AttachmentService → start_run.

Guards the headline inbound flow and its re-claim idempotency. Mocks only the
outermost platform fetch (`download_for`); Postgres, the object store, and
`AttachmentService` are real.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cubebox.im import inbound_attachments
from cubebox.im.inbound import ingest_inbound_event
from cubebox.im.inbound_attachments import make_resolver
from cubebox.im.types import InboundAttachmentRef, InboundEvent
from cubebox.im.worker import process_one_queue_item
from cubebox.models.attachment import Attachment
from cubebox.models.im_connector import IMConnectorAccount, IMRunQueueItem
from cubebox.streams.run_manager import RunContext
from tests.e2e.conftest import _build_database_url
from tests.e2e.im_fixtures import (
    im_cleanup,
    im_seed_account,
    im_seed_org_ws_user,
    im_seed_stub_credential,
)

pytestmark = pytest.mark.asyncio

_ORG_ID = "org-imatchA"
_WS_ID = "ws-imatchA"
_USER_ID = "usr-imatchA"
_CRED_ID = "cred-imatchA"
_ACCOUNT_ID = "imac-imatchA"

_PDF_BYTES = b"%PDF-1.4 fake attachment body"


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
                name="feishu:T-atchA",
            )
            await im_seed_account(
                session,
                account_id=_ACCOUNT_ID,
                org_id=_ORG_ID,
                ws_id=_WS_ID,
                user_id=_USER_ID,
                credential_id=_CRED_ID,
                external_account_id="cli_atchA",
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
                # Delete attachments first — they FK to conversations, which
                # im_cleanup deletes next.
                await session.execute(delete(Attachment).where(Attachment.workspace_id == _WS_ID))
                await session.commit()
                await im_cleanup(
                    session,
                    account_ids=[_ACCOUNT_ID],
                    ws_ids=[_WS_ID],
                    cleanup_conversations_in_ws=True,
                )
                await session.commit()
    finally:
        await engine.dispose()


class _RecordingRM:
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
        self.calls.append({"content": content, "attachments": attachments})
        return f"run-{len(self.calls)}"


def _file_event(event_id: str) -> InboundEvent:
    return InboundEvent(
        platform="feishu",
        account_external_id="cli_atchA",
        platform_event_id=event_id,
        channel_id="oc_chat",
        scope_key="u:on_userA",
        scope_kind="participant",
        reply_to_id="om_msgA",
        inbound_message_id="om_msgA",
        sender_ref="on_userA",
        sender_open_id="ou_userA",
        text="please read this",
        attachments=[
            InboundAttachmentRef(
                kind="file",
                filename="report.pdf",
                mime="application/pdf",
                handle="file_v3_resource_key",
            )
        ],
    )


def _resolver(maker: async_sessionmaker[AsyncSession]) -> object:
    async def _load_secrets(account: IMConnectorAccount) -> dict[str, object]:
        return {}

    def _client_for(key: tuple[str, str], secrets: dict[str, object]) -> object:
        return None

    return make_resolver(session_maker=maker, load_secrets=_load_secrets, client_for=_client_for)


async def _attachments_for_ws(maker: async_sessionmaker[AsyncSession]) -> list[Attachment]:
    async with maker() as s:
        return list(
            (await s.execute(select(Attachment).where(Attachment.workspace_id == _WS_ID))).scalars()
        )


async def test_feishu_inbound_file_materializes_attachment_and_starts_run(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Feishu file message → an Attachment row + start_run(attachments=[id]).

    Bug guarded: if the Feishu non-text drop re-tightens (or the worker stops
    resolving), inbound files silently vanish and start_run gets attachments=None.
    """
    maker, account = _seeded

    async def _fake_download(platform, client, ref, *, message_id):  # type: ignore[no-untyped-def]
        assert platform == "feishu"
        assert ref.handle == "file_v3_resource_key"
        return _PDF_BYTES

    monkeypatch.setattr(inbound_attachments, "download_for", _fake_download)

    await ingest_inbound_event(_file_event("evA1"), account=account, session_maker=maker)

    rm = _RecordingRM()
    did_run = await process_one_queue_item(
        session_maker=maker,
        run_manager=rm,
        on_run_started=None,
        lease_seconds=300,
        resolve_inbound_attachments=_resolver(maker),
    )

    assert did_run is True
    assert len(rm.calls) == 1
    attachment_ids = rm.calls[0]["attachments"]
    assert isinstance(attachment_ids, list) and len(attachment_ids) == 1

    rows = await _attachments_for_ws(maker)
    assert len(rows) == 1
    assert rows[0].id == attachment_ids[0]
    assert rows[0].filename == "report.pdf"
    assert rows[0].size_bytes == len(_PDF_BYTES)


async def test_reclaim_reuses_persisted_ids_without_duplicate_upload(
    _seeded: tuple[async_sessionmaker[AsyncSession], IMConnectorAccount],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 'already has an active run' rewind must not re-download/re-upload.

    Bug guarded: re-resolving on every re-claim mints duplicate Attachment rows
    and inflates the per-conversation quota.
    """
    maker, account = _seeded

    download_calls = {"n": 0}

    async def _fake_download(platform, client, ref, *, message_id):  # type: ignore[no-untyped-def]
        download_calls["n"] += 1
        return _PDF_BYTES

    monkeypatch.setattr(inbound_attachments, "download_for", _fake_download)
    await ingest_inbound_event(_file_event("evA2"), account=account, session_maker=maker)
    resolver = _resolver(maker)

    class _BusyRM:
        async def start_run(self, **kwargs: object) -> str:
            raise RuntimeError("conversation already has an active run")

    # First claim: resolve runs, ids persist, then start_run rewinds the row.
    await process_one_queue_item(
        session_maker=maker,
        run_manager=_BusyRM(),
        on_run_started=None,
        lease_seconds=300,
        resolve_inbound_attachments=resolver,
    )
    assert download_calls["n"] == 1
    async with maker() as s:
        item = (
            await s.execute(select(IMRunQueueItem).where(IMRunQueueItem.account_id == _ACCOUNT_ID))
        ).scalar_one()
        assert item.status == "pending"
        assert item.attachment_ids and len(item.attachment_ids) == 1
    first_rows = await _attachments_for_ws(maker)
    assert len(first_rows) == 1

    # Re-claim: ids already present → no re-resolve, no second upload.
    rm = _RecordingRM()
    await process_one_queue_item(
        session_maker=maker,
        run_manager=rm,
        on_run_started=None,
        lease_seconds=300,
        resolve_inbound_attachments=resolver,
    )
    assert download_calls["n"] == 1  # NOT re-downloaded
    assert rm.calls[0]["attachments"] == [first_rows[0].id]
    assert len(await _attachments_for_ws(maker)) == 1  # no duplicate Attachment
