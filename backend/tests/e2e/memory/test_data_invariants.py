"""Memory data-layer invariants — schema, scope filtering, dedup."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import User
from cubebox.models.memory import (
    MemoryItem,
    MemoryScope,
    MemoryType,
)
from cubebox.models.workspace import Workspace
from cubebox.repositories.memory import MemoryRepository
from cubebox.services.memory import CreateMemoryInput, MemoryService


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
