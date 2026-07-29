"""Seeding helpers for billing/cost tests, shared by the core and gated modules."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.db.engine import _build_database_url
from cubeplex.models.billing import BillingEvent, LlmBillingEvent
from cubeplex.models.conversation import Conversation
from cubeplex.models.organization import Organization
from cubeplex.models.user import User
from cubeplex.models.workspace import Workspace


@asynccontextmanager
async def _db_session() -> AsyncIterator[AsyncSession]:
    """Create a direct AsyncSession to the test DB (NullPool, no connection sharing)."""
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            yield session
    finally:
        await engine.dispose()


async def _ensure_org(session: AsyncSession, org_id: str) -> None:
    existing = (
        await session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            Organization(
                id=org_id,
                name=f"Test {org_id}",
                slug=org_id.replace("_", "-").lower()[:32],
            )
        )
        await session.commit()


async def _ensure_workspace(session: AsyncSession, *, ws_id: str, org_id: str) -> None:
    existing = (
        await session.execute(select(Workspace).where(Workspace.id == ws_id))
    ).scalar_one_or_none()
    if existing is None:
        session.add(Workspace(id=ws_id, org_id=org_id, name=f"Test {ws_id}"))
        await session.commit()


async def _ensure_user(
    session: AsyncSession,
    *,
    user_id: str,
    display_name: str | None = None,
) -> None:
    existing = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if existing is None:
        session.add(
            User(
                id=user_id,
                email=f"{user_id}@test-billing-cost.local",
                hashed_password="x",
                is_active=True,
                display_name=display_name,
            )
        )
        await session.commit()
    elif display_name is not None and existing.display_name != display_name:
        existing.display_name = display_name
        session.add(existing)
        await session.commit()


async def _ensure_conversation(
    session: AsyncSession,
    *,
    conv_id: str,
    org_id: str,
    ws_id: str,
    user_id: str,
) -> None:
    existing = (
        await session.execute(select(Conversation).where(Conversation.id == conv_id))
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            Conversation(
                id=conv_id,
                org_id=org_id,
                workspace_id=ws_id,
                creator_user_id=user_id,
                title="Test conv",
            )
        )
        await session.commit()


async def _seed_events(
    session: AsyncSession,
    *,
    org_id: str,
    rows: list[dict[str, Any]],
) -> None:
    """Insert billing rows after ensuring FK parents (org/ws/user/conversation) exist.

    Deletes any prior billing rows for ``org_id`` first so reruns are idempotent
    against a shared dev/test database.
    """
    # Clean prior billing rows for this test org (child first to satisfy FK).
    prior_ids = (
        (await session.execute(select(BillingEvent.id).where(BillingEvent.org_id == org_id)))
        .scalars()
        .all()
    )
    if prior_ids:
        await session.execute(
            delete(LlmBillingEvent).where(LlmBillingEvent.billing_event_id.in_(prior_ids))
        )
        await session.execute(delete(BillingEvent).where(BillingEvent.org_id == org_id))
        await session.commit()
    await _ensure_org(session, org_id)
    # Track which parents we've ensured to keep seeding fast.
    seen_ws: set[str] = set()
    seen_users: set[str] = set()
    seen_conv: set[str] = set()
    for r in rows:
        ws_id = str(r["workspace_id"])
        user_id = str(r["user_id"])
        conv_id = str(r.get("conversation_id", f"conv-seed-{org_id[:8]}"))
        if ws_id not in seen_ws:
            await _ensure_workspace(session, ws_id=ws_id, org_id=org_id)
            seen_ws.add(ws_id)
        if user_id not in seen_users:
            await _ensure_user(
                session,
                user_id=user_id,
                display_name=r.get("user_display_name"),  # type: ignore[arg-type]
            )
            seen_users.add(user_id)
        if conv_id not in seen_conv:
            await _ensure_conversation(
                session,
                conv_id=conv_id,
                org_id=org_id,
                ws_id=ws_id,
                user_id=user_id,
            )
            seen_conv.add(conv_id)
        be = BillingEvent(
            org_id=org_id,
            workspace_id=ws_id,
            user_id=user_id,
            conversation_id=conv_id,
            event_type="llm_call",
            cost_amount_micro=int(r["cost_micro"]),
            currency="USD",
            started_at=r["started_at"],
            ended_at=r["started_at"] + timedelta(milliseconds=200),
            duration_ms=200,
            status="ok",
        )
        le = LlmBillingEvent(
            billing_event_id=be.id,
            provider=str(r["provider"]),
            model_id=str(r["model_id"]),
            input_tokens=int(r.get("input", 0)),
            output_tokens=int(r.get("output", 0)),
            cache_read_tokens=int(r.get("cache_read", 0)),
            cache_write_tokens=int(r.get("cache_write", 0)),
        )
        session.add(be)
        session.add(le)
    await session.commit()
