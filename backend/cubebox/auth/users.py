"""UserManager and fastapi_users instance."""

import re
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request, Response
from fastapi_users import BaseUserManager, FastAPIUsers
from fastapi_users.db import SQLAlchemyUserDatabase
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.db import get_user_db
from cubebox.auth.jwt import auth_backend
from cubebox.config import config
from cubebox.models import User

_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_SLUG_DEDUP = re.compile(r"-{2,}")
_SLUG_MAX = 31


def _slugify_org_name(name: str) -> str:
    """Convert an org name → URL-safe slug (max 31 chars)."""
    lowered = name.strip().lower()
    raw = _SLUG_RE.sub("-", lowered)
    deduped = _SLUG_DEDUP.sub("-", raw).strip("-")
    if not deduped:
        return "org"
    return deduped[:_SLUG_MAX].rstrip("-")


async def _allocate_org_slug(session: AsyncSession, base: str) -> str:
    """Pick a slug not already taken; append -2, -3, ... if needed."""
    import sqlalchemy as sa

    from cubebox.models.organization import Organization

    base = base[:27]  # reserve room for -NNN suffix within 32-char limit
    candidate = base
    n = 2
    while True:
        existing = await session.execute(
            sa.select(Organization).where(Organization.slug == candidate)  # type: ignore[arg-type]
        )
        if existing.scalar_one_or_none() is None:
            return candidate
        candidate = f"{base}-{n}"
        n += 1


async def _install_preinstalled_skills(session: AsyncSession, *, org_id: str, user_id: str) -> None:
    """Auto-install all non-deprecated preinstalled skills for a newly created org."""
    from cubebox.repositories.skill import OrgSkillInstallRepository, SkillRepository

    try:
        skills = await SkillRepository(session).list_visible_for_org(org_id, source="preinstalled")
    except Exception as e:
        logger.warning("Failed to list preinstalled skills: {}", e)
        return
    installs = OrgSkillInstallRepository(session)
    for skill in skills:
        try:
            await installs.upsert(
                org_id=org_id,
                skill_id=skill.id,
                installed_version=skill.current_version,
                installed_by_user_id=user_id,
                auto_bind=True,
            )
        except Exception:
            logger.warning(
                "Failed to auto-install preinstalled skill {} for org {}", skill.name, org_id
            )


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

        org = None
        try:
            local_part = user.email.split("@", 1)[0]
            org_name = f"{local_part}'s Org"
            slug = await _allocate_org_slug(session, _slugify_org_name(org_name))
            org = await OrganizationRepository(session).create(name=org_name, slug=slug)
            ws = await WorkspaceRepository(session).create(org_id=org.id, name="Personal")
            await MembershipRepository(session).grant(
                user_id=user.id, workspace_id=ws.id, role=Role.ADMIN
            )
            from cubebox.models import OrgRole
            from cubebox.repositories import OrganizationMembershipRepository

            await OrganizationMembershipRepository(session).grant(
                user_id=user.id, org_id=org.id, role=OrgRole.OWNER
            )
            from cubebox.models.agent_config import AgentConfig

            agent_cfg = AgentConfig(org_id=org.id, workspace_id=ws.id)
            session.add(agent_cfg)
            await session.flush()
        except Exception as exc:
            logger.exception(
                "register_bootstrap failed for user {} ({}): {!r}",
                user.email,
                user.id,
                exc,
            )
            # Repo create/grant methods commit internally, so org/ws rows may already
            # be persisted when bootstrap fails mid-flight. Best-effort DELETE of the
            # org and user rows here — and translate to a 500 so clients see an HTTP
            # error instead of an opaque 500 from the framework's default handler.
            from fastapi import HTTPException, status
            from sqlalchemy import delete

            from cubebox.models import User as UserModel
            from cubebox.models.organization import Organization

            try:
                if org is not None:
                    await session.execute(
                        delete(Organization).where(Organization.id == org.id)  # type: ignore[arg-type]
                    )
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

        try:
            await _install_preinstalled_skills(session, org_id=org.id, user_id=user.id)
        except Exception:
            logger.warning(
                "Failed to auto-install preinstalled skills for new org {}; skipping",
                org.id,
            )

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
