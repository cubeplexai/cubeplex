"""Smoke test for the seed-mcp-templates CLI command."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from click.testing import CliRunner
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubeplex.cli import main as cli_main
from cubeplex.cli.seed_mcp_templates import _run as _run_seed_mcp_templates
from cubeplex.credentials.encryption import FernetBackend
from cubeplex.mcp.template_seed import CATALOG
from cubeplex.models import Credential, MCPConnectorTemplate


@pytest.fixture
async def in_memory_session_factory() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], FernetBackend]
]:
    """Build an in-memory async session maker + Fernet backend for CLI smoke."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    backend = FernetBackend([Fernet.generate_key()])
    yield maker, backend
    await engine.dispose()


async def test_seed_cli_dry_run_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    in_memory_session_factory: tuple[async_sessionmaker[AsyncSession], FernetBackend],
) -> None:
    """``cubeplex seed-mcp-templates --dry-run`` returns 0 + prints a summary.

    The CLI does its imports lazily inside ``seed_mcp_templates._run``, so
    we patch the source modules whose names get resolved at execution
    time.
    """
    maker, backend = in_memory_session_factory

    # The CLI imports these lazily; patch the source modules so the
    # local imports inside ``_run_seed_mcp_templates`` see the test
    # session factory and a deterministic backend.
    #
    # NOTE: ``import cubeplex.db.engine as m`` resolves ``m`` to the
    # ``engine`` attribute defined inside the module (Python's
    # attribute lookup wins over module-as-attribute here), so we
    # reach for ``sys.modules`` to get the actual module object.
    import sys

    import cubeplex.api.app as app_module

    db_engine_module = sys.modules["cubeplex.db.engine"]

    monkeypatch.setattr(app_module, "_build_encryption_backend", lambda: backend)
    monkeypatch.setattr(db_engine_module, "async_session_maker", maker)

    # Wipe env so DCR-less connectors get cleanly skipped.
    for entry in CATALOG:
        if entry.oauth_static_client_id_env is not None:
            monkeypatch.delenv(entry.oauth_static_client_id_env, raising=False)
        if entry.oauth_static_client_secret_env is not None:
            monkeypatch.delenv(entry.oauth_static_client_secret_env, raising=False)

    # Stub out asyncio.run so we can call the CLI inside an already-running
    # event loop (pytest-asyncio creates one).
    @asynccontextmanager
    async def _noop():
        yield

    async def _shim_runner() -> int:
        return await _run_seed_mcp_templates(dry_run=True, quiet=False)

    rc = await _shim_runner()
    captured = capsys.readouterr()

    assert rc == 0, captured.out + captured.err
    assert "seed-mcp-templates: upserted=" in captured.out
    assert "dry run, rolled back" in captured.out


async def test_seed_cli_dry_run_does_not_persist(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Dry-run rolls back the seeder transaction: no catalog rows, no
    OAuth-client-secret credentials are persisted to the DB.

    Uses ``StaticPool`` so the maker hands out a single shared
    connection — required for ``sqlite+aiosqlite:///:memory:`` to keep
    state visible across the separate ``async with`` blocks below
    (CLI vs. post-run verification session).
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    backend = FernetBackend([Fernet.generate_key()])

    import sys

    import cubeplex.api.app as app_module

    db_engine_module = sys.modules["cubeplex.db.engine"]

    monkeypatch.setattr(app_module, "_build_encryption_backend", lambda: backend)
    monkeypatch.setattr(db_engine_module, "async_session_maker", maker)

    # Provide full env so any DCR-less connectors actually attempt the
    # client-secret credential write — making this a meaningful
    # rollback assertion (the secret upsert path goes through a flush
    # the rollback must undo).
    for entry in CATALOG:
        if entry.oauth_static_client_id_env is not None:
            monkeypatch.setenv(entry.oauth_static_client_id_env, f"client-id-for-{entry.slug}")
        if entry.oauth_static_client_secret_env is not None:
            monkeypatch.setenv(
                entry.oauth_static_client_secret_env, f"client-secret-for-{entry.slug}"
            )

    # Sanity: DB is empty before the run.
    async with maker() as pre_session:
        pre_catalog = (await pre_session.execute(select(MCPConnectorTemplate))).scalars().all()
        pre_secrets = (
            (
                await pre_session.execute(
                    select(Credential).where(
                        Credential.org_id.is_(None),  # type: ignore[union-attr]
                        Credential.kind == "mcp_oauth_client_secret",  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(pre_catalog) == 0
    assert len(pre_secrets) == 0

    rc = await _run_seed_mcp_templates(dry_run=True, quiet=False)
    captured = capsys.readouterr()
    assert rc == 0, captured.out + captured.err
    assert "upserted=" in captured.out
    # Sanity: the run actually did some upserts in-transaction (so the
    # rollback is doing something, not no-op against an empty input).
    assert "upserted=0" not in captured.out

    # Post-run: zero catalog rows, zero client-secret credentials.
    async with maker() as post_session:
        post_catalog = (await post_session.execute(select(MCPConnectorTemplate))).scalars().all()
        post_secrets = (
            (
                await post_session.execute(
                    select(Credential).where(
                        Credential.org_id.is_(None),  # type: ignore[union-attr]
                        Credential.kind == "mcp_oauth_client_secret",  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(post_catalog) == 0, "dry-run leaked connector template rows into the DB"
    assert len(post_secrets) == 0, "dry-run leaked OAuth client_secret credentials"

    await engine.dispose()


def test_cli_main_unknown_command_returns_nonzero() -> None:
    """``click`` rejects unknown subcommands (exit code != 0)."""
    runner = CliRunner()
    result = runner.invoke(cli_main, ["does-not-exist"])
    assert result.exit_code != 0
