# Group Chat — Topics & Multi-User Conversations

Native multi-user conversations on the cubeplex platform. Multiple workspace
members share one AI agent conversation in real time, with full message
attribution, shared HITL, and configurable sandbox ownership.

This spec introduces two orthogonal concepts:

- **Topic** — a container that groups related conversations. Any user can
  create a topic to organize conversations (e.g. a project with multiple
  sessions). Topics exist independently of group chat.
- **Group chat** — a topic with multiple participants. Adds sender
  attribution, memory isolation, multi-responder HITL, and shared SSE.

A topic with one participant is a personal topic (memory works normally).
A topic with two or more participants is a group chat. The distinction is
derived from participant count, not a separate flag.

---

## Scope

**In scope (v1):**

- Topic model, participants table, conversation FK
- Group chat creation (standalone + upgrade from 1:1)
- Workspace-internal member invitation (owner picks members)
- Per-message sender attribution (model-side prefix + frontend avatar/name)
- Multi-responder HITL (any participant can answer AskUser/SandboxConfirm)
- SSE access for all participants
- Configurable sandbox (dedicated per-topic or reuse creator's)
- Repository access control (participants join for topic conversations)
- Frontend: sidebar with expandable topics, group message UI, member panel
- Memory isolation (skip personal memory when participant count > 1)
- Concurrent messages via existing steering mechanism
- 20-person participant cap

**Out of scope (v1):**

- IM group chat mapping (Slack/Feishu thread → topic) — v2
- Scheduled task → topic migration — v2
- Topic-level memory items
- Member online presence (WebSocket)
- Message read/unread tracking
- @mention specific participants (model-side recognition + frontend highlight)
- Invite links (workspace-internal only for v1)
- Topic-level agent/skill/preset configuration

---

## Data Model

### New table: `topics`

| Column | Type | Notes |
|---|---|---|
| `id` | str (PK) | `top-` prefixed public ID |
| `org_id` | FK → organizations | OrgScopedMixin |
| `workspace_id` | FK → workspaces | OrgScopedMixin |
| `creator_user_id` | FK → users | Audit: who created it |
| `title` | str(255) | Display name |
| `sandbox_mode` | str(20), nullable | `"dedicated"` / `"creator"` / NULL |
| `max_participants` | int | Default 20 |
| `is_archived` | bool | Soft delete |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

Indexes:
- `ix_topics_org_ws` on `(org_id, workspace_id)`
- `ix_topics_creator` on `(creator_user_id, workspace_id)`

### New table: `topic_participants`

| Column | Type | Notes |
|---|---|---|
| `id` | str (PK) | `tpm-` prefixed public ID |
| `topic_id` | FK → topics | |
| `user_id` | FK → users | |
| `role` | str(20) | `"owner"` / `"member"` |
| `joined_at` | timestamptz | |

Indexes:
- `uq_topic_participant` unique on `(topic_id, user_id)`
- `ix_topic_participants_user` on `(user_id)` — for sidebar listing

### Conversation table changes

Add one nullable FK:

- `topic_id: str | None` — FK → topics

Existing conversations keep `topic_id = NULL`. No migration backfill
needed; the `creator_user_id` path is untouched for `topic_id IS NULL`
rows.

Index:
- `ix_conversations_topic` on `(topic_id)` — for listing conversations
  within a topic

### Public ID prefixes

Add to `backend/cubeplex/models/public_id.py`:

- `PREFIX_TOP = "top"` — topics
- `PREFIX_TPM = "tpm"` — topic participants

---

## Repository Access Control

`ConversationRepository._scoped_select()` changes from:

```python
# current: creator only
.where(Conversation.creator_user_id == self.user_id)
```

to:

```python
# new: creator OR topic participant
.where(
    or_(
        and_(
            Conversation.topic_id.is_(None),
            Conversation.creator_user_id == self.user_id,
        ),
        Conversation.topic_id.in_(
            select(TopicParticipant.topic_id)
            .where(TopicParticipant.user_id == self.user_id)
        ),
    )
)
```

Conversations without a topic continue to use the `creator_user_id`
filter. `test_conversation_privacy` must pass unchanged.

### TopicRepository

New `TopicRepository(ScopedRepository[Topic])` with user-scoped queries:

- `_scoped_select()` joins `topic_participants` to filter by current user
- `create()` auto-inserts creator as `role="owner"` participant
- `add_participants()` validates workspace membership + max cap
- `remove_participant()` handles owner-leaves-last logic

---

## RunContext & Message Attribution

### RunContext extension

```python
@dataclass(slots=True)
class RunContext:
    user_id: str
    org_id: str
    workspace_id: str
    trigger: str = "interactive"
    topic_id: str | None = None
    is_group_chat: bool = False
    participant_ids: list[str] | None = None
```

- `is_group_chat = len(topic_participants) > 1` — computed at run start
- `participant_ids` loaded from `topic_participants` at run start, used
  for HITL responder validation

### Message sender marking

**Only when `is_group_chat = True`:**

Model-side: prefix user message text with `[DisplayName]: `. Applied in
`run_manager.py` when building `_UserMessage`. Single-participant topics
and no-topic conversations are unchanged.

Storage-side: write `sender_user_id` and `sender_display_name` into
cubepi message `metadata` (JSONB). The frontend uses these fields to
render avatar and name on each message bubble.

```python
metadata = {
    "sender_user_id": ctx.user_id,
    "sender_display_name": display_name,
}
```

### Memory isolation

- `is_group_chat = False` → inject personal memory as today
- `is_group_chat = True` → skip personal memory injection
  (`_build_memory_context` returns empty)

### Concurrent messages

No new mechanism. Messages from other participants during an active run
use the existing steering path. The only difference is the `[Name]:`
prefix so the agent knows who sent the steering message.

---

## HITL & SSE

### HITL multi-responder

When `is_group_chat = True`:

- The resume API (`POST /conversations/{id}/answer`) checks
  `responder_user_id IN ctx.participant_ids` instead of relying on the
  repository-layer creator filter
- Any participant can respond to AskUser / SandboxConfirm
- Non-group-chat conversations keep the current implicit creator-only
  behavior

### SSE access

For conversations with a `topic_id`, the SSE endpoint verifies the
requesting user is in `topic_participants` (instead of checking
`creator_user_id`).

SSE events themselves are not per-user filtered — all participants see
the same stream. Each participant holds an independent SSE connection;
the existing Redis stream mechanism supports multiple consumers without
modification.

---

## Sandbox

### Selection at topic creation

When creating a group chat topic, the creator chooses:

1. **Dedicated** (default) — a new sandbox instance bound to the topic.
   All participants' runs share this sandbox. Files and environment are
   continuous across runs.
2. **Creator's personal** — runs use the topic creator's existing sandbox.
   Frontend shows a warning: "Other members' operations will execute in
   your environment."

Stored in `topics.sandbox_mode` (`"dedicated"` / `"creator"`).

### Sandbox resolution at run start

- `is_group_chat = False` → current logic: resolve by `(workspace_id, user_id)`
- `is_group_chat = True` + `sandbox_mode = "dedicated"` → resolve by
  `(workspace_id, topic_id)`. Create on first use.
- `is_group_chat = True` + `sandbox_mode = "creator"` → resolve by
  `(workspace_id, topic.creator_user_id)`

### Lifecycle

Same as existing sandbox TTL behavior. Dedicated topic sandboxes are
reclaimed after inactivity, recreated on next run. No special lifecycle
management for topics.

---

## API

### Topic CRUD (workspace-scoped)

```
POST   /api/v1/ws/{ws}/topics
       Body: { title, sandbox_mode?, member_user_ids[] }
       → 201 { topic, conversation }
       Creates topic + first empty conversation. Creator auto-added as owner.

GET    /api/v1/ws/{ws}/topics
       → 200 { items[] }
       Topics where current user is a participant, ordered by last activity.

GET    /api/v1/ws/{ws}/topics/{topic_id}
       → 200 { topic, participants[], conversations[] }

PATCH  /api/v1/ws/{ws}/topics/{topic_id}
       Body: { title? }
       Owner only.
       → 200 { topic }

DELETE /api/v1/ws/{ws}/topics/{topic_id}
       Owner only. Sets is_archived = true.
       → 204
```

### Topic participants

```
POST   /api/v1/ws/{ws}/topics/{topic_id}/participants
       Body: { user_ids[] }
       Owner only. Validates workspace membership + max cap.
       → 201 { participants[] }

DELETE /api/v1/ws/{ws}/topics/{topic_id}/participants/{user_id}
       Owner removes others, or any member removes self.
       Last owner leaving → earliest member auto-promoted to owner.
       → 204

PATCH  /api/v1/ws/{ws}/topics/{topic_id}/participants/{user_id}
       Body: { role }
       Owner only. Transfer ownership.
       → 200 { participant }
```

### Conversation upgrade (1:1 → group chat)

```
POST   /api/v1/ws/{ws}/conversations/{conversation_id}/upgrade-to-topic
       Body: { title, sandbox_mode?, member_user_ids[] }
       → 201 { topic, conversation }
       Creates topic, attaches existing conversation, adds participants.
       Irreversible. All history visible to new members.
```

### Topic-scoped conversation creation

```
POST   /api/v1/ws/{ws}/topics/{topic_id}/conversations
       Body: { title? }
       → 201 { conversation }
       Any participant can create. Inherits topic properties.
```

### Existing conversation API

All existing routes (`GET/PATCH/DELETE /conversations/{id}`,
`POST /conversations/{id}/messages`, SSE) keep their paths. Access
control internally checks: has `topic_id` → verify participant; no
`topic_id` → verify creator.

---

## Frontend

### Sidebar

The flat conversation list becomes a mixed list:

- **No-topic conversations** — single row, unchanged
- **Topics** — expandable tree nodes. Expand to see conversations inside.
- Mixed ordering by last activity time
- Group chat topics show avatar group of participants
- Single-user topics show a folder-style icon
- Unread indicator on topic nodes

### Group chat creation

Two entry points:

1. **Sidebar "New group chat" button** → dialog: enter title, pick
   workspace members, choose sandbox mode (dedicated default, creator's
   personal with risk warning) → creates topic + first conversation
2. **"Invite members" in existing 1:1 conversation** → creates topic,
   attaches conversation, adds participants. Irreversible.

### Conversation page (group chat)

Differences from 1:1:

- **Message bubbles**: show sender avatar + display name above/beside
  each message. 1:1 conversations do not show sender (unchanged).
- **Member panel**: header area shows participant avatar group. Click to
  expand member list (avatar, name, role). Owner can invite/remove
  members from this panel.
- **`/new` in topic context**: creates a new conversation under the same
  topic. Appears in sidebar under the topic node.

### Member management

- Owner can invite workspace members and remove members
- Any member can leave
- Last owner leaving → earliest joined member auto-promoted to owner

---

## Access Control Summary

| Operation | No-topic conversation | Topic conversation |
|---|---|---|
| View conversation | `creator_user_id = user_id` | `user_id IN topic_participants` |
| Send message | Creator only | Any participant |
| SSE subscribe | Creator only | Any participant |
| HITL respond | Creator only | Any participant |
| Delete/rename conversation | Creator only | Topic owner |
| Manage members | N/A | Topic owner |
| Delete topic | N/A | Topic owner |

### Security boundaries

- **Topics don't cross workspaces**: topic and participants are
  constrained to one workspace via OrgScopedMixin. Only workspace
  members can be invited.
- **1:1 privacy unchanged**: conversations with `topic_id = NULL` use
  the original `creator_user_id` filter. `test_conversation_privacy`
  must pass unmodified.
- **Sandbox risk disclosure**: selecting "creator's personal sandbox"
  triggers a frontend warning about shared execution risk.
- **Post-departure invisibility**: after a member is removed or leaves,
  all topic conversations disappear from their list (the participants
  join in `_scoped_select` handles this naturally).

---

## Key Files (expected changes)

### Backend — new

| File | Purpose |
|---|---|
| `models/topic.py` | Topic + TopicParticipant models |
| `repositories/topic.py` | TopicRepository |
| `api/routes/v1/ws_topics.py` | Topic CRUD + participant management |
| `api/routes/v1/ws_topic_conversations.py` | Topic-scoped conversation creation |
| `tests/e2e/test_topics.py` | Topic lifecycle E2E |
| `tests/e2e/test_group_chat.py` | Group chat messaging + HITL E2E |

### Backend — modify

| File | Change |
|---|---|
| `models/conversation.py` | Add `topic_id` FK |
| `models/public_id.py` | Add `PREFIX_TOP`, `PREFIX_TPM` |
| `models/__init__.py` | Export new models |
| `repositories/conversation.py` | Extend `_scoped_select` for topic participant access |
| `streams/run_manager.py` | RunContext extension, sender prefix, memory skip |
| `api/routes/v1/conversations.py` | HITL resume participant check, SSE access check, upgrade-to-topic route |
| `api/app.py` | Mount new routers |

### Frontend — new

| File | Purpose |
|---|---|
| `core/src/types/topic.ts` | Topic + TopicParticipant types |
| `core/src/api/topics.ts` | Topic API client |
| `core/src/stores/topicStore.ts` | Topic state management |
| `web/components/chat/GroupMessageBubble.tsx` | Message with sender avatar/name |
| `web/components/chat/MemberPanel.tsx` | Participant list + management |
| `web/components/sidebar/TopicNode.tsx` | Expandable topic in sidebar |
| `web/components/dialogs/CreateGroupChatDialog.tsx` | Group chat creation dialog |
| `web/components/dialogs/UpgradeToTopicDialog.tsx` | 1:1 → group upgrade dialog |

### Frontend — modify

| File | Change |
|---|---|
| `web/components/sidebar/ConversationList.tsx` | Mix topics and conversations |
| `web/components/chat/MessageList.tsx` | Render GroupMessageBubble for group chats |
| `web/components/chat/ChatHeader.tsx` | Show member avatars, invite button |
| `core/src/types/conversation.ts` | Add `topic_id` field |
| `messages/en.json`, `messages/zh.json` | i18n keys for group chat UI |
