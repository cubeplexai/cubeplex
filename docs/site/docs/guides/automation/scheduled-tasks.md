---
sidebar_position: 1
title: Scheduled Tasks
---

# Scheduled Tasks

Scheduled tasks let you run agent prompts automatically on a recurring or one-time basis -- no manual interaction needed. Each fire creates a normal agent run in the target conversation, attributed to the task owner.

## Creating a scheduled task

Navigate to your workspace and open **Scheduled Tasks** from the sidebar. Click **New Task** and fill in:

| Field | Description |
|---|---|
| **Name** | A descriptive label (e.g., "Weekly issue digest"). |
| **Schedule** | When the task fires. See [schedule types](#schedule-types) below. |
| **Prompt** | The message sent to the agent on each fire (e.g., "Summarize all GitHub issues opened this week"). |
| **Conversation** | Which conversation to run in. See [conversation options](#conversation-options) below. |

## Schedule types

CubeBox supports three schedule kinds:

### Cron expression

Standard five-field cron syntax. Use this for precise recurring schedules.

| Expression | Meaning |
|---|---|
| `0 9 * * 1-5` | Every weekday at 9:00 AM |
| `0 0 1 * *` | First day of each month at midnight |
| `*/15 * * * *` | Every 15 minutes |
| `0 18 * * 5` | Every Friday at 6:00 PM |

### Fixed interval

Run every N minutes or hours. Simpler than cron when you just need a regular cadence.

**Examples:** every 30 minutes, every 2 hours, every 6 hours.

The first fire happens one interval after you create (or resume) the task.

### One-shot

Run once at a specific future date and time. The task moves to a completed state after it fires.

**Example:** "Run at 2025-03-15 14:00 UTC" to generate a quarterly report on a specific date.

## Conversation options

You control where each fire's agent run happens:

- **Fixed conversation** -- every fire runs in the same conversation. Context accumulates across fires, so the agent can reference previous runs. Good for rolling summaries or monitoring tasks.
- **New conversation per fire** -- each fire creates a fresh conversation. The agent starts with no prior context. Good for independent tasks like one-off reports.

## Pause, resume, and missed runs

You can **pause** a task at any time. While paused, no fires occur. Click **Resume** to reactivate it.

When a task is paused (or the system is temporarily unavailable) and one or more scheduled fires are missed, the **missed-run policy** determines what happens on resume:

| Policy | Behavior |
|---|---|
| **Skip** | Discard all missed fires. The next fire happens at the next scheduled time. |
| **Run latest** | Execute the most recent missed fire immediately, then resume the normal schedule. Earlier missed fires are discarded. |

## Run history

Every fire is recorded in the task's run history. Each entry shows:

- **Scheduled time** -- when the fire was supposed to happen.
- **Actual time** -- when the fire actually started.
- **Run ID** -- links to the agent run in the target conversation.
- **Outcome** -- success, failure, or skipped.

Use run history to audit what the agent did and when, or to debug tasks that are not behaving as expected.

## Example: weekly issue digest

**Goal:** Every Monday at 9 AM, summarize last week's GitHub issues in the #engineering workspace.

1. Go to **Scheduled Tasks** and click **New Task**.
2. Name it "Weekly GitHub issue digest".
3. Set the schedule to **Cron expression**: `0 9 * * 1`.
4. Set the prompt:
   > Summarize all GitHub issues opened in the last 7 days. Group by repository, highlight any critical or blocking issues, and note issues that have been open for more than 3 days without a response.
5. Choose a **fixed conversation** so the agent can reference previous weeks' summaries.
6. Set missed-run policy to **Run latest** so you still get a summary if Monday is a holiday.
7. Save and the task starts on the next Monday at 9 AM.

## Example: daily database health check

**Goal:** Every 6 hours, run a health check on your production database.

1. Create a new task named "DB health check".
2. Set the schedule to **Fixed interval**: every 6 hours.
3. Set the prompt:
   > Check the database connection pool usage, slow query log from the last 6 hours, and table sizes. Flag anything unusual.
4. Choose a **fixed conversation** so trends are visible across runs.
5. Set missed-run policy to **Skip** -- if a check is missed, the next one will catch any issues.

## Tips

- **Keep prompts specific.** The agent has no memory of why the task exists beyond what you write in the prompt. Include context about what to check, where to report, and what format to use.
- **Use fixed conversations for continuity.** When the agent can see its own previous runs, it can track trends and flag changes ("disk usage grew 12% since last check").
- **Use new conversations for isolation.** When each run is independent and you do not want accumulated context to influence the agent's behavior.
- **Start with a manual test.** Before scheduling, paste your prompt into a regular conversation to verify the agent produces the output you expect.
