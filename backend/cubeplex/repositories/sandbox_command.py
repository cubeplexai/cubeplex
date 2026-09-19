"""Repository for sandbox_commands."""

from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.models.sandbox_command import (
    SandboxCommand,
    SandboxCommandStatus,
)
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.base import ScopedRepository

MAX_INFLIGHT_COMMANDS = 8
_INFLIGHT = (
    SandboxCommandStatus.starting.value,
    SandboxCommandStatus.running.value,
)


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
        command_id: str | None = None,
    ) -> SandboxCommand:
        locked = await self.session.execute(
            select(UserSandbox).where(col(UserSandbox.id) == user_sandbox_id).with_for_update()
        )
        if locked.scalar_one_or_none() is None:
            await self.session.rollback()
            raise LookupError(f"user sandbox not found: {user_sandbox_id}")
        count_stmt = (
            select(func.count())
            .select_from(SandboxCommand)
            .where(
                col(SandboxCommand.user_sandbox_id) == user_sandbox_id,
                col(SandboxCommand.status).in_(_INFLIGHT),
            )
        )
        n = int((await self.session.execute(count_stmt)).scalar_one())
        if n >= MAX_INFLIGHT_COMMANDS:
            await self.session.rollback()
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
        if command_id:
            row.id = command_id
        return await self.add(row)

    async def mark_running(
        self,
        command_id: str,
        *,
        provider_ref: str,
        owner_id: str,
    ) -> bool:
        stmt = (
            update(SandboxCommand)
            .where(
                col(SandboxCommand.id) == command_id,
                col(SandboxCommand.org_id) == self.org_id,
                col(SandboxCommand.workspace_id) == self.workspace_id,
                col(SandboxCommand.owner_id) == owner_id,
                col(SandboxCommand.status) == SandboxCommandStatus.starting.value,
            )
            .values(
                status=SandboxCommandStatus.running.value,
                provider_ref=provider_ref,
            )
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return int(result.rowcount or 0) == 1  # type: ignore[attr-defined]

    async def mark_terminal(
        self,
        command_id: str,
        *,
        status: str,
        exit_code: int | None,
        finished_at: datetime,
        notice_state: str | None = None,
    ) -> bool:
        values: dict[str, object] = {
            "status": status,
            "exit_code": exit_code,
            "finished_at": finished_at,
            "owner_id": None,
            "owner_until": None,
        }
        if notice_state is not None:
            values["notice_state"] = notice_state
        stmt = (
            update(SandboxCommand)
            .where(
                col(SandboxCommand.id) == command_id,
                col(SandboxCommand.org_id) == self.org_id,
                col(SandboxCommand.workspace_id) == self.workspace_id,
                col(SandboxCommand.status).in_(_INFLIGHT),
            )
            .values(**values)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return int(result.rowcount or 0) == 1  # type: ignore[attr-defined]

    async def renew_owner(
        self,
        command_ids: list[str],
        *,
        owner_id: str,
        owner_until: datetime,
    ) -> None:
        if not command_ids:
            return
        stmt = (
            update(SandboxCommand)
            .where(
                col(SandboxCommand.id).in_(command_ids),
                col(SandboxCommand.org_id) == self.org_id,
                col(SandboxCommand.workspace_id) == self.workspace_id,
                col(SandboxCommand.owner_id) == owner_id,
                col(SandboxCommand.status).in_(_INFLIGHT),
            )
            .values(owner_until=owner_until)
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def list_inflight_for_run(self, run_id: str) -> list[SandboxCommand]:
        stmt = self._scoped_select().where(
            col(SandboxCommand.run_id) == run_id,
            col(SandboxCommand.status).in_(_INFLIGHT),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


async def claim_expired_inflight(
    session: AsyncSession,
    *,
    owner_id: str,
    owner_until: datetime,
    now: datetime,
) -> list[SandboxCommand]:
    """CAS-claim starting/running rows whose lease has expired. Unscoped."""
    expired = or_(
        col(SandboxCommand.owner_until).is_(None),
        col(SandboxCommand.owner_until) < now,
    )
    ids_stmt = select(col(SandboxCommand.id)).where(
        col(SandboxCommand.status).in_(_INFLIGHT),
        expired,
    )
    ids = [row[0] for row in (await session.execute(ids_stmt)).all()]
    if not ids:
        return []
    await session.execute(
        update(SandboxCommand)
        .where(
            col(SandboxCommand.id).in_(ids),
            col(SandboxCommand.status).in_(_INFLIGHT),
            expired,
        )
        .values(owner_id=owner_id, owner_until=owner_until)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    result = await session.execute(
        select(SandboxCommand).where(
            col(SandboxCommand.id).in_(ids),
            col(SandboxCommand.owner_id) == owner_id,
        )
    )
    return list(result.scalars().all())


async def mark_notice_delivered(session: AsyncSession, command_id: str) -> bool:
    """Checkpoint path: notice_id is in history, so mark delivered."""
    from cubeplex.models.sandbox_command import SandboxCommandNoticeState

    stmt = (
        update(SandboxCommand)
        .where(
            col(SandboxCommand.id) == command_id,
            col(SandboxCommand.notice_state) == SandboxCommandNoticeState.pending.value,
        )
        .values(notice_state=SandboxCommandNoticeState.delivered.value)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    await session.commit()
    return int(result.rowcount or 0) == 1  # type: ignore[attr-defined]
