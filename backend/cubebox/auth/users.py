"""UserManager and fastapi_users instance."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request, Response
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
        session = self.user_db.session  # type: ignore[attr-defined]
        from cubebox.models import Role
        from cubebox.repositories import (
            MembershipRepository,
            OrganizationRepository,
            WorkspaceRepository,
        )

        try:
            local_part = user.email.split("@", 1)[0]
            org = await OrganizationRepository(session).create(name=f"{local_part}'s Org")
            ws = await WorkspaceRepository(session).create(org_id=org.id, name="Personal")
            await MembershipRepository(session).grant(
                user_id=user.id, workspace_id=ws.id, role=Role.ADMIN
            )
        except Exception as exc:
            logger.exception(
                "register_bootstrap failed for user {} ({}): {!r}",
                user.email,
                user.id,
                exc,
            )
            # Repo create/grant methods commit internally, so org/ws rows may already
            # be persisted when bootstrap fails mid-flight. Best-effort DELETE of the
            # user row here — and translate to a 500 so clients see an HTTP error
            # instead of an opaque 500 from the framework's default handler.
            from fastapi import HTTPException, status
            from sqlalchemy import delete

            from cubebox.models import User as UserModel

            try:
                await session.execute(
                    delete(UserModel).where(UserModel.id == user.id)  # type: ignore[arg-type]
                )
                await session.commit()
            except Exception:
                await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="REGISTER_BOOTSTRAP_FAILED",
            ) from exc

        user._default_workspace_id = ws.id

        from cubebox.plugins.audit import audit_log

        await audit_log(
            action="auth.register",
            user_id=user.id,
            ip=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )

    async def on_after_login(
        self,
        user: User,
        request: Request | None = None,
        response: Response | None = None,
    ) -> None:
        from cubebox.plugins.audit import audit_log

        await audit_log(
            action="auth.login",
            user_id=user.id,
            ip=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )


async def get_user_manager(
    user_db: Annotated[SQLAlchemyUserDatabase[User, str], Depends(get_user_db)],
) -> AsyncIterator[UserManager]:
    yield UserManager(user_db)


fastapi_users = FastAPIUsers[User, str](get_user_manager, [auth_backend])
