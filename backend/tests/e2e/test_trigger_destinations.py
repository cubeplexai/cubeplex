"""E2E: trigger destinations — topic_id wiring + im_channel pipeline branch.

Mirrors test_scheduled_task_destinations.py: each test protects one business
invariant for the trigger half of the destinations feature. The trigger
pipeline replaces ``ScheduledTask.target_mode`` with
``Trigger.conversation_policy`` and uses ``TriggerEvent.status`` /
``last_error`` to surface failures, so the assertions follow the trigger
contract rather than the schedule one.
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
from cubebox.models.credential import Credential
from cubebox.models.im_connector import (
    IMRunQueueItem,
    IMThreadLink,
    IMWebhookReceipt,
)
from cubebox.models.public_id import generate_public_id
from cubebox.models.trigger import Trigger, TriggerEvent
from cubebox.repositories import TriggerEventRepository, TriggerRepository
from cubebox.triggers.events import NormalizedEvent
from cubebox.triggers.pipeline import TriggerPipeline

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Test seed helpers
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
    """Seed one IMConnectorAccount row via raw SQL; returns the account id."""
    if external_account_id is None:
        external_account_id = f"ext-{secrets.token_hex(8)}"
    else:
        external_account_id = f"{external_account_id}-{secrets.token_hex(4)}"
    user_id = await _get_my_user_id(client)
    cred_id = generate_public_id("cred")
    async with _db.async_session_maker() as session:
        ws_row = await session.get(Workspace, ws_id)
        assert ws_row is not None
        org_id = ws_row.org_id
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


async def _set_routing_mode(*, account_id: str, mode: str) -> None:
    """Set the bot's account-level routing mode (replaces per-channel binding)."""
    from cubebox.im.bot_settings import IMBotSettings, store_bot_settings

    async with _db.async_session_maker() as session:
        account = await session.get(IMConnectorAccount, account_id)
        assert account is not None
        account.config = store_bot_settings(
            account.config, IMBotSettings(routing_mode=mode)  # type: ignore[arg-type]
        )
        session.add(account)
        await session.commit()


async def _seed_existing_link(
    *,
    org_id: str,
    ws_id: str,
    account_id: str,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    user_id: str,
    title: str = "trig-existing-conv",
) -> str:
    """Create a Conversation + IMThreadLink. Returns the conversation id."""
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
        session.add(
            IMThreadLink(
                org_id=org_id,
                workspace_id=ws_id,
                account_id=account_id,
                channel_id=channel_id,
                scope_key=scope_key,
                scope_kind=scope_kind,
                conversation_id=conv.id,
            )
        )
        await session.commit()
        await session.refresh(conv)
        return conv.id


async def _seed_trigger_row(
    *,
    org_id: str,
    ws_id: str,
    run_as_user_id: str,
    conversation_policy: str = "new_each_time",
    topic_id: str | None = None,
    im_account_id: str | None = None,
    im_channel_id: str | None = None,
    im_scope_key: str | None = None,
    im_scope_kind: str | None = None,
    name: str = "trig",
) -> Trigger:
    """Insert a Trigger row directly. Returns the Trigger object.

    Bypasses the HTTP layer so each test can declare its destination
    shape independently; the create_trigger route's validation is covered
    by test_ws_triggers.py.
    """
    cred_id = generate_public_id("cred")
    async with _db.async_session_maker() as session:
        cred = Credential(
            id=cred_id,
            org_id=org_id,
            kind="webhook_secret",
            name=f"trig-secret:{secrets.token_hex(4)}",
            value_encrypted=b"s",
        )
        session.add(cred)
        await session.flush()
        repo = TriggerRepository(session, org_id=org_id, workspace_id=ws_id)
        trigger = await repo.add(
            Trigger(
                name=name,
                source_type="webhook",
                target_type="inline",
                target_ref={"prompt_template": "hello {{ event.action }}"},
                payload_fields=["event.action"],
                run_as_user_id=run_as_user_id,
                current_secret_cred_id=cred.id,
                conversation_policy=conversation_policy,
                topic_id=topic_id,
                im_account_id=im_account_id,
                im_channel_id=im_channel_id,
                im_scope_key=im_scope_key,
                im_scope_kind=im_scope_kind,
            )
        )
        return trigger


async def _seed_event_row(
    *,
    org_id: str,
    ws_id: str,
    trigger_id: str,
    dedup_key: str | None = None,
) -> TriggerEvent:
    """Insert an accepted TriggerEvent row; returns the saved row."""
    async with _db.async_session_maker() as session:
        repo = TriggerEventRepository(session, org_id=org_id, workspace_id=ws_id)
        inserted = await repo.insert_dedup(
            TriggerEvent(
                trigger_id=trigger_id,
                source_type="webhook",
                dedup_key=dedup_key or secrets.token_hex(8),
                status="accepted",
            )
        )
        assert inserted is not None, "dedup insert failed unexpectedly"
        return inserted


def _make_event(trigger: Trigger, event_id: str, dedup_key: str) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=event_id,
        source_type="webhook",
        trigger_id=trigger.id,
        event_type=None,
        occurred_at=None,
        subject=None,
        payload={"event": {"action": "opened"}},
        dedup_key=dedup_key,
    )


def _run_manager(client: httpx.AsyncClient) -> Any:
    app = client._transport.app  # type: ignore[attr-defined]
    return app.state.run_manager


# ---------------------------------------------------------------------------
# Cleanup fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def cleanup_trigger_destinations(
    authenticated_client: tuple[httpx.AsyncClient, str],
) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    """Yields ``(client, ws_id)``; wipes destination rows on teardown."""
    client, ws_id = authenticated_client
    yield client, ws_id

    async with _db.async_session_maker() as session:
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
            delete(TriggerEvent).where(
                TriggerEvent.workspace_id == ws_id  # type: ignore[arg-type]
            )
        )
        # Null topic_id + im_account_id so the parent rows can drop.
        await session.execute(
            text(
                "UPDATE triggers SET topic_id = NULL, im_account_id = NULL WHERE workspace_id = :ws"
            ),
            {"ws": ws_id},
        )
        await session.execute(
            text("DELETE FROM triggers WHERE workspace_id = :ws"),
            {"ws": ws_id},
        )
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
        # org (see _seed_trigger_topic_in_other_workspace below). Wipe
        # those rows BEFORE the credentials sweep — IM accounts still
        # hold FKs to the credential rows.
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
        await session.execute(
            text(
                "DELETE FROM credentials WHERE org_id IN "
                "(SELECT org_id FROM workspaces WHERE id = :ws)"
                " AND kind IN ('webhook_secret', 'im_bot')"
            ),
            {"ws": ws_id},
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Trigger tests — topic_id plumbing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_new_each_time_with_topic_creates_conv_in_topic(
    cleanup_trigger_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: pipeline forgets to pass topic_id into
    ConversationRepository.create on the new_each_time path — the
    triggered conv lands at the workspace root, breaking the
    "show me triggers under topic X" sidebar grouping."""
    client, ws_id = cleanup_trigger_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)
    topic_id = await _create_topic(client, ws_id, "trig-topic")

    trigger = await _seed_trigger_row(
        org_id=org_id,
        ws_id=ws_id,
        run_as_user_id=user_id,
        conversation_policy="new_each_time",
        topic_id=topic_id,
    )
    event_row = await _seed_event_row(org_id=org_id, ws_id=ws_id, trigger_id=trigger.id)

    pipeline = TriggerPipeline(
        run_manager=_run_manager(client),
        session_maker=_db.async_session_maker,
    )
    await pipeline.fire(
        trigger, _make_event(trigger, event_row.id, event_row.dedup_key), event_row.id
    )

    async with _db.async_session_maker() as session:
        repo = TriggerEventRepository(session, org_id=org_id, workspace_id=ws_id)
        updated = await repo.get(event_row.id)
        assert updated is not None
        assert updated.status == "accepted"
        assert updated.resulting_conversation_id is not None
        conv = await session.get(Conversation, updated.resulting_conversation_id)
        assert conv is not None
        assert conv.topic_id == topic_id


# ---------------------------------------------------------------------------
# Trigger tests — im_channel branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_im_channel_reuses_existing_link(
    cleanup_trigger_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: trigger pipeline ignores live IMThreadLink and mints
    a fresh conv — the agent's IM reply lands in a conversation the user
    can't see from the channel."""
    client, ws_id = cleanup_trigger_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)

    account_id = await _seed_im_account(client, ws_id, "trig-reuse")
    channel_id = "C-trig-reuse"
    scope_key = "dm"
    scope_kind = "dm"
    existing_conv_id = await _seed_existing_link(
        org_id=org_id,
        ws_id=ws_id,
        account_id=account_id,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        user_id=user_id,
    )

    trigger = await _seed_trigger_row(
        org_id=org_id,
        ws_id=ws_id,
        run_as_user_id=user_id,
        conversation_policy="im_channel",
        im_account_id=account_id,
        im_channel_id=channel_id,
        im_scope_key=scope_key,
        im_scope_kind=scope_kind,
    )
    event_row = await _seed_event_row(org_id=org_id, ws_id=ws_id, trigger_id=trigger.id)

    pipeline = TriggerPipeline(
        run_manager=_run_manager(client),
        session_maker=_db.async_session_maker,
    )
    await pipeline.fire(
        trigger, _make_event(trigger, event_row.id, event_row.dedup_key), event_row.id
    )

    async with _db.async_session_maker() as session:
        repo = TriggerEventRepository(session, org_id=org_id, workspace_id=ws_id)
        updated = await repo.get(event_row.id)
        assert updated is not None
        assert updated.status == "accepted"
        assert updated.resulting_conversation_id == existing_conv_id

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
        assert queue_items[0].conversation_id == existing_conv_id

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
        assert receipts[0].platform_event_id == f"trigger:{event_row.id}"


@pytest.mark.asyncio
async def test_trigger_im_channel_creates_fresh_after_new(
    cleanup_trigger_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: pipeline crashes when the user `/new`'d before the
    trigger fired (no IMThreadLink); the resolver must mint a fresh
    conversation + link so the agent's reply still lands in the channel."""
    client, ws_id = cleanup_trigger_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)

    account_id = await _seed_im_account(client, ws_id, "trig-fresh")
    channel_id = "C-trig-fresh"
    scope_key = "dm"
    scope_kind = "dm"
    # No IMThreadLink — simulates the user typed /new before the trigger fires.

    trigger = await _seed_trigger_row(
        org_id=org_id,
        ws_id=ws_id,
        run_as_user_id=user_id,
        conversation_policy="im_channel",
        im_account_id=account_id,
        im_channel_id=channel_id,
        im_scope_key=scope_key,
        im_scope_kind=scope_kind,
    )
    event_row = await _seed_event_row(org_id=org_id, ws_id=ws_id, trigger_id=trigger.id)

    pipeline = TriggerPipeline(
        run_manager=_run_manager(client),
        session_maker=_db.async_session_maker,
    )
    await pipeline.fire(
        trigger, _make_event(trigger, event_row.id, event_row.dedup_key), event_row.id
    )

    async with _db.async_session_maker() as session:
        repo = TriggerEventRepository(session, org_id=org_id, workspace_id=ws_id)
        updated = await repo.get(event_row.id)
        assert updated is not None
        assert updated.status == "accepted"
        new_conv_id = updated.resulting_conversation_id
        assert new_conv_id is not None

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
        assert links[0].conversation_id == new_conv_id


@pytest.mark.asyncio
async def test_trigger_im_channel_shared_mode_lands_conv_in_topic(
    cleanup_trigger_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: trigger pipeline drops shared-mode topic linkage
    on its im_channel path — the new conv lands at the workspace root
    instead of under the channel's (auto-created) topic."""
    client, ws_id = cleanup_trigger_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)

    account_id = await _seed_im_account(client, ws_id, "trig-shared")
    channel_id = "C-trig-shared"
    scope_key = "ch"
    scope_kind = "channel"
    await _set_routing_mode(account_id=account_id, mode="shared")

    trigger = await _seed_trigger_row(
        org_id=org_id,
        ws_id=ws_id,
        run_as_user_id=user_id,
        conversation_policy="im_channel",
        im_account_id=account_id,
        im_channel_id=channel_id,
        im_scope_key=scope_key,
        im_scope_kind=scope_kind,
    )
    event_row = await _seed_event_row(org_id=org_id, ws_id=ws_id, trigger_id=trigger.id)
    pipeline = TriggerPipeline(
        run_manager=_run_manager(client),
        session_maker=_db.async_session_maker,
    )
    await pipeline.fire(
        trigger, _make_event(trigger, event_row.id, event_row.dedup_key), event_row.id
    )

    async with _db.async_session_maker() as session:
        repo = TriggerEventRepository(session, org_id=org_id, workspace_id=ws_id)
        updated = await repo.get(event_row.id)
        assert updated is not None
        assert updated.status == "accepted"
        conv = await session.get(Conversation, updated.resulting_conversation_id)
        assert conv is not None
        assert conv.topic_id is not None  # landed under the channel's topic, not root
        assert conv.is_group_chat is True

        cp = (
            await session.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conv.id,  # type: ignore[arg-type]
                    ConversationParticipant.user_id == user_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        assert cp is not None


@pytest.mark.asyncio
async def test_trigger_im_account_deletion_marks_event_failed(
    cleanup_trigger_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: pipeline crashes (assertion) when the bound IM
    account was deleted — FK ON DELETE SET NULL clears
    ``triggers.im_account_id`` to NULL. Without a NULL-aware branch
    every following event would 500 inside the pipeline."""
    client, ws_id = cleanup_trigger_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)

    account_id = await _seed_im_account(client, ws_id, "trig-doom")
    trigger = await _seed_trigger_row(
        org_id=org_id,
        ws_id=ws_id,
        run_as_user_id=user_id,
        conversation_policy="im_channel",
        im_account_id=account_id,
        im_channel_id="C-doom",
        im_scope_key="dm",
        im_scope_kind="dm",
    )
    # Delete the IM account; FK SET NULL fires on triggers.im_account_id.
    async with _db.async_session_maker() as session:
        await session.execute(
            text("DELETE FROM im_connector_accounts WHERE id = :id"),
            {"id": account_id},
        )
        await session.commit()
        # Re-read trigger so the pipeline sees the post-FK state.
        trigger = (
            await session.execute(
                select(Trigger).where(Trigger.id == trigger.id)  # type: ignore[arg-type]
            )
        ).scalar_one()
    assert trigger.im_account_id is None

    event_row = await _seed_event_row(org_id=org_id, ws_id=ws_id, trigger_id=trigger.id)
    pipeline = TriggerPipeline(
        run_manager=_run_manager(client),
        session_maker=_db.async_session_maker,
    )
    await pipeline.fire(
        trigger, _make_event(trigger, event_row.id, event_row.dedup_key), event_row.id
    )

    async with _db.async_session_maker() as session:
        repo = TriggerEventRepository(session, org_id=org_id, workspace_id=ws_id)
        updated = await repo.get(event_row.id)
        assert updated is not None
        assert updated.status == "failed"
        assert updated.last_error == "im_account_unlinked"

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


# ---------------------------------------------------------------------------
# Trigger tests — REST validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_validation_rejects_im_channel_with_topic(
    cleanup_trigger_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: API allows ``conversation_policy='im_channel'`` plus
    ``topic_id`` — a meaningless combination since the IM resolver mints
    its own conv. The schema-level rejection prevents silently-ignored
    topic_ids on im_channel triggers."""
    client, ws_id = cleanup_trigger_destinations
    user_id = await _get_my_user_id(client)
    topic_id = await _create_topic(client, ws_id, "trig-im+topic")
    account_id = await _seed_im_account(client, ws_id, "trig-imtopic")
    r = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "trig-im-topic",
            "webhook_secret": "s",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": user_id,
            "conversation_policy": "im_channel",
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


# ---------------------------------------------------------------------------
# Trigger tests — list filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_list_filter_by_topic_id(
    cleanup_trigger_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: list endpoint ignores topic_id filter and the UI's
    "triggers under this topic" view returns the workspace-wide list."""
    client, ws_id = cleanup_trigger_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)

    topic_a = await _create_topic(client, ws_id, "trig-filter-a")
    topic_b = await _create_topic(client, ws_id, "trig-filter-b")
    trig_a = await _seed_trigger_row(
        org_id=org_id,
        ws_id=ws_id,
        run_as_user_id=user_id,
        conversation_policy="new_each_time",
        topic_id=topic_a,
        name="t-a",
    )
    trig_b = await _seed_trigger_row(
        org_id=org_id,
        ws_id=ws_id,
        run_as_user_id=user_id,
        conversation_policy="new_each_time",
        topic_id=topic_b,
        name="t-b",
    )

    r = await client.get(
        f"/api/v1/ws/{ws_id}/triggers",
        params={"topic_id": topic_a},
    )
    assert r.status_code == 200, r.text
    ids = {t["id"] for t in r.json()["triggers"]}
    assert trig_a.id in ids
    assert trig_b.id not in ids


@pytest.mark.asyncio
async def test_trigger_list_filter_by_im_account_and_channel(
    cleanup_trigger_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: list endpoint ignores im_* filters; the UI's
    "triggers posting to this IM channel" view returns the entire
    workspace."""
    client, ws_id = cleanup_trigger_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)

    account_id = await _seed_im_account(client, ws_id, "trig-filter-acct")
    trig_a = await _seed_trigger_row(
        org_id=org_id,
        ws_id=ws_id,
        run_as_user_id=user_id,
        conversation_policy="im_channel",
        im_account_id=account_id,
        im_channel_id="C-aaa",
        im_scope_key="ch",
        im_scope_kind="channel",
        name="im-a",
    )
    trig_b = await _seed_trigger_row(
        org_id=org_id,
        ws_id=ws_id,
        run_as_user_id=user_id,
        conversation_policy="im_channel",
        im_account_id=account_id,
        im_channel_id="C-bbb",
        im_scope_key="ch",
        im_scope_kind="channel",
        name="im-b",
    )
    r = await client.get(
        f"/api/v1/ws/{ws_id}/triggers",
        params={"im_account_id": account_id, "im_channel_id": "C-aaa"},
    )
    assert r.status_code == 200, r.text
    ids = {t["id"] for t in r.json()["triggers"]}
    assert trig_a.id in ids
    assert trig_b.id not in ids


# ---------------------------------------------------------------------------
# Cross-workspace FK isolation (R4 hardening)
# ---------------------------------------------------------------------------


async def _seed_trigger_topic_in_other_workspace(
    *, org_id: str, creator_user_id: str
) -> tuple[str, str]:
    """Mint a sibling workspace and seed a topic there for cross-ws tests.

    Returns ``(other_workspace_id, other_topic_id)``.
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
            title="trig-other-ws-topic",
            creator_user_id=creator_user_id,
        )
        session.add(topic)
        await session.commit()
        await session.refresh(other_ws)
        await session.refresh(topic)
        return other_ws.id, topic.id


async def _seed_trigger_im_account_in_other_workspace(
    *, org_id: str, owner_user_id: str
) -> tuple[str, str]:
    """Sibling-workspace IM account; returns ``(other_ws_id, im_account_id)``."""
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
                "name": f"im-account:trigxws:{cred_id}",
                "uid": owner_user_id,
            },
        )
        account = IMConnectorAccount(
            org_id=org_id,
            workspace_id=other_ws.id,
            platform="feishu",
            external_account_id=f"trigxws-{secrets.token_hex(4)}",
            acting_user_id=owner_user_id,
            credential_id=cred_id,
        )
        session.add(account)
        await session.commit()
        await session.refresh(account)
        return other_ws.id, account.id


@pytest.mark.asyncio
async def test_trigger_create_rejects_topic_from_other_workspace(
    cleanup_trigger_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: trigger create accepts a topic_id from another
    workspace; FK passes but fired conversations land under the foreign
    workspace's topic."""
    client, ws_id = cleanup_trigger_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)
    _other_ws, foreign_topic = await _seed_trigger_topic_in_other_workspace(
        org_id=org_id, creator_user_id=user_id
    )
    r = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "x-ws-topic",
            "webhook_secret": "s",
            "prompt_template": "hi {{ event.action }}",
            "payload_fields": [],
            "run_as_user_id": user_id,
            "conversation_policy": "new_each_time",
            "topic_id": foreign_topic,
        },
    )
    assert r.status_code == 422, r.text
    assert "topic_id" in r.text


@pytest.mark.asyncio
async def test_trigger_create_rejects_im_account_from_other_workspace(
    cleanup_trigger_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: trigger create accepts a foreign workspace's
    im_account_id; FK passes but every event posts back into the wrong
    workspace's IM."""
    client, ws_id = cleanup_trigger_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)
    _other_ws, foreign_account = await _seed_trigger_im_account_in_other_workspace(
        org_id=org_id, owner_user_id=user_id
    )
    r = await client.post(
        f"/api/v1/ws/{ws_id}/triggers",
        json={
            "name": "x-ws-im",
            "webhook_secret": "s",
            "prompt_template": "hi",
            "payload_fields": [],
            "run_as_user_id": user_id,
            "conversation_policy": "im_channel",
            "im_account_id": foreign_account,
            "im_channel_id": "C-x",
            "im_scope_key": "dm",
            "im_scope_kind": "dm",
        },
    )
    assert r.status_code == 422, r.text
    assert "im_account_id" in r.text


@pytest.mark.asyncio
async def test_create_trigger_tool_ingest_path_is_workspace_scoped(
    cleanup_trigger_destinations: tuple[httpx.AsyncClient, str],
) -> None:
    """Bug it catches: create_trigger agent tool returns an unscoped
    ``/api/v1/triggers/{id}/ingest`` URL — copying that link to a webhook
    publisher yields 404, the trigger silently never fires."""
    import json

    from cubebox.models.conversation import Conversation
    from cubebox.tools.builtin.create_trigger import (
        CreateTriggerArgs,
        make_create_trigger_tool,
    )

    client, ws_id = cleanup_trigger_destinations
    org_id = await _resolve_org_id(ws_id)
    user_id = await _get_my_user_id(client)
    async with _db.async_session_maker() as session:
        conv = Conversation(
            org_id=org_id,
            workspace_id=ws_id,
            creator_user_id=user_id,
            title="ingest-path-test",
            is_group_chat=False,
        )
        session.add(conv)
        await session.commit()
        await session.refresh(conv)
        conv_id = conv.id

    app = client._transport.app  # type: ignore[attr-defined]
    backend = app.state.encryption_backend
    tool = make_create_trigger_tool(
        org_id=org_id,
        workspace_id=ws_id,
        user_id=user_id,
        conversation_id=conv_id,
        encryption_backend=backend,
    )
    result = await tool.execute(
        "test-call",
        CreateTriggerArgs(name="ingest-path", prompt_template="hi"),
    )
    assert not result.is_error, result.content[0].text  # type: ignore[union-attr]
    payload = json.loads(result.content[0].text)  # type: ignore[union-attr]
    assert payload["status"] == "created"
    assert payload["ingest_path"] == f"/api/v1/ws/{ws_id}/triggers/{payload['id']}/ingest"
