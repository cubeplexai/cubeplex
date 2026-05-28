"""Copy a run's terminal outcome back onto its scheduled_task_runs row.

Keyed by run_id: interactive runs (no matching row) are a no-op. Best-effort —
never raises into the run-finalization path.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select

from cubebox.db.engine import async_session_maker
from cubebox.models.scheduled_task import ScheduledTaskRun

# RunManager status -> occurrence terminal state.
_TERMINAL_MAP = {"completed": "succeeded", "failed": "failed", "cancelled": "failed"}


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
                            ScheduledTaskRun.state == "started",  # type: ignore[arg-type]
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
