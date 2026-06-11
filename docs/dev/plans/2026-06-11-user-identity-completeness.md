# User Identity Completeness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement P0 + P1 identity features: display name, invite frontend, password lifecycle, workspace rename, email infra, email verification, profile page, org settings, workspace archive/delete, leave workspace, account deletion.

**Architecture:** 5 phases with dependency ordering. Each task is a self-contained backend or frontend unit that can be committed independently. Backend tasks use TDD with pytest; frontend tasks use component-level verification. All work happens in worktree `/home/chris/cubebox/.worktrees/feat/user-identity-completeness` (slot 65, ports 8065/3065).

**Tech Stack:** FastAPI + SQLModel + Alembic (backend), Next.js 14 + React 19 + Zustand + next-intl + sonner (frontend), fastapi-users (auth), aiosmtplib (email).

**Spec:** `docs/dev/specs/2026-06-11-user-identity-completeness-design.md`

**Worktree:** Always `cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness` first. Backend port 8065, frontend port 3065. Cookie names `cubebox_auth_65` / `cubebox_csrf_65`.

---

## File Map

### Backend — New Files
- `backend/cubebox/services/email.py` — EmailService with pluggable backends (log/smtp/resend)
- `backend/cubebox/templates/email/password_reset.html` — Password reset email template
- `backend/cubebox/templates/email/password_reset.txt` — Plain text variant
- `backend/cubebox/templates/email/email_verification.html` — Email verification template
- `backend/cubebox/templates/email/email_verification.txt` — Plain text variant
- `backend/cubebox/templates/email/workspace_invite.html` — Invite email template
- `backend/cubebox/templates/email/workspace_invite.txt` — Plain text variant
- `backend/tests/unit/test_email_service.py` — Email service unit tests
- `backend/tests/unit/test_change_password.py` — Change password endpoint tests
- `backend/tests/unit/test_workspace_rename.py` — Workspace rename tests
- `backend/tests/unit/test_workspace_lifecycle.py` — Archive/delete tests
- `backend/tests/unit/test_leave_workspace.py` — Leave workspace tests
- `backend/tests/unit/test_account_deletion.py` — Account deletion tests

### Backend — Modified Files
- `backend/cubebox/models/user.py` — Add `display_name` field
- `backend/cubebox/models/workspace.py` — Add `archived_at` field
- `backend/cubebox/api/routes/v1/auth.py` — Extend register/me/patch-me, add change-password, delete-account, include reset/verify routers
- `backend/cubebox/api/routes/v1/workspaces.py` — Add PATCH rename, list/revoke invites, archive/unarchive/delete, leave, extend accept response, filter archived
- `backend/cubebox/api/routes/v1/ws_members.py` — Add display_name to response
- `backend/cubebox/api/routes/v1/admin_members.py` — Add display_name to response
- `backend/cubebox/api/routes/v1/admin.py` — Add PATCH org endpoint
- `backend/cubebox/auth/users.py` — Add on_after_forgot_password, on_after_request_verify hooks
- `backend/cubebox/repositories/invite_token.py` — Add list_for_workspace, delete methods
- `backend/cubebox/repositories/workspace.py` — Add update, archive, delete methods
- `backend/cubebox/repositories/membership.py` — Add count_admins, remove_self methods

### Frontend — New Files
- `frontend/packages/core/src/api/invites.ts` — Invite API client functions
- `frontend/packages/core/src/api/profile.ts` — Profile/password API client functions
- `frontend/packages/web/app/(auth)/forgot-password/page.tsx` — Forgot password page
- `frontend/packages/web/app/(auth)/reset-password/page.tsx` — Reset password page
- `frontend/packages/web/app/(auth)/verify-email/page.tsx` — Verify email page
- `frontend/packages/web/app/(auth)/invite/accept/page.tsx` — Accept invite page
- `frontend/packages/web/app/(app)/settings/profile/page.tsx` — User profile page
- `frontend/packages/web/components/workspace-settings/members/InviteSection.tsx` — Invite management in members tab
- `frontend/packages/web/components/workspace-settings/members/CreateInviteDialog.tsx` — Create invite dialog
- `frontend/packages/web/components/profile/ProfileForm.tsx` — Profile edit form
- `frontend/packages/web/components/profile/ChangePasswordForm.tsx` — Change password form
- `frontend/packages/web/components/profile/DeleteAccountDialog.tsx` — Account deletion dialog
- `frontend/packages/web/components/admin/settings/OrgInfoCard.tsx` — Org name/slug editor

### Frontend — Modified Files
- `frontend/packages/core/src/api/auth.ts` — Extend MeResult type, add register with display_name
- `frontend/packages/core/src/api/workspaces.ts` — Add rename, archive, unarchive, delete, leave functions
- `frontend/packages/core/src/stores/authStore.ts` — Handle display_name, is_verified
- `frontend/packages/web/components/auth/LoginForm.tsx` — Add "Forgot password?" link
- `frontend/packages/web/components/auth/RegisterForm.tsx` — Add display_name field
- `frontend/packages/web/components/sidebar/AvatarPopover.tsx` — Show display_name, add "Profile settings" link
- `frontend/packages/web/components/workspace-settings/MembersPanel.tsx` — Add InviteSection
- `frontend/packages/web/components/workspace-settings/members/WsMembersTable.tsx` — Show display_name, add leave action
- `frontend/packages/web/components/admin/members/OrgMembersTable.tsx` — Show display_name
- `frontend/packages/web/components/admin/AdminSubNav.tsx` — Ensure /admin/settings links to org settings
- `frontend/packages/web/app/admin/settings/page.tsx` — Add OrgInfoCard
- `frontend/packages/web/app/(app)/w/[wsId]/settings/page.tsx` — Add danger zone tab for archive/delete
- `frontend/packages/web/messages/en.json` — New i18n keys for all features
- `frontend/packages/web/messages/zh.json` — Chinese translations

---

## Phase 1 — Foundation

### Task 1: Email Infrastructure (Backend)

**Files:**
- Create: `backend/cubebox/services/email.py`
- Create: `backend/cubebox/templates/email/` (directory + base templates)
- Create: `backend/tests/unit/test_email_service.py`
- Modify: `backend/pyproject.toml` (add aiosmtplib)

- [ ] **Step 1: Add aiosmtplib dependency**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend
uv add aiosmtplib
```

- [ ] **Step 2: Write failing test for EmailService**

Create `backend/tests/unit/test_email_service.py`:

```python
import pytest

from cubebox.services.email import EmailService, LogEmailBackend


@pytest.mark.asyncio
async def test_log_backend_does_not_raise(capsys: pytest.CaptureFixture[str]) -> None:
    svc = EmailService(backend=LogEmailBackend())
    await svc.send(
        to="test@example.com",
        subject="Hello",
        template="password_reset",
        context={"reset_url": "https://example.com/reset?token=abc", "email": "test@example.com"},
    )
    captured = capsys.readouterr()
    assert "test@example.com" in captured.out
    assert "Hello" in captured.out


@pytest.mark.asyncio
async def test_send_renders_template() -> None:
    svc = EmailService(backend=LogEmailBackend())
    await svc.send(
        to="user@test.com",
        subject="Reset",
        template="password_reset",
        context={"reset_url": "https://x.com/reset?token=t", "email": "user@test.com"},
    )
```

Run: `cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend && uv run pytest tests/unit/test_email_service.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement EmailService**

Create `backend/cubebox/services/email.py`:

```python
"""Pluggable email service with log/smtp/resend backends."""

from __future__ import annotations

import abc
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from loguru import logger

from cubebox.config import config

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"


class EmailBackend(abc.ABC):
    @abc.abstractmethod
    async def send(self, *, to: str, subject: str, html: str, text: str) -> None: ...


class LogEmailBackend(EmailBackend):
    async def send(self, *, to: str, subject: str, html: str, text: str) -> None:
        logger.info("Email to={} subject={}", to, subject)
        print(f"--- EMAIL to={to} subject={subject} ---\n{text}\n--- END ---")


class SmtpEmailBackend(EmailBackend):
    async def send(self, *, to: str, subject: str, html: str, text: str) -> None:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        from_addr = config.get("email.from_address", "noreply@cubebox.local")
        msg = MIMEMultipart("alternative")
        msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        await aiosmtplib.send(
            msg,
            hostname=config.get("email.smtp_host", "localhost"),
            port=config.get("email.smtp_port", 587),
            username=config.get("email.smtp_user", None),
            password=config.get("email.smtp_password", None),
            use_tls=config.get("email.smtp_tls", True),
        )


class EmailService:
    def __init__(self, backend: EmailBackend | None = None) -> None:
        if backend is None:
            kind = config.get("email.backend", "log")
            backend = SmtpEmailBackend() if kind == "smtp" else LogEmailBackend()
        self._backend = backend
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=True,
        )

    async def send(
        self,
        *,
        to: str,
        subject: str,
        template: str,
        context: dict[str, str],
    ) -> None:
        html = self._env.get_template(f"{template}.html").render(**context)
        text = self._env.get_template(f"{template}.txt").render(**context)
        await self._backend.send(to=to, subject=subject, html=html, text=text)


def get_email_service() -> EmailService:
    return EmailService()
```

- [ ] **Step 4: Create email templates directory and base templates**

Create `backend/cubebox/templates/email/password_reset.html`:

```html
<p>Hi,</p>
<p>Click the link below to reset your password:</p>
<p><a href="{{ reset_url }}">{{ reset_url }}</a></p>
<p>This link expires in 1 hour. If you didn't request this, ignore this email.</p>
```

Create `backend/cubebox/templates/email/password_reset.txt`:

```
Hi,

Click the link below to reset your password:

{{ reset_url }}

This link expires in 1 hour. If you didn't request this, ignore this email.
```

Create `backend/cubebox/templates/email/email_verification.html`:

```html
<p>Welcome to cubebox!</p>
<p>Click the link below to verify your email:</p>
<p><a href="{{ verify_url }}">{{ verify_url }}</a></p>
```

Create `backend/cubebox/templates/email/email_verification.txt`:

```
Welcome to cubebox!

Click the link below to verify your email:

{{ verify_url }}
```

Create `backend/cubebox/templates/email/workspace_invite.html`:

```html
<p>You've been invited to join the workspace <strong>{{ workspace_name }}</strong> on cubebox.</p>
<p>Click the link below to accept:</p>
<p><a href="{{ invite_url }}">{{ invite_url }}</a></p>
<p>This link expires in 24 hours.</p>
```

Create `backend/cubebox/templates/email/workspace_invite.txt`:

```
You've been invited to join the workspace "{{ workspace_name }}" on cubebox.

Click the link below to accept:

{{ invite_url }}

This link expires in 24 hours.
```

- [ ] **Step 5: Run tests, verify pass**

Run: `cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend && uv run pytest tests/unit/test_email_service.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add backend/cubebox/services/email.py backend/cubebox/templates/email/ backend/tests/unit/test_email_service.py backend/pyproject.toml backend/uv.lock
git commit -m "feat: add pluggable email service with log/smtp backends"
```

---

### Task 2: User Display Name — Backend

**Files:**
- Modify: `backend/cubebox/models/user.py` — add display_name
- Modify: `backend/cubebox/api/routes/v1/auth.py` — extend register, me, patch-me
- Modify: `backend/cubebox/api/routes/v1/ws_members.py` — add display_name to response
- Modify: `backend/cubebox/api/routes/v1/admin_members.py` — add display_name to response
- Alembic migration (autogenerated)

- [ ] **Step 1: Add display_name to User model**

In `backend/cubebox/models/user.py`, add after the `language` field (line 28):

```python
display_name: str | None = Field(default=None, max_length=100)
```

- [ ] **Step 2: Generate alembic migration**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend
uv run alembic revision --autogenerate -m "add display_name to users"
uv run alembic upgrade head
```

- [ ] **Step 3: Extend PATCH /auth/me to accept display_name**

In `backend/cubebox/api/routes/v1/auth.py`, replace the `UserLanguageUpdate` model and `patch_me` route:

Replace `class UserLanguageUpdate(BaseModel):` through the entire `patch_me` function with:

```python
class UserProfileUpdate(BaseModel):
    language: Literal["en", "zh"] | None = None
    display_name: str | None = Field(None, min_length=1, max_length=100)


@router.patch("/me")
async def patch_me(
    user: Annotated[User, Depends(current_active_user)],
    body: Annotated[UserProfileUpdate, Body()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    from sqlalchemy import select

    from cubebox.models import OrganizationMembership

    if body.language is None and body.display_name is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one field required",
        )
    if body.language is not None:
        user.language = body.language
    if body.display_name is not None:
        user.display_name = body.display_name
    session.add(user)
    await session.commit()
    await session.refresh(user)
    membership_rows = (
        (
            await session.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id == user.id  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    org_memberships = [{"org_id": m.org_id, "role": m.role} for m in membership_rows]
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "language": user.language,
        "needs_org_setup": False,
        "org_memberships": org_memberships,
    }
```

- [ ] **Step 4: Add display_name to GET /auth/me response**

In the `me()` function, add `"display_name": user.display_name,` to the return dict (after the `"email"` key).

- [ ] **Step 5: Extend register to accept display_name**

Replace the `UserCreate` class:

```python
class UserCreate(BaseUserCreate):
    display_name: str | None = Field(None, min_length=1, max_length=100)
```

In the `register()` function, after `user = await user_manager.create(body, safe=True, request=request)`, add:

```python
    if body.display_name is not None:
        session = request.state.session if hasattr(request.state, "session") else None
        if session is None:
            from cubebox.db import get_session as _gs
            async for _s in _gs():
                session = _s
                break
        if session is not None:
            user.display_name = body.display_name
            session.add(user)
            await session.commit()
```

Actually, simpler approach — add `session` as a dependency to `register()` and set display_name:

Add `session: Annotated[AsyncSession, Depends(get_session)],` to the register function signature.

After `user = await user_manager.create(...)`, add:

```python
    if body.display_name is not None:
        user.display_name = body.display_name
        session.add(user)
        await session.commit()
```

- [ ] **Step 6: Add display_name to member list responses**

In `backend/cubebox/api/routes/v1/ws_members.py`:
- Add `display_name: str | None = None` to `WsMemberOut` model
- Add `display_name: str | None = None` to `AddWsMemberResponse` model
- In `list_workspace_members()`, add `"display_name": u.display_name` when building each WsMemberOut (where `u` is the User looked up by user_id)
- In `add_workspace_member()`, look up the target user and include display_name in response

In `backend/cubebox/api/routes/v1/admin_members.py`:
- Add `display_name: str | None = None` to `OrgMemberOut` model
- Add `display_name: str | None = None` to `AddOrgMemberResponse` model
- In `list_org_members()`, add display_name from User lookup
- In `add_org_member()`, include display_name in response

- [ ] **Step 7: Run mypy and existing tests**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend
uv run mypy cubebox/
uv run pytest tests/unit/ -x -q
```

- [ ] **Step 8: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add backend/cubebox/models/user.py backend/cubebox/api/routes/v1/auth.py backend/cubebox/api/routes/v1/ws_members.py backend/cubebox/api/routes/v1/admin_members.py backend/alembic/versions/
git commit -m "feat: add user display_name to model, auth API, and member lists"
```

---

### Task 3: User Display Name — Frontend

**Files:**
- Modify: `frontend/packages/core/src/api/auth.ts` — extend types
- Modify: `frontend/packages/web/components/auth/RegisterForm.tsx` — add name field
- Modify: `frontend/packages/web/components/sidebar/AvatarPopover.tsx` — show display name
- Modify: `frontend/packages/web/components/workspace-settings/members/WsMembersTable.tsx` — display_name
- Modify: `frontend/packages/web/components/admin/members/OrgMembersTable.tsx` — display_name
- Modify: `frontend/packages/core/src/api/members.ts` — extend types
- Modify: `frontend/packages/web/messages/en.json` — add i18n keys
- Modify: `frontend/packages/web/messages/zh.json` — add i18n keys

- [ ] **Step 1: Extend MeResult type and registerUser**

In `frontend/packages/core/src/api/auth.ts`:

Add `display_name` to `MeResult`:

```typescript
export interface MeResult {
  id: string
  email: string
  display_name: string | null
  language: string
  is_verified?: boolean
  needs_org_setup?: boolean
  org_memberships?: OrgMembership[]
}
```

Extend `registerUser` to accept optional display_name:

```typescript
export async function registerUser(
  client: ApiClient,
  email: string,
  password: string,
  displayName?: string,
): Promise<RegisterResult> {
  const body: Record<string, string> = { email, password }
  if (displayName) body.display_name = displayName
  const res = await client.post('/api/v1/auth/register', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as RegisterResult
}
```

Add `updateProfile` function:

```typescript
export async function updateProfile(
  client: ApiClient,
  patch: { display_name?: string; language?: 'en' | 'zh' },
): Promise<MeResult> {
  const res = await client.patch('/api/v1/auth/me', patch)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MeResult
}
```

- [ ] **Step 2: Extend member types**

In `frontend/packages/core/src/api/members.ts`, add `display_name` to interfaces:

```typescript
export interface OrgMember {
  user_id: string
  email: string
  display_name: string | null
  role: 'owner' | 'admin' | 'member'
  created_at: string
}

export interface WsMember {
  user_id: string
  email: string
  display_name: string | null
  role: 'admin' | 'member'
  created_at: string
}
```

- [ ] **Step 3: Add display_name field to RegisterForm**

In `frontend/packages/web/components/auth/RegisterForm.tsx`, add state:

```typescript
const [displayName, setDisplayName] = useState('')
```

Pass to registerUser:

```typescript
const result = await registerUser(client, email, password, displayName || undefined)
```

Add input field before email (after the title div):

```tsx
<label className="block">
  <span className="text-sm text-foreground/80">{t('displayName')}</span>
  <input
    type="text"
    autoComplete="name"
    maxLength={100}
    className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
    value={displayName}
    onChange={(e) => setDisplayName(e.target.value)}
  />
</label>
```

- [ ] **Step 4: Show display_name in AvatarPopover**

In `frontend/packages/web/components/sidebar/AvatarPopover.tsx`:

Change the initials logic (line 37):

```typescript
const displayName = user?.display_name ?? null
const initials = displayName ? displayName[0]?.toUpperCase() : user?.email ? user.email[0]?.toUpperCase() : '?'
```

Change the trigger span (inside PopoverTrigger, the email span):

```tsx
<span className="text-xs truncate flex-1 text-left text-foreground">
  {displayName ?? user?.email ?? '...'}
</span>
```

Change the popover header (the email div inside PopoverContent):

```tsx
<div className="px-2 py-2 border-b border-border mb-1">
  {displayName && (
    <div className="text-xs font-medium text-foreground truncate">{displayName}</div>
  )}
  <div className="text-2xs text-muted-foreground truncate">{user?.email}</div>
</div>
```

- [ ] **Step 5: Show display_name in member tables**

In `WsMembersTable.tsx`, wherever a member's email is displayed as the primary identifier, show display_name first:

In the `WsMemberRow` component's email cell, change from just showing email to:

```tsx
<TableCell>
  <div className="flex flex-col">
    <span className="text-sm">{member.display_name ?? member.email.split('@')[0]}</span>
    <span className="text-xs text-muted-foreground">{member.email}</span>
  </div>
</TableCell>
```

Apply the same pattern in `OrgMembersTable.tsx`.

- [ ] **Step 6: Add i18n keys**

In `frontend/packages/web/messages/en.json`, add to `"auth"`:

```json
"displayName": "Your name (optional)"
```

In `frontend/packages/web/messages/zh.json`, add to `"auth"`:

```json
"displayName": "你的名字（可选）"
```

- [ ] **Step 7: Build core and verify**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/frontend
pnpm --filter @cubebox/core build
pnpm --filter @cubebox/web typecheck
```

- [ ] **Step 8: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add frontend/
git commit -m "feat(ui): display user name in registration, sidebar, and member lists"
```

---

### Task 4: Workspace Rename — Backend

**Files:**
- Modify: `backend/cubebox/api/routes/v1/workspaces.py` — add PATCH endpoint
- Modify: `backend/cubebox/repositories/workspace.py` — add update method

- [ ] **Step 1: Add update method to WorkspaceRepository**

In `backend/cubebox/repositories/workspace.py`, add:

```python
async def update_name(self, workspace_id: str, name: str) -> Workspace | None:
    ws = await self.get(workspace_id)
    if ws is None:
        return None
    ws.name = name
    self._session.add(ws)
    await self._session.commit()
    await self._session.refresh(ws)
    return ws
```

- [ ] **Step 2: Add PATCH /workspaces/{workspace_id} endpoint**

In `backend/cubebox/api/routes/v1/workspaces.py`, add a new Pydantic model after `WorkspaceCreate`:

```python
class WorkspaceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
```

Add the route after `create_workspace`:

```python
@router.patch("/{workspace_id}")
async def rename_workspace(
    workspace_id: str,
    body: Annotated[WorkspaceUpdate, Body()],
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> dict[str, str]:
    ws_repo = WorkspaceRepository(session)
    ws = await ws_repo.update_name(workspace_id, body.name)
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace not found")

    from cubebox.plugins.audit import audit_log

    await audit_log(
        action="workspace.renamed",
        user_id=ctx.user.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        ip=request.client.host if request.client else None,
        metadata={"new_name": body.name},
    )
    return {"id": ws.id, "name": ws.name, "org_id": ws.org_id}
```

- [ ] **Step 3: Run mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend
uv run mypy cubebox/api/routes/v1/workspaces.py cubebox/repositories/workspace.py
```

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add backend/cubebox/api/routes/v1/workspaces.py backend/cubebox/repositories/workspace.py
git commit -m "feat: add workspace rename endpoint (PATCH /workspaces/{id})"
```

---

### Task 5: Workspace Rename — Frontend

**Files:**
- Modify: `frontend/packages/core/src/api/workspaces.ts` — add renameWorkspace
- Modify: `frontend/packages/core/src/stores/workspaceStore.ts` — add rename action
- Modify: `frontend/packages/web/components/workspace-settings/PersonaEditor.tsx` or create dedicated workspace settings component
- Modify: `frontend/packages/web/messages/en.json` / `zh.json`

- [ ] **Step 1: Add renameWorkspace API function**

In `frontend/packages/core/src/api/workspaces.ts`, add:

```typescript
export async function renameWorkspace(
  client: ApiClient,
  wsId: string,
  name: string,
): Promise<Workspace> {
  const res = await client.patch(`/api/v1/workspaces/${wsId}`, { name })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as Workspace
}
```

Export from core's index.

- [ ] **Step 2: Add rename action to workspaceStore**

In `frontend/packages/core/src/stores/workspaceStore.ts`, add to the interface and implementation:

```typescript
rename(client: ApiClient, wsId: string, name: string): Promise<void>
```

Implementation:

```typescript
async rename(client, wsId, name) {
  const updated = await renameWorkspace(client, wsId, name)
  set((s) => ({
    workspaces: s.workspaces.map((w) => (w.id === wsId ? { ...w, name: updated.name } : w)),
  }))
},
```

- [ ] **Step 3: Add workspace name editing to workspace settings tab**

The "workspace" tab currently renders `PersonaEditor`. Add a workspace name section at the top of the settings tab. The simplest approach: create an inline editable name field in the workspace settings page.

This is best done by modifying `PersonaEditor` to include a name field above the system prompt, or by adding a separate section in the settings page layout. Check what PersonaEditor currently does and extend accordingly.

- [ ] **Step 4: Add i18n keys**

In `en.json` under `wsSettings` (or create if needed):

```json
"workspaceName": "Workspace name",
"workspaceNameSaved": "Workspace name updated"
```

Chinese counterparts in `zh.json`.

- [ ] **Step 5: Build and verify**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/frontend
pnpm --filter @cubebox/core build
pnpm --filter @cubebox/web typecheck
```

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add frontend/
git commit -m "feat(ui): workspace rename in settings"
```

---

## Phase 2 — Password Lifecycle

### Task 6: Forgot Password — Backend

**Files:**
- Modify: `backend/cubebox/api/routes/v1/auth.py` — register reset-password router
- Modify: `backend/cubebox/auth/users.py` — add on_after_forgot_password hook

- [ ] **Step 1: Register reset-password router**

In `backend/cubebox/api/routes/v1/auth.py`, at the bottom of the file (after the existing `router.include_router` line), add:

```python
router.include_router(fastapi_users.get_reset_password_router(), prefix="")
```

This mounts `POST /auth/forgot-password` and `POST /auth/reset-password`.

- [ ] **Step 2: Implement on_after_forgot_password hook in UserManager**

In `backend/cubebox/auth/users.py`, add to the `UserManager` class (after `on_after_login`):

```python
async def on_after_forgot_password(
    self, user: User, token: str, request: Request | None = None
) -> None:
    from cubebox.services.email import get_email_service

    base_url = config.get("app.base_url", "http://localhost:3000")
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
```

- [ ] **Step 3: Run mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend
uv run mypy cubebox/api/routes/v1/auth.py cubebox/auth/users.py
```

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add backend/cubebox/api/routes/v1/auth.py backend/cubebox/auth/users.py
git commit -m "feat: enable forgot/reset password via fastapi-users router + email hook"
```

---

### Task 7: Forgot Password — Frontend

**Files:**
- Create: `frontend/packages/web/app/(auth)/forgot-password/page.tsx`
- Create: `frontend/packages/web/app/(auth)/reset-password/page.tsx`
- Create: `frontend/packages/core/src/api/profile.ts` — password-related API
- Modify: `frontend/packages/web/components/auth/LoginForm.tsx` — add "Forgot password?" link
- Modify: `frontend/packages/web/messages/en.json` / `zh.json`

- [ ] **Step 1: Create profile API module**

Create `frontend/packages/core/src/api/profile.ts`:

```typescript
import { toApiError, type ApiClient } from './client'

export async function forgotPassword(client: ApiClient, email: string): Promise<void> {
  const res = await client.post('/api/v1/auth/forgot-password', { email })
  // fastapi-users returns 202 regardless of whether the email exists
  if (!res.ok && res.status !== 202) throw await toApiError(res)
}

export async function resetPassword(
  client: ApiClient,
  token: string,
  password: string,
): Promise<void> {
  const res = await client.post('/api/v1/auth/reset-password', { token, password })
  if (!res.ok) throw await toApiError(res)
}

export async function changePassword(
  client: ApiClient,
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  const res = await client.post('/api/v1/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
  if (!res.ok) throw await toApiError(res)
}
```

Export from core's index.

- [ ] **Step 2: Create forgot-password page**

Create `frontend/packages/web/app/(auth)/forgot-password/page.tsx`:

```tsx
'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { createApiClient, forgotPassword } from '@cubebox/core'

export default function ForgotPasswordPage() {
  const t = useTranslations('auth')
  const [email, setEmail] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    try {
      const client = createApiClient('')
      await forgotPassword(client, email)
    } catch {
      // Intentionally ignore — show success message regardless
    } finally {
      setSubmitting(false)
      setSubmitted(true)
    }
  }

  return (
    <div className="space-y-4">
      <div className="text-center mb-6">
        <h1 className="text-xl font-semibold">{t('forgotPasswordTitle')}</h1>
      </div>
      {submitted ? (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground text-center">
            {t('forgotPasswordSent')}
          </p>
          <div className="text-center text-sm">
            <Link href="/login" className="underline">{t('backToSignIn')}</Link>
          </div>
        </div>
      ) : (
        <form onSubmit={onSubmit} className="space-y-4">
          <label className="block">
            <span className="text-sm text-foreground/80">{t('email')}</span>
            <input
              type="email"
              required
              autoComplete="email"
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
          >
            {submitting ? t('sending') : t('sendResetLink')}
          </button>
          <div className="text-center text-sm text-foreground/60">
            <Link href="/login" className="underline">{t('backToSignIn')}</Link>
          </div>
        </form>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Create reset-password page**

Create `frontend/packages/web/app/(auth)/reset-password/page.tsx`:

```tsx
'use client'

import { use, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { createApiClient, resetPassword } from '@cubebox/core'

export default function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>
}) {
  const { token } = use(searchParams)
  const t = useTranslations('auth')
  const router = useRouter()
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (password !== confirm) {
      setError(t('passwordMismatch'))
      return
    }
    if (!token) {
      setError(t('invalidResetLink'))
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      const client = createApiClient('')
      await resetPassword(client, token, password)
      setSuccess(true)
      setTimeout(() => router.push('/login'), 3000)
    } catch {
      setError(t('invalidResetLink'))
    } finally {
      setSubmitting(false)
    }
  }

  if (success) {
    return (
      <div className="space-y-4 text-center">
        <h1 className="text-xl font-semibold">{t('passwordResetSuccess')}</h1>
        <p className="text-sm text-muted-foreground">{t('redirectingToLogin')}</p>
      </div>
    )
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <div className="text-center mb-6">
        <h1 className="text-xl font-semibold">{t('resetPasswordTitle')}</h1>
      </div>
      <label className="block">
        <span className="text-sm text-foreground/80">{t('newPassword')}</span>
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
      <label className="block">
        <span className="text-sm text-foreground/80">{t('confirmPassword')}</span>
        <input
          type="password"
          required
          minLength={8}
          autoComplete="new-password"
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
      </label>
      {error && <div className="text-sm text-destructive">{error}</div>}
      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
      >
        {submitting ? t('resetting') : t('resetPassword')}
      </button>
      <div className="text-center text-sm text-foreground/60">
        <Link href="/forgot-password" className="underline">{t('requestNewLink')}</Link>
      </div>
    </form>
  )
}
```

- [ ] **Step 4: Add "Forgot password?" link to LoginForm**

In `frontend/packages/web/components/auth/LoginForm.tsx`, after the password input label (before the error div), add:

```tsx
<div className="text-right">
  <Link href="/forgot-password" className="text-xs text-muted-foreground underline">
    {t('forgotPassword')}
  </Link>
</div>
```

- [ ] **Step 5: Add i18n keys**

In `en.json` under `"auth"`, add:

```json
"forgotPassword": "Forgot password?",
"forgotPasswordTitle": "Reset your password",
"forgotPasswordSent": "If an account exists for that email, we've sent a reset link. Check your inbox.",
"backToSignIn": "Back to sign in",
"sendResetLink": "Send reset link",
"sending": "Sending…",
"resetPasswordTitle": "Set a new password",
"newPassword": "New password",
"confirmPassword": "Confirm password",
"passwordMismatch": "Passwords don't match",
"invalidResetLink": "This reset link is invalid or has expired.",
"resetPassword": "Reset password",
"resetting": "Resetting…",
"passwordResetSuccess": "Password updated",
"redirectingToLogin": "Redirecting to sign in…",
"requestNewLink": "Request a new link"
```

Add Chinese counterparts in `zh.json`.

- [ ] **Step 6: Build and verify**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/frontend
pnpm --filter @cubebox/core build
pnpm --filter @cubebox/web typecheck
```

- [ ] **Step 7: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add frontend/
git commit -m "feat(ui): forgot/reset password pages and login link"
```

---

### Task 8: Change Password — Backend

**Files:**
- Modify: `backend/cubebox/api/routes/v1/auth.py` — add POST /auth/change-password

- [ ] **Step 1: Add change-password endpoint**

In `backend/cubebox/api/routes/v1/auth.py`, add a new model and route:

```python
class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


@router.post("/change-password")
async def change_password(
    body: Annotated[ChangePasswordRequest, Body()],
    user: Annotated[User, Depends(current_active_user)],
    user_manager: Annotated[UserManager, Depends(get_user_manager)],
    request: Request,
) -> dict[str, bool]:
    verified, _ = user_manager.password_helper.verify_and_update(
        body.current_password, user.hashed_password
    )
    if not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="incorrect_password",
        )
    try:
        await user_manager.validate_password(body.new_password, user)
    except InvalidPasswordException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_password",
        ) from None
    user.hashed_password = user_manager.password_helper.hash(body.new_password)
    session = user_manager.user_db.session  # type: ignore[attr-defined]
    session.add(user)
    await session.commit()

    from cubebox.plugins.audit import audit_log

    await audit_log(
        action="auth.password_changed",
        user_id=user.id,
        ip=request.client.host if request.client else None,
    )
    return {"ok": True}
```

- [ ] **Step 2: Run mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend
uv run mypy cubebox/api/routes/v1/auth.py
```

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add backend/cubebox/api/routes/v1/auth.py
git commit -m "feat: add POST /auth/change-password endpoint"
```

---

## Phase 3 — Invite System

### Task 9: Invite Backend Additions

**Files:**
- Modify: `backend/cubebox/repositories/invite_token.py` — add list, delete
- Modify: `backend/cubebox/api/routes/v1/workspaces.py` — add GET invites, DELETE invite, extend accept response

- [ ] **Step 1: Extend InviteTokenRepository**

In `backend/cubebox/repositories/invite_token.py`, add:

```python
async def list_for_workspace(self, workspace_id: str) -> list[InviteToken]:
    from sqlalchemy import select

    stmt = (
        select(InviteToken)
        .where(InviteToken.workspace_id == workspace_id)  # type: ignore[arg-type]
        .order_by(InviteToken.expires_at.desc())  # type: ignore[union-attr]
    )
    rows = (await self._session.execute(stmt)).scalars().all()
    return list(rows)

async def delete(self, token: str) -> None:
    from sqlalchemy import delete as sa_delete

    await self._session.execute(
        sa_delete(InviteToken).where(InviteToken.token == token)  # type: ignore[arg-type]
    )
    await self._session.commit()
```

- [ ] **Step 2: Add GET /workspaces/{workspace_id}/invites**

In `backend/cubebox/api/routes/v1/workspaces.py`, add:

```python
@router.get("/{workspace_id}/invites")
async def list_invites(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, str | None]]:
    inv_repo = InviteTokenRepository(session)
    tokens = await inv_repo.list_for_workspace(workspace_id)
    return [
        {
            "token": t.token,
            "role": t.role,
            "created_by": t.created_by,
            "expires_at": utc_isoformat(t.expires_at),
            "used_at": utc_isoformat(t.used_at) if t.used_at else None,
        }
        for t in tokens
    ]
```

- [ ] **Step 3: Add DELETE /workspaces/{workspace_id}/invites/{token}**

```python
@router.delete("/{workspace_id}/invites/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    workspace_id: str,
    token: str,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    inv_repo = InviteTokenRepository(session)
    await inv_repo.delete(token)
```

- [ ] **Step 4: Extend accept_invite response**

In the `accept_invite` function, after consuming the token and granting membership, look up the workspace and return additional fields:

```python
    ws = await WorkspaceRepository(session).get(tok.workspace_id)
    ws_name = ws.name if ws else ""
    org_id = ws.org_id if ws else ""
    return {
        "workspace_id": tok.workspace_id,
        "workspace_name": ws_name,
        "org_id": org_id,
        "role": tok.role,
    }
```

Also: before granting workspace membership, check if the user is an org member. If not, auto-add them:

```python
    from cubebox.models import OrgRole
    from cubebox.repositories import OrganizationMembershipRepository

    ws = await WorkspaceRepository(session).get(tok.workspace_id)
    if ws is not None:
        om_repo = OrganizationMembershipRepository(session)
        existing_org_role = await om_repo.get_role(user_id=user.id, org_id=ws.org_id)
        if existing_org_role is None:
            await om_repo.grant(user_id=user.id, org_id=ws.org_id, role=OrgRole.MEMBER)
```

- [ ] **Step 5: Run mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend
uv run mypy cubebox/api/routes/v1/workspaces.py cubebox/repositories/invite_token.py
```

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add backend/cubebox/api/routes/v1/workspaces.py backend/cubebox/repositories/invite_token.py
git commit -m "feat: invite list/revoke endpoints and enriched accept response"
```

---

### Task 10: Invite Frontend

**Files:**
- Create: `frontend/packages/core/src/api/invites.ts`
- Create: `frontend/packages/web/app/(auth)/invite/accept/page.tsx`
- Create: `frontend/packages/web/components/workspace-settings/members/InviteSection.tsx`
- Create: `frontend/packages/web/components/workspace-settings/members/CreateInviteDialog.tsx`
- Modify: `frontend/packages/web/components/workspace-settings/MembersPanel.tsx`
- Modify: `frontend/packages/web/messages/en.json` / `zh.json`

- [ ] **Step 1: Create invite API module**

Create `frontend/packages/core/src/api/invites.ts`:

```typescript
import { toApiError, type ApiClient } from './client'

export interface InviteToken {
  token: string
  role: string
  created_by: string
  expires_at: string
  used_at: string | null
}

export interface AcceptInviteResult {
  workspace_id: string
  workspace_name: string
  org_id: string
  role: string
}

export async function createInvite(
  client: ApiClient,
  wsId: string,
  role: string,
  email?: string,
): Promise<{ token: string; expires_at: string }> {
  const body: Record<string, string> = { role }
  if (email) body.email = email
  const res = await client.post(`/api/v1/workspaces/${wsId}/invites`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { token: string; expires_at: string }
}

export async function listInvites(client: ApiClient, wsId: string): Promise<InviteToken[]> {
  const res = await client.get(`/api/v1/workspaces/${wsId}/invites`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as InviteToken[]
}

export async function revokeInvite(
  client: ApiClient,
  wsId: string,
  token: string,
): Promise<void> {
  const res = await client.del(`/api/v1/workspaces/${wsId}/invites/${token}`)
  if (!res.ok) throw await toApiError(res)
}

export async function acceptInvite(
  client: ApiClient,
  token: string,
): Promise<AcceptInviteResult> {
  const res = await client.post('/api/v1/workspaces/invites/accept', { token })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as AcceptInviteResult
}
```

Export from core's index.

- [ ] **Step 2: Create accept invite page**

Create `frontend/packages/web/app/(auth)/invite/accept/page.tsx`:

```tsx
'use client'

import { use, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { createApiClient, acceptInvite, useAuthStore, type AcceptInviteResult } from '@cubebox/core'

export default function AcceptInvitePage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>
}) {
  const { token } = use(searchParams)
  const t = useTranslations('invite')
  const router = useRouter()
  const user = useAuthStore((s) => s.user)
  const [result, setResult] = useState<AcceptInviteResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!token) {
      setError(t('invalidLink'))
      setLoading(false)
      return
    }
    if (!user) {
      router.replace(`/login?next=${encodeURIComponent(`/invite/accept?token=${token}`)}`)
      return
    }
    const client = createApiClient('')
    acceptInvite(client, token)
      .then((r) => setResult(r))
      .catch(() => setError(t('expiredOrUsed')))
      .finally(() => setLoading(false))
  }, [token, user, router, t])

  if (loading) {
    return <p className="text-center text-sm text-muted-foreground">{t('accepting')}</p>
  }

  if (error) {
    return (
      <div className="space-y-4 text-center">
        <p className="text-sm text-destructive">{error}</p>
        <Link href="/" className="text-sm underline">{t('goHome')}</Link>
      </div>
    )
  }

  if (result) {
    return (
      <div className="space-y-4 text-center">
        <h1 className="text-xl font-semibold">{t('joined')}</h1>
        <p className="text-sm text-muted-foreground">
          {t('joinedDescription', { workspace: result.workspace_name })}
        </p>
        <Link
          href={`/w/${result.workspace_id}`}
          className="inline-block rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
        >
          {t('openWorkspace')}
        </Link>
      </div>
    )
  }

  return null
}
```

- [ ] **Step 3: Create CreateInviteDialog**

Create `frontend/packages/web/components/workspace-settings/members/CreateInviteDialog.tsx`:

```tsx
'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Copy, Check } from 'lucide-react'
import { createApiClient, createInvite } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import * as DialogPrimitive from '@base-ui-components/react/dialog'

interface CreateInviteDialogProps {
  wsId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CreateInviteDialog({ wsId, open, onOpenChange }: CreateInviteDialogProps) {
  const t = useTranslations('wsMembers.invite')
  const [role, setRole] = useState('member')
  const [link, setLink] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onCreate = async () => {
    setCreating(true)
    setError(null)
    try {
      const client = createApiClient('')
      const result = await createInvite(client, wsId, role)
      setLink(`${window.location.origin}/invite/accept?token=${result.token}`)
    } catch {
      setError(t('createError'))
    } finally {
      setCreating(false)
    }
  }

  const onCopy = async () => {
    if (!link) return
    await navigator.clipboard.writeText(link)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const onClose = () => {
    setLink(null)
    setCopied(false)
    setError(null)
    setRole('member')
    onOpenChange(false)
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(o) => (o ? onOpenChange(true) : onClose())}>
      <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm data-[ending-style]:opacity-0 transition-opacity duration-200" />
      <DialogPrimitive.Popup className="fixed left-1/2 top-1/2 z-50 w-[min(420px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border bg-popover p-5 shadow-xl data-[ending-style]:opacity-0 transition-opacity duration-200">
        <h2 className="text-base font-semibold mb-4">{t('title')}</h2>
        {!link ? (
          <>
            <label className="block mb-3">
              <span className="text-sm text-foreground/80">{t('roleLabel')}</span>
              <Select value={role} onValueChange={setRole}>
                <SelectTrigger className="mt-1 w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="admin">{t('admin')}</SelectItem>
                  <SelectItem value="member">{t('member')}</SelectItem>
                </SelectContent>
              </Select>
            </label>
            {error && <div className="text-sm text-destructive mb-3">{error}</div>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={onClose}>{t('cancel')}</Button>
              <Button onClick={onCreate} disabled={creating}>
                {creating ? t('creating') : t('create')}
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="text-sm text-muted-foreground mb-2">{t('linkReady')}</p>
            <div className="flex items-center gap-2 rounded-md border border-border bg-muted/50 px-3 py-2">
              <code className="flex-1 text-xs break-all">{link}</code>
              <Button variant="ghost" size="icon" onClick={onCopy} className="shrink-0">
                {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
              </Button>
            </div>
            <div className="flex justify-end mt-4">
              <Button onClick={onClose}>{t('done')}</Button>
            </div>
          </>
        )}
      </DialogPrimitive.Popup>
    </DialogPrimitive.Root>
  )
}
```

- [ ] **Step 4: Create InviteSection**

Create `frontend/packages/web/components/workspace-settings/members/InviteSection.tsx`:

```tsx
'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useFormatter, useTranslations } from 'next-intl'
import { Link2, Trash2 } from 'lucide-react'
import { createApiClient, listInvites, revokeInvite, type InviteToken } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { CreateInviteDialog } from './CreateInviteDialog'

function inviteStatus(invite: InviteToken): 'pending' | 'used' | 'expired' {
  if (invite.used_at) return 'used'
  if (new Date(invite.expires_at) < new Date()) return 'expired'
  return 'pending'
}

export function InviteSection({ wsId }: { wsId: string }) {
  const t = useTranslations('wsMembers.invite')
  const format = useFormatter()
  const client = useMemo(() => createApiClient(''), [])
  const [invites, setInvites] = useState<InviteToken[]>([])
  const [createOpen, setCreateOpen] = useState(false)

  const load = useCallback(async () => {
    const data = await listInvites(client, wsId)
    setInvites(data)
  }, [client, wsId])

  useEffect(() => { void load() }, [load])

  const onRevoke = async (token: string) => {
    await revokeInvite(client, wsId, token)
    setInvites((prev) => prev.filter((i) => i.token !== token))
  }

  const pending = invites.filter((i) => inviteStatus(i) === 'pending')

  return (
    <div className="mt-6">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-medium">{t('sectionTitle')}</h3>
        <Button variant="outline" size="sm" onClick={() => setCreateOpen(true)} className="gap-1.5">
          <Link2 className="size-3.5" />
          {t('createLink')}
        </Button>
      </div>
      {pending.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t('role')}</TableHead>
              <TableHead>{t('expires')}</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {pending.map((inv) => (
              <TableRow key={inv.token}>
                <TableCell className="text-sm capitalize">{inv.role}</TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {format.dateTime(new Date(inv.expires_at), { dateStyle: 'short', timeStyle: 'short' })}
                </TableCell>
                <TableCell>
                  <Button variant="ghost" size="icon" onClick={() => onRevoke(inv.token)}>
                    <Trash2 className="size-3.5 text-destructive" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <CreateInviteDialog wsId={wsId} open={createOpen} onOpenChange={(o) => { setCreateOpen(o); if (!o) void load() }} />
    </div>
  )
}
```

- [ ] **Step 5: Add InviteSection to MembersPanel**

In `frontend/packages/web/components/workspace-settings/MembersPanel.tsx`, import `InviteSection` and render it below `WsMembersTable`:

```tsx
import { InviteSection } from './members/InviteSection'

// In the admin-only branch, after <WsMembersTable>:
<WsMembersTable wsId={wsId} />
<InviteSection wsId={wsId} />
```

- [ ] **Step 6: Add i18n keys**

In `en.json`, add new namespaces:

Under `"wsMembers"`, add:

```json
"invite": {
  "sectionTitle": "Invite links",
  "createLink": "Create invite link",
  "role": "Role",
  "expires": "Expires",
  "title": "Create invite link",
  "roleLabel": "Role for invited user",
  "admin": "Admin",
  "member": "Member",
  "cancel": "Cancel",
  "create": "Create",
  "creating": "Creating…",
  "createError": "Failed to create invite",
  "linkReady": "Share this link with the person you'd like to invite:",
  "done": "Done"
}
```

Add top-level `"invite"`:

```json
"invite": {
  "accepting": "Accepting invite…",
  "invalidLink": "Invalid invite link.",
  "expiredOrUsed": "This invite link has expired or has already been used.",
  "goHome": "Go to homepage",
  "joined": "You're in!",
  "joinedDescription": "You've joined the workspace \"{workspace}\".",
  "openWorkspace": "Open workspace"
}
```

Add Chinese counterparts in `zh.json`.

- [ ] **Step 7: Build and verify**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/frontend
pnpm --filter @cubebox/core build
pnpm --filter @cubebox/web typecheck
```

- [ ] **Step 8: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add frontend/
git commit -m "feat(ui): invite creation, management, and accept page"
```

---

## Phase 4 — Profile & Settings

### Task 11: User Profile Page — Frontend

**Files:**
- Create: `frontend/packages/web/app/(app)/settings/profile/page.tsx`
- Create: `frontend/packages/web/components/profile/ProfileForm.tsx`
- Create: `frontend/packages/web/components/profile/ChangePasswordForm.tsx`
- Modify: `frontend/packages/web/components/sidebar/AvatarPopover.tsx` — add profile link
- Modify: `frontend/packages/web/messages/en.json` / `zh.json`

- [ ] **Step 1: Create ProfileForm component**

Create `frontend/packages/web/components/profile/ProfileForm.tsx`:

```tsx
'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { createApiClient, updateProfile, useAuthStore } from '@cubebox/core'
import { Button } from '@/components/ui/button'

export function ProfileForm() {
  const t = useTranslations('profile')
  const user = useAuthStore((s) => s.user)
  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [saving, setSaving] = useState(false)
  const dirty = displayName !== (user?.display_name ?? '')

  const onSave = async () => {
    setSaving(true)
    try {
      const client = createApiClient('')
      await updateProfile(client, { display_name: displayName })
      await useAuthStore.getState().loadMe(client)
      toast.success(t('saved'))
    } catch {
      toast.error(t('saveError'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="space-y-4">
      <h2 className="text-base font-medium">{t('personalInfo')}</h2>
      <label className="block">
        <span className="text-sm text-muted-foreground">{t('displayNameLabel')}</span>
        <input
          type="text"
          maxLength={100}
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </label>
      <label className="block">
        <span className="text-sm text-muted-foreground">{t('emailLabel')}</span>
        <input
          type="email"
          readOnly
          className="mt-1 w-full rounded-md border border-border bg-muted px-3 py-2 text-sm text-muted-foreground"
          value={user?.email ?? ''}
        />
      </label>
      {dirty && (
        <Button onClick={onSave} disabled={saving}>
          {saving ? t('saving') : t('save')}
        </Button>
      )}
    </section>
  )
}
```

- [ ] **Step 2: Create ChangePasswordForm**

Create `frontend/packages/web/components/profile/ChangePasswordForm.tsx`:

```tsx
'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { createApiClient, changePassword } from '@cubebox/core'
import { Button } from '@/components/ui/button'

export function ChangePasswordForm() {
  const t = useTranslations('profile')
  const [current, setCurrent] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (newPw !== confirm) {
      setError(t('passwordMismatch'))
      return
    }
    setError(null)
    setSaving(true)
    try {
      const client = createApiClient('')
      await changePassword(client, current, newPw)
      toast.success(t('passwordChanged'))
      setCurrent('')
      setNewPw('')
      setConfirm('')
    } catch (err) {
      const detail = (err as { detail?: string }).detail
      setError(detail === 'incorrect_password' ? t('incorrectPassword') : t('passwordChangeError'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <h2 className="text-base font-medium mb-4">{t('changePassword')}</h2>
      <form onSubmit={onSubmit} className="space-y-3 max-w-sm">
        <input
          type="password"
          required
          placeholder={t('currentPassword')}
          autoComplete="current-password"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
        <input
          type="password"
          required
          minLength={8}
          placeholder={t('newPassword')}
          autoComplete="new-password"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={newPw}
          onChange={(e) => setNewPw(e.target.value)}
        />
        <input
          type="password"
          required
          minLength={8}
          placeholder={t('confirmPassword')}
          autoComplete="new-password"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        {error && <div className="text-sm text-destructive">{error}</div>}
        <Button type="submit" disabled={saving}>
          {saving ? t('saving') : t('updatePassword')}
        </Button>
      </form>
    </section>
  )
}
```

- [ ] **Step 3: Create profile page**

Create `frontend/packages/web/app/(app)/settings/profile/page.tsx`:

```tsx
'use client'

import { useTranslations } from 'next-intl'
import { ProfileForm } from '@/components/profile/ProfileForm'
import { ChangePasswordForm } from '@/components/profile/ChangePasswordForm'
import { PageHeader } from '@/components/management/PageHeader'

export default function ProfilePage() {
  const t = useTranslations('profile')
  return (
    <div className="flex h-full flex-col">
      <PageHeader title={t('title')} description={t('subtitle')} />
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-xl flex-col gap-8">
          <ProfileForm />
          <hr className="border-border" />
          <ChangePasswordForm />
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Add profile link to AvatarPopover**

In `frontend/packages/web/components/sidebar/AvatarPopover.tsx`, add a profile link after the admin/back-to-app section and before the theme toggle. Import `User` from lucide-react.

```tsx
<Link
  href="/settings/profile"
  className="flex items-center gap-2 px-2 py-1.5 rounded-sm text-[12.5px] hover:bg-accent/60 transition-colors"
>
  <User className="size-3.5 text-muted-foreground" />
  <span>{t('profileSettings')}</span>
</Link>
```

Add `"profileSettings": "Profile settings"` to the `"avatar"` i18n keys.

- [ ] **Step 5: Add i18n keys**

In `en.json`, add `"profile"`:

```json
"profile": {
  "title": "Profile",
  "subtitle": "Manage your personal info and password",
  "personalInfo": "Personal info",
  "displayNameLabel": "Display name",
  "emailLabel": "Email",
  "save": "Save",
  "saving": "Saving…",
  "saved": "Profile updated",
  "saveError": "Failed to save",
  "changePassword": "Change password",
  "currentPassword": "Current password",
  "newPassword": "New password",
  "confirmPassword": "Confirm password",
  "passwordMismatch": "Passwords don't match",
  "incorrectPassword": "Current password is incorrect",
  "passwordChangeError": "Failed to change password",
  "passwordChanged": "Password updated",
  "updatePassword": "Update password"
}
```

Add Chinese counterparts.

- [ ] **Step 6: Build and verify**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/frontend
pnpm --filter @cubebox/core build
pnpm --filter @cubebox/web typecheck
```

- [ ] **Step 7: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add frontend/
git commit -m "feat(ui): user profile page with display name and password change"
```

---

### Task 12: Organization Settings — Backend

**Files:**
- Modify: `backend/cubebox/api/routes/v1/admin.py` — add PATCH /admin/org

- [ ] **Step 1: Add PATCH /admin/org endpoint**

In `backend/cubebox/api/routes/v1/admin.py`, add:

```python
import re

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


class OrgUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=255)
    slug: str | None = Field(None, min_length=3, max_length=32)


@router.patch("/org")
async def update_org(
    body: Annotated[OrgUpdate, Body()],
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> dict[str, str]:
    if body.name is None and body.slug is None:
        raise HTTPException(status_code=400, detail="at least one field required")
    if body.slug is not None and not _SLUG_RE.match(body.slug):
        raise HTTPException(status_code=400, detail="slug_invalid_format")

    from cubebox.auth.dependencies import resolve_current_org_id
    from cubebox.repositories import OrganizationRepository

    org_id = await resolve_current_org_id(user, session)
    org_repo = OrganizationRepository(session)
    org = await org_repo.get(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="org not found")

    if body.slug is not None and body.slug != org.slug:
        from sqlalchemy import select
        from cubebox.models import Organization

        existing = await session.execute(
            select(Organization).where(Organization.slug == body.slug)  # type: ignore[arg-type]
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="slug_taken")
        org.slug = body.slug

    if body.name is not None:
        org.name = body.name

    session.add(org)
    await session.commit()
    await session.refresh(org)

    from cubebox.plugins.audit import audit_log

    await audit_log(
        action="org.updated",
        user_id=user.id,
        org_id=org_id,
        ip=request.client.host if request.client else None,
    )
    return {"id": org.id, "name": org.name, "slug": org.slug}
```

Add necessary imports at the top of the file: `re`, `Field`, `Request` from existing imports; add `Body` if missing.

- [ ] **Step 2: Run mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend
uv run mypy cubebox/api/routes/v1/admin.py
```

- [ ] **Step 3: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add backend/cubebox/api/routes/v1/admin.py
git commit -m "feat: add PATCH /admin/org endpoint for org name/slug editing"
```

---

### Task 13: Organization Settings — Frontend

**Files:**
- Create: `frontend/packages/web/components/admin/settings/OrgInfoCard.tsx`
- Modify: `frontend/packages/web/app/admin/settings/page.tsx`
- Modify: `frontend/packages/web/messages/en.json` / `zh.json`

- [ ] **Step 1: Create OrgInfoCard**

Create `frontend/packages/web/components/admin/settings/OrgInfoCard.tsx`:

```tsx
'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { createApiClient, toApiError } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { useAdminAccess } from '@/hooks/useAdminAccess'

export function OrgInfoCard() {
  const t = useTranslations('adminSettings.orgInfo')
  const { orgName } = useAdminAccess()
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [origName, setOrigName] = useState('')
  const [origSlug, setOrigSlug] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const client = createApiClient('')
    client.get('/api/v1/admin/org').then(async (res) => {
      if (res.ok) {
        const data = (await res.json()) as { id: string; name: string; slug: string }
        setName(data.name)
        setSlug(data.slug)
        setOrigName(data.name)
        setOrigSlug(data.slug)
      }
    }).catch(() => {
      setName(orgName)
      setOrigName(orgName)
    })
  }, [orgName])

  const dirty = name !== origName || slug !== origSlug

  const onSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const client = createApiClient('')
      const patch: Record<string, string> = {}
      if (name !== origName) patch.name = name
      if (slug !== origSlug) patch.slug = slug
      const res = await client.patch('/api/v1/admin/org', patch)
      if (!res.ok) {
        const err = await toApiError(res)
        throw err
      }
      const data = (await res.json()) as { id: string; name: string; slug: string }
      setOrigName(data.name)
      setOrigSlug(data.slug)
      toast.success(t('saved'))
    } catch (err) {
      const detail = (err as { detail?: string }).detail
      setError(detail === 'slug_taken' ? t('slugTaken') : t('saveError'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="rounded-lg border border-border bg-card">
      <div className="px-4 py-3 border-b border-border">
        <h2 className="text-sm font-medium">{t('title')}</h2>
      </div>
      <div className="p-4 space-y-3">
        <label className="block">
          <span className="text-sm text-muted-foreground">{t('nameLabel')}</span>
          <input
            type="text"
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="text-sm text-muted-foreground">{t('slugLabel')}</span>
          <input
            type="text"
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-mono"
            value={slug}
            onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
          />
          <span className="text-xs text-muted-foreground">{t('slugHelp')}</span>
        </label>
        {error && <div className="text-sm text-destructive">{error}</div>}
        {dirty && (
          <Button onClick={onSave} disabled={saving}>
            {saving ? t('saving') : t('save')}
          </Button>
        )}
      </div>
    </section>
  )
}
```

Note: We also need a GET /admin/org endpoint. Since admin.py's `get_admin_me` already returns `org_name`, we can either add a dedicated GET or reuse the PATCH response. For simplicity, the OrgInfoCard fetches via GET /admin/org. Add a simple GET route to `admin.py`:

```python
@router.get("/org")
async def get_org(
    user: Annotated[User, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    from cubebox.auth.dependencies import resolve_current_org_id
    from cubebox.repositories import OrganizationRepository

    org_id = await resolve_current_org_id(user, session)
    org = await OrganizationRepository(session).get(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="org not found")
    return {"id": org.id, "name": org.name, "slug": org.slug}
```

- [ ] **Step 2: Add OrgInfoCard to admin settings page**

In `frontend/packages/web/app/admin/settings/page.tsx`, import and render:

```tsx
import { OrgInfoCard } from '@/components/admin/settings/OrgInfoCard'

// Inside the flex column, before OrgLLMSettingsCard:
<OrgInfoCard />
```

- [ ] **Step 3: Add i18n keys**

In `en.json` under `adminSettings`, add:

```json
"orgInfo": {
  "title": "Organization",
  "nameLabel": "Organization name",
  "slugLabel": "Organization slug",
  "slugHelp": "Lowercase letters, numbers, and hyphens only.",
  "save": "Save",
  "saving": "Saving…",
  "saved": "Organization updated",
  "slugTaken": "That slug is already in use",
  "saveError": "Failed to save"
}
```

- [ ] **Step 4: Build and verify**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/frontend
pnpm --filter @cubebox/core build
pnpm --filter @cubebox/web typecheck
```

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add frontend/ backend/cubebox/api/routes/v1/admin.py
git commit -m "feat: org settings card in admin panel (name + slug editing)"
```

---

### Task 14: Email Verification — Backend

**Files:**
- Modify: `backend/cubebox/api/routes/v1/auth.py` — register verify router, add is_verified to /me
- Modify: `backend/cubebox/auth/users.py` — add on_after_request_verify hook

- [ ] **Step 1: Register verify router**

In `backend/cubebox/api/routes/v1/auth.py`, at the bottom:

```python
router.include_router(fastapi_users.get_verify_router(UserRead), prefix="")
```

This mounts `POST /auth/request-verify-token` and `POST /auth/verify`.

- [ ] **Step 2: Add is_verified to GET /auth/me**

In the `me()` function's return dict, add:

```python
"is_verified": user.is_verified,
```

- [ ] **Step 3: Implement on_after_request_verify hook**

In `backend/cubebox/auth/users.py`, add to `UserManager`:

```python
async def on_after_request_verify(
    self, user: User, token: str, request: Request | None = None
) -> None:
    from cubebox.services.email import get_email_service

    base_url = config.get("app.base_url", "http://localhost:3000")
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
```

- [ ] **Step 4: Run mypy**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend
uv run mypy cubebox/api/routes/v1/auth.py cubebox/auth/users.py
```

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add backend/cubebox/api/routes/v1/auth.py backend/cubebox/auth/users.py
git commit -m "feat: enable email verification via fastapi-users router + email hook"
```

---

## Phase 5 — Lifecycle

### Task 15: Leave Workspace — Backend + Frontend

**Files:**
- Modify: `backend/cubebox/api/routes/v1/workspaces.py` — add POST /workspaces/{id}/leave
- Modify: `frontend/packages/core/src/api/workspaces.ts` — add leaveWorkspace
- Modify: `frontend/packages/web/components/workspace-settings/members/WsMembersTable.tsx` — add leave button
- Modify: `frontend/packages/web/messages/en.json` / `zh.json`

- [ ] **Step 1: Add leave endpoint (backend)**

In `backend/cubebox/api/routes/v1/workspaces.py`, add:

```python
@router.post("/{workspace_id}/leave")
async def leave_workspace(
    workspace_id: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> dict[str, bool]:
    mem_repo = MembershipRepository(session)
    role = await mem_repo.get_role(user_id=user.id, workspace_id=workspace_id)
    if role is None:
        raise HTTPException(status_code=404, detail="not a member")

    if role == Role.ADMIN:
        members = await mem_repo.list_workspace_members(workspace_id)
        admin_count = sum(1 for m in members if m.role == Role.ADMIN.value)
        if admin_count <= 1:
            raise HTTPException(
                status_code=400,
                detail="cannot_leave_as_last_admin",
            )

    from sqlalchemy import delete as sa_delete
    from cubebox.models import Membership

    await session.execute(
        sa_delete(Membership).where(
            Membership.user_id == user.id,  # type: ignore[arg-type]
            Membership.workspace_id == workspace_id,  # type: ignore[arg-type]
        )
    )
    await session.commit()

    from cubebox.plugins.audit import audit_log

    await audit_log(
        action="workspace.member_left",
        user_id=user.id,
        workspace_id=workspace_id,
        ip=request.client.host if request.client else None,
    )
    return {"left": True}
```

- [ ] **Step 2: Add leaveWorkspace frontend API**

In `frontend/packages/core/src/api/workspaces.ts`, add:

```typescript
export async function leaveWorkspace(client: ApiClient, wsId: string): Promise<void> {
  const res = await client.post(`/api/v1/workspaces/${wsId}/leave`, {})
  if (!res.ok) throw await toApiError(res)
}
```

- [ ] **Step 3: Add leave button in WsMembersTable**

In `WsMembersTable.tsx`, for the current user's row, add a "Leave" button instead of the remove button. When clicked, show a confirmation dialog, then call `leaveWorkspace`, remove the workspace from store, and redirect.

- [ ] **Step 4: Add i18n keys**

Under `"wsMembers"`:

```json
"leave": "Leave workspace",
"leaveConfirm": "Leave {workspace}? You'll lose access to all conversations and files.",
"leaveConfirmButton": "Leave"
```

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add backend/ frontend/
git commit -m "feat: leave workspace endpoint and UI"
```

---

### Task 16: Workspace Archive & Delete — Backend

**Files:**
- Modify: `backend/cubebox/models/workspace.py` — add archived_at
- Modify: `backend/cubebox/api/routes/v1/workspaces.py` — add archive/unarchive/delete, filter archived
- Alembic migration

- [ ] **Step 1: Add archived_at to Workspace model**

In `backend/cubebox/models/workspace.py`, add:

```python
from datetime import datetime
from sqlalchemy import Column, DateTime

archived_at: datetime | None = Field(
    default=None,
    sa_column=Column(DateTime(timezone=True), nullable=True),
)
```

- [ ] **Step 2: Generate migration**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend
uv run alembic revision --autogenerate -m "add archived_at to workspaces"
uv run alembic upgrade head
```

- [ ] **Step 3: Filter archived from workspace list**

In `list_my_workspaces()`, after fetching pairs, filter out archived:

```python
    pairs = [(role, ws) for role, ws in pairs if ws.archived_at is None]
```

Add query param support: check for `include_archived` in request query.

- [ ] **Step 4: Add archive/unarchive/delete endpoints**

```python
@router.post("/{workspace_id}/archive")
async def archive_workspace(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str | None]:
    from datetime import UTC, datetime

    ws = await WorkspaceRepository(session).get(workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="not found")
    ws.archived_at = datetime.now(UTC)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return {"id": ws.id, "name": ws.name, "archived_at": utc_isoformat(ws.archived_at)}


@router.post("/{workspace_id}/unarchive")
async def unarchive_workspace(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str | None]:
    ws = await WorkspaceRepository(session).get(workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="not found")
    ws.archived_at = None
    session.add(ws)
    await session.commit()
    return {"id": ws.id, "name": ws.name, "archived_at": None}


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    mem_repo = MembershipRepository(session)
    user_workspaces = await mem_repo.list_user_workspaces(ctx.user.id)
    if len(user_workspaces) <= 1:
        raise HTTPException(status_code=400, detail="cannot_delete_last_workspace")

    from sqlalchemy import delete as sa_delete

    await session.execute(
        sa_delete(Workspace).where(Workspace.id == workspace_id)  # type: ignore[arg-type]
    )
    await session.commit()
```

- [ ] **Step 5: Run mypy, commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend
uv run mypy cubebox/
```

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add backend/
git commit -m "feat: workspace archive/unarchive/delete with soft-delete column"
```

---

### Task 17: Workspace Archive & Delete — Frontend

**Files:**
- Modify: `frontend/packages/core/src/api/workspaces.ts` — add archive/unarchive/delete
- Modify: `frontend/packages/web/app/(app)/w/[wsId]/settings/page.tsx` — add danger zone
- Modify: `frontend/packages/web/messages/en.json` / `zh.json`

- [ ] **Step 1: Add API functions**

In `frontend/packages/core/src/api/workspaces.ts`:

```typescript
export async function archiveWorkspace(client: ApiClient, wsId: string): Promise<void> {
  const res = await client.post(`/api/v1/workspaces/${wsId}/archive`, {})
  if (!res.ok) throw await toApiError(res)
}

export async function unarchiveWorkspace(client: ApiClient, wsId: string): Promise<void> {
  const res = await client.post(`/api/v1/workspaces/${wsId}/unarchive`, {})
  if (!res.ok) throw await toApiError(res)
}

export async function deleteWorkspace(client: ApiClient, wsId: string): Promise<void> {
  const res = await client.del(`/api/v1/workspaces/${wsId}`)
  if (!res.ok) throw await toApiError(res)
}
```

- [ ] **Step 2: Add danger zone to workspace settings**

In the workspace settings page, add a new tab or section for workspace management (archive/delete). Use the `DangerZone` component. The user types the workspace name to confirm deletion.

- [ ] **Step 3: Add i18n keys, build, commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add frontend/
git commit -m "feat(ui): workspace archive and delete in settings danger zone"
```

---

### Task 18: Account Deletion — Backend + Frontend

**Files:**
- Modify: `backend/cubebox/api/routes/v1/auth.py` — add POST /auth/delete-account
- Create: `frontend/packages/web/components/profile/DeleteAccountDialog.tsx`
- Modify: `frontend/packages/web/app/(app)/settings/profile/page.tsx` — add danger zone
- Modify: `frontend/packages/web/messages/en.json` / `zh.json`

- [ ] **Step 1: Add delete-account endpoint (backend)**

In `backend/cubebox/api/routes/v1/auth.py`:

```python
class DeleteAccountRequest(BaseModel):
    password: str


@router.post("/delete-account")
async def delete_account(
    body: Annotated[DeleteAccountRequest, Body()],
    user: Annotated[User, Depends(current_active_user)],
    user_manager: Annotated[UserManager, Depends(get_user_manager)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> Response:
    verified, _ = user_manager.password_helper.verify_and_update(
        body.password, user.hashed_password
    )
    if not verified:
        raise HTTPException(status_code=400, detail="incorrect_password")

    from cubebox.models import OrgRole, OrganizationMembership
    from sqlalchemy import select

    owner_rows = (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user.id,  # type: ignore[arg-type]
                OrganizationMembership.role == OrgRole.OWNER.value,  # type: ignore[arg-type]
            )
        )
    ).scalars().all()
    if owner_rows:
        raise HTTPException(status_code=400, detail="transfer_ownership_first")

    from cubebox.plugins.audit import audit_log

    await audit_log(
        action="auth.account_deleted",
        user_id=user.id,
        ip=request.client.host if request.client else None,
    )

    from sqlalchemy import delete as sa_delete
    from cubebox.models import Membership, OrganizationMembership as OM

    await session.execute(
        sa_delete(Membership).where(Membership.user_id == user.id)  # type: ignore[arg-type]
    )
    await session.execute(
        sa_delete(OM).where(OM.user_id == user.id)  # type: ignore[arg-type]
    )
    from cubebox.models import User as UserModel
    await session.execute(
        sa_delete(UserModel).where(UserModel.id == user.id)  # type: ignore[arg-type]
    )
    await session.commit()

    response = Response(
        content='{"deleted": true}',
        media_type="application/json",
    )
    response.delete_cookie("cubebox_auth")
    return response
```

- [ ] **Step 2: Create DeleteAccountDialog (frontend)**

Create `frontend/packages/web/components/profile/DeleteAccountDialog.tsx`:

```tsx
'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { createApiClient, toApiError, useAuthStore } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import * as DialogPrimitive from '@base-ui-components/react/dialog'

export function DeleteAccountDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const t = useTranslations('profile.deleteAccount')
  const router = useRouter()
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const onDelete = async () => {
    setDeleting(true)
    setError(null)
    try {
      const client = createApiClient('')
      const res = await client.post('/api/v1/auth/delete-account', { password })
      if (!res.ok) {
        const err = await toApiError(res)
        throw err
      }
      useAuthStore.getState().reset()
      router.replace('/login')
    } catch (err) {
      const detail = (err as { detail?: string }).detail
      setError(detail === 'incorrect_password' ? t('wrongPassword') : detail === 'transfer_ownership_first' ? t('transferFirst') : t('error'))
    } finally {
      setDeleting(false)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm data-[ending-style]:opacity-0 transition-opacity duration-200" />
      <DialogPrimitive.Popup className="fixed left-1/2 top-1/2 z-50 w-[min(420px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border bg-popover p-5 shadow-xl data-[ending-style]:opacity-0 transition-opacity duration-200">
        <h2 className="text-base font-semibold text-destructive mb-2">{t('title')}</h2>
        <p className="text-sm text-muted-foreground mb-4">{t('warning')}</p>
        <input
          type="password"
          placeholder={t('passwordPlaceholder')}
          autoComplete="current-password"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm mb-3"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <div className="text-sm text-destructive mb-3">{error}</div>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('cancel')}</Button>
          <Button variant="destructive" onClick={onDelete} disabled={deleting || !password}>
            {deleting ? t('deleting') : t('confirm')}
          </Button>
        </div>
      </DialogPrimitive.Popup>
    </DialogPrimitive.Root>
  )
}
```

- [ ] **Step 3: Add danger zone to profile page**

In `frontend/packages/web/app/(app)/settings/profile/page.tsx`, add after ChangePasswordForm:

```tsx
import { DangerZone } from '@/components/management/DangerZone'
import { DeleteAccountDialog } from '@/components/profile/DeleteAccountDialog'

// Inside the component, add state:
const [deleteOpen, setDeleteOpen] = useState(false)

// After the <ChangePasswordForm />:
<DangerZone title={t('dangerZone')}>
  <div className="flex items-center justify-between">
    <div>
      <p className="text-sm font-medium">{t('deleteAccountTitle')}</p>
      <p className="text-xs text-muted-foreground">{t('deleteAccountDesc')}</p>
    </div>
    <Button variant="destructive" size="sm" onClick={() => setDeleteOpen(true)}>
      {t('deleteAccount')}
    </Button>
  </div>
  <DeleteAccountDialog open={deleteOpen} onOpenChange={setDeleteOpen} />
</DangerZone>
```

- [ ] **Step 4: Add i18n keys**

In `en.json` under `"profile"`, add:

```json
"dangerZone": "Danger zone",
"deleteAccountTitle": "Delete account",
"deleteAccountDesc": "Permanently delete your account and all your data.",
"deleteAccount": "Delete account",
"deleteAccount": {
  "title": "Delete your account",
  "warning": "This action cannot be undone. All your data will be permanently deleted.",
  "passwordPlaceholder": "Enter your password to confirm",
  "cancel": "Cancel",
  "confirm": "Delete my account",
  "deleting": "Deleting…",
  "wrongPassword": "Incorrect password",
  "transferFirst": "Transfer org ownership before deleting your account",
  "error": "Failed to delete account"
}
```

- [ ] **Step 5: Build, verify, commit**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/frontend
pnpm --filter @cubebox/core build
pnpm --filter @cubebox/web typecheck
```

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness
git add backend/ frontend/
git commit -m "feat: account deletion endpoint, dialog, and profile danger zone"
```

---

## Final: Full Suite Verification

- [ ] **Run full backend tests**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend
uv run pytest tests/ -x -q
```

- [ ] **Run full frontend typecheck**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/frontend
pnpm --filter @cubebox/core build
pnpm --filter @cubebox/web typecheck
```

- [ ] **Run frontend linting**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/frontend
pnpm --filter @cubebox/web lint
```

- [ ] **Manual verification: start backend and frontend**

```bash
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/backend
python main.py &
cd /home/chris/cubebox/.worktrees/feat/user-identity-completeness/frontend
pnpm dev &
```

Verify at `http://localhost:3065`:
1. Register with display name → name shows in sidebar
2. Login → "Forgot password?" link visible
3. Workspace settings → rename works
4. Workspace settings → members tab → invite section visible, create invite link works
5. Open invite link in incognito → accept page works
6. Profile settings → change password works
7. Admin panel → settings → org name/slug editable
8. Workspace settings → danger zone → archive/delete works
9. Members table → leave workspace works
10. Profile → delete account works
