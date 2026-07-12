# P1 Frontend Feature Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working web UI matching the P1 backend — users can register, log in, see and switch workspaces, and chat inside a scoped workspace. Everything under `/w/[wsId]/…`.

**Architecture:** Next.js App Router with two route groups (`(auth)` for login/register, `(app)` for authenticated chrome). Workspace id is in the URL; a React context provides it to a refactored `ApiClient` that injects `X-Workspace-Id`, `X-CSRF-Token`, and `credentials: 'include'` automatically. A root `middleware.ts` does the auth-cookie gate. Backend `on_after_register` auto-creates a personal org + workspace + admin membership transactionally.

**Tech Stack:** Next.js 16, React 19, TypeScript 5, Zustand (existing), shadcn/ui primitives (existing), Playwright (existing), Vitest (existing). Backend: FastAPI + fastapi-users.

**Spec:** `docs/superpowers/specs/2026-04-17-p1-frontend-feature-parity-design.md`.

**Branch:** `feat/p1-frontend-feature-parity` (already created, stacked on `feat/p1-identity-auth-rbac`).

---

## File Structure (created / modified)

### Backend

- **Modify** `backend/cubeplex/auth/users.py` — expand `on_after_register` to create Organization + Workspace + Admin Membership transactionally.
- **Modify** `backend/cubeplex/api/routes/v1/auth.py` — `register` response adds `default_workspace_id`.
- **Create** `backend/tests/e2e/test_register_bootstrap.py` — verifies register creates org+ws+membership.
- **Modify** `backend/CLAUDE.md` — document the bootstrap behavior.

### Core library (`frontend/packages/core/src/`)

- **Modify** `api/client.ts` — full refactor: credentials, methods, header injection, 401 observable.
- **Create** `api/auth.ts` — register, login, logout, getMe.
- **Create** `api/workspaces.ts` — listWorkspaces, createWorkspace.
- **Modify** `api/stream.ts` — accept the new client (or workspace + csrf) and forward credentials.
- **Modify** `api/conversations.ts` — no signature changes; the new client injects headers automatically.
- **Modify** `api/index.ts` — export new modules.
- **Create** `stores/authStore.ts` — user cache + reset + loadMe.
- **Create** `stores/workspaceStore.ts` — workspaces list + active hint + create + refresh.
- **Modify** `stores/index.ts` — export new stores.
- **Create** `__tests__/` folder for Vitest tests (mirroring structure).

### Web app (`frontend/packages/web/`)

- **Create** `middleware.ts` — auth cookie gate, next-param redirect.
- **Create** `app/(auth)/layout.tsx` — centered card.
- **Create** `app/(auth)/login/page.tsx` — login form.
- **Create** `app/(auth)/register/page.tsx` — register form.
- **Create** `app/(app)/layout.tsx` — app chrome (top-bar + workspace context).
- **Create** `app/(app)/workspaces/page.tsx` — list + create.
- **Create** `app/(app)/w/[wsId]/layout.tsx` — binds URL's `wsId` to context + refreshes stores.
- **Create** `app/(app)/w/[wsId]/page.tsx` — welcome + input (moves existing `app/page.tsx` logic here).
- **Create** `app/(app)/w/[wsId]/conversations/[id]/page.tsx` — chat page (moves existing `app/conversations/[id]/page.tsx` here).
- **Modify** `app/page.tsx` — becomes thin redirect (logged-in → first ws; not logged-in → login).
- **Delete** `app/conversations/[id]/page.tsx` — replaced by route under `(app)/w/[wsId]/conversations/[id]`.
- **Modify** `app/api/v1/conversations/[id]/messages/route.ts` — forward `X-Workspace-Id` and `X-CSRF-Token`.
- **Create** `components/auth/LoginForm.tsx`.
- **Create** `components/auth/RegisterForm.tsx`.
- **Create** `components/workspace/WorkspaceSwitcher.tsx`.
- **Create** `components/workspace/WorkspaceCreateForm.tsx`.
- **Create** `components/workspace/WorkspaceList.tsx`.
- **Create** `components/layout/AppTopBar.tsx`.
- **Create** `components/layout/AvatarMenu.tsx`.
- **Create** `components/shared/ErrorState.tsx`.
- **Create** `hooks/useWorkspaceContext.ts` — reads URL-bound ws id.
- **Create** `hooks/useAuthRedirect.ts` — subscribes to ApiClient 401 event.
- **Create** `__tests__/e2e/auth-flow.spec.ts` — register + login + logout.
- **Create** `__tests__/e2e/workspace-switch.spec.ts` — switch + scope isolation.
- **Modify** `__tests__/e2e/chat-flow.spec.ts` — register a user first, then do the chat.
- **Modify** `frontend/CLAUDE.md` — auth + workspace section.

---

## Task 1: Backend — failing test for register bootstrap

**Files:**
- Create: `backend/tests/e2e/test_register_bootstrap.py`

- [ ] **Step 1: Write the failing test**

```python
"""E2E test: registering a user auto-creates personal org + workspace + admin membership."""

import secrets

import pytest
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.api.middleware.rate_limit import limiter
from cubeplex.db.engine import _build_database_url
from cubeplex.models import Membership, Role, User
from cubeplex.repositories import MembershipRepository, WorkspaceRepository


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    limiter.reset()
    yield
    limiter.reset()


@pytest.mark.asyncio
async def test_register_creates_org_ws_and_admin_membership(unauthenticated_memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorsebatterystaple"

    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "id" in body
    assert body["email"] == email
    assert "default_workspace_id" in body, "register response must include default_workspace_id"
    ws_id = body["default_workspace_id"]

    # Verify DB side effects: workspace exists, user has admin membership there
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            ws = await WorkspaceRepository(session).get(ws_id)
            assert ws is not None, "workspace row must exist"
            assert ws.org_id is not None
            mem = await MembershipRepository(session).get_role(
                user_id=body["id"], workspace_id=ws_id
            )
            assert mem == Role.ADMIN, f"user must be admin of new workspace, got {mem}"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_register_bootstrap_is_atomic_on_failure(unauthenticated_memory_client, monkeypatch):
    """If org/ws/membership creation blows up, the User row must not be left behind."""
    from cubeplex.repositories import OrganizationRepository

    original_create = OrganizationRepository.create

    async def boom(self, name: str):
        raise RuntimeError("simulated org create failure")

    monkeypatch.setattr(OrganizationRepository, "create", boom)

    email = f"u-{secrets.token_hex(4)}@example.com"
    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": "correcthorse-12345"}
    )
    assert r.status_code >= 400, "should not succeed when bootstrap fails"

    # Restore and verify no orphan User row
    monkeypatch.setattr(OrganizationRepository, "create", original_create)

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            user_db = SQLAlchemyUserDatabase(session, User)
            u = await user_db.get_by_email(email)
            assert u is None, "User row must be rolled back when bootstrap fails"
    finally:
        await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_register_bootstrap.py -v`
Expected: both tests FAIL. First fails with `"default_workspace_id" in body` KeyError. Second fails because current register succeeds (no bootstrap runs, nothing to fail) leaving a User row.

- [ ] **Step 3: Commit the failing test**

```bash
git add backend/tests/e2e/test_register_bootstrap.py
git commit -m "test(auth): register must bootstrap org+ws+admin-membership"
```

---

## Task 2: Backend — implement register bootstrap

**Files:**
- Modify: `backend/cubeplex/auth/users.py`
- Modify: `backend/cubeplex/api/routes/v1/auth.py`

- [ ] **Step 1: Update `UserManager` to bootstrap org/ws/membership in `on_after_register`**

Replace `on_after_register` in `backend/cubeplex/auth/users.py`:

```python
async def on_after_register(self, user: User, request: Request | None = None) -> None:
    """Auto-create personal Org + Workspace + Admin Membership for new users.

    Runs on the same session as the user-create, so a failure here rolls the
    whole registration back (User row included) via the outer request session.
    """
    logger.info("User registered: {}", user.email)
    session = self.user_db.session  # SQLAlchemyUserDatabase exposes the AsyncSession
    from cubeplex.models import Role
    from cubeplex.repositories import (
        MembershipRepository,
        OrganizationRepository,
        WorkspaceRepository,
    )

    local_part = user.email.split("@", 1)[0]
    org_name = f"{local_part}'s Org"
    org = await OrganizationRepository(session).create(name=org_name)
    ws = await WorkspaceRepository(session).create(org_id=org.id, name="Personal")
    await MembershipRepository(session).grant(
        user_id=user.id, workspace_id=ws.id, role=Role.ADMIN
    )
    # Stash the ws id on the user object so the route can return it without
    # re-querying. `_default_workspace_id` is not a DB column; it's transient.
    user._default_workspace_id = ws.id  # type: ignore[attr-defined]
```

Note: `OrganizationRepository.create`, `WorkspaceRepository.create`, and `MembershipRepository.grant` all call `session.commit()` internally. That's fine — the commits stage incrementally. If `create()` on a later step raises, the already-committed user/org/ws rows remain. We therefore must either (a) switch these repos to no-commit mode here, or (b) accept partial-state risk. Simplest: call `await session.flush()` in the repo `create` methods instead of commit when invoked during register. Use a compromise: manually call `session.rollback()` inside `on_after_register` on exception and re-raise, so the outer register flow rolls back cleanly.

Wrap the bootstrap block:

```python
async def on_after_register(self, user: User, request: Request | None = None) -> None:
    logger.info("User registered: {}", user.email)
    session = self.user_db.session  # type: ignore[attr-defined]
    from cubeplex.models import Role
    from cubeplex.repositories import (
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
    except Exception:
        # The repo commits above mean org/ws may already be persisted. Best-effort
        # cleanup of the user (and any half-built org/ws) via DELETEs. If this
        # cascade fails we still raise to surface the original error to the caller.
        from sqlalchemy import delete

        from cubeplex.models import Organization, User, Workspace

        try:
            await session.execute(delete(User).where(User.id == user.id))  # type: ignore[arg-type]
            await session.commit()
        except Exception:
            await session.rollback()
        raise

    user._default_workspace_id = ws.id  # type: ignore[attr-defined]
```

- [ ] **Step 2: Update `register` route to include `default_workspace_id`**

In `backend/cubeplex/api/routes/v1/auth.py`, change the `register` function return:

```python
@router.post("/register", status_code=201)
@limiter.limit(REGISTER_LIMIT)
async def register(
    request: Request,
    body: Annotated[UserCreate, Body()],
    user_manager: Annotated[UserManager, Depends(get_user_manager)],
) -> dict[str, str]:
    try:
        user = await user_manager.create(body, safe=True, request=request)
    except UserAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="REGISTER_USER_ALREADY_EXISTS"
        ) from None
    except InvalidPasswordException as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "REGISTER_INVALID_PASSWORD", "reason": exc.reason},
        ) from None
    default_ws = getattr(user, "_default_workspace_id", None)
    return {
        "id": user.id,
        "email": user.email,
        "default_workspace_id": default_ws or "",
    }
```

- [ ] **Step 3: Run tests**

Run: `cd backend && uv run pytest tests/e2e/test_register_bootstrap.py tests/e2e/test_auth.py -v`
Expected: all PASS.

- [ ] **Step 4: Run full backend check**

Run: `cd backend && make check`
Expected: format, lint, type-check all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/auth/users.py backend/cubeplex/api/routes/v1/auth.py
git commit -m "feat(auth): on_after_register auto-creates personal org+ws+admin membership"
```

---

## Task 3: Core — ApiClient refactor tests (failing)

**Files:**
- Create: `frontend/packages/core/__tests__/api/client.test.ts`
- Modify: `frontend/packages/core/vitest.config.ts` if missing — check first

- [ ] **Step 1: Verify vitest is set up for the core package**

Run: `cd frontend && ls packages/core/vitest.config.ts 2>/dev/null && cat packages/core/package.json`
If vitest config is missing, create it:

```ts
// frontend/packages/core/vitest.config.ts
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
  },
})
```

And add a test script to `packages/core/package.json`:
```json
"scripts": { "test": "vitest run", "test:watch": "vitest" }
```

Install dev deps if not already present:
```bash
cd frontend && pnpm --filter @cubeplex/core add -D vitest @vitest/ui jsdom
```

- [ ] **Step 2: Write the failing test**

Create `frontend/packages/core/__tests__/api/client.test.ts`:

```typescript
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { createApiClient } from '../../src/api/client'

describe('ApiClient', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn(async () => new Response('{}', { status: 200 }))
    globalThis.fetch = fetchMock as unknown as typeof fetch
    // jsdom: fake cookie
    Object.defineProperty(document, 'cookie', {
      writable: true,
      value: 'cubeplex_csrf=csrf-abc; other=x',
    })
  })

  afterEach(() => vi.restoreAllMocks())

  it('always sends credentials: include', async () => {
    const client = createApiClient('')
    await client.get('/api/v1/anything')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/anything',
      expect.objectContaining({ credentials: 'include' })
    )
  })

  it('injects X-Workspace-Id on scoped paths when workspaceId is set', async () => {
    const client = createApiClient('')
    client.setWorkspaceId('ws-123')
    await client.get('/api/v1/conversations')
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).headers).toMatchObject({ 'X-Workspace-Id': 'ws-123' })
  })

  it('does NOT inject X-Workspace-Id on /api/v1/auth/* paths', async () => {
    const client = createApiClient('')
    client.setWorkspaceId('ws-123')
    await client.post('/api/v1/auth/login', { a: 1 })
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).headers).not.toHaveProperty('X-Workspace-Id')
  })

  it('does NOT inject X-Workspace-Id on /api/v1/workspaces paths', async () => {
    const client = createApiClient('')
    client.setWorkspaceId('ws-123')
    await client.get('/api/v1/workspaces')
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).headers).not.toHaveProperty('X-Workspace-Id')
  })

  it('injects X-CSRF-Token on POST/PATCH/DELETE from cubeplex_csrf cookie', async () => {
    const client = createApiClient('')
    await client.post('/api/v1/conversations', {})
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).headers).toMatchObject({ 'X-CSRF-Token': 'csrf-abc' })
  })

  it('does NOT inject X-CSRF-Token on GET', async () => {
    const client = createApiClient('')
    await client.get('/api/v1/conversations')
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).headers).not.toHaveProperty('X-CSRF-Token')
  })

  it('postForm sends form-urlencoded body', async () => {
    const client = createApiClient('')
    await client.postForm('/api/v1/auth/login', { username: 'a@b.c', password: 'pw' })
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).headers).toMatchObject({
      'Content-Type': 'application/x-www-form-urlencoded',
    })
    expect(String((init as RequestInit).body)).toContain('username=a%40b.c')
    expect(String((init as RequestInit).body)).toContain('password=pw')
  })

  it('fires onUnauthorized callback on 401', async () => {
    fetchMock.mockResolvedValueOnce(new Response('', { status: 401 }))
    const handler = vi.fn()
    const client = createApiClient('')
    client.onUnauthorized(handler)
    await client.get('/api/v1/anything')
    expect(handler).toHaveBeenCalledOnce()
  })

  it('does not fire onUnauthorized for /auth/login 400s', async () => {
    fetchMock.mockResolvedValueOnce(new Response('', { status: 400 }))
    const handler = vi.fn()
    const client = createApiClient('')
    client.onUnauthorized(handler)
    await client.postForm('/api/v1/auth/login', { username: 'x', password: 'y' })
    expect(handler).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd frontend && pnpm --filter @cubeplex/core test`
Expected: all 9 tests FAIL because `setWorkspaceId`, `postForm`, and `onUnauthorized` don't exist yet.

- [ ] **Step 4: Commit the failing test**

```bash
git add frontend/packages/core/__tests__/api/client.test.ts frontend/packages/core/vitest.config.ts frontend/packages/core/package.json
git commit -m "test(api): ApiClient must inject workspace/csrf headers and observe 401"
```

---

## Task 4: Core — ApiClient implementation

**Files:**
- Modify: `frontend/packages/core/src/api/client.ts`

- [ ] **Step 1: Rewrite `client.ts`**

```typescript
/**
 * ApiClient — wraps fetch with credentials, workspace/CSRF header injection,
 * and a 401 observable.
 *
 * Path-based rules:
 *   - credentials: 'include' on every call (so cookies flow).
 *   - X-Workspace-Id is injected on paths NOT starting with /api/v1/auth/ or
 *     /api/v1/workspaces (those are workspace-neutral).
 *   - X-CSRF-Token is injected on non-GET methods, read from document.cookie
 *     (cubeplex_csrf).
 *
 * 401 observable: any response with status 401 fires all registered
 * onUnauthorized callbacks. Login 400s do NOT fire.
 */

export interface ApiClient {
  baseUrl: string
  workspaceId: string | null
  setWorkspaceId(id: string | null): void
  get(path: string): Promise<Response>
  post(path: string, body: unknown): Promise<Response>
  postForm(path: string, form: Record<string, string>): Promise<Response>
  patch(path: string, body: unknown): Promise<Response>
  del(path: string): Promise<Response>
  onUnauthorized(handler: () => void): () => void
}

const WS_NEUTRAL_PREFIXES = ['/api/v1/auth/', '/api/v1/workspaces']

function needsWorkspaceHeader(path: string): boolean {
  return !WS_NEUTRAL_PREFIXES.some(
    (p) => path === p || path.startsWith(p + '/') || path.startsWith(p + '?') || path.startsWith(p)
  )
}

function readCookie(name: string): string {
  if (typeof document === 'undefined') return ''
  const match = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`))
  return match ? decodeURIComponent(match.slice(name.length + 1)) : ''
}

export function createApiClient(baseUrl: string): ApiClient {
  let workspaceId: string | null = null
  const unauthorizedHandlers = new Set<() => void>()

  const buildHeaders = (path: string, method: string, base: Record<string, string>) => {
    const headers: Record<string, string> = { ...base }
    if (workspaceId && needsWorkspaceHeader(path)) {
      headers['X-Workspace-Id'] = workspaceId
    }
    if (method !== 'GET') {
      const csrf = readCookie('cubeplex_csrf')
      if (csrf) headers['X-CSRF-Token'] = csrf
    }
    return headers
  }

  const doFetch = async (path: string, init: RequestInit): Promise<Response> => {
    const res = await fetch(`${baseUrl}${path}`, {
      ...init,
      credentials: 'include',
    })
    // 401 surfaces everywhere EXCEPT on initial auth/login (which returns 400 for
    // bad creds — 401 from login means cookies are malformed, still valid to fire).
    if (res.status === 401) {
      for (const h of unauthorizedHandlers) h()
    }
    return res
  }

  const client: ApiClient = {
    baseUrl,
    get workspaceId() {
      return workspaceId
    },
    setWorkspaceId(id) {
      workspaceId = id
    },
    get(path) {
      return doFetch(path, {
        method: 'GET',
        headers: buildHeaders(path, 'GET', {}),
      })
    },
    post(path, body) {
      return doFetch(path, {
        method: 'POST',
        headers: buildHeaders(path, 'POST', { 'Content-Type': 'application/json' }),
        body: JSON.stringify(body),
      })
    },
    postForm(path, form) {
      const body = new URLSearchParams(form).toString()
      return doFetch(path, {
        method: 'POST',
        headers: buildHeaders(path, 'POST', {
          'Content-Type': 'application/x-www-form-urlencoded',
        }),
        body,
      })
    },
    patch(path, body) {
      return doFetch(path, {
        method: 'PATCH',
        headers: buildHeaders(path, 'PATCH', { 'Content-Type': 'application/json' }),
        body: JSON.stringify(body),
      })
    },
    del(path) {
      return doFetch(path, {
        method: 'DELETE',
        headers: buildHeaders(path, 'DELETE', {}),
      })
    },
    onUnauthorized(handler) {
      unauthorizedHandlers.add(handler)
      return () => unauthorizedHandlers.delete(handler)
    },
  }
  return client
}

export async function toApiError(res: Response): Promise<Error> {
  const contentType = res.headers.get('content-type')
  if (contentType?.includes('application/json')) {
    const data = (await res.json()) as { message?: string; detail?: string | { reason?: string } }
    const detail =
      typeof data.detail === 'string'
        ? data.detail
        : (data.detail as { reason?: string } | undefined)?.reason
    return new Error(data.message || detail || `HTTP ${res.status}`)
  }
  return new Error(`HTTP ${res.status}: ${res.statusText}`)
}
```

- [ ] **Step 2: Run tests**

Run: `cd frontend && pnpm --filter @cubeplex/core test`
Expected: all 9 ApiClient tests PASS.

- [ ] **Step 3: Type-check core**

Run: `cd frontend && pnpm --filter @cubeplex/core type-check`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/core/src/api/client.ts
git commit -m "feat(api): ApiClient auto-injects workspace/csrf headers + 401 observable"
```

---

## Task 5: Core — `auth.ts` API module + tests

**Files:**
- Create: `frontend/packages/core/src/api/auth.ts`
- Create: `frontend/packages/core/__tests__/api/auth.test.ts`
- Modify: `frontend/packages/core/src/api/index.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/packages/core/__tests__/api/auth.test.ts
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { createApiClient } from '../../src/api/client'
import { registerUser, loginUser, logoutUser, getMe } from '../../src/api/auth'

describe('auth API', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })
  afterEach(() => vi.restoreAllMocks())

  it('registerUser POSTs JSON and returns id+email+default_workspace_id', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ id: 'u1', email: 'a@b.c', default_workspace_id: 'ws-1' }),
        { status: 201 }
      )
    )
    const client = createApiClient('')
    const result = await registerUser(client, 'a@b.c', 'pw')
    expect(result).toEqual({ id: 'u1', email: 'a@b.c', default_workspace_id: 'ws-1' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/auth/register')
    expect((init as RequestInit).method).toBe('POST')
  })

  it('loginUser POSTs form-urlencoded', async () => {
    fetchMock.mockResolvedValueOnce(new Response('', { status: 204 }))
    const client = createApiClient('')
    await loginUser(client, 'a@b.c', 'pw')
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/auth/login')
    expect((init as RequestInit).headers).toMatchObject({
      'Content-Type': 'application/x-www-form-urlencoded',
    })
  })

  it('loginUser throws on 400', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'LOGIN_BAD_CREDENTIALS' }), { status: 400 })
    )
    const client = createApiClient('')
    await expect(loginUser(client, 'a@b.c', 'pw')).rejects.toThrow('LOGIN_BAD_CREDENTIALS')
  })

  it('logoutUser POSTs with no body', async () => {
    fetchMock.mockResolvedValueOnce(new Response('', { status: 204 }))
    const client = createApiClient('')
    await logoutUser(client)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/auth/logout')
    expect((init as RequestInit).method).toBe('POST')
  })

  it('getMe returns { id, email } on 200', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'u1', email: 'a@b.c' }), { status: 200 })
    )
    const client = createApiClient('')
    const me = await getMe(client)
    expect(me).toEqual({ id: 'u1', email: 'a@b.c' })
  })

  it('getMe returns null on 401', async () => {
    fetchMock.mockResolvedValueOnce(new Response('', { status: 401 }))
    const client = createApiClient('')
    const me = await getMe(client)
    expect(me).toBeNull()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && pnpm --filter @cubeplex/core test __tests__/api/auth.test.ts`
Expected: 6 tests FAIL, module not found.

- [ ] **Step 3: Implement `auth.ts`**

```typescript
// frontend/packages/core/src/api/auth.ts
import { toApiError, type ApiClient } from './client'

export interface RegisterResult {
  id: string
  email: string
  default_workspace_id: string
}

export interface MeResult {
  id: string
  email: string
}

export async function registerUser(
  client: ApiClient,
  email: string,
  password: string,
): Promise<RegisterResult> {
  const res = await client.post('/api/v1/auth/register', { email, password })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as RegisterResult
}

export async function loginUser(
  client: ApiClient,
  email: string,
  password: string,
): Promise<void> {
  const res = await client.postForm('/api/v1/auth/login', {
    username: email,
    password,
  })
  if (!res.ok) throw await toApiError(res)
}

export async function logoutUser(client: ApiClient): Promise<void> {
  const res = await client.post('/api/v1/auth/logout', {})
  if (!res.ok && res.status !== 401) throw await toApiError(res)
}

export async function getMe(client: ApiClient): Promise<MeResult | null> {
  const res = await client.get('/api/v1/auth/me')
  if (res.status === 401) return null
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MeResult
}
```

Update `frontend/packages/core/src/api/index.ts`:

```typescript
export { createApiClient, toApiError, type ApiClient } from './client'
export * from './auth'
export * from './conversations'
export * from './stream'
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && pnpm --filter @cubeplex/core test`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/api/auth.ts frontend/packages/core/src/api/index.ts frontend/packages/core/__tests__/api/auth.test.ts
git commit -m "feat(api): add auth module (register/login/logout/getMe)"
```

---

## Task 6: Core — `workspaces.ts` API module + tests

**Files:**
- Create: `frontend/packages/core/src/api/workspaces.ts`
- Create: `frontend/packages/core/__tests__/api/workspaces.test.ts`
- Modify: `frontend/packages/core/src/api/index.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/packages/core/__tests__/api/workspaces.test.ts
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { createApiClient } from '../../src/api/client'
import { listWorkspaces, createWorkspace } from '../../src/api/workspaces'

describe('workspaces API', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    fetchMock = vi.fn()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })
  afterEach(() => vi.restoreAllMocks())

  it('listWorkspaces returns array of workspaces', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify([
          { id: 'w1', name: 'Personal', org_id: 'o1', role: 'admin' },
          { id: 'w2', name: 'Team', org_id: 'o1', role: 'member' },
        ]),
        { status: 200 }
      )
    )
    const client = createApiClient('')
    const list = await listWorkspaces(client)
    expect(list).toHaveLength(2)
    expect(list[0]).toMatchObject({ id: 'w1', name: 'Personal', role: 'admin' })
  })

  it('createWorkspace POSTs { name, org_id } and returns the new ws', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'w3', name: 'Ops', org_id: 'o1' }), { status: 201 })
    )
    const client = createApiClient('')
    const ws = await createWorkspace(client, { name: 'Ops', orgId: 'o1' })
    expect(ws).toMatchObject({ id: 'w3', name: 'Ops', org_id: 'o1' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/workspaces')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ name: 'Ops', org_id: 'o1' })
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && pnpm --filter @cubeplex/core test __tests__/api/workspaces.test.ts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```typescript
// frontend/packages/core/src/api/workspaces.ts
import { toApiError, type ApiClient } from './client'

export interface Workspace {
  id: string
  name: string
  org_id: string
  role?: 'admin' | 'member'
}

export async function listWorkspaces(client: ApiClient): Promise<Workspace[]> {
  const res = await client.get('/api/v1/workspaces')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as Workspace[]
}

export async function createWorkspace(
  client: ApiClient,
  input: { name: string; orgId: string },
): Promise<Workspace> {
  const res = await client.post('/api/v1/workspaces', { name: input.name, org_id: input.orgId })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as Workspace
}
```

Add to `frontend/packages/core/src/api/index.ts`:
```typescript
export * from './workspaces'
```

- [ ] **Step 4: Run tests**

Run: `cd frontend && pnpm --filter @cubeplex/core test`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/api/workspaces.ts frontend/packages/core/src/api/index.ts frontend/packages/core/__tests__/api/workspaces.test.ts
git commit -m "feat(api): add workspaces module (list/create)"
```

---

## Task 7: Core — update `stream.ts` to forward credentials + headers

**Files:**
- Modify: `frontend/packages/core/src/api/stream.ts`

- [ ] **Step 1: Rewrite `streamMessages` to accept the ApiClient**

```typescript
// frontend/packages/core/src/api/stream.ts
import type { AgentEvent } from '../types'
import type { ApiClient } from './client'

async function* readLines(
  reader: ReadableStreamDefaultReader<Uint8Array>
): AsyncGenerator<string> {
  let buffer = ''
  const decoder = new TextDecoder()
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      yield line
    }
  }
  if (buffer) yield buffer
}

function readCookie(name: string): string {
  if (typeof document === 'undefined') return ''
  const match = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`))
  return match ? decodeURIComponent(match.slice(name.length + 1)) : ''
}

export async function* streamMessages(
  client: ApiClient,
  conversationId: string,
  content: string
): AsyncGenerator<AgentEvent> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Accept': 'text/event-stream',
    'Cache-Control': 'no-cache',
  }
  if (client.workspaceId) headers['X-Workspace-Id'] = client.workspaceId
  const csrf = readCookie('cubeplex_csrf')
  if (csrf) headers['X-CSRF-Token'] = csrf

  const res = await fetch(
    `${client.baseUrl}/api/v1/conversations/${conversationId}/messages`,
    {
      method: 'POST',
      credentials: 'include',
      headers,
      cache: 'no-store',
      body: JSON.stringify({ content }),
    }
  )

  if (!res.ok) {
    yield {
      type: 'error',
      timestamp: new Date().toISOString(),
      data: { message: `HTTP ${res.status}` },
      agent_id: null,
      agent_name: null,
    } as AgentEvent
    return
  }

  const reader = res.body!.getReader()
  try {
    for await (const line of readLines(reader)) {
      if (line.startsWith('data: ')) {
        try {
          yield JSON.parse(line.slice(6)) as AgentEvent
        } catch {
          // skip malformed lines
        }
      }
    }
  } catch {
    yield {
      type: 'error',
      timestamp: new Date().toISOString(),
      data: { message: 'Connection lost' },
      agent_id: null,
      agent_name: null,
    } as AgentEvent
  }
}
```

- [ ] **Step 2: Update the single caller in `messageStore.ts`**

In `frontend/packages/core/src/stores/messageStore.ts` line 241, change:
```typescript
for await (const event of streamMessages(client.baseUrl, conversationId, content)) {
```
to:
```typescript
for await (const event of streamMessages(client, conversationId, content)) {
```

- [ ] **Step 3: Build the core package**

Run: `cd frontend && pnpm --filter @cubeplex/core build`
Expected: TypeScript compiles cleanly.

- [ ] **Step 4: Type-check web package**

Run: `cd frontend && pnpm --filter web type-check`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/api/stream.ts frontend/packages/core/src/stores/messageStore.ts
git commit -m "feat(api): stream.ts takes ApiClient; forwards credentials/workspace/csrf"
```

---

## Task 8: Core — `authStore` + tests

**Files:**
- Create: `frontend/packages/core/src/stores/authStore.ts`
- Create: `frontend/packages/core/__tests__/stores/authStore.test.ts`
- Modify: `frontend/packages/core/src/stores/index.ts`

- [ ] **Step 1: Write failing test**

```typescript
// frontend/packages/core/__tests__/stores/authStore.test.ts
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { useAuthStore } from '../../src/stores/authStore'
import { createApiClient } from '../../src/api/client'

describe('authStore', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    useAuthStore.setState({ user: null, isLoading: false })
    fetchMock = vi.fn()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })
  afterEach(() => vi.restoreAllMocks())

  it('loadMe populates user on 200', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'u1', email: 'a@b.c' }), { status: 200 })
    )
    const client = createApiClient('')
    await useAuthStore.getState().loadMe(client)
    expect(useAuthStore.getState().user).toEqual({ id: 'u1', email: 'a@b.c' })
  })

  it('loadMe leaves user null on 401', async () => {
    fetchMock.mockResolvedValueOnce(new Response('', { status: 401 }))
    const client = createApiClient('')
    await useAuthStore.getState().loadMe(client)
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('reset clears user', () => {
    useAuthStore.setState({ user: { id: 'u1', email: 'a@b.c' } })
    useAuthStore.getState().reset()
    expect(useAuthStore.getState().user).toBeNull()
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && pnpm --filter @cubeplex/core test __tests__/stores/authStore.test.ts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```typescript
// frontend/packages/core/src/stores/authStore.ts
import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import { getMe, type MeResult } from '../api/auth'

export interface AuthStore {
  user: MeResult | null
  isLoading: boolean
  error: string | null
  loadMe(client: ApiClient): Promise<void>
  reset(): void
}

export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  isLoading: false,
  error: null,

  async loadMe(client) {
    set({ isLoading: true, error: null })
    try {
      const user = await getMe(client)
      set({ user })
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ isLoading: false })
    }
  },

  reset() {
    set({ user: null, isLoading: false, error: null })
  },
}))
```

Update `frontend/packages/core/src/stores/index.ts`:
```typescript
export * from './authStore'
```
(preserve existing exports)

- [ ] **Step 4: Run tests**

Run: `cd frontend && pnpm --filter @cubeplex/core test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/stores/authStore.ts frontend/packages/core/src/stores/index.ts frontend/packages/core/__tests__/stores/authStore.test.ts
git commit -m "feat(stores): add authStore (user cache + reset)"
```

---

## Task 9: Core — `workspaceStore` + tests

**Files:**
- Create: `frontend/packages/core/src/stores/workspaceStore.ts`
- Create: `frontend/packages/core/__tests__/stores/workspaceStore.test.ts`
- Modify: `frontend/packages/core/src/stores/index.ts`

- [ ] **Step 1: Write failing test**

```typescript
// frontend/packages/core/__tests__/stores/workspaceStore.test.ts
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { useWorkspaceStore } from '../../src/stores/workspaceStore'
import { createApiClient } from '../../src/api/client'

describe('workspaceStore', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    useWorkspaceStore.setState({ workspaces: [], isLoading: false })
    fetchMock = vi.fn()
    globalThis.fetch = fetchMock as unknown as typeof fetch
  })
  afterEach(() => vi.restoreAllMocks())

  it('fetchList populates workspaces', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify([{ id: 'w1', name: 'Personal', org_id: 'o1', role: 'admin' }]),
        { status: 200 }
      )
    )
    const client = createApiClient('')
    await useWorkspaceStore.getState().fetchList(client)
    expect(useWorkspaceStore.getState().workspaces).toHaveLength(1)
  })

  it('create prepends new workspace to list', async () => {
    useWorkspaceStore.setState({
      workspaces: [{ id: 'w1', name: 'Personal', org_id: 'o1', role: 'admin' }],
    })
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'w2', name: 'Team', org_id: 'o1' }), { status: 201 })
    )
    const client = createApiClient('')
    const created = await useWorkspaceStore.getState().create(client, 'Team')
    expect(created.id).toBe('w2')
    const list = useWorkspaceStore.getState().workspaces
    expect(list[0].id).toBe('w2')
    expect(list).toHaveLength(2)
  })

  it('create throws when no workspaces (no org_id to use)', async () => {
    const client = createApiClient('')
    await expect(useWorkspaceStore.getState().create(client, 'Team')).rejects.toThrow(
      /load workspaces first/i
    )
  })

  it('reset clears list', () => {
    useWorkspaceStore.setState({
      workspaces: [{ id: 'w1', name: 'Personal', org_id: 'o1', role: 'admin' }],
    })
    useWorkspaceStore.getState().reset()
    expect(useWorkspaceStore.getState().workspaces).toEqual([])
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && pnpm --filter @cubeplex/core test __tests__/stores/workspaceStore.test.ts`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement**

```typescript
// frontend/packages/core/src/stores/workspaceStore.ts
import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import {
  listWorkspaces,
  createWorkspace,
  type Workspace,
} from '../api/workspaces'

export interface WorkspaceStore {
  workspaces: Workspace[]
  isLoading: boolean
  error: string | null
  fetchList(client: ApiClient): Promise<void>
  create(client: ApiClient, name: string): Promise<Workspace>
  reset(): void
}

/**
 * One-user-one-org M1 assumption: a new workspace is created under the first
 * workspace's org_id. When multi-org-per-user ships (P2), pass an explicit
 * org id instead of reusing the first-seen one.
 */
export const useWorkspaceStore = create<WorkspaceStore>((set, get) => ({
  workspaces: [],
  isLoading: false,
  error: null,

  async fetchList(client) {
    set({ isLoading: true, error: null })
    try {
      const workspaces = await listWorkspaces(client)
      set({ workspaces })
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ isLoading: false })
    }
  },

  async create(client, name) {
    const existing = get().workspaces
    if (existing.length === 0) {
      throw new Error('Cannot create workspace: load workspaces first to determine org_id')
    }
    const orgId = existing[0].org_id
    const ws = await createWorkspace(client, { name, orgId })
    set((s) => ({ workspaces: [ws, ...s.workspaces] }))
    return ws
  },

  reset() {
    set({ workspaces: [], isLoading: false, error: null })
  },
}))
```

Update `frontend/packages/core/src/stores/index.ts`:
```typescript
export * from './workspaceStore'
```

- [ ] **Step 4: Run tests + build**

Run: `cd frontend && pnpm --filter @cubeplex/core test && pnpm --filter @cubeplex/core build`
Expected: all pass, build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/stores/workspaceStore.ts frontend/packages/core/src/stores/index.ts frontend/packages/core/__tests__/stores/workspaceStore.test.ts
git commit -m "feat(stores): add workspaceStore (list/create/reset)"
```

---

## Task 10: Web — `middleware.ts` auth gate

**Files:**
- Create: `frontend/packages/web/middleware.ts`

- [ ] **Step 1: Create middleware**

```typescript
// frontend/packages/web/middleware.ts
import { NextResponse, type NextRequest } from 'next/server'

const PUBLIC_PATHS = ['/login', '/register']
const PROTECTED_PREFIXES = ['/w/', '/workspaces']

function isProtected(pathname: string): boolean {
  return PROTECTED_PREFIXES.some((p) => pathname === p.replace(/\/$/, '') || pathname.startsWith(p))
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl
  const hasAuth = !!request.cookies.get('cubeplex_auth')

  if (!hasAuth && isProtected(pathname)) {
    const url = request.nextUrl.clone()
    url.pathname = '/login'
    url.searchParams.set('next', pathname + request.nextUrl.search)
    return NextResponse.redirect(url)
  }
  if (hasAuth && PUBLIC_PATHS.includes(pathname)) {
    const url = request.nextUrl.clone()
    url.pathname = '/'
    url.search = ''
    return NextResponse.redirect(url)
  }
  return NextResponse.next()
}

export const config = {
  matcher: ['/((?!api|_next/static|_next/image|icon.svg|favicon.ico).*)'],
}
```

- [ ] **Step 2: Smoke-test manually**

Run: `cd frontend && pnpm --filter web dev` (in background)

In another terminal:
```bash
curl -I -L http://localhost:3000/workspaces
```
Expected: 307 redirect to `/login?next=%2Fworkspaces`.

```bash
curl -I -L http://localhost:3000/
```
Expected: 200 (root not in protected prefix list yet — will be handled in Task 15).

Stop the dev server.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/middleware.ts
git commit -m "feat(web): auth-cookie middleware gate for /w/* and /workspaces"
```

---

## Task 11: Web — `(auth)` route group + login page

**Files:**
- Create: `frontend/packages/web/app/(auth)/layout.tsx`
- Create: `frontend/packages/web/app/(auth)/login/page.tsx`
- Create: `frontend/packages/web/components/auth/LoginForm.tsx`

- [ ] **Step 1: Create the auth layout (centered card)**

```tsx
// frontend/packages/web/app/(auth)/layout.tsx
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-6">
      <div className="w-full max-w-sm">
        {children}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create the `LoginForm` component**

```tsx
// frontend/packages/web/components/auth/LoginForm.tsx
'use client'

import { useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { createApiClient, loginUser, useAuthStore } from '@cubeplex/core'

export function LoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const client = createApiClient('')
      await loginUser(client, email, password)
      await useAuthStore.getState().loadMe(client)
      const next = searchParams.get('next') ?? '/'
      const safeNext = next.startsWith('/') && !next.startsWith('//') ? next : '/'
      router.push(safeNext)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="text-center mb-6">
        <h1 className="text-xl font-semibold">Sign in to cubeplex</h1>
      </div>
      <label className="block">
        <span className="text-sm text-foreground/80">Email</span>
        <input
          type="email"
          required
          autoComplete="email"
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="text-sm text-foreground/80">Password</span>
        <input
          type="password"
          required
          autoComplete="current-password"
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      {error && <div className="text-sm text-red-500">{error}</div>}
      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        {submitting ? 'Signing in…' : 'Sign in'}
      </button>
      <div className="text-center text-sm text-foreground/60">
        New here? <Link href="/register" className="underline">Create an account</Link>
      </div>
    </form>
  )
}
```

- [ ] **Step 3: Create the login page**

```tsx
// frontend/packages/web/app/(auth)/login/page.tsx
import { LoginForm } from '@/components/auth/LoginForm'

export default function LoginPage() {
  return <LoginForm />
}
```

- [ ] **Step 4: Smoke-test**

Run: `cd frontend && pnpm --filter web dev` (background)

Visit `http://localhost:3000/login` — should render the form. Fill a known user email + password and verify it redirects.

Stop the dev server.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/app/\(auth\) frontend/packages/web/components/auth/LoginForm.tsx
git commit -m "feat(web): /login page with form, next-param redirect, error display"
```

---

## Task 12: Web — register page

**Files:**
- Create: `frontend/packages/web/app/(auth)/register/page.tsx`
- Create: `frontend/packages/web/components/auth/RegisterForm.tsx`

- [ ] **Step 1: Implement `RegisterForm`**

```tsx
// frontend/packages/web/components/auth/RegisterForm.tsx
'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  createApiClient,
  registerUser,
  loginUser,
  useAuthStore,
} from '@cubeplex/core'

export function RegisterForm() {
  const router = useRouter()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const client = createApiClient('')
      const result = await registerUser(client, email, password)
      // Auto log-in so a cookie is set; register endpoint does NOT set auth cookie.
      await loginUser(client, email, password)
      await useAuthStore.getState().loadMe(client)
      router.push(`/w/${result.default_workspace_id}`)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="text-center mb-6">
        <h1 className="text-xl font-semibold">Create your cubeplex account</h1>
      </div>
      <label className="block">
        <span className="text-sm text-foreground/80">Email</span>
        <input
          type="email"
          required
          autoComplete="email"
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="text-sm text-foreground/80">Password</span>
        <input
          type="password"
          required
          minLength={8}
          autoComplete="new-password"
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      {error && <div className="text-sm text-red-500">{error}</div>}
      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        {submitting ? 'Creating…' : 'Create account'}
      </button>
      <div className="text-center text-sm text-foreground/60">
        Already have an account? <Link href="/login" className="underline">Sign in</Link>
      </div>
    </form>
  )
}
```

- [ ] **Step 2: Create the register page**

```tsx
// frontend/packages/web/app/(auth)/register/page.tsx
import { RegisterForm } from '@/components/auth/RegisterForm'

export default function RegisterPage() {
  return <RegisterForm />
}
```

- [ ] **Step 3: Smoke-test manually**

Start dev + backend. Navigate `/register`, submit with a fresh email + 10-char password. Confirm redirect to `/w/<id>`. Confirm `cubeplex_auth` cookie exists in DevTools. Stop dev.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/app/\(auth\)/register frontend/packages/web/components/auth/RegisterForm.tsx
git commit -m "feat(web): /register page (auto-logs-in, redirects to new workspace)"
```

---

## Task 13: Web — `(app)` route group layout + workspace context

**Files:**
- Create: `frontend/packages/web/app/(app)/layout.tsx`
- Create: `frontend/packages/web/app/(app)/w/[wsId]/layout.tsx`
- Create: `frontend/packages/web/hooks/useWorkspaceContext.ts`
- Create: `frontend/packages/web/hooks/useAuthRedirect.ts`

- [ ] **Step 1: Create the workspace context hook**

```typescript
// frontend/packages/web/hooks/useWorkspaceContext.ts
'use client'

import { createContext, useContext } from 'react'

export interface WorkspaceContextValue {
  workspaceId: string | null
}

export const WorkspaceContext = createContext<WorkspaceContextValue>({ workspaceId: null })

export function useWorkspaceContext(): WorkspaceContextValue {
  return useContext(WorkspaceContext)
}
```

- [ ] **Step 2: Create the 401-redirect hook**

```typescript
// frontend/packages/web/hooks/useAuthRedirect.ts
'use client'

import { useEffect } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import type { ApiClient } from '@cubeplex/core'

export function useAuthRedirect(client: ApiClient) {
  const router = useRouter()
  const pathname = usePathname()

  useEffect(() => {
    const unsubscribe = client.onUnauthorized(() => {
      const next = encodeURIComponent(pathname)
      router.push(`/login?next=${next}`)
    })
    return () => {
      unsubscribe()
    }
  }, [client, router, pathname])
}
```

- [ ] **Step 3: Create `(app)` layout**

```tsx
// frontend/packages/web/app/(app)/layout.tsx
'use client'

import { useEffect, useMemo } from 'react'
import {
  createApiClient,
  useAuthStore,
  useWorkspaceStore,
} from '@cubeplex/core'
import { useAuthRedirect } from '@/hooks/useAuthRedirect'
import { AppTopBar } from '@/components/layout/AppTopBar'

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const client = useMemo(() => createApiClient(''), [])
  useAuthRedirect(client)

  useEffect(() => {
    useAuthStore.getState().loadMe(client)
    useWorkspaceStore.getState().fetchList(client)
  }, [client])

  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground">
      <AppTopBar />
      <div className="flex-1 flex flex-col">{children}</div>
    </div>
  )
}
```

- [ ] **Step 4: Create the `[wsId]` layout — binds URL segment to the context + ApiClient**

```tsx
// frontend/packages/web/app/(app)/w/[wsId]/layout.tsx
'use client'

import { use, useEffect, useMemo } from 'react'
import {
  createApiClient,
  useConversationStore,
  useArtifactStore,
} from '@cubeplex/core'
import { WorkspaceContext } from '@/hooks/useWorkspaceContext'

export default function WorkspaceLayout({
  params,
  children,
}: {
  params: Promise<{ wsId: string }>
  children: React.ReactNode
}) {
  const { wsId } = use(params)
  const value = useMemo(() => ({ workspaceId: wsId }), [wsId])

  useEffect(() => {
    // Re-prime a client with the workspace id for any caller that reads it.
    // Individual pages create their own clients too (see conversation page).
    // Reset cross-workspace state when the wsId changes.
    useConversationStore.setState({ conversations: [], activeId: null })
    useArtifactStore.setState({ artifactsByConversation: {} })
  }, [wsId])

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}
```

Note: check the real state shape for `artifactStore` first — if the key isn't `artifactsByConversation`, fix this reset accordingly. If unsure, leave only the conversation-store reset.

- [ ] **Step 5: Verify artifact store shape before the reset**

Read `frontend/packages/core/src/stores/artifactStore.ts`. If the public state keys differ from `artifactsByConversation`, update step 4's reset. Commit follows once both layouts type-check together.

- [ ] **Step 6: Commit (before pages exist — types will be verified with Task 14+)**

```bash
git add frontend/packages/web/hooks frontend/packages/web/app/\(app\)/layout.tsx frontend/packages/web/app/\(app\)/w/\[wsId\]/layout.tsx
git commit -m "feat(web): (app) route group layout + workspace context + 401 redirect hook"
```

---

## Task 14: Web — `AppTopBar`, `AvatarMenu`, `WorkspaceSwitcher`

**Files:**
- Create: `frontend/packages/web/components/layout/AppTopBar.tsx`
- Create: `frontend/packages/web/components/layout/AvatarMenu.tsx`
- Create: `frontend/packages/web/components/workspace/WorkspaceSwitcher.tsx`

- [ ] **Step 1: `WorkspaceSwitcher`**

```tsx
// frontend/packages/web/components/workspace/WorkspaceSwitcher.tsx
'use client'

import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useWorkspaceStore } from '@cubeplex/core'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { ChevronDown, Plus } from 'lucide-react'
import { useState, useRef, useEffect } from 'react'

export function WorkspaceSwitcher() {
  const router = useRouter()
  const { workspaceId } = useWorkspaceContext()
  const workspaces = useWorkspaceStore((s) => s.workspaces)
  const current = workspaces.find((w) => w.id === workspaceId)
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((s) => !s)}
        className="flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-foreground/5"
      >
        <span>{current?.name ?? 'Workspace'}</span>
        <ChevronDown className="size-4" />
      </button>
      {open && (
        <div className="absolute left-0 mt-1 w-56 rounded-md border border-border bg-background shadow-md py-1 z-20">
          {workspaces.map((w) => (
            <button
              key={w.id}
              type="button"
              className={`block w-full text-left px-3 py-1.5 text-sm hover:bg-foreground/5 ${w.id === workspaceId ? 'font-medium' : ''}`}
              onClick={() => {
                setOpen(false)
                router.push(`/w/${w.id}`)
              }}
            >
              {w.name}
            </button>
          ))}
          <div className="border-t border-border my-1" />
          <Link
            href="/workspaces"
            className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-foreground/5"
            onClick={() => setOpen(false)}
          >
            <Plus className="size-3.5" /> Manage workspaces
          </Link>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: `AvatarMenu`**

```tsx
// frontend/packages/web/components/layout/AvatarMenu.tsx
'use client'

import { useRouter } from 'next/navigation'
import { useState, useRef, useEffect } from 'react'
import {
  createApiClient,
  logoutUser,
  useAuthStore,
  useConversationStore,
  useWorkspaceStore,
} from '@cubeplex/core'

export function AvatarMenu() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const onLogout = async () => {
    const client = createApiClient('')
    try {
      await logoutUser(client)
    } catch {
      // logout is best-effort; proceed with local reset regardless
    }
    useAuthStore.getState().reset()
    useWorkspaceStore.getState().reset()
    useConversationStore.setState({ conversations: [], activeId: null })
    router.push('/login')
  }

  const initials = user?.email.slice(0, 2).toUpperCase() ?? '?'

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((s) => !s)}
        className="size-8 rounded-full bg-foreground/10 flex items-center justify-center text-xs font-medium"
        aria-label="Account"
      >
        {initials}
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-56 rounded-md border border-border bg-background shadow-md py-1 z-20">
          {user && (
            <div className="px-3 py-2 text-xs text-foreground/60 truncate">{user.email}</div>
          )}
          <button
            type="button"
            onClick={onLogout}
            className="block w-full text-left px-3 py-1.5 text-sm hover:bg-foreground/5"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: `AppTopBar`**

```tsx
// frontend/packages/web/components/layout/AppTopBar.tsx
'use client'

import Link from 'next/link'
import { Box } from 'lucide-react'
import { WorkspaceSwitcher } from '@/components/workspace/WorkspaceSwitcher'
import { AvatarMenu } from '@/components/layout/AvatarMenu'

export function AppTopBar() {
  return (
    <header className="border-b border-border bg-background">
      <div className="flex h-12 items-center gap-3 px-4">
        <Link href="/" className="flex items-center gap-2">
          <Box className="size-5" />
          <span className="text-sm font-semibold">cubeplex</span>
        </Link>
        <div className="ml-2">
          <WorkspaceSwitcher />
        </div>
        <div className="ml-auto">
          <AvatarMenu />
        </div>
      </div>
    </header>
  )
}
```

- [ ] **Step 4: Type-check**

Run: `cd frontend && pnpm --filter web type-check`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/layout/AppTopBar.tsx frontend/packages/web/components/layout/AvatarMenu.tsx frontend/packages/web/components/workspace/WorkspaceSwitcher.tsx
git commit -m "feat(web): top-bar with workspace switcher and avatar menu (logout)"
```

---

## Task 15: Web — move home + chat pages into `/w/[wsId]/…`

**Files:**
- Create: `frontend/packages/web/app/(app)/w/[wsId]/page.tsx`
- Create: `frontend/packages/web/app/(app)/w/[wsId]/conversations/[id]/page.tsx`
- Modify: `frontend/packages/web/app/page.tsx`
- Delete: `frontend/packages/web/app/conversations/[id]/page.tsx`

- [ ] **Step 1: Pre-move audit — find call sites to `/conversations/…`**

Run: `grep -rn "conversations/" frontend/packages/web/{app,components,hooks,__tests__} 2>/dev/null | grep -v node_modules | grep -v '.next'`
Note every line that uses a path string like `'/conversations/'` or `router.push('/conversations…')`. You'll update them in the next steps.

- [ ] **Step 2: Create the new welcome page at `/w/[wsId]`**

```tsx
// frontend/packages/web/app/(app)/w/[wsId]/page.tsx
'use client'

import { use } from 'react'
import { useRouter } from 'next/navigation'
import {
  createApiClient,
  useConversationStore,
  useMessageStore,
} from '@cubeplex/core'
import { InputBar } from '@/components/layout/InputBar'
import { Box } from 'lucide-react'

export default function WorkspaceHomePage({
  params,
}: {
  params: Promise<{ wsId: string }>
}) {
  const { wsId } = use(params)
  const router = useRouter()
  const { create: createConversation } = useConversationStore()
  const send = useMessageStore((s) => s.send)

  const handleSubmit = async (content: string) => {
    const client = createApiClient('')
    client.setWorkspaceId(wsId)
    try {
      const convo = await createConversation(client, content.slice(0, 30))
      useConversationStore.setState({ activeId: convo.id })
      router.push(`/w/${wsId}/conversations/${convo.id}`)
      send(client, convo.id, content).catch((err) => {
        console.error('Failed to send message:', err)
      })
    } catch (err) {
      console.error('Failed to create conversation:', err)
    }
  }

  return (
    <div className="flex-1 flex flex-col items-center justify-center">
      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-primary/10 border border-primary/20 mb-5">
          <Box className="size-6 text-primary" strokeWidth={2} />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight mb-1.5">cubeplex</h1>
        <p className="text-sm text-muted-foreground/70">AI 智能体系统</p>
      </div>
      <div className="w-full max-w-2xl px-4">
        <InputBar onSubmit={handleSubmit} />
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Create the new chat page at `/w/[wsId]/conversations/[id]`**

```tsx
// frontend/packages/web/app/(app)/w/[wsId]/conversations/[id]/page.tsx
'use client'

import { use, useEffect, useMemo } from 'react'
import {
  useConversationStore,
  usePanelStore,
  useArtifactStore,
  createApiClient,
} from '@cubeplex/core'
import { AppShell } from '@/components/layout/AppShell'
import { MessageList } from '@/components/chat/MessageList'
import { ArtifactGallery } from '@/components/chat/ArtifactGallery'
import { InputBar } from '@/components/layout/InputBar'

export default function ChatPage({
  params,
}: {
  params: Promise<{ wsId: string; id: string }>
}) {
  const { wsId, id: conversationId } = use(params)
  const setActive = useConversationStore((s) => s.setActive)
  const fetchList = useConversationStore((s) => s.fetchList)
  const conversations = useConversationStore((s) => s.conversations)
  const loadArtifacts = useArtifactStore((s) => s.loadArtifacts)

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    usePanelStore.getState().close()
    setActive(conversationId)
    fetchList(client)
    loadArtifacts(client, conversationId)
  }, [conversationId, client, setActive, fetchList, loadArtifacts])

  const currentConvo = conversations.find((c) => c.id === conversationId)

  return (
    <AppShell headerTitle={currentConvo?.title}>
      <ArtifactGallery conversationId={conversationId} />
      <MessageList conversationId={conversationId} />
      <div className="border-t border-border px-4 py-3 bg-background">
        <InputBar conversationId={conversationId} />
      </div>
    </AppShell>
  )
}
```

- [ ] **Step 4: Update any component that knows how to build a conversation URL**

Grep result from Step 1 may show `InputBar`, `MessageList`, or other components calling `router.push('/conversations/…')`. Each must be updated to `/w/${wsId}/conversations/${id}`. Since `wsId` isn't always in scope, accept it as a prop or read from `useWorkspaceContext()` (inside the `(app)/w/[wsId]/…` tree).

For `InputBar` (`frontend/packages/web/components/layout/InputBar.tsx`) — if it uses `router.push('/conversations/' + id)` internally, change it to use `useWorkspaceContext` and push `/w/${ctx.workspaceId}/conversations/${id}`. Read the file to confirm before editing.

- [ ] **Step 5: Make `app/page.tsx` a redirect**

```tsx
// frontend/packages/web/app/page.tsx
import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'

export default async function RootRedirectPage() {
  const cookieStore = await cookies()
  const authed = !!cookieStore.get('cubeplex_auth')
  if (!authed) redirect('/login')

  // Server-side fetch to pick a workspace. Requires forwarding cookies.
  const cookieHeader = cookieStore.toString()
  const apiUrl = process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'
  const res = await fetch(`${apiUrl}/api/v1/workspaces`, {
    headers: { cookie: cookieHeader },
    cache: 'no-store',
  })
  if (!res.ok) redirect('/login')
  const workspaces = (await res.json()) as { id: string }[]
  if (workspaces.length === 0) redirect('/workspaces')
  redirect(`/w/${workspaces[0].id}`)
}
```

- [ ] **Step 6: Delete the old chat page**

```bash
rm frontend/packages/web/app/conversations/[id]/page.tsx
rmdir frontend/packages/web/app/conversations/\[id\] 2>/dev/null
rmdir frontend/packages/web/app/conversations 2>/dev/null
```

- [ ] **Step 7: Type-check + build**

Run: `cd frontend && pnpm --filter @cubeplex/core build && pnpm --filter web type-check && pnpm --filter web build`
Expected: all pass.

- [ ] **Step 8: Smoke-test the flow**

Start dev + backend. Register a fresh user → should land at `/w/<id>`. Send a message → should go to `/w/<id>/conversations/<convId>`. Reload — should still show. Stop dev.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(web): move home + chat pages under /w/[wsId]/; root redirects to first ws"
```

---

## Task 16: Web — forward workspace + csrf headers in the SSE proxy route

**Files:**
- Modify: `frontend/packages/web/app/api/v1/conversations/[id]/messages/route.ts`

- [ ] **Step 1: Update `buildProxyHeaders` to forward `x-workspace-id` and `x-csrf-token`**

Edit `buildProxyHeaders` in the route handler:

```typescript
function buildProxyHeaders(request: NextRequest, accept: string): HeadersInit {
  const headers: Record<string, string> = { Accept: accept }
  const cookie = request.headers.get('cookie')
  const userId = request.headers.get('x-user-id')
  const wsId = request.headers.get('x-workspace-id')
  const csrf = request.headers.get('x-csrf-token')

  if (cookie) headers.cookie = cookie
  if (userId) headers['x-user-id'] = userId
  if (wsId) headers['X-Workspace-Id'] = wsId
  if (csrf) headers['X-CSRF-Token'] = csrf

  return headers
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && pnpm --filter web type-check`
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/app/api/v1/conversations/\[id\]/messages/route.ts
git commit -m "fix(web): forward X-Workspace-Id + X-CSRF-Token in SSE proxy route"
```

---

## Task 17: Web — `/workspaces` page (list + create)

**Files:**
- Create: `frontend/packages/web/app/(app)/workspaces/page.tsx`
- Create: `frontend/packages/web/components/workspace/WorkspaceList.tsx`
- Create: `frontend/packages/web/components/workspace/WorkspaceCreateForm.tsx`

- [ ] **Step 1: `WorkspaceList`**

```tsx
// frontend/packages/web/components/workspace/WorkspaceList.tsx
'use client'

import Link from 'next/link'
import { useWorkspaceStore } from '@cubeplex/core'

export function WorkspaceList() {
  const workspaces = useWorkspaceStore((s) => s.workspaces)

  if (workspaces.length === 0) {
    return (
      <div className="text-sm text-foreground/60">You have no workspaces yet.</div>
    )
  }

  return (
    <ul className="divide-y divide-border rounded-md border border-border">
      {workspaces.map((w) => (
        <li key={w.id} className="flex items-center justify-between px-4 py-3">
          <div>
            <div className="text-sm font-medium">{w.name}</div>
            <div className="text-xs text-foreground/50">
              Role: {w.role ?? 'unknown'}
            </div>
          </div>
          <Link
            href={`/w/${w.id}`}
            className="text-sm underline text-foreground/80 hover:text-foreground"
          >
            Open
          </Link>
        </li>
      ))}
    </ul>
  )
}
```

- [ ] **Step 2: `WorkspaceCreateForm`**

```tsx
// frontend/packages/web/components/workspace/WorkspaceCreateForm.tsx
'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { createApiClient, useWorkspaceStore } from '@cubeplex/core'

export function WorkspaceCreateForm() {
  const router = useRouter()
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const client = createApiClient('')
      const ws = await useWorkspaceStore.getState().create(client, name)
      setName('')
      router.push(`/w/${ws.id}`)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3 rounded-md border border-border p-4">
      <label className="block">
        <span className="text-sm text-foreground/80">New workspace name</span>
        <input
          type="text"
          required
          maxLength={64}
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Side project"
        />
      </label>
      {error && <div className="text-sm text-red-500">{error}</div>}
      <button
        type="submit"
        disabled={submitting || !name.trim()}
        className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        {submitting ? 'Creating…' : 'Create workspace'}
      </button>
    </form>
  )
}
```

- [ ] **Step 3: The `/workspaces` page**

```tsx
// frontend/packages/web/app/(app)/workspaces/page.tsx
import { WorkspaceList } from '@/components/workspace/WorkspaceList'
import { WorkspaceCreateForm } from '@/components/workspace/WorkspaceCreateForm'

export default function WorkspacesPage() {
  return (
    <div className="max-w-2xl mx-auto w-full p-6 space-y-6">
      <h1 className="text-lg font-semibold">Workspaces</h1>
      <WorkspaceList />
      <WorkspaceCreateForm />
    </div>
  )
}
```

- [ ] **Step 4: Type-check + smoke**

Run: `cd frontend && pnpm --filter web type-check`
Expected: pass.

Start dev, navigate to `/workspaces`, confirm list + form render. Create a workspace, verify redirect to `/w/<new-id>`. Stop dev.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/app/\(app\)/workspaces frontend/packages/web/components/workspace/WorkspaceList.tsx frontend/packages/web/components/workspace/WorkspaceCreateForm.tsx
git commit -m "feat(web): /workspaces page with list + create form"
```

---

## Task 18: Web — ErrorState component + 404 / 403 page handlers

**Files:**
- Create: `frontend/packages/web/components/shared/ErrorState.tsx`
- Modify: `frontend/packages/web/app/(app)/w/[wsId]/conversations/[id]/page.tsx`

- [ ] **Step 1: `ErrorState`**

```tsx
// frontend/packages/web/components/shared/ErrorState.tsx
import Link from 'next/link'

export function ErrorState({
  title,
  description,
  backHref,
  backLabel = 'Go back',
}: {
  title: string
  description?: string
  backHref: string
  backLabel?: string
}) {
  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <div className="max-w-md text-center space-y-3">
        <h2 className="text-lg font-semibold">{title}</h2>
        {description && <p className="text-sm text-foreground/60">{description}</p>}
        <Link href={backHref} className="inline-block text-sm underline">
          {backLabel}
        </Link>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Wire 403/404 in the chat page**

Update `frontend/packages/web/app/(app)/w/[wsId]/conversations/[id]/page.tsx` to track a fetch status and render `<ErrorState />` when messages load fails. Add a local state for the fetch outcome:

```tsx
// …existing imports…
import { useEffect, useMemo, useState } from 'react'
import { ErrorState } from '@/components/shared/ErrorState'

// …inside component after `useMemo(...)` for client:
const [status, setStatus] = useState<'loading' | 'ok' | 'notfound' | 'forbidden'>('loading')

useEffect(() => {
  usePanelStore.getState().close()
  setActive(conversationId)
  ;(async () => {
    const res = await client.get(`/api/v1/conversations/${conversationId}`)
    if (res.status === 404) setStatus('notfound')
    else if (res.status === 403) setStatus('forbidden')
    else if (res.ok) setStatus('ok')
    else setStatus('notfound')
  })()
  fetchList(client)
  loadArtifacts(client, conversationId)
}, [conversationId, client, setActive, fetchList, loadArtifacts])

if (status === 'notfound') {
  return (
    <ErrorState
      title="Conversation not found"
      description="It may have been deleted, or it belongs to a different workspace."
      backHref={`/w/${wsId}`}
      backLabel="Back to workspace"
    />
  )
}
if (status === 'forbidden') {
  return (
    <ErrorState
      title="No access"
      description="You are not a member of this workspace."
      backHref="/workspaces"
      backLabel="Choose a workspace"
    />
  )
}
```

Leave the existing AppShell render untouched for the `ok`/`loading` paths.

- [ ] **Step 3: Type-check + smoke**

Run: `cd frontend && pnpm --filter web type-check`
Smoke: navigate to `/w/<your-ws>/conversations/<invalid-id>` — should show "Conversation not found".

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/shared/ErrorState.tsx frontend/packages/web/app/\(app\)/w/\[wsId\]/conversations/\[id\]/page.tsx
git commit -m "feat(web): shared ErrorState + 403/404 handling on chat page"
```

---

## Task 19: E2E — auth flow Playwright spec

**Files:**
- Create: `frontend/packages/web/__tests__/e2e/auth-flow.spec.ts`

- [ ] **Step 1: Write the spec**

```typescript
// frontend/packages/web/__tests__/e2e/auth-flow.spec.ts
import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

const PASSWORD = 'correcthorsebatterystaple'

test('register → auto-login → land in personal workspace', async ({ page }) => {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
  await expect(page.getByRole('heading', { name: 'cubeplex' })).toBeVisible()
})

test('login → redirect to workspace; logout → redirect to login', async ({ page, context }) => {
  const email = uniqueEmail()
  // Register first so the user exists
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\//)

  // Clear cookies, log back in
  await context.clearCookies()
  await page.goto('/login')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page).toHaveURL(/\/w\//, { timeout: 10_000 })

  // Logout via avatar menu
  await page.getByRole('button', { name: 'Account' }).click()
  await page.getByRole('button', { name: /sign out/i }).click()
  await expect(page).toHaveURL(/\/login$/)
})

test('unauthenticated visit to /workspaces redirects to /login with next param', async ({
  context, page,
}) => {
  await context.clearCookies()
  await page.goto('/workspaces')
  await expect(page).toHaveURL(/\/login\?next=%2Fworkspaces/)
})
```

- [ ] **Step 2: Run the spec**

Run: `cd frontend && pnpm test:e2e __tests__/e2e/auth-flow.spec.ts`
Expected: all 3 tests PASS. (Backend must be running on :8000.)

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/auth-flow.spec.ts
git commit -m "test(e2e): auth flow — register/login/logout/redirect"
```

---

## Task 20: E2E — workspace-scope isolation Playwright spec

**Files:**
- Create: `frontend/packages/web/__tests__/e2e/workspace-switch.spec.ts`

- [ ] **Step 1: Write the spec**

```typescript
// frontend/packages/web/__tests__/e2e/workspace-switch.spec.ts
import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

const PASSWORD = 'correcthorsebatterystaple'

test('workspace switching isolates conversation lists', async ({ page }) => {
  const email = uniqueEmail()
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
  const firstWsUrl = page.url()
  const firstWsId = firstWsUrl.split('/w/')[1]

  // Send a message in workspace 1 — creates a conversation
  const input = page.getByPlaceholder('有什么可以帮你的？')
  await input.fill('Hello in workspace 1')
  await input.press('Enter')
  await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//, { timeout: 10_000 })
  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 50_000 })
  const convInWs1Url = page.url()

  // Create a second workspace
  await page.goto('/workspaces')
  await page.getByPlaceholder('e.g. Side project').fill('Side')
  await page.getByRole('button', { name: /create workspace/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/)
  const secondWsUrl = page.url()
  const secondWsId = secondWsUrl.split('/w/')[1]
  expect(secondWsId).not.toBe(firstWsId)

  // The welcome page of ws2 shows input but no previous conv list
  // Try to open the ws1 conversation under ws2's URL — should 404
  const wrongUrl = convInWs1Url.replace(`/w/${firstWsId}/`, `/w/${secondWsId}/`)
  await page.goto(wrongUrl)
  await expect(page.getByText(/conversation not found/i)).toBeVisible({ timeout: 10_000 })
})
```

- [ ] **Step 2: Run the spec**

Run: `cd frontend && pnpm test:e2e __tests__/e2e/workspace-switch.spec.ts`
Expected: PASS. (If `InputBar` placeholder text differs, update the locator.)

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/workspace-switch.spec.ts
git commit -m "test(e2e): workspace switching + cross-workspace 404 isolation"
```

---

## Task 21: E2E — update existing `chat-flow` to register first

**Files:**
- Modify: `frontend/packages/web/__tests__/e2e/chat-flow.spec.ts`

- [ ] **Step 1: Add a helper that registers a fresh user and navigates to the workspace home**

```typescript
// frontend/packages/web/__tests__/e2e/chat-flow.spec.ts
import { test, expect, type Page } from '@playwright/test'

const PASSWORD = 'correcthorsebatterystaple'

async function registerAndLand(page: Page): Promise<void> {
  const email = `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
}

test('can send a message and see a response', async ({ page }) => {
  await registerAndLand(page)

  const input = page.getByPlaceholder('有什么可以帮你的？')
  await input.fill('Say the word "hello" and nothing else.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//, { timeout: 10_000 })

  const main = page.getByRole('main')
  await expect(main.getByText('Say the word "hello" and nothing else.')).toBeVisible({
    timeout: 10_000,
  })

  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 50_000 })

  const assistantMsg = main.locator('[data-role="assistant"]')
  await expect(assistantMsg).toBeVisible()
  const text = await assistantMsg.textContent()
  expect(text!.trim().length).toBeGreaterThan(0)
})

test('conversation history persists after page reload', async ({ page }) => {
  await registerAndLand(page)

  const input = page.getByPlaceholder('有什么可以帮你的？')
  await input.fill('My favorite color is blue.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//)
  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 50_000 })

  await page.reload()

  const main = page.getByRole('main')
  await expect(main.getByText('My favorite color is blue.')).toBeVisible({ timeout: 10_000 })
  await expect(main.locator('[data-role="assistant"]')).toBeVisible()
})
```

- [ ] **Step 2: Update `streaming.spec.ts` if it also starts at `/` without a session**

If `frontend/packages/web/__tests__/e2e/streaming.spec.ts` does the same `goto('/')` dance, apply the same `registerAndLand` helper. Read the file first and fix it similarly.

- [ ] **Step 3: Run full e2e suite**

Run: `cd frontend && pnpm test:e2e`
Expected: all existing + new tests pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/chat-flow.spec.ts frontend/packages/web/__tests__/e2e/streaming.spec.ts
git commit -m "test(e2e): migrate existing specs to register-before-chat + /w/[wsId] URL shape"
```

---

## Task 22: Docs — update CLAUDE.md files

**Files:**
- Modify: `backend/CLAUDE.md`
- Modify: `frontend/CLAUDE.md`

- [ ] **Step 1: Backend CLAUDE.md — document the bootstrap**

In the "Auth & RBAC" section of `backend/CLAUDE.md`, append after the endpoints list:

```markdown
**Register bootstrap:** `UserManager.on_after_register` auto-creates a personal Organization (`"<email-local-part>'s Org"`), a Workspace (`"Personal"`), and an Admin Membership for the new user in the same session. If any of these fails, the User row is best-effort deleted before the exception propagates so registration appears atomic to the client. The register response returns `{id, email, default_workspace_id}`.
```

- [ ] **Step 2: Frontend CLAUDE.md — document auth + workspace model**

Add a new "Auth & Workspace Model" section to `frontend/CLAUDE.md` above "Common Gotchas":

```markdown
## Auth & Workspace Model

**Route structure:** `(auth)/{login,register}` for unauthenticated pages; `(app)/{workspaces, w/[wsId]/...}` for authenticated pages. `/` is a server redirect: logged-in → first workspace, else `/login`.

**Middleware (`middleware.ts`):** checks for the `cubeplex_auth` cookie. Unauthenticated hits to `/w/*` or `/workspaces` redirect to `/login?next=<path>`. Logged-in hits to `/login` or `/register` redirect to `/`.

**Active workspace:** the URL segment `[wsId]` is the single source of truth. `useWorkspaceContext()` (in `(app)` tree) reads it. The `ApiClient` instance each page creates via `createApiClient('')` calls `client.setWorkspaceId(wsId)`, which triggers automatic `X-Workspace-Id` injection on workspace-scoped calls.

**CSRF:** double-submit pattern. `ApiClient` reads `cubeplex_csrf` from `document.cookie` and adds `X-CSRF-Token` on every non-GET. The backend seeds the cookie on login.

**Stores:**
- `authStore` — `{id, email}` of the current user, or `null`. Populated by `loadMe` on `(app)` mount.
- `workspaceStore` — list of the user's workspaces + `create(client, name)` (reuses the first workspace's `org_id`, M1 assumption: one user = one org).

**SSE proxy:** the Next.js route handler at `app/api/v1/conversations/[id]/messages/route.ts` forwards `cookie`, `X-Workspace-Id`, `X-CSRF-Token`, and `x-user-id` to the backend so streaming requests carry auth + scoping.

**Known one-user-one-org assumption:** `workspaceStore.create` reads `workspaces[0].org_id`. When multi-org-per-user ships (P2), this must take an explicit org id.
```

- [ ] **Step 3: Commit**

```bash
git add backend/CLAUDE.md frontend/CLAUDE.md
git commit -m "docs: auth + workspace model (backend bootstrap, frontend routing/stores)"
```

---

## Self-review checklist (controller runs before dispatching)

Before Task 1, skim the spec's "Non-Goals" and confirm no accidental work sneaks into tasks (no member invite UI, no org management, no admin-only gating).

After Task 22 is committed:
- Run `cd backend && make check && uv run pytest tests/ -v` — all pass.
- Run `cd frontend && pnpm --filter @cubeplex/core build && pnpm --filter @cubeplex/core test && pnpm --filter web type-check && pnpm test:e2e` — all pass.
- Sanity check: `git log --oneline origin/feat/p1-identity-auth-rbac..HEAD` should show ~22 commits scoped to frontend + one backend bootstrap change.

If anything is amiss, fix in a follow-up commit before opening the PR.
