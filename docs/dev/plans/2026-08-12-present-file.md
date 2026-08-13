# present_file — implementation plan

**Goal**: Agent can show a sandbox file in chat via `present_file`; bytes live in
ObjectStore for the conversation lifetime; FE renders image/file cards from
tool results.

**Architecture**: New `presented_files` row + object key; tool on the same
sandbox-gated path as `save_artifact`; content API mirrors attachments;
FE special-cases `tool_call.name === 'present_file'`. No gallery, no independent TTL.

**Stack**: FastAPI, SQLModel, alembic, ObjectStore, cubepi AgentTool, Next/React.

---

## Unit 1 — Model + repo + service + migration

**Files**

- `backend/cubeplex/models/presented_file.py` — table
- `backend/cubeplex/models/__init__.py` — export
- `backend/cubeplex/repositories/presented_file.py` — scoped CRUD + sum size
- `backend/cubeplex/repositories/__init__.py` — export
- `backend/cubeplex/services/presented_files.py` — validate path, download sandbox,
  upload store, persist row, DTO, `delete_for_conversation`
- `backend/alembic/versions/*_presented_files.py` — autogenerate

**Interfaces**

- `PresentedFile` fields per spec §4; `_PREFIX = "pfile"`
- `present_from_sandbox(sandbox, *, conversation_id, org_id, workspace_id, path, caption?, run_id?) -> PresentedFile`
- Path must normalize under `/workspace`; reject `..` escape
- Size/MIME: reuse `attachments.max_file_bytes` + `attachments.allowed_mime_types`;
  present quota key `presented_files.max_per_conversation_bytes` (default same as
  attachments 500MB), **separate** from attachment sum

**Tests**: unit — path reject; missing file error shape; happy path with fake
sandbox + mock objectstore (or in-memory)

---

## Unit 2 — Tool + prompt

**Files**

- `backend/cubeplex/middleware/artifacts.py` — add `present_file` tool factory
  alongside `save_artifact` (same middleware DI)
- `backend/cubeplex/prompts/artifacts.py` — present vs save_artifact rules

**Interfaces**

- Tool name `present_file`, args `{path, caption?}`, result JSON per spec
- Middleware `.tools` returns both tools

**Tests**: unit — tool error on missing path; success JSON has `presented_file.id`

---

## Unit 3 — HTTP content/thumbnail

**Files**

- `backend/cubeplex/api/routes/v1/presented_files.py`
- `backend/cubeplex/api/routes/v1/__init__.py`
- `backend/cubeplex/api/app.py` — include router

**Interfaces**

```
GET .../presented-files/{id}/content
GET .../presented-files/{id}/thumbnail
```

Member + conversation in scope + not soft-deleted → stream; else 404.

**Tests**: e2e or integration — create row + object, GET content 200; foreign
workspace 404

---

## Unit 4 — Frontend cards

**Files**

- `frontend/packages/web/components/chat/PresentedFileCard.tsx` — image inline /
  non-image chip with download URL
- `frontend/packages/web/components/chat/AssistantMessage.tsx` — branch on
  `present_file`; exclude from tool group
- `frontend/packages/web/messages/en.json` + `zh.json` — short strings if needed

**Interfaces**

- Content URL:
  `/api/v1/ws/{ws}/conversations/{conv}/presented-files/{id}/content`
- Parse tool_result JSON `presented_file`

**Tests**: component test parse + render image src when result present

---

## Unit 5 — Verify

- `uv run pytest` targeted unit + e2e for present_file
- Manual checklist from spec success criteria where e2e is heavy
