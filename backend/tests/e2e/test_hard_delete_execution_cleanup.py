"""Hard deletion waits for execution cleanup and succeeds on endpoint retry."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.models import (
    BackgroundTask,
    Conversation,
    ConversationExecutionAdmission,
    SandboxCommand,
    UserSandbox,
    Workspace,
)
from cubeplex.services.background_tasks import (
    BackgroundTaskService,
    CommandExecutionDetails,
    TaskSpec,
)

pytestmark = pytest.mark.e2e


async def test_workspace_delete_retains_cleanup_proof_until_retry(
    admin_client: tuple[httpx.AsyncClient, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, _ = admin_client
    workspaces = await client.get("/api/v1/workspaces")
    org_id = workspaces.json()[0]["org_id"]
    created = await client.post(
        "/api/v1/workspaces",
        json={"name": "pending deletion", "org_id": org_id},
    )
    assert created.status_code == 201, created.text
    workspace_id = created.json()["id"]
    me = await client.get("/api/v1/auth/me")
    user_id = me.json()["id"]

    async with session_factory() as session:
        now = datetime.now(UTC)
        conversation = Conversation(
            org_id=org_id,
            workspace_id=workspace_id,
            creator_user_id=user_id,
            title="pending workspace run",
            is_group_chat=True,
        )
        session.add(conversation)
        await session.flush()
        admission = ConversationExecutionAdmission(
            org_id=org_id,
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            actor_user_id=user_id,
            execution_generation=0,
            source_kind="user_message",
            source_id=f"web:{uuid4()}",
            run_id=str(uuid4()),
        )
        sandbox = UserSandbox(
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=user_id,
            scope_type="conversation",
            scope_id=conversation.id,
            sandbox_id=f"delete-{uuid4()}",
            status="running",
            provider="local",
            image="test",
        )
        session.add_all([admission, sandbox])
        await session.flush()
        reserved = await BackgroundTaskService(
            session, org_id=org_id, workspace_id=workspace_id
        ).reserve_task(
            admission_id=admission.id,
            task_spec=TaskSpec(
                originating_run_id=admission.run_id or "",
                tool_call_id=str(uuid4()),
            ),
            execution_details=CommandExecutionDetails(
                user_sandbox_id=sandbox.id,
                sandbox_instance_id=sandbox.sandbox_id or "",
                provider="local",
                command="long-running build",
                log_path="/workspace/build.log",
            ),
            owner_token=str(uuid4()),
            owner_until=now + timedelta(seconds=30),
            now=now,
        )
        # A conversation can reopen while an older generation is still cleaning up.
        # Hard deletion must retain that older task and command until both settle.
        conversation.execution_generation = 1
        await session.commit()
        conversation_id, admission_id = conversation.id, admission.id
        task_id, command_id = reserved.task.id, reserved.command.id

    first = await client.delete(f"/api/v1/workspaces/{workspace_id}")
    assert first.status_code == 200, first.text
    assert first.json() == {"deleted": False, "cleanup_pending": True}
    async with session_factory() as session:
        workspace = await session.get(Workspace, workspace_id)
        conversation = await session.get(Conversation, conversation_id)
        admission = await session.get(ConversationExecutionAdmission, admission_id)
        task = await session.get(BackgroundTask, task_id)
        command = await session.get(SandboxCommand, command_id)
        assert workspace is not None and workspace.deletion_pending_at is not None
        assert conversation is not None and conversation.deleted_at is not None
        assert admission is not None and admission.revoked_at is not None
        assert task is not None and task.stop_requested_at is not None
        assert command is not None
        now = datetime.now(UTC)
        admission.run_finished_at = now
        admission.run_terminal_at = now
        admission.run_terminal_status = "cancelled"
        task.state = "cancelled"
        task.finished_at = now
        command.status = "killed"
        command.log_state = "complete"
        command.finished_at = now
        await session.commit()

    retry = await client.delete(f"/api/v1/workspaces/{workspace_id}")
    assert retry.status_code == 200, retry.text
    assert retry.json() == {"deleted": True, "cleanup_pending": False}
    async with session_factory() as session:
        assert await session.get(Workspace, workspace_id) is None
        assert await session.get(ConversationExecutionAdmission, admission_id) is None
        assert await session.get(BackgroundTask, task_id) is None
        assert await session.get(SandboxCommand, command_id) is None
