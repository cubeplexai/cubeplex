---
sidebar_position: 6
title: Cost Tracking
---

# Cost Tracking

CubePlex tracks LLM token usage across your organization so you can monitor spending and identify cost trends. The cost tracking dashboard is available at **Admin > Cost** (`/admin/cost`).

## What is tracked

Every LLM call made through CubePlex records:

- **Input tokens** — the number of tokens sent to the model (prompt, system instructions, tool results, etc.).
- **Output tokens** — the number of tokens the model generated in its response.
- **Cache read / cache write tokens** — tokens served from or written to the prompt cache, billed at their own rates (often much cheaper than fresh input tokens).
- **Provider and model** — which provider and model handled the call.
- **Timestamps** — when the call started and ended.

Each call also records the per-model price rates (input, output, cache read, and cache write) **as a snapshot at the time of the call**, so historical costs stay accurate even after you later change a model's rates in [Model Management](./models.md).

Cost tracking also records **sandbox compute** events, so the totals reflect more than just LLM token spend.

## Usage dashboard

The dashboard gives you a high-level view of your organization's AI spending.

### Spend over time

A time-series chart of spend, viewable at **daily** or **weekly** granularity. You can break the series down by **workspace**, **model**, or **user**. Use this to spot trends — increasing usage after onboarding new team members, spikes from large batch tasks, or the impact of switching to a cheaper model.

### Breakdowns

Alongside the org-wide totals, the dashboard breaks spend down several ways for the selected period:

- **By model** — which models drive the most spend, and whether cheaper alternatives might work for certain use cases.
- **By workspace** — which teams or projects are spending the most.
- **By user** — per-person usage.
- **By day** — daily totals across the period.

Each row shows input/output and cache token counts, the call count, and the cost.

### Export

You can export the raw cost data as CSV — for the whole organization or for a single workspace — to analyze it in a spreadsheet or feed it into your own reporting.

:::info 📸 Screenshot placeholder
**Capture:** The Admin > Cost dashboard showing the spend-over-time chart at the top (with the workspace/model/user dimension toggle) and a breakdown table below it.
**Asset:** `/img/admin/cost-dashboard.png`
:::

## Tips for managing costs

- **Set accurate cost rates.** Make sure the input and output token rates in [Model Management](./models.md) reflect your actual provider pricing. Without accurate rates, the dashboard numbers will be misleading.
- **Review regularly.** Check the dashboard weekly to catch unexpected spikes early.
- **Right-size your models.** If a significant portion of spend comes from a high-cost model being used for simple tasks, consider guiding your team toward a lighter model for those use cases.
- **Use the per-model view to compare.** After switching models or adding a new one, the per-model breakdown shows whether the change had the expected cost impact.
