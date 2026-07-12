"""SQLAlchemy adapter for fastapi-users."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.db import get_session
from cubeplex.models import User


async def get_user_db(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AsyncIterator[SQLAlchemyUserDatabase]:  # type: ignore[type-arg]
    yield SQLAlchemyUserDatabase(session, User)
