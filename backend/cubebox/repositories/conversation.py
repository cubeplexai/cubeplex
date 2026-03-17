"""Conversation repository."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import desc

from cubebox.models import Conversation


class ConversationRepository:
    """Repository for Conversation CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, title: str) -> Conversation:
        """Create a new conversation."""
        conversation = Conversation(title=title)
        self.session.add(conversation)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def get_by_id(self, conversation_id: str) -> Conversation | None:
        """Get conversation by ID."""
        stmt = select(Conversation).where(Conversation.id == conversation_id)  # type: ignore[arg-type]
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self, limit: int = 20, offset: int = 0) -> tuple[list[Conversation], int]:
        """List conversations with pagination.

        Returns:
            Tuple of (conversations, total_count)
        """
        # Get paginated results
        result = await self.session.execute(
            select(Conversation).order_by(desc(Conversation.updated_at)).limit(limit).offset(offset)
        )
        conversations = list(result.scalars().all())

        # Get total count
        count_result = await self.session.execute(select(func.count()).select_from(Conversation))
        total = count_result.scalar_one()

        return conversations, total

    async def update_title(self, conversation_id: str, title: str) -> Conversation | None:
        """Update conversation title."""
        conversation = await self.get_by_id(conversation_id)
        if not conversation:
            return None

        conversation.title = title
        conversation.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def update_timestamp(self, conversation_id: str) -> None:
        """Update conversation updated_at timestamp."""
        conversation = await self.get_by_id(conversation_id)
        if conversation:
            conversation.updated_at = datetime.now(UTC)
            await self.session.commit()

    async def delete(self, conversation_id: str) -> bool:
        """Delete conversation and cascade delete messages."""
        conversation = await self.get_by_id(conversation_id)
        if not conversation:
            return False

        await self.session.delete(conversation)
        await self.session.commit()
        return True
