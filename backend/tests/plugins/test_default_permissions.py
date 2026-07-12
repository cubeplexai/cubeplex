from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cubeplex.models import Role
from cubeplex.plugins import PermissionChecker, PermissionResource
from cubeplex.plugins.defaults.permissions import DefaultPermissionChecker


def test_default_permission_checker_satisfies_protocol() -> None:
    assert isinstance(DefaultPermissionChecker(), PermissionChecker)


@pytest.mark.asyncio
async def test_admin_access_grants_admin_role() -> None:
    repo = AsyncMock()
    repo.get_role = AsyncMock(return_value=Role.ADMIN)
    checker = DefaultPermissionChecker(membership_repo_factory=lambda _s: repo)
    user = MagicMock(id=str(uuid4()))
    ws_id = uuid4()
    res = PermissionResource(type="workspace", id=ws_id, workspace_id=ws_id)
    assert await checker.check(user, "admin_access", res) is True


@pytest.mark.asyncio
async def test_admin_access_denies_member_role() -> None:
    repo = AsyncMock()
    repo.get_role = AsyncMock(return_value=Role.MEMBER)
    checker = DefaultPermissionChecker(membership_repo_factory=lambda _s: repo)
    user = MagicMock(id=str(uuid4()))
    ws_id = uuid4()
    res = PermissionResource(type="workspace", id=ws_id, workspace_id=ws_id)
    assert await checker.check(user, "admin_access", res) is False


@pytest.mark.asyncio
async def test_member_access_grants_admin_or_member() -> None:
    repo = AsyncMock()
    repo.get_role = AsyncMock(return_value=Role.MEMBER)
    checker = DefaultPermissionChecker(membership_repo_factory=lambda _s: repo)
    user = MagicMock(id=str(uuid4()))
    ws_id = uuid4()
    res = PermissionResource(type="workspace", id=ws_id, workspace_id=ws_id)
    assert await checker.check(user, "member_access", res) is True


@pytest.mark.asyncio
async def test_unknown_action_denies() -> None:
    repo = AsyncMock()
    repo.get_role = AsyncMock(return_value=Role.ADMIN)
    checker = DefaultPermissionChecker(membership_repo_factory=lambda _s: repo)
    user = MagicMock(id=str(uuid4()))
    ws_id = uuid4()
    res = PermissionResource(type="workspace", id=ws_id, workspace_id=ws_id)
    assert await checker.check(user, "delete_workspace", res) is False
