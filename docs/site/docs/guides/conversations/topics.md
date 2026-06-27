---
sidebar_position: 5
title: Topics (group conversations)
---

# Topics

A **topic** turns the agent into a shared, multi-person workspace. Where a normal conversation is just you and the agent, a topic has a **roster of participants** and can hold **several conversations** under one umbrella — everyone in the topic can see and join every conversation inside it.

Use a topic when more than one person needs to work with the same agent on the same thing: a project channel, a team investigation, an on-call room, or a group chat driven from an IM platform.

## Topic vs. conversation

| | Conversation | Topic |
|---|---|---|
| Participants | Just you | Many (you + invited members) |
| Contains | One message thread | One or more conversations |
| Sidebar | A standalone row | A group node with its conversations nested under it |
| Sandbox | Your personal sandbox | A shared sandbox (see [The shared sandbox](#the-shared-sandbox)) |

A topic with only one participant behaves like a normal 1:1 chat until you add someone.

## Creating a topic

There are three ways a topic comes into being. However it is created, the creator becomes its **owner**.

### New Topic

Click **New Topic** in the sidebar. In the dialog, give it a title, invite workspace members, and choose the [sandbox mode](#the-shared-sandbox). CubeBox creates the topic with a first conversation and drops you into it.

:::info 📸 Screenshot placeholder
**Capture:** The "New Topic" dialog with a title entered, two members selected, and the sandbox-mode choice visible.
**Asset:** `/img/conversations/topic-create-dialog.png`
:::

### Upgrade an existing conversation

Already deep in a 1:1 conversation that should become a group effort? Use **Upgrade to topic** from the conversation header. The existing conversation becomes the first conversation under a new topic, and you become its owner.

You cannot upgrade a conversation that is already part of a topic, or one that is wired to an IM bot, a scheduled task, or an event trigger — detach those first.

### From an IM bot

When a bot is bound to a group chat in **topic** or **shared** routing mode, CubeBox creates a topic automatically the first time a message arrives in that chat. These IM-created topics carry the originating platform and channel as metadata. See [IM Connectors](../im/overview.md).

:::note Web topics and IM topics don't cross over (current limitation)
The IM runner only drives topics that originated from IM. A topic you create in the web app cannot currently be answered from an IM channel.
:::

## Participants and roles

Every participant has one of two roles:

- **Owner** — the creator. Manages the roster, the title, and the topic's lifecycle.
- **Member** — can read and post in every conversation under the topic, and start new conversations in it.

| Action | Who can do it |
|---|---|
| Invite participants | Owner only |
| Leave the topic | Any participant (yourself) |
| Remove another participant | Owner only |
| Transfer ownership / change a role | Owner only |
| Rename the topic | Owner only |
| Pin / unpin the topic | Any participant |
| Archive the topic | Owner only |
| Start a new conversation in the topic | Any participant |

Invited people must already be members of the workspace — inviting them to a topic does not add them to the workspace or create an account. A topic is only visible to its participants; everyone else in the workspace cannot see it.

If an owner leaves or is removed, ownership automatically passes to the earliest-joined remaining participant, so a topic is never left ownerless.

**Participant limit:** a topic holds up to **20** participants by default. Topics created from a shared IM channel allow up to **100**.

:::info 📸 Screenshot placeholder
**Capture:** The topic members panel showing the participant list with the owner badge, the Invite button (owner view), and the Leave action.
**Asset:** `/img/conversations/topic-members-panel.png`
:::

## The shared sandbox

Because a topic is shared, its [code-execution sandbox](../../admin/sandbox.md) is shared too. You pick how that works when you create the topic, and **it cannot be changed afterward** — choose deliberately:

- **Dedicated topic sandbox** — CubeBox spins up a **fresh sandbox that belongs to the topic**. It gets its own isolated storage, separate from anyone's personal sandbox and from every other topic. Files from the conversation you started in are **not** carried over. This is the default for **New Topic** and the safer choice for a group.
- **Reuse the creator's sandbox** — the topic runs in the **owner's personal sandbox**. Files and environment carry over, which is convenient when you're upgrading your own conversation and want to keep its working files. The trade-off: **every participant's code runs in the owner's environment**, with access to whatever is in it. This is the default when you **upgrade** an existing conversation.

:::caution
In "reuse the creator's sandbox" mode, other members' actions execute inside your personal sandbox. Only use it with people you'd trust with your own environment.
:::

You can see and manage every sandbox you own — including a topic's dedicated sandbox — from the workspace [Sandboxes](./sandboxes.md) settings tab.

## Conversations inside a topic

A topic starts with one conversation, but any participant can open more — for example, one conversation per sub-task. All of them are visible to every participant and appear nested under the topic in the sidebar. There is no separate "topic page"; you work inside the topic's conversations just like any other chat.

**Who sent each message.** In a shared topic, every message is tagged with the participant who sent it, so you can tell contributors apart at a glance, and the agent is told who is speaking so it can keep track. In a plain 1:1 chat this tag is hidden — there's only you. When you **upgrade** a 1:1 conversation into a topic, messages you send from then on carry your name, so the new participants can see who said what.

## Ordering, pinning, and archiving

- **Ordering.** Topics and standalone conversations share one sidebar list, ordered by most recent activity. A topic floats up whenever any conversation inside it gets a new message.
- **Pinning.** Any participant can pin a topic to keep it at the top. Pinning is shared — it affects the topic's position for everyone. (Pinning an individual conversation lifts just that conversation to the top, even out of its topic.)
- **Archiving.** An owner can archive a topic from its sidebar menu. Archived topics disappear from the sidebar for all participants. Individual members who just want out can **leave** the topic from the members panel instead.

## Topics as automation destinations

A scheduled task can target a topic, so a recurring run posts into a shared group instead of a private conversation. See [Scheduled Tasks](../automation/scheduled-tasks.md).

## Tips

- **Pick the sandbox mode on purpose.** It is the one topic setting you can't change later. Default to a dedicated sandbox unless you specifically need the creator's files.
- **Upgrade, don't recreate.** If a 1:1 chat grows into team work, upgrade it — you keep the history and (optionally) the working files.
- **Use separate conversations for separate threads.** One topic, many conversations keeps a group's parallel workstreams readable instead of interleaved in a single thread.
