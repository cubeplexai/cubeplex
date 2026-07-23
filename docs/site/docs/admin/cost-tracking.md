---
sidebar_position: 6
title: Cost Tracking
---

# Cost Tracking

CubePlex tracks LLM token usage across your organization so you can monitor load and spending. The usage dashboard is available at **Admin > Insights** (`/admin/insights`; `/admin/cost` redirects there).

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

The dashboard defaults to **token** metrics so it stays useful even when model prices are not configured (costs would otherwise show as $0). Use the **Tokens | Cost** toggle in the top bar to switch to USD when you have set pricing under Models. Your last choice is remembered in the browser.

### Primary metric

| Mode | KPIs and charts | When to use |
| --- | --- | --- |
| **Tokens** (default) | Total / input / output tokens, avg tokens per call, stacked series ranked by tokens | Always meaningful; no pricing required |
| **Cost** | Total cost, avg $/call, series ranked by cost | After model prices are set |

If you switch to **Cost** while total cost is $0 but tokens are non-zero, the page shows a short hint linking to [Model Management](./models.md).

### Spend / usage over time

A time-series chart at **daily** or **weekly** granularity, broken down by **workspace**, **model**, or **user**. In tokens mode the axis is token totals; in cost mode it is USD. Use this to spot trends — increasing usage after onboarding, spikes from large batch tasks, or the impact of switching models.

### Breakdowns

Alongside the org-wide totals, the dashboard breaks usage down for the selected period:

- **By model** — which models drive the most traffic (or spend).
- **By workspace** — which teams or projects are busiest.
- **By user** — per-person usage.
- **Cache efficiency** — hit rate from token fields (token-based in both modes).

Each table row shows input/output (and cache where relevant) token counts, call count, and either a token total or cost depending on the active metric.

:::note KPI vs filters
Summary KPI tiles use **org-wide** totals for the date range. Workspace/model filters currently apply to timeseries charts. Matching filters on the summary is a known follow-up.
:::

### Export

You can export the raw cost data as CSV — for the whole organization or for a single workspace — to analyze it in a spreadsheet or feed it into your own reporting.

:::info 📸 Screenshot placeholder
**Capture:** The Admin > Insights dashboard with the Tokens | Cost toggle, token KPIs (or cost KPIs), stacked chart, and breakdown table.
**Asset:** `/img/admin/cost-dashboard.png`
:::

## Tips for managing costs

- **Set accurate cost rates.** Make sure the input and output token rates in [Model Management](./models.md) reflect your actual provider pricing. Without rates, cost mode shows $0; tokens mode still reflects real load.
- **Review regularly.** Check the dashboard weekly to catch unexpected spikes early.
- **Right-size your models.** If a significant portion of usage comes from a high-cost model on simple tasks, guide the team toward a lighter model for those cases.
- **Use the per-model view to compare.** After switching models or adding a new one, the per-model breakdown shows whether the change had the expected impact.
