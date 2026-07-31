---
sidebar_position: 8
title: Single Sign-On
---

# Single Sign-On

CubePlex can delegate login to your identity provider over **SAML 2.0** or **OIDC**, so people
sign in with the account they already have and you manage access in one place.

Configuration lives at **Admin > Authentication** (`/admin/authentication`). One connection per
organization.

:::info Enterprise feature
SSO requires an enterprise licence. Without one the page shows an "Enterprise feature" notice.
See [Editions & Licensing](./editions.md). Google sign-in is **not** part of this — it ships in
the open-source edition and keeps working regardless.
:::

:::info 📸 Screenshot placeholder
**Capture:** Admin > Authentication with an OIDC connection in the testing state, showing the
status badge, the Redirect URI copy field, and the Activate button.
**Asset:** `/img/admin/sso-connection.png`
:::

## Before you start

You will move values in both directions, so keep your IdP's admin console open alongside CubePlex:

| From your IdP into CubePlex | From CubePlex into your IdP |
|---|---|
| OIDC: issuer URL, client ID, client secret | OIDC: the **Redirect URI** |
| SAML: entity ID, SSO URL, signing certificate | SAML: the **ACS URL** and **SP metadata URL** |

**Copy the CubePlex-side URLs from the form, not from this page.** They are built from your
deployment's configured public address, so they differ per install — and an IdP configured with
the wrong one rejects the login.

## Set up OIDC

1. Go to **Admin > Authentication** and click **Configure SSO**.
2. Choose **OIDC**.
3. Paste your **Issuer URL** and click **Discover**. CubePlex reads the provider's
   `.well-known` document and fills in the authorization, token, and JWKS endpoints. Fill them
   in by hand if your provider does not publish discovery.
4. Enter the **Client ID** and **Client Secret** from the application you registered with your IdP.
5. Copy the **Redirect URI** shown in the form into your IdP's list of allowed redirect URIs.
6. Click **Save**. The connection is created in the **testing** state — see
   [Test before you switch it on](#test-before-you-switch-it-on).

## Set up SAML

1. Go to **Admin > Authentication** and click **Configure SSO**.
2. Choose **SAML**.
3. Enter your IdP's **Entity ID**, **SSO URL**, and **signing certificate** (PEM).
4. Give your IdP the **ACS URL** shown in the form. If it can consume SP metadata instead, point
   it at the **SP metadata URL** — it carries the same values and stays correct if they change.
5. Click **Save**.

## Attribute mapping

CubePlex needs an email address and a stable identifier for each person. If your IdP sends those
under non-standard claim or attribute names, map them in the **Attribute mapping** section.

The email decides which account a login resolves to, so a wrong mapping either creates duplicate
accounts or fails to match existing ones. The display name and avatar are optional; the avatar is
refreshed from the IdP on every login unless the person uploaded their own.

## Test before you switch it on

A new connection starts in **testing**, and that state exists for a reason: **password login keeps
working for everyone while a connection is in testing.** You can complete a real round trip with
your IdP and fix what is wrong without locking anybody out.

While a connection is in testing, CubePlex records the raw attributes of the most recent login
attempt, so you can see exactly what your IdP sent and correct your mapping against real data.

Use **Validate** before attempting a login. It checks what it can reach without a user: for OIDC
the discovery document, the JWKS endpoint, and whether your client ID and secret are accepted; for
SAML the certificate, the IdP SSO URL, and the metadata URL. A wrong client secret shows up here
rather than as a failed login later.

When a test login succeeds and the mapped attributes look right, click **Activate**.

## What activation changes

Once a connection is **active**, SSO is mandatory for that organization:

- Members signing in with email and password are refused and pointed at your organization's SSO
  entry point (`/login/<org-slug>`).
- The login page offers an SSO button for the organization.
- New people can be created on first login if provisioning is set to automatic; with
  **invite-only** they must already have an account and be a member of the organization.

This applies to everyone in the organization, so read
[If SSO locks you out](#if-sso-locks-you-out) before activating.

## Provisioning

| Mode | First login by someone with no CubePlex account |
|---|---|
| **Automatic** | The account is created and added to your organization. |
| **Invite-only** | The login is refused. Add the person first, then they can sign in. |

Invite-only is the stricter choice: it means the set of people who can reach your organization is
whatever you maintain in CubePlex, not whatever your IdP happens to assert.

## Linked identities

The **Linked identities** table lists the external accounts currently linked to CubePlex accounts
through this connection. Unlinking one does not delete the CubePlex account — it only breaks the
association, so the next SSO login is treated as a first login and re-links according to your
provisioning setting.

## If SSO locks you out

A misconfigured IdP can leave every administrator unable to sign in — SSO is refusing them and
password login is disabled. Recovery does not go through the UI, because the UI is behind the thing
that is broken. Run this on the server, where the CLI can reach the database directly:

```bash
cubeplex admin disable-sso --org-slug <your-org-slug>
```

That flips the connection back to inactive and restores password login immediately. Fix the
configuration, test it, then activate again.

```bash
cubeplex admin list-sso    # every org's connection and its current state
```

## Removing the enterprise licence while SSO is active

Uninstalling the enterprise package does **not** remove your SSO configuration — the connection
row stays in the database. A deployment in that state strands the affected organization: SSO can
no longer be served, and password login is still refused for its members.

CubePlex logs an error naming the affected organizations and the recovery command at startup, and
keeps running so other organizations are unaffected. Run `disable-sso` for each organization
listed, or reinstall the package with a valid licence.

## Google sign-in

Google sign-in is separate from enterprise SSO, ships in the open-source edition, and is
configured in the backend settings rather than on this page.

One interaction is worth knowing: if a person belongs to an organization with **active** SSO,
Google sign-in is refused for them too. Otherwise a mandatory-SSO policy would be trivially
bypassable.
