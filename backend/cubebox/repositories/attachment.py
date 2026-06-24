"""Attachment repository."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from cubebox.models import Attachment
from cubebox.repositories.base import ScopedRepository

# Max ancestor hops walked by ``get_with_fork_fallback``. A request that has
# climbed past this many forks-of-forks is either pathological or someone
# probing for an exfiltration loop — short-circuit with a 404.
_FORK_WALK_MAX_HOPS = 8


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

    async def get_with_fork_fallback(
        self, *, conversation_id: str, attachment_id: str
    ) -> Attachment | None:
        """Resolve an attachment, walking up the fork chain on miss.

        A forked conversation's cloned cubepi messages still reference the
        SOURCE conversation's attachment ids (the messages were copied
        verbatim, and Attachment rows are PK'd by id under a single
        conversation_id — no per-fork clone, no row aliasing). Without
        this fallback every image / file in a fork would 404 on first
        render.

        Walks ``cubepi_threads.parent_thread_id`` up to ``_FORK_WALK_MAX_HOPS``
        ancestors, retrying the per-conversation lookup at each step. The
        workspace scope from ``_scoped_select`` is preserved at every hop,
        so this can never reach an attachment outside the caller's
        workspace.
        """
        row = await self.get_in_conversation(
            conversation_id=conversation_id, attachment_id=attachment_id
        )
        if row is not None:
            return row
        # Climb the fork chain.  Direct SQL against the cubepi-owned table
        # (no SQLModel for it on the cubebox side) — kept to a single
        # column SELECT so the coupling is minimal and obvious.
        current = conversation_id
        seen: set[str] = {current}
        for _ in range(_FORK_WALK_MAX_HOPS):
            result = await self.session.execute(
                text("SELECT parent_thread_id FROM cubepi_threads WHERE thread_id = :tid"),
                {"tid": current},
            )
            parent = result.scalar_one_or_none()
            if not parent or parent in seen:
                return None
            seen.add(parent)
            current = parent
            row = await self.get_in_conversation(
                conversation_id=current, attachment_id=attachment_id
            )
            if row is not None:
                return row
        return None

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
