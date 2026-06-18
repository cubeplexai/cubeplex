"""E2E tests for IMChannelBindingRepository CRUD operations.

Exercises create, get, list, update, delete, and unique-constraint
enforcement against a real Postgres test database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.repositories.im_channel_binding import IMChannelBindingRepository
from tests.e2e.conftest import _build_database_url
from tests.e2e.im_fixtures import (
    im_cleanup,
    im_seed_account,
    im_seed_org_ws_user,
    im_seed_stub_credential,
)

pytestmark = pytest.mark.asyncio

_ORG_ID = "org-icb-test-000001"
_WS_ID = "ws-icb-test-000001"
_USER_ID = "usr-icb-test-000001"
_CRED_ID = "cred-icb-test-00001"
_ACCOUNT_ID = "imac-icb-test-0001"


@pytest_asyncio.fixture
async def seeded() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Seed dependency rows, yield session maker, clean up on exit."""
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            await im_seed_org_ws_user(session, org_id=_ORG_ID, ws_id=_WS_ID, user_id=_USER_ID)
            await im_seed_stub_credential(
                session, credential_id=_CRED_ID, org_id=_ORG_ID, user_id=_USER_ID
            )
            await im_seed_account(
                session,
                account_id=_ACCOUNT_ID,
                org_id=_ORG_ID,
                ws_id=_WS_ID,
                user_id=_USER_ID,
                credential_id=_CRED_ID,
                external_account_id="cli_icb_test",
            )
            await session.commit()

        try:
            yield maker
        finally:
            async with maker() as session:
                # Clean bindings first (child of account via FK).
                await session.execute(
                    text("DELETE FROM im_channel_bindings WHERE account_id = :id"),
                    {"id": _ACCOUNT_ID},
                )
                await im_cleanup(
                    session,
                    account_ids=[_ACCOUNT_ID],
                    credential_ids=[_CRED_ID],
                    ws_ids=[_WS_ID],
                    user_ids=[_USER_ID],
                    org_ids=[_ORG_ID],
                )
                await session.commit()
    finally:
        await engine.dispose()


async def test_create_and_get_binding(seeded: async_sessionmaker[AsyncSession]) -> None:
    """Create a shared binding, verify fields, fetch by account+channel."""
    async with seeded() as session:
        repo = IMChannelBindingRepository(session, org_id=_ORG_ID, workspace_id=_WS_ID)
        binding = await repo.create(
            account_id=_ACCOUNT_ID,
            channel_id="oc_grp_1",
            channel_name="Test Group",
            mode="shared",
            sandbox_mode="shared",
        )
        await session.commit()

        assert binding.id.startswith("icb-")
        assert binding.account_id == _ACCOUNT_ID
        assert binding.channel_id == "oc_grp_1"
        assert binding.channel_name == "Test Group"
        assert binding.mode == "shared"
        assert binding.sandbox_mode == "shared"
        assert binding.org_id == _ORG_ID
        assert binding.workspace_id == _WS_ID

        fetched = await repo.get_by_account_channel(account_id=_ACCOUNT_ID, channel_id="oc_grp_1")
        assert fetched is not None
        assert fetched.id == binding.id


async def test_list_by_account(seeded: async_sessionmaker[AsyncSession]) -> None:
    """Create 2 bindings, list them — newest first."""
    async with seeded() as session:
        repo = IMChannelBindingRepository(session, org_id=_ORG_ID, workspace_id=_WS_ID)
        b1 = await repo.create(account_id=_ACCOUNT_ID, channel_id="oc_list_1", mode="isolated")
        b2 = await repo.create(account_id=_ACCOUNT_ID, channel_id="oc_list_2", mode="shared")
        await session.commit()

        items = await repo.list_by_account(account_id=_ACCOUNT_ID)
        ids = [b.id for b in items]
        assert b1.id in ids
        assert b2.id in ids
        assert len(items) >= 2
        # Newest first: b2 was created after b1.
        assert ids.index(b2.id) < ids.index(b1.id)


async def test_update_mode(seeded: async_sessionmaker[AsyncSession]) -> None:
    """Create shared, update to isolated, verify."""
    async with seeded() as session:
        repo = IMChannelBindingRepository(session, org_id=_ORG_ID, workspace_id=_WS_ID)
        binding = await repo.create(
            account_id=_ACCOUNT_ID,
            channel_id="oc_upd_1",
            mode="shared",
            sandbox_mode="shared",
        )
        await session.commit()

        updated = await repo.update(
            binding_id=binding.id,
            mode="isolated",
            sandbox_mode=None,
            channel_name="Renamed",
        )
        await session.commit()

        assert updated is not None
        assert updated.mode == "isolated"
        assert updated.sandbox_mode is None
        assert updated.channel_name == "Renamed"


async def test_delete_binding(seeded: async_sessionmaker[AsyncSession]) -> None:
    """Create, delete, verify gone."""
    async with seeded() as session:
        repo = IMChannelBindingRepository(session, org_id=_ORG_ID, workspace_id=_WS_ID)
        binding = await repo.create(account_id=_ACCOUNT_ID, channel_id="oc_del_1")
        await session.commit()

        ok = await repo.delete(binding.id)
        await session.commit()
        assert ok is True

        gone = await repo.get_by_account_channel(account_id=_ACCOUNT_ID, channel_id="oc_del_1")
        assert gone is None

        # Second delete returns False.
        ok2 = await repo.delete(binding.id)
        assert ok2 is False


async def test_unique_constraint(seeded: async_sessionmaker[AsyncSession]) -> None:
    """Duplicate (account_id, channel_id) raises ValueError."""
    async with seeded() as session:
        repo = IMChannelBindingRepository(session, org_id=_ORG_ID, workspace_id=_WS_ID)
        await repo.create(account_id=_ACCOUNT_ID, channel_id="oc_dup_1", mode="shared")
        await session.commit()

    async with seeded() as session:
        repo = IMChannelBindingRepository(session, org_id=_ORG_ID, workspace_id=_WS_ID)
        with pytest.raises(ValueError, match="already bound"):
            await repo.create(account_id=_ACCOUNT_ID, channel_id="oc_dup_1", mode="isolated")
