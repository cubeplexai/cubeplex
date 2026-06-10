---
sidebar_position: 3
title: MCP Connector Management
---

# MCP Connector Management

MCP (Model Context Protocol) connectors let the agent call external APIs — databases, SaaS products, internal services, and more. As an admin, you manage the connector catalog, configure authentication, and control which workspaces have access.

Connector management happens at **Admin > MCP** (`/admin/mcp`).

## Key concepts

| Concept | Description |
|---|---|
| **Template** | A connector definition that describes what the connector does, its tools, and how it authenticates. Think of it as the "app" in an app store. |
| **Auth config** | The credentials and OAuth settings attached to a template. |
| **Installed instance** | A template that has been activated for a specific org or workspace, with credentials configured. |
| **Grant** | Permission for a workspace (and its members) to use an installed connector. |

## Manage the connector catalog

The catalog contains all connector templates available to your organization.

### Add a connector template

1. Go to **Admin > MCP**.
2. Click **Add Connector**.
3. Fill in the template definition: name, description, server URL, and available tools.
4. Click **Save**.

### Remove a connector template

Removing a template also removes all of its installed instances across workspaces. Confirm before proceeding.

## Configure authentication

Connectors authenticate with external services in different ways. You configure auth at the template level.

### OAuth connectors

For connectors that use OAuth (e.g., GitHub, Slack, Google):

1. Select the connector in the catalog.
2. Open the **Auth** tab.
3. Enter the OAuth client ID and client secret from the external service's developer console.
4. Configure the redirect URI (CubeBox provides the expected URI).
5. Save the configuration.

When a user installs this connector, they are guided through the OAuth authorization flow (DRC — Dynamic Registration and Consent) to grant CubeBox access.

### Manual token entry

For connectors that use API keys or bearer tokens:

1. Select the connector in the catalog.
2. Open the **Auth** tab.
3. Choose the auth type (API key or Bearer token).
4. The token will be requested when the connector is installed.

## Install connectors

Connectors can be installed at the **organization level** (available to all workspaces) or at the **workspace level** (available only to that workspace).

### Install at the org level

1. Go to **Admin > MCP**.
2. Select a template from the catalog.
3. Click **Install** and choose **Organization-wide**.
4. Complete the auth flow if required (OAuth redirect or token entry).
5. The connector is now available to grant to workspaces.

### Install at the workspace level

1. Navigate to the target workspace's settings.
2. Go to the **MCP Tools** section.
3. Select a connector from the catalog and click **Install**.
4. Complete the auth flow.

## Grant workspace access

After installing a connector at the org level, you control which workspaces can use it.

1. Go to **Admin > MCP** and select the installed connector.
2. Open the **Workspace Access** tab.
3. Toggle access on or off for each workspace.

Only members of granted workspaces will see the connector's tools in their conversations.

## Revoke access

To remove a workspace's access to a connector, toggle it off in the **Workspace Access** tab. Active conversations that were using the connector will no longer be able to call its tools in subsequent turns.
