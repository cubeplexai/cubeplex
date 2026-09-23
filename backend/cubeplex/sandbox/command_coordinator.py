"""Lease and recover inflight sandbox_commands after a worker crash."""

from __future__ import annotations

import asyncio
import os
import shlex
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from cubeloop.providers.base import ReasoningControl
from loguru import logger
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col
from uuid_utils import uuid7

from cubeplex.models.sandbox_command import (
    SandboxCommand,
    SandboxCommandKind,
    SandboxCommandLifetime,
    SandboxCommandNoticeState,
    SandboxCommandStatus,
    SandboxCommandWake,
    SandboxCommandWakeState,
)
from cubeplex.repositories.sandbox_command import claim_expired_inflight
from cubeplex.sandbox.base import ProcessHandle, Sandbox

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from cubeplex.streams.run_manager import RunManager

COMMAND_LEASE_SECONDS = 45
WAKE_LEASE_SECONDS = 15
WAKE_RETRY_SECONDS = 60
LINE_WAKE_INTERVAL_SECONDS = 15
MAX_LINE_WAKE_DROPS = 3
MAX_LINE_WAKES = 8
FLOOD_KILL_SECONDS = 30
COMMAND_POLL_INTERVAL_SECONDS = 15.0
COORDINATOR_OWNER_ID = f"command-coordinator:{os.getpid()}:{uuid.uuid4().hex[:8]}"
GetSandbox = Callable[[SandboxCommand], Awaitable[Sandbox | None]]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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
    owned = list(
        (
            await session.execute(
                select(SandboxCommand).where(
                    col(SandboxCommand.owner_id) == owner_id,
                    col(SandboxCommand.lifetime) == SandboxCommandLifetime.conversation.value,
                    col(SandboxCommand.status).in_(
                        (
                            SandboxCommandStatus.starting.value,
                            SandboxCommandStatus.running.value,
                        )
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    seen = {row.id for row in claimed}
    claimed.extend(row for row in owned if row.id not in seen)
    finished: list[str] = []
    for row in claimed:
        try:
            did = await _reconcile_row(
                session,
                row,
                get_sandbox=get_sandbox,
                now=moment,
                owner_id=owner_id,
            )
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
        col(SandboxCommand.lifetime) == SandboxCommandLifetime.run.value,
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


async def kill_sandbox_commands(
    session: AsyncSession,
    user_sandbox_id: str,
    *,
    get_sandbox: GetSandbox,
    now: datetime | None = None,
) -> list[str]:
    """Mark starting/running rows for a sandbox killed (pause/restart/delete)."""
    from sqlalchemy import select
    from sqlmodel import col

    stmt = select(SandboxCommand).where(
        col(SandboxCommand.user_sandbox_id) == user_sandbox_id,
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


async def kill_command(
    session: AsyncSession,
    row: SandboxCommand,
    *,
    get_sandbox: GetSandbox,
    now: datetime | None = None,
) -> bool:
    """Interrupt one scoped command and terminalize it only after confirmation."""
    sandbox = await get_sandbox(row) if row.provider_ref is not None else None
    if row.provider_ref is not None and sandbox is None:
        logger.warning("cannot reconnect sandbox to interrupt command {}", row.id)
        return False
    return await _terminalize(
        session,
        row,
        status=SandboxCommandStatus.killed.value,
        exit_code=None,
        now=now or datetime.now(UTC),
        sandbox=sandbox,
        interrupt=sandbox is not None and row.provider_ref is not None,
        wake_text="monitor killed by user",
    )


async def command_coordinator_loop(
    session_factory: async_sessionmaker[AsyncSession],
    get_sandbox: GetSandbox | None = None,
    *,
    interval: float = COMMAND_POLL_INTERVAL_SECONDS,
    run_manager: RunManager | None = None,
    redis: Redis | None = None,
    redis_key_prefix: str | None = None,
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
            if run_manager is not None and redis is not None and redis_key_prefix is not None:
                await enqueue_pending_notices_once(
                    session_factory,
                    redis=redis,
                    redis_key_prefix=redis_key_prefix,
                )
                await deliver_wakes_once(
                    session_factory,
                    run_manager=run_manager,
                    redis=redis,
                    redis_key_prefix=redis_key_prefix,
                )
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
        if us is None or us.status != "running" or us.sandbox_id is None:
            return None
        original_sandbox_id = us.sandbox_id
        manager = get_sandbox_manager()
        attachment = await manager.get_or_create(
            scope_type=us.scope_type,
            scope_id=us.scope_id,
            user_id=us.user_id,
            org_id=us.org_id,
            workspace_id=us.workspace_id,
        )
        if attachment.sandbox.id != original_sandbox_id:
            await _terminalize(
                session,
                row,
                status=SandboxCommandStatus.killed.value,
                exit_code=None,
                now=datetime.now(UTC),
                sandbox=None,
                interrupt=False,
                wake_text="sandbox replaced while command was running",
            )
            return None
        await manager.touch(
            attachment.sandbox.id,
            org_id=us.org_id,
            workspace_id=us.workspace_id,
            force=True,
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
    owner_id: str,
) -> bool:
    if row.lifetime == SandboxCommandLifetime.conversation.value:
        from cubeplex.models import Conversation

        if not await _renew_conversation_row(
            session,
            row,
            now=now,
            owner_id=owner_id,
        ):
            return False
        conversation = await session.get(Conversation, row.conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            sandbox = await get_sandbox(row) if row.provider_ref is not None else None
            if row.provider_ref is not None and sandbox is None:
                logger.warning(
                    "cannot reconnect sandbox to stop command {} for deleted conversation",
                    row.id,
                )
                return False
            return await _terminalize(
                session,
                row,
                status=SandboxCommandStatus.killed.value,
                exit_code=None,
                now=now,
                sandbox=sandbox,
                interrupt=sandbox is not None and row.provider_ref is not None,
                wake_text="conversation deleted",
            )
    if row.provider_ref is None:
        age = (now - _as_utc(row.created_at)).total_seconds() if row.created_at else 0
        if age < COMMAND_LEASE_SECONDS * 2:
            return False
        return await _terminalize(
            session,
            row,
            status=SandboxCommandStatus.killed.value,
            exit_code=None,
            now=now,
            sandbox=None,
            interrupt=False,
        )
    sandbox = await get_sandbox(row)
    if sandbox is None:
        logger.warning("cannot reconnect sandbox to reconcile command {}; retrying", row.id)
        return False
    handle = ProcessHandle(
        command_id=row.id,
        provider_ref=row.provider_ref,
        log_cursor=row.log_cursor or "0",
        deadline_at=row.monitor_deadline_at,
    )
    snap = await sandbox.poll(handle)
    if snap.new_output and row.log_path:
        await _append_log(sandbox, row.log_path, snap.new_output)
    if snap.log_cursor is not None:
        row.log_cursor = snap.log_cursor
    deadline_hit = row.monitor_deadline_at is not None and _as_utc(row.monitor_deadline_at) <= now
    if snap.status == "running" and deadline_hit:
        return await _terminalize(
            session,
            row,
            status=SandboxCommandStatus.killed.value,
            exit_code=None,
            now=now,
            sandbox=sandbox,
            interrupt=True,
            wake_text=f"{row.kind} timeout reached",
        )
    if row.kind == SandboxCommandKind.monitor.value:
        return await _reconcile_monitor(
            session,
            row,
            snap=snap,
            sandbox=sandbox,
            now=now,
            owner_id=owner_id,
        )
    if snap.status == "running":
        if row.lifetime == SandboxCommandLifetime.conversation.value:
            await _renew_conversation_row(session, row, now=now, owner_id=owner_id)
            return False
        return await _terminalize(
            session,
            row,
            status=SandboxCommandStatus.killed.value,
            exit_code=None,
            now=now,
            sandbox=sandbox,
            interrupt=True,
        )
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
    )


async def _reconcile_monitor(
    session: AsyncSession,
    row: SandboxCommand,
    *,
    snap: Any,
    sandbox: Sandbox,
    now: datetime,
    owner_id: str,
) -> bool:
    lines = [line for line in snap.new_output.splitlines() if line.strip()]
    if snap.status != "running":
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
            wake_text=lines[-1] if lines else f"monitor {status}",
        )

    should_kill_flood = False
    if lines and not row.line_wakes_disabled:
        last_wake_at = await _last_line_wake_at(session, row.id)
        allowed = (
            last_wake_at is None
            or (now - _as_utc(last_wake_at)).total_seconds() >= LINE_WAKE_INTERVAL_SECONDS
        )
        if allowed:
            await _ensure_wake(
                session,
                row,
                reason="line",
                text_tail=lines[-1][-4000:],
                dedupe_suffix=snap.log_cursor or sha256(snap.new_output.encode()).hexdigest()[:16],
            )
            row.wake_count += 1
            row.wake_drops = max(0, len(lines) - 1)
            row.flood_started_at = now if row.wake_drops else None
            if row.wake_count >= MAX_LINE_WAKES:
                row.line_wakes_disabled = True
            if row.wake_drops >= MAX_LINE_WAKE_DROPS:
                row.line_wakes_disabled = True
        else:
            row.wake_drops += len(lines)
            if row.flood_started_at is None:
                row.flood_started_at = now
            should_kill_flood = (
                now - _as_utc(row.flood_started_at)
            ).total_seconds() >= FLOOD_KILL_SECONDS
            if row.wake_drops >= MAX_LINE_WAKE_DROPS:
                row.line_wakes_disabled = True
    elif lines:
        if len(lines) >= MAX_LINE_WAKE_DROPS:
            row.wake_drops += len(lines)
            if row.flood_started_at is None:
                row.flood_started_at = now
            should_kill_flood = (
                now - _as_utc(row.flood_started_at)
            ).total_seconds() >= FLOOD_KILL_SECONDS
        else:
            row.flood_started_at = None
    elif not lines:
        row.flood_started_at = None

    if should_kill_flood:
        return await _terminalize(
            session,
            row,
            status=SandboxCommandStatus.killed.value,
            exit_code=None,
            now=now,
            sandbox=sandbox,
            interrupt=True,
            wake_text="monitor killed after sustained output flood",
        )

    await _renew_conversation_row(session, row, now=now, owner_id=owner_id)
    return False


async def _last_line_wake_at(session: AsyncSession, command_id: str) -> datetime | None:
    stmt = (
        select(col(SandboxCommandWake.created_at))
        .where(
            col(SandboxCommandWake.command_id) == command_id,
            col(SandboxCommandWake.reason) == "line",
        )
        .order_by(col(SandboxCommandWake.created_at).desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _renew_conversation_row(
    session: AsyncSession,
    row: SandboxCommand,
    *,
    now: datetime,
    owner_id: str,
) -> bool:
    from cubeplex.models.user_sandbox import UserSandbox

    lease_start = max(now, datetime.now(UTC))
    owner_until = lease_start + timedelta(seconds=COMMAND_LEASE_SECONDS)
    stmt = (
        update(SandboxCommand)
        .where(
            col(SandboxCommand.id) == row.id,
            col(SandboxCommand.owner_id) == owner_id,
            col(SandboxCommand.status).in_(
                (
                    SandboxCommandStatus.starting.value,
                    SandboxCommandStatus.running.value,
                )
            ),
        )
        .values(owner_until=owner_until)
        .execution_options(synchronize_session=False)
    )
    with session.no_autoflush:
        result = await session.execute(stmt)
    if int(result.rowcount or 0) != 1:  # type: ignore[attr-defined]
        await session.rollback()
        return False
    row.owner_until = owner_until
    sandbox_row = await session.get(UserSandbox, row.user_sandbox_id)
    if sandbox_row is not None:
        sandbox_row.in_use_until = now + timedelta(seconds=COMMAND_LEASE_SECONDS * 2)
        session.add(sandbox_row)
    session.add(row)
    await session.commit()
    return True


async def _ensure_wake(
    session: AsyncSession,
    row: SandboxCommand,
    *,
    reason: str,
    text_tail: str,
    dedupe_suffix: str,
) -> SandboxCommandWake:
    dedupe_key = f"{row.id}:{reason}:{dedupe_suffix}"
    existing = (
        await session.execute(
            select(SandboxCommandWake).where(col(SandboxCommandWake.dedupe_key) == dedupe_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    wake = SandboxCommandWake(
        org_id=row.org_id,
        workspace_id=row.workspace_id,
        command_id=row.id,
        conversation_id=row.conversation_id,
        reason=reason,
        dedupe_key=dedupe_key,
        text_tail=text_tail,
        started_by_user_id=row.started_by_user_id,
    )
    try:
        async with session.begin_nested():
            session.add(wake)
            await session.flush()
        return wake
    except IntegrityError:
        existing = (
            await session.execute(
                select(SandboxCommandWake).where(col(SandboxCommandWake.dedupe_key) == dedupe_key)
            )
        ).scalar_one()
        return existing


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
    wake_text: str | None = None,
) -> bool:
    final_output = output
    if interrupt and sandbox is not None and row.provider_ref:
        handle = ProcessHandle(
            command_id=row.id,
            provider_ref=row.provider_ref,
            log_cursor=row.log_cursor or "0",
        )
        try:
            await sandbox.kill(handle)
            snap = await sandbox.poll(handle)
        except Exception:
            logger.exception("interrupt failed for sandbox command {}", row.id)
            return False
        if snap.status == "running":
            logger.warning("interrupt did not stop sandbox command {}", row.id)
            return False
        final_output += snap.new_output
        if snap.log_cursor is not None:
            row.log_cursor = snap.log_cursor
    if final_output and sandbox is not None and row.log_path:
        await _append_log(sandbox, row.log_path, final_output)
    row.status = status
    row.exit_code = exit_code
    row.finished_at = now
    row.owner_id = None
    row.owner_until = None
    if status == SandboxCommandStatus.killed.value:
        row.provider_ref = None
    if row.kind == SandboxCommandKind.execute.value and row.notify_on_complete:
        row.notice_state = SandboxCommandNoticeState.pending.value
    if row.kind == SandboxCommandKind.monitor.value:
        await _ensure_wake(
            session,
            row,
            reason="exit",
            text_tail=(final_output or wake_text or f"monitor {status}")[-4000:],
            dedupe_suffix="exit",
        )
    session.add(row)
    await session.commit()
    return True


async def enqueue_pending_notices_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    redis: Redis,
    redis_key_prefix: str,
) -> list[str]:
    """Move orphaned execute notices into the durable wake outbox."""
    from cubeplex.streams.run_events import get_active_run

    async with session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(SandboxCommand).where(
                        col(SandboxCommand.kind) == SandboxCommandKind.execute.value,
                        col(SandboxCommand.notice_state) == SandboxCommandNoticeState.pending.value,
                        col(SandboxCommand.status).not_in(
                            (
                                SandboxCommandStatus.starting.value,
                                SandboxCommandStatus.running.value,
                            )
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        enqueued: list[str] = []
        for row in rows:
            active = await get_active_run(
                redis,
                prefix=redis_key_prefix,
                conversation_id=row.conversation_id,
            )
            if active is not None and active.run_id == row.run_id:
                continue
            detail = f"Background command {row.id} {row.status}"
            if row.exit_code is not None:
                detail += f" (exit {row.exit_code})"
            wake = await _ensure_wake(
                session,
                row,
                reason="completion",
                text_tail=detail + ".",
                dedupe_suffix="completion",
            )
            enqueued.append(wake.id)
        await session.commit()
        return enqueued


async def deliver_wakes_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_manager: RunManager,
    redis: Redis,
    redis_key_prefix: str,
    now: datetime | None = None,
    owner_id: str = COORDINATOR_OWNER_ID,
) -> list[str]:
    """Claim and durably deliver monitor wakes through steering or a new run."""
    moment = now or datetime.now(UTC)
    async with session_factory() as session:
        wakes = await _claim_wakes(
            session,
            owner_id=owner_id,
            owner_until=moment + timedelta(seconds=WAKE_LEASE_SECONDS),
            now=moment,
        )
    delivered: list[str] = []
    for wake in wakes:
        delivery_now = datetime.now(UTC)
        async with session_factory() as session:
            current = await session.get(SandboxCommandWake, wake.id)
            if current is None or current.owner_id != owner_id:
                continue
            current.owner_until = delivery_now + timedelta(seconds=WAKE_LEASE_SECONDS)
            session.add(current)
            await session.commit()
        try:
            if await _deliver_wake(
                session_factory,
                wake,
                run_manager=run_manager,
                redis=redis,
                redis_key_prefix=redis_key_prefix,
                owner_id=owner_id,
                now=delivery_now,
            ):
                delivered.append(wake.id)
        except Exception:
            logger.exception("sandbox command wake delivery failed for {}", wake.id)
    return delivered


async def _claim_wakes(
    session: AsyncSession,
    *,
    owner_id: str,
    owner_until: datetime,
    now: datetime,
    limit: int = 20,
) -> list[SandboxCommandWake]:
    claimable = or_(
        col(SandboxCommandWake.state) == SandboxCommandWakeState.pending.value,
        (
            (col(SandboxCommandWake.state) == SandboxCommandWakeState.claimed.value)
            & (
                col(SandboxCommandWake.owner_until).is_(None)
                | (col(SandboxCommandWake.owner_until) < now)
            )
        ),
    )
    rows = list(
        (
            await session.execute(
                select(SandboxCommandWake)
                .where(claimable)
                .order_by(
                    col(SandboxCommandWake.created_at),
                    col(SandboxCommandWake.id),
                )
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.state = SandboxCommandWakeState.claimed.value
        row.owner_id = owner_id
        row.owner_until = owner_until
        session.add(row)
    await session.commit()
    return rows


def _wake_message(wake: SandboxCommandWake, *, description: str = "") -> str:
    detail = wake.text_tail.strip() or "no output"
    label = f" ({description})" if description else ""
    if wake.reason == "completion":
        return f"Sandbox command {wake.command_id}{label} completed.\n{detail}"
    return f"Sandbox monitor {wake.command_id}{label} emitted a {wake.reason} wake.\n{detail}"


def _wake_steer_id(wake_id: str, run_id: str) -> str:
    """Return a stable per-run steer id while retaining the wake id prefix."""
    run_key = sha256(run_id.encode()).hexdigest()[:16]
    return f"{wake_id}:{run_key}"


async def _deliver_wake(
    session_factory: async_sessionmaker[AsyncSession],
    wake: SandboxCommandWake,
    *,
    run_manager: RunManager,
    redis: Redis,
    redis_key_prefix: str,
    owner_id: str,
    now: datetime,
) -> bool:
    from cubeplex.agents.checkpointer import shared_checkpointer
    from cubeplex.models import SteeringMessage, SteeringMessageState, Topic
    from cubeplex.models.membership import Membership
    from cubeplex.repositories.conversation import ConversationRepository
    from cubeplex.streams.run_events import (
        get_active_run,
        get_run_meta,
        is_stale_meta,
        mark_run_stale,
    )
    from cubeplex.streams.run_manager import RunContext

    async with shared_checkpointer() as checkpointer:
        checkpoint = await checkpointer.load(wake.conversation_id)
    if checkpoint is not None:
        expected_notice_ids = {wake.id}
        if wake.reason == "completion":
            expected_notice_ids.add(wake.command_id)
        for message in checkpoint.messages:
            metadata = getattr(message, "metadata", None)
            if isinstance(metadata, dict) and metadata.get("notice_id") in expected_notice_ids:
                await _finish_wake(session_factory, wake.id, owner_id=owner_id)
                return True

    async with session_factory() as session:
        current = await session.get(SandboxCommandWake, wake.id)
        if current is None or current.owner_id != owner_id:
            return False

        membership = await session.get(
            Membership,
            (current.started_by_user_id, current.workspace_id),
        )
        conversation = None
        if membership is not None:
            conversation_repo = ConversationRepository(
                session,
                org_id=current.org_id,
                workspace_id=current.workspace_id,
                user_id=current.started_by_user_id,
            )
            conversation = await conversation_repo.get_by_id(current.conversation_id)
        if conversation is None or membership is None:
            await _finish_wake(session_factory, current.id, owner_id=owner_id)
            return True

        active = await get_active_run(
            redis,
            prefix=redis_key_prefix,
            conversation_id=current.conversation_id,
        )
        if active is not None and active.status == "running":
            from cubeplex.config import config

            stale_threshold = int(config.get("lifecycle.stale_run_threshold_seconds", 180))
            if is_stale_meta(active, threshold_seconds=stale_threshold, now=now):
                await mark_run_stale(
                    redis,
                    prefix=redis_key_prefix,
                    run_id=active.run_id,
                    conversation_id=current.conversation_id,
                    observed_last_event_at=active.last_event_at or active.started_at,
                )
                active = await get_active_run(
                    redis,
                    prefix=redis_key_prefix,
                    conversation_id=current.conversation_id,
                )
        if current.delivery_steer_id:
            steer = (
                await session.execute(
                    select(SteeringMessage).where(
                        col(SteeringMessage.conversation_id) == current.conversation_id,
                        col(SteeringMessage.client_steer_id) == current.delivery_steer_id,
                    )
                )
            ).scalar_one_or_none()
            if steer is not None and steer.state == SteeringMessageState.injected:
                await _finish_wake(session_factory, current.id, owner_id=owner_id)
                return True
            if steer is not None and steer.state in (
                SteeringMessageState.queued,
                SteeringMessageState.dispatched,
            ):
                if (
                    active is not None
                    and active.run_id == steer.run_id
                    and active.status == "running"
                ):
                    current.owner_until = now + timedelta(seconds=WAKE_RETRY_SECONDS)
                    session.add(current)
                    await session.commit()
                    try:
                        await run_manager.drain_durable_steering(steer.run_id)
                    except RuntimeError:
                        await _defer_wake(session, current, now=now)
                    return False
                from cubeplex.repositories.steering_message import SteeringMessageRepository

                repo = SteeringMessageRepository(
                    session,
                    org_id=current.org_id,
                    workspace_id=current.workspace_id,
                )
                await repo.request_cancel(
                    conversation_id=current.conversation_id,
                    client_steer_id=current.delivery_steer_id,
                )
            current.delivery_steer_id = None
            if active is not None:
                await _defer_wake(session, current, now=now)
                return False

        if current.delivery_run_id:
            meta = await get_run_meta(
                redis,
                prefix=redis_key_prefix,
                run_id=current.delivery_run_id,
            )
            if meta is not None and meta.status == "running":
                from cubeplex.config import config

                stale_threshold = int(config.get("lifecycle.stale_run_threshold_seconds", 180))
                if is_stale_meta(meta, threshold_seconds=stale_threshold, now=now):
                    marked_stale = await mark_run_stale(
                        redis,
                        prefix=redis_key_prefix,
                        run_id=current.delivery_run_id,
                        conversation_id=current.conversation_id,
                        observed_last_event_at=meta.last_event_at or meta.started_at,
                    )
                    if marked_stale:
                        active = await get_active_run(
                            redis,
                            prefix=redis_key_prefix,
                            conversation_id=current.conversation_id,
                        )
                        meta = await get_run_meta(
                            redis,
                            prefix=redis_key_prefix,
                            run_id=current.delivery_run_id,
                        )
            if meta is not None and meta.status in ("running", "paused_hitl"):
                current.owner_until = now + timedelta(seconds=WAKE_RETRY_SECONDS)
                session.add(current)
                await session.commit()
                return False
            current.delivery_run_id = None

        if active is not None and active.status == "paused_hitl":
            await _defer_wake(session, current, now=now)
            return False

        command = await session.get(SandboxCommand, current.command_id)
        content = _wake_message(
            current,
            description=command.description if command is not None else "",
        )
        if active is not None and active.status == "running":
            from cubeplex.repositories.steering_message import SteeringMessageRepository

            steer_id = _wake_steer_id(current.id, active.run_id)
            current.delivery_steer_id = steer_id
            current.owner_until = now + timedelta(seconds=WAKE_RETRY_SECONDS)
            session.add(current)
            if command is not None:
                command.notify_run_id = active.run_id
                session.add(command)
            repo = SteeringMessageRepository(
                session,
                org_id=current.org_id,
                workspace_id=current.workspace_id,
            )
            await repo.enqueue(
                conversation_id=current.conversation_id,
                run_id=active.run_id,
                client_steer_id=steer_id,
                content=content,
                sender_user_id=current.started_by_user_id,
                sender_display_name=None,
                hitl_question_id=f"sandbox-wake:{current.id}",
            )
            await session.commit()
            try:
                await run_manager.drain_durable_steering(active.run_id)
            except RuntimeError:
                await _defer_wake(session, current, now=now)
            return False

        topic = await session.get(Topic, conversation.topic_id) if conversation.topic_id else None
        delivery_run_id = current.delivery_run_id or str(uuid7())
        current.delivery_run_id = delivery_run_id
        current.owner_until = now + timedelta(seconds=WAKE_LEASE_SECONDS)
        session.add(current)
        if command is not None:
            command.notify_run_id = delivery_run_id
            session.add(command)
        await session.commit()

    ctx = RunContext(
        user_id=wake.started_by_user_id,
        org_id=wake.org_id,
        workspace_id=wake.workspace_id,
        conversation_id=wake.conversation_id,
        trigger="automated",
        topic_id=conversation.topic_id,
        is_group_chat=conversation.is_group_chat,
        sender_display_name=None,
        sandbox_mode=topic.sandbox_mode if topic is not None else None,
        topic_creator_user_id=topic.creator_user_id if topic is not None else None,
        conversation_creator_user_id=conversation.creator_user_id,
    )
    try:
        await run_manager.start_run(
            conversation_id=wake.conversation_id,
            content=content,
            attachments=[],
            ctx=ctx,
            run_id=delivery_run_id,
            model_key=conversation.model_key,
            reasoning=ReasoningControl.model_validate(conversation.reasoning),
            input_metadata={"notice_id": wake.id, "command_id": wake.command_id},
        )
    except RuntimeError:
        async with session_factory() as session:
            current = await session.get(SandboxCommandWake, wake.id)
            if current is not None and current.owner_id == owner_id:
                await _defer_wake(session, current, now=now)
        return False
    return False


async def _defer_wake(
    session: AsyncSession,
    wake: SandboxCommandWake,
    *,
    now: datetime,
) -> None:
    wake.state = SandboxCommandWakeState.claimed.value
    wake.owner_id = None
    wake.owner_until = now + timedelta(seconds=WAKE_RETRY_SECONDS)
    session.add(wake)
    await session.commit()


async def _finish_wake(
    session_factory: async_sessionmaker[AsyncSession],
    wake_id: str,
    *,
    owner_id: str,
) -> None:
    async with session_factory() as session:
        wake = await session.get(SandboxCommandWake, wake_id)
        if wake is None or wake.owner_id != owner_id:
            return
        wake.state = SandboxCommandWakeState.delivered.value
        wake.owner_id = None
        wake.owner_until = None
        session.add(wake)
        command = await session.get(SandboxCommand, wake.command_id)
        if command is not None and command.notice_state == SandboxCommandNoticeState.pending.value:
            command.notice_state = SandboxCommandNoticeState.delivered.value
            session.add(command)
        await session.commit()


async def _append_log(sandbox: Sandbox, path: str, text: str) -> None:
    if not text:
        return
    chunk_path = f"{path}.append-{uuid.uuid4().hex}"
    try:
        parent = path.rsplit("/", 1)[0] or "."
        await sandbox.upload([(chunk_path, text.encode())])
        result = await sandbox.execute(
            f"mkdir -p {shlex.quote(parent)} && "
            f"cat {shlex.quote(chunk_path)} >> {shlex.quote(path)} && "
            f"rm -f {shlex.quote(chunk_path)}",
            timeout=30,
        )
        if result.exit_code not in (0, None):
            raise RuntimeError(f"append exited with {result.exit_code}")
    except Exception:
        logger.exception("failed to append sandbox command log {}", path)
        try:
            await sandbox.execute(f"rm -f {shlex.quote(chunk_path)}", timeout=30)
        except Exception:
            logger.debug("failed to clean sandbox command log chunk {}", chunk_path)
