# Agent Topic Context Awareness Specification

**Status:** Proposed Specification

**Date:** 2026-11-06

**Scope:** Backend (`RunContext`, context resolution, message transformation), Agent Prompt Context

**Issue:** [#738](https://github.com/cubeplexai/cubeplex/issues/738)

**Related Designs:**
- [Prompt Cache Discipline](../../../backend/docs/prompt-cache-discipline.md)
- [Workspace Projectization (M4)](2026-05-06-m4-workspace-projectization-design.md)
- [Schedule & Trigger Destinations](2026-06-23-schedule-trigger-destinations-design.md)
- [Memory Workspace & Personal Scopes](2026-07-26-memory-workspace-personal-design.md)

---

## 1. Executive Summary & Motivation

In CubePlex, **Topics** organize conversations, tasks, and state around distinct projects, workstreams, or operational targets. Each conversation optionally belongs to a Topic via `conversation.topic_id`. Backend execution tracking already records `RunContext.topic_id`, and activities bump the parent topic (`_bump_topic_activity`).

However, the agent itself remains entirely blind to its topic affiliation:
1. **Context Blindness:** When a user initiates a conversation under a Topic titled *"Website i18n Refactor"* or *"Twitter Bot Operations"*, the LLM is given no awareness of this high-level umbrella. The user is forced to repeatedly restate high-level domain context that was already captured when naming or structuring the Topic.
2. **Tool Inconsistency:** Built-in tools like `create_scheduled_task` and `create_trigger` state in their tool docstrings:
   > *"Inherited from the current conversation's topic when omitted."*
   Yet when an agent reasons about creating a schedule or trigger, it cannot explain to the user which topic the task will attach to because it does not know the current topic's title or ID.

This specification defines how the active Topic identity is resolved, sanitized, and injected into the agent's turn context while strictly adhering to **Prompt Cache Discipline**.

---

## 2. Core Architectural Principles & Prompt Cache Discipline

### 2.1 The Prompt Cache Anti-Pattern to Avoid
CubePlex enforces strict prompt-cache rules (see `backend/docs/prompt-cache-discipline.md`):
- The **Stable Prefix** (Base system prompt, sorted tool definitions, pinned memory) must be **byte-identical** across turns and conversations to maintain high cache-hit ratios on Anthropic, OpenAI, and DeepSeek.
- **Rule:** Dynamic per-conversation or per-topic strings (such as topic title, topic ID, or current timestamps) **must never be concatenated into the base system prompt** (`BASE_SYSTEM_PROMPT` or `effective_system_prompt`).
- Placing topic metadata into the system prompt would fragment the cache across different topics in the same workspace and invalidate the cache whenever a topic is renamed.

### 2.2 The Turn-Context Injection Pattern
Dynamic context items that vary per conversation or turn (such as relevance memory snapshots, attachments, and turn metadata) are injected into the message stream:
- Specifically, dynamically prepended or appended to `UserMessage` during `transform_context` (or when constructing the initial user message payload).
- Recorded deterministically in `UserMessage.metadata` so that history replay reproduces the exact byte sequence across subsequent turns.

---

## 3. Detailed Architecture & Design

### 3.1 Data Flow

```
User Message Request (POST /conversations/{id}/messages)
   │
   ├── 1. _resolve_topic_run_context(conversation, ...)
   │      Resolves: (topic_id, topic_title, is_group_chat, ...)
   │
   ├── 2. RunContext instantiated with topic_id & topic_title
   │
   ├── 3. UserMessage creation (wire_input_to_cubeloop_user_message)
   │      - If topic_id is present:
   │        Stores {"topic_id": ..., "topic_title": ...} in UserMessage.metadata["topic_context"]
   │
   └── 4. Context Transformation (transform_context)
          - Renders <conversation_context> block prepended/appended to UserMessage text
          - Agent receives clear, structured metadata without breaking system-prompt caching
```

### 3.2 Context Schema & Representation

When a conversation belongs to a topic, the context block is rendered as:

```markdown
<conversation_context>
topic_id: "top-1a2b3c4d5e"
topic_title: "Website i18n Refactor"
</conversation_context>
```

#### Guidelines for the Agent:
The base system prompt (`BASE_SYSTEM_PROMPT`) will include a single, static, cache-stable section explaining the block:

```markdown
## Conversation Context

When a message contains a `<conversation_context>` block:
- `topic_title` indicates the high-level project or operational workstream for this conversation.
- Use this context to align domain understanding without requiring the user to restate the overall project goal.
- Built-in tools that inherit the topic (such as `create_scheduled_task` or `create_trigger`) automatically attach to this topic.
```

Because this instructional clause is completely static, it lives in the cache-eligible prefix and incurs zero per-topic cache misses.

### 3.3 Standalone (Non-Topic) Conversations
If `conversation.topic_id` is `None`:
- `topic_id` and `topic_title` remain `None`.
- No `<conversation_context>` block is injected.
- The message content remains completely unmodified.

---

## 4. Component Changes

### 4.1 Backend Models & Contexts

1. **`cubeplex.streams.run_manager.RunContext`**:
   - Add field:
     ```python
     topic_title: str | None = None
     ```

2. **`cubeplex.api.routes.v1.conversations._resolve_topic_run_context`**:
   - Currently queries `TopicRepository.get(topic_id)`.
   - Update return tuple to include `topic_title: str | None`.
   - Extract `topic_obj.title` when `topic_obj` is resolved.

3. **Background / Scheduled / Trigger Execution Paths**:
   - Ensure `run_manager.py` loaders that construct `RunContext` from DB (e.g. background tasks or trigger runs) also load and attach `topic.title` when `conversation.topic_id` is set.

### 4.2 Message Context Injection

1. **Metadata Assignment**:
   - In `convert.py` / `run_manager.py` where incoming user messages are converted into `cubeloop.UserMessage`:
     ```python
     if ctx.topic_id and ctx.topic_title:
         metadata["topic_context"] = {
             "topic_id": ctx.topic_id,
             "topic_title": ctx.topic_title,
         }
     ```

2. **Rendering Middleware / Context Transformer**:
   - Render the `<conversation_context>` block into the user message turn.
   - Idempotent and deterministic: during replay, `metadata["topic_context"]` ensures historical turns retain the exact topic title captured at that turn, preserving cache stability during multi-turn sessions even if the topic is subsequently renamed.

---

## 5. Security & Isolation

- **Workspace Scoping**: The Topic title is loaded using the caller's scoped `org_id` and `workspace_id`.
- **RBAC**: Access to the topic is governed by existing topic participation and workspace membership checks.
- **Content Sanitization**: Topic titles are sanitized (XML/bracket escaping or standard key-value rendering) to prevent prompt injection from malicious topic names.

---

## 6. Testing Strategy

1. **Unit Tests**:
   - `test_resolve_topic_run_context`: Verify `topic_title` is correctly resolved for topic conversations and `None` for non-topic conversations.
   - `test_topic_context_injection`: Verify `<conversation_context>` formatting and metadata persistence in `UserMessage`.
2. **Cache Stability Regression Tests**:
   - Run `tests/e2e/memory/test_prompt_cache.py` to ensure the system prompt byte stream remains identical across multiple topics.
3. **Replay Tests**:
   - Verify that resuming or streaming existing conversation turns reproduces the byte-identical turn text.
