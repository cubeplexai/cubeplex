"""PresentedFile repository."""

from sqlalchemy import func, select

from cubeplex.models.presented_file import PresentedFile
from cubeplex.repositories.base import ScopedRepository


class PresentedFileRepository(ScopedRepository[PresentedFile]):
    """CRUD for conversation-scoped presented files."""

    model = PresentedFile

    async def get_in_conversation(
        self, *, conversation_id: str, presented_file_id: str
    ) -> PresentedFile | None:
        stmt = self._scoped_select().where(
            PresentedFile.id == presented_file_id,
            PresentedFile.conversation_id == conversation_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_conversation(self, *, conversation_id: str) -> list[PresentedFile]:
        stmt = (
            self._scoped_select()
            .where(PresentedFile.conversation_id == conversation_id)
            .order_by(PresentedFile.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def sum_size(self, conversation_id: str) -> int:
        tbl = PresentedFile.__table__  # type: ignore[attr-defined]
        stmt = select(func.coalesce(func.sum(tbl.c.size_bytes), 0)).where(
            tbl.c.org_id == self.org_id,
            tbl.c.workspace_id == self.workspace_id,
            tbl.c.conversation_id == conversation_id,
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())
