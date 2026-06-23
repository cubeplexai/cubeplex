# Plan: fork conversation from a message

Spec: [../specs/2026-06-23-fork-conversation-design.md](../specs/2026-06-23-fork-conversation-design.md)

Branch: `feat/2026-06-23-fork-conversation`
Worktree: `.worktrees/feat/2026-06-23-fork-conversation/` (slot 78,
ports 8078/3078, DB `cubebox_feat_2026_06_23_fork_conversation`)

Single PR. The frontend touches one new affordance and three files; the
backend adds one route + one repo method. Splitting would mean shipping
a backend endpoint nothing calls — not worth the review overhead.

## Step 1 — Backend: repository method

File: `backend/cubebox/repositories/conversation.py`

Add `ConversationRepository.fork(src: Conversation, *, after_run_id: str) -> Conversation`:

1. Reject `src.is_group_chat` with `ValueError("group_chat_not_supported")`.
2. Allocate the new id up front: `new_id = generate_public_id("conv")`
   (so we can pass it to `cp.fork` *and* the row insert).
3. `async with init_checkpointer() as cp: await cp.fork(src.id, new_id,
   after_run_id=after_run_id, metadata={"source_conversation_id": src.id,
   "forked_by_user_id": self.user_id, "forked_at": utc_isoformat(now())})`.
4. Catch `cubepi.RunNotCompletedError` → re-raise as a typed cubebox
   exception the route maps to 400. Same for `ThreadNotFoundError` and
   `ThreadAlreadyExistsError`.
5. Insert `Conversation(id=new_id, ...)` carrying over fields per spec.
   `has_messages=True`. `creator_user_id=self.user_id`. `topic_id=src.topic_id`.
   Title: `f"{src.title} — fork"` truncated to the 255-char column limit.

Wrap the SQL transaction `await self.session.commit()`. If commit fails,
re-raise; orphan cleanup is documented in the spec (out of scope here).

Define module-local exception classes `ForkRunNotCompleted`,
`ForkRunNotFound`, `ForkGroupChat`, `ForkNewThreadExists` in this file —
no separate exceptions module yet; the codebase pattern is to raise
HTTPException from the route layer and keep small typed errors next to
the repo that produces them. Mirror what `conversation_title.py` does
for `TitleGenerationError`.

## Step 2 — Backend: route

File: `backend/cubebox/api/routes/v1/conversations.py`

Insert after the existing `PATCH /{id}/pin` block (so the file's
ordering — list/create/get → mutations → message ops → run ops — stays
intact):

```py
class ForkConversationRequest(BaseModel):
    after_run_id: str = Field(min_length=1, max_length=64)

@router.post("/{conversation_id}/fork")
async def fork_conversation(
    conversation_id: str,
    body: ForkConversationRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> ConversationResponse:
    conv_repo = ConversationRepository(session, org_id=..., workspace_id=..., user_id=...)
    src = await conv_repo.get_by_id(conversation_id)
    if not src:
        raise HTTPException(404, ...)
    try:
        new_conv = await conv_repo.fork(src, after_run_id=body.after_run_id)
    except ForkGroupChat:
        raise HTTPException(400, detail={"error": "group_chat_not_supported"})
    except ForkRunNotCompleted:
        raise HTTPException(400, detail={"error": "run_not_completed"})
    except ForkRunNotFound:
        raise HTTPException(400, detail={"error": "run_not_found"})
    except ForkNewThreadExists:
        raise HTTPException(409, detail={"error": "new_thread_exists"})
    return serialize_conversation(new_conv)
```

## Step 3 — Backend e2e test

File: `backend/tests/e2e/test_conversation_fork.py`

Pattern: mirror `tests/e2e/test_conversation_flow.py` because we need
real messages in cubepi before we can fork. The minimum useful test
sends a message with the mocked-LLM helper, waits for the run to
complete, then issues the fork. Cases:

1. `test_fork_happy_path` — send a turn, get the assistant's `run_id`
   from `GET /messages`, fork, assert: new conv id ≠ src id, response
   carries title `"… — fork"`, `GET /{new}/messages` returns the same
   message bodies as the source (compare by `(role, content)`, ignore
   timestamps and the run_id which is preserved by `cp.fork`).
2. `test_fork_run_not_completed` — kick off a send but cancel before
   the run finishes (or fork against a fabricated run_id) → 400
   `run_not_completed` / `run_not_found`.
3. `test_fork_cross_workspace_returns_404` — create a conv in WS A,
   call fork as a member of WS B → 404, not 403.
4. `test_fork_group_chat_rejected` — set `is_group_chat=True` on the
   source row, attempt fork → 400 `group_chat_not_supported`.

Tag with `pytest.mark.e2e` (auto-applied by conftest under `tests/e2e/`).
The happy path needs an LLM-completed run; use the existing test helper
that fakes the provider (look for usage in `test_conversation_flow.py`).
If no such helper exists, write a tiny one that uses cubepi's
checkpointer directly to seed a completed run — simpler than running the
real agent.

Cleanup: each test creates its own conversation; delete both `src` and
`fork` rows at the end so the shared workspace stays clean.

## Step 4 — Frontend: API client

File: `frontend/packages/core/src/api/conversations.ts`

Add:

```ts
export async function forkConversation(
  client: ApiClient,
  conversationId: string,
  afterRunId: string,
): Promise<ConversationResponse> {
  return client.post(
    `/api/v1/conversations/${conversationId}/fork`,
    { after_run_id: afterRunId },
  );
}
```

Re-export the function from `packages/core/src/api/index.ts` if needed
(check the existing pattern; `listMessages` etc. are already exported
through the barrel).

## Step 5 — Frontend: per-message hover menu

This is the only new affordance. The codebase has no
`MessageActions`-style component yet, so introduce a tiny one:

File: `frontend/packages/web/components/chat/MessageActions.tsx` (new)

Props: `{ runId: string | null, conversationId: string, disabledReason?: string }`.
Renders a small button group that becomes visible on hover of the parent
message. Single button: `Fork conversation` (i18n key — see Step 6).

Behavior:

- If `runId == null` or `disabledReason != null`, render the button
  disabled with a tooltip carrying the reason.
- On click, call `forkConversation(client, conversationId, runId)`.
- On success, `router.push(\`/w/${wsId}/conversations/${newConv.id}\`)`.
  Use `useWsId()` from the existing hook.
- On error, toast the error (`useToast`); for the `run_not_completed`
  shape, surface a kinder string ("Wait for the response to finish").

File: `frontend/packages/web/components/chat/UserMessage.tsx`
File: `frontend/packages/web/components/chat/AssistantMessage.tsx`

Add `runId` prop (read from the cubepi message dump — the field is named
`run_id` on the wire; the frontend renames to `runId` at the message-list
boundary). Render `<MessageActions ...>` positioned absolute-top-right
inside a `group-hover:opacity-100` wrapper. Don't restyle the bubble.

File: `frontend/packages/web/components/chat/MessageList.tsx`

Pass `runId={m.run_id}` and `conversationId={conversationId}` through to
the per-role components. Plumb `is_group_chat` from the conversation
record to a `forkDisabled` prop so MessageActions can render the right
tooltip on group chats.

## Step 6 — i18n strings

Add to whichever locale files the chat namespace lives in (likely
`frontend/packages/web/locales/{en,zh-CN}/chat.json` — verify path
before writing). Keys:

- `chat.forkConversation` — button label ("Fork conversation" / "复刻对话")
- `chat.forkDisabled.noRun` — "Cannot fork from this message"
- `chat.forkDisabled.runStreaming` — "Wait for the response to finish"
- `chat.forkDisabled.groupChat` — "Fork is not available in group chats"
- `chat.forkSuccess` — "Forked to a new conversation"
- `chat.forkError` — generic toast fallback

## Step 7 — Verify

```bash
cd backend
uv run mypy cubebox 2>&1 | tee tmp/mypy.log | tail -3
uv run pytest tests/e2e/test_conversation_fork.py --no-cov 2>&1 | tee tmp/fork.log | tail -10

cd ../frontend
pnpm --filter @cubebox/core build 2>&1 | tee ../tmp/core-build.log | tail -3
pnpm --filter @cubebox/web lint 2>&1 | tee ../tmp/lint.log | tail -5
pnpm --filter @cubebox/web typecheck 2>&1 | tee ../tmp/tsc.log | tail -3
```

Manual smoke (optional, gated on time): start the dev server on this
worktree's ports (8078/3078), send a message, hover, click Fork, confirm
the URL changes and the history is identical.
