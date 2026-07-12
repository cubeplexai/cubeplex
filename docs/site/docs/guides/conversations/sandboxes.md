---
sidebar_position: 6
title: Managing Sandboxes
---

# Managing Sandboxes

A sandbox is the isolated environment where the agent runs code for a conversation or topic. Each sandbox has its own files and installed packages, and CubePlex keeps it around across container restarts — so the working files from a conversation are still there the next time you open it. The **Sandboxes** tab in your workspace settings is where you see every sandbox that belongs to you in this workspace and take action on one when something goes wrong.

:::info 📸 Screenshot placeholder
**Capture:** Workspace settings → Sandboxes tab, showing a list of sandbox rows. Each row has a status badge (Running / Off / Paused / Failed), a scope label (e.g. "Your workspace sandbox", "Topic: release-plan"), last-active time, and Restart + Delete buttons.
**Asset:** `/img/conversations/sandboxes-panel.png`
:::

## Opening the Sandboxes tab

Go to your workspace and open **Settings** → **Sandboxes**. The list shows only sandboxes that belong to you in this workspace. To see every member's sandboxes, an org admin uses the admin-level sandbox observability view instead (see [Sandbox administration](../../admin/sandbox.md)).

## What each row shows

Every active sandbox you own gets one row, regardless of whether its container is currently running:

- **Status** — a badge showing the sandbox's runtime state (see below).
- **Scope label** — which conversation or topic the sandbox belongs to:
  - **Your workspace sandbox** — your personal sandbox, used by 1:1 conversations that aren't part of a topic.
  - **Group chat: {title}** — a standalone group conversation (not in a topic).
  - **Topic: {title}** — a sandbox belonging to a [topic](./topics.md).
  - **(deleted)** — the conversation or topic that owned this sandbox has been deleted. The sandbox row remains so you can clean it up.
- **Last active** — when the agent last ran code in it.
- **Restart** and **Delete** — the two actions (see below).

A sandbox with status **Off** (container stopped) still appears in the list. That is intentional: the sandbox's files are still on disk, and it will start back up the next time you send a message in its conversation. You do not need to restart it manually before chatting.

## Restart

**Restart** stops the sandbox's container but keeps the sandbox row and all of its files. The status flips to **Off**, and the next time the agent needs to run code in that conversation a fresh container starts up on the same storage, with all your files intact.

Use Restart when the container is in a bad state — a hung process, a broken shell, or a runaway command you want to cut short — but you want to keep the working files.

## Delete

**Delete** permanently removes the sandbox. The row is soft-deleted, the container is stopped, and the sandbox will not start again for that conversation or topic. The next time you send a message there, CubePlex provisions a brand-new sandbox with empty storage.

:::caution
Delete cannot be undone. Stored files are left on disk for your operator to reclaim (CubePlex cannot delete the underlying storage directly). If you only want a fresh container while keeping your files, use **Restart** instead.
:::

## Sandbox statuses

The badge on each row reflects the sandbox's runtime state:

| Badge | Meaning |
|---|---|
| **Running** | The container is up and ready to execute code. |
| **Starting** | A container is being provisioned. This is transient — it becomes Running shortly. |
| **Paused** | The container is paused (idle for a long time). It resumes automatically on next use. |
| **Pausing** / **Resuming** | Transitional states while pausing or resuming. |
| **Stopping** | The container is being stopped after a Restart or Delete action. |
| **Off** | The container is stopped, but the sandbox row and its files are still around. It starts again on next use. |
| **Failed** | The last provisioning attempt failed. Use **Restart** to try again, or **Delete** to clear it. |

## Storage isolation

Each sandbox gets its own isolated storage — files in one sandbox are never visible to another. This holds for the [shared sandboxes in topics](./topics.md) too: a topic with the **Dedicated topic sandbox** mode gets a fresh sandbox with its own storage, separate from the creator's personal sandbox and from every other topic. Files from the conversation you upgraded are **not** carried over into a dedicated topic sandbox.

## When sandboxes appear and disappear

- A sandbox row is created the first time the agent runs code in a conversation or topic that doesn't already have one.
- The row stays in the list until you **Delete** it (or until its owning conversation/topic is deleted and you clean up the orphaned row).
- Stopping, pausing, or restarting a container does **not** remove the row — only Delete does.

## Tips

- **Restart before Delete.** If a sandbox is misbehaving but you want to keep its files, Restart it. Reach for Delete only when you genuinely want a clean slate.
- **Clean up orphaned rows.** If a row shows **(deleted)** as its scope, the conversation or topic that owned it is gone. Delete the row to stop paying for an idle container.
- **You can only manage your own.** Each member sees only their own sandboxes. To audit sandbox usage across the workspace, an org admin uses the admin observability view (see [Sandbox administration](../../admin/sandbox.md)).
