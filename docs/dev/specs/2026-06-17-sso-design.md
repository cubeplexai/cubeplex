# SSO Support Design

## Overview

Add SSO support to cubebox with two layers:

- **Platform-level social login** — Google OIDC, available to all users, configured by platform operator
- **Organization-level enterprise SSO** — OIDC or SAML 2.0, configured per-org by org admin, forced for org members

SSO is an authentication entry point, not a replacement for the auth stack. All SSO
flows terminate by issuing the existing cubebox JWT cookie — downstream workspace
scoping, CSRF, RequestContext are untouched.

## Data Model

### `sso_connections` — org-level enterprise SSO configuration

One active connection per organization.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID7, prefix `sso` | public id |
| `org_id` | FK organizations | unique — one connection per org |
| `protocol` | varchar | `"oidc"` \| `"saml"` |
| `display_name` | varchar | e.g. "Acme Okta SSO" |
| `status` | varchar | `"active"` \| `"inactive"` \| `"testing"` |
| `provisioning` | varchar | `"auto"` \| `"invite_only"` |
| `config` | JSONB | protocol-specific configuration (see below) |
| `credential_id` | FK credentials, nullable | vault entry for secrets (client_secret / signing cert) |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

**OIDC config shape:**

```json
{
  "issuer": "https://acme.okta.com",
  "authorization_endpoint": "https://acme.okta.com/oauth2/v1/authorize",
  "token_endpoint": "https://acme.okta.com/oauth2/v1/token",
  "userinfo_endpoint": "https://acme.okta.com/oauth2/v1/userinfo",
  "client_id": "0oa...",
  "scopes": ["openid", "email", "profile"],
  "attribute_mapping": {
    "id": "sub",
    "email": "email",
    "name": "name"
  }
}
```

OIDC `attribute_mapping` has sensible defaults (`sub`, `email`, `name` per
OpenID Connect Core standard claims) so admins only need to override when the
IdP uses non-standard claim names.

**SAML config shape:**

```json
{
  "idp_entity_id": "https://acme.okta.com/saml",
  "idp_sso_url": "https://acme.okta.com/app/.../sso/saml",
  "idp_certificate": "MIIDpDCCA...",
  "sp_entity_id": "https://cubebox.app/saml/acme",
  "name_id_format": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
  "attribute_mapping": {
    "id": "NameID",
    "email": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "name": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"
  }
}
```

SAML `attribute_mapping` is required — SAML attribute names are not
standardized and vary between IdPs (Okta uses short names like `email`,
Azure AD uses full URI-style names, LDAP-backed IdPs use OID URNs). The
admin panel shows common presets per IdP vendor to reduce manual input.

### Attribute mapping

Both OIDC and SAML configs carry an `attribute_mapping` object that tells
Identity Resolution how to extract cubebox-relevant fields from IdP responses:

| Mapping key | Purpose | OIDC default | SAML default |
|---|---|---|---|
| `id` | Unique user identifier at the IdP | `sub` | `NameID` |
| `email` | User email address | `email` | *(must configure)* |
| `name` | Display name (optional) | `name` | *(must configure)* |

Identity Resolution reads the raw IdP response (OIDC claims dict / SAML
assertion attributes), applies the mapping, and passes the normalized
`(id, email, name)` tuple to the find-or-create logic. If a mapped key is
missing from the IdP response, `id` and `email` cause a login failure;
`name` falls back to the email local part.

Sensitive values (OIDC client_secret, SAML SP private key) are stored in the
existing Credential Vault, referenced by `credential_id`.

### `external_identities` — maps external identities to cubebox users

Unified table for both enterprise SSO and social login.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID7, prefix `eid` | public id |
| `user_id` | FK users | |
| `provider_type` | varchar | `"oidc_sso"` \| `"saml_sso"` \| `"google"` |
| `provider_id` | varchar | `sso_connection.id` for enterprise, `"google"` for social |
| `external_id` | varchar | IdP-side user identifier (OIDC `sub` / SAML NameID) |
| `external_email` | varchar | email from IdP (for audit, source of truth is IdP) |
| `metadata` | JSONB | extra claims (display_name, groups, etc.) |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

Unique constraint: `(provider_type, provider_id, external_id)`.

### Social login configuration

Google social login is platform-level. No DB table needed — configured in the
application config file:

```yaml
social_login:
  google:
    enabled: true
    client_id: "xxx.apps.googleusercontent.com"
    # client_secret stored in Credential Vault: kind="social_login", name="google"
```

### Relationships

```
Organization ──1:1──> SSOConnection
                          |
User ──1:N──> ExternalIdentity ──N:1──┘ (provider_id = sso_connection.id)
  |
  └──1:N──> ExternalIdentity (provider_type = "google", provider_id = "google")
```

## Authentication Flows

### OIDC enterprise SSO

```
Browser                     cubebox                          IdP (Okta, etc.)
  |                           |                                |
  | POST /auth/sso/initiate   |                                |
  | {org_slug: "acme"}        |                                |
  |──────────────────────────>|                                |
  |                           | look up SSOConnection by org   |
  |                           | generate state + PKCE           |
  |                           | store in Redis                  |
  |  200 {redirect_url}       |                                |
  |<──────────────────────────|                                |
  |                                                            |
  | GET redirect_url (IdP authorize endpoint)                  |
  |───────────────────────────────────────────────────────────>|
  |                                                            |
  | user authenticates at IdP                                  |
  |                                                            |
  | 302 → /auth/sso/oidc/callback?code=xxx&state=xxx           |
  |<───────────────────────────────────────────────────────────|
  |                           |                                |
  | GET /sso/oidc/callback    |                                |
  |──────────────────────────>|                                |
  |                           | validate state                 |
  |                           | exchange code for tokens        |
  |                           | fetch userinfo                  |
  |                           | → Identity Resolution           |
  |                           | → issue JWT cookie              |
  |  302 → /w/{workspace}    |                                |
  |<──────────────────────────|                                |
```

### SAML enterprise SSO

```
Browser                     cubebox (SP)                     IdP
  |                           |                                |
  | POST /auth/sso/initiate   |                                |
  | {org_slug: "acme"}        |                                |
  |──────────────────────────>|                                |
  |                           | build AuthnRequest              |
  |                           | store RelayState in Redis       |
  |  200 {redirect_url}       | (IdP SSO URL + SAMLRequest)    |
  |<──────────────────────────|                                |
  |                                                            |
  | GET redirect_url (IdP SSO URL with SAMLRequest)            |
  |───────────────────────────────────────────────────────────>|
  |                                                            |
  | user authenticates at IdP                                  |
  |                                                            |
  | POST /auth/sso/saml/acs                                    |
  | (SAMLResponse + RelayState)                                |
  |<───────────────────────────────────────────────────────────|
  |                           |                                |
  | POST /sso/saml/acs        |                                |
  |──────────────────────────>|                                |
  |                           | validate signature              |
  |                           | parse Assertion                 |
  |                           | extract NameID + attributes     |
  |                           | → Identity Resolution           |
  |                           | → issue JWT cookie              |
  |  302 → /w/{workspace}    |                                |
  |<──────────────────────────|                                |
```

### Google social login

Same as OIDC enterprise flow, except:

- No `org_slug` — platform-level, single Google config
- Route: `GET /api/v1/auth/social/google/authorize` and `/callback`
- Not forced — user chooses voluntarily
- Does not associate user with any organization

### Identity Resolution (shared by all flows)

All SSO and social login flows converge on the same resolution logic.

**Step 0 — Attribute mapping.** Before resolution, the raw IdP response is
normalized via the connection's `attribute_mapping`:

- OIDC: read claims dict, apply mapping (defaults: `sub` → id, `email` →
  email, `name` → name).
- SAML: read assertion attributes, apply mapping (no defaults — admin must
  configure).
- Google social: hardcoded standard claims, no mapping needed.

If `id` or `email` cannot be resolved after mapping, the login fails with a
clear error ("missing required attribute").

**Step 1–2 — Find or create user:**

```
Input: (provider_type, provider_id, external_id, external_email, claims)
       ← all derived from the mapped attributes above

1. Look up external_identities → found → sign in as linked user

2. Not found → look up users by email
   a. User exists → create ExternalIdentity link → sign in
   b. User does not exist →
      - Enterprise SSO: check provisioning policy
        - "auto" → create User + OrgMembership + Membership + ExternalIdentity → sign in
        - "invite_only" → reject, tell user to contact admin
      - Social login: create User (no org) + ExternalIdentity → sign in
```

## Enforcement

### Forced SSO

When `sso_connections.status = 'active'`, password login is disabled for all
org members:

- User submits email + password at `/login` → backend checks user's org → if
  org has active SSO → reject with `{"code": "sso_required", "message": "...",
  "login_url": "/login/{slug}"}`. The error is identical to "wrong password"
  for non-existent users to prevent user enumeration.
- Org admins are also subject to forced SSO — no exceptions.

### Testing mode

`status = "testing"`:

- SSO login works (admin can test the flow)
- Password login is NOT disabled (prevents lockout during configuration)
- Admin manually switches to `active` after confirming SSO works

### Lockout recovery

If SSO is misconfigured and all users are locked out:

```bash
cubebox admin disable-sso --org-slug acme    # sets status → inactive
cubebox admin list-sso                       # lists all SSO connections
```

No self-service recovery (complex and security-risky).

## Account Linking

### Automatic linking (email match)

When an org enables SSO and existing password users log in via SSO for the
first time:

1. SSO flow returns email
2. Identity Resolution finds existing user by email
3. Automatically creates ExternalIdentity link
4. User can only log in via SSO going forward (for that org's context)

Password hash is retained in DB but the login endpoint refuses to use it.

### Admin manual management

Admin panel shows:

- List of org members with SSO link status (linked / not linked / never logged in)
- Unlink action: removes ExternalIdentity record (user will re-link on next SSO login via email match)
- No manual link creation — links are only created through actual IdP authentication

## Login Page UX

### `/login` (generic entry)

```
┌─────────────────────────────────┐
│          Login to cubebox        │
│                                  │
│  ┌────────────────────────────┐  │
│  │  Email                      │  │
│  └────────────────────────────┘  │
│  ┌────────────────────────────┐  │
│  │  Password                   │  │
│  └────────────────────────────┘  │
│  ┌────────────────────────────┐  │
│  │  Login                      │  │
│  └────────────────────────────┘  │
│                                  │
│  ─────────── or ────────────     │
│                                  │
│  ┌────────────────────────────┐  │
│  │  G  Login with Google       │  │
│  └────────────────────────────┘  │
│  ┌────────────────────────────┐  │
│  │  SSO Login                  │  │
│  └────────────────────────────┘  │
│                                  │
│  No account? Register            │
└─────────────────────────────────┘
```

Clicking "SSO Login":

- **Multi-tenant**: expands an org slug input field + "Continue" button →
  calls `POST /auth/sso/initiate` with the slug → redirects to IdP
- **Single-tenant**: calls `POST /auth/sso/initiate` with no slug (backend
  resolves the singleton org) → redirects to IdP directly

### `/login/{org_slug}` (org-specific entry)

```
┌─────────────────────────────────┐
│     Login to {Org Name}          │
│                                  │
│  ┌────────────────────────────┐  │
│  │  SSO Login                  │  │  ← direct IdP redirect
│  └────────────────────────────┘  │
│                                  │
│  Not a member? Go to login       │
└─────────────────────────────────┘
```

If the org has no SSO configured, `/login/{slug}` falls back to the standard
email + password form.

Admins distribute `/login/{slug}` to employees or configure it in the IdP's
app tile.

## API Routes

### SSO auth flow

```
POST /api/v1/auth/sso/initiate              — start SSO flow, returns IdP redirect URL
GET  /api/v1/auth/sso/oidc/callback         — OIDC authorization code callback
POST /api/v1/auth/sso/saml/acs              — SAML Assertion Consumer Service
GET  /api/v1/auth/sso/saml/metadata/{sso_id} — SP metadata XML (for IdP configuration)
```

### Org public info (pre-login)

```
GET  /api/v1/auth/org-info/{org_slug}       — public, returns {org_name, sso_enabled, sso_protocol}
```

Used by `/login/{slug}` page to display org name and decide whether to show
SSO button or password form. No sensitive information exposed.

### Social login

```
GET  /api/v1/auth/social/google/authorize   — start Google OAuth flow
GET  /api/v1/auth/social/google/callback    — Google OAuth callback
```

### Admin SSO management

```
GET    /api/v1/admin/sso                           — get org's SSO config
POST   /api/v1/admin/sso                           — create SSO connection
PUT    /api/v1/admin/sso/{sso_id}                  — update config
DELETE /api/v1/admin/sso/{sso_id}                  — delete (must be inactive first)
POST   /api/v1/admin/sso/{sso_id}/activate         — testing → active
POST   /api/v1/admin/sso/{sso_id}/deactivate       — active → inactive

GET    /api/v1/admin/sso/{sso_id}/identities       — list linked identities
DELETE /api/v1/admin/sso/{sso_id}/identities/{eid}  — unlink an identity

POST   /api/v1/admin/sso/discover-oidc              — input issuer URL, returns discovered endpoints
```

### Operator CLI

```bash
cubebox admin disable-sso --org-slug acme    # emergency SSO disable
cubebox admin list-sso                       # list all SSO connections
```

## Admin Panel UX

New "Authentication" page under org settings:

```
Org Settings
├── General
├── Members
├── Authentication        ← new
│   ├── SSO Connection
│   └── Member SSO Status
└── ...
```

### SSO connection setup flow

1. Select protocol: OIDC / SAML
2. OIDC: enter Issuer URL (auto-discover via `.well-known/openid-configuration`),
   Client ID, Client Secret. Display Redirect URI for admin to copy into IdP.
3. SAML: upload IdP Metadata XML (auto-parse) or fill manually. Display
   SP Metadata URL / ACS URL / Entity ID for admin to configure in IdP.
4. Configure attribute mapping:
   - OIDC: pre-filled with standard defaults (`sub`, `email`, `name`); admin
     can override if their IdP uses non-standard claim names.
   - SAML: required fields. The form offers vendor presets (Okta, Azure AD,
     Google Workspace, Generic LDAP) that pre-fill common attribute URIs;
     admin can still edit manually.
5. Select provisioning: auto-create / invite-only
6. Save → `status = "testing"`
7. Admin clicks "Test SSO" → opens new window, runs SSO flow → success/failure
   feedback. The test result page shows the raw attributes returned by the IdP
   alongside the mapped values, so the admin can verify the mapping is correct.
8. Admin clicks "Activate" → `status = "active"`, password login disabled for org

### Member SSO status page

- Lists org members
- Each member shows: linked / not linked / never logged in via SSO
- Admin can unlink (remove ExternalIdentity)

## Infrastructure Reuse

- **State + PKCE storage**: reuse existing MCP OAuth `OAuthStateStore` (Redis)
- **Credential Vault**: store client_secret, SAML certificates
- **JWT cookie issuance**: reuse existing `auth_backend.login()` from fastapi-users
- **Plugin architecture**: SSO routes registered via `AuthProvider.get_auth_routers()`
  alongside existing auth routes — no need to replace the AuthProvider

## Scope Boundaries

In scope:

- OIDC + SAML enterprise SSO (org-level)
- Google social login (platform-level)
- Forced SSO enforcement
- Auto-provision + invite-only provisioning
- Email-based auto-linking + admin manual unlink
- Admin panel SSO configuration
- Operator CLI for lockout recovery

Out of scope (future work):

- SCIM user directory sync
- Domain verification (DNS TXT)
- Multi-factor authentication
- Session duration / idle timeout policies per org
- Social login providers beyond Google
- IdP-initiated SAML login (only SP-initiated)
- Group-to-workspace mapping from IdP attributes
