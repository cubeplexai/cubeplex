"""Repository for sandbox_commands."""

from datetime import datetime

from sqlalchemy import func, select

from cubeplex.models.sandbox_command import (
    SandboxCommand,
    SandboxCommandStatus,
)
from cubeplex.repositories.base import ScopedRepository

MAX_INFLIGHT_COMMANDS = 8


class SandboxCommandCapError(Exception):
    """Too many starting+running commands on this sandbox."""


class SandboxCommandRepository(ScopedRepository[SandboxCommand]):
    model = SandboxCommand

    async def reserve(
        self,
        *,
        user_sandbox_id: str,
        conversation_id: str,
        run_id: str,
        tool_call_id: str,
        started_by_user_id: str,
        command: str,
        description: str,
        notify_on_complete: bool,
        owner_id: str,
        owner_until: datetime,
        log_path: str,
        agent_id: str | None = None,
        provider: str = "opensandbox",
    ) -> SandboxCommand:
        await self.session.execute(
            select(SandboxCommand.user_sandbox_id)
            .where(SandboxCommand.user_sandbox_id == user_sandbox_id)
            .with_for_update()
        )
        count_stmt = (
            select(func.count())
            .select_from(SandboxCommand)
            .where(
                SandboxCommand.user_sandbox_id == user_sandbox_id,
                SandboxCommand.status.in_(
                    (
                        SandboxCommandStatus.starting.value,
                        SandboxCommandStatus.running.value,
                    )
                ),
            )
        )
        n = int((await self.session.execute(count_stmt)).scalar_one())
        if n >= MAX_INFLIGHT_COMMANDS:
            raise SandboxCommandCapError(
                f"at most {MAX_INFLIGHT_COMMANDS} running commands per sandbox"
            )
        row = SandboxCommand(
            user_sandbox_id=user_sandbox_id,
            conversation_id=conversation_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            started_by_user_id=started_by_user_id,
            agent_id=agent_id,
            command=command,
            description=description,
            notify_on_complete=notify_on_complete,
            owner_id=owner_id,
            owner_until=owner_until,
            log_path=log_path,
            provider=provider,
            status=SandboxCommandStatus.starting.value,
        )
        return await self.add(row)

    async def list_inflight_for_run(self, run_id: str) -> list[SandboxCommand]:
        stmt = self._scoped_select().where(
            SandboxCommand.run_id == run_id,
            SandboxCommand.status.in_(
                (
                    SandboxCommandStatus.starting.value,
                    SandboxCommandStatus.running.value,
                )
            ),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
