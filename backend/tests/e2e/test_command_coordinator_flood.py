"""The pre-cutover monitor still stops a flood after disabling line notifications."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import SandboxCommand
from cubeplex.sandbox.base import ProcessHandle, ProcessSnapshot
from cubeplex.sandbox.command_coordinator import MAX_LINE_WAKES, reconcile_once
from cubeplex.sandbox.local import LocalSandbox
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.test_background_task_reservation import ReservationContext

reservation_context = reservation_fixtures.reservation_context


async def test_disabled_line_wakes_still_kill_sustained_output_flood(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    class FloodingSandbox(LocalSandbox):
        async def poll(self, handle: ProcessHandle) -> ProcessSnapshot:
            snapshot = await super().poll(handle)
            if snapshot.status == "running":
                snapshot.new_output = "one\ntwo\nthree\n"
            return snapshot

    sandbox = FloodingSandbox()
    original_handle = await sandbox.start("sleep 30")
    start = datetime.now(UTC)
    row = SandboxCommand(
        org_id=reservation_fixtures.DEFAULT_ORG_ID,
        workspace_id=reservation_fixtures.DEFAULT_WS_ID,
        user_sandbox_id=reservation_context.details.user_sandbox_id,
        conversation_id=reservation_context.conversation_id,
        run_id=reservation_context.spec.originating_run_id,
        started_by_user_id="legacy-observer",
        command="sleep 30",
        provider="local",
        provider_ref=original_handle.provider_ref,
        status="running",
        kind="monitor",
        lifetime="conversation",
        owner_id="dead-worker",
        owner_until=start - timedelta(seconds=30),
        wake_count=MAX_LINE_WAKES,
        line_wakes_disabled=True,
    )

    async def get_sandbox(command: SandboxCommand) -> LocalSandbox:
        return sandbox

    row_id = row.id
    try:
        db_session.add(row)
        await db_session.commit()
        await reconcile_once(db_session, get_sandbox=get_sandbox, now=start)
        await db_session.refresh(row)
        assert row.flood_started_at == start
        finished = await reconcile_once(
            db_session, get_sandbox=get_sandbox, now=start + timedelta(seconds=31)
        )
        await db_session.refresh(row)
        assert finished == [row.id] and row.status == "killed"
        observed = await sandbox.observe(original_handle)
        assert observed.status == "killed"
        assert observed.exit_code is not None and observed.exit_code < 0
    finally:
        # The legacy writer clears provider_ref on Stop; retain the actual handle.
        await sandbox.kill(original_handle)
        from sqlalchemy import delete
        from sqlmodel import col

        from cubeplex.models.sandbox_command import SandboxCommandWake

        await db_session.rollback()
        await db_session.execute(
            delete(SandboxCommandWake).where(col(SandboxCommandWake.command_id) == row_id)
        )
        await db_session.commit()
