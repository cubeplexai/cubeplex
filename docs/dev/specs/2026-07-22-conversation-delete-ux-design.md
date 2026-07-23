# Conversation delete UX

## Goal

Improve the sidebar conversation **Delete** action so it matches other short
menu verbs, requires confirmation before an irreversible soft-delete, and
describes consequences accurately (messages and artifacts become inaccessible;
storage is not hard-wiped).

## Context

Today each sidebar row’s ⋯ menu offers a destructive item labeled
**“Delete conversation”** (en) / **“删除对话”** (zh). Clicking it immediately
calls `conversationStore.remove` → `DELETE /api/v1/ws/{ws}/conversations/{id}`
with no confirm dialog. Errors only land in `console.error`.

Relevant code:

| Area | Location |
| --- | --- |
| Menu + immediate delete | `frontend/packages/web/components/layout/Sidebar.tsx` → `ConversationRow` (`DropdownMenuContent` `w-36`) |
| i18n label | `shellLayout.deleteConversation` in `messages/en.json` / `zh.json` |
| Store | `useConversationStore().remove` |
| Soft-delete API | `backend/cubeplex/api/routes/v1/conversations.py` |
| Model | `conversations.deleted_at` (`backend/cubeplex/models/conversation.py`) |

**Backend semantics (unchanged by this work):** delete stamps `deleted_at`.
Child rows (billing events, artifacts, attachments) stay so FKs and cost
audit remain valid. Reads filter `deleted_at IS NULL`, so the conversation
looks gone. Conversation artifact routes 404; the workspace artifact library
hides rows whose parent conversation is soft-deleted. Object-store objects
are **not** purged on conversation delete. There is no user-facing undelete.

User docs already mention soft-delete lightly
(`docs/site/docs/guides/conversations/basics.md`).

Elsewhere, destructive actions use `AlertDialog` (e.g. sandbox cards,
artifact library, workspace danger zone). The sidebar is the inconsistent
exception.

## Approaches considered

**A. Label-only shortening** — change en to “Delete”, no confirm.  
Cheap, but misclicks remain; does not address “what happens to artifacts?”.

**B. Label + confirm dialog, keep soft-delete (recommended)** — short en
label, `AlertDialog` with plain-language consequences, no backend cascade
change. Matches product patterns and issue recommendations.

**C. Hard cascade delete of artifacts + storage** — conflicts with billing
FK / future GC design; out of scope for a UX issue.

**D. Confirm + undo toast with undelete API** — needs reverse soft-delete
and restore UI; follow-up, not MVP.

**Chosen: B.**

## Design

### 1. Short menu label

| Locale | Today | Ship |
| --- | --- | --- |
| en | Delete conversation | **Delete** |
| zh | 删除对话 | **Keep 删除对话** (short enough; matches 重命名 / 置顶) |

- Keep destructive styling (`variant="destructive"` + trash icon).
- Visible label is short; dialog title remains specific
  (“Delete conversation?”) for context and a11y.
- Optional: `aria-label` on the menu item can stay longer if needed; not
  required if the dialog titles the action clearly.

### 2. Confirmation dialog

Flow:

1. ⋯ → **Delete** opens `AlertDialog` (same stack as
   `SandboxCard` / shared alert-dialog primitives).
2. **Title:** “Delete conversation?” (en) / matching zh.
3. **Body (plain language):**
   - Name the conversation (title or “Untitled chat”).
   - “This removes the chat from your history.”
   - “Messages will no longer be available **in this conversation**.”
   - “Related artifacts will no longer appear in the library or the
     conversation panel.”
   - Do **not** say files are securely wiped or permanently purged from
     storage.
   - Do **not** promise restore / recycle bin.
   - Do **not** claim that existing public / org / workspace **share links**
     or share snapshots are revoked. Shares are independent immutable
     snapshots (and short-lived artifact share tokens); deleting the source
     conversation does not revoke them. Share revocation stays out of scope.
4. **Actions:** Cancel | Delete (destructive). Esc / cancel = no API call.
5. Confirm → existing `remove(client, id)`; on success row leaves the list
   and active conversation clears as today.
6. **Required:** surface delete failures to the user (toast or inline), not
   only `console.error`. While `remove` is in flight: disable Confirm (and
   prefer disable Cancel only if double-submit is a risk — at minimum disable
   Confirm). On failure: keep the dialog open, show the error, leave the row
   in the list. On success: close dialog then apply store removal as today.

Dialog state: local to `ConversationRow` (or a tiny row-level confirm
component). Do not put delete-confirm in a global store.

### 3. Soft-delete + artifacts product statement

Document and copy must agree:

> Deleting a conversation soft-deletes it. The conversation leaves the
> sidebar history; messages are no longer available in that conversation;
> related artifacts leave the workspace library and conversation artifact
> surfaces. Rows and object-store files are retained for integrity, cost
> audit, and future GC — not hard cascade-wiped. **Existing conversation
> share snapshots and active public share links are not revoked by this
> action** (shares copy data at share time; revoke is a separate flow).

No change to:

- Authorization (`_require_topic_owner_or_creator_if_topic`)
- Soft-delete repo/API
- Artifact hide/404 behavior (existing e2e stays green)

### 4. Docs (when shipping implementation)

Update `docs/site/docs/guides/conversations/basics.md` **Delete** bullet if
needed so it states: soft-delete, no UI restore, artifacts leave library /
conversation surfaces (not “files wiped”). Spec/plan PR may only add the
design; site copy ships with the code PR per docs discipline.

### 5. Menu width

With en **Delete**, `w-36` is enough. No width change required unless layout
still wraps after implementation.

## Out of scope

- Hard cascade delete of artifacts / attachments / object store
- Conversation restore, recycle bin, or undo toast
- Skipping confirm for empty zero-message chats (nice-to-have later)
- Stopping an in-flight run on delete (related #388 if needed separately)
- GC job for soft-deleted conversations
- Redesign of the full sidebar menu
- Changing who may delete
- Revoking conversation shares or public artifact share tokens on delete

## Success criteria

1. English menu item reads **Delete**; zh remains acceptable length
   (**删除对话**).
2. Choosing Delete opens a confirm dialog; Cancel does not call the API.
3. Confirm soft-deletes via existing API and removes the row; active
   conversation clears safely as today.
4. Confirm copy does not claim permanent storage wipe; it states messages
   and artifacts become unavailable in conversation / library surfaces, and
   does not claim share-link revocation.
5. Failed delete shows user-visible error; row remains; dialog stays open.
6. No auth / soft-delete / artifact-404 regressions.
7. en/zh i18n for label + dialog title/body/actions.
8. Implementation PR updates user-facing docs if the basics page still
   under-explains artifact library visibility or soft-delete limits.

## Resolved product choices (this design)

| Question | Decision |
| --- | --- |
| zh label | Keep **删除对话** |
| Confirm body | Explicit artifacts + messages language (not only “can’t be undone”) |
| Empty chats skip confirm | No for MVP |
| Cascade | Soft-hide (current) stays |
| Browser undo / GC | Separate follow-ups |

## Related

- Issue #392
- Soft-delete model: `backend/cubeplex/models/conversation.py`
- Artifact library scoping: workspace artifacts list + conversation routes
- Existing docs: `docs/site/docs/guides/conversations/basics.md`
