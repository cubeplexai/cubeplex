---
sidebar_position: 7
title: Editions & Licensing
---

# Editions & Licensing

CubePlex ships in two editions.

**OSS (Apache-2.0)** is what you get when you self-host, and it is complete on its own: the
full agent runtime, sandboxes, skills, artifacts, memory, MCP, every IM connector,
email/password sign-in with admin and member roles, and one organization. Your data never
leaves your deployment.

**Enterprise** adds the governance layer that security reviews ask about: SSO (SAML/OIDC),
fine-grained roles, persistent audit logs, the trace viewer, cost insights, and running
several isolated organizations in one deployment.

## How the edition is determined

Set your license key in the backend configuration:

```yaml
license:
  key: "CPX1...."
```

or through the environment variable `CUBEPLEX_LICENSE__KEY`.

The key is verified **offline** — an Ed25519 signature checked against a public key built
into the release. Nothing is sent anywhere, so air-gapped deployments work normally, and
there is no license server to be unavailable.

With a valid key, `GET /api/v1/system/info` reports `"edition": "ee"` along with the
licensed `features`, and the Enterprise pages appear in the admin sidebar. Without one,
CubePlex runs as OSS: Enterprise pages are hidden from navigation and show an "Enterprise
feature" notice if you navigate to them directly.

![Admin sidebar without the Enterprise entries, and the "Enterprise feature" notice in place of the page body](/img/admin/ee-gate-card.png)

Note what the sidebar does and does not list: Authentication, Insights, and Traces are
absent, while everything OSS — including IM connectors — stays.


## If a key is rejected

A key that is missing, malformed, or past its expiry is **not** a fatal error — the server
logs a warning and continues as OSS. A misconfigured key should not take down a deployment
that is not otherwise using Enterprise features.

There is one exception. If the Enterprise package is installed but no valid key is
configured, the server refuses to start rather than silently running Enterprise code
unlicensed. The startup error names the two ways out: configure a key, or uninstall the
package.

## Expiry and renewal

Every key carries an expiry. Once it passes, the deployment falls back to OSS behavior at
the next restart — Enterprise pages disappear, and any Enterprise package that is installed
will block startup until you supply a current key. Renew before the expiry date rather than
after.

Because verification is offline, there is no way to revoke a key remotely; the expiry date
is the only time bound. Keys are issued for one year by default.

## Multiple organizations

An OSS deployment in `single_tenant` mode hosts exactly one organization. Creating a second
one — say, an isolated organization per client or per department — needs a license that
includes the `multi_org` feature. Without it the API returns
`403 multi_org_requires_license`, and a deployment whose database already holds more than
one organization refuses to start.

Workspaces are not affected. You can create as many workspaces inside your organization as
you like on any edition.

## Configuration reference

| Key | Environment variable | Purpose |
|---|---|---|
| `license.key` | `CUBEPLEX_LICENSE__KEY` | Your Enterprise license key. Absent → OSS edition. |
| `license.public_key_hex` | `CUBEPLEX_LICENSE__PUBLIC_KEY_HEX` | Overrides the built-in signing key. Development and testing only — never set this in production. |
