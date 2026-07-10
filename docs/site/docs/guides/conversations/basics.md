---
sidebar_position: 1
title: Conversation Basics
---

# Conversation Basics

Conversations are where you interact with the AI agent. Each conversation is a persistent message thread scoped to your workspace, with full context carried across turns.

## Starting a conversation

When you open a workspace, you land on the home page with an empty input bar. Type a message and press **Enter** (or click the send button) to start a new conversation. CubeBox creates the conversation automatically and routes you to it.

You can also click **New Chat** in the sidebar to start fresh at any time.

## The conversation interface

The layout has three main areas:

- **Sidebar** (left) — lists your recent conversations, workspace navigation (skills, tools, memory, settings), and workspace switcher.
- **Chat area** (center) — the message thread and input bar.
- **Side panel** (right, opens on demand) — previews artifacts, attachment images, tool call details, or the sandbox browser.

:::info 📸 Screenshot placeholder
**Capture:** The full conversation view with the three areas labeled — sidebar (left), chat thread (center), and the side panel open on the right showing an artifact preview.
**Asset:** `/img/conversations/conversation-layout.png`
:::

The input bar sits at the bottom. It includes:

- **Model preset picker** — choose which model to use for this message.
- **Thinking control** — set the reasoning depth (Off, Low, Medium, High, Max).
- **Attach button** (paperclip icon) — add files to your message.
- **Text area** — type your message. Press **Enter** to send, **Shift+Enter** for a new line.
- **Send / Stop button** — sends the message, or stops a running response.

## What the agent can do

During a conversation, the agent can:

- **Respond with text** — standard conversational replies with markdown formatting.
- **Think** — show its reasoning process in a collapsible "thinking" block (when thinking is enabled).
- **Call tools** — invoke MCP connectors to reach external services (databases, APIs, SaaS products).
- **Execute code** — run commands in a sandboxed environment.
- **Generate artifacts** — produce downloadable files, live website previews, images, data files, and more. See [Artifacts](./artifacts.md).
- **Save memories** — store facts, preferences, or decisions for future conversations. See [Memory overview](../memory/overview.md).

## Steering and stopping

While the agent is responding, you can:

- **Stop the response** — click the stop button (square icon) to cancel the current response.
- **Steer mid-stream** — type a message while the agent is still responding and press Enter. This sends a "steer" instruction that redirects the agent without waiting for it to finish.

When the agent needs your input (e.g., a confirmation before proceeding), the input bar locks until you respond to the prompt card that appears in the chat.

## Managing conversations

Right-click (or click the three-dot menu) on any conversation in the sidebar to access these actions:

- **Rename** — give the conversation a descriptive title. By default, CubeBox auto-generates a title from your first message.
- **Pin** — pinned conversations stick to the top of the sidebar list so you can find them quickly.
- **Unpin** — remove a conversation from the pinned section.
- **Delete** — soft-deletes the conversation. It disappears from the sidebar but its data (messages, artifacts, cost records) is preserved internally.

## Auto-generated titles

When you send the first message in a conversation, CubeBox uses the LLM to generate a short title summarizing the topic. If you prefer a custom title, rename the conversation from the sidebar menu.

## Using prior work

The agent can search conversations in your current workspace and review artifacts that are
visible to you. By default, it reads a small window of recent turns from a conversation so it
can quickly understand the relevant context. When needed, it can retrieve detailed historical
tool output for a specific result.

This access is read-only: the agent cannot delete artifacts through it.

## Tips

- **Use pinning for active projects.** Pin conversations you return to frequently so they stay visible regardless of how many new chats you start.
- **One topic per conversation.** The agent uses the full conversation history as context. Mixing unrelated topics in one thread dilutes that context and may produce less relevant responses.
- **Steer instead of stopping.** If the agent is heading in the wrong direction, steer it with a follow-up message rather than stopping and restarting. This preserves the context the agent has already built up.
- **Check the thinking block.** When reasoning is enabled, expand the thinking block to understand how the agent arrived at its answer. This is especially useful for complex tasks.
