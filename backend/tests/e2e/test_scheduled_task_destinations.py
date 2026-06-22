"""E2E: schedule destinations — topic_id wiring + im_channel dispatch.

Each test protects one business invariant. The one-line bug the test
would catch lives in the docstring above the test. Per-test workspaces
prevent the shared DEFAULT_WS from accumulating rows that turn other
suites flaky.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

import cubebox.db as _db
from cubebox.models import IMConnectorAccount, Workspace
from cubebox.models.conversation import Conversation
from cubebox.models.conversation_participant import ConversationParticipant
from cubebox.models.im_channel_binding import IMChannelBinding
from cubebox.models.im_connector import (
    IMRunQueueItem,
    IMThreadLink,
    IMWebhookReceipt,
)
from cubebox.models.public_id import generate_public_id
from cubebox.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubebox.schedules.dispatch import dispatch_scheduled_run

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Test seed helpers — per-test IDs to keep rows independent
# ---------------------------------------------------------------------------


async def _get_my_user_id(client: httpx.AsyncClient) -> str:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 200, r.text
    user_id: str = r.json()["id"]
    return user_id


async def _resolve_org_id(ws_id: str) -> str:
    async with _db.async_session_maker() as session:
        ws = await session.get(Workspace, ws_id)
        assert ws is not None
        return ws.org_id


async def _create_topic(client: httpx.AsyncClient, ws_id: str, title: str) -> str:
    r = await client.post(f"/api/v1/ws/{ws_id}/topics", json={"title": title})
    assert r.status_code in (200, 201), r.text
    tid: str = r.json()["topic"]["id"]
    assert tid.startswith("top")
    return tid


async def _seed_im_account(
    client: httpx.AsyncClient,
    ws_id: str,
    external_account_id: str | None = None,
) -> str:
    """Seed one IMConnectorAccount row + credential via raw SQL.

    Mirrors the helper in ``test_ws_triggers.py`` so the destination tests do
    not need to go through the full Feishu OAuth bootstrap path. Returns the
    new account id.
    """
    if external_account_id is None:
        external_account_id = f"ext-{secrets.token_hex(8)}"
    else:
        external_account_id = f"{external_account_id}-{secrets.token_hex(4)}"
    user_id = await _get_my_user_id(client)
    cred_id = generate_public_id("cred")
    async with _db.async_session_maker() as session:
        org_id = (await session.get(Workspace, ws_id)).org_id  # type: ignore[union-attr]
        await session.execute(
            text(
                "INSERT INTO credentials (id, org_id, kind, name, value_encrypted,"
                " cred_metadata, created_by_user_id, created_at, updated_at)"
                " VALUES (:id, :org, 'im_bot', :name, '\\x00'::bytea,"
                " '{}'::jsonb, :uid, NOW(), NOW())"
            ),
            {
                "id": cred_id,
                "org": org_id,
                "name": f"im-account:{external_account_id}:{cred_id}",
                "uid": user_id,
            },
        )
        account = IMConnectorAccount(
            org_id=org_id,
            workspace_id=ws_id,
            platform="feishu",
            external_account_id=external_account_id,
            acting_user_id=user_id,
            credential_id=cred_id,
        )
        session.add(account)
        await session.commit()
        await session.refresh(account)
        return account.id


async def _seed_binding(
    *,
    org_id: str,
    ws_id: str,
    account_id: str,
    channel_id: str,
    mode: str = "isolated",
    topic_id: str | None = None,
    channel_name: str = "test-channel",
    sandbox_mode: str | None = None,
) -> str:
    async with _db.async_session_maker() as session:
        binding = IMChannelBinding(
            org_id=org_id,
            workspace_id=ws_id,
            account_id=account_id,
            channel_id=channel_id,
            channel_name=channel_name,
            mode=mode,
            sandbox_mode=sandbox_mode,
            topic_id=topic_id,
        )
        session.add(binding)
        await session.commit()
        await session.refresh(binding)
        return binding.id


async def _seed_existing_link(
    *,
    org_id: str,
    ws_id: str,
    account_id: str,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    user_id: str,
    title: str = "existing-conv",
) -> tuple[str, str]:
    """Create a Conversation + IMThreadLink. Returns ``(conv_id, link_id)``."""
    async with _db.async_session_maker() as session:
        conv = Conversation(
            org_id=org_id,
            workspace_id=ws_id,
            creator_user_id=user_id,
            title=title,
            is_group_chat=False,
        )
        session.add(conv)
        await session.flush()
        link = IMThreadLink(
            org_id=org_id,
            workspace_id=ws_id,
            account_id=account_id,
            channel_id=channel_id,
            scope_key=scope_key,
            scope_kind=scope_kind,
            conversation_id=conv.id,
        )
        session.add(link)
        await session.commit()
        await session.refresh(conv)
        await session.refresh(link)
        return conv.id, link.id


async def _create_schedule_row(
    *,
    org_id: str,
    ws_id: str,
    owner_user_id: str,
    target_mode: str,
    target_conversation_id: str | None = None,
    topic_id: str | None = None,
    im_account_id: str | None = None,
    im_channel_id: str | None = None,
    im_scope_key: str | None = None,
    im_scope_kind: str | None = None,
    prompt: str = "ping",
    name: str = "destination-test",
) -> str:
    """Insert a ScheduledTask row directly. Returns the task id.

    Using the service path here would also exercise next_fire_at compute,
    which the dispatch tests don't need (they invoke dispatch_scheduled_run
    directly without going through the poller's claim window).
    """
    async with _db.async_session_maker() as session:
        task = ScheduledTask(
            org_id=org_id,
            workspace_id=ws_id,
            owner_user_id=owner_user_id,
            name=name,
            prompt=prompt,
            schedule_kind="interval",
            interval_seconds=3600,
            target_mode=target_mode,
            target_conversation_id=target_conversation_id,
            topic_id=topic_id,
            im_account_id=im_account_id,
            im_channel_id=im_channel_id,
            im_scope_key=im_scope_key,
            im_scope_kind=im_scope_kind,
            status="active",
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id


async def _claim_one_run(*, task_id: str, org_id: str, ws_id: str) -> str:
    """Insert a claimed ScheduledTaskRun for the task and return its id.

    The dispatcher reads ``state='claimed'`` and writes the terminal state on
    that row; the poller normally inserts this row inside its claim
    transaction, which we shortcut here because the destination tests are
    interested in the dispatcher branch, not the claim race.
    """
    from datetime import UTC, datetime

    async with _db.async_session_maker() as session:
        now = datetime.now(UTC)
        row = ScheduledTaskRun(
            scheduled_task_id=task_id,
            org_id=org_id,
            workspace_id=ws_id,
            scheduled_for=now,
            claimed_at=now,
            state="claimed",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


def _run_manager(client: httpx.AsyncClient) -> Any:
    app = client._transport.app  # type: ignore[attr-defined]
    return app.state.run_manager


async def _dispatch_via_session(
    *,
    task_id: str,
    run_row_id: str,
    run_manager: Any,
) -> None:
    """Open one session, load both rows, drive dispatch, return."""
    async with _db.async_session_maker() as session:
        task = await session.get(ScheduledTask, task_id)
        assert task is not None
        run_row = await session.get(ScheduledTaskRun, run_row_id)
        assert run_row is not None
        await dispatch_scheduled_run(
            task=task,
            run_manager=run_manager,
            session=session,
            run_row=run_row,
        )


# ---------------------------------------------------------------------------
# Cleanup fixture — per-test wipe of all rows we might create
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def cleanup_destinations(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Yields ``(client, ws_id)``; cleans up destination rows on teardown.

    The authenticated_client fixture already gives an isolated workspace, so
    cross-test pollution is bounded — but rows created via raw SQL or direct
    SQLModel inserts (IM accounts, channel bindings, thread links, scheduled
    tasks, runs, queue items, receipts) survive across tests in the *same*
    workspace. The fixture wipes them in reverse-FK order on teardown.
    """
    client, ws_id = authenticated_client
    yield client, ws_id

    async with _db.async_session_maker() as session:
        # Order matches FK direction (children → parents). The
        # ``authenticated_client`` fixture gives us a brand-new workspace,
        # so we don't need to be surgical — wipe everything we might have
        # written that hangs off the workspace.
        await session.execute(
            delete(IMRunQueueItem).where(
                IMRunQueueItem.workspace_id == ws_id  # type: ignore[arg-type]
            )
        )
        await session.execute(
            delete(IMWebhookReceipt).where(
                IMWebhookReceipt.workspace_id == ws_id  # type: ignore[arg-type]
            )
        )
        await session.execute(
            delete(IMThreadLink).where(
                IMThreadLink.workspace_id == ws_id  # type: ignore[arg-type]
            )
        )
        await session.execute(
            delete(IMChannelBinding).where(
                IMChannelBinding.workspace_id == ws_id  # type: ignore[arg-type]
            )
        )
        await session.execute(
            delete(ScheduledTaskRun).where(
                ScheduledTaskRun.workspace_id == ws_id  # type: ignore[arg-type]
            )
        )
        # Null FK columns so the parent rows can drop next.
        await session.execute(
            text(
                "UPDATE scheduled_tasks SET topic_id = NULL, im_account_id = NULL "
                "WHERE workspace_id = :ws"
            ),
            {"ws": ws_id},
        )
        await session.execute(
            text("DELETE FROM scheduled_tasks WHERE workspace_id = :ws"),
            {"ws": ws_id},
        )
        # Conversations drag a long FK trail — embedding_jobs,
        # conversation_search_index, messages, billing_events, artifacts,
        # attachments — that other tests already learned to wipe in this
        # order. Mirror the same shape here.
        await session.execute(
            text(
                "DELETE FROM embedding_jobs WHERE conversation_id IN "
                "(SELECT id FROM conversations WHERE workspace_id = :ws)"
            ),
            {"ws": ws_id},
        )
        await session.execute(
            text("UPDATE conversations SET topic_id = NULL WHERE workspace_id = :ws"),
            {"ws": ws_id},
        )
        await session.execute(
            text(
                "DELETE FROM topic_participants WHERE topic_id IN "
                "(SELECT id FROM topics WHERE workspace_id = :ws)"
            ),
            {"ws": ws_id},
        )
        await session.execute(
            text("DELETE FROM topics WHERE workspace_id = :ws"),
            {"ws": ws_id},
        )
        await session.execute(
            text(
                "DELETE FROM conversation_participants "
                "WHERE conversation_id IN "
                "(SELECT id FROM conversations WHERE workspace_id = :ws)"
            ),
            {"ws": ws_id},
        )
        await session.execute(
            text("DELETE FROM conversations WHERE workspace_id = :ws"),
            {"ws": ws_id},
        )
        await session.execute(
            text("DELETE FROM im_connector_accounts WHERE workspace_id = :ws"),
            {"ws": ws_id},
        )
        # Cross-workspace FK tests mint a sibling workspace in the same
        # org (see _seed_topic_in_other_workspace /
        # _seed_im_account_in_other_workspace). Wipe any non-primary
        # workspace under the test's org so the next test sees a clean
        # slate. The primary ws (passed in via fixture) is deleted by the
        # outer authenticated_client teardown.
        await session.execute(
            text(
                "DELETE FROM topics WHERE workspace_id IN "
                "(SELECT id FROM workspaces WHERE org_id = "
                "(SELECT org_id FROM workspaces WHERE id = :ws) "
                "AND id != :ws)"
            ),
            {"ws": ws_id},
        )
        await session.execute(
            text(
                "DELETE FROM im_connector_accounts WHERE workspace_id IN "
                "(SELECT id FROM workspaces WHERE org_id = "
                "(SELECT org_id FROM workspaces WHERE id = :ws) "
                "AND id != :ws)"
            ),
            {"ws": ws_id},
        )
        await session.execute(
            text(
                "DELETE FROM workspaces WHERE org_id = "
                "(SELECT org_id FROM workspaces WHERE id = :ws) "
                "AND id != :ws"
            ),
            {"ws": ws_id},
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Schedule tests — topic_id plumbing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_each_run_with_topic_creates_conv_in_topic(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: dispatcher forgets to thread topic_id through
    ConversationRepository.create — newly minted run conversations float
    free of any topic and the sidebar grouping breaks."""
    client, ws_id = cleanup_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)
    topic_id = await _create_topic(client, ws_id, "stask-topic")

    task_id = await _create_schedule_row(
        org_id=org_id,
        ws_id=ws_id,
        owner_user_id=user_id,
        target_mode="new_each_run",
        topic_id=topic_id,
        prompt="weekly digest",
    )
    rm = _run_manager(client)
    # Call dispatch_scheduled_run directly without the poller — the
    # non-im branch only needs the run_manager + task; the poller's
    # claim window is irrelevant to the topic-wiring invariant.
    async with _db.async_session_maker() as session:
        task = await session.get(ScheduledTask, task_id)
        assert task is not None
    result = await dispatch_scheduled_run(task=task, run_manager=rm)
    assert result is not None
    async with _db.async_session_maker() as session:
        conv = await session.get(Conversation, result.conversation_id)
    assert conv is not None
    assert conv.topic_id == topic_id


# ---------------------------------------------------------------------------
# Schedule tests — im_channel dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_im_channel_reuses_existing_link(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: dispatcher mints a fresh conv instead of reusing
    the live IMThreadLink — the schedule's IM reply lands in a new
    conversation the user can't see in the channel context."""
    client, ws_id = cleanup_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)

    account_id = await _seed_im_account(client, ws_id, "reuse-link")
    channel_id = "C-reuse"
    scope_key = "dm"
    scope_kind = "dm"

    existing_conv_id, _link_id = await _seed_existing_link(
        org_id=org_id,
        ws_id=ws_id,
        account_id=account_id,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        user_id=user_id,
    )

    task_id = await _create_schedule_row(
        org_id=org_id,
        ws_id=ws_id,
        owner_user_id=user_id,
        target_mode="im_channel",
        im_account_id=account_id,
        im_channel_id=channel_id,
        im_scope_key=scope_key,
        im_scope_kind=scope_kind,
        prompt="reuse-prompt",
    )
    run_row_id = await _claim_one_run(task_id=task_id, org_id=org_id, ws_id=ws_id)

    await _dispatch_via_session(
        task_id=task_id,
        run_row_id=run_row_id,
        run_manager=_run_manager(client),
    )

    async with _db.async_session_maker() as session:
        run = await session.get(ScheduledTaskRun, run_row_id)
        assert run is not None
        assert run.state == "succeeded"
        assert run.detail == "im_channel_enqueued"
        assert run.conversation_id == existing_conv_id

        queue_items = (
            (
                await session.execute(
                    select(IMRunQueueItem).where(
                        IMRunQueueItem.account_id == account_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(queue_items) == 1
        item = queue_items[0]
        assert item.conversation_id == existing_conv_id
        # ``status`` may already have been advanced by the IM run-queue worker
        # (it polls in the background while the test app is up). We only
        # need to assert the dispatcher *wrote* the row and pointed it at
        # the right conversation; the worker's draining is exercised
        # separately in test_im_worker.py.
        assert item.channel_id == channel_id
        assert item.scope_key == scope_key
        assert item.scope_kind == scope_kind

        receipts = (
            (
                await session.execute(
                    select(IMWebhookReceipt).where(
                        IMWebhookReceipt.account_id == account_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(receipts) == 1
        assert receipts[0].platform_event_id == f"schedule:{run_row_id}"


@pytest.mark.asyncio
async def test_im_channel_creates_fresh_after_new(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: dispatcher dies (or silently no-ops) when the user
    `/new`'d before the schedule fires — there's no IMThreadLink, so the
    resolver must mint a fresh conv + link."""
    client, ws_id = cleanup_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)

    account_id = await _seed_im_account(client, ws_id, "new-after")
    channel_id = "C-fresh"
    scope_key = "dm"
    scope_kind = "dm"

    # Simulate "user typed /new" by NOT seeding an IMThreadLink. The
    # dispatcher's first call to resolve_im_conversation will need to mint
    # a fresh conversation.

    task_id = await _create_schedule_row(
        org_id=org_id,
        ws_id=ws_id,
        owner_user_id=user_id,
        target_mode="im_channel",
        im_account_id=account_id,
        im_channel_id=channel_id,
        im_scope_key=scope_key,
        im_scope_kind=scope_kind,
        prompt="fresh-prompt",
    )
    run_row_id = await _claim_one_run(task_id=task_id, org_id=org_id, ws_id=ws_id)
    await _dispatch_via_session(
        task_id=task_id,
        run_row_id=run_row_id,
        run_manager=_run_manager(client),
    )

    async with _db.async_session_maker() as session:
        run = await session.get(ScheduledTaskRun, run_row_id)
        assert run is not None
        assert run.state == "succeeded"
        assert run.conversation_id is not None

        # A new IMThreadLink was minted and points at the new conv.
        links = (
            (
                await session.execute(
                    select(IMThreadLink).where(
                        IMThreadLink.account_id == account_id,  # type: ignore[arg-type]
                        IMThreadLink.channel_id == channel_id,  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(links) == 1
        assert links[0].conversation_id == run.conversation_id

        queue_items = (
            (
                await session.execute(
                    select(IMRunQueueItem).where(
                        IMRunQueueItem.account_id == account_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(queue_items) == 1
        assert queue_items[0].conversation_id == run.conversation_id


@pytest.mark.asyncio
async def test_im_channel_shared_mode_inherits_binding_topic(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: resolver loses shared-mode topic inheritance
    on the dispatcher path — the schedule's new conv lands at the root
    instead of under the channel's topic, and the channel-wide
    participant insertion is skipped."""
    client, ws_id = cleanup_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)

    account_id = await _seed_im_account(client, ws_id, "shared")
    channel_id = "C-shared"
    scope_key = "ch"
    scope_kind = "channel"

    # Pre-create the shared topic so we can assert binding.topic_id reuse.
    topic_id = await _create_topic(client, ws_id, "shared-topic")
    await _seed_binding(
        org_id=org_id,
        ws_id=ws_id,
        account_id=account_id,
        channel_id=channel_id,
        mode="shared",
        topic_id=topic_id,
        channel_name="shared-binding",
    )

    task_id = await _create_schedule_row(
        org_id=org_id,
        ws_id=ws_id,
        owner_user_id=user_id,
        target_mode="im_channel",
        im_account_id=account_id,
        im_channel_id=channel_id,
        im_scope_key=scope_key,
        im_scope_kind=scope_kind,
        prompt="shared-prompt",
    )
    run_row_id = await _claim_one_run(task_id=task_id, org_id=org_id, ws_id=ws_id)
    await _dispatch_via_session(
        task_id=task_id,
        run_row_id=run_row_id,
        run_manager=_run_manager(client),
    )

    async with _db.async_session_maker() as session:
        run = await session.get(ScheduledTaskRun, run_row_id)
        assert run is not None
        assert run.state == "succeeded"
        conv = await session.get(Conversation, run.conversation_id)
        assert conv is not None
        assert conv.topic_id == topic_id
        assert conv.is_group_chat is True

        # Owner participant was inserted by the resolver.
        cp = (
            await session.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conv.id,  # type: ignore[arg-type]
                    ConversationParticipant.user_id == user_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        assert cp is not None


# ---------------------------------------------------------------------------
# Schedule tests — deletion + validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_im_account_deletion_marks_run_failed(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: dispatcher crashes (assertion) when the bound IM
    account was deleted — the FK uses ON DELETE SET NULL, leaving
    ``im_account_id`` NULL. Without a NULL-aware failure branch the
    poller spins on the same run and the operator can't tell from the
    run-history row why the schedule died."""
    client, ws_id = cleanup_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)

    account_id = await _seed_im_account(client, ws_id, "doomed-acct")
    task_id = await _create_schedule_row(
        org_id=org_id,
        ws_id=ws_id,
        owner_user_id=user_id,
        target_mode="im_channel",
        im_account_id=account_id,
        im_channel_id="C-dead",
        im_scope_key="dm",
        im_scope_kind="dm",
    )

    # Delete the IM account; the FK is ON DELETE SET NULL, so the schedule
    # survives but its im_account_id goes NULL.
    async with _db.async_session_maker() as session:
        await session.execute(
            text("DELETE FROM im_connector_accounts WHERE id = :id"),
            {"id": account_id},
        )
        await session.commit()

    # Verify the schedule row's im_account_id was cleared by the FK action.
    async with _db.async_session_maker() as session:
        task = await session.get(ScheduledTask, task_id)
        assert task is not None
        assert task.im_account_id is None, (
            "ON DELETE SET NULL did not clear the FK column — destination migration regression"
        )

    run_row_id = await _claim_one_run(task_id=task_id, org_id=org_id, ws_id=ws_id)
    await _dispatch_via_session(
        task_id=task_id,
        run_row_id=run_row_id,
        run_manager=_run_manager(client),
    )

    async with _db.async_session_maker() as session:
        run = await session.get(ScheduledTaskRun, run_row_id)
        assert run is not None
        assert run.state == "failed"
        assert run.detail == "im_account_unlinked"

        queue_items = (
            (
                await session.execute(
                    select(IMRunQueueItem).where(
                        IMRunQueueItem.workspace_id == ws_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert queue_items == []


@pytest.mark.asyncio
async def test_topic_deletion_sets_topic_id_null_and_continues(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: the topic_id FK is missing ON DELETE SET NULL,
    or the dispatcher errors when the column is NULL. Either way a
    deleted topic would break every schedule that referenced it."""
    client, ws_id = cleanup_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)
    topic_id = await _create_topic(client, ws_id, "to-be-deleted")

    task_id = await _create_schedule_row(
        org_id=org_id,
        ws_id=ws_id,
        owner_user_id=user_id,
        target_mode="new_each_run",
        topic_id=topic_id,
        prompt="orphan after topic delete",
    )

    # Hard-delete the topic. SET NULL fires on scheduled_tasks.topic_id and
    # the column clears automatically. The topic API also creates a default
    # conversation under the topic, so null out conversations.topic_id
    # first (the conversation FK is not ON DELETE SET NULL).
    async with _db.async_session_maker() as session:
        await session.execute(
            text("UPDATE conversations SET topic_id = NULL WHERE topic_id = :tid"),
            {"tid": topic_id},
        )
        await session.execute(
            text("DELETE FROM topic_participants WHERE topic_id = :tid"),
            {"tid": topic_id},
        )
        await session.execute(
            text("DELETE FROM topics WHERE id = :tid"),
            {"tid": topic_id},
        )
        await session.commit()

    async with _db.async_session_maker() as session:
        task = await session.get(ScheduledTask, task_id)
        assert task is not None
        assert task.topic_id is None

    result = await dispatch_scheduled_run(
        task=await _fetch_task(task_id),
        run_manager=_run_manager(client),
    )
    assert result is not None

    async with _db.async_session_maker() as session:
        conv = await session.get(Conversation, result.conversation_id)
        assert conv is not None
        assert conv.topic_id is None


async def _fetch_task(task_id: str) -> ScheduledTask:
    async with _db.async_session_maker() as session:
        task = await session.get(ScheduledTask, task_id)
    assert task is not None
    return task


# ---------------------------------------------------------------------------
# Schedule tests — REST validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_rejects_im_channel_with_topic(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: API permits an im_channel schedule that also names a
    topic_id. That combination is structurally meaningless (the conv is
    minted by the IM resolver, not by topic-aware repository.create) and
    indicates client confusion that we should refuse loudly."""
    client, ws_id = cleanup_destinations
    topic_id = await _create_topic(client, ws_id, "im+topic")
    account_id = await _seed_im_account(client, ws_id, "imtopic")
    r = await client.post(
        f"/api/v1/ws/{ws_id}/scheduled-tasks",
        json={
            "name": "bad",
            "prompt": "hi",
            "schedule_kind": "interval",
            "interval_seconds": 3600,
            "target_mode": "im_channel",
            "topic_id": topic_id,
            "im_account_id": account_id,
            "im_channel_id": "C-x",
            "im_scope_key": "dm",
            "im_scope_kind": "dm",
        },
    )
    assert r.status_code == 422
    body = r.text.lower()
    assert "topic_id" in body
    assert "im_channel" in body


@pytest.mark.asyncio
async def test_validation_rejects_fixed_without_conversation(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: API accepts target_mode='fixed' without
    target_conversation_id; the dispatcher would then raise
    TargetUnavailableError every fire."""
    client, ws_id = cleanup_destinations
    r = await client.post(
        f"/api/v1/ws/{ws_id}/scheduled-tasks",
        json={
            "name": "missing-conv",
            "prompt": "hi",
            "schedule_kind": "interval",
            "interval_seconds": 3600,
            "target_mode": "fixed",
        },
    )
    assert r.status_code == 422
    assert "target_conversation_id" in r.text


# ---------------------------------------------------------------------------
# Schedule tests — list filters + idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_filter_by_topic_id(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: list endpoint ignores ?topic_id= filter and returns
    every schedule in the workspace — UI's "schedules under this topic"
    sidebar would mislead the user."""
    client, ws_id = cleanup_destinations
    user_id = await _get_my_user_id(client)
    org_id = await _resolve_org_id(ws_id)

    topic_a = await _create_topic(client, ws_id, "list-filter-a")
    topic_b = await _create_topic(client, ws_id, "list-filter-b")
    task_a = await _create_schedule_row(
        org_id=org_id,
        ws_id=ws_id,
        owner_user_id=user_id,
        target_mode="new_each_run",
        topic_id=topic_a,
        name="task-a",
    )
    task_b = await _create_schedule_row(
        org_id=org_id,
        ws_id=ws_id,
        owner_user_id=user_id,
        target_mode="new_each_run",
        topic_id=topic_b,
        name="task-b",
    )

    r = await client.get(
        f"/api/v1/ws/{ws_id}/scheduled-tasks",
        params={"topic_id": topic_a},
    )
    assert r.status_code == 200, r.text
    ids = {t["id"] for t in r.json()["tasks"]}
    assert task_a in ids
    assert task_b not in ids


@pytest.mark.asyncio
async def test_list_filter_by_im_account_and_channel(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: list endpoint ignores im_* filters; the UI's "what
    schedules talk into this channel" view shows the entire workspace."""
    client, ws_id = cleanup_destinations
    user_id = await _get_my_user_id(client)
    org_id = await _resolve_org_id(ws_id)

    account_id = await _seed_im_account(client, ws_id, "filter-acct")
    task_a = await _create_schedule_row(
        org_id=org_id,
        ws_id=ws_id,
        owner_user_id=user_id,
        target_mode="im_channel",
        im_account_id=account_id,
        im_channel_id="C-aaa",
        im_scope_key="ch",
        im_scope_kind="channel",
        name="ch-a",
    )
    task_b = await _create_schedule_row(
        org_id=org_id,
        ws_id=ws_id,
        owner_user_id=user_id,
        target_mode="im_channel",
        im_account_id=account_id,
        im_channel_id="C-bbb",
        im_scope_key="ch",
        im_scope_kind="channel",
        name="ch-b",
    )

    r = await client.get(
        f"/api/v1/ws/{ws_id}/scheduled-tasks",
        params={"im_account_id": account_id, "im_channel_id": "C-aaa"},
    )
    assert r.status_code == 200, r.text
    ids = {t["id"] for t in r.json()["tasks"]}
    assert task_a in ids
    assert task_b not in ids


@pytest.mark.asyncio
async def test_idempotent_dispatch_on_retry(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: a poller crash between enqueue and terminal-state
    commit produces a second receipt + queue item on retry. The receipt
    short-circuit in enqueue_im_channel_run protects against this — the
    test asserts exactly one receipt + one queue row persist across two
    dispatches of the same run row."""
    client, ws_id = cleanup_destinations
    user_id = await _get_my_user_id(client)
    org_id = await _resolve_org_id(ws_id)

    account_id = await _seed_im_account(client, ws_id, "idem-acct")
    channel_id = "C-idem"
    scope_key = "dm"
    scope_kind = "dm"

    task_id = await _create_schedule_row(
        org_id=org_id,
        ws_id=ws_id,
        owner_user_id=user_id,
        target_mode="im_channel",
        im_account_id=account_id,
        im_channel_id=channel_id,
        im_scope_key=scope_key,
        im_scope_kind=scope_kind,
    )
    run_row_id = await _claim_one_run(task_id=task_id, org_id=org_id, ws_id=ws_id)

    rm = _run_manager(client)
    await _dispatch_via_session(
        task_id=task_id,
        run_row_id=run_row_id,
        run_manager=rm,
    )

    # Simulate a crash-and-redispatch: flip the run back to ``claimed``
    # without touching the receipt + queue rows the prior dispatch wrote.
    async with _db.async_session_maker() as session:
        row = await session.get(ScheduledTaskRun, run_row_id)
        assert row is not None
        row.state = "claimed"
        row.detail = None
        await session.commit()

    await _dispatch_via_session(
        task_id=task_id,
        run_row_id=run_row_id,
        run_manager=rm,
    )

    async with _db.async_session_maker() as session:
        receipts = (
            (
                await session.execute(
                    select(IMWebhookReceipt).where(
                        IMWebhookReceipt.account_id == account_id,  # type: ignore[arg-type]
                        IMWebhookReceipt.platform_event_id == f"schedule:{run_row_id}",  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(receipts) == 1
        queue_items = (
            (
                await session.execute(
                    select(IMRunQueueItem).where(
                        IMRunQueueItem.account_id == account_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(queue_items) == 1
        # Final terminal state still succeeded.
        row = await session.get(ScheduledTaskRun, run_row_id)
        assert row is not None
        assert row.state == "succeeded"


# ---------------------------------------------------------------------------
# Cross-workspace FK isolation (R4 hardening)
# ---------------------------------------------------------------------------


async def _seed_topic_in_other_workspace(*, org_id: str, creator_user_id: str) -> tuple[str, str]:
    """Mint a second workspace in the same org and seed a topic there.

    Returns ``(other_workspace_id, other_topic_id)``. The caller's identity
    is irrelevant for *what we're testing* (the cross-workspace FK guard),
    but the topic still needs a real ``creator_user_id`` — Topic has an FK
    to ``users.id``. We reuse the test's logged-in user id since users are
    org-scoped only via memberships, not by workspace.
    """
    from cubebox.models import Workspace as WorkspaceModel
    from cubebox.models.topic import Topic

    async with _db.async_session_maker() as session:
        other_ws = WorkspaceModel(org_id=org_id, name=f"other-{secrets.token_hex(4)}")
        session.add(other_ws)
        await session.flush()
        topic = Topic(
            org_id=org_id,
            workspace_id=other_ws.id,
            title="topic-in-other-ws",
            creator_user_id=creator_user_id,
        )
        session.add(topic)
        await session.commit()
        await session.refresh(other_ws)
        await session.refresh(topic)
        return other_ws.id, topic.id


async def _seed_im_account_in_other_workspace(
    *, org_id: str, owner_user_id: str
) -> tuple[str, str]:
    """Seed an IM account in a second workspace under the same org.

    Returns ``(other_workspace_id, im_account_id)``.
    """
    from cubebox.models import Workspace as WorkspaceModel

    cred_id = generate_public_id("cred")
    async with _db.async_session_maker() as session:
        other_ws = WorkspaceModel(org_id=org_id, name=f"other-{secrets.token_hex(4)}")
        session.add(other_ws)
        await session.flush()
        await session.execute(
            text(
                "INSERT INTO credentials (id, org_id, kind, name, value_encrypted,"
                " cred_metadata, created_by_user_id, created_at, updated_at)"
                " VALUES (:id, :org, 'im_bot', :name, '\\x00'::bytea,"
                " '{}'::jsonb, :uid, NOW(), NOW())"
            ),
            {
                "id": cred_id,
                "org": org_id,
                "name": f"im-account:xws:{cred_id}",
                "uid": owner_user_id,
            },
        )
        account = IMConnectorAccount(
            org_id=org_id,
            workspace_id=other_ws.id,
            platform="feishu",
            external_account_id=f"xws-{secrets.token_hex(4)}",
            acting_user_id=owner_user_id,
            credential_id=cred_id,
        )
        session.add(account)
        await session.commit()
        await session.refresh(account)
        return other_ws.id, account.id


@pytest.mark.asyncio
async def test_create_rejects_topic_from_other_workspace(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: service layer accepts a topic_id from another
    workspace because FK only checks existence — workspace A admin could
    POST a schedule with workspace B's topic_id and the dispatcher would
    create runs under B's topic."""
    client, ws_id = cleanup_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)
    _other_ws, other_topic_id = await _seed_topic_in_other_workspace(
        org_id=org_id, creator_user_id=user_id
    )
    r = await client.post(
        f"/api/v1/ws/{ws_id}/scheduled-tasks",
        json={
            "name": "x-ws-topic",
            "prompt": "p",
            "schedule_kind": "interval",
            "interval_seconds": 3600,
            "target_mode": "new_each_run",
            "topic_id": other_topic_id,
        },
    )
    assert r.status_code == 422, r.text
    assert "topic_id" in r.text


@pytest.mark.asyncio
async def test_create_rejects_im_account_from_other_workspace(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: service accepts im_account_id from another workspace.
    Workspace A admin posts a schedule that references workspace B's account;
    every fire dispatches into B's IM channel."""
    client, ws_id = cleanup_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)
    _other_ws, foreign_account = await _seed_im_account_in_other_workspace(
        org_id=org_id, owner_user_id=user_id
    )
    r = await client.post(
        f"/api/v1/ws/{ws_id}/scheduled-tasks",
        json={
            "name": "x-ws-im",
            "prompt": "p",
            "schedule_kind": "interval",
            "interval_seconds": 3600,
            "target_mode": "im_channel",
            "im_account_id": foreign_account,
            "im_channel_id": "C-x",
            "im_scope_key": "dm",
            "im_scope_kind": "dm",
        },
    )
    assert r.status_code == 422, r.text
    assert "im_account_id" in r.text


@pytest.mark.asyncio
async def test_update_rejects_destination_field_changes(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: ScheduledTaskService.update silently writes
    destination fields, letting an agent path mutate target_mode after the
    HTTP layer's lock — would either skew dispatch or trip the DB CHECK
    constraint and 500."""
    from cubebox.agents.actions.context import ScopeContext
    from cubebox.agents.actions.types import ActionInvalidInput
    from cubebox.models import Role
    from cubebox.services.scheduled_task import ScheduledTaskService

    client, ws_id = cleanup_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)
    task_id = await _create_schedule_row(
        org_id=org_id,
        ws_id=ws_id,
        owner_user_id=user_id,
        target_mode="new_each_run",
        name="immutable",
    )
    ctx = ScopeContext(
        org_id=org_id,
        workspace_id=ws_id,
        user_id=user_id,
        role=Role.ADMIN,
        conversation_id=None,
    )
    svc = ScheduledTaskService()
    forbidden_payloads: list[dict[str, Any]] = [
        {"target_mode": "fixed"},
        {"target_conversation_id": "conv-foo"},
        {"im_account_id": "imacct-foo"},
        {"im_channel_id": "C-foo"},
        {"im_scope_key": "dm"},
        {"im_scope_kind": "dm"},
    ]
    for payload in forbidden_payloads:
        async with _db.async_session_maker() as session:
            with pytest.raises(ActionInvalidInput, match="destination"):
                await svc.update(ctx, session, task_id, payload)


@pytest.mark.asyncio
async def test_update_rejects_topic_id_from_other_workspace(
    cleanup_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: service.update lets the caller patch topic_id with a
    topic that lives in another workspace; the FK passes but dispatch
    routes new conversations under a foreign workspace's topic."""
    from cubebox.agents.actions.context import ScopeContext
    from cubebox.agents.actions.types import ActionInvalidInput
    from cubebox.models import Role
    from cubebox.services.scheduled_task import ScheduledTaskService

    client, ws_id = cleanup_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)
    task_id = await _create_schedule_row(
        org_id=org_id,
        ws_id=ws_id,
        owner_user_id=user_id,
        target_mode="new_each_run",
        name="x-ws-topic-patch",
    )
    _other_ws, foreign_topic = await _seed_topic_in_other_workspace(
        org_id=org_id, creator_user_id=user_id
    )
    ctx = ScopeContext(
        org_id=org_id,
        workspace_id=ws_id,
        user_id=user_id,
        role=Role.ADMIN,
        conversation_id=None,
    )
    svc = ScheduledTaskService()
    async with _db.async_session_maker() as session:
        with pytest.raises(ActionInvalidInput, match="topic_id"):
            await svc.update(ctx, session, task_id, {"topic_id": foreign_topic})
