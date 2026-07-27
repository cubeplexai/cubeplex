"""Memory data-layer invariants — schema, scope filtering, dedup."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import User
from cubeplex.models.memory import (
    MemoryItem,
    MemoryScope,
    MemorySourceType,
    MemoryType,
)
from cubeplex.models.workspace import Workspace
from cubeplex.repositories.memory import MemoryRepository, normalize_memory_content
from cubeplex.services.memory import CreateMemoryInput, MemoryPermissionError, MemoryService


async def test_consolidation_source_type_persists(
    db_session: AsyncSession, seed_user: User, seed_workspace: Workspace
) -> None:
    """Regression: the `memorysourcetype` Postgres enum must include
    'consolidation' (background consolidation writes it). Without the enum
    migration, this insert fails with an invalid-enum-value error."""
    repo = MemoryRepository(
        db_session,
        user_id=seed_user.id,
        org_id=seed_workspace.org_id,
        workspace_id=seed_workspace.id,
    )
    svc = MemoryService(
        repo,
        user_id=seed_user.id,
        org_id=seed_workspace.org_id,
        workspace_id=seed_workspace.id,
    )
    item = await svc.create(
        CreateMemoryInput(
            scope=MemoryScope.PERSONAL,
            type=MemoryType.PREFERENCE,
            content="prefers metric units",
            source_type=MemorySourceType.CONSOLIDATION,
            source_conversation_id="conv-x",
        )
    )
    assert item.source_type == MemorySourceType.CONSOLIDATION
    assert item.scope == MemoryScope.PERSONAL
    assert item.workspace_id == seed_workspace.id
    assert item.owner_user_id == seed_user.id


async def test_personal_create_requires_workspace(
    db_session: AsyncSession, seed_user: User
) -> None:
    repo = MemoryRepository(db_session, user_id=seed_user.id, org_id=None, workspace_id=None)
    svc = MemoryService(repo, user_id=seed_user.id, org_id=None, workspace_id=None)
    with pytest.raises(MemoryPermissionError, match="workspace context"):
        await svc.create(
            CreateMemoryInput(
                scope=MemoryScope.PERSONAL,
                type=MemoryType.PREFERENCE,
                content="no workspace",
            )
        )


async def test_find_exact_normalizes_trailing_punctuation(
    db_session: AsyncSession, seed_user: User, seed_workspace: Workspace
) -> None:
    """L0 dedup: trailing punctuation / whitespace must not create a second row."""
    repo = MemoryRepository(
        db_session,
        user_id=seed_user.id,
        org_id=seed_workspace.org_id,
        workspace_id=seed_workspace.id,
    )
    svc = MemoryService(
        repo,
        user_id=seed_user.id,
        org_id=seed_workspace.org_id,
        workspace_id=seed_workspace.id,
    )

    first = await svc.create(
        CreateMemoryInput(
            scope=MemoryScope.PERSONAL,
            type=MemoryType.PREFERENCE,
            content="用户喜欢吃小笼包。",
        )
    )
    second = await svc.create(
        CreateMemoryInput(
            scope=MemoryScope.PERSONAL,
            type=MemoryType.PREFERENCE,
            content="用户喜欢吃小笼包",
        )
    )
    assert second.id == first.id

    third = await svc.create(
        CreateMemoryInput(
            scope=MemoryScope.PERSONAL,
            type=MemoryType.PREFERENCE,
            content="  用户喜欢吃小笼包.  ",
        )
    )
    assert third.id == first.id


async def test_find_exact_collapses_internal_whitespace(
    db_session: AsyncSession, seed_user: User, seed_workspace: Workspace
) -> None:
    repo = MemoryRepository(
        db_session,
        user_id=seed_user.id,
        org_id=seed_workspace.org_id,
        workspace_id=seed_workspace.id,
    )
    svc = MemoryService(
        repo,
        user_id=seed_user.id,
        org_id=seed_workspace.org_id,
        workspace_id=seed_workspace.id,
    )
    first = await svc.create(
        CreateMemoryInput(
            scope=MemoryScope.PERSONAL,
            type=MemoryType.PREFERENCE,
            content="foo   bar",
        )
    )
    second = await svc.create(
        CreateMemoryInput(
            scope=MemoryScope.PERSONAL,
            type=MemoryType.PREFERENCE,
            content="  foo bar  ",
        )
    )
    assert second.id == first.id


def test_normalize_does_not_merge_negations() -> None:
    assert normalize_memory_content("今天下雨") != normalize_memory_content("今天没下雨")
    assert normalize_memory_content("raining") != normalize_memory_content("not raining")


async def test_personal_scope_invariant_violation_rejected(
    db_session: AsyncSession, seed_user: User, seed_workspace: Workspace
) -> None:
    item = MemoryItem(
        scope=MemoryScope.PERSONAL,
        owner_user_id=seed_user.id,
        workspace_id=seed_workspace.id,
        org_id="org-leak",  # personal must keep org_id NULL
        type=MemoryType.PREFERENCE,
        content="x",
        created_by_user_id=seed_user.id,
    )
    db_session.add(item)
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_workspace_visible_to_member_not_outsider(
    db_session: AsyncSession,
    seed_workspace: Workspace,
    seed_user: User,
    seed_other_workspace_user: User,
) -> None:
    repo_owner = MemoryRepository(
        db_session,
        user_id=seed_user.id,
        org_id=seed_workspace.org_id,
        workspace_id=seed_workspace.id,
    )
    svc = MemoryService(
        repo_owner,
        user_id=seed_user.id,
        org_id=seed_workspace.org_id,
        workspace_id=seed_workspace.id,
    )
    await svc.create(
        CreateMemoryInput(
            scope=MemoryScope.WORKSPACE,
            type=MemoryType.PROCEDURE,
            content="Run E2E with `pnpm test:e2e`.",
        )
    )

    repo_outsider = MemoryRepository(
        db_session,
        user_id=seed_other_workspace_user.id,
        org_id="org-other",
        workspace_id="ws-other",
    )
    items = await repo_outsider.list(scope=MemoryScope.WORKSPACE)
    assert items == []


async def test_personal_memory_isolated_per_workspace(
    db_session: AsyncSession,
    seed_user: User,
    seed_two_workspaces: tuple[Workspace, Workspace],
) -> None:
    """Personal is private-to-user within a workspace — not cross-workspace."""
    ws_a, ws_b = seed_two_workspaces
    repo_a = MemoryRepository(
        db_session, user_id=seed_user.id, org_id=ws_a.org_id, workspace_id=ws_a.id
    )
    svc_a = MemoryService(repo_a, user_id=seed_user.id, org_id=ws_a.org_id, workspace_id=ws_a.id)
    await svc_a.create(
        CreateMemoryInput(
            scope=MemoryScope.PERSONAL,
            type=MemoryType.PREFERENCE,
            content="Respond in Chinese.",
        )
    )
    repo_b = MemoryRepository(
        db_session, user_id=seed_user.id, org_id=ws_b.org_id, workspace_id=ws_b.id
    )
    items_b = await repo_b.list(scope=MemoryScope.PERSONAL)
    assert items_b == []

    items_a = await repo_a.list(scope=MemoryScope.PERSONAL)
    assert len(items_a) == 1
    assert items_a[0].content == "Respond in Chinese."
    assert items_a[0].workspace_id == ws_a.id
