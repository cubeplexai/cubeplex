"""Attachment repository."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from cubebox.models import Attachment
from cubebox.repositories.base import ScopedRepository


class AttachmentRepository(ScopedRepository[Attachment]):
    """CRUD + state-machine ops for Attachment."""

    model = Attachment

    async def get_by_id(self, attachment_id: str) -> Attachment | None:
        return await self.get(attachment_id)

    async def get_in_conversation(
        self, *, conversation_id: str, attachment_id: str
    ) -> Attachment | None:
        stmt = self._scoped_select().where(
            Attachment.id == attachment_id,
            Attachment.conversation_id == conversation_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_conversation(
        self, *, conversation_id: str, status: str | None = None
    ) -> list[Attachment]:
        stmt = (
            self._scoped_select()
            .where(Attachment.conversation_id == conversation_id)
            .order_by(Attachment.created_at)
        )
        if status is not None:
            stmt = stmt.where(Attachment.status == status)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_sandbox_path(self, sandbox_path: str) -> Attachment | None:
        stmt = self._scoped_select().where(Attachment.sandbox_path == sandbox_path)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def sum_active_size(self, conversation_id: str) -> int:
        tbl = Attachment.__table__  # type: ignore[attr-defined]
        stmt = select(func.coalesce(func.sum(tbl.c.size_bytes), 0)).where(
            tbl.c.org_id == self.org_id,
            tbl.c.workspace_id == self.workspace_id,
            tbl.c.conversation_id == conversation_id,
            tbl.c.status.in_(["pending", "attached"]),
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def mark_attached_bulk(self, *, conversation_id: str, attachment_ids: list[str]) -> int:
        """Set status='attached', attached_at=now() for pending rows. Idempotent.

        Returns number of rows newly transitioned.
        """
        if not attachment_ids:
            return 0
        rows = await self.list_by_conversation(conversation_id=conversation_id)
        now = datetime.now(UTC)
        n = 0
        target = set(attachment_ids)
        for row in rows:
            if row.id in target and row.status == "pending":
                row.status = "attached"
                row.attached_at = now
                row.updated_at = now
                n += 1
        await self.session.commit()
        return n

    async def list_orphans(self, *, older_than_seconds: int) -> list[Attachment]:
        """Pending attachments older than threshold within current scope."""
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        stmt = self._scoped_select().where(
            Attachment.status == "pending",
            Attachment.created_at < cutoff,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
