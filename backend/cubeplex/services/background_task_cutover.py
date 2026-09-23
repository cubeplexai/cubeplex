"""Read-only startup gate for the lifecycle writer cutover."""

from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.models.background_task import BackgroundTask, BackgroundTaskEvent
from cubeplex.models.sandbox_command import SandboxCommand, SandboxCommandWake


@dataclass(frozen=True)
class BackgroundTaskCutoverStatus:
    unmigrated_commands: int
    unmapped_wakes: int
    command_tasks_without_details: int

    @property
    def ready(self) -> bool:
        return not any(
            (
                self.unmigrated_commands,
                self.unmapped_wakes,
                self.command_tasks_without_details,
            )
        )


async def inspect_background_task_cutover(
    session: AsyncSession,
) -> BackgroundTaskCutoverStatus:
    """Return facts that must be zero before the new writers can start."""
    unmigrated_commands = int(
        await session.scalar(
            select(func.count())
            .select_from(SandboxCommand)
            .where(col(SandboxCommand.task_id).is_(None))
        )
        or 0
    )
    unmapped_wakes = int(
        await session.scalar(
            select(func.count())
            .select_from(SandboxCommandWake)
            .join(SandboxCommand, col(SandboxCommand.id) == col(SandboxCommandWake.command_id))
            .outerjoin(
                BackgroundTaskEvent,
                col(BackgroundTaskEvent.id) == col(SandboxCommandWake.id),
            )
            .where(
                or_(
                    col(BackgroundTaskEvent.id).is_(None),
                    col(BackgroundTaskEvent.task_id) != col(SandboxCommand.task_id),
                )
            )
        )
        or 0
    )
    command_tasks_without_details = int(
        await session.scalar(
            select(func.count())
            .select_from(BackgroundTask)
            .outerjoin(SandboxCommand, col(SandboxCommand.task_id) == col(BackgroundTask.id))
            .where(
                col(BackgroundTask.kind) == "command",
                col(SandboxCommand.id).is_(None),
            )
        )
        or 0
    )
    return BackgroundTaskCutoverStatus(
        unmigrated_commands=unmigrated_commands,
        unmapped_wakes=unmapped_wakes,
        command_tasks_without_details=command_tasks_without_details,
    )


async def require_background_task_cutover(session: AsyncSession) -> None:
    status = await inspect_background_task_cutover(session)
    if status.ready:
        return
    raise RuntimeError(
        "background-task lifecycle cutover is incomplete "
        f"(unmigrated_commands={status.unmigrated_commands}, "
        f"unmapped_wakes={status.unmapped_wakes}, "
        f"command_tasks_without_details={status.command_tasks_without_details}); "
        "stop old writers and run scripts/dev/migrate_background_tasks.py --apply"
    )
