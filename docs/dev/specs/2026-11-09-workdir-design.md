# Topic Default Working Directory & Per-Conversation CWD Specification

**Status:** Proposed Specification

**Date:** 2026-11-09

**Scope:** Data Models (`Topic`, `Conversation`), Sandbox Execution Runtime (`OpenSandbox`, `SandboxMiddleware`), REST API, Frontend Composer (`InputBar`, Topic Modals)

**Issue:** [#739](https://github.com/cubeplexai/cubeplex/issues/739) (Refs #739)

**Related Designs:**
- [Sandbox Scoping Policy](2026-05-27-sandbox-scoping-policy-design.md)
- [Workspace Projectization (M4)](2026-05-06-m4-workspace-projectization-design.md)
- [Agent Topic Context Awareness](2026-11-06-agent-topic-context-design.md)
- [Prompt Cache Discipline](../../../backend/docs/prompt-cache-discipline.md)
- [Testing Discipline & Layer Split](../../testing.md)

---

## 1. Executive Summary & Problem Statement

CubePlex sandboxes mount a persistent volume at `/workspace`. Inside this persistent volume, users organize code repositories, subprojects, and data directories (e.g. `/workspace/cubeplex`, `/workspace/projects/fin-deck`).

Currently, the sandbox runtime (`OpenSandbox`) hardcodes its command working directory to `/workspace`:
1. **Manual Navigation Overhead:** Every terminal execution or script run performed by the agent must explicitly `cd` into the project subdirectory, adding fragile prefix boilerplate (`cd /workspace/... && ...`) to tool calls.
2. **File Clutter & Discipline Drift:** Despite system prompt guidelines advising the agent to keep output under `/workspace/projects/<name>/`, agents frequently forget to navigate and accidentally create temporary artifacts or build caches in the root directory.
3. **Missing First-Class Workdir Binding:** Topics represent project workstreams, yet there is no concept of a "project home directory" attached to a Topic. Users cannot bind a Topic to a repository directory, nor can they adjust the working directory for a single conversation.

This specification defines the schema, security boundaries, runtime execution dispatch, and UI interactions to support:
- Setting a **default working directory (`workdir`) on Topics**.
- Allowing a **per-conversation working directory override (`workdir`)**.
- Executing all sandbox commands (`execute`, `monitor`) in the resolved working directory by default.
- Adhering to **Prompt Cache Discipline**: keeping the static system prompt cache hot while reflecting the active directory in turn context and tool runtime.

---

## 2. Core Architectural Principles & Security Boundaries

### 2.1 The Sandbox Security Boundary
All working directory paths MUST satisfy strict containment rules:
- **Sandbox Root Constraint:** Paths must resolve strictly within the sandbox persistent volume: `/workspace` or a subdirectory thereof (e.g. `/workspace/projects/my-repo`).
- **Path Traversal Prevention:** The path is normalized using `os.path.normpath` / POSIX cleanpath to strip relative sequences (`..`, `./`). Any path resolving outside `/workspace` (such as `/`, `/etc`, `/tmp`, `../..`) is rejected with a `422 Unprocessable Entity` or `InvalidInputError`.
- **Absolute Paths Only:** Stored working directory paths must begin with `/workspace`. Relative paths are disallowed at the API boundary to prevent ambiguous root resolution.

### 2.2 Prompt Cache Discipline
- `SANDBOX_PROMPT_TEMPLATE` in `backend/cubeplex/prompts/sandbox.py` is injected into `transform_system_prompt` by `SandboxMiddleware`. It currently formats `**Working directory:** {workdir}` using `self.sandbox.workdir` (`/workspace`).
- To prevent per-conversation directory switches from fragmenting or invalidating the workspace-wide system prompt cache:
  - The static sandbox system prompt retains `/workspace` as the base persistent volume anchor.
  - The active conversation-level working directory is passed directly to the execution runtime (`OpenSandbox.execute` sets `working_directory=effective_workdir`) and surfaced in the dynamic `<conversation_context ... />` turn block (e.g. `workdir="/workspace/cubeplex"`).
  - The agent's `execute` tool parameter descriptions and prompt notes inform the model that shell commands execute in the active `workdir` by default.

---

## 3. Data Model & Database Migrations

### 3.1 Topic Model Extension
Add `workdir` to `Topic` (`backend/cubeplex/models/topic.py`):
```python
workdir: str | None = Field(
    default=None,
    max_length=512,
    sa_column=Column(String(512), nullable=True),
)
```

### 3.2 Conversation Model Extension
Add `workdir` to `Conversation` (`backend/cubeplex/models/conversation.py`):
```python
workdir: str | None = Field(
    default=None,
    max_length=512,
    sa_column=Column(String(512), nullable=True),
)
```

### 3.3 Resolution Precedence
The effective working directory for any run is resolved as:
1. `Conversation.workdir` (if explicitly set on the conversation).
2. `Topic.workdir` (if the conversation belongs to a topic and the topic has a default `workdir`).
3. Fallback to `/workspace` (the persistent sandbox root).

---

## 4. Backend Runtime & Execution Dispatch

### 4.1 RunContext & Resolution
Extend `RunContext` (`backend/cubeplex/streams/run_manager.py`):
```python
workdir: str | None = None
```
In `_resolve_topic_run_context`:
- Query `Conversation.workdir` and `Topic.workdir`.
- Resolve and return `effective_workdir: str`.
- Validate that the path starts with `/workspace` (reject path traversal).

### 4.2 Sandbox Execution Layer (`OpenSandbox`)
In `backend/cubeplex/sandbox/opensandbox.py` and `backend/cubeplex/sandbox/base.py`:
- `execute` currently uses `working_directory=self._workdir` (where `self._workdir` is `/workspace`).
- Support an optional `cwd: str | None = None` parameter in `Sandbox.execute(...)`.
- When `cwd` is supplied (or when `effective_workdir` is set on `SandboxMiddleware`), `RunCommandOpts` sets:
  ```python
  working_directory=cwd or self._workdir
  ```
- Before execution, if the target directory does not exist, the runtime automatically creates it via `mkdir -p` (or falls back safely to `/workspace` if directory creation fails).

### 4.3 Context Injection
The dynamic turn tag established in #738 is expanded to carry the effective working directory:
```xml
<conversation_context topic_id="top-1a2b3c4d5e" topic_title="Website i18n" workdir="/workspace/cubeplex" />
```
This informs the LLM of its current execution base without altering the static system prompt.

---

## 5. API Endpoints

### 5.1 Topic Endpoints
- `POST /api/v1/ws/{ws_id}/topics`: Accepts `workdir: str | None`.
- `PATCH /api/v1/ws/{ws_id}/topics/{topic_id}`: Accepts `workdir: str | None` to update default directory.
- Responses return `workdir`.

### 5.2 Conversation Endpoints
- `POST /api/v1/ws/{ws_id}/conversations`: Accepts `workdir: str | None`.
- `PATCH /api/v1/conversations/{conversation_id}`: Accepts `workdir: str | None`.
- `POST /api/v1/conversations/{conversation_id}/upgrade-to-topic`: Copies `workdir` to topic if topic `workdir` is omitted.
- `SendMessageRequest`: Optionally accepts `cwd: str | None` for per-message runtime steering.

---

## 6. Frontend UI / UX Architecture

### 6.1 Topic Settings & Creation Modal
- Add a **Working Directory** input field to `UpgradeToTopicDialog` and Topic settings.
- Provide placeholder `/workspace/...` with quick suggestion chips (`/workspace/cubeplex`, `/workspace/projects/<topic-title>`).

### 6.2 Composer (InputBar) CWD Badge & Popover
- Place a compact directory chip next to the model picker and skills picker in the chat composer:
  - Display icon: `Folder` / `FolderGit2`.
  - Label: Shortened name (e.g. `cubeplex` or `/workspace/cubeplex`).
  - Tooltip: Full path: `Working Directory: /workspace/cubeplex`.
- Clicking the chip opens a small popover allowing:
  - Changing the working directory for the current conversation.
  - Resetting to the Topic default.

---

## 7. Testing Strategy

Per `docs/testing.md`:
- **Unit Tests (`backend/tests/unit/`):**
  - Path sanitization helper: rejects paths outside `/workspace` (`../..`, `/etc`, `C:\`, relative paths).
  - Effective workdir precedence resolver: conversation override > topic default > `/workspace`.
- **E2E Tests (`backend/tests/e2e/`):**
  - Database schema & Alembic migration round-trip test for `workdir` columns.
  - Topic creation and patch with `workdir`.
  - Execution test: verify that shell commands execute in the resolved `workdir` (`pwd` returns the configured directory).
  - Security test: verify API rejects path traversal attempts.
