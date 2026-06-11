# User Identity Completeness — Design

- **Status**: Draft
- **Date**: 2026-06-11
- **Scope**: P0 + P1 gaps from the user-system audit
- **Depends on**: M9 single-tenant UX, member management (both shipped)

---

## Problem

The identity system has functional bones (register, login, org/workspace
scoping, membership CRUD) but the user journey has critical holes:

- Users have no name or face — every UI shows raw email addresses.
- Password lifecycle is missing — no change, no forgot, no recovery.
- The invite system has backend API but zero frontend — teams cannot grow
  through the UI.
- Workspaces are immutable after creation — no rename, no delete.
- Organization settings are invisible — admins cannot edit org name or slug.
- Users cannot leave workspaces or delete their accounts.

This spec covers P0 (core usability) and P1 (experience completeness) in a
single design. Each feature is independently implementable and deployable.

---

## Feature Index

| # | Feature | Priority | Backend | Frontend |
|---|---------|----------|---------|----------|
| F1 | User display name | P0 | model + migration + API | profile, member lists |
| F2 | Invite system frontend | P0 | — (API exists) | create link, accept page, management |
| F3 | Forgot password | P0 | email infra + fastapi-users router | forgot + reset pages |
| F4 | Change password | P0 | new endpoint | profile settings |
| F5 | Workspace rename | P0 | PATCH endpoint | settings inline edit |
| F6 | Email verification | P1 | fastapi-users router + hook | banner + resend |
| F7 | User profile page | P1 | — (consumes F1/F4) | new route |
| F8 | Organization settings | P1 | PATCH endpoint | admin settings tab |
| F9 | Workspace archive/delete | P1 | DELETE endpoint + soft-delete | UI + confirmation |
| F10 | Invite email notification | P1 | email template | — (backend sends) |
| F11 | Leave workspace | P1 | new endpoint | member list self-action |
| F12 | Account deletion | P1 | DELETE endpoint + cascade | profile danger zone |

---

## Shared Infrastructure: Email Sending

F3, F6, and F10 all need outbound email. Introduce a thin email service
before implementing any of them.

### Backend

New module `backend/cubebox/services/email.py`:

```
class EmailService:
    async def send(to: str, subject: str, html: str, text: str) -> None
```

Pluggable backend via config `email.backend`:

| Value | Behavior |
|---|---|
| `smtp` | aiosmtplib; configured via `email.smtp_host`, `email.smtp_port`, `email.smtp_user`, `email.smtp_password`, `email.smtp_tls` |
| `log` (default) | Logs the email body to stdout; for dev and single-tenant deployments that don't need email |
| `resend` | Resend API; configured via `email.resend_api_key` |

`email.from_address` — defaults to `noreply@cubebox.local`.

Templates live in `backend/cubebox/templates/email/` as plain Jinja2
(`.html` + `.txt` pairs). Initial templates:

- `password_reset.{html,txt}` — reset link with token
- `email_verification.{html,txt}` — verify link with token
- `workspace_invite.{html,txt}` — invite link with workspace name and inviter

### Dependency

```toml
aiosmtplib = ">=3.0"
jinja2  # already a dependency via FastAPI
```

### Config example

```yaml
email:
  backend: smtp          # or "log" or "resend"
  from_address: noreply@example.com
  smtp_host: smtp.example.com
  smtp_port: 587
  smtp_tls: true
  smtp_user: apikey
  smtp_password: ${CUBEBOX_EMAIL_SMTP_PASSWORD}
```

---

## F1 · User Display Name

### Goal

Every identity touchpoint shows a human-readable name instead of an email
address.

### Data Model

Add to `User`:

```python
display_name: str | None = Field(default=None, max_length=100)
```

Migration: `ALTER TABLE users ADD COLUMN display_name VARCHAR(100)`.
Nullable — existing users keep `NULL`; UI falls back to email local-part.

### API

**PATCH /api/v1/auth/me** — extend the existing endpoint:

```python
class UserProfileUpdate(BaseModel):
    language: Literal["en", "zh"] | None = None
    display_name: str | None = Field(None, min_length=1, max_length=100)
```

At least one field must be non-null (400 otherwise).

**GET /api/v1/auth/me** — add `display_name` to response.

**Member list endpoints** — add `display_name` to `WsMemberOut` and
`OrgMemberOut`. These join against the `users` table already; add the column
to the SELECT.

### Frontend

- `MeResult` type adds `display_name: string | null`.
- Display logic everywhere: `display_name ?? email.split('@')[0]`.
- `AvatarPopover`, `AdminAvatarMenu` — show display name above email.
- `WsMembersTable`, `OrgMembersTable` — primary column becomes display
  name; email shown as secondary text.
- `RegisterForm` — add optional "Your name" field.

Register API change: extend `POST /auth/register` to accept optional
`display_name` in body. `UserCreate` schema inherits from fastapi-users
`BaseUserCreate` — add the field via a mixin or override.

---

## F2 · Invite System Frontend

### Goal

Workspace admins can create invite links in the UI; invitees can accept
them via a dedicated page. Admins can view and revoke pending invites.

### Backend Changes

The core invite API already exists (`POST /workspaces/{ws}/invites` and
`POST /workspaces/invites/accept`). Three additions:

**1. List pending invites**

```
GET /api/v1/workspaces/{workspace_id}/invites
→ [{token, role, created_by, expires_at, used_at}]
```

Gated by `require_admin`. Returns all tokens for this workspace (used and
unused) sorted by `expires_at` desc. Frontend filters to show
pending (unused + not expired) vs. used vs. expired.

**2. Revoke invite**

```
DELETE /api/v1/workspaces/{workspace_id}/invites/{token}
→ 204
```

Gated by `require_admin`. Hard-deletes the token row. Idempotent (204 even
if already gone).

**3. Accept invite returns more context**

Extend `POST /workspaces/invites/accept` response:

```json
{
  "workspace_id": "ws_xxx",
  "workspace_name": "Engineering",
  "org_id": "org_xxx",
  "role": "member"
}
```

Currently returns `workspace_id` and `role` only. Add `workspace_name` and
`org_id` by joining workspaces table in the consume path.

Also: accept must handle the case where the user is not yet an org member
(single_tenant subsequent-user scenario). If the user is authenticated but
not an org member, auto-add them as `OrgRole.MEMBER` before granting
workspace membership.

### Frontend

**Invite creation dialog** — new component in workspace settings members
tab:

- "Create invite link" button at top of members panel.
- Dialog: role selector (admin / member), "Create" button.
- On success: show the full invite URL with copy-to-clipboard.
- URL format: `{origin}/invite/accept?token={token}`.

**Invite list** — collapsible section below the members table:

- Table columns: role, created by (display_name or email), expires, status
  (pending/used/expired), revoke button.
- Revoke shows inline confirmation.

**Accept invite page** — new route `app/(auth)/invite/accept/page.tsx`:

- Reads `?token=` from URL search params.
- If user is not authenticated → redirect to `/login?next=/invite/accept?token=xxx`.
- If authenticated → calls `POST /workspaces/invites/accept`.
- On success → shows "You've joined {workspace_name}" with a button to
  open the workspace.
- On error (expired, already used, invalid) → shows appropriate error
  message.

**API client** — add to `@cubebox/core`:

```typescript
listInvites(client, wsId): Promise<InviteToken[]>
revokeInvite(client, wsId, token): Promise<void>
acceptInvite(client, token): Promise<AcceptInviteResult>
```

---

## F3 · Forgot Password

### Goal

Users who forget their password can reset it via email without admin
intervention.

### Backend

fastapi-users already provides `get_reset_password_router()` which exposes:

- `POST /forgot-password` — accepts `{email}`, generates a token, calls
  `UserManager.on_after_forgot_password(user, token, request)`.
- `POST /reset-password` — accepts `{token, password}`, validates token,
  updates password.

Currently the router is not registered. The `UserManager` already has
`reset_password_token_secret` configured.

**Steps:**

1. Register the router:
   ```python
   router.include_router(
       fastapi_users.get_reset_password_router(),
       prefix="",
   )
   ```
   This mounts `POST /auth/forgot-password` and `POST /auth/reset-password`.

2. Implement the hook in `UserManager`:
   ```python
   async def on_after_forgot_password(
       self, user: User, token: str, request: Request | None = None,
   ) -> None:
       reset_url = f"{config.get('app.base_url')}/reset-password?token={token}"
       await email_service.send(
           to=user.email,
           subject="Reset your password",
           template="password_reset",
           context={"reset_url": reset_url, "email": user.email},
       )
   ```

3. Rate-limit both endpoints (same `LOGIN_LIMIT`).

### Frontend

**Forgot password page** — `app/(auth)/forgot-password/page.tsx`:

- Email input field.
- "Send reset link" button.
- On submit → `POST /auth/forgot-password`.
- Success state: "If an account exists for that email, we've sent a reset
  link." (Intentionally vague to avoid email enumeration.)
- Link from login page: "Forgot password?" below the login button.

**Reset password page** — `app/(auth)/reset-password/page.tsx`:

- Reads `?token=` from URL.
- Two password fields (new + confirm).
- On submit → `POST /auth/reset-password`.
- Success → "Password updated. Redirecting to login..." (auto-redirect
  after 3s).
- Token invalid/expired → "This reset link has expired. Request a new one."
  with link back to forgot-password.

---

## F4 · Change Password

### Goal

Authenticated users can change their password from within the app.

### Backend

New endpoint:

```
POST /api/v1/auth/change-password
Body: { current_password: str, new_password: str }
→ 200 {}
```

- Requires `current_active_user`.
- Verifies `current_password` against stored hash (use
  `user_manager.validate_password` + `password_helper.verify_and_update`).
- Wrong current password → 400 `incorrect_password`.
- New password validation failure → 400 `invalid_password`.
- On success: update hash, commit. Optionally invalidate other sessions
  (out of scope for now — JWT is stateless).
- Audit log: `auth.password_changed`.

### Frontend

Rendered inside the profile page (F7). Standalone form with three fields:

- Current password
- New password
- Confirm new password

Client-side: confirm must match new. On success: toast "Password updated".

API client: `changePassword(client, currentPassword, newPassword)`.

---

## F5 · Workspace Rename

### Goal

Workspace admins can rename their workspace.

### Backend

New endpoint:

```
PATCH /api/v1/workspaces/{workspace_id}
Body: { name: str (1..255) }
→ { id, name, org_id }
```

- Gated by `require_admin` (workspace admin).
- Validates `name` length.
- Updates `workspace.name`, commits.
- Audit log: `workspace.renamed`.

### Frontend

In workspace settings (first tab "Workspace"):

- Workspace name shown as editable text field.
- "Save" button appears when name differs from current.
- On success: update `workspaceStore`, update sidebar, toast.

API client: `renameWorkspace(client, wsId, name)`.

Sidebar `WorkspacesSection` reads from store — rename propagates
automatically.

---

## F6 · Email Verification

### Goal

New users verify their email address. Unverified users can still use the
app but see a persistent banner prompting verification.

### Backend

fastapi-users provides `get_verify_router()`:

- `POST /request-verify-token` — accepts `{email}`, calls
  `UserManager.on_after_request_verify(user, token, request)`.
- `POST /verify` — accepts `{token}`, sets `is_verified = True`.

**Steps:**

1. Register the router:
   ```python
   router.include_router(
       fastapi_users.get_verify_router(UserRead),
       prefix="",
   )
   ```

2. Implement the hook:
   ```python
   async def on_after_request_verify(
       self, user: User, token: str, request: Request | None = None,
   ) -> None:
       verify_url = f"{config.get('app.base_url')}/verify-email?token={token}"
       await email_service.send(
           to=user.email,
           subject="Verify your email",
           template="email_verification",
           context={"verify_url": verify_url},
       )
   ```

3. Auto-trigger on registration: call `request_verify` in
   `on_after_register` (after bootstrap completes).

4. `GET /auth/me` — add `is_verified` to response.

### Behavior: soft enforcement

Unverified users are NOT blocked. Rationale: single-tenant deployments
often run without email; blocking would lock out users.

Config: `auth.require_email_verification` (default `false`). When `true`,
unverified users get 403 on business routes (except `/auth/*` and
`/system/*`). When `false`, verification is purely cosmetic.

### Frontend

**Verification banner** — persistent top bar when `!user.is_verified`:

> "Please verify your email address. [Resend verification email]"

Clicking "Resend" calls `POST /auth/request-verify-token`.

**Verify email page** — `app/(auth)/verify-email/page.tsx`:

- Reads `?token=` from URL.
- Auto-submits `POST /auth/verify` on mount.
- Success → "Email verified!" with redirect to app.
- Error → "Invalid or expired link. [Resend]".

---

## F7 · User Profile Page

### Goal

A single place for users to manage their identity: name, avatar, language,
password.

### Frontend

New route: `app/(app)/settings/profile/page.tsx`.

Entry point: AvatarPopover → "Profile settings" link.

Sections:

1. **Personal info** — display name (F1), email (read-only), language
   selector. Save button.
2. **Change password** (F4) — current / new / confirm fields.
3. **Danger zone** (F12) — "Delete my account" button.

No new backend work — this page consumes F1, F4, F12 endpoints.

### Navigation

- `AvatarPopover` adds "Profile settings" link above "Admin".
- Route sits outside workspace scope (no `[wsId]` in path) since it's
  user-global.

---

## F8 · Organization Settings

### Goal

Org admins can view and edit their organization's name and slug.

### Backend

New endpoint:

```
PATCH /api/v1/admin/org
Body: { name?: str (2..255), slug?: str (3..32, same regex as setup) }
→ { id, name, slug }
```

- Gated by `require_org_admin`.
- At least one field must be provided (400 otherwise).
- Slug change validates uniqueness (409 `slug_taken`).
- Audit log: `org.updated`.

### Frontend

Admin panel → new "Settings" tab (first item in `AdminSubNav`, currently
exists but is empty or placeholder).

Content:

- Org name — text input.
- Org slug — text input with format validation (same as setup page).
- Save button.

---

## F9 · Workspace Archive & Delete

### Goal

Workspace admins can archive (hide) or permanently delete a workspace.

### Design Decision: Soft Delete

Introduce `archived_at: datetime | None` on `Workspace`. Archiving is
reversible; deletion is permanent.

### Backend

**Archive:**

```
POST /api/v1/workspaces/{workspace_id}/archive
→ 200 { id, name, archived_at }
```

**Unarchive:**

```
POST /api/v1/workspaces/{workspace_id}/unarchive
→ 200 { id, name, archived_at: null }
```

**Delete (permanent):**

```
DELETE /api/v1/workspaces/{workspace_id}
→ 204
```

All gated by `require_admin`.

Delete cascades: memberships, conversations, messages, artifacts,
agent configs, MCP enrollments, skill installs, scheduled tasks, triggers
for this workspace. Use DB-level `ON DELETE CASCADE` where FK exists;
explicit cleanup for tables without direct FK.

Guard: cannot delete the user's last workspace (400
`cannot_delete_last_workspace`). The "Personal" workspace created at
registration is not special — it can be deleted if other workspaces exist.

### Data Model

```python
archived_at: datetime | None = Field(
    default=None,
    sa_column=Column(DateTime(timezone=True), nullable=True),
)
```

Migration adds the column. `GET /workspaces` filters out
`archived_at IS NOT NULL` by default; add `?include_archived=true` query
param to show them.

### Frontend

Workspace settings → "Danger zone" section:

- "Archive workspace" toggle (or button). Archived workspaces disappear
  from sidebar but can be found via "Show archived" toggle on the
  workspaces list page.
- "Delete workspace" — requires typing workspace name to confirm.
  Permanently destroys all data.

---

## F10 · Invite Email Notification

### Goal

When a workspace admin creates an invite, the system can optionally send an
email to the intended recipient.

### Backend

Extend `POST /workspaces/{workspace_id}/invites`:

```python
class InviteCreate(BaseModel):
    role: Literal["admin", "member"]
    email: str | None = None  # NEW: optional recipient email
```

When `email` is provided:

1. Create the invite token (existing logic).
2. Build the accept URL: `{base_url}/invite/accept?token={token}`.
3. Send email via `EmailService` using `workspace_invite` template.
4. Response includes `email_sent: bool`.

When `email` is omitted: current behavior (return token, no email).

The email is NOT stored on `InviteToken` — it's fire-and-forget. The token
itself is not tied to a specific email; anyone with the link can accept.

### Frontend

Invite creation dialog (F2) gains an optional "Send to email" field. When
filled, the invite is created and an email is sent. When empty, only the
link is shown for manual sharing.

---

## F11 · Leave Workspace

### Goal

Non-admin members can remove themselves from a workspace. Admins can leave
if they're not the last admin.

### Backend

New endpoint:

```
POST /api/v1/workspaces/{workspace_id}/leave
→ 200 { left: true }
```

- Requires `current_active_user` + workspace membership (any role).
- If user is an admin and is the ONLY admin → 400
  `cannot_leave_as_last_admin` ("Transfer admin role to another member
  before leaving").
- Deletes the `Membership` row.
- Does NOT remove the user from the org.
- Audit log: `workspace.member_left`.

### Frontend

Two entry points:

1. **Workspace settings → members tab** — if the current user appears in
   the list, show a "Leave workspace" action next to their own row.
2. **Sidebar workspace context menu** — "Leave workspace" option.

Both show a confirmation dialog: "Leave {workspace_name}? You'll lose
access to conversations and files in this workspace."

On success: remove workspace from store, redirect to `/workspaces` if
it was the active workspace.

---

## F12 · Account Deletion

### Goal

Users can permanently delete their account and all associated data. GDPR
Article 17 compliance.

### Backend

New endpoint:

```
POST /api/v1/auth/delete-account
Body: { password: str }
→ 200 { deleted: true }
```

- Requires `current_active_user`.
- Verifies password (same as change-password).
- Guards:
  - User is an org OWNER → 400 `transfer_ownership_first` ("Transfer org
    ownership before deleting your account").
- Cascade (single transaction):
  1. Delete all `Membership` rows for this user.
  2. Delete all `OrganizationMembership` rows for this user.
  3. Delete the `User` row (this cascades to conversations authored by
     user, etc. via FK — verify FK cascade coverage).
- Log out: clear auth cookie in response.
- Audit log: `auth.account_deleted` (logged before deletion, with user
  context).

### Data handling

Conversations owned by the deleted user are deleted (cascade). Shared
workspace data (conversations started by others, artifacts) remains
intact — only the user's identity is anonymized in references (e.g.,
`created_by` becomes NULL or a sentinel `deleted_user`).

Decision: for v1, hard-delete the user row. Conversations table has
`user_id` FK with `ON DELETE CASCADE` — verify this covers all
business tables. Tables without FK to users (e.g., audit logs) retain
the `user_id` string as a tombstone reference.

### Frontend

Profile page (F7) → Danger zone section:

- "Delete account" button (red, destructive styling).
- Confirmation dialog: "This action cannot be undone. All your data will be
  permanently deleted. Type your password to confirm."
- Password field + "Delete my account" button.
- On success: redirect to `/login` with a toast "Account deleted".

---

## Implementation Sequence

Features have dependencies; this is the recommended build order:

```
Phase 1 — Foundation (no cross-deps):
  F1  User display name        ← standalone model + API + UI
  F5  Workspace rename          ← standalone endpoint + UI
  Email infrastructure          ← needed by F3, F6, F10

Phase 2 — Password lifecycle (depends on Email):
  F3  Forgot password           ← email infra + fastapi-users router
  F4  Change password           ← standalone endpoint

Phase 3 — Invite system (depends on F1 for display name in UI):
  F2  Invite frontend           ← backend list/revoke + full UI
  F10 Invite email (optional)   ← email infra + invite API extension

Phase 4 — Profile & settings (depends on F1, F4):
  F7  User profile page         ← assembles F1, F4, F12
  F8  Org settings              ← standalone admin endpoint + UI
  F6  Email verification        ← email infra + fastapi-users router

Phase 5 — Lifecycle (depends on F7):
  F11 Leave workspace           ← standalone endpoint + UI
  F9  Workspace archive/delete  ← model change + cascade + UI
  F12 Account deletion          ← endpoint + cascade + profile UI
```

Within each phase, features can be implemented in parallel by different
developers.

---

## Out of Scope

Explicitly deferred to future work:

- **User avatar / profile picture** — needs object storage integration;
  do after the file-upload system stabilizes.
- **OAuth / social login** — separate design; interacts with EE auth
  plugin system.
- **MFA / TOTP / WebAuthn** — security hardening milestone.
- **Session management** — view/revoke active sessions; needs token
  registry (JWT is stateless today).
- **Custom roles / fine-grained permissions** — enterprise feature.
- **Org deletion** — rarely needed; admin CLI is sufficient for now.
- **Onboarding tutorial / product tour** — UX project, not identity.
- **Activity feed / notifications** — separate system design.
- **Data export** — needs format decisions and scope definition.

---

## Migration Summary

| Feature | Migration | Reversible |
|---------|-----------|------------|
| F1 | `ADD COLUMN display_name VARCHAR(100)` to `users` | Yes (drop column) |
| F9 | `ADD COLUMN archived_at TIMESTAMPTZ` to `workspaces` | Yes (drop column) |

All other features add endpoints or frontend pages only — no schema
changes.
