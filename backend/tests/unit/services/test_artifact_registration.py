"""Unit tests for register_artifact_from_sandbox."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubeplex.services.artifact_registration import register_artifact_from_sandbox

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_session() -> AsyncIterator[AsyncSession]:
    """In-memory SQLite session with all cubeplex tables."""
    # Import all models so SQLModel.metadata is fully populated.
    import cubeplex.models  # noqa: F401

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture()
def fake_sandbox_ok() -> MagicMock:
    """Sandbox stub: execute('test -e ...') → exit_code 0."""
    sandbox = MagicMock()
    ok_result = MagicMock()
    ok_result.exit_code = 0
    sandbox.execute = AsyncMock(return_value=ok_result)
    return sandbox


@pytest.fixture()
def fake_sandbox_missing() -> MagicMock:
    """Sandbox stub: execute('test -e ...') → exit_code 1 (path absent)."""
    sandbox = MagicMock()
    fail_result = MagicMock()
    fail_result.exit_code = 1
    sandbox.execute = AsyncMock(return_value=fail_result)
    return sandbox


# ---------------------------------------------------------------------------
# Helper to wire the in-memory DB into the helper's async_session_maker call
# ---------------------------------------------------------------------------


def _patch_session_maker(session: AsyncSession):  # type: ignore[return]
    """Return a context manager that patches async_session_maker to yield session."""

    @asynccontextmanager
    async def _fake_maker() -> AsyncIterator[AsyncSession]:
        yield session

    return patch("cubeplex.db.engine.async_session_maker", return_value=_fake_maker())


def _patch_objectstore() -> patch:  # type: ignore[return]
    """Patch get_objectstore_client so upload is a no-op in unit tests."""
    mock_store = AsyncMock()
    mock_store.upload_from_sandbox = AsyncMock()
    return patch(
        "cubeplex.objectstore.get_objectstore_client",
        return_value=mock_store,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_call_creates_artifact_with_version_1(
    db_session: AsyncSession, fake_sandbox_ok: MagicMock
) -> None:
    """First registration of a new path → artifact version == 1."""
    with _patch_session_maker(db_session), _patch_objectstore():
        artifact = await register_artifact_from_sandbox(
            sandbox=fake_sandbox_ok,
            conversation_id="conv-test-1",
            org_id="org-1",
            workspace_id="ws-1",
            name="My Site",
            artifact_type="website",
            path="/out/index.html",
        )

    assert artifact.version == 1
    assert artifact.name == "My Site"
    assert artifact.artifact_type == "website"
    assert artifact.path == "/out/index.html"
    assert artifact.id.startswith("art-")


@pytest.mark.asyncio
async def test_second_call_same_path_auto_matches_and_bumps_version(
    db_session: AsyncSession, fake_sandbox_ok: MagicMock
) -> None:
    """Second call with the same path (no artifact_id) → same artifact.id, version == 2."""
    with _patch_session_maker(db_session), _patch_objectstore():
        first = await register_artifact_from_sandbox(
            sandbox=fake_sandbox_ok,
            conversation_id="conv-test-2",
            org_id="org-1",
            workspace_id="ws-1",
            name="My Site",
            artifact_type="website",
            path="/out/site",
        )

    with _patch_session_maker(db_session), _patch_objectstore():
        second = await register_artifact_from_sandbox(
            sandbox=fake_sandbox_ok,
            conversation_id="conv-test-2",
            org_id="org-1",
            workspace_id="ws-1",
            name="My Site v2",
            artifact_type="website",
            path="/out/site",
        )

    assert second.id == first.id
    assert second.version == 2


@pytest.mark.asyncio
async def test_path_missing_in_sandbox_raises_file_not_found(
    db_session: AsyncSession, fake_sandbox_missing: MagicMock
) -> None:
    """Path absent from sandbox → FileNotFoundError is raised."""
    with _patch_session_maker(db_session), _patch_objectstore():
        with pytest.raises(FileNotFoundError, match="/nonexistent"):
            await register_artifact_from_sandbox(
                sandbox=fake_sandbox_missing,
                conversation_id="conv-test-3",
                org_id="org-1",
                workspace_id="ws-1",
                name="Ghost",
                artifact_type="file",
                path="/nonexistent",
            )
