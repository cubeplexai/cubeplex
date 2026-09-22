"""Trigger repositories — scoped by (org_id, workspace_id)."""

from typing import Any, cast

from sqlalchemy.exc import IntegrityError
from sqlmodel import col

from cubeplex.models.trigger import Trigger, TriggerEvent
from cubeplex.repositories.base import ScopedRepository


class TriggerRepository(ScopedRepository[Trigger]):
    model = Trigger

    def _scoped_select(self) -> Any:
        return super()._scoped_select().where(cast(Any, Trigger.deleted_at).is_(None))

    async def get_locked(self, trigger_id: str) -> Trigger | None:
        result = await self.session.execute(
            self._scoped_select()
            .where(col(Trigger.id) == trigger_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_enabled(self) -> list[Trigger]:
        stmt = self._scoped_select().where(Trigger.enabled.is_(True))  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_filtered(
        self,
        *,
        topic_id: str | None = None,
        im_account_id: str | None = None,
        im_channel_id: str | None = None,
    ) -> list[Trigger]:
        """List triggers with optional destination filters.

        Used by the sidebar / detail views to show "which triggers route into
        this topic / IM channel". Filters are AND-combined; unset filters are
        ignored.
        """
        stmt = self._scoped_select()
        if topic_id is not None:
            stmt = stmt.where(Trigger.topic_id == topic_id)
        if im_account_id is not None:
            stmt = stmt.where(Trigger.im_account_id == im_account_id)
        if im_channel_id is not None:
            stmt = stmt.where(Trigger.im_channel_id == im_channel_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_ingest(self, trigger_id: str) -> Trigger | None:
        """Return the trigger only if it exists AND is enabled.

        The ingest route uses this to collapse "missing" and "disabled"
        into a single 404 shape that doesn't leak existence.
        """
        stmt = self._scoped_select().where(
            Trigger.id == trigger_id,
            Trigger.enabled.is_(True),  # type: ignore[attr-defined]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class TriggerEventRepository(ScopedRepository[TriggerEvent]):
    model = TriggerEvent

    async def insert_dedup(self, event: TriggerEvent) -> TriggerEvent | None:
        """Insert event; return None if (trigger_id, dedup_key) conflict.

        The insert is flushed in a savepoint but not committed. This lets the
        ingest transaction add its immutable execution snapshot before a 202
        response can make the event externally accepted.
        """
        event.org_id = self.org_id
        event.workspace_id = self.workspace_id
        try:
            async with self.session.begin_nested():
                self.session.add(event)
                await self.session.flush()
        except IntegrityError:
            return None
        return event

    async def set_terminal(
        self,
        event_id: str,
        status: str,
        *,
        run_id: str | None = None,
        conversation_id: str | None = None,
        last_error: str | None = None,
    ) -> None:
        event = await self.get(event_id)
        if event is None:
            return
        event.status = status
        if run_id is not None:
            event.resulting_run_id = run_id
        if conversation_id is not None:
            event.resulting_conversation_id = conversation_id
        if last_error is not None:
            event.last_error = last_error
        await self.session.commit()

    async def list_for_trigger(
        self,
        trigger_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TriggerEvent]:
        stmt = self._scoped_select().where(TriggerEvent.trigger_id == trigger_id)
        if status is not None:
            stmt = stmt.where(TriggerEvent.status == status)
        stmt = (
            stmt.order_by(TriggerEvent.received_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
