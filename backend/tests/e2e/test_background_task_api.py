"""Conversation task APIs expose durable facts without polling providers."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.models import (
    BackgroundTask,
    BackgroundTaskEvent,
    Conversation,
    ConversationExecutionAdmission,
    SandboxCommand,
    UserSandbox,
)
from cubeplex.models.background_task import BackgroundTaskState
from cubeplex.services.background_tasks import (
    BackgroundTaskService,
    CommandExecutionDetails,
    TaskReservation,
    TaskSpec,
)
from cubeplex.streams.run_events import create_run

NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)


@dataclass(frozen=True)
class ApiTaskContext:
    conversation: Conversation
    admission: ConversationExecutionAdmission
    sandbox: UserSandbox

    def service(self, session: AsyncSession) -> BackgroundTaskService:
        return BackgroundTaskService(
            session,
            org_id=self.conversation.org_id,
            workspace_id=self.conversation.workspace_id,
        )

    async def reserve(
        self,
        session: AsyncSession,
        *,
        command: str = "build project",
        description: str = "Build the project",
    ) -> TaskReservation:
        assert self.admission.run_id is not None
        assert self.sandbox.sandbox_id is not None
        return await self.service(session).reserve_task(
            admission_id=self.admission.id,
            task_spec=TaskSpec(
                originating_run_id=self.admission.run_id,
                tool_call_id=str(uuid4()),
                description=description,
            ),
            execution_details=CommandExecutionDetails(
                user_sandbox_id=self.sandbox.id,
                sandbox_instance_id=self.sandbox.sandbox_id,
                provider="local",
                command=command,
                log_path=f"/workspace/.cubeplex/{uuid4()}.log",
            ),
            owner_token=str(uuid4()),
            owner_until=NOW + timedelta(minutes=1),
            now=NOW,
        )


@pytest_asyncio.fixture
async def api_task_context(
    authenticated_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
) -> AsyncIterator[ApiTaskContext]:
    client, workspace_id = authenticated_client
    response = await client.post(f"/api/v1/ws/{workspace_id}/conversations")
    assert response.status_code == 201, response.text
    conversation_id = response.json()["id"]
    conversation = await db_session.get(Conversation, conversation_id)
    assert conversation is not None
    sandbox = UserSandbox(
        org_id=conversation.org_id,
        workspace_id=conversation.workspace_id,
        user_id=conversation.creator_user_id,
        scope_type="user",
        scope_id=conversation.creator_user_id,
        sandbox_id=f"sandbox-{uuid4()}",
        status="running",
        provider="local",
        image="test",
    )
    admission = ConversationExecutionAdmission(
        org_id=conversation.org_id,
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        actor_user_id=conversation.creator_user_id,
        execution_generation=conversation.execution_generation,
        source_kind="user_message",
        source_id=f"web:{uuid4()}",
        run_id=str(uuid4()),
    )
    db_session.add_all([sandbox, admission])
    await db_session.commit()
    conversation_id = conversation.id
    sandbox_id = sandbox.id
    context = ApiTaskContext(conversation, admission, sandbox)
    try:
        yield context
    finally:
        await db_session.rollback()
        for model in (BackgroundTaskEvent, SandboxCommand, BackgroundTask):
            await db_session.execute(
                delete(model).where(col(model.conversation_id) == conversation_id)
            )
        await db_session.execute(
            delete(ConversationExecutionAdmission).where(
                col(ConversationExecutionAdmission.conversation_id) == conversation_id
            )
        )
        await db_session.execute(delete(UserSandbox).where(col(UserSandbox.id) == sandbox_id))
        await db_session.execute(
            delete(Conversation).where(col(Conversation.id) == conversation_id)
        )
        await db_session.commit()


def task_path(workspace_id: str, conversation_id: str) -> str:
    return f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/background-tasks"


async def test_list_is_read_only_and_terminal_rows_require_explicit_ids(
    authenticated_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    api_task_context: ApiTaskContext,
) -> None:
    client, workspace_id = authenticated_client
    inflight = await api_task_context.reserve(db_session)
    inflight.command.provider_ref = "local-process"
    terminal = await api_task_context.reserve(db_session, command="true", description="Done")
    terminal.task.state = BackgroundTaskState.succeeded.value
    terminal.task.finished_at = NOW
    terminal.task.result_readiness = "ready"
    terminal.command.status = "exited"
    terminal.command.exit_code = 0
    terminal.command.log_state = "complete"
    terminal.command.finished_at = NOW
    await db_session.commit()
    original_revision = inflight.task.revision
    original_owner = inflight.task.owner_token

    path = task_path(workspace_id, api_task_context.conversation.id)
    response = await client.get(path)
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [inflight.task.id]
    item = response.json()["items"][0]
    assert item["details"] == {
        "type": "command",
        "command_id": inflight.command.id,
        "command_kind": "execute",
        "command": "build project",
        "status": "starting",
        "exit_code": None,
        "log_path": inflight.command.log_path,
        "log_state": "pending",
        "monitor_outcome": None,
    }
    assert "provider_ref" not in response.text
    assert "owner_token" not in response.text
    assert item["capabilities"]["remote_cancel_supported"] is False
    assert item["capabilities"]["reconnect_supported"] is False

    response = await client.get(path, params=[("task_ids", terminal.task.id)])
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [terminal.task.id]
    await db_session.refresh(inflight.task)
    assert inflight.task.revision == original_revision
    assert inflight.task.owner_token == original_owner


async def test_list_discovers_terminal_tasks_while_log_cleanup_is_pending(
    authenticated_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    api_task_context: ApiTaskContext,
) -> None:
    client, workspace_id = authenticated_client
    item = await api_task_context.reserve(db_session)
    item.task.state = BackgroundTaskState.succeeded.value
    item.task.finished_at = NOW
    item.command.status = "exited"
    item.command.exit_code = 0
    item.command.log_state = "retrying"
    item.command.finished_at = NOW
    await db_session.commit()

    response = await client.get(task_path(workspace_id, item.task.conversation_id))

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()["items"]] == [item.task.id]
    assert response.json()["items"][0]["cleanup_pending"] is True


async def test_recent_list_restores_stopped_task_without_result_event(
    authenticated_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    api_task_context: ApiTaskContext,
) -> None:
    client, workspace_id = authenticated_client
    item = await api_task_context.reserve(db_session)
    item.task.state = BackgroundTaskState.cancelled.value
    item.task.stop_requested_at = NOW
    item.task.notifications_cancelled_at = NOW
    item.task.finished_at = NOW
    item.command.status = "exited"
    item.command.log_state = "complete"
    item.command.finished_at = NOW
    await db_session.commit()

    path = task_path(workspace_id, item.task.conversation_id)
    actionable = await client.get(path)
    recent = await client.get(path, params={"recent_limit": 50})

    assert actionable.status_code == 200, actionable.text
    assert actionable.json()["items"] == []
    assert recent.status_code == 200, recent.text
    assert [row["id"] for row in recent.json()["items"]] == [item.task.id]
    assert recent.json()["items"][0]["state"] == "cancelled"


async def test_stop_returns_unconfirmed_then_preserves_terminal_fact(
    authenticated_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    api_task_context: ApiTaskContext,
) -> None:
    client, workspace_id = authenticated_client
    item = await api_task_context.reserve(db_session)
    item.command.provider = "opensandbox"
    item.command.provider_ref = "provider-process"
    item.task.backgrounded_at = NOW
    event = BackgroundTaskEvent(
        org_id=item.task.org_id,
        workspace_id=item.task.workspace_id,
        task_id=item.task.id,
        conversation_id=item.task.conversation_id,
        execution_generation=item.task.execution_generation,
        reason="completion",
        dedupe_key="completion",
        summary="done",
    )
    db_session.add(event)
    await db_session.commit()
    path = f"{task_path(workspace_id, item.task.conversation_id)}/{item.task.id}/stop"

    response = await client.post(path)
    assert response.status_code == 202, response.text
    assert response.json()["accepted"] is True
    assert response.json()["cleanup_pending"] is True
    assert response.json()["remote_cancel_supported"] is True
    assert response.json()["task"]["state"] == "starting"
    await db_session.refresh(item.task)
    await db_session.refresh(event)
    assert item.task.stop_reason == "user_stop"
    assert item.task.notifications_cancelled_at is not None
    assert event.state == "discarded"

    item.task.state = BackgroundTaskState.cancelled.value
    item.task.finished_at = NOW + timedelta(minutes=1)
    item.command.status = "killed"
    item.command.finished_at = item.task.finished_at
    item.command.log_state = "complete"
    item.task.result_readiness = "ready"
    await db_session.commit()
    response = await client.post(path)
    assert response.status_code == 200, response.text
    assert response.json()["cleanup_pending"] is False
    assert response.json()["task"]["state"] == "cancelled"


async def test_terminal_task_with_pending_event_remains_stoppable(
    authenticated_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    api_task_context: ApiTaskContext,
) -> None:
    client, workspace_id = authenticated_client
    item = await api_task_context.reserve(db_session)
    item.task.state = "succeeded"
    item.task.finished_at = NOW
    item.task.result_readiness = "ready"
    item.command.status = "exited"
    item.command.exit_code = 0
    item.command.log_state = "complete"
    item.command.finished_at = NOW
    event = BackgroundTaskEvent(
        org_id=item.task.org_id,
        workspace_id=item.task.workspace_id,
        task_id=item.task.id,
        conversation_id=item.task.conversation_id,
        execution_generation=item.task.execution_generation,
        reason="completion",
        dedupe_key="completion",
        summary="complete",
    )
    db_session.add(event)
    await db_session.commit()

    listed = await client.get(task_path(workspace_id, item.task.conversation_id))
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["items"]] == [item.task.id]
    assert listed.json()["items"][0]["capabilities"]["can_stop"] is True

    detail = await client.get(
        f"{task_path(workspace_id, item.task.conversation_id)}/{item.task.id}"
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["capabilities"]["can_stop"] is True
    response = await client.post(
        f"{task_path(workspace_id, item.task.conversation_id)}/{item.task.id}/stop"
    )
    assert response.status_code == 200, response.text
    await db_session.refresh(event)
    assert event.state == "discarded"


async def test_event_cursor_is_stable_filtered_and_bound_to_conversation(
    authenticated_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    api_task_context: ApiTaskContext,
) -> None:
    client, workspace_id = authenticated_client
    item = await api_task_context.reserve(db_session)
    events = [
        BackgroundTaskEvent(
            org_id=item.task.org_id,
            workspace_id=item.task.workspace_id,
            task_id=item.task.id,
            conversation_id=item.task.conversation_id,
            execution_generation=item.task.execution_generation,
            reason="completion",
            dedupe_key=f"event-{index}",
            summary=f"result {index}",
            state="delivered" if index == 1 else "pending",
            created_at=NOW + timedelta(seconds=index),
        )
        for index in range(3)
    ]
    db_session.add_all(events)
    await db_session.commit()
    path = (
        f"/api/v1/ws/{workspace_id}/conversations/"
        f"{item.task.conversation_id}/background-task-events"
    )

    first = await client.get(path, params={"limit": 1})
    assert first.status_code == 200, first.text
    assert [event["summary"] for event in first.json()["items"]] == ["result 2"]
    assert first.json()["has_more"] is True
    cursor = first.json()["next_cursor"]
    second = await client.get(path, params={"limit": 1, "cursor": cursor})
    assert second.status_code == 200, second.text
    assert [event["summary"] for event in second.json()["items"]] == ["result 0"]
    assert second.json()["has_more"] is False

    all_events = await client.get(path, params={"delivery": "all"})
    assert all_events.status_code == 200, all_events.text
    assert [event["summary"] for event in all_events.json()["items"]] == [
        "result 2",
        "result 1",
        "result 0",
    ]
    mismatched_filter = await client.get(path, params={"delivery": "all", "cursor": cursor})
    assert mismatched_filter.status_code == 422

    other_response = await client.post(f"/api/v1/ws/{workspace_id}/conversations")
    assert other_response.status_code == 201
    other_id = other_response.json()["id"]
    wrong_conversation = await client.get(
        f"/api/v1/ws/{workspace_id}/conversations/{other_id}/background-task-events",
        params={"cursor": cursor},
    )
    assert wrong_conversation.status_code == 422
    other = await db_session.get(Conversation, other_id)
    assert other is not None
    await db_session.delete(other)
    await db_session.commit()


async def test_task_ids_are_bounded_and_cross_conversation_ids_are_hidden(
    authenticated_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    api_task_context: ApiTaskContext,
) -> None:
    client, workspace_id = authenticated_client
    item = await api_task_context.reserve(db_session)
    await db_session.commit()
    path = task_path(workspace_id, api_task_context.conversation.id)
    too_many = await client.get(path, params=[("task_ids", str(index)) for index in range(101)])
    assert too_many.status_code == 422

    other_response = await client.post(f"/api/v1/ws/{workspace_id}/conversations")
    assert other_response.status_code == 201
    other_id = other_response.json()["id"]
    hidden = await client.get(f"{task_path(workspace_id, other_id)}/{item.task.id}")
    assert hidden.status_code == 404
    other = await db_session.scalar(select(Conversation).where(col(Conversation.id) == other_id))
    assert other is not None
    await db_session.delete(other)
    await db_session.commit()


async def test_bootstrap_summary_counts_full_event_set_not_first_page(
    authenticated_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    api_task_context: ApiTaskContext,
) -> None:
    client, workspace_id = authenticated_client
    item = await api_task_context.reserve(db_session)
    item.task.state = "succeeded"
    item.task.finished_at = NOW
    item.task.result_readiness = "ready"
    item.command.status = "exited"
    item.command.exit_code = 0
    item.command.log_state = "complete"
    item.command.finished_at = NOW
    db_session.add_all(
        [
            BackgroundTaskEvent(
                org_id=item.task.org_id,
                workspace_id=item.task.workspace_id,
                task_id=item.task.id,
                conversation_id=item.task.conversation_id,
                execution_generation=item.task.execution_generation,
                reason="completion",
                dedupe_key=f"bootstrap-{index}",
                summary=f"result {index}",
                created_at=NOW + timedelta(seconds=index),
            )
            for index in range(51)
        ]
    )
    await db_session.commit()

    response = await client.get(
        f"/api/v1/ws/{workspace_id}/conversations/{item.task.conversation_id}/bootstrap"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_generation"] == item.task.execution_generation
    assert body["background_summary"] == {
        "has_inflight": False,
        "has_pending": True,
        "has_cleanup": False,
        "can_stop": True,
    }
    assert len(body["background_events"]["items"]) == 50
    assert body["background_events"]["has_more"] is True
    assert body["background_events"]["next_cursor"]


async def test_bootstrap_stop_all_progress_includes_unfinished_run(
    authenticated_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    api_task_context: ApiTaskContext,
) -> None:
    client, workspace_id = authenticated_client
    conversation_id = api_task_context.conversation.id
    response = await client.post(
        f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/stop-all",
        json={"execution_generation": api_task_context.conversation.execution_generation},
    )
    assert response.status_code == 202, response.text

    bootstrap = await client.get(
        f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/bootstrap"
    )
    assert bootstrap.status_code == 200, bootstrap.text
    assert bootstrap.json()["stop_all"]["execution_generation"] == (
        api_task_context.conversation.execution_generation
    )
    assert bootstrap.json()["stop_all"]["cleanup_pending"] is True

    await db_session.refresh(api_task_context.admission)
    api_task_context.admission.run_finished_at = NOW
    await db_session.commit()
    bootstrap = await client.get(
        f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/bootstrap"
    )
    assert bootstrap.status_code == 200, bootstrap.text
    assert bootstrap.json()["stop_all"]["cleanup_pending"] is False


async def test_bootstrap_run_control_tracks_explicit_run_stop(
    authenticated_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    api_task_context: ApiTaskContext,
) -> None:
    client, workspace_id = authenticated_client
    conversation_id = api_task_context.conversation.id
    run_id = api_task_context.admission.run_id
    assert run_id is not None
    foreground = await api_task_context.reserve(db_session)
    await db_session.commit()
    app = client._transport.app  # type: ignore[attr-defined]
    await create_run(
        app.state.redis,
        prefix=app.state.redis_key_prefix,
        conversation_id=conversation_id,
        run_id=run_id,
        status="running",
        started_at=NOW.isoformat(),
        ttl_seconds=60,
    )
    response = await client.post(
        f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/cancel",
        json={"run_id": run_id},
    )
    assert response.status_code == 202, response.text

    bootstrap = await client.get(
        f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/bootstrap"
    )
    assert bootstrap.status_code == 200, bootstrap.text
    control = bootstrap.json()["run_control"]
    assert control["run_id"] == run_id
    assert control["stop_requested_at"] is not None
    assert control["cleanup_pending"] is True
    assert control["can_stop"] is False

    await db_session.refresh(api_task_context.admission)
    api_task_context.admission.run_finished_at = NOW
    await db_session.commit()
    bootstrap = await client.get(
        f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/bootstrap"
    )
    assert bootstrap.status_code == 200, bootstrap.text
    control = bootstrap.json()["run_control"]
    assert control["run_id"] == run_id
    assert control["cleanup_pending"] is True

    foreground.task.state = BackgroundTaskState.cancelled.value
    foreground.task.finished_at = NOW
    foreground.command.status = "killed"
    foreground.command.finished_at = NOW
    foreground.command.log_state = "complete"
    await db_session.commit()
    bootstrap = await client.get(
        f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/bootstrap"
    )
    assert bootstrap.status_code == 200, bootstrap.text
    assert bootstrap.json()["run_control"] is None
