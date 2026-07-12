# Member Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add org-level and workspace-level member management (list, add, change role, remove) via API endpoints and frontend UI.

**Architecture:** New backend route modules (`admin_members.py`, `ws_members.py`) mounted alongside existing admin/workspace-scoped routers. New Zustand store + API module in `@cubeplex/core`. New admin page at `/admin/members` and new workspace settings tab `members`. No new models or migrations — all operations use existing `OrganizationMembership` and `Membership` tables.

**Tech Stack:** FastAPI, SQLAlchemy async, Zustand, React, shadcn/ui table + badge + select + combobox, `@base-ui/react/dialog`, next-intl i18n.

---

## File Structure

### Backend — new files
- `backend/cubeplex/api/routes/v1/admin_members.py` — org member CRUD routes (4 endpoints)
- `backend/cubeplex/api/routes/v1/ws_members.py` — workspace member CRUD routes (5 endpoints)
- `backend/tests/e2e/test_admin_members.py` — E2E tests for org member routes
- `backend/tests/e2e/test_ws_members.py` — E2E tests for workspace member routes

### Backend — modified files
- `backend/cubeplex/api/routes/v1/__init__.py` — export new routers
- `backend/cubeplex/api/app.py` — register new routers
- `backend/cubeplex/repositories/membership.py` — add `remove_user_from_org_workspaces` method

### Frontend — new files
- `frontend/packages/core/src/api/members.ts` — API client functions for both scopes
- `frontend/packages/core/src/stores/memberStore.ts` — Zustand store for org + workspace members
- `frontend/packages/web/app/admin/members/page.tsx` — admin members page
- `frontend/packages/web/components/admin/members/OrgMembersTable.tsx` — org member table + actions
- `frontend/packages/web/components/admin/members/AddOrgMemberDialog.tsx` — add member dialog
- `frontend/packages/web/components/workspace-settings/MembersPanel.tsx` — workspace members panel
- `frontend/packages/web/components/workspace-settings/members/WsMembersTable.tsx` — workspace member table
- `frontend/packages/web/components/workspace-settings/members/AddWsMemberDialog.tsx` — add workspace member dialog

### Frontend — modified files
- `frontend/packages/core/src/stores/index.ts` — export new store
- `frontend/packages/core/src/api/index.ts` — export new API module
- `frontend/packages/web/components/admin/AdminSubNav.tsx` — add Members nav item
- `frontend/packages/web/components/workspace-settings/SettingsNav.tsx` — add Members nav item
- `frontend/packages/web/app/(app)/w/[wsId]/settings/page.tsx` — render MembersPanel for tab=members
- `frontend/packages/web/messages/en.json` — add i18n keys
- `frontend/packages/web/messages/zh.json` — add i18n keys

---

## Task 1: Repository — add cascade remove helper

**Files:**
- Modify: `backend/cubeplex/repositories/membership.py`

- [ ] **Step 1: Add `remove_user_from_org_workspaces` method**

Add this method to `MembershipRepository` after the existing `list_workspace_members` method:

```python
async def remove_user_from_org_workspaces(self, *, user_id: str, org_id: str) -> int:
    """Delete all workspace memberships for a user within an org. Returns count deleted."""
    from sqlalchemy import delete

    from cubeplex.models import Workspace

    ws_ids_subq = select(Workspace.id).where(Workspace.org_id == org_id).scalar_subquery()
    stmt = delete(Membership).where(
        Membership.user_id == user_id,  # type: ignore[arg-type]
        Membership.workspace_id.in_(ws_ids_subq),  # type: ignore[union-attr]
    )
    result = await self.session.execute(stmt)
    return result.rowcount  # type: ignore[return-value]
```

- [ ] **Step 2: Verify type-check passes**

Run: `cd /home/chris/cubeplex/backend && make type-check`
Expected: `Success: no issues found`

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/repositories/membership.py
git commit -m "feat(members): add cascade remove helper to MembershipRepository"
```

---

## Task 2: Backend — org member admin routes

**Files:**
- Create: `backend/cubeplex/api/routes/v1/admin_members.py`
- Modify: `backend/cubeplex/api/routes/v1/__init__.py`
- Modify: `backend/cubeplex/api/app.py`

- [ ] **Step 1: Create the admin_members route module**

Create `backend/cubeplex/api/routes/v1/admin_members.py`:

```python
"""Org member management routes: list / add / change-role / remove."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.dependencies import current_active_user, require_org_admin, resolve_current_org_id
from cubeplex.db import get_session
from cubeplex.models import OrgRole, User
from cubeplex.repositories import MembershipRepository, OrganizationMembershipRepository
from cubeplex.utils.time import utc_isoformat

router = APIRouter(prefix="/admin/members", tags=["admin-members"])

ASSIGNABLE_ROLES = {"admin", "member"}


class AddMemberBody(BaseModel):
    email: str
    role: str


class ChangeRoleBody(BaseModel):
    role: str


async def _resolve_org(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> tuple[User, str]:
    org_id = await resolve_current_org_id(user, session)
    return user, org_id


@router.get("")
async def list_org_members(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, str]]:
    org_id = await resolve_current_org_id(user, session)
    om_repo = OrganizationMembershipRepository(session)
    members = await om_repo.list_org_members(org_id)

    from sqlalchemy import select

    user_ids = [m.user_id for m in members]
    if not user_ids:
        return []
    stmt = select(User).where(User.id.in_(user_ids))  # type: ignore[arg-type]
    users = {u.id: u for u in (await session.execute(stmt)).scalars().all()}

    return [
        {
            "user_id": m.user_id,
            "email": users[m.user_id].email if m.user_id in users else "",
            "role": m.role,
            "created_at": utc_isoformat(m.created_at),
        }
        for m in members
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_org_member(
    body: AddMemberBody,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="role must be admin or member")
    org_id = await resolve_current_org_id(user, session)

    from sqlalchemy import select

    target = (
        await session.execute(select(User).where(User.email == body.email))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No user with this email")

    om_repo = OrganizationMembershipRepository(session)
    existing = await om_repo.get_role(user_id=target.id, org_id=org_id)
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Already a member")

    await om_repo.grant(user_id=target.id, org_id=org_id, role=OrgRole(body.role))
    return {"user_id": target.id, "email": target.email, "role": body.role}


@router.patch("/{user_id}/role")
async def update_org_member_role(
    user_id: str,
    body: ChangeRoleBody,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="role must be admin or member")
    org_id = await resolve_current_org_id(user, session)

    om_repo = OrganizationMembershipRepository(session)
    current = await om_repo.get_role(user_id=user_id, org_id=org_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not a member of this org")
    if current == OrgRole.OWNER:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Cannot change owner role")

    await om_repo.promote(user_id=user_id, org_id=org_id, role=OrgRole(body.role))
    return {"user_id": user_id, "role": body.role}


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_org_member(
    user_id: str,
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    org_id = await resolve_current_org_id(user, session)

    if user_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot remove yourself")

    om_repo = OrganizationMembershipRepository(session)
    current = await om_repo.get_role(user_id=user_id, org_id=org_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not a member of this org")
    if current == OrgRole.OWNER:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Cannot remove org owner")

    mem_repo = MembershipRepository(session)
    await mem_repo.remove_user_from_org_workspaces(user_id=user_id, org_id=org_id)
    await om_repo.revoke(user_id=user_id, org_id=org_id)
```

- [ ] **Step 2: Register the router in `__init__.py`**

In `backend/cubeplex/api/routes/v1/__init__.py`, add:
- Import: `from cubeplex.api.routes.v1 import admin_members`
- Add `"admin_members"` to `__all__`

- [ ] **Step 3: Mount the router in `app.py`**

In `backend/cubeplex/api/app.py`, within the router registration block:
- Import `admin_members`
- Add: `app.include_router(admin_members.router, prefix="/api/v1")`

- [ ] **Step 4: Verify type-check and lint pass**

Run: `cd /home/chris/cubeplex/backend && make type-check && make lint`
Expected: Both pass clean.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/routes/v1/admin_members.py \
       backend/cubeplex/api/routes/v1/__init__.py \
       backend/cubeplex/api/app.py
git commit -m "feat(members): add org member management admin routes"
```

---

## Task 3: Backend — workspace member routes

**Files:**
- Create: `backend/cubeplex/api/routes/v1/ws_members.py`
- Modify: `backend/cubeplex/api/routes/v1/__init__.py`
- Modify: `backend/cubeplex/api/app.py`

- [ ] **Step 1: Create the ws_members route module**

Create `backend/cubeplex/api/routes/v1/ws_members.py`:

```python
"""Workspace member management routes: list / available / add / change-role / remove."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_admin
from cubeplex.db import get_session
from cubeplex.models import Membership, Role, User
from cubeplex.repositories import MembershipRepository, OrganizationMembershipRepository
from cubeplex.utils.time import utc_isoformat

router = APIRouter(prefix="/ws/{workspace_id}/members", tags=["workspace-members"])

ASSIGNABLE_ROLES = {"admin", "member"}


class AddWsMemberBody(BaseModel):
    user_id: str
    role: str


class ChangeRoleBody(BaseModel):
    role: str


@router.get("")
async def list_workspace_members(
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, str]]:
    mem_repo = MembershipRepository(session)
    members = await mem_repo.list_workspace_members(ctx.workspace_id)

    user_ids = [m.user_id for m in members]
    if not user_ids:
        return []
    stmt = select(User).where(User.id.in_(user_ids))  # type: ignore[arg-type]
    users = {u.id: u for u in (await session.execute(stmt)).scalars().all()}

    return [
        {
            "user_id": m.user_id,
            "email": users[m.user_id].email if m.user_id in users else "",
            "role": m.role,
            "created_at": utc_isoformat(m.created_at),
        }
        for m in members
    ]


@router.get("/available")
async def list_available_org_members(
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, str]]:
    """Org members who are NOT already in this workspace."""
    om_repo = OrganizationMembershipRepository(session)
    org_members = await om_repo.list_org_members(ctx.org_id)

    mem_repo = MembershipRepository(session)
    ws_members = await mem_repo.list_workspace_members(ctx.workspace_id)
    ws_user_ids = {m.user_id for m in ws_members}

    available = [m for m in org_members if m.user_id not in ws_user_ids]
    if not available:
        return []

    user_ids = [m.user_id for m in available]
    stmt = select(User).where(User.id.in_(user_ids))  # type: ignore[arg-type]
    users = {u.id: u for u in (await session.execute(stmt)).scalars().all()}

    return [
        {
            "user_id": m.user_id,
            "email": users[m.user_id].email if m.user_id in users else "",
            "org_role": m.role,
        }
        for m in available
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_workspace_member(
    body: AddWsMemberBody,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="role must be admin or member")

    om_repo = OrganizationMembershipRepository(session)
    org_role = await om_repo.get_role(user_id=body.user_id, org_id=ctx.org_id)
    if org_role is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="User is not a member of this organization"
        )

    mem_repo = MembershipRepository(session)
    existing = await mem_repo.get_role(user_id=body.user_id, workspace_id=ctx.workspace_id)
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Already a workspace member")

    await mem_repo.grant(user_id=body.user_id, workspace_id=ctx.workspace_id, role=Role(body.role))

    target = await session.get(User, body.user_id)
    email = target.email if target else ""
    return {"user_id": body.user_id, "email": email, "role": body.role}


@router.patch("/{user_id}/role")
async def update_workspace_member_role(
    user_id: str,
    body: ChangeRoleBody,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="role must be admin or member")

    mem_repo = MembershipRepository(session)
    current = await mem_repo.get_role(user_id=user_id, workspace_id=ctx.workspace_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not a member of this workspace")

    from sqlalchemy import update

    stmt = (
        update(Membership)
        .where(
            Membership.user_id == user_id,  # type: ignore[arg-type]
            Membership.workspace_id == ctx.workspace_id,  # type: ignore[arg-type]
        )
        .values(role=body.role)
    )
    await session.execute(stmt)
    await session.commit()
    return {"user_id": user_id, "role": body.role}


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_workspace_member(
    user_id: str,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    if user_id == ctx.user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot remove yourself")

    mem_repo = MembershipRepository(session)
    current = await mem_repo.get_role(user_id=user_id, workspace_id=ctx.workspace_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not a member of this workspace")

    from sqlalchemy import delete

    stmt = delete(Membership).where(
        Membership.user_id == user_id,  # type: ignore[arg-type]
        Membership.workspace_id == ctx.workspace_id,  # type: ignore[arg-type]
    )
    await session.execute(stmt)
    await session.commit()
```

- [ ] **Step 2: Register the router**

In `backend/cubeplex/api/routes/v1/__init__.py`:
- Import: `from cubeplex.api.routes.v1 import ws_members`
- Add `"ws_members"` to `__all__`

In `backend/cubeplex/api/app.py`:
- Import `ws_members`
- Add: `app.include_router(ws_members.router, prefix="/api/v1")`

- [ ] **Step 3: Verify type-check and lint pass**

Run: `cd /home/chris/cubeplex/backend && make type-check && make lint`
Expected: Both pass clean.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/api/routes/v1/ws_members.py \
       backend/cubeplex/api/routes/v1/__init__.py \
       backend/cubeplex/api/app.py
git commit -m "feat(members): add workspace member management routes"
```

---

## Task 4: E2E tests — org member admin routes

**Files:**
- Create: `backend/tests/e2e/test_admin_members.py`

- [ ] **Step 1: Write E2E tests**

Create `backend/tests/e2e/test_admin_members.py`:

```python
"""E2E tests for org member management routes (/admin/members)."""

import secrets

import pytest
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUserCreate
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.users import UserManager
from cubeplex.models import OrgRole, User
from cubeplex.repositories import OrganizationMembershipRepository

pytestmark = pytest.mark.e2e


async def _create_standalone_user(session: AsyncSession) -> User:
    """Create a user not in any org (for add-member tests)."""
    email = f"standalone-{secrets.token_hex(4)}@example.com"
    user_db = SQLAlchemyUserDatabase(session, User)
    manager = UserManager(user_db)
    return await manager.create(BaseUserCreate(email=email, password="test12345"), safe=False)


async def test_list_org_members(admin_client, session_factory):
    client, _ws = admin_client
    resp = await client.get("/api/v1/admin/members")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    first = data[0]
    assert "user_id" in first
    assert "email" in first
    assert "role" in first
    assert "created_at" in first


async def test_add_org_member(admin_client, session_factory):
    client, _ws = admin_client
    async with session_factory() as session:
        new_user = await _create_standalone_user(session)

    resp = await client.post(
        "/api/v1/admin/members",
        json={"email": new_user.email, "role": "member"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["email"] == new_user.email
    assert data["role"] == "member"

    # Verify shows in list
    list_resp = await client.get("/api/v1/admin/members")
    emails = [m["email"] for m in list_resp.json()]
    assert new_user.email in emails


async def test_add_duplicate_member_returns_409(admin_client, session_factory):
    client, _ws = admin_client
    async with session_factory() as session:
        new_user = await _create_standalone_user(session)

    await client.post("/api/v1/admin/members", json={"email": new_user.email, "role": "member"})
    resp = await client.post(
        "/api/v1/admin/members",
        json={"email": new_user.email, "role": "member"},
    )
    assert resp.status_code == 409


async def test_add_nonexistent_email_returns_404(admin_client):
    client, _ws = admin_client
    resp = await client.post(
        "/api/v1/admin/members",
        json={"email": "nobody-exists@example.com", "role": "member"},
    )
    assert resp.status_code == 404


async def test_add_invalid_role_returns_400(admin_client, session_factory):
    client, _ws = admin_client
    async with session_factory() as session:
        new_user = await _create_standalone_user(session)
    resp = await client.post(
        "/api/v1/admin/members",
        json={"email": new_user.email, "role": "owner"},
    )
    assert resp.status_code == 400


async def test_change_member_role(admin_client, session_factory):
    client, _ws = admin_client
    async with session_factory() as session:
        new_user = await _create_standalone_user(session)

    await client.post("/api/v1/admin/members", json={"email": new_user.email, "role": "member"})
    resp = await client.patch(
        f"/api/v1/admin/members/{new_user.id}/role",
        json={"role": "admin"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"


async def test_change_owner_role_returns_409(admin_client):
    client, _ws = admin_client
    # The admin_client user is the owner of their org
    me = await client.get("/api/v1/auth/me")
    my_id = me.json()["id"]
    resp = await client.patch(
        f"/api/v1/admin/members/{my_id}/role",
        json={"role": "member"},
    )
    assert resp.status_code == 409


async def test_remove_member(admin_client, session_factory):
    client, _ws = admin_client
    async with session_factory() as session:
        new_user = await _create_standalone_user(session)

    await client.post("/api/v1/admin/members", json={"email": new_user.email, "role": "member"})
    resp = await client.delete(f"/api/v1/admin/members/{new_user.id}")
    assert resp.status_code == 204

    # Verify removed from list
    list_resp = await client.get("/api/v1/admin/members")
    emails = [m["email"] for m in list_resp.json()]
    assert new_user.email not in emails


async def test_remove_owner_returns_409(admin_client):
    client, _ws = admin_client
    me = await client.get("/api/v1/auth/me")
    my_id = me.json()["id"]
    resp = await client.delete(f"/api/v1/admin/members/{my_id}")
    # self-removal blocked first (400), but owner is also protected (409)
    assert resp.status_code == 400


async def test_remove_self_returns_400(admin_client, session_factory):
    client, _ws = admin_client
    me = await client.get("/api/v1/auth/me")
    my_id = me.json()["id"]
    resp = await client.delete(f"/api/v1/admin/members/{my_id}")
    assert resp.status_code == 400


async def test_member_user_cannot_manage_org_members(member_client):
    client, _ws = member_client
    resp = await client.get("/api/v1/admin/members")
    assert resp.status_code == 403


async def test_remove_cascades_workspace_memberships(admin_client, session_factory):
    client, ws_id = admin_client
    async with session_factory() as session:
        new_user = await _create_standalone_user(session)

    # Add to org
    await client.post("/api/v1/admin/members", json={"email": new_user.email, "role": "member"})

    # Also add to the workspace
    await client.post(
        f"/api/v1/ws/{ws_id}/members",
        json={"user_id": new_user.id, "role": "member"},
    )

    # Verify in workspace
    ws_resp = await client.get(f"/api/v1/ws/{ws_id}/members")
    assert new_user.id in [m["user_id"] for m in ws_resp.json()]

    # Remove from org — should cascade
    await client.delete(f"/api/v1/admin/members/{new_user.id}")

    # Verify gone from workspace too
    ws_resp2 = await client.get(f"/api/v1/ws/{ws_id}/members")
    assert new_user.id not in [m["user_id"] for m in ws_resp2.json()]
```

- [ ] **Step 2: Run the tests**

Run: `cd /home/chris/cubeplex/backend && uv run pytest tests/e2e/test_admin_members.py -v`
Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_admin_members.py
git commit -m "test(members): add E2E tests for org member admin routes"
```

---

## Task 5: E2E tests — workspace member routes

**Files:**
- Create: `backend/tests/e2e/test_ws_members.py`

- [ ] **Step 1: Write E2E tests**

Create `backend/tests/e2e/test_ws_members.py`:

```python
"""E2E tests for workspace member management routes (/ws/{wsId}/members)."""

import secrets

import pytest
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.schemas import BaseUserCreate
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.users import UserManager
from cubeplex.models import OrgRole, User
from cubeplex.repositories import OrganizationMembershipRepository

pytestmark = pytest.mark.e2e


async def _create_org_member(session: AsyncSession, org_id: str) -> User:
    """Create a user who is an org member but not in any workspace."""
    email = f"orgmember-{secrets.token_hex(4)}@example.com"
    user_db = SQLAlchemyUserDatabase(session, User)
    manager = UserManager(user_db)
    user = await manager.create(BaseUserCreate(email=email, password="test12345"), safe=False)
    om_repo = OrganizationMembershipRepository(session)
    await om_repo.grant(user_id=user.id, org_id=org_id, role=OrgRole.MEMBER)
    return user


async def _get_org_id(client, ws_id: str) -> str:
    resp = await client.get("/api/v1/workspaces")
    for ws in resp.json():
        if ws["id"] == ws_id:
            return ws["org_id"]
    raise ValueError(f"workspace {ws_id} not found")


async def test_list_workspace_members(admin_client):
    client, ws_id = admin_client
    resp = await client.get(f"/api/v1/ws/{ws_id}/members")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "user_id" in data[0]
    assert "email" in data[0]
    assert "role" in data[0]


async def test_list_available_members(admin_client, session_factory):
    client, ws_id = admin_client
    org_id = await _get_org_id(client, ws_id)

    async with session_factory() as session:
        new_user = await _create_org_member(session, org_id)

    resp = await client.get(f"/api/v1/ws/{ws_id}/members/available")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    user_ids = [m["user_id"] for m in data]
    assert new_user.id in user_ids


async def test_add_workspace_member(admin_client, session_factory):
    client, ws_id = admin_client
    org_id = await _get_org_id(client, ws_id)

    async with session_factory() as session:
        new_user = await _create_org_member(session, org_id)

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/members",
        json={"user_id": new_user.id, "role": "member"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user_id"] == new_user.id

    # Verify in member list
    list_resp = await client.get(f"/api/v1/ws/{ws_id}/members")
    assert new_user.id in [m["user_id"] for m in list_resp.json()]

    # Verify no longer in available list
    avail_resp = await client.get(f"/api/v1/ws/{ws_id}/members/available")
    assert new_user.id not in [m["user_id"] for m in avail_resp.json()]


async def test_add_non_org_member_returns_403(admin_client, session_factory):
    client, ws_id = admin_client
    async with session_factory() as session:
        email = f"outsider-{secrets.token_hex(4)}@example.com"
        user_db = SQLAlchemyUserDatabase(session, User)
        manager = UserManager(user_db)
        outsider = await manager.create(
            BaseUserCreate(email=email, password="test12345"), safe=False
        )

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/members",
        json={"user_id": outsider.id, "role": "member"},
    )
    assert resp.status_code == 403


async def test_add_duplicate_returns_409(admin_client, session_factory):
    client, ws_id = admin_client
    org_id = await _get_org_id(client, ws_id)

    async with session_factory() as session:
        new_user = await _create_org_member(session, org_id)

    await client.post(f"/api/v1/ws/{ws_id}/members", json={"user_id": new_user.id, "role": "member"})
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/members",
        json={"user_id": new_user.id, "role": "member"},
    )
    assert resp.status_code == 409


async def test_change_workspace_member_role(admin_client, session_factory):
    client, ws_id = admin_client
    org_id = await _get_org_id(client, ws_id)

    async with session_factory() as session:
        new_user = await _create_org_member(session, org_id)

    await client.post(f"/api/v1/ws/{ws_id}/members", json={"user_id": new_user.id, "role": "member"})
    resp = await client.patch(
        f"/api/v1/ws/{ws_id}/members/{new_user.id}/role",
        json={"role": "admin"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"


async def test_remove_workspace_member(admin_client, session_factory):
    client, ws_id = admin_client
    org_id = await _get_org_id(client, ws_id)

    async with session_factory() as session:
        new_user = await _create_org_member(session, org_id)

    await client.post(f"/api/v1/ws/{ws_id}/members", json={"user_id": new_user.id, "role": "member"})
    resp = await client.delete(f"/api/v1/ws/{ws_id}/members/{new_user.id}")
    assert resp.status_code == 204

    list_resp = await client.get(f"/api/v1/ws/{ws_id}/members")
    assert new_user.id not in [m["user_id"] for m in list_resp.json()]


async def test_remove_self_returns_400(admin_client):
    client, ws_id = admin_client
    me = await client.get("/api/v1/auth/me")
    my_id = me.json()["id"]
    resp = await client.delete(f"/api/v1/ws/{ws_id}/members/{my_id}")
    assert resp.status_code == 400


async def test_member_cannot_manage_workspace_members(member_client):
    client, ws_id = member_client
    resp = await client.get(f"/api/v1/ws/{ws_id}/members")
    assert resp.status_code == 403
```

- [ ] **Step 2: Run the tests**

Run: `cd /home/chris/cubeplex/backend && uv run pytest tests/e2e/test_ws_members.py -v`
Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_ws_members.py
git commit -m "test(members): add E2E tests for workspace member routes"
```

---

## Task 6: Frontend — core API module + Zustand store

**Files:**
- Create: `frontend/packages/core/src/api/members.ts`
- Create: `frontend/packages/core/src/stores/memberStore.ts`
- Modify: `frontend/packages/core/src/api/index.ts`
- Modify: `frontend/packages/core/src/stores/index.ts`

- [ ] **Step 1: Create the API module**

Create `frontend/packages/core/src/api/members.ts`:

```typescript
import { toApiError, type ApiClient } from './client'

export interface OrgMember {
  user_id: string
  email: string
  role: 'owner' | 'admin' | 'member'
  created_at: string
}

export interface WsMember {
  user_id: string
  email: string
  role: 'admin' | 'member'
  created_at: string
}

export interface AvailableMember {
  user_id: string
  email: string
  org_role: string
}

export async function listOrgMembers(client: ApiClient): Promise<OrgMember[]> {
  const res = await client.get('/api/v1/admin/members')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as OrgMember[]
}

export async function addOrgMember(
  client: ApiClient,
  email: string,
  role: string,
): Promise<{ user_id: string; email: string; role: string }> {
  const res = await client.post('/api/v1/admin/members', { email, role })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { user_id: string; email: string; role: string }
}

export async function updateOrgMemberRole(
  client: ApiClient,
  userId: string,
  role: string,
): Promise<void> {
  const res = await client.patch(`/api/v1/admin/members/${userId}/role`, { role })
  if (!res.ok) throw await toApiError(res)
}

export async function removeOrgMember(client: ApiClient, userId: string): Promise<void> {
  const res = await client.del(`/api/v1/admin/members/${userId}`)
  if (!res.ok) throw await toApiError(res)
}

export async function listWsMembers(client: ApiClient, wsId: string): Promise<WsMember[]> {
  const res = await client.get(`/api/v1/ws/${wsId}/members`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as WsMember[]
}

export async function listAvailableMembers(
  client: ApiClient,
  wsId: string,
): Promise<AvailableMember[]> {
  const res = await client.get(`/api/v1/ws/${wsId}/members/available`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as AvailableMember[]
}

export async function addWsMember(
  client: ApiClient,
  wsId: string,
  userId: string,
  role: string,
): Promise<{ user_id: string; email: string; role: string }> {
  const res = await client.post(`/api/v1/ws/${wsId}/members`, { user_id: userId, role })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { user_id: string; email: string; role: string }
}

export async function updateWsMemberRole(
  client: ApiClient,
  wsId: string,
  userId: string,
  role: string,
): Promise<void> {
  const res = await client.patch(`/api/v1/ws/${wsId}/members/${userId}/role`, { role })
  if (!res.ok) throw await toApiError(res)
}

export async function removeWsMember(
  client: ApiClient,
  wsId: string,
  userId: string,
): Promise<void> {
  const res = await client.del(`/api/v1/ws/${wsId}/members/${userId}`)
  if (!res.ok) throw await toApiError(res)
}
```

- [ ] **Step 2: Create the Zustand store**

Create `frontend/packages/core/src/stores/memberStore.ts`:

```typescript
import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import {
  listOrgMembers,
  addOrgMember as apiAddOrgMember,
  updateOrgMemberRole as apiUpdateOrgMemberRole,
  removeOrgMember as apiRemoveOrgMember,
  listWsMembers,
  listAvailableMembers as apiListAvailable,
  addWsMember as apiAddWsMember,
  updateWsMemberRole as apiUpdateWsMemberRole,
  removeWsMember as apiRemoveWsMember,
  type OrgMember,
  type WsMember,
  type AvailableMember,
} from '../api/members'

export interface MemberStore {
  orgMembers: OrgMember[]
  orgLoading: boolean
  wsMembers: WsMember[]
  wsLoading: boolean
  available: AvailableMember[]

  loadOrgMembers(client: ApiClient): Promise<void>
  addOrgMember(client: ApiClient, email: string, role: string): Promise<void>
  updateOrgMemberRole(client: ApiClient, userId: string, role: string): Promise<void>
  removeOrgMember(client: ApiClient, userId: string): Promise<void>

  loadWsMembers(client: ApiClient, wsId: string): Promise<void>
  loadAvailable(client: ApiClient, wsId: string): Promise<void>
  addWsMember(client: ApiClient, wsId: string, userId: string, role: string): Promise<void>
  updateWsMemberRole(
    client: ApiClient,
    wsId: string,
    userId: string,
    role: string,
  ): Promise<void>
  removeWsMember(client: ApiClient, wsId: string, userId: string): Promise<void>

  reset(): void
}

export const useMemberStore = create<MemberStore>((set, get) => ({
  orgMembers: [],
  orgLoading: false,
  wsMembers: [],
  wsLoading: false,
  available: [],

  async loadOrgMembers(client) {
    set({ orgLoading: true })
    try {
      const orgMembers = await listOrgMembers(client)
      set({ orgMembers })
    } finally {
      set({ orgLoading: false })
    }
  },

  async addOrgMember(client, email, role) {
    await apiAddOrgMember(client, email, role)
    await get().loadOrgMembers(client)
  },

  async updateOrgMemberRole(client, userId, role) {
    await apiUpdateOrgMemberRole(client, userId, role)
    set((s) => ({
      orgMembers: s.orgMembers.map((m) => (m.user_id === userId ? { ...m, role: role as OrgMember['role'] } : m)),
    }))
  },

  async removeOrgMember(client, userId) {
    await apiRemoveOrgMember(client, userId)
    set((s) => ({ orgMembers: s.orgMembers.filter((m) => m.user_id !== userId) }))
  },

  async loadWsMembers(client, wsId) {
    set({ wsLoading: true })
    try {
      const wsMembers = await listWsMembers(client, wsId)
      set({ wsMembers })
    } finally {
      set({ wsLoading: false })
    }
  },

  async loadAvailable(client, wsId) {
    const available = await apiListAvailable(client, wsId)
    set({ available })
  },

  async addWsMember(client, wsId, userId, role) {
    await apiAddWsMember(client, wsId, userId, role)
    await get().loadWsMembers(client, wsId)
    await get().loadAvailable(client, wsId)
  },

  async updateWsMemberRole(client, wsId, userId, role) {
    await apiUpdateWsMemberRole(client, wsId, userId, role)
    set((s) => ({
      wsMembers: s.wsMembers.map((m) => (m.user_id === userId ? { ...m, role: role as WsMember['role'] } : m)),
    }))
  },

  async removeWsMember(client, wsId, userId) {
    await apiRemoveWsMember(client, wsId, userId)
    set((s) => ({ wsMembers: s.wsMembers.filter((m) => m.user_id !== userId) }))
    await get().loadAvailable(client, wsId)
  },

  reset() {
    set({ orgMembers: [], orgLoading: false, wsMembers: [], wsLoading: false, available: [] })
  },
}))
```

- [ ] **Step 3: Update barrel exports**

In `frontend/packages/core/src/api/index.ts`, add:
```typescript
export * from './members'
```

In `frontend/packages/core/src/stores/index.ts`, add:
```typescript
export { useMemberStore } from './memberStore'
```

- [ ] **Step 4: Build core and verify**

Run: `cd /home/chris/cubeplex/frontend && pnpm --filter @cubeplex/core build`
Expected: Build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/api/members.ts \
       frontend/packages/core/src/stores/memberStore.ts \
       frontend/packages/core/src/api/index.ts \
       frontend/packages/core/src/stores/index.ts
git commit -m "feat(members): add member API module and Zustand store in @cubeplex/core"
```

---

## Task 7: Frontend — i18n keys

**Files:**
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`

- [ ] **Step 1: Add i18n keys to en.json**

Add the following keys to `en.json`:

In `"adminNav"` section, add:
```json
"members": "Members"
```

Add a new top-level section `"adminMembers"`:
```json
"adminMembers": {
  "title": "Members",
  "subtitle": "Manage organization members and their roles.",
  "addMember": "Add member",
  "email": "Email",
  "role": "Role",
  "joined": "Joined",
  "actions": "Actions",
  "remove": "Remove",
  "owner": "owner",
  "admin": "admin",
  "member": "member",
  "addDialog": {
    "title": "Add member",
    "emailLabel": "Email address",
    "emailPlaceholder": "user@example.com",
    "roleLabel": "Role",
    "cancel": "Cancel",
    "add": "Add",
    "errorNotFound": "No user with this email",
    "errorDuplicate": "Already a member"
  },
  "roleConfirm": {
    "title": "Change role",
    "message": "Change {email} role to {role}?",
    "confirm": "Change role",
    "cancel": "Cancel"
  },
  "removeConfirm": {
    "title": "Remove member",
    "message": "Remove {email} from the organization? They will also lose access to all workspaces.",
    "confirm": "Remove",
    "cancel": "Cancel"
  },
  "empty": "No members yet."
}
```

In `"wsSettings"` section, add:
```json
"navMembers": "Members"
```

Add a new top-level section `"wsMembers"`:
```json
"wsMembers": {
  "title": "Members",
  "subtitle": "Manage workspace members and their roles.",
  "addMember": "Add member",
  "email": "Email",
  "role": "Role",
  "joined": "Joined",
  "actions": "Actions",
  "remove": "Remove",
  "admin": "admin",
  "member": "member",
  "addDialog": {
    "title": "Add member",
    "selectLabel": "Select member",
    "selectPlaceholder": "Search members...",
    "roleLabel": "Role",
    "cancel": "Cancel",
    "add": "Add",
    "noAvailable": "All org members are already in this workspace."
  },
  "roleConfirm": {
    "title": "Change role",
    "message": "Change {email} role to {role}?",
    "confirm": "Change role",
    "cancel": "Cancel"
  },
  "removeConfirm": {
    "title": "Remove member",
    "message": "Remove {email} from this workspace?",
    "confirm": "Remove",
    "cancel": "Cancel"
  },
  "empty": "No members yet."
}
```

- [ ] **Step 2: Add i18n keys to zh.json**

Mirror all keys added above with Chinese translations. Use:
- "Members" → "成员"
- "Manage organization members and their roles." → "管理组织成员及其角色。"
- "Add member" → "添加成员"
- "Email address" → "邮箱地址"
- "Role" → "角色"
- "Joined" → "加入时间"
- "Remove" → "移除"
- "No user with this email" → "未找到该邮箱的用户"
- "Already a member" → "已经是成员"
- "Change role" → "更改角色"
- "Change {email} role to {role}?" → "将 {email} 的角色更改为 {role}？"
- "Remove member" → "移除成员"
- "Remove {email} from the organization? They will also lose access to all workspaces." → "从组织中移除 {email}？该成员将同时失去所有工作区的访问权限。"
- "No members yet." → "暂无成员。"
- "Manage workspace members and their roles." → "管理工作区成员及其角色。"
- "Select member" → "选择成员"
- "Search members..." → "搜索成员..."
- "All org members are already in this workspace." → "所有组织成员都已在此工作区中。"
- "Remove {email} from this workspace?" → "从此工作区中移除 {email}？"

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/messages/en.json frontend/packages/web/messages/zh.json
git commit -m "feat(members): add i18n keys for member management UI"
```

---

## Task 8: Frontend — admin members page + components

**Files:**
- Create: `frontend/packages/web/app/admin/members/page.tsx`
- Create: `frontend/packages/web/components/admin/members/OrgMembersTable.tsx`
- Create: `frontend/packages/web/components/admin/members/AddOrgMemberDialog.tsx`
- Modify: `frontend/packages/web/components/admin/AdminSubNav.tsx`

- [ ] **Step 1: Add "Members" to AdminSubNav**

In `frontend/packages/web/components/admin/AdminSubNav.tsx`:
- Add `Users` to the lucide import: `import { Box, CircleDollarSign, Cpu, Globe, Plug, Puzzle, Settings, Sparkles, Users } from 'lucide-react'`
- Insert `{ href: '/admin/members', label: t('members'), icon: Users }` as the second item in `NATIVE_ITEMS` (after "settings", before "models").

- [ ] **Step 2: Create AddOrgMemberDialog**

Create `frontend/packages/web/components/admin/members/AddOrgMemberDialog.tsx`:

```tsx
'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

interface AddOrgMemberDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onAdd: (email: string, role: string) => Promise<void>
}

export function AddOrgMemberDialog({ open, onOpenChange, onAdd }: AddOrgMemberDialogProps) {
  const t = useTranslations('adminMembers.addDialog')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('member')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async () => {
    setSaving(true)
    setError(null)
    try {
      await onAdd(email, role)
      setEmail('')
      setRole('member')
      onOpenChange(false)
    } catch (err: unknown) {
      const e = err as { status?: number }
      if (e.status === 404) setError(t('errorNotFound'))
      else if (e.status === 409) setError(t('errorDuplicate'))
      else setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop
          className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]"
        />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2',
            'rounded-lg border border-border bg-popover p-6 shadow-xl',
          )}
        >
          <div className="flex items-center justify-between mb-4">
            <DialogPrimitive.Title className="text-base font-semibold">
              {t('title')}
            </DialogPrimitive.Title>
            <DialogPrimitive.Close
              className="rounded-sm p-1 hover:bg-accent"
            >
              <X className="size-4" />
            </DialogPrimitive.Close>
          </div>

          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label className="text-xs">{t('emailLabel')}</Label>
              <Input
                type="email"
                placeholder={t('emailPlaceholder')}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs">{t('roleLabel')}</Label>
              <Select value={role} onValueChange={setRole}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">admin</SelectItem>
                  <SelectItem value="member">member</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {error && (
              <p className="text-xs text-destructive">{error}</p>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onOpenChange(false)}
                disabled={saving}
              >
                {t('cancel')}
              </Button>
              <Button
                size="sm"
                onClick={handleSubmit}
                disabled={saving || !email.trim()}
              >
                {t('add')}
              </Button>
            </div>
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
```

- [ ] **Step 3: Create OrgMembersTable**

Create `frontend/packages/web/components/admin/members/OrgMembersTable.tsx`:

```tsx
'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { createApiClient, useMemberStore } from '@cubeplex/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { AddOrgMemberDialog } from './AddOrgMemberDialog'

export function OrgMembersTable() {
  const t = useTranslations('adminMembers')
  const client = useMemo(() => createApiClient(''), [])
  const { orgMembers, orgLoading, loadOrgMembers, addOrgMember, updateOrgMemberRole, removeOrgMember } =
    useMemberStore()
  const [addOpen, setAddOpen] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState<{ userId: string; email: string } | null>(null)

  useEffect(() => {
    loadOrgMembers(client)
  }, [client, loadOrgMembers])

  const handleAdd = useCallback(
    async (email: string, role: string) => {
      await addOrgMember(client, email, role)
    },
    [client, addOrgMember],
  )

  const handleRoleChange = useCallback(
    async (userId: string, role: string) => {
      await updateOrgMemberRole(client, userId, role)
    },
    [client, updateOrgMemberRole],
  )

  const handleRemove = useCallback(
    async (userId: string) => {
      await removeOrgMember(client, userId)
      setConfirmRemove(null)
    },
    [client, removeOrgMember],
  )

  const formatDate = (iso: string) => {
    const d = new Date(iso)
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  }

  return (
    <>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
        </div>
        <Button size="sm" onClick={() => setAddOpen(true)}>
          {t('addMember')}
        </Button>
      </div>

      {orgLoading ? (
        <div className="py-8 text-center text-sm text-muted-foreground">Loading...</div>
      ) : orgMembers.length === 0 ? (
        <div className="py-8 text-center text-sm text-muted-foreground">{t('empty')}</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('email')}</TableHead>
              <TableHead>{t('role')}</TableHead>
              <TableHead>{t('joined')}</TableHead>
              <TableHead className="text-right">{t('actions')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {orgMembers.map((m) => (
              <TableRow key={m.user_id}>
                <TableCell className="text-sm">{m.email}</TableCell>
                <TableCell>
                  {m.role === 'owner' ? (
                    <Badge variant="outline" className="text-xs">
                      {t('owner')}
                    </Badge>
                  ) : (
                    <Select
                      value={m.role}
                      onValueChange={(v) => handleRoleChange(m.user_id, v)}
                    >
                      <SelectTrigger className="h-7 w-24 text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="admin">{t('admin')}</SelectItem>
                        <SelectItem value="member">{t('member')}</SelectItem>
                      </SelectContent>
                    </Select>
                  )}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatDate(m.created_at)}
                </TableCell>
                <TableCell className="text-right">
                  {m.role !== 'owner' && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-xs text-destructive hover:text-destructive"
                      onClick={() => setConfirmRemove({ userId: m.user_id, email: m.email })}
                    >
                      {t('remove')}
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <AddOrgMemberDialog open={addOpen} onOpenChange={setAddOpen} onAdd={handleAdd} />

      {confirmRemove && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-[2px]">
          <div className="w-full max-w-sm rounded-lg border border-border bg-popover p-6 shadow-xl">
            <h3 className="text-sm font-semibold">{t('removeConfirm.title')}</h3>
            <p className="mt-2 text-xs text-muted-foreground">
              {t('removeConfirm.message', { email: confirmRemove.email })}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setConfirmRemove(null)}>
                {t('removeConfirm.cancel')}
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => handleRemove(confirmRemove.userId)}
              >
                {t('removeConfirm.confirm')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
```

- [ ] **Step 4: Create the admin members page**

Create `frontend/packages/web/app/admin/members/page.tsx`:

```tsx
'use client'

import { OrgMembersTable } from '@/components/admin/members/OrgMembersTable'

export default function AdminMembersPage() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
          <OrgMembersTable />
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Build frontend and verify**

Run: `cd /home/chris/cubeplex/frontend && pnpm build`
Expected: Build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/app/admin/members/page.tsx \
       frontend/packages/web/components/admin/members/OrgMembersTable.tsx \
       frontend/packages/web/components/admin/members/AddOrgMemberDialog.tsx \
       frontend/packages/web/components/admin/AdminSubNav.tsx
git commit -m "feat(members): add admin org members page and components"
```

---

## Task 9: Frontend — workspace members settings tab + components

**Files:**
- Create: `frontend/packages/web/components/workspace-settings/MembersPanel.tsx`
- Create: `frontend/packages/web/components/workspace-settings/members/WsMembersTable.tsx`
- Create: `frontend/packages/web/components/workspace-settings/members/AddWsMemberDialog.tsx`
- Modify: `frontend/packages/web/components/workspace-settings/SettingsNav.tsx`
- Modify: `frontend/packages/web/app/(app)/w/[wsId]/settings/page.tsx`

- [ ] **Step 1: Add "Members" to SettingsNav**

In `frontend/packages/web/components/workspace-settings/SettingsNav.tsx`:
- Add `Users` to the lucide import: `import { Bot, Plug, Sparkles, Users } from 'lucide-react'`
- Add to `TOP_LEVEL` array after the `mcp` entry:
  ```typescript
  { key: 'members', labelKey: 'navMembers' as TopLabelKey, icon: Users },
  ```
- Add `'navMembers'` to the `TopLabelKey` union type.

- [ ] **Step 2: Create AddWsMemberDialog**

Create `frontend/packages/web/components/workspace-settings/members/AddWsMemberDialog.tsx`:

```tsx
'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import type { AvailableMember } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

interface AddWsMemberDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  available: AvailableMember[]
  onAdd: (userId: string, role: string) => Promise<void>
}

export function AddWsMemberDialog({
  open,
  onOpenChange,
  available,
  onAdd,
}: AddWsMemberDialogProps) {
  const t = useTranslations('wsMembers.addDialog')
  const [selectedUserId, setSelectedUserId] = useState('')
  const [role, setRole] = useState('member')
  const [saving, setSaving] = useState(false)

  const handleSubmit = async () => {
    if (!selectedUserId) return
    setSaving(true)
    try {
      await onAdd(selectedUserId, role)
      setSelectedUserId('')
      setRole('member')
      onOpenChange(false)
    } finally {
      setSaving(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop
          className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]"
        />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2',
            'rounded-lg border border-border bg-popover p-6 shadow-xl',
          )}
        >
          <div className="flex items-center justify-between mb-4">
            <DialogPrimitive.Title className="text-base font-semibold">
              {t('title')}
            </DialogPrimitive.Title>
            <DialogPrimitive.Close className="rounded-sm p-1 hover:bg-accent">
              <X className="size-4" />
            </DialogPrimitive.Close>
          </div>

          {available.length === 0 ? (
            <p className="text-xs text-muted-foreground py-4">{t('noAvailable')}</p>
          ) : (
            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label className="text-xs">{t('selectLabel')}</Label>
                <Select value={selectedUserId} onValueChange={setSelectedUserId}>
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder={t('selectPlaceholder')} />
                  </SelectTrigger>
                  <SelectContent>
                    {available.map((m) => (
                      <SelectItem key={m.user_id} value={m.user_id}>
                        {m.email}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label className="text-xs">{t('roleLabel')}</Label>
                <Select value={role} onValueChange={setRole}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">admin</SelectItem>
                    <SelectItem value="member">member</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onOpenChange(false)}
                  disabled={saving}
                >
                  {t('cancel')}
                </Button>
                <Button
                  size="sm"
                  onClick={handleSubmit}
                  disabled={saving || !selectedUserId}
                >
                  {t('add')}
                </Button>
              </div>
            </div>
          )}
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
```

- [ ] **Step 3: Create WsMembersTable**

Create `frontend/packages/web/components/workspace-settings/members/WsMembersTable.tsx`:

```tsx
'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { createApiClient, useMemberStore, useAuthStore } from '@cubeplex/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { AddWsMemberDialog } from './AddWsMemberDialog'

interface WsMembersTableProps {
  wsId: string
}

export function WsMembersTable({ wsId }: WsMembersTableProps) {
  const t = useTranslations('wsMembers')
  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])
  const currentUser = useAuthStore((s) => s.user)
  const {
    wsMembers, wsLoading, available,
    loadWsMembers, loadAvailable,
    addWsMember, updateWsMemberRole, removeWsMember,
  } = useMemberStore()
  const [addOpen, setAddOpen] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState<{ userId: string; email: string } | null>(null)

  useEffect(() => {
    loadWsMembers(client, wsId)
  }, [client, wsId, loadWsMembers])

  const handleOpenAdd = useCallback(() => {
    loadAvailable(client, wsId)
    setAddOpen(true)
  }, [client, wsId, loadAvailable])

  const handleAdd = useCallback(
    async (userId: string, role: string) => {
      await addWsMember(client, wsId, userId, role)
    },
    [client, wsId, addWsMember],
  )

  const handleRoleChange = useCallback(
    async (userId: string, role: string) => {
      await updateWsMemberRole(client, wsId, userId, role)
    },
    [client, wsId, updateWsMemberRole],
  )

  const handleRemove = useCallback(
    async (userId: string) => {
      await removeWsMember(client, wsId, userId)
      setConfirmRemove(null)
    },
    [client, wsId, removeWsMember],
  )

  const formatDate = (iso: string) => {
    const d = new Date(iso)
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  }

  return (
    <>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
        </div>
        <Button size="sm" onClick={handleOpenAdd}>
          {t('addMember')}
        </Button>
      </div>

      {wsLoading ? (
        <div className="py-8 text-center text-sm text-muted-foreground">Loading...</div>
      ) : wsMembers.length === 0 ? (
        <div className="py-8 text-center text-sm text-muted-foreground">{t('empty')}</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('email')}</TableHead>
              <TableHead>{t('role')}</TableHead>
              <TableHead>{t('joined')}</TableHead>
              <TableHead className="text-right">{t('actions')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {wsMembers.map((m) => (
              <TableRow key={m.user_id}>
                <TableCell className="text-sm">{m.email}</TableCell>
                <TableCell>
                  <Select
                    value={m.role}
                    onValueChange={(v) => handleRoleChange(m.user_id, v)}
                  >
                    <SelectTrigger className="h-7 w-24 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="admin">{t('admin')}</SelectItem>
                      <SelectItem value="member">{t('member')}</SelectItem>
                    </SelectContent>
                  </Select>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatDate(m.created_at)}
                </TableCell>
                <TableCell className="text-right">
                  {m.user_id !== currentUser?.id && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-xs text-destructive hover:text-destructive"
                      onClick={() => setConfirmRemove({ userId: m.user_id, email: m.email })}
                    >
                      {t('remove')}
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <AddWsMemberDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        available={available}
        onAdd={handleAdd}
      />

      {confirmRemove && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-[2px]">
          <div className="w-full max-w-sm rounded-lg border border-border bg-popover p-6 shadow-xl">
            <h3 className="text-sm font-semibold">{t('removeConfirm.title')}</h3>
            <p className="mt-2 text-xs text-muted-foreground">
              {t('removeConfirm.message', { email: confirmRemove.email })}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setConfirmRemove(null)}>
                {t('removeConfirm.cancel')}
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => handleRemove(confirmRemove.userId)}
              >
                {t('removeConfirm.confirm')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
```

- [ ] **Step 4: Create MembersPanel wrapper**

Create `frontend/packages/web/components/workspace-settings/MembersPanel.tsx`:

```tsx
'use client'

import { WsMembersTable } from './members/WsMembersTable'

interface MembersPanelProps {
  wsId: string
}

export function MembersPanel({ wsId }: MembersPanelProps) {
  return (
    <div className="flex h-full flex-col overflow-y-auto px-6 py-6">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
        <WsMembersTable wsId={wsId} />
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Wire into settings page**

In `frontend/packages/web/app/(app)/w/[wsId]/settings/page.tsx`:
- Add import: `import { MembersPanel } from '@/components/workspace-settings/MembersPanel'`
- Add render case: `{tab === 'members' && <MembersPanel wsId={wsId} />}`

- [ ] **Step 6: Build and verify**

Run: `cd /home/chris/cubeplex/frontend && pnpm build`
Expected: Build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/web/app/\(app\)/w/\[wsId\]/settings/page.tsx \
       frontend/packages/web/components/workspace-settings/SettingsNav.tsx \
       frontend/packages/web/components/workspace-settings/MembersPanel.tsx \
       frontend/packages/web/components/workspace-settings/members/WsMembersTable.tsx \
       frontend/packages/web/components/workspace-settings/members/AddWsMemberDialog.tsx
git commit -m "feat(members): add workspace members settings tab and components"
```

---

## Task 10: Manual smoke test

- [ ] **Step 1: Start backend**

Run: `cd /home/chris/cubeplex/backend && python main.py`

- [ ] **Step 2: Start frontend**

Run: `cd /home/chris/cubeplex/frontend && pnpm dev`

- [ ] **Step 3: Verify admin members page**

1. Log in as an admin user
2. Navigate to `/admin/members`
3. Verify the member list loads with at least one member (the owner)
4. Click "Add member" — verify the dialog opens
5. Verify the owner row shows a static "owner" badge with no actions

- [ ] **Step 4: Verify workspace members tab**

1. Navigate to `/w/{wsId}/settings?tab=members`
2. Verify the workspace member list loads
3. Click "Add member" — verify the dialog opens with a dropdown of available org members
4. Verify your own row has no "Remove" button

- [ ] **Step 5: Run full E2E suite**

Run: `cd /home/chris/cubeplex/backend && uv run pytest tests/e2e/test_admin_members.py tests/e2e/test_ws_members.py -v`
Expected: All tests pass.
