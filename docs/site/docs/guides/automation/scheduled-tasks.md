---
sidebar_position: 1
title: Scheduled Tasks
---

# Scheduled Tasks

Scheduled tasks let you run agent prompts automatically on a recurring or one-time basis — no manual interaction needed. Each fire creates a normal agent run in the target conversation, attributed to the task owner.

## Creating a scheduled task

Open your workspace and go to **Scheduled Tasks** from the sidebar. Click **New task** and fill in:

| Field | Description |
|---|---|
| **Name** | A descriptive label (e.g., "Daily digest"). |
| **Prompt** | The message sent to the agent on each fire (e.g., "Summarize all GitHub issues opened this week"). |
| **Schedule** | How often the task fires. See [schedule types](#schedule-types) below. |
| **Conversation target** | Which conversation each fire runs in. See [conversation options](#conversation-options) below. |

:::info 📸 Screenshot placeholder
**Capture:** The "New scheduled task" dialog with name, prompt, the frequency pills (Daily / Weekly / Monthly / Every… / Once), and the conversation target radio group all visible.
**Asset:** `/img/scheduled-tasks/new-task-dialog.png`
:::

## Schedule types

The schedule editor is a visual builder. Pick a **frequency**, then fill in the matching details:

### Daily, weekly, monthly

Run at a fixed time of day, on a recurring calendar pattern:

- **Daily** — every day at a chosen time (e.g., 09:00).
- **Weekly** — at a chosen time on one or more selected weekdays.
- **Monthly** — at a chosen time on a specific day of the month. Days 1–28 are offered to avoid skipping short months, plus a **Last day of month** option that adapts to each month's length.

Each of these sets a **Run time** and a **timezone** (defaulted to your browser's timezone, editable). Internally these are stored as cron expressions, but you configure them through the visual controls — you never have to write cron by hand.

### Every… (fixed interval)

Run every N minutes, hours, or days. Simpler than a calendar pattern when you just need a regular cadence.

**Examples:** every 30 minutes, every 2 hours, every 6 hours.

The minimum interval is 60 seconds. The first fire happens one interval after you create (or resume) the task.

### Once

Run a single time at a specific future date and time. After it fires, the task has no upcoming run.

**Example:** "Run on 2025-03-15 at 14:00" to generate a quarterly report on a specific date.

### End date

For recurring schedules (everything except **Once**), you can set an optional **End date** after which the task stops firing automatically. If unset, the task runs indefinitely.

## Conversation options

You control where each fire's agent run happens with the **Conversation target**:

- **New conversation each run** — each fire creates a fresh conversation. The agent starts with no prior context. Good for independent tasks like one-off reports. You can optionally pick a **Topic** to group these conversations together; new conversations inherit the topic's members and sandbox.
- **This conversation (fixed)** — every fire runs in one existing conversation you own. Context accumulates across fires, so the agent can reference previous runs. Good for rolling summaries or monitoring tasks.

A third mode, **IM channel**, posts results into a chat on a linked IM platform. This mode can't be selected here — it's created only from IM, by using a slash command inside the chat. See the IM integration guide for details.

The conversation target is fixed at creation time. You can still edit the name, prompt, and schedule afterward, but to change the destination you delete and recreate the task.

## Pause, resume, and missed runs

You can **pause** a task at any time from its detail panel or card menu. While paused, no fires occur and the status shows **Paused**. Click **Resume** to reactivate it.

When a task can't fire on time — because it was paused, or the scheduler was temporarily unavailable — missed occurrences are handled automatically; there is no per-task policy to configure:

- **Only the latest missed occurrence catches up.** On resume (or when the scheduler recovers), the task runs the most recent occurrence that was due, then continues on its normal schedule. Any earlier missed occurrences are recorded as **Skipped (missed)** rather than replayed, so a task that was down for a week doesn't suddenly fire seven backlogged runs.
- **Stale occurrences are skipped entirely.** If even the latest due occurrence is older than a short grace window, it is also recorded as **Skipped (missed)** and not run. This prevents a long-paused task from firing a run that is no longer timely.

## Run history

Every occurrence is recorded in the task's run history, shown in the task detail panel. Each entry shows:

- **Scheduled time** — when the occurrence was due to fire.
- **State** — one of:
  - **Claimed** — the occurrence was picked up and is about to start.
  - **Running** — the agent run is in progress.
  - **Succeeded** — the run completed.
  - **Failed** — the run errored.
  - **Skipped (missed)** — the occurrence was missed and not run (see [missed runs](#pause-resume-and-missed-runs)).
  - **Skipped (busy)** — the conversation was busy and the occurrence exhausted its retries.
- **Retry info** — if the occurrence was retried, the retry count and the next retry time.
- **View conversation** — a link to the agent run's conversation, when one was created.

The panel refreshes automatically every few seconds, and a **Refresh** button forces an immediate reload. Use run history to audit what the agent did and when, or to debug tasks that are not behaving as expected.

:::info 📸 Screenshot placeholder
**Capture:** The task detail panel showing the schedule summary, next-run line, Pause/Resume controls, and the run-history list with a mix of Succeeded and Skipped (missed) state badges.
**Asset:** `/img/scheduled-tasks/run-history.png`
:::

## Example: weekly issue digest

**Goal:** Every Monday at 9 AM, summarize last week's GitHub issues.

1. Go to **Scheduled Tasks** and click **New task**.
2. Name it "Weekly GitHub issue digest".
3. Set the prompt:
   > Summarize all GitHub issues opened in the last 7 days. Group by repository, highlight any critical or blocking issues, and note issues that have been open for more than 3 days without a response.
4. Set the frequency to **Weekly**, select **Mon**, and set the run time to **09:00** in your timezone.
5. Choose **This conversation** so the agent can reference previous weeks' summaries.
6. Click **Create task**. The task starts on the next Monday at 9 AM.

## Example: database health check

**Goal:** Every 6 hours, run a health check on your production database.

1. Create a new task named "DB health check".
2. Set the prompt:
   > Check the database connection pool usage, slow query log from the last 6 hours, and table sizes. Flag anything unusual.
3. Set the frequency to **Every…** and choose **6 hours**.
4. Choose **This conversation** so trends are visible across runs.
5. Click **Create task**.

If a check is ever missed, the task automatically picks up at the next due interval — only the most recent missed occurrence catches up, so you won't get a burst of stale checks.

## Tips

- **Keep prompts specific.** The agent has no memory of why the task exists beyond what you write in the prompt. Include context about what to check, where to report, and what format to use.
- **Use a fixed conversation for continuity.** When the agent can see its own previous runs, it can track trends and flag changes ("disk usage grew 12% since last check").
- **Use new conversations for isolation.** When each run is independent and you do not want accumulated context to influence the agent's behavior. Add a topic to keep those conversations grouped.
- **Start with a manual test.** Before scheduling, paste your prompt into a regular conversation to verify the agent produces the output you expect.
