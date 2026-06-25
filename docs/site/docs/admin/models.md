---
sidebar_position: 1
title: Model Management
---

# Model Management

CubeBox connects to LLM providers through API keys you configure at the organization level. Once a provider is set up and its models are enabled, workspace members can select those models in their conversations.

All model management happens at **Admin > Models** (`/admin/models`).

:::info 📸 Screenshot placeholder
**Capture:** The Admin > Models page showing the list of configured providers with their logos and connection/test status, and the models each provider exposes.
**Asset:** `/img/admin/models-providers.png`
:::

## Providers

A provider represents an LLM API endpoint. Each provider has:

- **Name** and **slug** — a human-readable label and a URL-safe identifier.
- **Base URL** — the API endpoint (e.g., `https://api.anthropic.com` for Anthropic).
- **Auth credentials** — typically an API key.
- **Capability descriptor** — declares what the provider supports (chat, vision, tool use, etc.).

### Add a provider from a preset

CubeBox ships with presets for common providers (Anthropic, OpenAI, and others). Presets pre-fill the base URL and capability descriptor so you only need to enter your API key.

1. Go to **Admin > Models**.
2. Click **Add Provider**.
3. Select a preset from the list (e.g., "Anthropic").
4. Paste your API key.
5. Click **Save**.

### Add a custom provider

Any service that exposes an OpenAI-compatible chat completions endpoint can be added as a custom provider.

1. Go to **Admin > Models**.
2. Click **Add Provider**.
3. Choose **Custom (OpenAI-compatible)**.
4. Enter a name, base URL, and API key.
5. Configure the capability descriptor to match what the endpoint supports.
6. Click **Save**.

### Test provider connectivity

After adding a provider, click **Test Connection** to verify that CubeBox can reach the endpoint and authenticate. The test sends a lightweight request and reports success or failure with details.

## Models

Each provider exposes one or more models. After adding a provider, its available models appear in the model list.

### Per-model configuration

You can configure the following for each model:

| Setting | Description |
|---|---|
| **Reasoning mode** | How the model handles extended thinking. Options vary by model: binary on/off, budget (token budget), effort level, or enum selection. |
| **Modalities** | Input/output capabilities — text, vision, tool use, etc. |
| **Cost rates** | Per-token costs — input, output, and (where applicable) cache read / cache write — used for the [Cost Tracking](./cost-tracking.md) dashboard. |

### How models reach workspaces

Once a provider is configured and its models are enabled, those models appear in the model picker for every workspace in your organization. Workspace members select a model when starting or continuing a conversation.

## Common tasks

### Rotate an API key

1. Go to **Admin > Models** and select the provider.
2. Update the API key field with the new key.
3. Click **Save**, then **Test Connection** to confirm the new key works.

### Disable a model

If you want to stop offering a specific model to your team, disable it in the model list. Existing conversations that used the model are preserved, but users cannot select it for new messages.

### Add a self-hosted or proxy endpoint

For models behind a reverse proxy, VPN, or self-hosted inference server, use the custom provider flow. Make sure the base URL is reachable from the CubeBox backend server.
