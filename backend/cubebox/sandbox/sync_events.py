"""Persist a SyncResult: write one event row + on success, update the
UserSandbox snapshot in the SAME transaction.

Hot-path noop is the controller's responsibility (it must short-circuit
without calling ``record``). This service handles only success / failed.
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.models import UserSandbox, UserSandboxSyncEvent
from cubebox.sandbox.sync_result import SyncResult


class UserSandboxSyncEventService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(
        self,
        *,
        user_sandbox_id: str,
        org_id: str,
        workspace_id: str,
        result: SyncResult,
    ) -> None:
        if result.status == "noop":
            # Controller is responsible for short-circuit; defensive guard.
            return
        async with self._session_factory() as session:
            event = UserSandboxSyncEvent(
                org_id=org_id,
                workspace_id=workspace_id,
                user_sandbox_id=user_sandbox_id,
                started_at=result.started_at,
                finished_at=result.finished_at,
                status=result.status,
                manifest_snapshot=(result.manifest if result.status == "success" else None),
                n_pushed=result.n_pushed,
                n_removed=result.n_removed,
                tar_size_bytes=result.tar_size_bytes,
                error_type=result.error_type,
                error_message=result.error_message,
            )
            session.add(event)
            await session.flush()  # populate event.id

            if result.status == "success":
                await session.execute(
                    update(UserSandbox)
                    .where(UserSandbox.id == user_sandbox_id)  # type: ignore[arg-type]
                    .values(
                        skills_manifest_hash=result.manifest_hash,
                        skills_count=result.skills_count,
                        last_skill_sync_at=result.finished_at,
                        last_skill_sync_event_id=event.id,
                    )
                )
            await session.commit()
