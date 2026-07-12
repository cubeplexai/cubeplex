"""E2E tests for Task 7: distribute / purge / lazy-enable / bootstrap-filter.

Tests run directly against the DB (no HTTP server); reuse the seeding
helpers from test_mcp_template_repositories.py.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url
from cubebox.models import MCPConnectorTemplate, Organization, User, Workspace
from cubebox.repositories.mcp import (
    MCPConnectorRepository,
    MCPCredentialGrantRepository,
    MCPTemplateSettingsRepository,
    MCPWorkspaceConnectorStateRepository,
)
from cubebox.repositories.workspace import WorkspaceRepository
from cubebox.services.mcp_installs import MCPConnectorService

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixtures / seeders
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Direct async_sessionmaker for DB-state assertions (NullPool)."""
    eng = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    finally:
        await eng.dispose()


async def _seed_user(session: AsyncSession, suffix: str) -> User:
    token = secrets.token_hex(4)
    user = User(
        email=f"dist-test-{suffix}-{token}@example.com",
        hashed_password="$2b$12$notarealhash",
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def _seed_org(session: AsyncSession, suffix: str) -> Organization:
    token = secrets.token_hex(3)
    org = Organization(
        name=f"Dist Test Org {suffix}-{token}", slug=f"dist-test-org-{suffix}-{token}"
    )
    session.add(org)
    await session.flush()
    await session.refresh(org)
    return org


async def _seed_workspace(session: AsyncSession, org_id: str, suffix: str) -> Workspace:
    ws = Workspace(org_id=org_id, name=f"Dist Test WS {suffix}")
    session.add(ws)
    await session.flush()
    await session.refresh(ws)
    return ws


def _global_template(**kwargs: object) -> MCPConnectorTemplate:
    return MCPConnectorTemplate(
        slug=f"global-dist-{secrets.token_hex(5)}",
        name=f"Dist Tool {secrets.token_hex(3)}",
        description="",
        provider="test",
        server_url=f"https://dist-{secrets.token_hex(4)}.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["none"],
        default_credential_policy="none",
        scope="global",
        status="active",
        **kwargs,  # type: ignore[arg-type]
    )


def _make_service(
    session: AsyncSession,
    org_id: str,
    actor_user_id: str,
) -> MCPConnectorService:
    """Build an MCPConnectorService without workspace_repo (for non-distribute tests)."""
    return MCPConnectorService(
        state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=org_id),
        grant_repo=MCPCredentialGrantRepository(session, org_id=org_id),
        cred_service=AsyncMock(),
        org_id=org_id,
        actor_user_id=actor_user_id,
        connector_repo=MCPConnectorRepository(session, org_id=org_id),
    )


def _make_service_with_ws_repo(
    session: AsyncSession,
    org_id: str,
    actor_user_id: str,
) -> MCPConnectorService:
    """Build an MCPConnectorService that includes a WorkspaceRepository."""
    return MCPConnectorService(
        state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=org_id),
        grant_repo=MCPCredentialGrantRepository(session, org_id=org_id),
        cred_service=AsyncMock(),
        org_id=org_id,
        actor_user_id=actor_user_id,
        workspace_repo=WorkspaceRepository(session),
        connector_repo=MCPConnectorRepository(session, org_id=org_id),
    )


# ---------------------------------------------------------------------------
# Test 1: distribute inserts only missing state rows
# ---------------------------------------------------------------------------


async def test_distribute_inserts_only_missing_state_rows(
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """ws1 has an explicit enabled=False row; ws2 has none.

    After distribute(enable_existing=True, auto_enroll=True):
    - ws1 row is untouched (enabled stays False, source unchanged)
    - ws2 row is created (enabled=True, source='admin_auto')
    - connector.auto_enroll_new_workspaces is True
    """
    async with db_maker() as session:
        org = await _seed_org(session, "dist")
        user = await _seed_user(session, "dist")
        ws1 = await _seed_workspace(session, org.id, "ws1")
        ws2 = await _seed_workspace(session, org.id, "ws2")

        template = _global_template()
        session.add(template)
        await session.commit()
        await session.refresh(template)
        await session.refresh(org)
        await session.refresh(user)
        await session.refresh(ws1)
        await session.refresh(ws2)

    # Seed ws1 with an explicit enabled=False row before distribute
    async with db_maker() as session:
        state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org.id)
        connector_repo = MCPConnectorRepository(session, org_id=org.id)

        # Ensure connector exists first
        svc = _make_service_with_ws_repo(session, org.id, user.id)
        connector = await svc.ensure_connector(template)
        connector_id = connector.id

        # Manually insert ws1's explicit disabled state BEFORE distribute
        await state_repo.upsert_for_connector(
            workspace_id=ws1.id,
            connector_id=connector_id,
            enabled=False,
            credential_policy="none",
            enablement_source="workspace_manual",
            updated_by_user_id=user.id,
        )
        await session.commit()

    # Run distribute
    async with db_maker() as session:
        svc = _make_service_with_ws_repo(session, org.id, user.id)
        connector = await svc.distribute(template, enable_existing=True, auto_enroll=True)

    # Verify
    async with db_maker() as session:
        state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org.id)
        connector_repo = MCPConnectorRepository(session, org_id=org.id)

        # ws1: must be untouched — still enabled=False, source=workspace_manual
        ws1_state = await state_repo.get_by_connector(ws1.id, connector_id)
        assert ws1_state is not None, "ws1 must still have a state row"
        assert ws1_state.enabled is False, "ws1 must remain disabled"
        assert ws1_state.enablement_source == "workspace_manual", "ws1 source must be unchanged"

        # ws2: must be created with enabled=True, source=admin_auto
        ws2_state = await state_repo.get_by_connector(ws2.id, connector_id)
        assert ws2_state is not None, "ws2 must now have a state row"
        assert ws2_state.enabled is True, "ws2 must be enabled"
        assert ws2_state.enablement_source == "admin_auto"

        # auto_enroll_new_workspaces must be True
        saved = await connector_repo.get(connector_id)
        assert saved is not None
        assert saved.auto_enroll_new_workspaces is True


# ---------------------------------------------------------------------------
# Test 2: purge deletes connector, grants, state rows; keeps template
# ---------------------------------------------------------------------------


async def test_purge_deletes_connector_grants_states_keeps_template(
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """purge(template_id) hard-deletes the connector, all state rows, and all grants.
    The template itself is untouched. A second purge call is no-op (ValueError)."""
    async with db_maker() as session:
        org = await _seed_org(session, "purge")
        user = await _seed_user(session, "purge")
        ws = await _seed_workspace(session, org.id, "ws-purge")

        template = _global_template()
        session.add(template)
        await session.commit()
        for obj in (org, user, ws, template):
            await session.refresh(obj)

    # Create connector + state row
    async with db_maker() as session:
        svc = _make_service_with_ws_repo(session, org.id, user.id)
        connector = await svc.ensure_connector(template)
        connector_id = connector.id

        state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org.id)
        await state_repo.upsert_for_connector(
            workspace_id=ws.id,
            connector_id=connector_id,
            enabled=True,
            credential_policy="none",
            enablement_source="admin_auto",
            updated_by_user_id=user.id,
        )

    # Run purge
    async with db_maker() as session:
        svc = _make_service(session, org.id, user.id)
        await svc.purge(template.id)

    # Verify connector is gone; template survives; state rows gone
    async with db_maker() as session:
        from sqlalchemy import select

        from cubebox.models import MCPConnector, MCPConnectorTemplate, MCPWorkspaceConnectorState

        gone_connector = (
            await session.execute(
                select(MCPConnector).where(MCPConnector.id == connector_id)  # type: ignore[arg-type]
            )
        ).scalar_one_or_none()
        assert gone_connector is None, "connector row must be hard-deleted"

        still_template = (
            await session.execute(
                select(MCPConnectorTemplate).where(
                    MCPConnectorTemplate.id == template.id  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        assert still_template is not None, "template must survive purge"

        gone_states = (
            (
                await session.execute(
                    select(MCPWorkspaceConnectorState).where(
                        MCPWorkspaceConnectorState.connector_id == connector_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(gone_states) == 0, "all state rows must be deleted"

    # Second purge on same template_id -> no-op ValueError
    async with db_maker() as session:
        svc = _make_service(session, org.id, user.id)
        with pytest.raises(ValueError, match="mcp_install_not_found"):
            await svc.purge(template.id)


# ---------------------------------------------------------------------------
# Test 2b: purge is atomic — mid-purge failure leaves connector intact
# ---------------------------------------------------------------------------


async def test_purge_is_atomic_rollback_on_failure(
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """If delete_for_connector raises mid-purge the connector row must survive (rollback).

    Pre-fix code committed the state deletion inside the repo method before the grant
    repo raised, so state rows were permanently gone even though the connector survived.
    The discriminating assertion is that the state row STILL EXISTS after the failed purge.

    Seed order:
      1. create connector (via ensure_connector)
      2. seed a workspace + MCPWorkspaceConnectorState row for that connector
      3. seed an MCPCredentialGrant row for that connector
    Then patch grant_repo.delete_for_connector to raise and assert rollback.
    """
    from unittest.mock import AsyncMock

    from sqlalchemy import select

    from cubebox.models import (
        Credential,
        MCPConnector,
        MCPCredentialGrant,
        MCPWorkspaceConnectorState,
    )

    async with db_maker() as session:
        org = await _seed_org(session, "patom")
        user = await _seed_user(session, "patom")

        template = _global_template()
        session.add(template)
        await session.commit()
        for obj in (org, user, template):
            await session.refresh(obj)

    # Create connector
    async with db_maker() as session:
        svc = _make_service(session, org.id, user.id)
        connector = await svc.ensure_connector(template)
        connector_id = connector.id

    # Seed a workspace state row + a credential grant for this connector
    async with db_maker() as session:
        ws = await _seed_workspace(session, org.id, "patom-ws")

        state_row = MCPWorkspaceConnectorState(
            org_id=org.id,
            workspace_id=ws.id,
            connector_id=connector_id,
            enabled=True,
            credential_policy="none",
            enablement_source="admin_manual",
        )
        session.add(state_row)

        cred = Credential(
            org_id=org.id,
            kind="mcp_server",
            name=f"patom-cred-{connector_id}",
            value_encrypted=b"fake",
        )
        session.add(cred)
        await session.flush()

        grant_row = MCPCredentialGrant(
            org_id=org.id,
            connector_id=connector_id,
            grant_scope="workspace",
            auth_method="static",
            workspace_id=ws.id,
            credential_id=cred.id,
            created_by_user_id=user.id,
        )
        session.add(grant_row)
        await session.commit()
        state_row_id = state_row.id
        grant_row_id = grant_row.id

    # Run purge with a patched grant_repo that raises mid-way
    async with db_maker() as session:
        svc = _make_service(session, org.id, user.id)
        # Replace grant_repo.delete_for_connector with one that raises.
        # Under pre-fix code, state_repo.delete_for_connector commits inside the repo
        # method before this raises — so state rows would be gone on exit.
        svc._grant_repo.delete_for_connector = AsyncMock(
            side_effect=RuntimeError("mid-purge failure")
        )
        with pytest.raises(RuntimeError, match="mid-purge failure"):
            await svc.purge(template.id)

    # All rows must still be present — the whole purge must have rolled back atomically.
    async with db_maker() as session:
        still_there = (
            await session.execute(
                select(MCPConnector).where(MCPConnector.id == connector_id)  # type: ignore[arg-type]
            )
        ).scalar_one_or_none()
        assert still_there is not None, "connector must survive a failed purge (atomic rollback)"
        assert still_there.status == "active", "connector must still be active"

        # Discriminating assertion: state row must NOT have been deleted even though
        # state_repo.delete_for_connector runs before grant_repo raises.
        state_still_there = (
            await session.execute(
                select(MCPWorkspaceConnectorState).where(
                    MCPWorkspaceConnectorState.id == state_row_id  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        assert state_still_there is not None, (
            "state row must survive failed purge — pre-fix code committed inside the repo "
            "and would have deleted it before the rollback opportunity"
        )

        grant_still_there = (
            await session.execute(
                select(MCPCredentialGrant).where(
                    MCPCredentialGrant.id == grant_row_id  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        assert grant_still_there is not None, "grant row must survive failed purge"


# ---------------------------------------------------------------------------
# Test 2c: rollback preserves grants, states, AND credential rows when
#           connector delete fails (discriminates against the R3 bug where
#           _cred_service.delete committed mid-transaction)
# ---------------------------------------------------------------------------


async def test_purge_rollback_preserves_grants_states_and_credentials_when_connector_delete_fails(
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """If session.delete(connector) raises, all rows including Credential must survive.

    The R3 bug: _cred_service.delete() called CredentialRepository.delete() which
    called session.commit() internally, permanently deleting the Credential row before
    the overall transaction could be rolled back.  Under the fixed code, vault deletion
    runs AFTER the DB commit, so a failure in the DB transaction phase (before commit)
    leaves credentials untouched.

    Discriminating assertion: Credential row is STILL PRESENT after the failed purge.
    Under the R3 code it would be gone.
    """
    from cryptography.fernet import Fernet
    from sqlalchemy import select

    from cubebox.credentials.encryption import FernetBackend
    from cubebox.models import (
        Credential,
        MCPConnector,
        MCPCredentialGrant,
        MCPWorkspaceConnectorState,
    )
    from cubebox.repositories.credential import CredentialRepository
    from cubebox.services.credential import CredentialService

    fernet_backend = FernetBackend([Fernet.generate_key()])

    def _make_real_cred_service(session: AsyncSession) -> MCPConnectorService:
        cred_repo = CredentialRepository(session, org_id=org.id)
        cred_svc = CredentialService(
            cred_repo, fernet_backend, org_id=org.id, actor_user_id=user.id
        )
        return MCPConnectorService(
            state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=org.id),
            grant_repo=MCPCredentialGrantRepository(session, org_id=org.id),
            cred_service=cred_svc,
            org_id=org.id,
            actor_user_id=user.id,
            connector_repo=MCPConnectorRepository(session, org_id=org.id),
        )

    async with db_maker() as session:
        org = await _seed_org(session, "crd-rb")
        user = await _seed_user(session, "crd-rb")

        template = _global_template()
        session.add(template)
        await session.commit()
        for obj in (org, user, template):
            await session.refresh(obj)

    # Create connector
    async with db_maker() as session:
        svc = _make_real_cred_service(session)
        connector = await svc.ensure_connector(template)
        connector_id = connector.id

    # Seed workspace, state row, credential, and grant
    async with db_maker() as session:
        ws = await _seed_workspace(session, org.id, "crd-rb-ws")

        state_row = MCPWorkspaceConnectorState(
            org_id=org.id,
            workspace_id=ws.id,
            connector_id=connector_id,
            enabled=True,
            credential_policy="none",
            enablement_source="admin_manual",
        )
        session.add(state_row)

        cred = Credential(
            org_id=org.id,
            kind="mcp_server",
            name=f"crd-rb-cred-{connector_id}",
            value_encrypted=b"fake-encrypted",
        )
        session.add(cred)
        await session.flush()

        grant_row = MCPCredentialGrant(
            org_id=org.id,
            connector_id=connector_id,
            grant_scope="workspace",
            auth_method="static",
            workspace_id=ws.id,
            credential_id=cred.id,
            created_by_user_id=user.id,
        )
        session.add(grant_row)
        await session.commit()
        state_row_id = state_row.id
        grant_row_id = grant_row.id
        credential_id = cred.id

    # Run purge but make session.delete(connector) raise before the commit.
    # Under the R3 code, _cred_service.delete() would have already committed its
    # own deletion of the Credential row by this point.  Under the fixed code,
    # vault deletion only runs AFTER a successful DB commit — so nothing has been
    # deleted yet when session.delete raises.
    async with db_maker() as session:
        svc = _make_real_cred_service(session)
        original_delete = session.delete

        call_count = 0

        async def _raising_delete(obj: object) -> None:
            nonlocal call_count
            call_count += 1
            if isinstance(obj, MCPConnector):
                raise RuntimeError("injected connector delete failure")
            await original_delete(obj)

        session.delete = _raising_delete  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="injected connector delete failure"):
            await svc.purge(template.id)

    assert call_count >= 1, "session.delete must have been called at least once"

    # All rows must still be present — the DB transaction rolled back, and vault
    # deletion never ran because it is post-commit.
    async with db_maker() as session:
        still_connector = (
            await session.execute(
                select(MCPConnector).where(MCPConnector.id == connector_id)  # type: ignore[arg-type]
            )
        ).scalar_one_or_none()
        assert still_connector is not None, "connector must survive a failed purge"

        still_state = (
            await session.execute(
                select(MCPWorkspaceConnectorState).where(
                    MCPWorkspaceConnectorState.id == state_row_id  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        assert still_state is not None, "state row must survive failed purge"

        still_grant = (
            await session.execute(
                select(MCPCredentialGrant).where(
                    MCPCredentialGrant.id == grant_row_id  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        assert still_grant is not None, "grant row must survive failed purge"

        # Discriminating assertion: credential row must still exist because vault
        # deletion is post-commit; the DB transaction never committed, so no vault
        # cleanup was attempted.
        still_cred = (
            await session.execute(
                select(Credential).where(Credential.id == credential_id)  # type: ignore[arg-type]
            )
        ).scalar_one_or_none()
        assert still_cred is not None, (
            "Credential row must survive — vault deletion is post-commit; "
            "under R3 code _cred_service.delete committed mid-transaction and would "
            "have deleted this row before the rollback opportunity"
        )


# ---------------------------------------------------------------------------
# Test 3: lazy enable from template creates connector and reuses it
# ---------------------------------------------------------------------------


async def test_lazy_enable_from_template_creates_connector_and_state(
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """set_workspace_enabled on a template with no connector creates one.

    A second workspace enabling the same template reuses the same connector id.
    """
    async with db_maker() as session:
        org = await _seed_org(session, "lazy-enable")
        user = await _seed_user(session, "lazy-enable")
        ws1 = await _seed_workspace(session, org.id, "le-ws1")
        ws2 = await _seed_workspace(session, org.id, "le-ws2")

        template = _global_template()
        session.add(template)
        await session.commit()
        for obj in (org, user, ws1, ws2, template):
            await session.refresh(obj)

    # First workspace enables the template → connector is created
    async with db_maker() as session:
        svc = _make_service(session, org.id, user.id)
        state1 = await svc.set_workspace_enabled(
            template,
            workspace_id=ws1.id,
            enabled=True,
            credential_policy=None,
        )

    connector_id = state1.connector_id
    assert state1.enabled is True
    assert state1.enablement_source == "workspace_manual"

    # Second workspace enables same template → same connector id
    async with db_maker() as session:
        svc = _make_service(session, org.id, user.id)
        state2 = await svc.set_workspace_enabled(
            template,
            workspace_id=ws2.id,
            enabled=True,
            credential_policy=None,
        )

    assert state2.connector_id == connector_id, "second call must reuse the same connector"
    assert state2.workspace_id == ws2.id
    assert state2.enabled is True

    # Verify connector exists with right template_id
    async with db_maker() as session:
        connector_repo = MCPConnectorRepository(session, org_id=org.id)
        connector = await connector_repo.get(connector_id)
        assert connector is not None
        assert connector.template_id == template.id


# ---------------------------------------------------------------------------
# Test 4: bootstrap skips org-disabled templates
# ---------------------------------------------------------------------------


async def test_bootstrap_skips_org_disabled_templates(
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """enroll_workspace_in_org_wide_mcp skips connectors whose template is org-disabled.

    An auto_enroll connector whose template is disabled -> the new workspace
    gets no state row.
    """
    from cubebox.mcp.workspace_bootstrap import enroll_workspace_in_org_wide_mcp

    async with db_maker() as session:
        org = await _seed_org(session, "bootstrap")
        user = await _seed_user(session, "bootstrap")
        ws_existing = await _seed_workspace(session, org.id, "bs-existing")
        ws_new = await _seed_workspace(session, org.id, "bs-new")

        template = _global_template()
        session.add(template)
        await session.commit()
        for obj in (org, user, ws_existing, ws_new, template):
            await session.refresh(obj)

    # Create an auto-enroll connector for the template
    async with db_maker() as session:
        svc = _make_service_with_ws_repo(session, org.id, user.id)
        connector = await svc.distribute(template, enable_existing=False, auto_enroll=True)
        connector_id = connector.id

    # Disable the template for the org
    async with db_maker() as session:
        settings_repo = MCPTemplateSettingsRepository(session, org_id=org.id)
        await settings_repo.set_disabled(template.id, True, updated_by_user_id=user.id)
        await session.commit()

    # Bootstrap the new workspace — should skip the connector because template is disabled
    async with db_maker() as session:
        await enroll_workspace_in_org_wide_mcp(
            session,
            org_id=org.id,
            workspace_id=ws_new.id,
            actor_user_id=user.id,
        )

    # Verify: new workspace has no state row for the disabled-template connector
    async with db_maker() as session:
        state_repo = MCPWorkspaceConnectorStateRepository(session, org_id=org.id)
        state = await state_repo.get_by_connector(ws_new.id, connector_id)
        assert state is None, "bootstrap must not enroll workspace when template is org-disabled"


# ---------------------------------------------------------------------------
# Test 5: purge actually deletes vault credential rows (not just grants)
# ---------------------------------------------------------------------------


async def test_purge_deletes_vault_credential_rows(
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """purge() must delete the vault Credential row referenced by a static grant.

    Before the fix, _guard_references() raised CredentialInUseError because
    grants were still present when cred_service.delete() ran (the old code
    tried to delete creds before flushing the grant deletes).  The fix flushes
    grant deletes first so the guard sees no live references.

    Discriminating assertion: Credential row is GONE after purge.
    Without the reorder it would survive.
    """
    from cryptography.fernet import Fernet
    from sqlalchemy import select

    from cubebox.credentials.encryption import FernetBackend
    from cubebox.models import Credential
    from cubebox.repositories.credential import CredentialRepository
    from cubebox.services.credential import CredentialService

    async with db_maker() as session:
        org = await _seed_org(session, "cred-purge")
        user = await _seed_user(session, "cred-purge")

        template = MCPConnectorTemplate(
            slug=f"global-cred-purge-{secrets.token_hex(5)}",
            name=f"Cred Purge Tool {secrets.token_hex(3)}",
            description="",
            provider="test",
            server_url=f"https://cred-purge-{secrets.token_hex(4)}.example.com/mcp",
            transport="streamable_http",
            supported_auth_methods=["static"],
            default_credential_policy="none",
            scope="global",
            status="active",
        )
        session.add(template)
        await session.commit()
        for obj in (org, user, template):
            await session.refresh(obj)

    # Build a real CredentialService backed by a test Fernet key.
    fernet_backend = FernetBackend([Fernet.generate_key()])

    def _make_real_service(session: AsyncSession) -> MCPConnectorService:
        cred_repo = CredentialRepository(session, org_id=org.id)
        cred_svc = CredentialService(
            cred_repo, fernet_backend, org_id=org.id, actor_user_id=user.id
        )
        return MCPConnectorService(
            state_repo=MCPWorkspaceConnectorStateRepository(session, org_id=org.id),
            grant_repo=MCPCredentialGrantRepository(session, org_id=org.id),
            cred_service=cred_svc,
            org_id=org.id,
            actor_user_id=user.id,
            connector_repo=MCPConnectorRepository(session, org_id=org.id),
        )

    # Create connector then attach a static grant (which creates a Credential row).
    async with db_maker() as session:
        svc = _make_real_service(session)
        connector = await svc.ensure_connector(template)
        connector_id = connector.id

    async with db_maker() as session:
        svc = _make_real_service(session)
        grant = await svc.create_static_grant(
            connector_id=connector_id,
            grant_scope="org",
            plaintext="super-secret-token",
        )
        credential_id = grant.credential_id
        await session.commit()

    assert credential_id is not None, "grant must have a credential_id"

    # Confirm the Credential row exists before purge.
    async with db_maker() as session:
        cred_before = (
            await session.execute(
                select(Credential).where(Credential.id == credential_id)  # type: ignore[arg-type]
            )
        ).scalar_one_or_none()
        assert cred_before is not None, "credential row must exist before purge"

    # Run purge with the real CredentialService.
    async with db_maker() as session:
        svc = _make_real_service(session)
        await svc.purge(template.id)

    # Discriminating assertion: credential row must be deleted.
    async with db_maker() as session:
        cred_after = (
            await session.execute(
                select(Credential).where(Credential.id == credential_id)  # type: ignore[arg-type]
            )
        ).scalar_one_or_none()
        assert cred_after is None, (
            "vault Credential row must be hard-deleted by purge; "
            "if this fails the grants were not flushed before cred delete attempted"
        )
