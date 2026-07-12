---
sidebar_position: 5
title: Sandbox
---

# Sandbox

CubePlex executes agent-generated code in an isolated sandbox powered by OpenSandbox. The sandbox provides a secure environment where the agent can run code, install packages, and interact with files without affecting your host infrastructure.

Sandbox configuration is split across two admin pages: **Admin > Sandbox** (`/admin/sandbox`) for policies and **Admin > Sandbox Environment** (`/admin/sandbox-env`) for environment variables and secrets.

This page covers org-level administration. For the user-facing side — listing, restarting, and deleting your own sandboxes from workspace settings — see [Managing Sandboxes](../guides/conversations/sandboxes.md).

## What the sandbox can run

The agent runs code in the sandbox through a single shell-based `execute` tool — it issues shell commands, writes files via heredocs, and runs scripts (for example, `python script.py`). It is not tied to a fixed list of languages.

Which languages and tools are actually available depends on the **sandbox image** configured for your organization (the `default_image` in the sandbox policy). Whatever is installed in that image — Python, Node.js, and any other runtimes or CLI tools — is what the agent can use. To support an additional language or library, configure a sandbox image that includes it.

## Sandbox policies

Policies control what the sandbox is allowed to do, particularly around network access.

### Configure network access

1. Go to **Admin > Sandbox**.
2. Set the **default network action**: either **Allow** (permit all outbound traffic by default) or **Deny** (block all outbound traffic by default).
3. Add specific rules to override the default:
   - **Allow rules** — permit traffic to specific hosts or IP ranges (useful when the default is Deny).
   - **Deny rules** — block traffic to specific destinations (useful when the default is Allow).
4. Click **Save**.

### Example: locked-down sandbox

Set the default action to **Deny**, then add allow rules for only the hosts the agent needs:

| Rule | Action | Target |
|---|---|---|
| Default | Deny | All outbound |
| Rule 1 | Allow | `pypi.org`, `files.pythonhosted.org` |
| Rule 2 | Allow | `registry.npmjs.org` |
| Rule 3 | Allow | `api.your-internal-service.com` |

This ensures the agent can install packages and call your internal API, but cannot reach anything else.

:::info 📸 Screenshot placeholder
**Capture:** The Admin > Sandbox network policy editor with the default action set to **Deny** and a few allow rules added (e.g. `pypi.org`, `registry.npmjs.org`), showing the per-rule action/target controls.
**Asset:** `/img/admin/sandbox-network-policy.png`
:::

### Command rules

In addition to network rules, sandbox policies can match shell commands the agent tries to run and apply one of three actions:

- **Allow** — permit commands matching the pattern.
- **Deny** — block commands matching the pattern.
- **Confirm** — pause the agent and require a human to approve or deny the command before it runs.

Use **Confirm** for sensitive operations you want a person to sign off on, and **Deny** for commands that should never run.

:::note
Each sandbox run also has an execution timeout: long-running commands are cut off automatically so a stuck process cannot hold a sandbox open indefinitely.
:::

## Environment variables and secrets

You can inject environment variables and secrets into the sandbox at runtime. This lets the agent's code access API keys, database URLs, or configuration values without hardcoding them.

### Scope levels

Environment variables can be scoped to three levels:

| Scope | Visibility | Managed from |
|---|---|---|
| **Organization** | All workspaces, all users | **Admin > Sandbox Environment** |
| **Workspace** | All users within that workspace | The workspace's own settings |
| **User** | Only that user's sandbox sessions within a workspace | The workspace's own settings |

When the same variable name exists at multiple scopes, the narrower scope wins: User overrides Workspace, which overrides Organization.

The **Admin > Sandbox Environment** page manages **Organization**-scope variables only — these are injected into every workspace sandbox. Workspace- and user-scope variables are added from within each workspace, not from this admin page.

### Add an organization environment variable

1. Go to **Admin > Sandbox Environment**.
2. Click **Add Variable**.
3. Enter the variable name and value.
4. To store a credential, mark it as a **secret**. Secrets are handled very differently from plain values (see [How secrets stay out of the sandbox](#how-secrets-stay-out-of-the-sandbox)) — you also specify the **allowed hosts** the secret may be sent to, and optionally which request **header names** it applies to.
5. Click **Save**. A secret's value is encrypted and is not shown again after saving.

### How secrets stay out of the sandbox

For anything sensitive — API keys, tokens, database passwords — **use a secret entry**, not a plain variable. CubePlex is designed so the real secret value never lives inside the sandbox, where agent-written code (or its logs) could leak it. The two entry kinds behave differently:

- **Plain variables** (not marked secret) are injected into the sandbox environment as their literal value. Use them for non-sensitive config (base URLs, region names, feature flags). Code reads them directly, and they can appear in output and logs.
- **Secrets** are **never** injected as their real value. The sandbox instead receives an opaque **placeholder** token (e.g. `cbxref_…`). Your code uses the placeholder exactly as it would the real key — for example, putting it in an `Authorization` header. When the sandbox makes an outbound request, CubePlex's **egress proxy** substitutes the placeholder for the real secret **at the network boundary**, and only when both:
  - the destination **host** is one you allowed for that secret, and
  - the placeholder appears in one of the allowed **header names** (when you configured them).

Consequences of this design:

- **The real secret never exists inside the sandbox.** Code, files, and execution logs only ever see the placeholder — so secrets cannot end up in the agent's output or run logs.
- **Substitution happens outside the sandbox**, inside the egress proxy, so the secret is never written to any sandbox process or log.
- **Encrypted at rest.** The value lives in the credential vault and is only decrypted by the proxy at request time, for the verified sandbox, for an allowed host.
- **A leaked placeholder is useless.** It resolves only for its own sandbox and its allowed hosts, and is revoked when the run ends.

### Common use cases

- **API keys** for external services the agent's code calls directly (not via MCP).
- **Database connection strings** for data analysis tasks.
- **Feature flags** or configuration values that vary by workspace.
