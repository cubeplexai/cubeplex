# Conversation delete UX — implementation plan

**Goal**: Shorten the English sidebar Delete label, require an accurate
confirm dialog before soft-delete, and keep cascade/artifact semantics
unchanged.

**Architecture**: Frontend-only product change. Reuse existing
`AlertDialog` primitives and `conversationStore.remove`. No API or
migration work.

**Tech stack**: Next.js / React 19, next-intl, existing
`components/ui/alert-dialog`, conversation store client.

---

## Unit 1: i18n strings

**Files**:

- `frontend/packages/web/messages/en.json`
- `frontend/packages/web/messages/zh.json`

**What changes**:

- `shellLayout.deleteConversation`: en → `"Delete"`; zh stays `"删除对话"`.
- Add dialog keys under `shellLayout` (or a dedicated nested object), e.g.:
  - `deleteConversationTitle` — “Delete conversation?”
  - `deleteConversationDescription` — multi-sentence body with `{title}`
  - reuse or add `cancel` / confirm **Delete** labels consistent with other
    destructive dialogs

**Interfaces**: string keys only; no TS types beyond existing next-intl usage.

**Core logic**: none.

**Tests intent**: none beyond typecheck / lint on message usage. Optional
snapshot not required.

---

## Unit 2: Confirm dialog in `ConversationRow`

**Files**:

- `frontend/packages/web/components/layout/Sidebar.tsx`
- Optionally extract
  `frontend/packages/web/components/layout/DeleteConversationDialog.tsx`
  if `Sidebar.tsx` is already large

**What changes**:

1. `Delete` menu item sets local `confirmOpen` / pending id instead of
   calling `remove` immediately.
2. Render `AlertDialog` patterned after
   `workspace-settings/sandboxes/SandboxCard.tsx` confirm block:
   Title, Description (include conversation title; messages + library
   artifacts inaccessible; **no** share-revoke or storage-wipe claims),
   Cancel, destructive Confirm.
3. Confirm handler: `await remove(buildClient(currentWsId), convo.id)`.
   - Pending: disable Confirm; track `deleting` state.
   - Success: close dialog (store already drops row / clears activeId).
   - Failure: keep dialog open; show toast or inline error (required —
     not `console.error` only). Use any nearby toast helper; if none,
     add a minimal one consistent with other shell errors.
4. Prevent row navigation when interacting with menu/dialog
   (`stopPropagation` as today).

**Interfaces**:

```tsx
// Local state sketch
const [deleteOpen, setDeleteOpen] = useState(false)
// on Delete item: setDeleteOpen(true)
// onConfirm: remove(...); setDeleteOpen(false)
```

No store API change.

**Core logic**:

```
menu Delete → open dialog
Cancel / Esc → close, no API
Confirm → remove(client, id) → store drops row; activeId clear if needed
```

**Tests intent**:

- Component / RTL test: opening Delete shows dialog; Cancel does not call
  remove; Confirm calls remove once.
- Rejected `remove`: dialog stays open, error is shown, row mock remains
  (no successful list mutation).
- Prefer mocking `useConversationStore` remove.
- Existing backend e2e for soft-delete + artifact 404 remain the
  contract for server behavior (no new e2e required for MVP unless
  Playwright sidebar helpers already exist).

---

## Unit 3: User docs (implementation PR)

**Files**:

- `docs/site/docs/guides/conversations/basics.md`

**What changes**: Tighten the **Delete** bullet so it matches product copy:
soft-delete, no UI restore, messages unavailable in that conversation,
related artifacts leave library / conversation surfaces; internal retention
for audit/GC, not a user recycle bin. Do not imply share links are revoked.

**Tests intent**: docs-only; none.

---

## Unit 4: Verification

**Commands** (implementation phase):

- Targeted frontend unit/component test for the dialog.
- Manual: en label short; confirm cancel/confirm; zh strings render.
- Do not run full `make check-ci` by hand (pre-push hook).

---

## Non-goals (plan boundary)

- Backend cascade / GC / undelete
- Skip-confirm for empty chats
- Stop-run-on-delete coupling to #388
