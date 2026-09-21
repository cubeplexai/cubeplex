"""Accepted inputs retain their files; deletion claims cannot become attached."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from cubeplex.models import Attachment
from cubeplex.objectstore import ObjectStoreClient
from cubeplex.repositories.attachment import AttachmentRepository
from cubeplex.services.attachments import cleanup_orphan_attachments
from cubeplex.services.conversation_execution import UserMessageIntent
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID
from tests.e2e.test_background_task_reservation import NOW, ReservationContext
from tests.e2e.test_conversation_execution_control import actor_id, service, snapshot

reservation_context = reservation_fixtures.reservation_context


@pytest_asyncio.fixture
async def pending_file(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
) -> AsyncIterator[tuple[str, str, str]]:
    actor = await actor_id(db_session, reservation_context)
    key = f"test-execution-attachments/{uuid4()}/input.txt"
    store = ObjectStoreClient()
    await store.upload_file(key, b"keep accepted input", "text/plain")
    row = Attachment(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=reservation_context.conversation_id,
        uploader_user_id=actor,
        filename="input.txt",
        mime_type="text/plain",
        size_bytes=19,
        kind="document",
        object_key=key,
        sandbox_path="/workspace/input.txt",
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add(row)
    await db_session.commit()
    row_id = row.id
    try:
        yield row_id, key, actor
    finally:
        await db_session.rollback()
        await db_session.execute(delete(Attachment).where(col(Attachment.id) == row_id))
        await db_session.commit()
        await store.delete_file(key)
        from cubeplex.db.engine import engine

        await engine.dispose()


async def test_accepted_file_survives_orphan_cleanup(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    pending_file: tuple[str, str, str],
) -> None:
    row_id, key, actor = pending_file
    await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="read", attachment_ids=(row_id,)),
        snapshot=snapshot(),
        now=NOW,
    )
    await db_session.commit()
    await cleanup_orphan_attachments()
    data, _ = await ObjectStoreClient().download_file(key)
    assert data == b"keep accepted input"
    row = await db_session.get(Attachment, row_id, populate_existing=True)
    assert row is not None and row.status == "attached"


async def test_deletion_claim_rejects_admission_before_object_disappears(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    pending_file: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row_id, key, actor = pending_file
    entered, release = asyncio.Event(), asyncio.Event()
    delete_file = ObjectStoreClient.delete_file

    async def held_delete(store: ObjectStoreClient, object_key: str) -> None:
        if object_key == key:
            entered.set()
            await release.wait()
        await delete_file(store, object_key)

    monkeypatch.setattr(ObjectStoreClient, "delete_file", held_delete)
    worker = asyncio.create_task(cleanup_orphan_attachments())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        row = await db_session.get(Attachment, row_id, populate_existing=True)
        assert row is not None and row.status == "deleting"
        with pytest.raises(ValueError, match="attachment"):
            await service(db_session).admit_user_message(
                conversation_id=reservation_context.conversation_id,
                actor_user_id=actor,
                namespace="web",
                source_id=str(uuid4()),
                intent=UserMessageIntent(content="read", attachment_ids=(row_id,)),
                snapshot=snapshot(),
                now=NOW,
            )
    finally:
        await db_session.rollback()
        release.set()
        await asyncio.wait_for(worker, timeout=10)


async def test_object_delete_failure_keeps_durable_retry_evidence(
    db_session: AsyncSession,
    pending_file: tuple[str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row_id, key, _ = pending_file
    delete_file = ObjectStoreClient.delete_file

    async def failed_delete(store: ObjectStoreClient, object_key: str) -> None:
        if object_key == key:
            raise OSError("object storage unavailable")
        await delete_file(store, object_key)

    with monkeypatch.context() as patch:
        patch.setattr(ObjectStoreClient, "delete_file", failed_delete)
        await cleanup_orphan_attachments()
    row = await db_session.get(Attachment, row_id, populate_existing=True)
    assert row is not None and row.status == "deleting"
    repo = AttachmentRepository(db_session, org_id=DEFAULT_ORG_ID, workspace_id=DEFAULT_WS_ID)
    assert (
        await repo.get_in_conversation(conversation_id=row.conversation_id, attachment_id=row_id)
        is None
    )
    assert await repo.list_by_conversation(conversation_id=row.conversation_id) == []
    await db_session.rollback()
    await cleanup_orphan_attachments()
    assert await db_session.get(Attachment, row_id, populate_existing=True) is None
    assert await ObjectStoreClient().list_objects(key) == []


async def test_stale_cleanup_candidate_is_rechecked_after_admission(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    pending_file: tuple[str, str, str],
) -> None:
    row_id, key, actor = pending_file
    async with session_factory() as stale_session:
        repo = AttachmentRepository(
            stale_session, org_id=DEFAULT_ORG_ID, workspace_id=DEFAULT_WS_ID
        )
        candidate = await repo.get_in_conversation(
            conversation_id=reservation_context.conversation_id, attachment_id=row_id
        )
        assert candidate is not None and candidate.status == "pending"
        await service(db_session).admit_user_message(
            conversation_id=reservation_context.conversation_id,
            actor_user_id=actor,
            namespace="web",
            source_id=str(uuid4()),
            intent=UserMessageIntent(content="read", attachment_ids=(row_id,)),
            snapshot=snapshot(),
            now=NOW,
        )
        await db_session.commit()
        assert (
            await repo.claim_pending_deletion(
                conversation_id=reservation_context.conversation_id,
                attachment_id=row_id,
                older_than=datetime.now(UTC) - timedelta(hours=1),
            )
            is None
        )
        await stale_session.commit()
    assert (await ObjectStoreClient().download_file(key))[0] == b"keep accepted input"
