# Auth, Identity & RBAC

**Read before modifying:** registration, login, CSRF, onboarding, invitations, API keys, org/workspace bootstrap, RBAC enforcement, or admin tooling.

## Identity model

`Organization → Workspace → Membership → User` is the workspace access path. Users can belong to more than one organization and more than one workspace. `OrganizationMembership` carries an organization role (`owner`, `admin`, or `member`); `Membership` carries the workspace role (`admin` or `member`).

Business data is scoped by `(org_id, workspace_id)` through `OrgScopedMixin` and `ScopedRepository[T]`. This is structural query isolation, not an ACL check added after a query. A user with several organization memberships must resolve an organization explicitly where the operation is organization-scoped; `AmbiguousOrgError` prevents silently choosing one.

## Sessions, OTP, and CSRF

Authentication uses fastapi-users with a JWT in the configurable, HTTP-only `cubeplex_auth` cookie. Registration and password login are rate-limited through Redis so limits are shared by backend replicas; frontend-forwarded requests are keyed by their original address in `X-Forwarded-For`. When email verification is enabled, registration issues a one-time password; `/verify-otp` verifies it and establishes the cookie session. `/resend-otp` deliberately returns success even when delivery fails, so it does not reveal whether an address exists.

CSRF uses double-submit cookies. `CSRFMiddleware` creates the configurable `cubeplex_csrf` cookie on a safe request when it is absent. A request with an auth cookie must send the same value in `X-CSRF-Token` for `POST`, `PUT`, `PATCH`, or `DELETE`.

## Registration and onboarding

Registration creates the user; it does not use a system setup endpoint. The authenticated user's `GET /api/v1/auth/me` response includes `needs_onboarding`, which is true until the user has a workspace membership.

`POST /api/v1/onboarding` provisions the first usable workspace:

- With no organization membership, the request needs `org_name`, `org_slug`, and `workspace_name`; it creates the organization and workspace.
- With an organization membership but no workspace membership, it needs only `workspace_name`; it creates a workspace in that organization.
- A user who already has a workspace receives `409 onboarding_not_required`.

The deployment mode remains available for product configuration, and onboarding is the setup flow. In a single-tenant deployment, creating an additional organization may require the multi-org license; concurrent first organization creation is protected by the bootstrap guard.

## Workspace scoping and RBAC

Business routes use `/api/v1/ws/{workspace_id}/...`. The workspace ID is a path parameter, never a request header. `request_context` resolves membership and provides the user, organization ID, workspace ID, and role. A missing workspace is a 404; a workspace the caller cannot access is a 403.

Organization-admin routes remain separate under `/api/v1/admin/...` even when they share a lower-level service with a workspace route. Do not introduce a `scope` query parameter or a route-level role switch to combine them.

## Key endpoints

- `POST /api/v1/auth/register`, `/login`, `/logout`
- `POST /api/v1/auth/verify-otp`, `/resend-otp`
- `GET` and `PATCH /api/v1/auth/me`
- `POST /api/v1/onboarding`
- `GET /api/v1/system/info` (public): `deployment_mode`, `version`, `sandbox_enabled`, `password_policy`, `edition`, `features`, and `public_base_url`
- `GET`, `POST`, and `DELETE /api/v1/me/api-keys...`
- `POST /api/v1/admin/orgs/invites` and `POST /api/v1/orgs/invites/accept`
- `/api/v1/ws/{workspace_id}/...` for scoped business operations

Personal-access API key plaintext is returned only once when it is created. Store it immediately; later list responses expose only its prefix and last-use time.

## Org invites

An organization admin creates an invite with an `admin` or `member` role; owners cannot be assigned by an invite. An authenticated recipient accepts a valid, unexpired single-use token at `/api/v1/orgs/invites/accept`. Accepting an org invitation may leave the user needing workspace-only onboarding.

## Credential vault

System credentials use `org_id=NULL` and the partial unique index `uq_credential_system_kind_name`. Reuse the same vault pattern for a new credential kind. For key rotation, see [quick-reference.md](quick-reference.md#vault-key-rotation).
