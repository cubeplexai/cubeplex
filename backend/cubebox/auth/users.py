"""UserManager and fastapi_users instance."""

import re
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi_users import BaseUserManager, FastAPIUsers
from fastapi_users.db import SQLAlchemyUserDatabase
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.db import get_user_db
from cubebox.auth.jwt import auth_backend
from cubebox.config import config
from cubebox.models import User, Workspace
from cubebox.models.organization import Organization

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


async def _install_preinstalled_skills_safe(
    session: AsyncSession, *, org_id: str, user_id: str
) -> None:
    """Wrap _install_preinstalled_skills; log and swallow on failure."""
    try:
        await _install_preinstalled_skills(session, org_id=org_id, user_id=user_id)
    except Exception:
        logger.warning(
            "Failed to auto-install preinstalled skills for new org {}; skipping",
            org_id,
        )


async def _bootstrap_org_and_workspace(
    session: AsyncSession,
    *,
    user_id: str,
    org_name: str,
    org_slug: str,
    workspace_name: str,
) -> tuple[Organization, Workspace]:
    """Full first-owner bootstrap: org + workspace + memberships + AgentConfig + MCP + skills."""
    from cubebox.mcp.workspace_bootstrap import enroll_workspace_in_org_wide_mcp
    from cubebox.models import OrgRole, Role
    from cubebox.models.agent_config import AgentConfig
    from cubebox.repositories import (
        MembershipRepository,
        OrganizationMembershipRepository,
        OrganizationRepository,
        WorkspaceRepository,
    )

    org = await OrganizationRepository(session).create(name=org_name, slug=org_slug)
    await OrganizationMembershipRepository(session).grant(
        user_id=user_id, org_id=org.id, role=OrgRole.OWNER
    )
    ws = await WorkspaceRepository(session).create(org_id=org.id, name=workspace_name)
    await MembershipRepository(session).grant(user_id=user_id, workspace_id=ws.id, role=Role.ADMIN)
    session.add(AgentConfig(org_id=org.id, workspace_id=ws.id))
    await enroll_workspace_in_org_wide_mcp(
        session, org_id=org.id, workspace_id=ws.id, actor_user_id=user_id
    )
    await session.flush()
    await _install_preinstalled_skills_safe(session, org_id=org.id, user_id=user_id)
    return org, ws


async def _bootstrap_workspace_in_org(
    session: AsyncSession,
    *,
    user_id: str,
    org_id: str,
    workspace_name: str,
) -> Workspace:
    """Create a workspace in an existing org for a user who already has an org membership."""
    from cubebox.mcp.workspace_bootstrap import enroll_workspace_in_org_wide_mcp
    from cubebox.models import Role
    from cubebox.models.agent_config import AgentConfig
    from cubebox.repositories import MembershipRepository, WorkspaceRepository

    ws = await WorkspaceRepository(session).create(org_id=org_id, name=workspace_name)
    await MembershipRepository(session).grant(user_id=user_id, workspace_id=ws.id, role=Role.ADMIN)
    session.add(AgentConfig(org_id=org_id, workspace_id=ws.id))
    await enroll_workspace_in_org_wide_mcp(
        session, org_id=org_id, workspace_id=ws.id, actor_user_id=user_id
    )
    await session.flush()
    await _install_preinstalled_skills_safe(session, org_id=org_id, user_id=user_id)
    return ws


class UserManager(BaseUserManager[User, str]):
    reset_password_token_secret = config.get("auth.jwt_secret", "CHANGE_ME")
    verification_token_secret = config.get("auth.jwt_secret", "CHANGE_ME")

    def parse_id(self, value: object) -> str:
        # uuid7 strings, not UUIDs
        return str(value)

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        logger.info("User registered: {}", user.email)
        session = self.user_db.session  # type: ignore[attr-defined]

        from cubebox.auth.singleton_org import (
            acquire_setup_lock,
            get_singleton_org_id,
            org_count,
            user_count,
        )

        # When called without an HTTP request (e.g. CLI / test fixtures), default to
        # multi_tenant so programmatic user creation always performs a full bootstrap.
        mode = "multi_tenant"
        if request is not None:
            mode = getattr(request.app.state, "deployment_mode", "multi_tenant")

        if mode == "single_tenant":
            await self._on_register_single_tenant(
                user=user,
                session=session,
                acquire_setup_lock=acquire_setup_lock,
                get_singleton_org_id=get_singleton_org_id,
                org_count_fn=org_count,
                user_count_fn=user_count,
            )
        else:
            await self._on_register_multi_tenant(user=user, session=session)

        from cubebox.plugins.audit import audit_log

        await audit_log(
            action="auth.register",
            user_id=user.id,
            ip=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )

        # Only send the initial verification email for self-registered
        # (password) users. SSO/social-login provisioning creates users with
        # is_verified=True (the IdP attested the email), so request_verify
        # would raise UserAlreadyVerified — and there's nothing to verify.
        if not user.is_verified:
            try:
                await self.request_verify(user, request)
            except Exception:
                logger.opt(exception=True).warning(
                    "Failed to send initial verification email to {}", user.email
                )

    async def _on_register_multi_tenant(self, *, user: User, session: AsyncSession) -> None:
        """multi_tenant: register creates the user only; onboarding bootstraps."""
        user._default_workspace_id = None

    async def _on_register_single_tenant(
        self,
        *,
        user: User,
        session: AsyncSession,
        acquire_setup_lock: object,
        get_singleton_org_id: object,
        org_count_fn: object,
        user_count_fn: object,
    ) -> None:
        """First user → pending owner; subsequent → attach to singleton org.

        The advisory lock guards against two concurrent /register requests both
        reading org_count == 0 and both treating themselves as the first user.
        The user_count check guards against sequential registrations where the
        first user is in pending-owner state (no org yet, but user row exists).
        """
        from collections.abc import Callable, Coroutine
        from typing import Any

        from cubebox.models import OrgRole
        from cubebox.repositories import OrganizationMembershipRepository

        # Type aliases to satisfy mypy — the callables are injected for testability
        _acquire_fn: Callable[[AsyncSession], Coroutine[Any, Any, bool]] = acquire_setup_lock  # type: ignore[assignment]
        _org_count_fn: Callable[[AsyncSession], Coroutine[Any, Any, int]] = org_count_fn  # type: ignore[assignment]
        _user_count_fn: Callable[[AsyncSession], Coroutine[Any, Any, int]] = user_count_fn  # type: ignore[assignment]
        _get_org_fn: Callable[[AsyncSession], Coroutine[Any, Any, str | None]] = (
            get_singleton_org_id  # type: ignore[assignment]
        )

        locked = await _acquire_fn(session)
        if not locked:
            await self._best_effort_cleanup_register(user=user, org=None, session=session)
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="setup_in_progress")

        count = await _org_count_fn(session)
        if count == 0:
            # No org yet. Check if another user is already in pending-owner state
            # (user_count includes the just-inserted user, so > 1 means someone else exists).
            ucount = await _user_count_fn(session)
            if ucount > 1:
                await self._best_effort_cleanup_register(user=user, org=None, session=session)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="setup_in_progress"
                )
            user._default_workspace_id = None
            return

        singleton_org_id = await _get_org_fn(session)
        if singleton_org_id is None:
            await self._best_effort_cleanup_register(user=user, org=None, session=session)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="REGISTER_BOOTSTRAP_FAILED",
            )

        try:
            await OrganizationMembershipRepository(session).grant(
                user_id=user.id, org_id=singleton_org_id, role=OrgRole.MEMBER
            )
            ws = await _bootstrap_workspace_in_org(
                session, user_id=user.id, org_id=singleton_org_id, workspace_name="Personal"
            )
        except Exception as exc:
            await self._best_effort_cleanup_register(user=user, org=None, session=session)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="REGISTER_BOOTSTRAP_FAILED",
            ) from exc
        user._default_workspace_id = ws.id

    async def _best_effort_cleanup_register(
        self,
        *,
        user: User,
        org: Organization | None,
        session: AsyncSession,
    ) -> None:
        """Best-effort delete of user (and org if provided) rows on registration failure."""
        from sqlalchemy import delete

        from cubebox.models import User as UserModel

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

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        from cubebox.services.email import get_email_service

        base_url = config.get("frontend_base_url", "http://localhost:3000")
        reset_url = f"{base_url}/reset-password?token={token}"
        try:
            await get_email_service().send(
                to=user.email,
                subject="Reset your cubebox password",
                template="password_reset",
                context={"reset_url": reset_url, "email": user.email},
            )
        except Exception:
            logger.warning("Failed to send password reset email to {}", user.email)

    async def on_after_request_verify(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        from cubebox.services.email import get_email_service

        base_url = config.get("frontend_base_url", "http://localhost:3000")
        verify_url = f"{base_url}/verify-email?token={token}"
        try:
            await get_email_service().send(
                to=user.email,
                subject="Verify your cubebox email",
                template="email_verification",
                context={"verify_url": verify_url},
            )
        except Exception:
            logger.warning("Failed to send verification email to {}", user.email)


async def get_user_manager(
    user_db: Annotated[SQLAlchemyUserDatabase[User, str], Depends(get_user_db)],
) -> AsyncIterator[UserManager]:
    yield UserManager(user_db)


fastapi_users = FastAPIUsers[User, str](get_user_manager, [auth_backend])
