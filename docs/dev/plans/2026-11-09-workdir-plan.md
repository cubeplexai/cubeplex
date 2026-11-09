# Topic Default Working Directory & Per-Conversation CWD — Implementation Plan

**Status:** Proposed; ready for implementation

**Date:** 2026-11-09

**Spec:** [Topic Default Working Directory & Per-Conversation CWD Specification](../specs/2026-11-09-workdir-design.md)

**Issue:** [#739](https://github.com/cubeplexai/cubeplex/issues/739) (Refs #739)

**Goal:** Provide first-class working directory (`workdir`) configuration on Topics and per-conversation CWD overrides, dispatching sandbox commands directly in the resolved directory without manual `cd` boilerplate while strictly preserving sandbox isolation and Prompt Cache Discipline.

---

## 1. Scope & Technical Overview

- **Database Models & Migrations:** Add `workdir: str | None` to `Topic` and `Conversation` models; write Alembic migration.
- **Backend API & Validation:** Add path validation helper ensuring `workdir` is an absolute, normalized path strictly under `/workspace`. Expose field in Topic/Conversation CRUD and message send endpoints.
- **Sandbox Execution Dispatch:** Update `RunContext`, `SandboxMiddleware`, and `OpenSandbox.execute` to accept and execute commands inside the resolved `workdir`. Automatically ensure target directory exists.
- **Frontend UI / Composer:** Add working directory field to Topic settings/create dialogs; add CWD badge & popover to the chat composer (`InputBar`).

---

## 2. Implementation Milestones

### Milestone 1: Database Migration & Schema Validation

**Files to modify/create:**
- `backend/cubeplex/models/topic.py`: Add `workdir: str | None = Field(default=None, max_length=512, sa_column=Column(String(512), nullable=True))`
- `backend/cubeplex/models/conversation.py`: Add `workdir: str | None = Field(default=None, max_length=512, sa_column=Column(String(512), nullable=True))`
- `backend/cubeplex/utils/paths.py`: Implement `validate_and_normalize_workdir(path: str | None) -> str | None`
- `backend/alembic/versions/<revision>_add_workdir_to_topics_and_conversations.py`: Migration adding nullable `workdir` columns

**Tasks:**
1. Generate and verify linear Alembic migration adding `workdir` column to `topics` and `conversations`.
2. Implement strict validation:
   - Must start with `/workspace` (or be `None`).
   - Normalizes path using `posixpath.normpath`.
   - Rejects traversal attempts (`..`, escaping to `/` or `/tmp`).

---

### Milestone 2: API Endpoints & Workdir Precedence Resolution

**Files to modify:**
- `backend/cubeplex/api/schemas/ws_topics.py`
- `backend/cubeplex/api/schemas/conversations.py`
- `backend/cubeplex/api/routes/v1/ws_topics.py`
- `backend/cubeplex/api/routes/v1/conversations.py`
- `backend/cubeplex/streams/run_manager.py`

**Tasks:**
1. Update Topic & Conversation request/response schemas to include `workdir`.
2. In `_resolve_topic_run_context`:
   - Resolve `effective_workdir` with precedence: `Conversation.workdir` -> `Topic.workdir` -> `"/workspace"`.
   - Pass `workdir` into `RunContext`.
3. In `TopicContextMiddleware` / `topic_context.py`:
   - Include `workdir` in `<conversation_context ... />` when different from default `/workspace` or when specified.

---

### Milestone 3: Sandbox Runtime Execution Dispatch

**Files to modify:**
- `backend/cubeplex/sandbox/base.py`
- `backend/cubeplex/sandbox/opensandbox.py`
- `backend/cubeplex/middleware/sandbox.py`

**Tasks:**
1. In `Sandbox.execute(...)`, accept optional `cwd: str | None = None`.
2. In `OpenSandbox.execute(...)`:
   - Pass `working_directory=cwd or self._workdir` into `RunCommandOpts`.
   - Ensure the working directory exists before command execution (`mkdir -p`).
3. In `SandboxMiddleware`:
   - Initialize with `workdir: str = "/workspace"`.
   - Forward `self.workdir` to tool calls (`execute`, `monitor`).

---

### Milestone 4: Frontend UI (Composer & Topic Settings)

**Files to modify:**
- `frontend/packages/core/src/types/topic.ts`
- `frontend/packages/core/src/types/conversation.ts`
- `frontend/packages/web/components/dialogs/UpgradeToTopicDialog.tsx`
- `frontend/packages/web/components/layout/InputBar.tsx`
- `frontend/packages/web/components/chat/WorkdirBadge.tsx` (new)

**Tasks:**
1. Add `workdir` field to Topic and Conversation TypeScript interfaces.
2. Add "Default Working Directory" input field in `UpgradeToTopicDialog` and Topic settings with `/workspace/...` validation.
3. Add `WorkdirBadge` in the Composer bottom bar showing current directory chip with quick switcher popover.

---

### Milestone 5: Testing & Verification

**Files to create/modify:**
- `backend/tests/unit/utils/test_workdir_validation.py` (Unit tests for path traversal & normalization)
- `backend/tests/e2e/test_workdir_execution.py` (E2E tests for DB migration, API CRUD, and sandbox execution in CWD)

**Tasks:**
1. Unit tests:
   - Valid paths: `/workspace`, `/workspace/cubeplex`, `/workspace/projects/fin-deck/` -> normalized cleanly.
   - Invalid paths: `/etc`, `/`, `../..`, `/workspace/../etc` -> raises validation error.
2. E2E tests:
   - Create topic with `workdir="/workspace/projects/demo"`.
   - Create conversation under topic, send message, execute `pwd` -> verify output is `/workspace/projects/demo`.
   - Override conversation `workdir="/workspace/projects/demo/sub"`, execute `pwd` -> verify output is `/workspace/projects/demo/sub`.

---

## 3. Review Checklist

- [ ] Path validation guarantees no directory traversal outside `/workspace`.
- [ ] Database migration is linear and fully reversible.
- [ ] Base System Prompt cache is untouched (Prompt Cache Discipline preserved).
- [ ] Commands run in resolved working directory without manual `cd`.
- [ ] Composer UI shows active directory and allows switching.
- [ ] All unit and E2E tests pass.
