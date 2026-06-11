"""FastAPI dependencies for auth + scoping."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, cast

from fastapi import Depends, HTTPException, Path, Request, status
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.db import get_session
from cubebox.llm.errors import AmbiguousOrgError
from cubebox.models import Membership, OrganizationMembership, OrgRole, Role, User, Workspace
from cubebox.plugins import PermissionChecker, PermissionResource, get_registry
from cubebox.plugins.defaults.permissions import DefaultPermissionChecker
from cubebox.repositories import MembershipRepository, WorkspaceRepository


async def current_active_user(request: Request) -> User:
    """Resolve the active user via the configured AuthProvider.

    CE default = fastapi-users JWT cookie. EE plugins (e.g. SAML) override.
    """
    user = await get_registry().get_auth_provider().authenticate(request)  # type: ignore[attr-defined]
    if user is None or not getattr(user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user  # type: ignore[no-any-return]


async def optional_current_user(request: Request) -> User | None:
    """Like current_active_user but returns None instead of 401."""
    try:
        user = await get_registry().get_auth_provider().authenticate(request)  # type: ignore[attr-defined]
    except Exception:
        return None
    if user is None or not getattr(user, "is_active", True):
        return None
    return user  # type: ignore[no-any-return]


async def request_context(
    workspace_id: Annotated[str, Path()],
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RequestContext:
    """Resolve the active workspace + role from URL path + membership lookup.

    Workspace scoping is encoded in the URL (`/api/v1/ws/{workspace_id}/...`);
    every business endpoint declares `workspace_id` as a path parameter and
    depends on this function. There is no header fallback — the path is the
    single source of truth.
    """
    ws_repo = WorkspaceRepository(session)
    workspace = await ws_repo.get(workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace '{workspace_id}' not found",
        )

    mem_repo = MembershipRepository(session)
    role = await mem_repo.get_role(user_id=user.id, workspace_id=workspace_id)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this workspace",
        )

    return RequestContext(user=user, org_id=workspace.org_id, workspace_id=workspace_id, role=role)


def _action_for_roles(allowed: tuple[Role, ...]) -> str:
    """Map allowed-role set → action name for PermissionChecker."""
    s = set(allowed)
    if s == {Role.ADMIN}:
        return "admin_access"
    if s == {Role.ADMIN, Role.MEMBER}:
        return "member_access"
    raise NotImplementedError(f"role set {s} has no mapped action")


def require_role(
    *allowed: Role,
) -> Callable[..., Awaitable[RequestContext]]:
    """Dependency factory: enforce permission via PermissionChecker."""

    action = _action_for_roles(allowed)

    async def _check(
        ctx: Annotated[RequestContext, Depends(request_context)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> RequestContext:
        checker = cast(PermissionChecker, get_registry().get_permission_checker())
        # CE default needs a session-bound repo factory at call time.
        if isinstance(checker, DefaultPermissionChecker):
            checker._repo_factory = lambda _s: MembershipRepository(session)
        resource = PermissionResource(
            type="workspace",
            id=ctx.workspace_id,  # type: ignore[arg-type]
            workspace_id=ctx.workspace_id,  # type: ignore[arg-type]
        )
        if not await checker.check(ctx.user, action, resource):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: action={action}",
            )
        return ctx

    return _check


require_admin = require_role(Role.ADMIN)
require_member = require_role(Role.ADMIN, Role.MEMBER)


async def resolve_unambiguous_admin_org_id(user: User, session: AsyncSession) -> str:
    """Like ``resolve_current_org_id``, but raises when the user is admin of
    more than one org and no explicit org_id was selected.

    Admin routes don't carry a workspace path segment, so there is no other
    structural source of truth for which org the call targets. Silently
    picking the highest-role / oldest membership (as ``resolve_current_org_id``
    does) can route a write to the wrong org. This helper opts into a hard
    400 — surfaced via :class:`AmbiguousOrgError` — so the frontend can ask
    the admin to disambiguate.

    Single-org admins are the common case (single_tenant deployments and
    most multi_tenant accounts) and continue to work transparently.
    """
    admin_roles = (OrgRole.OWNER.value, OrgRole.ADMIN.value)
    role_col = cast(Any, OrganizationMembership.role)
    stmt = (
        select(OrganizationMembership)
        .where(OrganizationMembership.user_id == user.id)  # type: ignore[arg-type]
        .where(role_col.in_(admin_roles))
    )
    admin_oms = list((await session.execute(stmt)).scalars().all())
    if len(admin_oms) > 1:
        raise AmbiguousOrgError(org_ids=[om.org_id for om in admin_oms])
    return await resolve_current_org_id(user, session)


async def resolve_current_org_id(user: User, session: AsyncSession) -> str:
    """Resolve the user's current org from `organization_memberships`.

    A user may belong to multiple orgs once cross-org workspace membership is
    in play. We prefer the org where the user has the highest role
    (owner > admin > member), then the oldest membership as a stable tiebreaker.

    Falls back to the user's oldest workspace's org (for legacy data where the
    org_memberships row may be missing) before giving up with 403.
    """
    role_priority = case(
        (OrganizationMembership.role == OrgRole.OWNER.value, 0),  # type: ignore[arg-type]
        (OrganizationMembership.role == OrgRole.ADMIN.value, 1),  # type: ignore[arg-type]
        else_=2,
    )
    om_stmt = (
        select(OrganizationMembership)
        .where(OrganizationMembership.user_id == user.id)  # type: ignore[arg-type]
        .order_by(role_priority, OrganizationMembership.created_at)  # type: ignore[arg-type]
        .limit(1)
    )
    om = (await session.execute(om_stmt)).scalars().first()
    if om is not None:
        return om.org_id

    ws_stmt = (
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)  # type: ignore[arg-type]
        .where(Membership.user_id == user.id)  # type: ignore[arg-type]
        .order_by(Workspace.created_at)  # type: ignore[arg-type]
        .limit(1)
    )
    workspace = (await session.execute(ws_stmt)).scalars().first()
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No org membership found for user",
        )
    return workspace.org_id


async def require_org_admin(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """User has org-level admin or owner role in their current org."""
    from cubebox.repositories import OrganizationMembershipRepository

    org_id = await resolve_current_org_id(user, session)
    is_admin = await OrganizationMembershipRepository(session).is_admin(
        user_id=user.id, org_id=org_id
    )
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Org admin role required",
        )
    return user
