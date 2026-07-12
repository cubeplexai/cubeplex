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
from cubeplex.repositories.memory import MemoryRepository
from cubeplex.services.memory import CreateMemoryInput, MemoryService


async def test_consolidation_source_type_persists(
    db_session: AsyncSession, seed_user: User
) -> None:
    """Regression: the `memorysourcetype` Postgres enum must include
    'consolidation' (background consolidation writes it). Without the enum
    migration, this insert fails with an invalid-enum-value error."""
    repo = MemoryRepository(db_session, user_id=seed_user.id, org_id=None, workspace_id=None)
    svc = MemoryService(repo, user_id=seed_user.id, org_id=None, workspace_id=None)
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


async def test_find_exact_normalizes_trailing_punctuation(
    db_session: AsyncSession, seed_user: User
) -> None:
    """Regression: find_exact must catch mechanical duplicates even when the
    only difference is trailing punctuation. The agent-vs-reflection race
    typically saves "用户喜欢X。" and "用户喜欢X" within the same turn;
    strict-equality dedup misses this and the user sees doubled entries.
    """
    repo = MemoryRepository(db_session, user_id=seed_user.id, org_id=None, workspace_id=None)
    svc = MemoryService(repo, user_id=seed_user.id, org_id=None, workspace_id=None)

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
            content="用户喜欢吃小笼包",  # same fact, no trailing punctuation
        )
    )
    # Should return the existing row (bump_updated_at), NOT insert a new one.
    assert second.id == first.id

    # Same for trailing whitespace + ASCII punctuation mixes.
    third = await svc.create(
        CreateMemoryInput(
            scope=MemoryScope.PERSONAL,
            type=MemoryType.PREFERENCE,
            content="  用户喜欢吃小笼包.  ",
        )
    )
    assert third.id == first.id


async def test_personal_scope_invariant_violation_rejected(
    db_session: AsyncSession, seed_user: User
) -> None:
    item = MemoryItem(
        scope=MemoryScope.PERSONAL,
        owner_user_id=seed_user.id,
        org_id="org-leak",  # invariant violation: personal must have org_id NULL
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

    # Outsider user in a different workspace
    repo_outsider = MemoryRepository(
        db_session,
        user_id=seed_other_workspace_user.id,
        org_id="org-other",
        workspace_id="ws-other",
    )
    items = await repo_outsider.list(scope=MemoryScope.WORKSPACE)
    assert items == []


async def test_personal_memory_org_independent(
    db_session: AsyncSession,
    seed_user: User,
    seed_two_workspaces: tuple[Workspace, Workspace],
) -> None:
    ws_a, ws_b = seed_two_workspaces
    # Save personal memory while in ws_a
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
    # Read from ws_b
    repo_b = MemoryRepository(
        db_session, user_id=seed_user.id, org_id=ws_b.org_id, workspace_id=ws_b.id
    )
    items = await repo_b.list(scope=MemoryScope.PERSONAL)
    assert len(items) == 1
    assert items[0].content == "Respond in Chinese."
