---
sidebar_position: 5
title: Sandbox
---

# Sandbox

CubeBox executes agent-generated code in an isolated sandbox powered by OpenSandbox. The sandbox provides a secure environment where the agent can run code, install packages, and interact with files without affecting your host infrastructure.

Sandbox configuration is split across two admin pages: **Admin > Sandbox** (`/admin/sandbox`) for policies and **Admin > Sandbox Environment** (`/admin/sandbox-env`) for environment variables and secrets.

## Supported languages

The sandbox supports multiple runtimes out of the box:

- Python
- JavaScript / TypeScript (Node.js)
- Java
- C# (.NET)
- Go
- Shell scripts (Bash)

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

### Resource limits

Sandbox policies also control resource limits such as CPU time, memory, and execution duration. These prevent runaway processes from consuming excessive resources.

## Environment variables and secrets

You can inject environment variables and secrets into the sandbox at runtime. This lets the agent's code access API keys, database URLs, or configuration values without hardcoding them.

### Scope levels

Environment variables can be scoped to three levels:

| Scope | Visibility | Managed by |
|---|---|---|
| **Organization** | All workspaces, all users | Org admin |
| **Workspace** | All users within that workspace | Workspace admin |
| **User** | Only the individual user's sandbox sessions | The user themselves |

When the same variable name exists at multiple scopes, the narrower scope wins: User overrides Workspace, which overrides Organization.

### Add an environment variable

1. Go to **Admin > Sandbox Environment**.
2. Click **Add Variable**.
3. Enter the variable name and value.
4. Choose the scope (Organization or Workspace).
5. Optionally mark it as a **secret** (the value will be masked in the UI after saving).
6. Click **Save**.

### Security

- **Secrets are encrypted at rest.** Values marked as secrets are stored encrypted in the database and are only decrypted when injected into a sandbox session.
- **Secrets are not logged.** Sandbox execution logs redact secret values.
- **Secrets are injected at runtime.** They exist in the sandbox process's environment only for the duration of the execution.

### Common use cases

- **API keys** for external services the agent's code calls directly (not via MCP).
- **Database connection strings** for data analysis tasks.
- **Feature flags** or configuration values that vary by workspace.
