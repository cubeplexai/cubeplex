"""E2E: TriggerPipeline.fire — happy path, membership lost, managed_agent target."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import cubeplex.db as _cubeplex_db
from cubeplex.api.app import create_app
from cubeplex.db.engine import _build_database_url
from cubeplex.models import Membership, Role, User
from cubeplex.models.credential import Credential
from cubeplex.models.trigger import Trigger, TriggerEvent
from cubeplex.repositories import (
    MembershipRepository,
    OrganizationRepository,
    TriggerEventRepository,
    TriggerRepository,
    WorkspaceRepository,
)
from cubeplex.triggers.events import NormalizedEvent
from cubeplex.triggers.pipeline import TriggerPipeline

pytestmark = pytest.mark.e2e


def _uid(prefix: str = "u") -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


def _make_app() -> tuple[FastAPI, async_sessionmaker[AsyncSession]]:
    """Build a test FastAPI app with NullPool and return (app, session_maker)."""
    url = _build_database_url()
    test_engine = create_async_engine(url, poolclass=NullPool)
    session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    _cubeplex_db.async_session_maker = session_maker

    from collections.abc import AsyncIterator

    from cubeplex.db.session import get_session

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    app = create_app(sandbox_factory=None)
    app.dependency_overrides[get_session] = override_get_session
    app.state.deployment_mode = "multi_tenant"
    return app, session_maker


async def _seed_context(
    session_maker: async_sessionmaker[AsyncSession],
) -> tuple[str, str, str, str, str]:
    """Create org+ws+user+membership+credential. Return (org_id, ws_id, user_id, cred_id)."""
    async with session_maker() as session:
        org = await OrganizationRepository(session).create(
            name=f"PipelineOrg-{secrets.token_hex(4)}",
            slug=f"pipe-{secrets.token_hex(4)}",
        )
        ws = await WorkspaceRepository(session).create(org_id=org.id, name="PipelineWS")

        user = User(id=_uid(), email=f"{secrets.token_hex(4)}@pipe.test", hashed_password="x")
        session.add(user)
        await session.flush()

        cred = Credential(
            id=_uid("cred"),
            org_id=org.id,
            kind="webhook_secret",
            name="pipe-secret",
            value_encrypted=b"s3cr3t",
        )
        session.add(cred)
        await session.commit()

        await MembershipRepository(session).grant(
            user_id=user.id, workspace_id=ws.id, role=Role.ADMIN
        )
        return org.id, ws.id, user.id, cred.id


async def _seed_trigger(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    org_id: str,
    ws_id: str,
    user_id: str,
    cred_id: str,
    target_type: str = "inline",
) -> Trigger:
    async with session_maker() as session:
        repo = TriggerRepository(session, org_id=org_id, workspace_id=ws_id)
        return await repo.add(
            Trigger(
                name="pipe-trigger",
                source_type="webhook",
                target_type=target_type,
                target_ref={"prompt_template": "Hello from {{ event.action }}"},
                payload_fields=["event.action"],
                run_as_user_id=user_id,
                current_secret_cred_id=cred_id,
            )
        )


async def _seed_event_row(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    trigger: Trigger,
    dedup_key: str,
) -> TriggerEvent:
    async with session_maker() as session:
        repo = TriggerEventRepository(
            session, org_id=trigger.org_id, workspace_id=trigger.workspace_id
        )
        inserted = await repo.insert_dedup(
            TriggerEvent(
                trigger_id=trigger.id,
                source_type="webhook",
                dedup_key=dedup_key,
                status="accepted",
            )
        )
        assert inserted is not None, "dedup insert failed unexpectedly"
        return inserted


def _make_event(trigger: Trigger, dedup_key: str) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=dedup_key,
        source_type="webhook",
        trigger_id=trigger.id,
        event_type=None,
        occurred_at=None,
        subject=None,
        payload={"event": {"action": "opened"}},
        dedup_key=dedup_key,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pipeline_app():  # type: ignore[no-untyped-def]
    """Yield (TriggerPipeline, session_maker, app) with lifespan managed."""
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    app, session_maker = _make_app()

    @asynccontextmanager
    async def _lifespan(a: FastAPI) -> AsyncIterator[None]:
        async with a.router.lifespan_context(a):  # type: ignore[attr-defined]
            yield

    async with _lifespan(app):
        run_manager = app.state.run_manager
        pipeline = TriggerPipeline(run_manager=run_manager, session_maker=session_maker)
        yield pipeline, session_maker, app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_inline_new_each_time(pipeline_app):  # type: ignore[no-untyped-def]
    """fire() on a valid inline trigger creates a conversation and marks accepted."""
    pipeline, session_maker, app = pipeline_app

    org_id, ws_id, user_id, cred_id = await _seed_context(session_maker)
    trigger = await _seed_trigger(
        session_maker, org_id=org_id, ws_id=ws_id, user_id=user_id, cred_id=cred_id
    )
    dedup_key = secrets.token_hex(8)
    event_row = await _seed_event_row(session_maker, trigger=trigger, dedup_key=dedup_key)
    event = _make_event(trigger, dedup_key)

    await pipeline.fire(trigger, event, event_row.id)

    # Reload the event row and assert terminal state.
    async with session_maker() as session:
        repo = TriggerEventRepository(session, org_id=org_id, workspace_id=ws_id)
        updated = await repo.get(event_row.id)
        assert updated is not None
        assert updated.status == "accepted"
        assert updated.resulting_run_id is not None
        assert updated.resulting_conversation_id is not None

        # Conversation must exist.
        from cubeplex.models import Conversation

        conv = await session.get(Conversation, updated.resulting_conversation_id)
        assert conv is not None
        assert conv.creator_user_id == user_id

        # Counter increments.
        from sqlalchemy import select

        trig_row = (
            await session.execute(select(Trigger).where(Trigger.id == trigger.id))
        ).scalar_one()
        assert trig_row.events_total == 1
        assert trig_row.events_success == 1
        assert trig_row.events_failed == 0


@pytest.mark.asyncio
async def test_membership_lost_disables_trigger(pipeline_app):  # type: ignore[no-untyped-def]
    """fire() when run_as_user lost membership disables trigger and records failed."""
    pipeline, session_maker, app = pipeline_app

    org_id, ws_id, user_id, cred_id = await _seed_context(session_maker)
    trigger = await _seed_trigger(
        session_maker, org_id=org_id, ws_id=ws_id, user_id=user_id, cred_id=cred_id
    )

    # Revoke membership so fire() finds role=None.
    async with session_maker() as session:
        from sqlalchemy import delete

        await session.execute(
            delete(Membership).where(
                Membership.user_id == user_id,  # type: ignore[arg-type]
                Membership.workspace_id == ws_id,  # type: ignore[arg-type]
            )
        )
        await session.commit()

    dedup_key = secrets.token_hex(8)
    event_row = await _seed_event_row(session_maker, trigger=trigger, dedup_key=dedup_key)
    event = _make_event(trigger, dedup_key)

    await pipeline.fire(trigger, event, event_row.id)

    async with session_maker() as session:
        # Trigger must be disabled.
        from sqlalchemy import select

        trig_row = (
            await session.execute(select(Trigger).where(Trigger.id == trigger.id))
        ).scalar_one()
        assert trig_row.enabled is False
        assert trig_row.events_total == 1
        assert trig_row.events_failed == 1
        assert trig_row.events_success == 0

        # Event row must be failed with "membership" in last_error.
        repo = TriggerEventRepository(session, org_id=org_id, workspace_id=ws_id)
        updated = await repo.get(event_row.id)
        assert updated is not None
        assert updated.status == "failed"
        assert updated.last_error is not None
        assert "membership" in updated.last_error


@pytest.mark.asyncio
async def test_managed_agent_target_records_failed(pipeline_app):  # type: ignore[no-untyped-def]
    """fire() with target_type=managed_agent records failed without raising."""
    pipeline, session_maker, app = pipeline_app

    org_id, ws_id, user_id, cred_id = await _seed_context(session_maker)
    trigger = await _seed_trigger(
        session_maker,
        org_id=org_id,
        ws_id=ws_id,
        user_id=user_id,
        cred_id=cred_id,
        target_type="managed_agent",
    )
    dedup_key = secrets.token_hex(8)
    event_row = await _seed_event_row(session_maker, trigger=trigger, dedup_key=dedup_key)
    event = _make_event(trigger, dedup_key)

    # Must not raise.
    await pipeline.fire(trigger, event, event_row.id)

    async with session_maker() as session:
        repo = TriggerEventRepository(session, org_id=org_id, workspace_id=ws_id)
        updated = await repo.get(event_row.id)
        assert updated is not None
        assert updated.status == "failed"
        assert updated.last_error is not None
        assert "managed_agent" in updated.last_error

        from sqlalchemy import select

        trig_row = (
            await session.execute(select(Trigger).where(Trigger.id == trigger.id))
        ).scalar_one()
        assert trig_row.events_total == 1
        assert trig_row.events_failed == 1
        assert trig_row.events_success == 0
