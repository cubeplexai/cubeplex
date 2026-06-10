---
sidebar_position: 6
title: Cost Tracking
---

# Cost Tracking

CubeBox tracks LLM token usage across your organization so you can monitor spending and identify cost trends. The cost tracking dashboard is available at **Admin > Cost** (`/admin/cost`).

## What is tracked

Every LLM call made through CubeBox records:

- **Input tokens** — the number of tokens sent to the model (prompt, system instructions, tool results, etc.).
- **Output tokens** — the number of tokens the model generated in its response.
- **Model** — which model was used.
- **Timestamp** — when the call was made.

CubeBox multiplies token counts by the per-model cost rates you configured in [Model Management](./models.md) to calculate dollar amounts.

## Usage dashboard

The dashboard gives you a high-level view of your organization's AI spending.

### Spend over time

A time-series chart showing daily or weekly spend. Use this to spot trends — increasing usage after onboarding new team members, spikes from large batch tasks, or the impact of switching to a cheaper model.

### Per-model breakdown

A breakdown table showing token consumption and cost for each model. This helps you understand which models drive the most spend and whether cheaper alternatives might work for certain use cases.

## Tips for managing costs

- **Set accurate cost rates.** Make sure the input and output token rates in [Model Management](./models.md) reflect your actual provider pricing. Without accurate rates, the dashboard numbers will be misleading.
- **Review regularly.** Check the dashboard weekly to catch unexpected spikes early.
- **Right-size your models.** If a significant portion of spend comes from a high-cost model being used for simple tasks, consider guiding your team toward a lighter model for those use cases.
- **Use the per-model view to compare.** After switching models or adding a new one, the per-model breakdown shows whether the change had the expected cost impact.
