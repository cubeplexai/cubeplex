"""Lease and recover inflight sandbox_commands after a worker crash."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.models.sandbox_command import (
    SandboxCommand,
    SandboxCommandNoticeState,
    SandboxCommandStatus,
)
from cubeplex.repositories.sandbox_command import claim_expired_inflight
from cubeplex.sandbox.base import ProcessHandle, Sandbox

COMMAND_LEASE_SECONDS = 15
COORDINATOR_OWNER_ID = "command-coordinator"
GetSandbox = Callable[[SandboxCommand], Awaitable[Sandbox | None]]


async def reconcile_once(
    session: AsyncSession,
    *,
    get_sandbox: GetSandbox,
    now: datetime | None = None,
    owner_id: str = COORDINATOR_OWNER_ID,
) -> list[str]:
    """Claim expired inflight rows, poll or kill them, return finished ids."""
    moment = now or datetime.now(UTC)
    claimed = await claim_expired_inflight(
        session,
        owner_id=owner_id,
        owner_until=moment + timedelta(seconds=COMMAND_LEASE_SECONDS),
        now=moment,
    )
    finished: list[str] = []
    for row in claimed:
        try:
            did = await _reconcile_row(session, row, get_sandbox=get_sandbox, now=moment)
        except Exception:
            logger.exception("sandbox command reconcile failed for {}", row.id)
            continue
        if did:
            finished.append(row.id)
    return finished


async def kill_run_commands(
    session: AsyncSession,
    run_id: str,
    *,
    get_sandbox: GetSandbox,
    now: datetime | None = None,
) -> list[str]:
    """Terminate leftover starting/running rows for a finished or stale run."""
    from sqlalchemy import select
    from sqlmodel import col

    stmt = select(SandboxCommand).where(
        col(SandboxCommand.run_id) == run_id,
        col(SandboxCommand.status).in_(
            (
                SandboxCommandStatus.starting.value,
                SandboxCommandStatus.running.value,
            )
        ),
    )
    rows = list((await session.execute(stmt)).scalars().all())
    finished: list[str] = []
    moment = now or datetime.now(UTC)
    for row in rows:
        if await _terminalize(
            session,
            row,
            status=SandboxCommandStatus.killed.value,
            exit_code=None,
            now=moment,
            sandbox=await get_sandbox(row),
            interrupt=True,
        ):
            finished.append(row.id)
    return finished


async def command_coordinator_loop(
    session_factory: async_sessionmaker[AsyncSession],
    get_sandbox: GetSandbox | None = None,
    *,
    interval: float = 1.0,
) -> None:
    logger.info("Sandbox command coordinator started (interval={}s)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            async with session_factory() as session:

                async def _get(
                    row: SandboxCommand,
                    _session: AsyncSession = session,
                ) -> Sandbox | None:
                    if get_sandbox is not None:
                        return await get_sandbox(row)
                    return await sandbox_from_row(row, _session)

                await reconcile_once(session, get_sandbox=_get)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sandbox command coordinator tick failed")


async def sandbox_from_row(
    row: SandboxCommand,
    session: AsyncSession,
) -> Sandbox | None:
    """Reconnect the provider sandbox that owns ``row`` (best-effort)."""
    try:
        from cubeplex.models.user_sandbox import UserSandbox
        from cubeplex.sandbox.manager import get_sandbox_manager

        us = await session.get(UserSandbox, row.user_sandbox_id)
        if us is None:
            return None
        manager = get_sandbox_manager()
        attachment = await manager.get_or_create(
            scope_type=us.scope_type,
            scope_id=us.scope_id,
            user_id=us.user_id,
            org_id=us.org_id,
            workspace_id=us.workspace_id,
        )
        return attachment.sandbox
    except Exception:
        logger.exception("could not attach sandbox for command {}", row.id)
        return None


async def _reconcile_row(
    session: AsyncSession,
    row: SandboxCommand,
    *,
    get_sandbox: GetSandbox,
    now: datetime,
) -> bool:
    sandbox = await get_sandbox(row)
    if row.provider_ref is None:
        age = (now - row.created_at).total_seconds() if row.created_at else 0
        if age < COMMAND_LEASE_SECONDS * 2:
            return False
        return await _terminalize(
            session,
            row,
            status=SandboxCommandStatus.killed.value,
            exit_code=None,
            now=now,
            sandbox=sandbox,
            interrupt=False,
        )
    if sandbox is None:
        return await _terminalize(
            session,
            row,
            status=SandboxCommandStatus.killed.value,
            exit_code=None,
            now=now,
            sandbox=None,
            interrupt=False,
        )
    handle = ProcessHandle(command_id=row.id, provider_ref=row.provider_ref)
    snap = await sandbox.poll(handle)
    if snap.status == "running":
        return False
    status = (
        SandboxCommandStatus.killed.value
        if snap.status == "killed"
        else SandboxCommandStatus.exited.value
    )
    return await _terminalize(
        session,
        row,
        status=status,
        exit_code=snap.exit_code,
        now=now,
        sandbox=sandbox,
        interrupt=False,
        output=snap.new_output,
    )


async def _terminalize(
    session: AsyncSession,
    row: SandboxCommand,
    *,
    status: str,
    exit_code: int | None,
    now: datetime,
    sandbox: Sandbox | None,
    interrupt: bool,
    output: str = "",
) -> bool:
    if interrupt and sandbox is not None and row.provider_ref:
        try:
            await sandbox.kill(ProcessHandle(command_id=row.id, provider_ref=row.provider_ref))
        except Exception:
            logger.exception("interrupt failed for sandbox command {}", row.id)
    if output and sandbox is not None and row.log_path:
        await _append_log(sandbox, row.log_path, output)
    notice = row.notice_state
    if (
        row.notify_on_complete
        and status in (SandboxCommandStatus.exited.value, SandboxCommandStatus.killed.value)
        and notice == SandboxCommandNoticeState.none.value
    ):
        notice = SandboxCommandNoticeState.pending.value
    row.status = status
    row.exit_code = exit_code
    row.finished_at = now
    row.owner_id = None
    row.owner_until = None
    row.notice_state = notice
    session.add(row)
    await session.commit()
    return True


async def _append_log(sandbox: Sandbox, path: str, text: str) -> None:
    if not text:
        return
    existing = b""
    try:
        downloaded = await sandbox.download([path])
        if downloaded:
            existing = downloaded[0][1]
    except Exception:
        existing = b""
    try:
        await sandbox.upload([(path, existing + text.encode())])
    except Exception:
        logger.exception("failed to append sandbox command log {}", path)
