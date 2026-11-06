# Agent Topic Context Awareness — Implementation Plan

**Status:** Proposed; ready for implementation

**Date:** 2026-11-06

**Spec:** [Agent Topic Context Awareness Specification](../specs/2026-11-06-agent-topic-context-design.md)

**Issue:** [#738](https://github.com/cubeplexai/cubeplex/issues/738)

**Goal:** Ensure the agent is aware of its parent Topic ID and Title in conversations that belong to a Topic, enabling seamless project-level domain understanding and tool alignment (schedules and triggers) without violating Prompt Cache Discipline.

---

## 1. Scope & Technical Overview

- **Target Systems:** Backend (`RunContext`, `conversations.py`, `run_manager.py`), Agent Prompt (`system.py`).
- **Cache Discipline Constraints:** No dynamic topic strings in `BASE_SYSTEM_PROMPT` or `effective_system_prompt`. Dynamic injection occurs in the turn message stream and is persisted in `UserMessage.metadata["topic_context"]`.
- **Database Migrations:** None required (`Topic.title` and `Conversation.topic_id` already exist).

---

## 2. Implementation Milestones

### Milestone 1: RunContext & Topic Metadata Resolution

**Files to modify:**
- `backend/cubeplex/streams/run_manager.py`
- `backend/cubeplex/api/routes/v1/conversations.py`

**Tasks:**
1. In `RunContext`:
   - Add field `topic_title: str | None = None`.
2. In `_resolve_topic_run_context`:
   - Update `_read(topic_session)` to return `(sandbox_mode, topic_creator_user_id, topic_title)`.
   - Update `_resolve_topic_run_context(...)` signature and return value to include `topic_title: str | None`.
3. In `send_message` and steer endpoints in `conversations.py`:
   - Unpack `topic_title` and pass it when constructing `RunContext`.
4. In background/trigger/scheduler restoration paths in `run_manager.py`:
   - Where `Topic` is fetched by `conversation.topic_id` (e.g. `await session.get(Topic, conversation.topic_id)`), populate `RunContext.topic_title = topic.title`.

---

### Milestone 2: Prompt Guidelines & Dynamic Context Injection

**Files to modify:**
- `backend/cubeplex/prompts/system.py`
- `backend/cubeplex/middleware/` (or `agents/convert.py` / `run_manager.py`)

**Tasks:**
1. **Base System Prompt Update (`system.py`):**
   - Add a static, cache-stable explanation section to `BASE_SYSTEM_PROMPT`:
     ```markdown
     ## Conversation Context

     When a message contains a `<conversation_context>` block:
     - `topic_title` indicates the overall project or operational stream for this conversation.
     - Use this context to align domain understanding without requiring the user to restate the overall project goal.
     - Built-in tools that inherit the topic (such as `create_scheduled_task` or `create_trigger`) automatically attach to this topic.
     ```
2. **Metadata Attachment:**
   - When converting incoming prompt input into `cubeloop.UserMessage` (in `convert.py` / `run_manager.py`), attach topic metadata if `ctx.topic_id` and `ctx.topic_title` are present:
     ```python
     if ctx.topic_id and ctx.topic_title:
         metadata["topic_context"] = {
             "topic_id": ctx.topic_id,
             "topic_title": ctx.topic_title,
         }
     ```
3. **Turn Message Context Transformation:**
   - Inject the `<conversation_context>` block into the user message before dispatch to the provider.
   - Format:
     ```markdown
     <conversation_context>
     topic_id: "top-1a2b3c4d5e"
     topic_title: "Website i18n Refactor"
     </conversation_context>
     ```
   - Ensure historical replay idempotency so cached past turns retain identical byte sequences.

---

### Milestone 3: Testing & Prompt Cache Verification

**Test Files:**
- `backend/tests/unit/streams/test_topic_context.py` (new)
- `backend/tests/api/v1/test_conversations.py`
- `backend/tests/e2e/memory/test_prompt_cache.py`

**Tasks:**
1. **Unit Test:**
   - Test `_resolve_topic_run_context` with non-topic conversation -> returns `(None, ..., None)`.
   - Test with topic conversation -> returns `(topic_id, ..., topic_title)`.
2. **Turn Context Test:**
   - Verify `UserMessage` contains `metadata["topic_context"]` and the rendered turn context block.
3. **Prompt Cache Regression Test:**
   - Run `pytest backend/tests/e2e/memory/test_prompt_cache.py` to confirm that `effective_system_prompt` does not change across conversations with different topics.

---

## 3. Verification & Review Checklist

- [ ] `RunContext` correctly propagates `topic_title`.
- [ ] Base System Prompt contains static guideline block only.
- [ ] No topic metadata leaks into `effective_system_prompt`.
- [ ] Standalone (non-topic) 1:1 and group conversations remain unaffected.
- [ ] All unit, integration, and prompt-cache tests pass cleanly.
