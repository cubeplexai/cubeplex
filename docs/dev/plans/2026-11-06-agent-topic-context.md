# Agent Topic Context Awareness — Implementation Plan

**Status:** Proposed; ready for implementation

**Date:** 2026-11-06

**Spec:** [Agent Topic Context Awareness Specification](../specs/2026-11-06-agent-topic-context-design.md)

**Issue:** [#738](https://github.com/cubeplexai/cubeplex/issues/738) (Refs #738)

**Goal:** Ensure the agent is aware of its parent Topic ID and Title in conversations that belong to a Topic, enabling seamless project-level domain understanding and tool alignment (schedules and triggers) without violating Prompt Cache Discipline.

---

## 1. Scope & Technical Overview

- **Target Systems:** Backend (`RunContext`, `conversations.py`, `run_manager.py`), Message Context Transformation.
- **Cache Discipline Constraints:** **Zero changes to `BASE_SYSTEM_PROMPT` or `effective_system_prompt`**. Dynamic injection occurs exclusively in the turn message stream and is persisted in `UserMessage.metadata["topic_context"]`.
- **Sanitization & Anti-Injection:** Full XML entity escaping (`<`, `>`, `&`, `"`, `'`) and newline collapsing on `topic_title` to prevent tag breakout or attribute manipulation.
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
   - Update internal query/helper to return `(sandbox_mode, topic_creator_user_id, topic_title)`.
   - Update `_resolve_topic_run_context(...)` signature and return tuple to include `topic_title: str | None`.
3. In `send_message` and steer endpoints in `conversations.py`:
   - Unpack `topic_title` and pass it when constructing `RunContext`.
4. In background/trigger/scheduler restoration paths in `run_manager.py`:
   - Where `Topic` is fetched by `conversation.topic_id` (e.g. `await session.get(Topic, conversation.topic_id)`), populate `RunContext.topic_title = topic.title`.

---

### Milestone 2: Sanitization Helper & Dynamic Context Injection

**Files to modify/create:**
- `backend/cubeplex/agents/topic_context.py` (new helper module)
- `backend/cubeplex/agents/convert.py`
- `backend/cubeplex/streams/run_manager.py`

**Tasks:**
1. **Sanitization & Rendering Helper (`cubeplex/agents/topic_context.py`):**
   - Implement pure functions:
     ```python
     def sanitize_topic_title(title: str) -> str:
         # 1. Normalize whitespace & collapse newlines/tabs
         # 2. Hard truncate to 255 chars
         # 3. XML entity escape (<, >, &, ", ')
         ...

     def render_conversation_context(topic_id: str, topic_title: str) -> str:
         # Output standard self-describing XML block:
         # <conversation_context topic_id="..." topic_title="..." />
         ...
     ```
   - No prompt bloat: modern LLMs natively parse XML blocks; no meta-instructions added to `BASE_SYSTEM_PROMPT`.
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
   - In message preparation / context transform pipeline, prepend rendered `<conversation_context>` block to the user message.
   - Ensure historical replay idempotency: replay reads stored `metadata["topic_context"]` verbatim.

---

### Milestone 3: Testing & Prompt Cache Verification

Per `docs/testing.md` standards:

**Files to create/modify:**
- `backend/tests/unit/streams/test_topic_context.py` (Pure unit tests)
- `backend/tests/e2e/test_conversation_topic_context.py` (E2E / DB / API integration)
- `backend/tests/e2e/memory/test_prompt_cache.py` (Prompt cache regression gate)

**Tasks:**
1. **Unit Test (`backend/tests/unit/streams/test_topic_context.py`):**
   - Pure function tests for `sanitize_topic_title` and `render_conversation_context`.
   - Test injection attacks:
     - Embedded closing tags: `</conversation_context>`
     - Malicious newlines / fake XML attributes: `\n<fake_tag>`
     - Special characters: `&`, `<`, `>`, `"`, `'`
     - Excessively long titles (> 255 chars).
2. **E2E Integration Test (`backend/tests/e2e/test_conversation_topic_context.py`):**
   - Uses real `AsyncSession` and FastAPI client.
   - Verify `_resolve_topic_run_context` resolution on topic vs non-topic conversations.
   - Verify `POST /api/v1/conversations/{id}/messages` produces a run whose user message contains `metadata["topic_context"]` and the rendered turn context block.
3. **Prompt Cache Regression Test (`backend/tests/e2e/memory/test_prompt_cache.py`):**
   - Run `pytest backend/tests/e2e/memory/test_prompt_cache.py` to confirm that `effective_system_prompt` remains 100% byte-identical across conversations in different topics and non-topic conversations.

---

## 3. Verification & Review Checklist

- [ ] `RunContext` correctly propagates `topic_title`.
- [ ] Base System Prompt is untouched (zero tokens wasted, zero cache impact).
- [ ] Topic title is strictly sanitized against prompt injection and newline breakouts.
- [ ] Standalone (non-topic) 1:1 and group conversations remain unaffected with zero overhead.
- [ ] Unit tests pass in `backend/tests/unit/`.
- [ ] DB/API integration tests pass in `backend/tests/e2e/`.
