"""Unit tests for ``cubeplex admin disable-sso`` / ``list-sso`` CLI commands.

The commands wrap their logic in ``asyncio.run`` which means we must invoke
them from a sync context (otherwise pytest-asyncio's running loop trips
``RuntimeError: asyncio.run() cannot be called from a running event loop``).
Tests are therefore plain ``def`` functions that do their own
``asyncio.run`` for setup/verification and stub
``cubeplex.cli.admin.async_session_maker`` with an in-memory aiosqlite
session factory. ``StaticPool`` keeps state visible across the multiple
``async with`` blocks (CLI under test, then post-assertion verification).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from click.testing import CliRunner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubeplex.cli import admin as admin_cli
from cubeplex.models import Organization
from cubeplex.models.sso_connection import SSOConnection


@pytest.fixture
def session_maker(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[async_sessionmaker[AsyncSession]]:
    """In-memory aiosqlite session maker, patched into ``admin_cli``."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async def _setup() -> async_sessionmaker[AsyncSession]:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    maker = asyncio.run(_setup())
    monkeypatch.setattr(admin_cli, "async_session_maker", maker)
    try:
        yield maker
    finally:
        asyncio.run(engine.dispose())


async def _make_org(
    maker: async_sessionmaker[AsyncSession], slug: str = "acme", name: str = "Acme"
) -> Organization:
    async with maker() as session:
        org = Organization(name=name, slug=slug)
        session.add(org)
        await session.commit()
        await session.refresh(org)
        return org


async def _make_sso(
    maker: async_sessionmaker[AsyncSession],
    org_id: str,
    *,
    status: str = "active",
    protocol: str = "oidc",
    display_name: str = "Acme OIDC",
    provisioning: str = "auto",
) -> SSOConnection:
    async with maker() as session:
        conn = SSOConnection(
            org_id=org_id,
            protocol=protocol,
            display_name=display_name,
            status=status,
            provisioning=provisioning,
            config={},
        )
        session.add(conn)
        await session.commit()
        await session.refresh(conn)
        return conn


async def _fetch_sso(maker: async_sessionmaker[AsyncSession], sso_id: str) -> SSOConnection:
    async with maker() as session:
        return (
            await session.execute(
                select(SSOConnection).where(SSOConnection.id == sso_id)  # type: ignore[arg-type]
            )
        ).scalar_one()


def test_disable_sso_flips_active_to_inactive_and_commits(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """`disable-sso` flips an active connection to ``inactive`` and persists."""
    org = asyncio.run(_make_org(session_maker))
    conn = asyncio.run(_make_sso(session_maker, org.id, status="active"))

    runner = CliRunner()
    result = runner.invoke(admin_cli.admin_group, ["disable-sso", "--org-slug", "acme"])

    assert result.exit_code == 0, result.output
    assert "Disabled SSO for org 'acme'" in result.output
    assert f"sso_id={conn.id}" in result.output

    refreshed = asyncio.run(_fetch_sso(session_maker, conn.id))
    assert refreshed.status == "inactive"
    # Config / credential pointer must NOT be cleared — operator needs to inspect.
    assert refreshed.config == {}


def test_disable_sso_unknown_org_exits_nonzero_and_no_writes(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Unknown ``--org-slug`` exits non-zero; no DB rows mutate."""
    org = asyncio.run(_make_org(session_maker))
    conn = asyncio.run(_make_sso(session_maker, org.id, status="active"))

    runner = CliRunner()
    result = runner.invoke(admin_cli.admin_group, ["disable-sso", "--org-slug", "unknown"])

    assert result.exit_code != 0
    assert "No org with slug 'unknown'" in result.output

    refreshed = asyncio.run(_fetch_sso(session_maker, conn.id))
    assert refreshed.status == "active"


def test_disable_sso_org_without_connection_exits_nonzero(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """An org with no ``SSOConnection`` row exits non-zero."""
    asyncio.run(_make_org(session_maker, slug="bare"))

    runner = CliRunner()
    result = runner.invoke(admin_cli.admin_group, ["disable-sso", "--org-slug", "bare"])

    assert result.exit_code != 0
    assert "No SSO connection for org 'bare'" in result.output


def test_list_sso_prints_row_per_connection(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """`list-sso` prints one row per connection containing slug and sso_id."""
    org_a = asyncio.run(_make_org(session_maker, slug="acme", name="Acme"))
    org_b = asyncio.run(_make_org(session_maker, slug="globex", name="Globex"))
    conn_a = asyncio.run(
        _make_sso(
            session_maker,
            org_a.id,
            protocol="oidc",
            display_name="Acme OIDC",
            status="active",
        )
    )
    conn_b = asyncio.run(
        _make_sso(
            session_maker,
            org_b.id,
            protocol="saml",
            display_name="Globex SAML",
            status="inactive",
            provisioning="invite_only",
        )
    )

    runner = CliRunner()
    result = runner.invoke(admin_cli.admin_group, ["list-sso"])

    assert result.exit_code == 0, result.output
    out = result.output
    assert "acme" in out
    assert "globex" in out
    assert conn_a.id in out
    assert conn_b.id in out
    assert "oidc" in out
    assert "saml" in out
    assert "invite_only" in out
    # Header columns are present.
    for column in ("org_slug", "protocol", "status", "provisioning", "display_name", "sso_id"):
        assert column in out


def test_list_sso_empty_prints_message_and_exits_zero(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Empty list is not an error condition."""
    runner = CliRunner()
    result = runner.invoke(admin_cli.admin_group, ["list-sso"])

    assert result.exit_code == 0, result.output
    assert "No SSO connections configured." in result.output
