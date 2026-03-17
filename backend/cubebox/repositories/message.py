"""Message repository."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import Message


class MessageRepository:
    """Repository for Message CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        conversation_id: str,
        role: str,
        content: str | None = None,
        events: list[dict[str, object]] | None = None,
    ) -> Message:
        """Create a new message."""
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            events=events,
        )
        self.session.add(message)
        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def get_by_id(self, message_id: str) -> Message | None:
        """Get message by ID."""
        stmt = select(Message).where(Message.id == message_id)  # type: ignore[arg-type]
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_conversation(
        self, conversation_id: str, limit: int = 100, offset: int = 0
    ) -> tuple[list[Message], int]:
        """List messages for a conversation with pagination.

        Returns:
            Tuple of (messages, total_count)
        """
        # Get paginated results ordered by created_at
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)  # type: ignore[arg-type]
            .order_by(Message.created_at)  # type: ignore[arg-type]
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        messages = list(result.scalars().all())

        # Get total count
        count_stmt = (
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)  # type: ignore[arg-type]
        )
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        return messages, total

    async def delete(self, message_id: str) -> bool:
        """Delete a message."""
        message = await self.get_by_id(message_id)
        if not message:
            return False

        await self.session.delete(message)
        await self.session.commit()
        return True
