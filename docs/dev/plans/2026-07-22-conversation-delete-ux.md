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
   Title, Description (include conversation title), Cancel, destructive
   Confirm.
3. Confirm handler: `void remove(buildClient(currentWsId), convo.id)` with
   better error surfacing if a toast helper is already used nearby; else
   keep `console.error` and note a follow-up.
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
- Prefer mocking `useConversationStore` remove.
- Existing backend e2e for soft-delete + artifact 404 remain the
  contract for server behavior (no new e2e required for MVP unless
  Playwright sidebar helpers already exist).

---

## Unit 3: User docs (implementation PR)

**Files**:

- `docs/site/docs/guides/conversations/basics.md`

**What changes**: Tighten the **Delete** bullet so it matches product copy:
soft-delete, no UI restore, messages unavailable, related artifacts leave
library / conversation surfaces; internal retention for audit/GC, not a
user recycle bin.

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
