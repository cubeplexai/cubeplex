"""CE default PermissionChecker: wraps existing Membership.get_role lookup."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from cubeplex.models import Role
from cubeplex.plugins.protocols import PermissionResource

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401

    from cubeplex.repositories import MembershipRepository


class DefaultPermissionChecker:
    """Maps known actions to Role checks; unknown actions deny."""

    def __init__(
        self,
        membership_repo_factory: Callable[[Any], MembershipRepository] | None = None,
    ) -> None:
        # Allow injection for tests; default obtains a fresh repo from current session.
        self._repo_factory = membership_repo_factory

    async def check(
        self,
        user: Any,
        action: str,
        resource: PermissionResource,
    ) -> bool:
        if resource.workspace_id is None:
            return False
        repo = self._get_repo()
        role = await repo.get_role(user_id=user.id, workspace_id=str(resource.workspace_id))
        if role is None:
            return False
        if action == "admin_access":
            return role == Role.ADMIN
        if action == "member_access":
            return role in (Role.ADMIN, Role.MEMBER)
        return False

    def _get_repo(self) -> MembershipRepository:
        if self._repo_factory is None:
            raise RuntimeError(
                "DefaultPermissionChecker requires a membership_repo_factory "
                "in production (FastAPI dependency injection)"
            )
        # In production this will be wired via FastAPI Depends; for tests it's injected.
        return self._repo_factory(None)
