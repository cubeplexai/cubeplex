---
sidebar_position: 4
title: Model Selection
---

# Model Selection

CubeBox lets you choose which AI model powers each message. Different models have different strengths — cost, speed, reasoning depth, vision support, and more. You pick the model from the input bar before sending a message.

## Presets

Models are exposed to workspace members through **presets**. A preset is a named configuration that your org admin creates, mapping a friendly label (e.g., "Claude Sonnet", "GPT-4o") to a specific model on a specific provider.

Each workspace has a **default preset** that is automatically selected when you start a new conversation. You can switch to any other available preset at any time.

### How to switch presets

1. Look at the input bar, above the text area. The **preset picker** dropdown shows the currently selected preset.
2. Click the dropdown and choose a different preset.
3. Your next message (and all subsequent messages in this conversation) will use the new preset, until you switch again.

The preset choice is **sticky per workspace** — if you switch to "Claude Opus," it stays selected for your next conversation in that workspace too, until you change it. Each workspace remembers its own choice independently in your browser, so switching workspaces does not carry the selection over.

### Default badge

The preset marked as the workspace default shows a small "Default" badge in the dropdown. This is the model new members and new browser sessions start with.

## Thinking (extended reasoning)

Some models support **extended thinking** — a mode where the agent reasons step-by-step before producing its final answer. CubeBox exposes this as a separate **Effort** control (a "Faster → Smarter" slider) next to the preset picker.

### Thinking levels

| Level      | Behavior                                                       |
| ---------- | -------------------------------------------------------------- |
| **Off**    | No extended thinking. The agent responds directly.             |
| **Low**    | Brief internal reasoning. Fast, low cost.                      |
| **Medium** | Moderate reasoning depth. Good default for most tasks.         |
| **High**   | Deep reasoning. Better for complex analytical or coding tasks. |
| **Max**    | Maximum reasoning effort. Use for the hardest problems.        |

Higher thinking levels consume more tokens and take longer, but produce more thorough analysis for complex questions. For simple questions ("What is the capital of France?"), thinking adds cost without benefit.

The thinking level is sticky across messages, like the preset. CubeBox stores it as a standard reasoning setting (`mode`, `effort`, and `summary`) and maps it to each provider's API. A **thinking badge** appears next to the control when an elevated level is active, so you do not accidentally leave it on high for routine questions.

### Viewing thinking output

When thinking is enabled, the agent's response includes a collapsible **Thinking** block above the main answer. Expand it to see the reasoning chain. This is useful for:

- Understanding how the agent approached a problem.
- Catching logical errors in complex reasoning.
- Learning from the agent's analytical process.

## Model failover

If the selected model is temporarily unavailable (provider outage, rate limit, network issue), CubeBox can automatically fail over to a backup model in the same preset's fallback chain. When this happens, a small banner appears in the chat naming the model it switched from and the one it switched to:

> Switched from `<provider>/<model>` to `<fallback-provider>/<fallback-model>`

Click the banner to expand it and see the reason for the failover. The conversation continues on the backup model until the next message, at which point CubeBox tries the primary model again.

If the entire fallback chain is exhausted (all models in the preset are unavailable), the banner instead reads **"Failover exhausted on `<provider>/<model>`"** and the run stops. Wait and try again, or switch to a different preset.

## Which model to choose

There is no single best model. Here are practical guidelines:

| Task type                                  | Recommended approach                                                                 |
| ------------------------------------------ | ------------------------------------------------------------------------------------ |
| Quick questions, summarization, formatting | A fast, cost-efficient model (e.g., Haiku, GPT-4o-mini). Thinking off.               |
| Coding, debugging, code review             | A capable model with thinking on medium or high.                                     |
| Complex analysis, multi-step reasoning     | A top-tier model with thinking on high or max.                                       |
| Image understanding, screenshot analysis   | A model with **vision** support. Check with your admin which presets support vision. |
| Long documents, large context              | A model with a large context window.                                                 |

## What org admins control

The set of available presets is determined by your org admin. If you need access to a model that is not listed:

- Ask your admin to add the provider and model at **Admin > Models**.
- Ask your admin to create a preset that includes the model.

See the [Model Management](../../admin/models.md) admin guide for details on how providers and presets are configured.

## Tips

- **Match the model to the task.** Do not use the most expensive model for every message. Switch to a fast model for simple follow-ups and save the powerful one for complex tasks.
- **Turn thinking off for simple exchanges.** Extended reasoning adds latency and cost. Use it when the task genuinely benefits from step-by-step analysis.
- **Watch for the thinking badge.** If you set thinking to high earlier and forgot, the badge reminds you. Click the control to lower it when you no longer need deep reasoning.
- **Try different models on the same task.** If you are not satisfied with a response, switch presets and re-ask. Different models have different strengths and the same question can get a better answer from a different model.
