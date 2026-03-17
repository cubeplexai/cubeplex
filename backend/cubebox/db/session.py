"""FastAPI dependency for database sessions."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.db.engine import async_session_maker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency to get database session.

    Usage:
        @router.get("/items")
        async def list_items(session: AsyncSession = Depends(get_session)):
            ...
    """
    async with async_session_maker() as session:
        yield session
