"""FastAPI dependency for database sessions."""

import asyncio
from collections.abc import AsyncIterator

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.db.engine import async_session_maker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency to get database session.

    Usage:
        @router.get("/items")
        async def list_items(session: AsyncSession = Depends(get_session)):
            ...
    """
    async with async_session_maker() as session:
        try:
            yield session
        except asyncio.CancelledError:
            # 请求被取消（客户端断开连接等），优雅关闭会话
            logger.debug("Database session cancelled, rolling back transaction")
            await session.rollback()
            raise
        except Exception:
            # 其他异常，回滚事务
            await session.rollback()
            raise
