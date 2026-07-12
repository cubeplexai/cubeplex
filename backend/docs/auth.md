# Auth, Identity & RBAC

**Read before modifying:** registration flow, login, CSRF, org/workspace
bootstrap, RBAC enforcement, admin tooling.

## Identity Model

`Organization → Workspace → Membership → User`. One user can belong to
many workspaces via memberships; each membership carries a `Role`
(`admin` | `member`). All business tables carry `(org_id, workspace_id)`
via `OrgScopedMixin`.

## Auth Mechanics

- **fastapi-users** with JWT cookie strategy.
- Auth cookie: `cubeplex_auth`.
- Register / login endpoints are rate-limited via slowapi.

### CSRF

Double-submit cookie pattern. A `cubeplex_csrf` cookie is set on login;
mutating requests (POST/PUT/PATCH/DELETE) must echo it in the
`X-CSRF-Token` header whenever the `cubeplex_auth` cookie is present.

### Workspace Scoping

Every business route lives under `/api/v1/ws/{workspace_id}/...`. The
workspace id is a **path parameter**, not a header. The `request_context`
dependency extracts it via FastAPI `Path`, validates membership, and
produces a `RequestContext` (user + org_id + workspace_id + role).

- Workspace not found → 404.
- Not a member → 403.

Path-based scoping lets browser-direct loads (`<img>`, `<iframe>`,
`<a href>`) work without custom headers.

### Repository Layer

`OrgScopedMixin` + `ScopedRepository[T]`
(`cubeplex/repositories/base.py`) automatically filter every query by
`(org_id, workspace_id)` — **structural isolation, not an ACL check
bolted on top**. New business repositories should subclass
`ScopedRepository`.

## Endpoints

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `GET/POST /api/v1/workspaces`
- `POST /api/v1/workspaces/{ws}/invites` (admin only)
- `POST /api/v1/workspaces/invites/accept`
- `/api/v1/ws/{workspace_id}/conversations/...`
- `/api/v1/ws/{workspace_id}/conversations/{cid}/artifacts/...`
- All scoped business endpoints live under `/api/v1/ws/{workspace_id}/`.

## Register Bootstrap (M9, mode-aware)

`UserManager.on_after_register` branches on `deployment.mode`:

### `multi_tenant` (cloud SaaS)

- Per-user org auto-created (`"<local>'s Org"`).
- Personal workspace + workspace-admin membership.
- `OrganizationMembership(role=owner)` on the new org.

### `single_tenant` (OSS default)

- **First user** is a **pending owner** (only the `User` row exists).
  They complete `POST /api/v1/system/setup` to name the org and pick a
  slug.
- **Subsequent users** attach to the singleton org as
  `OrgRole.MEMBER` and get their own Personal workspace +
  workspace-admin membership.
- A **PostgreSQL advisory lock** plus a
  `user_count > 1 AND org_count == 0` check serialize concurrent first
  registrations and return 409 `setup_in_progress` for races.

If any bootstrap step fails the `User` row is best-effort deleted before
the exception propagates, so registration appears atomic to the client.

Register response: `{id, email, default_workspace_id}` (empty string in
the single-tenant pending-owner case).

## Org-level Role Model (M9)

`OrganizationMembership`:

- Table: `organization_memberships`
- Composite PK: `(user_id, org_id)`
- `role` from `OrgRole = {OWNER, ADMIN, MEMBER}`
- DB-level partial unique index `uq_org_membership_owner ON (org_id) WHERE role = 'owner'` — at most one owner per org.

Admin gates (`require_org_admin`, `/admin/me`, cost routes) read this
row — **distinct from workspace-level `Membership.role`.**

## Operator CLI (M9)

```bash
cubeplex admin grant-admin <email> [--org-slug X]   # promote → org admin
cubeplex admin revoke-admin <email> [--org-slug X]  # demote admin → member
```

`--org-slug` is required when more than one org exists. `revoke-admin`
refuses to touch an owner.

## System Info / Setup Endpoints (M9)

- `GET /api/v1/system/info` — **public, pre-login** —
  `{deployment_mode, version, needs_org_setup}` for frontend mode
  discovery.
- `POST /api/v1/system/setup` — auth, `single_tenant` only — accepts
  `{org_name, slug}`. Slug regex `^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$`,
  length 3..32.
  - 404 in `multi_tenant`.
  - 409 if org already exists or another setup is in flight.
- Startup check: in `single_tenant`, the lifespan refuses to boot when
  the DB has more than 1 org.

## Credential Vault

System creds use `org_id=NULL` + partial unique index
(`uq_credential_system_kind_name`). Same table as org-scoped creds —
reuse this pattern when adding a new vault kind.

Rotation: see [quick-reference.md](quick-reference.md#vault-key-rotation).
