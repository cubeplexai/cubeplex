# M2 · 管理员控制台骨架 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `/admin` independent-layout route group (org admin shell, opens in new tab via `<a target="_blank">`); refactor main app sidebar (remove AppTopBar, hoist sidebar to `(app)/layout.tsx`, add Workspaces nav section + AvatarPopover); consume M0's `/api/v1/admin/_extensions/manifest` to render plugin nav items + iframe pages. Batch 1 strictly skeleton: all 5 CE-native tabs (Models / Web tools / Skills / MCP / Sandbox) ship as "Coming Soon" placeholders.

**Architecture:** New `app/admin/` route group with its own layout (no main app sidebar); auth gate via new backend `GET /api/v1/admin/me` endpoint backed by `require_org_admin` dependency (current org's any-workspace admin). Main app sidebar moves up to `(app)/layout.tsx` so every authenticated page (homepage, /workspaces, /w/[wsId]/...) sees it. Plugin extension iframes loaded same-origin; CSP `frame-src 'self'`.

**Tech Stack:** Next.js 15 App Router, React 19, TypeScript, Tailwind CSS 4, shadcn/ui (popover already present, tabs to add), SWR, FastAPI, fastapi-users, SQLModel.

**Spec:** `docs/superpowers/specs/2026-04-23-admin-console-design.md`

---

## File Structure

### Create

```
backend/cubeplex/api/routes/v1/admin.py            # GET /admin/me; mounts manifest sub-router (already from M0)
backend/cubeplex/api/schemas/admin.py              # AdminMeResponse pydantic model
backend/tests/test_admin_me.py                    # /admin/me admin / member / no-membership cases
backend/tests/test_require_org_admin.py           # dependency + repo method coverage

frontend/packages/web/app/admin/layout.tsx        # Independent admin layout: top bar + sub-nav + content
frontend/packages/web/app/admin/page.tsx          # redirects to /admin/models
frontend/packages/web/app/admin/models/page.tsx
frontend/packages/web/app/admin/web-tools/page.tsx
frontend/packages/web/app/admin/skills/page.tsx
frontend/packages/web/app/admin/mcp/page.tsx
frontend/packages/web/app/admin/sandbox/page.tsx
frontend/packages/web/app/admin/ext/[plugin]/[...path]/page.tsx    # iframe extension page

frontend/packages/web/components/admin/AdminTopBar.tsx
frontend/packages/web/components/admin/AdminSubNav.tsx
frontend/packages/web/components/admin/AdminAvatarMenu.tsx
frontend/packages/web/components/admin/ComingSoonCard.tsx

frontend/packages/web/components/sidebar/WorkspacesSection.tsx
frontend/packages/web/components/sidebar/AvatarPopover.tsx

frontend/packages/web/hooks/useAdminAccess.ts
frontend/packages/web/hooks/useAdminExtensions.ts

frontend/packages/web/components/ui/tabs.tsx       # via npx shadcn add tabs
```

### Modify

```
backend/cubeplex/auth/dependencies.py               # Add require_org_admin
backend/cubeplex/repositories/membership.py         # Add user_has_role_in_org method
backend/cubeplex/api/app.py                         # Mount admin router
backend/cubeplex/api/routes/v1/workspaces.py        # GET /workspaces add last_activity_at field

frontend/packages/web/app/(app)/layout.tsx         # Drop AppTopBar; mount Sidebar around children
frontend/packages/web/app/(app)/w/[wsId]/layout.tsx # No structural change (already minimal); just confirm Sidebar isn't double-rendered
frontend/packages/web/components/layout/Sidebar.tsx  # Compose WorkspacesSection + AvatarPopover; keep recent-conversations
frontend/packages/web/components/layout/AppShell.tsx  # Drop its own <Sidebar/> (now provided by outer layout); keep resizable-panel logic
frontend/packages/web/components/workspace/WorkspaceSwitcher.tsx  # Either delete (replaced by sidebar list) or simplify to nav-section variant
frontend/packages/web/components/layout/AppTopBar.tsx  # Delete
frontend/packages/web/components/layout/AvatarMenu.tsx  # Delete (replaced by AvatarPopover)
frontend/packages/web/next.config.ts                # Add CSP header frame-src 'self'
frontend/packages/core/src/stores/workspaceStore.ts  # Add last_activity_at to Workspace type
```

---

## Tasks

### Task 1: Add shadcn `tabs` component

**Files:**
- Create: `frontend/packages/web/components/ui/tabs.tsx`

- [ ] **Step 1: Run shadcn add**

```bash
cd frontend/packages/web && npx shadcn@latest add tabs
```

If it prompts for confirmation, accept defaults. The file lands at `components/ui/tabs.tsx`.

- [ ] **Step 2: Verify import works**

```bash
cd frontend && pnpm --filter @cubeplex/web exec tsc --noEmit 2>&1 | head -5
```
Expected: no errors mentioning `tabs.tsx`.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/ui/tabs.tsx
git commit -m "chore(ui): add shadcn tabs component for admin console"
```

---

### Task 2: Backend `MembershipRepository.user_has_role_in_org`

**Files:**
- Modify: `backend/cubeplex/repositories/membership.py`
- Create: `backend/tests/test_membership_org_role.py`

- [ ] **Step 1: Inspect current MembershipRepository structure**

```bash
sed -n '1,60p' backend/cubeplex/repositories/membership.py
```

Note the existing async session pattern + `get_role(user_id, workspace_id)` method.

- [ ] **Step 2: Write failing tests**

```python
# backend/tests/test_membership_org_role.py
"""Tests for MembershipRepository.user_has_role_in_org (added by M2)."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Role
from cubeplex.repositories import MembershipRepository, OrganizationRepository, WorkspaceRepository


@pytest.mark.asyncio
async def test_user_with_admin_membership_in_org_returns_true(session: AsyncSession) -> None:
    org = await OrganizationRepository(session).create(name="Acme")
    ws = await WorkspaceRepository(session).create(org_id=org.id, name="Team")
    user_id = str(uuid4())
    await MembershipRepository(session).grant(user_id=user_id, workspace_id=ws.id, role=Role.ADMIN)

    repo = MembershipRepository(session)
    assert await repo.user_has_role_in_org(user_id=user_id, org_id=org.id, role=Role.ADMIN) is True


@pytest.mark.asyncio
async def test_user_with_only_member_role_admin_check_returns_false(session: AsyncSession) -> None:
    org = await OrganizationRepository(session).create(name="Acme")
    ws = await WorkspaceRepository(session).create(org_id=org.id, name="Team")
    user_id = str(uuid4())
    await MembershipRepository(session).grant(user_id=user_id, workspace_id=ws.id, role=Role.MEMBER)

    repo = MembershipRepository(session)
    assert await repo.user_has_role_in_org(user_id=user_id, org_id=org.id, role=Role.ADMIN) is False


@pytest.mark.asyncio
async def test_user_with_no_membership_in_org_returns_false(session: AsyncSession) -> None:
    org = await OrganizationRepository(session).create(name="Acme")
    user_id = str(uuid4())
    repo = MembershipRepository(session)
    assert await repo.user_has_role_in_org(user_id=user_id, org_id=org.id, role=Role.ADMIN) is False


@pytest.mark.asyncio
async def test_admin_in_one_workspace_grants_org_admin(session: AsyncSession) -> None:
    """User who is ADMIN in any workspace of the org passes the check."""
    org = await OrganizationRepository(session).create(name="Acme")
    ws_a = await WorkspaceRepository(session).create(org_id=org.id, name="A")
    ws_b = await WorkspaceRepository(session).create(org_id=org.id, name="B")
    user_id = str(uuid4())
    # Member in A, Admin in B → admin in org
    await MembershipRepository(session).grant(user_id=user_id, workspace_id=ws_a.id, role=Role.MEMBER)
    await MembershipRepository(session).grant(user_id=user_id, workspace_id=ws_b.id, role=Role.ADMIN)

    repo = MembershipRepository(session)
    assert await repo.user_has_role_in_org(user_id=user_id, org_id=org.id, role=Role.ADMIN) is True
```

- [ ] **Step 3: Run, verify fail**

```bash
cd backend && uv run pytest tests/test_membership_org_role.py -v
```
Expected: FAIL with `AttributeError: 'MembershipRepository' object has no attribute 'user_has_role_in_org'`.

- [ ] **Step 4: Implement the method**

Append to `backend/cubeplex/repositories/membership.py` (inside `MembershipRepository` class):

```python
    async def user_has_role_in_org(
        self,
        *,
        user_id: str,
        org_id: str,
        role: Role,
    ) -> bool:
        """True if `user_id` has `role` in any workspace belonging to `org_id`.

        v1: "org admin" = admin in any workspace of the org. M2 uses this as the
        gate for /admin/* until a real org-level role concept is introduced.
        """
        from sqlalchemy import select

        from cubeplex.models import Membership, Workspace

        stmt = (
            select(Membership.user_id)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(
                Workspace.org_id == org_id,
                Membership.user_id == user_id,
                Membership.role == role.value,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None
```

NOTE: Adjust `from sqlalchemy import select` placement to follow project conventions; if `Workspace` isn't imported at top, add the import.

- [ ] **Step 5: Run tests, verify pass**

```bash
cd backend && uv run pytest tests/test_membership_org_role.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/repositories/membership.py backend/tests/test_membership_org_role.py
git commit -m "feat(repos): MembershipRepository.user_has_role_in_org for org-level admin gate"
```

---

### Task 3: Backend `require_org_admin` dependency

**Files:**
- Modify: `backend/cubeplex/auth/dependencies.py`
- Create: `backend/tests/test_require_org_admin.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_require_org_admin.py
"""Tests for require_org_admin FastAPI dependency."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from cubeplex.auth.dependencies import require_org_admin
from cubeplex.auth.context import RequestContext
from cubeplex.models import Role


@pytest.mark.asyncio
async def test_passes_for_org_admin() -> None:
    user = MagicMock(id=str(uuid4()))
    ctx = RequestContext(user=user, org_id=str(uuid4()), workspace_id=str(uuid4()), role=Role.ADMIN)
    repo = AsyncMock()
    repo.user_has_role_in_org = AsyncMock(return_value=True)

    result = await require_org_admin(user=user, request_context=ctx, membership_repo=repo)
    assert result is user


@pytest.mark.asyncio
async def test_raises_403_for_non_admin() -> None:
    user = MagicMock(id=str(uuid4()))
    ctx = RequestContext(user=user, org_id=str(uuid4()), workspace_id=str(uuid4()), role=Role.MEMBER)
    repo = AsyncMock()
    repo.user_has_role_in_org = AsyncMock(return_value=False)

    with pytest.raises(HTTPException) as exc:
        await require_org_admin(user=user, request_context=ctx, membership_repo=repo)
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run, verify fail**

```bash
cd backend && uv run pytest tests/test_require_org_admin.py -v
```
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement `require_org_admin`**

Append to `backend/cubeplex/auth/dependencies.py`:

```python
async def require_org_admin(
    user: Annotated[User, Depends(current_active_user)],
    request_context: Annotated[RequestContext, Depends(request_context)],
    membership_repo: Annotated[
        MembershipRepository,
        Depends(lambda session=Depends(get_session): MembershipRepository(session)),
    ],
) -> User:
    """v1: user is "org admin" iff they hold ADMIN in ANY workspace of current org.

    Future: when org-level role concept exists, this implementation is replaced;
    callers (admin routes, /admin/me endpoint) are unchanged.
    """
    is_admin = await membership_repo.user_has_role_in_org(
        user_id=user.id, org_id=request_context.org_id, role=Role.ADMIN
    )
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Org admin role required",
        )
    return user
```

NOTE: The dependency requires `request_context` which itself needs a `workspace_id` path parameter. For `/admin/*` routes (no workspace in URL), wrap differently — either accept `org_id` from a different source (cookie / user default org) OR skip `request_context` for admin routes. Simpler v1 path: add a separate `current_org_context` dependency that resolves org_id from `user.default_workspace.org_id` (or similar) without needing a path param. See Task 4 for the exact wiring.

- [ ] **Step 4: Run tests, verify pass**

```bash
cd backend && uv run pytest tests/test_require_org_admin.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/auth/dependencies.py backend/tests/test_require_org_admin.py
git commit -m "feat(auth): require_org_admin dependency (v1 = any-workspace admin in current org)"
```

---

### Task 4: Backend `GET /api/v1/admin/me` endpoint

**Files:**
- Create: `backend/cubeplex/api/schemas/admin.py`
- Create: `backend/cubeplex/api/routes/v1/admin.py`
- Modify: `backend/cubeplex/api/app.py` (mount router)
- Create: `backend/tests/test_admin_me.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/test_admin_me.py
"""End-to-end test for GET /api/v1/admin/me."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_admin_user_gets_is_admin_true(client: AsyncClient, admin_user_cookie) -> None:
    resp = await client.get("/api/v1/admin/me", cookies=admin_user_cookie)
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_admin"] is True
    assert "org_id" in data
    assert "org_name" in data


@pytest.mark.asyncio
async def test_member_user_gets_is_admin_false(client: AsyncClient, member_user_cookie) -> None:
    resp = await client.get("/api/v1/admin/me", cookies=member_user_cookie)
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_admin"] is False
    # org info still returned (frontend uses for display)
    assert "org_id" in data


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/admin/me")
    assert resp.status_code == 401
```

NOTE: `admin_user_cookie` and `member_user_cookie` fixtures must exist in `backend/tests/conftest.py`. If absent, scaffold them (register a user → grant ADMIN/MEMBER → return the auth cookie). Pattern matches existing `test_rbac.py` fixtures.

- [ ] **Step 2: Run, verify fail (404 — endpoint doesn't exist yet)**

```bash
cd backend && uv run pytest tests/test_admin_me.py -v
```
Expected: FAIL.

- [ ] **Step 3: Define response schema**

```python
# backend/cubeplex/api/schemas/admin.py
"""Admin route response schemas."""

from pydantic import BaseModel


class AdminMeResponse(BaseModel):
    is_admin: bool
    org_id: str
    org_name: str
```

- [ ] **Step 4: Define current-org dependency + admin router**

```python
# backend/cubeplex/api/routes/v1/admin.py
"""Admin routes: /admin/me + manifest mount.

The /admin/me endpoint returns 200 with is_admin=true|false (NOT 403)
because the frontend uses it to decide whether to show admin entry
points; only the ROUTING gate (require_org_admin) returns 403.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.admin import AdminMeResponse
from cubeplex.auth.users import current_active_user
from cubeplex.db import get_session
from cubeplex.models import Role, User
from cubeplex.repositories import MembershipRepository, OrganizationRepository

router = APIRouter(prefix="/admin", tags=["admin"])


async def _resolve_current_org_id(
    user: User,
    session: AsyncSession,
) -> str:
    """v1: take the user's first workspace's org as 'current org'.

    Future: cookie-driven multi-org switching reads current_org_id from a
    cookie set by an OrgSwitcher endpoint. v1 has no switcher → just pick.
    """
    from sqlalchemy import select

    from cubeplex.models import Membership, Workspace

    stmt = (
        select(Workspace.org_id)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
        .order_by(Workspace.created_at)
        .limit(1)
    )
    result = await session.execute(stmt)
    org_id = result.scalar_one_or_none()
    if org_id is None:
        # User has no workspace memberships at all — shouldn't happen for a
        # registered user (register bootstrap creates one), but guard anyway.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No org membership found for user",
        )
    return org_id


@router.get("/me", response_model=AdminMeResponse)
async def get_admin_me(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminMeResponse:
    org_id = await _resolve_current_org_id(user, session)
    is_admin = await MembershipRepository(session).user_has_role_in_org(
        user_id=user.id, org_id=org_id, role=Role.ADMIN
    )
    org = await OrganizationRepository(session).get(org_id)
    return AdminMeResponse(
        is_admin=is_admin,
        org_id=org_id,
        org_name=org.name if org else "",
    )
```

- [ ] **Step 5: Mount router in app.py**

In `backend/cubeplex/api/app.py`, find the section where routers are included (typically near `include_router(workspaces.router, ...)`). Add:

```python
from cubeplex.api.routes.v1 import admin
app.include_router(admin.router, prefix="/api/v1")
```

- [ ] **Step 6: Run tests, verify pass**

```bash
cd backend && uv run pytest tests/test_admin_me.py -v
```
Expected: PASS (3 tests). If `admin_user_cookie` fixture is missing, scaffold it first.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/api/schemas/admin.py backend/cubeplex/api/routes/v1/admin.py backend/cubeplex/api/app.py backend/tests/test_admin_me.py
git commit -m "feat(api): GET /api/v1/admin/me returning is_admin + org info"
```

---

### Task 5: Backend `GET /api/v1/workspaces` add `last_activity_at`

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/workspaces.py`
- Modify: `backend/cubeplex/api/schemas/workspace.py` (or wherever WorkspaceResponse lives)
- Create: `backend/tests/test_workspaces_last_activity.py`

- [ ] **Step 1: Locate the existing GET workspaces handler + schema**

```bash
grep -n "GET\|@router.get\|workspaces" backend/cubeplex/api/routes/v1/workspaces.py | head
grep -rn "class Workspace.*Response\|WorkspaceRead" backend/cubeplex/api/schemas/ 2>/dev/null
```

- [ ] **Step 2: Write failing test**

```python
# backend/tests/test_workspaces_last_activity.py
"""GET /api/v1/workspaces includes last_activity_at."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_workspace_response_has_last_activity_at(
    client: AsyncClient, member_user_cookie
) -> None:
    resp = await client.get("/api/v1/workspaces", cookies=member_user_cookie)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "last_activity_at" in data[0]
    # ISO-8601 string or null
    val = data[0]["last_activity_at"]
    assert val is None or isinstance(val, str)
```

- [ ] **Step 3: Run, verify fail**

```bash
cd backend && uv run pytest tests/test_workspaces_last_activity.py -v
```
Expected: FAIL.

- [ ] **Step 4: Add `last_activity_at` to response schema + computation**

In the workspace schema (e.g. `backend/cubeplex/api/schemas/workspace.py`):

```python
class WorkspaceResponse(BaseModel):
    id: str
    org_id: str
    name: str
    role: str
    last_activity_at: str | None = None  # ISO-8601 UTC; null if no conversations yet
```

In the GET handler in `backend/cubeplex/api/routes/v1/workspaces.py`, compute via aggregate over `Conversation.updated_at`:

```python
from sqlalchemy import func, select

from cubeplex.models import Conversation
from cubeplex.utils.time import utc_isoformat  # project standard for UTC-tagged ISO


async def list_workspaces(...) -> list[WorkspaceResponse]:
    # ... existing code that fetches the user's workspaces ...

    # cubeplex doesn't have a Message table — messages live in LangGraph
    # checkpointer thread state. But Conversation.updated_at is bumped by
    # ConversationRepository.update_timestamp() on every message round-trip
    # (called from POST /api/v1/ws/{ws}/conversations/{id}/messages).
    # So aggregating max(Conversation.updated_at) per workspace gives accurate
    # "last activity" semantics.
    activity_stmt = (
        select(
            Conversation.workspace_id,
            func.max(Conversation.updated_at).label("last_at"),
        )
        .where(Conversation.workspace_id.in_([ws.id for ws in workspaces]))
        .group_by(Conversation.workspace_id)
    )
    activity_rows = (await session.execute(activity_stmt)).all()
    activity_map = {r.workspace_id: r.last_at for r in activity_rows}

    return [
        WorkspaceResponse(
            id=ws.id,
            org_id=ws.org_id,
            name=ws.name,
            role=role_for_user(ws, user),
            last_activity_at=utc_isoformat(activity_map.get(ws.id)) if activity_map.get(ws.id) else None,
        )
        for ws in workspaces
    ]
```

NOTE: Use `utc_isoformat()` per memory `feedback_timestamp_handling.md` — DB datetimes need explicit UTC offset for frontend.

- [ ] **Step 5: Run tests, verify pass**

```bash
cd backend && uv run pytest tests/test_workspaces_last_activity.py -v
```
Expected: PASS.

- [ ] **Step 6: Update frontend `Workspace` type in `@cubeplex/core`**

In `frontend/packages/core/src/stores/workspaceStore.ts`, add to the `Workspace` type:

```typescript
export type Workspace = {
  id: string
  org_id: string
  name: string
  role: 'admin' | 'member'
  last_activity_at: string | null  // ISO-8601 with offset, or null
}
```

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/api/schemas/workspace.py backend/cubeplex/api/routes/v1/workspaces.py backend/tests/test_workspaces_last_activity.py frontend/packages/core/src/stores/workspaceStore.ts
git commit -m "feat(workspaces): expose last_activity_at for sidebar sort + frontend type"
```

---

### Task 6: Frontend `useAdminAccess` hook

**Files:**
- Create: `frontend/packages/web/hooks/useAdminAccess.ts`

- [ ] **Step 1: Verify SWR is installed**

```bash
cd frontend && pnpm --filter @cubeplex/web list swr 2>&1 | head -5
```
If absent: `cd frontend && pnpm --filter @cubeplex/web add swr`

- [ ] **Step 2: Implement the hook**

```typescript
// frontend/packages/web/hooks/useAdminAccess.ts
'use client'

import useSWR from 'swr'

type AdminMeResponse = {
  is_admin: boolean
  org_id: string
  org_name: string
}

const fetcher = async (url: string): Promise<AdminMeResponse> => {
  const res = await fetch(url, { credentials: 'include' })
  if (res.status === 401) {
    throw new Error('unauthorized')
  }
  if (!res.ok) {
    throw new Error(`admin/me failed: ${res.status}`)
  }
  return res.json()
}

export function useAdminAccess() {
  const { data, error, isLoading } = useSWR<AdminMeResponse>(
    '/api/v1/admin/me',
    fetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  return {
    isAdmin: data?.is_admin ?? false,
    orgId: data?.org_id ?? null,
    orgName: data?.org_name ?? '',
    loading: isLoading,
    error: error as Error | undefined,
  }
}
```

- [ ] **Step 3: Manually smoke (skip if test framework wired)**

Start dev server, log in as an admin user, open browser console:
```javascript
fetch('/api/v1/admin/me', { credentials: 'include' }).then(r => r.json()).then(console.log)
```
Expected: `{is_admin: true, org_id: "...", org_name: "..."}`

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/hooks/useAdminAccess.ts
git commit -m "feat(web): useAdminAccess hook backed by /admin/me"
```

---

### Task 7: Frontend `<WorkspacesSection />` component

**Files:**
- Create: `frontend/packages/web/components/sidebar/WorkspacesSection.tsx`

- [ ] **Step 1: Implement WorkspacesSection**

```typescript
// frontend/packages/web/components/sidebar/WorkspacesSection.tsx
'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useState, useMemo } from 'react'
import { useWorkspaceStore } from '@cubeplex/core'
import { Folder, Plus } from 'lucide-react'
import { cn } from '@/lib/utils'

const DEFAULT_VISIBLE = 5

export function WorkspacesSection() {
  const workspaces = useWorkspaceStore((s) => s.workspaces)
  const pathname = usePathname()
  const [showAll, setShowAll] = useState(false)

  // Sort by last_activity_at desc; nulls last
  const sorted = useMemo(() => {
    return [...workspaces].sort((a, b) => {
      const at = a.last_activity_at ? Date.parse(a.last_activity_at) : 0
      const bt = b.last_activity_at ? Date.parse(b.last_activity_at) : 0
      return bt - at
    })
  }, [workspaces])

  const visible = showAll ? sorted : sorted.slice(0, DEFAULT_VISIBLE)
  const hidden = sorted.length - DEFAULT_VISIBLE

  // Detect current workspace from URL: /w/[wsId]/...
  const currentWsId = useMemo(() => {
    const match = pathname.match(/^\/w\/([^/]+)/)
    return match ? match[1] : null
  }, [pathname])

  return (
    <div className="px-2 py-2">
      <p className="px-2 text-xs font-medium text-muted-foreground mb-1">工作区</p>
      <ul className="space-y-0.5">
        {visible.map((ws) => (
          <li key={ws.id}>
            <Link
              href={`/w/${ws.id}`}
              className={cn(
                'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent transition-colors',
                ws.id === currentWsId && 'bg-accent font-medium',
              )}
            >
              <Folder className="size-3.5 text-muted-foreground shrink-0" />
              <span className="truncate flex-1">{ws.name}</span>
              {ws.id === currentWsId && (
                <span className="size-1.5 rounded-full bg-primary shrink-0" />
              )}
            </Link>
          </li>
        ))}
        {hidden > 0 && !showAll && (
          <li>
            <button
              type="button"
              onClick={() => setShowAll(true)}
              className="w-full text-left px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
            >
              更多 ({hidden}) ↓
            </button>
          </li>
        )}
        <li>
          <Link
            href="/workspaces"
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-accent"
          >
            <Plus className="size-3.5" />
            <span>新建工作区</span>
          </Link>
        </li>
      </ul>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend && pnpm --filter @cubeplex/web exec tsc --noEmit 2>&1 | head -10
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/sidebar/WorkspacesSection.tsx
git commit -m "feat(sidebar): WorkspacesSection list with last-activity sort + show-more"
```

---

### Task 8: Frontend `<AvatarPopover />` component

**Files:**
- Create: `frontend/packages/web/components/sidebar/AvatarPopover.tsx`

- [ ] **Step 1: Implement AvatarPopover**

```typescript
// frontend/packages/web/components/sidebar/AvatarPopover.tsx
'use client'

import { useRouter } from 'next/navigation'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Button } from '@/components/ui/button'
import { ThemeToggle } from '@/components/ui/theme-toggle'
import {
  createApiClient,
  logoutUser,
  useAuthStore,
  useConversationStore,
  useWorkspaceStore,
} from '@cubeplex/core'
import { useAdminAccess } from '@/hooks/useAdminAccess'
import { Shield, LogOut } from 'lucide-react'

export function AvatarPopover() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)
  const { isAdmin } = useAdminAccess()

  const initials = user?.email ? user.email[0].toUpperCase() : '?'

  const onLogout = async () => {
    const client = createApiClient('')
    try {
      await logoutUser(client)
    } catch {
      /* ignore */
    }
    useAuthStore.setState({ user: null })
    useConversationStore.setState({ conversations: [], activeId: null })
    useWorkspaceStore.setState({ workspaces: [] })
    router.replace('/login')
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-accent transition-colors"
        >
          <div className="size-7 rounded-full bg-primary text-white flex items-center justify-center text-xs font-medium shrink-0">
            {initials}
          </div>
          <span className="text-sm truncate flex-1 text-left">{user?.email ?? '...'}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        className="w-56 p-1"
        sideOffset={4}
      >
        <div className="px-2 py-1.5 text-xs text-muted-foreground border-b mb-1">
          {user?.email}
        </div>

        {isAdmin && (
          <a
            href="/admin"
            target="_blank"
            rel="noopener"
            className="flex items-center gap-2 px-2 py-1.5 rounded-sm text-sm hover:bg-accent"
          >
            <Shield className="size-4" />
            <span>管理后台</span>
          </a>
        )}

        <div className="flex items-center gap-2 px-2 py-1.5 rounded-sm text-sm hover:bg-accent">
          <ThemeToggle />
          <span>主题</span>
        </div>

        <button
          type="button"
          onClick={onLogout}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded-sm text-sm hover:bg-accent text-destructive"
        >
          <LogOut className="size-4" />
          <span>退出</span>
        </button>
      </PopoverContent>
    </Popover>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend && pnpm --filter @cubeplex/web exec tsc --noEmit 2>&1 | head -10
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/sidebar/AvatarPopover.tsx
git commit -m "feat(sidebar): AvatarPopover with admin-only entry + theme + logout"
```

---

### Task 9: Refactor `Sidebar.tsx` to compose new sections

**Files:**
- Modify: `frontend/packages/web/components/layout/Sidebar.tsx`

- [ ] **Step 1: Inspect current Sidebar behavior**

```bash
cat frontend/packages/web/components/layout/Sidebar.tsx
```

The current Sidebar is workspace-coupled (uses `useWorkspaceContext`); we need it to work on every page including non-workspace ones.

- [ ] **Step 2: Refactor Sidebar**

Replace the contents of `frontend/packages/web/components/layout/Sidebar.tsx` with:

```typescript
'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useConversationStore, createApiClient } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Plus, Trash2, Box } from 'lucide-react'
import { WorkspacesSection } from '@/components/sidebar/WorkspacesSection'
import { AvatarPopover } from '@/components/sidebar/AvatarPopover'

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return '刚刚'
  if (diffMins < 60) return `${diffMins}m 前`
  if (diffHours < 24) return `${diffHours}h 前`
  return `${diffDays}d 前`
}

export function Sidebar() {
  const { conversations, activeId, remove } = useConversationStore()
  const pathname = usePathname()

  // Detect current workspace from URL (no longer requires WorkspaceContext)
  const wsMatch = pathname.match(/^\/w\/([^/]+)/)
  const currentWsId = wsMatch ? wsMatch[1] : null
  const newChatHref = currentWsId ? `/w/${currentWsId}` : '/'

  const handleDeleteClick = async (e: React.MouseEvent, id: string) => {
    e.preventDefault()
    const client = createApiClient('')
    if (currentWsId) client.setWorkspaceId(currentWsId)
    try {
      await remove(client, id)
    } catch (err) {
      console.error('Failed to delete conversation:', err)
    }
  }

  return (
    <div className="w-56 bg-card border-r border-border flex flex-col h-screen shrink-0">
      {/* Brand + new chat */}
      <div className="px-4 pt-4 pb-3 border-b border-border">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-6 h-6 rounded-md bg-primary flex items-center justify-center shrink-0">
            <Box className="size-3.5 text-white" strokeWidth={2.5} />
          </div>
          <span className="text-sm font-semibold tracking-tight">cubeplex</span>
        </div>
        <Link href={newChatHref}>
          <Button variant="default" className="w-full gap-2" size="sm">
            <Plus className="size-3.5" />
            新建对话
          </Button>
        </Link>
      </div>

      {/* Workspaces section */}
      <WorkspacesSection />

      {/* Recent conversations */}
      <div className="px-2 pt-2 pb-1">
        <p className="px-2 text-xs font-medium text-muted-foreground">最近会话</p>
      </div>
      <ScrollArea className="flex-1 px-2">
        <ul className="space-y-0.5">
          {conversations.map((c) => (
            <li key={c.id}>
              <Link
                href={currentWsId ? `/w/${currentWsId}/conversations/${c.id}` : '#'}
                className={`group flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent ${
                  c.id === activeId ? 'bg-accent' : ''
                }`}
              >
                <span className="truncate flex-1">{c.title || '(untitled)'}</span>
                <span className="text-[10px] text-muted-foreground shrink-0">
                  {formatRelativeTime(c.updated_at)}
                </span>
                <button
                  onClick={(e) => handleDeleteClick(e, c.id)}
                  className="opacity-0 group-hover:opacity-100 p-0.5 hover:text-destructive"
                  aria-label="Delete conversation"
                >
                  <Trash2 className="size-3" />
                </button>
              </Link>
            </li>
          ))}
        </ul>
      </ScrollArea>

      {/* Footer: avatar popover */}
      <div className="border-t border-border p-2">
        <AvatarPopover />
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Type-check**

```bash
cd frontend && pnpm --filter @cubeplex/web exec tsc --noEmit 2>&1 | head -10
```
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/layout/Sidebar.tsx
git commit -m "refactor(sidebar): compose WorkspacesSection + AvatarPopover; drop workspace-context coupling"
```

---

### Task 10: Hoist Sidebar to `(app)/layout.tsx`; remove `AppTopBar`

**Files:**
- Modify: `frontend/packages/web/app/(app)/layout.tsx`
- Modify: `frontend/packages/web/components/layout/AppShell.tsx`
- Delete: `frontend/packages/web/components/layout/AppTopBar.tsx`
- Delete: `frontend/packages/web/components/layout/AvatarMenu.tsx`
- Delete: `frontend/packages/web/components/workspace/WorkspaceSwitcher.tsx`

- [ ] **Step 1: Refactor `(app)/layout.tsx` to mount Sidebar**

Replace `frontend/packages/web/app/(app)/layout.tsx` with:

```typescript
'use client'

import { useEffect, useMemo } from 'react'
import { createApiClient, useAuthStore, useWorkspaceStore } from '@cubeplex/core'
import { useAuthRedirect } from '@/hooks/useAuthRedirect'
import { Sidebar } from '@/components/layout/Sidebar'

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const client = useMemo(() => createApiClient(''), [])
  useAuthRedirect(client)

  useEffect(() => {
    useAuthStore.getState().loadMe(client)
    useWorkspaceStore.getState().fetchList(client)
  }, [client])

  return (
    <div className="flex h-screen bg-background text-foreground">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">{children}</div>
    </div>
  )
}
```

- [ ] **Step 2: Refactor `AppShell.tsx` to drop its own Sidebar**

The Sidebar is now provided by the outer layout. Strip `<Sidebar />` and the brand-flex-row from AppShell, leaving only the resizable artifact-panel layout:

```typescript
'use client'

import { ReactNode } from 'react'
import { ThemeToggle } from '@/components/ui/theme-toggle'
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from '@/components/ui/resizable'
import { ToolDetailPanel } from '@/components/panel/ToolDetailPanel'
import { ArtifactPanel } from '@/components/panel/artifact/ArtifactPanel'
import { usePanelStore } from '@cubeplex/core'

interface AppShellProps {
  children: ReactNode
  headerTitle?: string
}

export function AppShell({ children, headerTitle }: AppShellProps) {
  const viewType = usePanelStore((s) => s.view.type)
  const panelOpen = viewType !== 'closed'

  return (
    <ResizablePanelGroup orientation="horizontal" className="h-full">
      <ResizablePanel defaultSize={panelOpen ? 50 : 100} minSize={30}>
        <div className="flex flex-col h-full overflow-hidden">
          <header className="h-11 border-b border-border flex items-center px-4 shrink-0">
            <span className="text-sm text-muted-foreground truncate flex-1">
              {headerTitle || ''}
            </span>
            <ThemeToggle />
          </header>
          <main className="flex-1 flex flex-col overflow-hidden">{children}</main>
        </div>
      </ResizablePanel>

      {panelOpen && (
        <>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize={50} minSize={25}>
            {viewType === 'artifact' ? <ArtifactPanel /> : <ToolDetailPanel />}
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  )
}
```

- [ ] **Step 3: Delete obsolete files**

```bash
git rm frontend/packages/web/components/layout/AppTopBar.tsx
git rm frontend/packages/web/components/layout/AvatarMenu.tsx
git rm frontend/packages/web/components/workspace/WorkspaceSwitcher.tsx
```

- [ ] **Step 4: Update any remaining imports**

```bash
grep -rn "AppTopBar\|AvatarMenu\|WorkspaceSwitcher" frontend/packages/web/ --include="*.tsx" --include="*.ts" 2>/dev/null
```
Expected: no results. Fix any leftovers (they should be only in the deleted files themselves).

- [ ] **Step 5: Smoke test (start dev server, verify all pages)**

```bash
cd frontend && pnpm --filter @cubeplex/web dev &
sleep 5
# Open in browser:
#   http://localhost:3000/                  -- sidebar visible
#   http://localhost:3000/workspaces        -- sidebar visible
#   http://localhost:3000/w/<wsId>/...      -- sidebar visible (no double sidebar)
kill %1
```

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/app/\(app\)/layout.tsx frontend/packages/web/components/layout/AppShell.tsx
git commit -m "refactor(layout): hoist Sidebar to (app)/layout; drop AppTopBar / AvatarMenu / WorkspaceSwitcher"
```

---

### Task 11: Frontend `useAdminExtensions` hook

**Files:**
- Create: `frontend/packages/web/hooks/useAdminExtensions.ts`

- [ ] **Step 1: Implement hook**

```typescript
// frontend/packages/web/hooks/useAdminExtensions.ts
'use client'

import useSWR from 'swr'

export type AdminNavItem = {
  id: string
  label: string
  icon: string | null
  section: string
  order: number
  url_path: string
}

export type AdminExtensionEntry = {
  plugin: string
  nav_items: AdminNavItem[]
  iframe_base_url: string
}

const fetcher = async (url: string): Promise<AdminExtensionEntry[]> => {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`manifest fetch failed: ${res.status}`)
  return res.json()
}

export function useAdminExtensions() {
  const { data, error, isLoading } = useSWR<AdminExtensionEntry[]>(
    '/api/v1/admin/_extensions/manifest',
    fetcher,
    { revalidateOnFocus: false },
  )
  return {
    extensions: data ?? [],
    loading: isLoading,
    error: error as Error | undefined,
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/web/hooks/useAdminExtensions.ts
git commit -m "feat(web): useAdminExtensions hook for plugin manifest"
```

---

### Task 12: Frontend `<AdminTopBar />` + `<AdminSubNav />` + `<AdminAvatarMenu />`

**Files:**
- Create: `frontend/packages/web/components/admin/AdminTopBar.tsx`
- Create: `frontend/packages/web/components/admin/AdminSubNav.tsx`
- Create: `frontend/packages/web/components/admin/AdminAvatarMenu.tsx`
- Create: `frontend/packages/web/components/admin/ComingSoonCard.tsx`

- [ ] **Step 1: Implement AdminTopBar**

```typescript
// frontend/packages/web/components/admin/AdminTopBar.tsx
'use client'

import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Box } from 'lucide-react'
import { AdminAvatarMenu } from './AdminAvatarMenu'

interface AdminTopBarProps {
  orgName: string
}

function handleBackToApp() {
  if (typeof window !== 'undefined') {
    if (window.opener) {
      window.close()
    } else {
      window.location.href = '/'
    }
  }
}

export function AdminTopBar({ orgName }: AdminTopBarProps) {
  return (
    <header className="flex items-center gap-4 border-b border-border bg-card px-4 py-3 h-14 shrink-0">
      <div className="flex items-center gap-2">
        <div className="w-6 h-6 rounded-md bg-primary flex items-center justify-center shrink-0">
          <Box className="size-3.5 text-white" strokeWidth={2.5} />
        </div>
        <span className="text-sm font-semibold">cubeplex</span>
      </div>
      <Separator orientation="vertical" className="h-6" />
      <h1 className="text-sm font-medium">管理后台</h1>
      {/* v1: static org label; multi-org future replaces this with OrgSwitcher dropdown */}
      <span className="text-sm text-muted-foreground">· {orgName}</span>

      <div className="ml-auto flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={handleBackToApp}>
          回应用
        </Button>
        <AdminAvatarMenu />
      </div>
    </header>
  )
}
```

- [ ] **Step 2: Implement AdminAvatarMenu (simpler than sidebar AvatarPopover — just user info + logout)**

```typescript
// frontend/packages/web/components/admin/AdminAvatarMenu.tsx
'use client'

import { useRouter } from 'next/navigation'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { createApiClient, logoutUser, useAuthStore } from '@cubeplex/core'

export function AdminAvatarMenu() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)
  const initials = user?.email ? user.email[0].toUpperCase() : '?'

  const onLogout = async () => {
    const client = createApiClient('')
    try {
      await logoutUser(client)
    } catch {
      /* ignore */
    }
    router.replace('/login')
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="size-8 rounded-full bg-primary text-white flex items-center justify-center text-xs font-medium"
        >
          {initials}
        </button>
      </PopoverTrigger>
      <PopoverContent side="bottom" align="end" className="w-48 p-1">
        <div className="px-2 py-1.5 text-xs text-muted-foreground border-b mb-1">
          {user?.email}
        </div>
        <button
          type="button"
          onClick={onLogout}
          className="w-full text-left px-2 py-1.5 rounded-sm text-sm hover:bg-accent text-destructive"
        >
          退出
        </button>
      </PopoverContent>
    </Popover>
  )
}
```

- [ ] **Step 3: Implement AdminSubNav**

```typescript
// frontend/packages/web/components/admin/AdminSubNav.tsx
'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Cpu, Globe, Sparkles, Plug, Box, Puzzle } from 'lucide-react'
import { Separator } from '@/components/ui/separator'
import { useAdminExtensions } from '@/hooks/useAdminExtensions'
import { cn } from '@/lib/utils'

const NATIVE_ITEMS = [
  { href: '/admin/models', label: '模型', icon: Cpu },
  { href: '/admin/web-tools', label: 'Web 工具', icon: Globe },
  { href: '/admin/skills', label: '技能管理', icon: Sparkles },
  { href: '/admin/mcp', label: 'MCP 连接器', icon: Plug },
  { href: '/admin/sandbox', label: '沙盒', icon: Box },
]

export function AdminSubNav() {
  const pathname = usePathname()
  const { extensions } = useAdminExtensions()

  // Flatten plugin nav items: each entry contributes 0+ items
  const extItems = extensions.flatMap((ext) =>
    ext.nav_items.map((item) => ({
      href: `/admin/ext/${ext.plugin}/${item.url_path}`,
      label: item.label,
      icon: Puzzle,  // fallback; future: dynamic lucide icon by name
    })),
  )

  return (
    <nav className="w-56 border-r border-border bg-card flex flex-col p-2 overflow-y-auto">
      <ul className="space-y-0.5">
        {NATIVE_ITEMS.map((item) => {
          const Icon = item.icon
          const active = pathname === item.href || pathname.startsWith(item.href + '/')
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                className={cn(
                  'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent transition-colors',
                  active && 'bg-accent font-medium',
                )}
              >
                <Icon className="size-3.5 text-muted-foreground" />
                <span className="truncate">{item.label}</span>
              </Link>
            </li>
          )
        })}

        {extItems.length > 0 && (
          <>
            <li className="py-1">
              <Separator />
            </li>
            <li>
              <p className="px-2 py-1 text-xs text-muted-foreground">扩展</p>
            </li>
            {extItems.map((item) => {
              const Icon = item.icon
              const active = pathname === item.href
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className={cn(
                      'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent transition-colors',
                      active && 'bg-accent font-medium',
                    )}
                  >
                    <Icon className="size-3.5 text-muted-foreground" />
                    <span className="truncate">{item.label}</span>
                  </Link>
                </li>
              )
            })}
          </>
        )}
      </ul>
    </nav>
  )
}
```

- [ ] **Step 4: Implement ComingSoonCard**

```typescript
// frontend/packages/web/components/admin/ComingSoonCard.tsx
interface ComingSoonCardProps {
  title: string
  description: string
  backlogRef: string
}

export function ComingSoonCard({ title, description, backlogRef }: ComingSoonCardProps) {
  return (
    <div className="max-w-2xl mx-auto mt-12 px-6">
      <h2 className="text-2xl font-semibold mb-3">{title}</h2>
      <p className="text-muted-foreground mb-8">{description}</p>
      <div className="rounded-lg border border-dashed border-border p-8 text-center">
        <p className="text-sm font-medium mb-1">本版本不可用</p>
        <p className="text-xs text-muted-foreground">实现归属：{backlogRef}</p>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/admin/
git commit -m "feat(admin): AdminTopBar + AdminSubNav + AdminAvatarMenu + ComingSoonCard"
```

---

### Task 13: Admin layout + 5 CE tab placeholder pages

**Files:**
- Create: `frontend/packages/web/app/admin/layout.tsx`
- Create: `frontend/packages/web/app/admin/page.tsx`
- Create: `frontend/packages/web/app/admin/models/page.tsx`
- Create: `frontend/packages/web/app/admin/web-tools/page.tsx`
- Create: `frontend/packages/web/app/admin/skills/page.tsx`
- Create: `frontend/packages/web/app/admin/mcp/page.tsx`
- Create: `frontend/packages/web/app/admin/sandbox/page.tsx`

- [ ] **Step 1: Implement admin layout with auth gate**

```typescript
// frontend/packages/web/app/admin/layout.tsx
'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { AdminTopBar } from '@/components/admin/AdminTopBar'
import { AdminSubNav } from '@/components/admin/AdminSubNav'
import { useAdminAccess } from '@/hooks/useAdminAccess'

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { isAdmin, orgName, loading, error } = useAdminAccess()
  const router = useRouter()

  useEffect(() => {
    if (loading) return
    if (error) {
      router.replace('/login?next=/admin')
      return
    }
    if (!isAdmin) {
      router.replace('/')
    }
  }, [loading, isAdmin, error, router])

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">
        Loading…
      </div>
    )
  }
  if (!isAdmin) return null

  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      <AdminTopBar orgName={orgName} />
      <div className="flex flex-1 overflow-hidden">
        <AdminSubNav />
        <main className="flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Implement admin index → redirect**

```typescript
// frontend/packages/web/app/admin/page.tsx
import { redirect } from 'next/navigation'

export default function AdminIndex() {
  redirect('/admin/models')
}
```

- [ ] **Step 3: Implement 5 CE tab pages (all "Coming Soon")**

`frontend/packages/web/app/admin/models/page.tsx`:
```typescript
import { ComingSoonCard } from '@/components/admin/ComingSoonCard'

export default function ModelsPage() {
  return (
    <ComingSoonCard
      title="模型管理"
      description="按 provider 列出可用模型，配置组织默认模型与 fallback 链。"
      backlogRef="M2 完整版（v1 后续 spec）"
    />
  )
}
```

`frontend/packages/web/app/admin/web-tools/page.tsx`:
```typescript
import { ComingSoonCard } from '@/components/admin/ComingSoonCard'

export default function WebToolsPage() {
  return (
    <ComingSoonCard
      title="Web 工具"
      description="搜索服务提供商配置（除默认外至少支持 1-2 个可切换）。"
      backlogRef="M2 完整版（v1 后续 spec）"
    />
  )
}
```

`frontend/packages/web/app/admin/skills/page.tsx`:
```typescript
import { ComingSoonCard } from '@/components/admin/ComingSoonCard'

export default function SkillsPage() {
  return (
    <ComingSoonCard
      title="技能管理"
      description="安装 / 禁用 / 版本 / workspace 可见性。"
      backlogRef="M3 Skills 市场"
    />
  )
}
```

`frontend/packages/web/app/admin/mcp/page.tsx`:
```typescript
import { ComingSoonCard } from '@/components/admin/ComingSoonCard'

export default function McpPage() {
  return (
    <ComingSoonCard
      title="MCP 连接器"
      description="新增 / 编辑 MCP 服务，凭证绑定走 Credential Vault。"
      backlogRef="M2 完整版 + M1-E4 Credential Vault"
    />
  )
}
```

`frontend/packages/web/app/admin/sandbox/page.tsx`:
```typescript
import { ComingSoonCard } from '@/components/admin/ComingSoonCard'

export default function SandboxPage() {
  return (
    <ComingSoonCard
      title="沙盒"
      description="指定默认镜像与资源上限。"
      backlogRef="M2 完整版（v1 后续 spec）"
    />
  )
}
```

- [ ] **Step 4: Smoke test**

```bash
cd frontend && pnpm --filter @cubeplex/web dev &
sleep 5
# In browser, log in as admin user, then:
#   http://localhost:3000/admin           -- should redirect to /admin/models
#   http://localhost:3000/admin/skills    -- should show "技能管理 / Coming Soon"
# Then log in as non-admin user:
#   http://localhost:3000/admin           -- should redirect to /
kill %1
```

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/app/admin/
git commit -m "feat(admin): /admin layout with auth gate + 5 CE tab Coming Soon placeholders"
```

---

### Task 14: Plugin extension iframe page

**Files:**
- Create: `frontend/packages/web/app/admin/ext/[plugin]/[...path]/page.tsx`

- [ ] **Step 1: Implement extension page**

```typescript
// frontend/packages/web/app/admin/ext/[plugin]/[...path]/page.tsx
'use client'

import { use } from 'react'
import { useAdminExtensions } from '@/hooks/useAdminExtensions'

interface ExtensionPageProps {
  params: Promise<{ plugin: string; path?: string[] }>
}

export default function ExtensionPage({ params }: ExtensionPageProps) {
  const { plugin, path } = use(params)
  const { extensions, loading } = useAdminExtensions()

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Loading extension…
      </div>
    )
  }

  const ext = extensions.find((e) => e.plugin === plugin)
  if (!ext) {
    return (
      <div className="max-w-2xl mx-auto mt-12 px-6">
        <h2 className="text-2xl font-semibold mb-3">未知扩展</h2>
        <p className="text-muted-foreground">
          找不到名为 <code>{plugin}</code> 的插件 —— 它可能未安装或已禁用。
        </p>
      </div>
    )
  }

  const subPath = (path ?? []).join('/')
  const iframeUrl = `${ext.iframe_base_url}${subPath}`

  return (
    <iframe
      src={iframeUrl}
      className="h-full w-full border-0"
      sandbox="allow-scripts allow-forms allow-same-origin"
      title={`Extension: ${plugin}`}
    />
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/web/app/admin/ext/
git commit -m "feat(admin): plugin extension iframe page (consumes M0 manifest)"
```

---

### Task 15: CSP `frame-src 'self'` in `next.config.ts`

**Files:**
- Modify: `frontend/packages/web/next.config.ts`

- [ ] **Step 1: Inspect current next.config.ts**

```bash
cat frontend/packages/web/next.config.ts
```

- [ ] **Step 2: Add CSP header in headers() callback**

Modify `next.config.ts` to add a `headers()` async function (or extend existing one):

```typescript
import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  // ... existing config (compress: false from prior fix; keep) ...
  async headers() {
    return [
      {
        source: '/admin/:path*',
        headers: [
          {
            key: 'Content-Security-Policy',
            value: "frame-src 'self'; default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:",
          },
        ],
      },
    ]
  },
}

export default nextConfig
```

NOTE: The `default-src` portion includes `'unsafe-inline' 'unsafe-eval'` because Next.js dev mode requires them (React Refresh injects inline scripts; HMR uses `eval()`). The **security-critical** part of this CSP is `frame-src 'self'` (prevents arbitrary iframe URLs from manifest), which is enforced regardless of dev/prod mode. Production CSP tightening (NODE_ENV-driven, nonce-based `script-src`, drop unsafe-eval) is tracked in **M12 · 开源工程基建** scope per backlog — out of M2 batch 1 scope.

- [ ] **Step 3: Verify dev server starts without errors**

```bash
cd frontend && pnpm --filter @cubeplex/web dev &
sleep 5
curl -s -I http://localhost:3000/admin/models | grep -i content-security-policy
kill %1
```
Expected: header present.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/next.config.ts
git commit -m "feat(admin): set CSP frame-src 'self' for /admin routes (plugin iframe safety)"
```

---

### Task 16: E2E — admin console sidebar + /admin gating

Per CLAUDE.md project rule "Focus on E2E tests". Protocol / hook-level tests from earlier tasks are not sufficient; this task drives the browser against real backend + frontend to cover: sidebar structure, admin popover gating, `/admin` route, `org_admin` enforcement, and non-admin redirect. Uses the existing frontend e2e harness.

**Files:**
- Create: `frontend/packages/web/e2e/admin-console-skeleton.spec.ts`

- [ ] **Step 1: Write failing Playwright e2e**

```ts
// frontend/packages/web/e2e/admin-console-skeleton.spec.ts
import { test, expect } from "@playwright/test";
import { loginAs } from "./helpers/auth";

test.describe("admin console skeleton", () => {
  test("admin sees 管理后台 in avatar popover and can reach /admin", async ({ page, context }) => {
    await loginAs(page, "admin");
    await page.goto("/");
    await expect(page.getByRole("complementary", { name: /sidebar/i })).toBeVisible();
    await page.getByRole("button", { name: /avatar/i }).click();
    const adminLink = page.getByRole("menuitem", { name: "管理后台" });
    await expect(adminLink).toBeVisible();

    const pagePromise = context.waitForEvent("page");
    await adminLink.click();
    const adminPage = await pagePromise;
    await adminPage.waitForLoadState();

    await expect(adminPage).toHaveURL(/\/admin(\/|$)/);
    await expect(adminPage.getByText(/cubeplex · 管理后台/)).toBeVisible();
    for (const tab of ["模型", "技能", "MCP", "成员", "审计"]) {
      await expect(adminPage.getByRole("link", { name: new RegExp(tab) })).toBeVisible();
    }
  });

  test("non-admin member cannot access /admin", async ({ page }) => {
    await loginAs(page, "member");

    await page.goto("/");
    await page.getByRole("button", { name: /avatar/i }).click();
    await expect(page.getByRole("menuitem", { name: "管理后台" })).toHaveCount(0);

    await page.goto("/admin");
    await expect(page).toHaveURL(/^.*\/$/); // redirected to root
  });

  test("CE deployment: extensions manifest returns empty and no external tabs render", async ({ page }) => {
    await loginAs(page, "admin");
    await page.goto("/admin");
    // Only the 5 built-in CE tabs should be present in the sub-nav.
    const navLinks = page.getByRole("navigation", { name: /admin sub-nav/i }).getByRole("link");
    await expect(navLinks).toHaveCount(5);
  });
});
```

- [ ] **Step 2: Ensure `loginAs(page, "admin" | "member")` helper exists**

If `frontend/packages/web/e2e/helpers/auth.ts` does not already expose a dual-role `loginAs`, extend it to accept `"admin" | "member"` and seed / reuse both fixture users against the backend's auth endpoints. Reuse the same fixtures the streaming e2e already depends on — do not create a parallel auth harness.

- [ ] **Step 3: Run**

```bash
cd frontend && pnpm --filter @cubeplex/web exec playwright test admin-console-skeleton.spec.ts
```

Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/e2e/admin-console-skeleton.spec.ts frontend/packages/web/e2e/helpers/auth.ts
git commit -m "test(admin): e2e admin console skeleton (sidebar + /admin gating + CE extensions)"
```

---

### Task 17: Final integration smoke test

**Files:**
- Run all tests + manual smoke

- [ ] **Step 1: Backend full test sweep**

```bash
cd backend && uv run pytest tests/ -v
```
Expected: all PASS (including pre-existing test_rbac, auth tests).

- [ ] **Step 2: Frontend type-check + build**

```bash
cd frontend && pnpm --filter @cubeplex/web exec tsc --noEmit
cd frontend && pnpm --filter @cubeplex/web build
```
Expected: no errors.

- [ ] **Step 3: Manual smoke matrix**

Start backend + frontend dev servers. Log in as an admin user. Walk through:

| URL | Expected |
|---|---|
| `/` | Sidebar visible at left; main content area; avatar at sidebar bottom |
| `/workspaces` | Sidebar visible; workspace list in main |
| `/w/<wsId>` | Sidebar visible; current workspace marked with dot in WorkspacesSection |
| `/w/<wsId>/conversations/<id>` | Sidebar visible; AppShell renders main + (optional) artifact panel |
| Click avatar in sidebar | Popover opens upward; "管理后台" item shown |
| Click "管理后台" | Opens `/admin` in NEW TAB |
| `/admin` (new tab) | Independent layout; top bar shows "cubeplex · 管理后台 · <org name>"; left sub-nav with 5 native items + (no extensions in CE-only); content redirects to `/admin/models` |
| `/admin/skills` | "技能管理 / Coming Soon" card |
| Click "回应用" | New tab closes (because window.opener exists) |

Then log out, log in as a non-admin (member-only) user. Walk through:

| Action | Expected |
|---|---|
| Open avatar popover | "管理后台" entry NOT shown |
| Manually navigate to `/admin` | Redirects to `/` |

- [ ] **Step 4: Commit if anything was tweaked during smoke**

```bash
git status
# If anything changed, fix + commit. Otherwise M2 is implementation-complete.
```

---

## Self-Review Notes (planner ran)

- ✅ Spec coverage:
  - Sidebar refactor (D3, D4, D5, D6, D7, D8) → Tasks 7, 8, 9, 10
  - `/admin` independent layout (D1, D11) → Tasks 12, 13
  - Avatar popover (D4, D2) → Task 8
  - Plugin extension iframe (D14) → Tasks 11, 14
  - Auth gate (D12) → Tasks 2, 3, 4
  - URL stays `/admin` + org-name slot (D9, D10) → Task 12 AdminTopBar
  - Workspace settings NOT included (D8 / D10) → confirmed not in plan
  - shadcn tabs + popover (D15) → Task 1 (popover already present per /components/ui/ inspection)
  - CSP frame-src self (D14) → Task 15
  - Back-to-app via window.close fallback (D16) → Task 12 AdminTopBar
- ✅ Backend new endpoints (`/api/v1/admin/me`) + new dependency (`require_org_admin`) + new repo method (`user_has_role_in_org`) all explicit
- ✅ Frontend file paths use existing `@/` alias and `@cubeplex/core` package
- ✅ Existing AppTopBar / AvatarMenu / WorkspaceSwitcher explicitly deleted
- ✅ AppShell refactor preserves resizable artifact-panel logic (only Sidebar removed since outer layout provides it)
- ✅ Task 5 `last_activity_at` confirmed to use `Conversation.updated_at` (verified that `ConversationRepository.update_timestamp()` is called from messages endpoint at conversations.py:55 on every message round-trip); no Message table exists in cubeplex
- ⚠ Task 4 `_resolve_current_org_id` v1 picks user's first workspace's org. Multi-org users see only one org's admin until OrgSwitcher lands (Path A in spec §8). Acceptable per D9.
- ⚠ Task 10 deletes WorkspaceSwitcher; before merging, grep for any non-(app)/layout reference (deep-linked imports). The plan's grep step covers this.
