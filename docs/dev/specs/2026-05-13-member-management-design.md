# Member Management Design

Org-level and workspace-level member management: API endpoints and frontend UI for
listing, adding, role-changing, and removing members at both scopes.

## Context

The identity model (Organization → Workspace → Membership → User) and both membership
tables (`organization_memberships`, `memberships`) already exist. Repositories with
`list_org_members`, `list_workspace_members`, `grant`, `promote`, `revoke` methods are
in place. What's missing is the HTTP API layer and the frontend UI.

The existing workspace invite flow (single-use token link) remains untouched. This
feature adds **direct-add** management for org admins and workspace admins.

## Backend API

### Org-level member routes

Mounted on the existing `/admin` router. All gated by `require_org_admin` dependency.
Org resolution uses `resolve_current_org_id` (same as `/admin/me`).

| Method | Path | Body / Params | Response | Notes |
|--------|------|---------------|----------|-------|
| GET | `/admin/members` | — | `[{user_id, email, role, created_at}]` | All org members |
| POST | `/admin/members` | `{email, role}` | `{user_id, email, role}` | Add existing user by email |
| PATCH | `/admin/members/{user_id}/role` | `{role}` | `{user_id, role}` | Change org role |
| DELETE | `/admin/members/{user_id}` | — | 204 | Remove from org + cascade |

**POST /admin/members** logic:
1. Look up user by email → 404 if not found.
2. Check not already an org member → 409 if duplicate.
3. `role` must be `admin` or `member` (not `owner`) → 400 otherwise.
4. Create `OrganizationMembership(user_id, org_id, role)`.

**PATCH /admin/members/{user_id}/role** logic:
1. Fetch existing org membership → 404 if not found.
2. Target is owner → 409 "Cannot change owner role".
3. `role` must be `admin` or `member` → 400 otherwise.
4. Update via `promote(user_id, org_id, role)`.

**DELETE /admin/members/{user_id}** logic:
1. Fetch existing org membership → 404 if not found.
2. Target is owner → 409 "Cannot remove org owner".
3. Target is the requesting user → 400 "Cannot remove yourself".
4. Delete all workspace memberships for this user in this org's workspaces (single transaction).
5. Delete the org membership.

### Workspace-level member routes

New route module `members.py` registered under the workspace-scoped router
(`/ws/{workspace_id}/members`). Gated by `require_admin` (workspace admin).

| Method | Path | Body / Params | Response | Notes |
|--------|------|---------------|----------|-------|
| GET | `/ws/{wsId}/members` | — | `[{user_id, email, role, created_at}]` | Workspace members |
| POST | `/ws/{wsId}/members` | `{user_id, role}` | `{user_id, email, role}` | Add org member to workspace |
| PATCH | `/ws/{wsId}/members/{user_id}/role` | `{role}` | `{user_id, role}` | Change workspace role |
| DELETE | `/ws/{wsId}/members/{user_id}` | — | 204 | Remove from workspace |

**POST /ws/{wsId}/members** logic:
1. Verify target user is an org member (same org as workspace) → 403 if not.
2. Check not already a workspace member → 409 if duplicate.
3. `role` must be `admin` or `member` → 400 otherwise.
4. Create `Membership(user_id, workspace_id, role)`.

**DELETE /ws/{wsId}/members/{user_id}** logic:
1. Fetch existing membership → 404 if not found.
2. Target is the requesting user → 400 "Cannot remove yourself".
3. Delete the workspace membership.

### Available org members endpoint

For the workspace "add member" UI, we need to know which org members are NOT yet in
the workspace.

| Method | Path | Response |
|--------|------|----------|
| GET | `/ws/{wsId}/members/available` | `[{user_id, email, org_role}]` |

Returns org members minus current workspace members. Gated by `require_admin`.

## Frontend — Admin Dashboard (Org Members)

### Navigation

Add "Members" entry to `AdminSubNav` between "Settings" and "Models", using the
`Users` lucide icon. Route: `/admin/members`.

### Page layout

```
┌─────────────────────────────────────────────────┐
│  Members                        [+ Add member]  │
├─────────────────────────────────────────────────┤
│  Email              Role       Joined   Actions  │
│  ────────────────── ───────── ──────── ───────── │
│  alice@example.com  owner     Apr 20            │
│  bob@example.com    [admin ▾] Apr 22   [Remove] │
│  carol@example.com  [member▾] May 01   [Remove] │
└─────────────────────────────────────────────────┘
```

- Owner row: static badge, no actions.
- Other rows: role is an inline dropdown (admin/member), remove button.
- Role change triggers a confirmation dialog before PATCH.
- Remove triggers a confirmation dialog warning about workspace cascade.

### Add member dialog

Modal form:
- Email input (free-text, validated on submit)
- Role select: admin | member
- Submit → POST `/admin/members` → refresh list
- Error states shown inline: "No user with this email", "Already a member"

### State

New Zustand store in `@cubeplex/core`:

```typescript
interface MemberStoreState {
  orgMembers: OrgMember[]
  orgMembersLoading: boolean
  loadOrgMembers(client: ApiClient): Promise<void>
  addOrgMember(client: ApiClient, email: string, role: string): Promise<void>
  updateOrgMemberRole(client: ApiClient, userId: string, role: string): Promise<void>
  removeOrgMember(client: ApiClient, userId: string): Promise<void>
}
```

## Frontend — Workspace Settings (Workspace Members)

### Navigation

Add "Members" entry to `SettingsNav` as a new top-level item after "MCP", using the
`Users` lucide icon. Tab key: `members`.

### Page layout

Same table pattern as org members, but scoped to the workspace:

```
┌─────────────────────────────────────────────────┐
│  Members                        [+ Add member]  │
├─────────────────────────────────────────────────┤
│  Email              Role       Joined   Actions  │
│  ────────────────── ───────── ──────── ───────── │
│  alice@example.com  [admin ▾] Apr 20   [Remove] │
│  bob@example.com    [member▾] Apr 22   [Remove] │
└─────────────────────────────────────────────────┘
```

- Current user's row: no remove action (self-removal blocked).
- Role dropdown and remove button with confirmation dialogs.

### Add member dialog

Modal with:
- Combobox/select listing org members NOT in this workspace (fetched from
  `GET /ws/{wsId}/members/available`)
- Role select: admin | member
- Submit → POST `/ws/{wsId}/members` → refresh list

### State

Extend the member store or create a workspace-scoped slice:

```typescript
interface WsMemberStoreState {
  wsMembers: WsMember[]
  wsMembersLoading: boolean
  availableOrgMembers: AvailableMember[]
  loadWsMembers(client: ApiClient, wsId: string): Promise<void>
  loadAvailableMembers(client: ApiClient, wsId: string): Promise<void>
  addWsMember(client: ApiClient, wsId: string, userId: string, role: string): Promise<void>
  updateWsMemberRole(client: ApiClient, wsId: string, userId: string, role: string): Promise<void>
  removeWsMember(client: ApiClient, wsId: string, userId: string): Promise<void>
}
```

## Authorization Summary

| Action | Required role |
|--------|--------------|
| List org members | Org admin/owner |
| Add/change/remove org member | Org admin/owner |
| List workspace members | Workspace admin |
| Add/change/remove workspace member | Workspace admin |
| View available org members (for ws add) | Workspace admin |

Note: non-admin org members can view the org member list (read-only, no mutation
actions rendered). This lets workspace admins who may not be org admins still see the
pool. Revisit if this is too permissive.

Correction: listing org members at `/admin/members` is already behind the org-admin
gate (admin layout). For workspace admins who need the org member pool, the
`/ws/{wsId}/members/available` endpoint serves that purpose without exposing the full
org member list.

## Constraints and Edge Cases

- **Owner immutability:** Cannot remove or demote the org owner. DB partial unique
  index `uq_org_membership_owner` enforces one owner per org. API returns 409.
- **Self-removal blocked:** API returns 400 at both levels.
- **Org removal cascades:** Deleting an org membership also deletes all workspace
  memberships for that user within the org (single DB transaction).
- **Workspace removal is isolated:** Removing from a workspace does not affect org
  membership.
- **Single-tenant / multi-tenant:** No mode-specific UI logic. `resolve_current_org_id`
  handles both modes transparently.
- **No new models or migrations:** All operations use existing tables and repositories.

## What This Does NOT Include

- Email-based invitation flow (out of scope — direct add only)
- Org ownership transfer
- Bulk member import
- Activity/audit log UI (audit events are already logged server-side)
- Multi-org switching UI
