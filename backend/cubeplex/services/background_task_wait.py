"""Validate CubeLoop Todo waits against durable CubePlex task facts."""

from __future__ import annotations

from dataclasses import dataclass

from cubeloop.agent.types import AgentContext
from cubeloop.middleware.todo import TaskWaitBinding, TaskWaitValidation
from cubeloop.types import JsonObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from cubeplex.models.background_task import (
    INFLIGHT_TASK_STATES,
    BackgroundTask,
    BackgroundTaskEvent,
    BackgroundTaskEventState,
)
from cubeplex.models.sandbox_command import SandboxCommand


@dataclass(frozen=True)
class _WaitFact:
    task: BackgroundTask
    command: SandboxCommand
    has_pending_event: bool


class BackgroundTaskWaitValidator:
    """Fail-closed host callback for ``write_todos.wait_for_tasks``."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        org_id: str,
        workspace_id: str,
        conversation_id: str,
        execution_generation: int,
        run_id: str,
    ) -> None:
        self._session_factory = session_factory
        self._org_id = org_id
        self._workspace_id = workspace_id
        self._conversation_id = conversation_id
        self._execution_generation = execution_generation
        self._run_id = run_id

    async def __call__(
        self,
        task_ids: list[str],
        ctx: AgentContext,
        previous: TaskWaitBinding | None,
    ) -> TaskWaitValidation:
        if ctx.run_id != self._run_id:
            return self._invalid("the live run does not own this wait declaration")
        if previous is not None and previous.task_ids != task_ids:
            return self._invalid("the task list changed after wait validation")

        facts = await self._load_facts(task_ids)
        if len(facts) != len(task_ids):
            return self._invalid("one or more background tasks were not found")
        if any(
            fact.task.conversation_id != self._conversation_id
            or fact.task.execution_generation != self._execution_generation
            for fact in facts
        ):
            return self._invalid("background tasks belong to another conversation generation")

        expected_validation = self._validation(facts)
        if previous is not None:
            prior_revisions = previous.validation.get("task_revisions")
            if (
                previous.validation.get("conversation_id") != self._conversation_id
                or previous.validation.get("execution_generation") != self._execution_generation
                or not isinstance(prior_revisions, dict)
            ):
                return self._invalid("the prior task wait has no valid host binding")
            cancelled = [
                fact
                for fact in facts
                if fact.task.stop_requested_at is not None
                or fact.task.notifications_cancelled_at is not None
            ]
            if cancelled:
                if all(
                    isinstance(prior_revisions.get(fact.task.id), int)
                    and fact.task.revision > prior_revisions[fact.task.id]
                    for fact in cancelled
                ):
                    return TaskWaitValidation(
                        status="cancelled",
                        reason="a previously validated background task was stopped",
                        validation=expected_validation,
                    )
                return self._invalid("task cancellation predates the wait declaration")

        reason = self._undeliverable_reason(facts)
        if reason is not None:
            return self._invalid(reason)
        return TaskWaitValidation(status="valid", validation=expected_validation)

    async def _load_facts(self, task_ids: list[str]) -> list[_WaitFact]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(BackgroundTask, SandboxCommand)
                    .join(SandboxCommand, col(SandboxCommand.task_id) == col(BackgroundTask.id))
                    .where(
                        col(BackgroundTask.id).in_(task_ids),
                        col(BackgroundTask.org_id) == self._org_id,
                        col(BackgroundTask.workspace_id) == self._workspace_id,
                        col(SandboxCommand.org_id) == self._org_id,
                        col(SandboxCommand.workspace_id) == self._workspace_id,
                    )
                )
            ).all()
            pending_event_ids = set(
                (
                    await session.execute(
                        select(col(BackgroundTaskEvent.task_id)).where(
                            col(BackgroundTaskEvent.task_id).in_(task_ids),
                            col(BackgroundTaskEvent.org_id) == self._org_id,
                            col(BackgroundTaskEvent.workspace_id) == self._workspace_id,
                            col(BackgroundTaskEvent.state).in_(
                                (
                                    BackgroundTaskEventState.pending.value,
                                    BackgroundTaskEventState.claimed.value,
                                )
                            ),
                        )
                    )
                ).scalars()
            )
        by_id = {
            task.id: _WaitFact(
                task=task,
                command=command,
                has_pending_event=task.id in pending_event_ids,
            )
            for task, command in rows
        }
        return [by_id[task_id] for task_id in task_ids if task_id in by_id]

    def _validation(self, facts: list[_WaitFact]) -> JsonObject:
        return {
            "conversation_id": self._conversation_id,
            "execution_generation": self._execution_generation,
            "task_revisions": {fact.task.id: fact.task.revision for fact in facts},
        }

    @staticmethod
    def _undeliverable_reason(facts: list[_WaitFact]) -> str | None:
        for fact in facts:
            task = fact.task
            if task.backgrounded_at is None:
                return "task has not been handed to background execution"
            if not task.notify_on_complete:
                return "task completion notifications are disabled"
            if task.foreground_result_delivered_at is not None:
                return "task result was already delivered in the foreground"
            if task.stop_requested_at is not None or task.notifications_cancelled_at is not None:
                return "task was stopped before this wait declaration"
            if task.state in INFLIGHT_TASK_STATES:
                if fact.command.provider_ref is None:
                    return "task has no recoverable provider handle"
            elif not fact.has_pending_event:
                return "task has no pending deliverable result"
        return None

    @staticmethod
    def _invalid(reason: str) -> TaskWaitValidation:
        return TaskWaitValidation(status="invalid", reason=reason)
