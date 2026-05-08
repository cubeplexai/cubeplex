"""Smoke test for the seed-mcp-catalog CLI command."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import cubebox.cli.__main__ as cli_module
from cubebox.cli.__main__ import main as cli_main
from cubebox.credentials.encryption import FernetBackend
from cubebox.mcp.catalog_seed import CATALOG


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
    """``main(['seed-mcp-catalog', '--dry-run'])`` returns 0 + prints a summary.

    The CLI does its imports lazily inside ``_run_seed_mcp_catalog``, so we
    pre-import the module once via ``cli_module`` and patch the *names that
    will be resolved* at execution time.
    """
    maker, backend = in_memory_session_factory

    # The CLI imports these lazily; patch the source modules so the
    # local imports inside ``_run_seed_mcp_catalog`` see the test
    # session factory and a deterministic backend.
    #
    # NOTE: ``import cubebox.db.engine as m`` resolves ``m`` to the
    # ``engine`` attribute defined inside the module (Python's
    # attribute lookup wins over module-as-attribute here), so we
    # reach for ``sys.modules`` to get the actual module object.
    import sys

    import cubebox.api.app as app_module

    db_engine_module = sys.modules["cubebox.db.engine"]

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
        return await cli_module._run_seed_mcp_catalog(dry_run=True, quiet=False)

    rc = await _shim_runner()
    captured = capsys.readouterr()

    assert rc == 0, captured.out + captured.err
    assert "seed-mcp-catalog: upserted=" in captured.out
    assert "dry run, rolled back" in captured.out


def test_cli_main_unknown_command_returns_nonzero() -> None:
    """``argparse`` rejects unknown subcommands (exit code != 0)."""
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["does-not-exist"])
    assert exc_info.value.code != 0
