"""Unit tests for markdown eligibility, path helpers, and content update flow."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from cubeplex.repositories import ArtifactRepository
from cubeplex.services.artifact_content import (
    MAX_CONTENT_BYTES,
    ArtifactContentError,
    is_markdown_eligible,
    markdown_filename,
    resolve_sandbox_write_path,
    update_artifact_content,
)


def _art(**kwargs: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "artifact_type": "document",
        "path": "/workspace/docs/guide.md",
        "entry_file": None,
        "mime_type": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_markdown_filename_prefers_entry() -> None:
    assert markdown_filename(_art(path="/workspace/docs", entry_file="nested/README.md")) == (
        "README.md"
    )


def test_markdown_filename_rejects_escape() -> None:
    assert markdown_filename(_art(entry_file="../x.md")) is None
    assert markdown_filename(_art(entry_file="/etc/passwd")) is None


def test_is_markdown_eligible_by_extension() -> None:
    assert is_markdown_eligible(_art()) is True
    assert is_markdown_eligible(_art(path="/workspace/a.pdf")) is False


def test_is_markdown_eligible_by_mime() -> None:
    assert is_markdown_eligible(_art(path="/workspace/x", mime_type="text/markdown")) is True


def test_resolve_sandbox_write_path_file() -> None:
    path, reason = resolve_sandbox_write_path(_art())
    assert reason is None
    assert path == "/workspace/docs/guide.md"


def test_resolve_sandbox_write_path_entry() -> None:
    path, reason = resolve_sandbox_write_path(_art(path="/workspace/docs", entry_file="README.md"))
    assert reason is None
    assert path == "/workspace/docs/README.md"


def test_resolve_sandbox_write_path_escape() -> None:
    path, reason = resolve_sandbox_write_path(_art(entry_file="../secret.md"))
    assert path is None
    assert reason == "path_escape"


def test_resolve_sandbox_write_path_rejects_outside_workspace() -> None:
    path, reason = resolve_sandbox_write_path(_art(path="/etc/config.md", entry_file=None))
    assert path is None
    assert reason == "path_escape"
    path2, reason2 = resolve_sandbox_write_path(
        _art(path="/workspace/../etc/config.md", entry_file=None)
    )
    assert path2 is None
    assert reason2 == "path_escape"


def test_resolve_sandbox_write_path_no_path() -> None:
    path, reason = resolve_sandbox_write_path(_art(path="", entry_file=None))
    assert path is None
    assert reason == "no_path"


def test_resolve_sandbox_write_path_directory_without_entry() -> None:
    path, reason = resolve_sandbox_write_path(
        _art(path="/workspace/docs/", entry_file=None, mime_type="text/markdown")
    )
    assert path is None
    assert reason == "path_is_directory"


# ---------------------------------------------------------------------------
# update_artifact_content (SQLite + mocked object store)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_session() -> AsyncIterator[AsyncSession]:
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


async def _seed_md_artifact(
    session: AsyncSession,
    *,
    org_id: str = "org-1",
    workspace_id: str = "ws-1",
    conversation_id: str = "conv-1",
    path: str = "/workspace/guide.md",
    entry_file: str | None = None,
    mime_type: str | None = "text/markdown",
    artifact_type: str = "document",
    version: int = 1,
) -> str:
    repo = ArtifactRepository(session, org_id=org_id, workspace_id=workspace_id)
    art = await repo.create(
        conversation_id=conversation_id,
        name="guide.md",
        artifact_type=artifact_type,
        path=path,
        entry_file=entry_file,
        mime_type=mime_type,
        description=None,
    )
    if version != 1:
        art.version = version
        await session.commit()
        await session.refresh(art)
    return art.id


def _mock_store(*, objects: list[str] | None = None, list_error: Exception | None = None):
    store = AsyncMock()
    if list_error is not None:
        store.list_objects = AsyncMock(side_effect=list_error)
    else:
        store.list_objects = AsyncMock(return_value=objects if objects is not None else ["a.md"])
    store.upload_file = AsyncMock()
    store.delete_file = AsyncMock()
    return store


@pytest.mark.asyncio
async def test_update_happy_path_no_sandbox(db_session: AsyncSession) -> None:
    art_id = await _seed_md_artifact(db_session)
    store = _mock_store(objects=[f"artifacts/conv-1/{art_id}/v1/guide.md"])
    with (
        patch(
            "cubeplex.services.artifact_content.get_objectstore_client",
            return_value=store,
        ),
        patch(
            "cubeplex.services.artifact_content._best_effort_sandbox_write",
            new=AsyncMock(return_value=(False, "no_sandbox")),
        ),
    ):
        result = await update_artifact_content(
            db_session,
            org_id="org-1",
            workspace_id="ws-1",
            conversation_id="conv-1",
            artifact_id=art_id,
            content="# hi\n",
            expected_version=1,
            caller_user_id="usr-1",
        )
    assert result.artifact.version == 2
    assert result.sandbox_synced is False
    assert result.sandbox_sync_reason == "no_sandbox"
    assert store.upload_file.await_count >= 2  # staging + final
    store.delete_file.assert_awaited()


@pytest.mark.asyncio
async def test_update_version_conflict(db_session: AsyncSession) -> None:
    art_id = await _seed_md_artifact(db_session, version=2)
    store = _mock_store(objects=["x.md"])
    with patch(
        "cubeplex.services.artifact_content.get_objectstore_client",
        return_value=store,
    ):
        with pytest.raises(ArtifactContentError) as ei:
            await update_artifact_content(
                db_session,
                org_id="org-1",
                workspace_id="ws-1",
                conversation_id="conv-1",
                artifact_id=art_id,
                content="# stale\n",
                expected_version=1,
                caller_user_id="usr-1",
            )
    assert ei.value.code == "version_conflict"
    store.delete_file.assert_awaited()  # staging GC


@pytest.mark.asyncio
async def test_update_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(ArtifactContentError) as ei:
        await update_artifact_content(
            db_session,
            org_id="org-1",
            workspace_id="ws-1",
            conversation_id="conv-1",
            artifact_id="art-missing",
            content="x",
            expected_version=1,
            caller_user_id="usr-1",
        )
    assert ei.value.code == "not_found"


@pytest.mark.asyncio
async def test_update_wrong_conversation(db_session: AsyncSession) -> None:
    art_id = await _seed_md_artifact(db_session, conversation_id="conv-A")
    with pytest.raises(ArtifactContentError) as ei:
        await update_artifact_content(
            db_session,
            org_id="org-1",
            workspace_id="ws-1",
            conversation_id="conv-B",
            artifact_id=art_id,
            content="x",
            expected_version=1,
            caller_user_id="usr-1",
        )
    assert ei.value.code == "not_found"


@pytest.mark.asyncio
async def test_update_not_markdown(db_session: AsyncSession) -> None:
    art_id = await _seed_md_artifact(
        db_session,
        path="/workspace/a.pdf",
        mime_type="application/pdf",
        artifact_type="document",
    )
    with pytest.raises(ArtifactContentError) as ei:
        await update_artifact_content(
            db_session,
            org_id="org-1",
            workspace_id="ws-1",
            conversation_id="conv-1",
            artifact_id=art_id,
            content="x",
            expected_version=1,
            caller_user_id="usr-1",
        )
    assert ei.value.code == "not_markdown"


@pytest.mark.asyncio
async def test_update_no_entry_for_mime_only(db_session: AsyncSession) -> None:
    # mime marks as markdown eligible, but no .md filename target
    art_id = await _seed_md_artifact(
        db_session,
        path="/workspace/notes",
        entry_file=None,
        mime_type="text/markdown",
    )
    with pytest.raises(ArtifactContentError) as ei:
        await update_artifact_content(
            db_session,
            org_id="org-1",
            workspace_id="ws-1",
            conversation_id="conv-1",
            artifact_id=art_id,
            content="x",
            expected_version=1,
            caller_user_id="usr-1",
        )
    assert ei.value.code == "no_entry"


@pytest.mark.asyncio
async def test_update_multi_file_rejected(db_session: AsyncSession) -> None:
    art_id = await _seed_md_artifact(db_session)
    store = _mock_store(objects=["a.md", "b.md"])
    with patch(
        "cubeplex.services.artifact_content.get_objectstore_client",
        return_value=store,
    ):
        with pytest.raises(ArtifactContentError) as ei:
            await update_artifact_content(
                db_session,
                org_id="org-1",
                workspace_id="ws-1",
                conversation_id="conv-1",
                artifact_id=art_id,
                content="# x\n",
                expected_version=1,
                caller_user_id="usr-1",
            )
    assert ei.value.code == "multi_file"


@pytest.mark.asyncio
async def test_update_list_failed(db_session: AsyncSession) -> None:
    art_id = await _seed_md_artifact(db_session)
    store = _mock_store(list_error=RuntimeError("s3 down"))
    with patch(
        "cubeplex.services.artifact_content.get_objectstore_client",
        return_value=store,
    ):
        with pytest.raises(ArtifactContentError) as ei:
            await update_artifact_content(
                db_session,
                org_id="org-1",
                workspace_id="ws-1",
                conversation_id="conv-1",
                artifact_id=art_id,
                content="# x\n",
                expected_version=1,
                caller_user_id="usr-1",
            )
    assert ei.value.code == "list_failed"


@pytest.mark.asyncio
async def test_update_bad_version_and_too_large(db_session: AsyncSession) -> None:
    art_id = await _seed_md_artifact(db_session)
    with pytest.raises(ArtifactContentError) as ei:
        await update_artifact_content(
            db_session,
            org_id="org-1",
            workspace_id="ws-1",
            conversation_id="conv-1",
            artifact_id=art_id,
            content="x",
            expected_version=0,
            caller_user_id="usr-1",
        )
    assert ei.value.code == "bad_version"

    with pytest.raises(ArtifactContentError) as ei2:
        await update_artifact_content(
            db_session,
            org_id="org-1",
            workspace_id="ws-1",
            conversation_id="conv-1",
            artifact_id=art_id,
            content="x" * (MAX_CONTENT_BYTES + 1),
            expected_version=1,
            caller_user_id="usr-1",
        )
    assert ei2.value.code == "too_large"


@pytest.mark.asyncio
async def test_best_effort_sandbox_synced(db_session: AsyncSession) -> None:
    art_id = await _seed_md_artifact(db_session)
    store = _mock_store(objects=["guide.md"])
    sandbox = AsyncMock()
    sandbox.upload = AsyncMock()
    attachment = MagicMock()
    attachment.sandbox = sandbox
    manager = MagicMock()
    manager.get_or_create = AsyncMock(return_value=attachment)
    record = SimpleNamespace(status="running")

    with (
        patch(
            "cubeplex.services.artifact_content.get_objectstore_client",
            return_value=store,
        ),
        patch(
            "cubeplex.api.routes.v1.ws_sandbox._resolve_sandbox_scope",
            new=AsyncMock(return_value=("user", "usr-1", "usr-1")),
        ),
        patch(
            "cubeplex.repositories.user_sandbox.UserSandboxRepository.get_active_by_scope",
            new=AsyncMock(return_value=record),
        ),
        patch(
            "cubeplex.sandbox.manager.get_sandbox_manager",
            return_value=manager,
        ),
    ):
        result = await update_artifact_content(
            db_session,
            org_id="org-1",
            workspace_id="ws-1",
            conversation_id="conv-1",
            artifact_id=art_id,
            content="# synced\n",
            expected_version=1,
            caller_user_id="usr-1",
        )
    assert result.sandbox_synced is True
    assert result.sandbox_sync_reason is None
    sandbox.upload.assert_awaited()


@pytest.mark.asyncio
async def test_best_effort_sandbox_missing_record(db_session: AsyncSession) -> None:
    art_id = await _seed_md_artifact(db_session)
    store = _mock_store(objects=["guide.md"])
    with (
        patch(
            "cubeplex.services.artifact_content.get_objectstore_client",
            return_value=store,
        ),
        patch(
            "cubeplex.api.routes.v1.ws_sandbox._resolve_sandbox_scope",
            new=AsyncMock(return_value=("user", "usr-1", "usr-1")),
        ),
        patch(
            "cubeplex.repositories.user_sandbox.UserSandboxRepository.get_active_by_scope",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await update_artifact_content(
            db_session,
            org_id="org-1",
            workspace_id="ws-1",
            conversation_id="conv-1",
            artifact_id=art_id,
            content="# x\n",
            expected_version=1,
            caller_user_id="usr-1",
        )
    assert result.sandbox_synced is False
    assert result.sandbox_sync_reason == "no_sandbox"


@pytest.mark.asyncio
async def test_best_effort_sandbox_upload_error(db_session: AsyncSession) -> None:
    art_id = await _seed_md_artifact(db_session)
    store = _mock_store(objects=["guide.md"])
    sandbox = AsyncMock()
    sandbox.upload = AsyncMock(side_effect=RuntimeError("upload fail"))
    attachment = MagicMock(sandbox=sandbox)
    manager = MagicMock()
    manager.get_or_create = AsyncMock(return_value=attachment)
    record = SimpleNamespace(status="running")

    with (
        patch(
            "cubeplex.services.artifact_content.get_objectstore_client",
            return_value=store,
        ),
        patch(
            "cubeplex.api.routes.v1.ws_sandbox._resolve_sandbox_scope",
            new=AsyncMock(return_value=("user", "usr-1", "usr-1")),
        ),
        patch(
            "cubeplex.repositories.user_sandbox.UserSandboxRepository.get_active_by_scope",
            new=AsyncMock(return_value=record),
        ),
        patch(
            "cubeplex.sandbox.manager.get_sandbox_manager",
            return_value=manager,
        ),
    ):
        result = await update_artifact_content(
            db_session,
            org_id="org-1",
            workspace_id="ws-1",
            conversation_id="conv-1",
            artifact_id=art_id,
            content="# x\n",
            expected_version=1,
            caller_user_id="usr-1",
        )
    assert result.sandbox_synced is False
    assert result.sandbox_sync_reason == "sandbox_error"
    assert result.artifact.version == 2  # save still succeeds


@pytest.mark.asyncio
async def test_cas_bump_version_repo_helper(db_session: AsyncSession) -> None:
    """Cover ArtifactRepository.cas_bump_version (still used as shared CAS primitive)."""
    art_id = await _seed_md_artifact(db_session)
    repo = ArtifactRepository(db_session, org_id="org-1", workspace_id="ws-1")
    ok = await repo.cas_bump_version(art_id, expected_version=1)
    assert ok is not None
    assert ok.version == 2
    miss = await repo.cas_bump_version(art_id, expected_version=1)
    assert miss is None
