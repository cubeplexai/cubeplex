"""Copy a run's terminal outcome back onto its scheduled_task_runs row.

Keyed by run_id: interactive runs (no matching row) are a no-op. Best-effort —
never raises into the run-finalization path.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select

from cubeplex.db.engine import async_session_maker
from cubeplex.models.scheduled_task import ScheduledTaskRun

# RunManager status -> occurrence terminal state.
_TERMINAL_MAP = {"completed": "succeeded", "failed": "failed", "cancelled": "failed"}

# Non-terminal states the hook may overwrite. ``claimed`` is included because
# the poller pre-stamps ``run_id`` on the row while it is still ``claimed``
# (state flips to ``started`` only after the post-dispatch UPDATE commits).
# A very short run can finish and call this hook before that UPDATE lands,
# in which case the row matched by ``run_id`` is still ``claimed`` — we
# still flip it to terminal so history is not stuck. The poller's
# post-dispatch UPDATE is conditional (only flips ``claimed`` → ``started``,
# never re-flips a terminal row), so this and the poller don't fight.
_NON_TERMINAL = ("claimed", "started")


async def record_scheduled_run_terminal_state(*, run_id: str, run_status: str) -> None:
    new_state = _TERMINAL_MAP.get(run_status)
    if new_state is None:
        return
    try:
        async with async_session_maker() as session:
            row: ScheduledTaskRun | None = (
                (
                    await session.execute(
                        select(ScheduledTaskRun).where(
                            ScheduledTaskRun.run_id == run_id,  # type: ignore[arg-type]
                            ScheduledTaskRun.state.in_(_NON_TERMINAL),  # type: ignore[attr-defined]
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return
            row.state = new_state
            row.detail = None if new_state == "succeeded" else f"run {run_status}"
            row.updated_at = datetime.now(UTC)
            await session.commit()
    except Exception as exc:
        logger.warning("scheduled-run completion hook failed for {}: {}", run_id, exc)
