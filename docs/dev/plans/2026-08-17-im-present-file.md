# IM outbound for present_file — implementation plan

**Goal**: A successful `present_file` during an IM run delivers the file to
the chat at terminal, without changing `save_artifact` outbound or the
tool prompt.

**Architecture**: Mirror artifact outbound. The stream converter emits a
sibling `presented_file` event; `cubepi_dict_to_agent_event` persists it;
the IM tailer captures it on `IMArtifactDispatcher` and sends at
terminal (image message on Feishu, `send_file` elsewhere). ObjectStore
key is reconstructed; Redis `SET NX` is the idempotency gate.

**Stack**: FastAPI / cubepi SSE, Redis run stream, existing IM
`OutboundConnector` + `IMArtifactDispatcher`.

---

## Unit 1 — SSE event

**Files**

- `backend/cubeplex/agents/stream.py` — emit sibling event from a
  successful `present_file` tool result
- `backend/cubeplex/agents/schemas.py` — `PresentedFileEvent`
- `backend/cubeplex/streams/run_manager.py` — map the dict in
  `cubepi_dict_to_agent_event` and `_dicts_to_sse_events`
- `frontend/packages/core/src/types/events.ts` — add `'presented_file'`
  to `AgentEventType`
- `frontend/packages/core/src/stores/messageStore.ts` — treat like
  `artifact` / `citation` (no-op, cursor still advances)

**Interfaces**

```text
SSE dict:
  type: "presented_file"
  presented_file: {id, filename, mime_type, kind, size_bytes, caption?}

PresentedFileEvent.data = { presented_file: <that object> }
```

Error / non-JSON / missing `presented_file` → no sibling event.

**Tests** (`backend/tests/unit/`)

- `test_stream.py`: success → `tool_result` + `presented_file`; error →
  `tool_result` only
- `test_run_manager_cubepi_dict_to_event.py`: dict maps to
  `PresentedFileEvent`; unknown types still drop

Invariant: if the sibling is omitted from `cubepi_dict_to_agent_event`,
IM never sees the file.

---

## Unit 2 — Object key helper + run_id

**Files**

- `backend/cubeplex/services/presented_files.py` — export
  `presented_object_key(...)` (today's private builder)
- `backend/cubeplex/middleware/artifacts.py` — optional `run_id` on
  middleware / tool factory, passed into `present_from_sandbox`
- `backend/cubeplex/streams/run_manager.py` — pass the run's `run_id`
  into `ArtifactMiddleware`

**Interfaces**

```python
def presented_object_key(
    *, org_id: str, workspace_id: str, conversation_id: str,
    file_id: str, filename: str,
) -> str
```

Key layout unchanged:
`presented/{org}/{ws}/{conv}/{file_id}/original/{filename}`.

**Tests**: existing middleware ctor tests still construct without
`run_id`. Add a unit assertion that the exported key matches the
layout.

---

## Unit 3 — Connector `send_image`

**Files**

- `backend/cubeplex/im/types.py` — add to `OutboundConnector`
- `backend/cubeplex/im/feishu/connector.py` — upload + `msg_type=image`
- `backend/cubeplex/im/slack/connector.py` — delegate to `send_file`
- `backend/cubeplex/im/discord/connector.py` — delegate to `send_file`
- `backend/cubeplex/im/dingtalk/connector.py` — return `False`
- `backend/cubeplex/im/teams/connector.py` — return `False`

**Interfaces**

```python
async def send_image(self, *, local_path: str, filename: str) -> bool
```

Feishu uses the bound chat / reply target, same as `send_file`.

**Tests**: Feishu unit tests if a send-file test already mocks
`im.v1.message.create` — extend or add a parallel send_image case.
Otherwise the dispatcher tests (Unit 4) cover the call.

---

## Unit 4 — Dispatcher + tailer

**Files**

- `backend/cubeplex/im/artifacts.py` — `handle_presented`,
  `deliver_terminal_presented` (or fold into the existing terminal
  gather), download helper, claim key
  `{prefix}:im:presented_sent:{run_id}:{pfile_id}`
- `backend/cubeplex/im/outbound.py` — on `type == "presented_file"`
  call `handle_presented`; after artifact terminal delivery, deliver
  presented files

**Core logic**

1. Capture payload by `id` at handle time (overwrite same id is
   impossible — each present is a new id).
2. At terminal, concurrently deliver each captured file.
3. Image + `supports_inline_image` → `send_image`; else `send_file`.
4. Size above `outbound_size_cap` → skip native send, text fallback.
5. Native send False / exception → text fallback
   (`📎 {filename}` + caption if present).
6. Text send also fails → release claim.

Do not mint artifact share-links for presented files.

**Tests** (`backend/tests/integration/test_im_outbound_files.py` or a
sibling `test_im_outbound_presented.py`)

- presented document → `send_file` once at terminal
- presented image + inline → `send_image`, not `send_file`
- replay → once (NX claim)
- failed send + failed text → claim released, replay retries
- oversize → no native send, one text notice
- `save_artifact` document path still hits `send_file` (no regression)

---

## Unit 5 — Docs

**Files**

- `docs/site/docs/guides/conversations/artifacts.md` — mention
  `present_file` (show-now, not gallery) and that IM delivers both
- `docs/site/docs/guides/im/overview.md` — agent-shown files arrive as
  native messages
- `backend/docs/im-feishu-setup.md` — smoke: present a PNG → image
  message

No prompt change.

---

## Spec coverage

| Spec requirement | Unit |
|---|---|
| Sibling SSE + persist | 1 |
| `run_id` on row | 2 |
| Feishu image message | 3 + 4 |
| Native file on Slack/Discord | 4 |
| Idempotency / retry | 4 |
| Artifact outbound unchanged | 4 regression |
| Text fallback, no share-link | 4 |
| Prompt unchanged | 5 (explicit non-change) |
| User-facing docs | 5 |
