"""UserManager and fastapi_users instance."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers
from fastapi_users.db import SQLAlchemyUserDatabase
from loguru import logger

from cubebox.auth.db import get_user_db
from cubebox.auth.jwt import auth_backend
from cubebox.config import config
from cubebox.models import User


class UserManager(BaseUserManager[User, str]):
    reset_password_token_secret = config.get("auth.jwt_secret", "CHANGE_ME")
    verification_token_secret = config.get("auth.jwt_secret", "CHANGE_ME")

    def parse_id(self, value: object) -> str:
        # uuid7 strings, not UUIDs
        return str(value)

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        logger.info("User registered: {}", user.email)


async def get_user_manager(
    user_db: Annotated[SQLAlchemyUserDatabase[User, str], Depends(get_user_db)],
) -> AsyncIterator[UserManager]:
    yield UserManager(user_db)


fastapi_users = FastAPIUsers[User, str](get_user_manager, [auth_backend])
