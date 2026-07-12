"""E2E tests for MCP template-centric repository methods (Task 4).

Covers:
- MCPConnectorTemplateRepository: list_visible_for_org, list_visible_for_workspace,
  create_scoped, promote_to_org
- MCPTemplateSettingsRepository: get, set_disabled, disabled_template_ids
- MCPConnectorRepository: get_by_template_id, get_or_create_for_template (lazy+race-safe)

Opens AsyncSession → lives in tests/e2e/ per project rules.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url
from cubebox.models import MCPConnector, MCPConnectorTemplate, Organization, User, Workspace

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixtures
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
        email=f"repo-test-{suffix}-{token}@example.com",
        hashed_password="$2b$12$notarealhash",
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def _seed_org(session: AsyncSession, suffix: str) -> Organization:
    suffix_val = f"{suffix}-{secrets.token_hex(3)}"
    org = Organization(name=f"Repo Test Org {suffix_val}", slug=f"repo-test-org-{suffix_val}")
    session.add(org)
    await session.flush()
    await session.refresh(org)
    return org


async def _seed_workspace(session: AsyncSession, org_id: str, suffix: str) -> Workspace:
    ws = Workspace(org_id=org_id, name=f"Repo Test WS {suffix}")
    session.add(ws)
    await session.flush()
    await session.refresh(ws)
    return ws


def _global_template(**kwargs: object) -> MCPConnectorTemplate:
    return MCPConnectorTemplate(
        slug=f"global-{secrets.token_hex(4)}",
        name="Global Tool",
        description="",
        provider="test",
        server_url="https://global.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["none"],
        default_credential_policy="none",
        scope="global",
        status="active",
        **kwargs,  # type: ignore[arg-type]
    )


def _org_template(org_id: str, **kwargs: object) -> MCPConnectorTemplate:
    return MCPConnectorTemplate(
        slug=f"org-{org_id}-{secrets.token_hex(4)}",
        name=f"Org Tool {secrets.token_hex(2)}",
        description="",
        provider="custom",
        server_url=f"https://org-{org_id}.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["none"],
        default_credential_policy="none",
        scope="org",
        org_id=org_id,
        status="active",
        **kwargs,  # type: ignore[arg-type]
    )


def _workspace_template(org_id: str, workspace_id: str, **kwargs: object) -> MCPConnectorTemplate:
    return MCPConnectorTemplate(
        slug=f"ws-{workspace_id}-{secrets.token_hex(4)}",
        name=f"WS Tool {secrets.token_hex(2)}",
        description="",
        provider="custom",
        server_url=f"https://ws-{workspace_id}.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["none"],
        default_credential_policy="none",
        scope="workspace",
        org_id=org_id,
        workspace_id=workspace_id,
        status="active",
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Test 1: visibility partition
# ---------------------------------------------------------------------------


async def test_visibility_partition(
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """list_visible_for_org and list_visible_for_workspace honour scope rules."""
    from cubebox.repositories.mcp import MCPConnectorTemplateRepository

    async with db_maker() as session:
        org_a = await _seed_org(session, "vis-a")
        org_b = await _seed_org(session, "vis-b")
        ws1 = await _seed_workspace(session, org_a.id, "vis-ws1")
        ws2 = await _seed_workspace(session, org_a.id, "vis-ws2")

        t_global = _global_template()
        t_org_a = _org_template(org_a.id)
        t_ws_a1 = _workspace_template(org_a.id, ws1.id)
        t_org_b = _org_template(org_b.id)

        for t in (t_global, t_org_a, t_ws_a1, t_org_b):
            session.add(t)
        await session.commit()
        for t in (t_global, t_org_a, t_ws_a1, t_org_b):
            await session.refresh(t)

        repo = MCPConnectorTemplateRepository(session)

        org_a_visible = await repo.list_visible_for_org(org_a.id)
        org_a_ids = {r.id for r in org_a_visible}
        assert t_global.id in org_a_ids, "global must appear for org A"
        assert t_org_a.id in org_a_ids, "org-A template must appear for org A"
        assert t_ws_a1.id in org_a_ids, "ws1 template (owned by org A) must appear"
        assert t_org_b.id not in org_a_ids, "org-B template must NOT appear for org A"

        ws1_visible = await repo.list_visible_for_workspace(org_a.id, ws1.id)
        ws1_ids = {r.id for r in ws1_visible}
        assert t_global.id in ws1_ids
        assert t_org_a.id in ws1_ids
        assert t_ws_a1.id in ws1_ids
        assert t_org_b.id not in ws1_ids

        ws2_visible = await repo.list_visible_for_workspace(org_a.id, ws2.id)
        ws2_ids = {r.id for r in ws2_visible}
        assert t_global.id in ws2_ids
        assert t_org_a.id in ws2_ids
        assert t_ws_a1.id not in ws2_ids, "ws1 custom must NOT appear in ws2"
        assert t_org_b.id not in ws2_ids

        org_b_visible = await repo.list_visible_for_org(org_b.id)
        org_b_ids = {r.id for r in org_b_visible}
        assert t_global.id in org_b_ids
        assert t_org_b.id in org_b_ids
        assert t_org_a.id not in org_b_ids


# ---------------------------------------------------------------------------
# Test 2: settings upsert + disabled_template_ids
# ---------------------------------------------------------------------------


async def test_settings_upsert_and_disabled_ids(
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """MCPTemplateSettingsRepository upserts idempotently; disabled_template_ids correct."""
    from cubebox.repositories.mcp import MCPTemplateSettingsRepository

    async with db_maker() as session:
        org = await _seed_org(session, "settings")
        t = _global_template()
        session.add(t)
        await session.commit()
        await session.refresh(t)

        user_id = None  # updated_by_user_id is nullable

        settings_repo = MCPTemplateSettingsRepository(session, org_id=org.id)

        # set_disabled True twice → idempotent; only one row; disabled=True
        row1 = await settings_repo.set_disabled(t.id, True, updated_by_user_id=user_id)
        row2 = await settings_repo.set_disabled(t.id, True, updated_by_user_id=user_id)
        assert row1.id == row2.id, "upsert must return the same row id"
        assert row2.disabled is True

        disabled_ids = await settings_repo.disabled_template_ids()
        assert t.id in disabled_ids

        # set_disabled False → row survives but disabled=False; set empty
        row3 = await settings_repo.set_disabled(t.id, False, updated_by_user_id=user_id)
        assert row3.id == row1.id, "still same row"
        assert row3.disabled is False

        disabled_ids_after = await settings_repo.disabled_template_ids()
        assert t.id not in disabled_ids_after


# ---------------------------------------------------------------------------
# Test 3: lazy connector create is idempotent + race-safe (IntegrityError branch)
# ---------------------------------------------------------------------------


async def test_lazy_connector_create_is_idempotent(
    db_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_or_create_for_template is idempotent and the IntegrityError race branch is hit."""
    from cubebox.repositories.mcp import MCPConnectorRepository

    async with db_maker() as session:
        org = await _seed_org(session, "lazy")
        user = await _seed_user(session, "lazy")
        t = _global_template()
        t.server_url = "https://lazy-connector.example.com/mcp"
        session.add(t)
        await session.commit()
        await session.refresh(t)
        await session.refresh(user)

    # First call: creates connector
    async with db_maker() as session:
        repo = MCPConnectorRepository(session, org_id=org.id)
        conn1 = await repo.get_or_create_for_template(t, created_by_user_id=user.id)

    # Second call: returns same row (fast path — row already exists)
    async with db_maker() as session:
        repo = MCPConnectorRepository(session, org_id=org.id)
        conn2 = await repo.get_or_create_for_template(t, created_by_user_id=user.id)

    assert conn1.id == conn2.id, "second call must return same connector"
    assert conn1.name == t.name
    assert conn1.server_url == t.server_url
    assert conn1.transport == t.transport

    # Deterministic race-window coverage: the existing row is already committed;
    # patch get_by_template_id to return None on the first call so the code path
    # falls through to INSERT, hits the unique-index IntegrityError, rolls back,
    # and re-fetches (returning the existing winner row).
    async with db_maker() as session:
        repo_a = MCPConnectorRepository(session, org_id=org.id)
        real_get: Callable[[str], Awaitable[MCPConnector | None]] = repo_a.get_by_template_id
        calls: dict[str, int] = {"n": 0}

        async def racy_get(template_id: str) -> MCPConnector | None:
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # simulate losing the race: row exists but we don't see it yet
            return await real_get(template_id)

        monkeypatch.setattr(repo_a, "get_by_template_id", racy_get)
        row = await repo_a.get_or_create_for_template(t, created_by_user_id=user.id)

    assert row.id == conn1.id, "re-fetch after IntegrityError must return the winner row"
    assert calls["n"] == 2, "get_by_template_id must be called twice (check + re-fetch)"


# ---------------------------------------------------------------------------
# Test 4: promote workspace template to org scope
# ---------------------------------------------------------------------------


async def test_promote_to_org(
    db_maker: async_sessionmaker[AsyncSession],
) -> None:
    """promote_to_org transitions scope=workspace to scope=org, clears workspace_id."""
    from cubebox.repositories.mcp import MCPConnectorTemplateRepository

    async with db_maker() as session:
        org = await _seed_org(session, "promote")
        ws = await _seed_workspace(session, org.id, "promote-ws")

        t_ws = _workspace_template(org.id, ws.id)
        t_org_existing = _org_template(org.id)
        for t in (t_ws, t_org_existing):
            session.add(t)
        await session.commit()
        for t in (t_ws, t_org_existing):
            await session.refresh(t)

        repo = MCPConnectorTemplateRepository(session)

        # Promote workspace-scoped template → org scope
        promoted = await repo.promote_to_org(t_ws.id)
        assert promoted.scope == "org"
        assert promoted.workspace_id is None
        assert promoted.org_id == org.id

        # Promoting an org-scoped template raises ValueError
        with pytest.raises(ValueError, match="template_not_owned_by_workspace"):
            await repo.promote_to_org(t_org_existing.id)


# ---------------------------------------------------------------------------
# Test 5: create_scoped TOCTOU maps IntegrityError to ValueError
# ---------------------------------------------------------------------------


async def test_create_scoped_toctou_raises_conflict(
    db_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_scoped catches IntegrityError from a slug collision and raises ValueError.

    Simulates the TOCTOU window: get_by_slug returns None (no conflict seen) but
    the INSERT fails because a concurrent writer already committed the same slug.
    The session must remain usable after the error.
    """
    from cubebox.repositories.mcp import MCPConnectorTemplateRepository

    async with db_maker() as session:
        org = await _seed_org(session, "toctou")
        ws = await _seed_workspace(session, org.id, "toctou-ws")
        user = await _seed_user(session, "toctou")
        await session.flush()
        repo = MCPConnectorTemplateRepository(session)

        # Create the row that will occupy the slug
        existing = await repo.create_scoped(
            scope="workspace",
            org_id=org.id,
            workspace_id=ws.id,
            created_by_user_id=user.id,
            name="My Tool",
            server_url="https://toctou.example.com/mcp",
            transport="streamable_http",
            supported_auth_methods=["none"],
            default_credential_policy="none",
        )
        await session.commit()

    # Now open a fresh session and patch get_by_slug to return None once
    # (simulating the race window), so the INSERT proceeds and hits the index.
    async with db_maker() as session:
        repo = MCPConnectorTemplateRepository(session)
        real_get_by_slug = repo.get_by_slug
        calls: dict[str, int] = {"n": 0}

        async def racy_slug(slug: str) -> MCPConnectorTemplate | None:
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # simulate: check passes, but colliding row already exists
            return await real_get_by_slug(slug)

        monkeypatch.setattr(repo, "get_by_slug", racy_slug)

        with pytest.raises(ValueError, match="connector_name_conflict"):
            await repo.create_scoped(
                scope="workspace",
                org_id=org.id,
                workspace_id=ws.id,
                created_by_user_id=user.id,
                name="My Tool",  # same name → same slug → collision
                server_url="https://toctou2.example.com/mcp",
                transport="streamable_http",
                supported_auth_methods=["none"],
                default_credential_policy="none",
            )

        # Session must be usable after the rollback
        recovered = await repo.get_by_slug(existing.slug)
        assert recovered is not None, "session must be usable after IntegrityError rollback"
        assert recovered.id == existing.id
