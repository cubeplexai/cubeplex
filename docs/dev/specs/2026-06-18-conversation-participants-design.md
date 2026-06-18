# Conversation Participants — Two-Tier ACL for Group Chat & Topics

This spec refactors the group-chat data model into a **two-tier ACL** that
separates "who can access this conversation" from "who is actively
participating in it." It supersedes the single-tier model in
[2026-06-17-group-chat-design.md](./2026-06-17-group-chat-design.md) for
the same PR (the previous design's tables haven't shipped — we reset and
regenerate migrations).

## Why we're revising

The shipped design conflates two distinct concepts:

- **"Group chat"** — a multi-participant conversation. Product surface.
  Could exist with or without a topic container.
- **"Topic"** — a grouping container for related conversations. Has its
  own membership (who is *eligible* to access conversations under it).

Two real problems with the old conflation:

1. **No standalone group chat.** To have a multi-user conversation you
   must create a topic. Topic implies a long-lived container, which is
   overkill for a one-off "add Bob to this chat" action.
2. **No conversation-level isolation inside a topic.** Every topic
   participant sees every conversation under the topic. Real teams want
   "Alice and Bob spin off a side conversation inside the Marketing
   topic that only they see" without creating a sub-topic.

The new model:

- **`conversation_participants`** — "who is *in* this conversation right
  now." Drives `is_group_chat`, memory injection, sender attribution,
  HITL responder authorization, SSE filtering for participation events.
- **`topic_participants`** (unchanged) — "who has *eligibility* to
  access conversations under this topic." Drives sidebar visibility of
  the whole topic + ability to browse/auto-join any conversation under
  it.

---

## Three conversation forms (post-change)

| Form | Topic? | conversation_participants | UI label |
|---|---|---|---|
| Personal 1:1 | NULL | empty (creator implicit) | "Conversation" |
| Standalone group chat | NULL | ≥ 2 explicit rows | "Group chat" |
| Topic conversation | non-null | ≥ 1 explicit rows (creator + auto-joins) | "Group chat in `<topic>`" |

The product surface is **"group chat"** in all multi-participant cases.
"Topic" is the optional container — it exists when users want a
grouped, persistent collection of related conversations sharing
membership.

---

## Data model

### New table: `conversation_participants`

| Column | Type | Notes |
|---|---|---|
| `id` | str (PK) | `cpm-` prefixed public ID |
| `org_id` | FK → organizations | OrgScopedMixin |
| `workspace_id` | FK → workspaces | OrgScopedMixin |
| `conversation_id` | FK → conversations | |
| `user_id` | FK → users | |
| `joined_at` | timestamptz | First-message timestamp for auto-joins |

Indexes:
- `uq_conversation_participant` unique on `(conversation_id, user_id)`
- `ix_conversation_participants_user` on `(user_id)` — for "my conversations"

**No role column.** Conversation-level admin (rename / delete) is gated
by `creator_user_id` and (when topic present) topic owner. Avoiding a
per-participant role keeps the table append-only and the rules simple.

### Conversation table — new column

```python
class Conversation(...):
    # existing fields ...
    is_group_chat: bool = Field(default=False)
```

Cached denormalization. Maintained by `ConversationRepository`:
- `add_conversation_participant` re-counts rows; flips to True when
  count crosses 1 → 2.
- (No `remove_conversation_participant` — see "Auto-join semantics".)

Why a stored field instead of `COUNT(*) > 1` on every read: hot path
(every message send, every sandbox resolution, every middleware
init). Stored bool is one column read; computed needs a join + count.

### Topic table — unchanged (from group-chat-design.md)

`topics` and `topic_participants` keep their existing shape:
- topic_participants gates topic-level visibility (sidebar + cross-conv
  browsing)
- topic owner manages topic membership, archives the topic, etc.

### `user_sandboxes` — polymorphic scope (replaces nullable topic_id)

The shipped design adds `user_sandboxes.topic_id` + a second partial
unique index. We're replacing this with a single polymorphic scope:

```python
class UserSandbox(...):
    # remove the nullable topic_id column
    # remove the second partial unique index on (workspace_id, topic_id)

    scope_type: str = Field(max_length=20)  # 'user' | 'conversation' | 'topic'
    scope_id: str = Field(max_length=20)    # user_id | conversation_id | topic_id

    __table_args__ = (
        Index(
            "uq_user_sandbox_active_scope",
            "org_id", "workspace_id", "scope_type", "scope_id",
            unique=True,
            postgresql_where=text("status IN ('provisioning','running')"),
        ),
    )
```

The existing `uq_user_sandbox_active(org_id, workspace_id, user_id)`
partial unique is removed (replaced by the scope-keyed one above with
`scope_type='user'`).

**Why polymorphic instead of "add a conversation_id column":**

1. The "which key is set?" branching disappears — repo + manager always
   take `(scope_type, scope_id)`.
2. **Upgrade is one UPDATE.** Promoting a standalone group chat to a
   topic: `UPDATE user_sandboxes SET scope_type='topic', scope_id=:new_topic_id
   WHERE scope_type='conversation' AND scope_id=:conv_id`. The running
   sandbox instance is inherited; files don't move.
3. One partial unique index instead of N — each new keying dimension
   would otherwise need its own.

### Migration strategy

The shipped group-chat tables (`topics`, `topic_participants`,
`conversations.topic_id`, `user_sandboxes.topic_id`,
`topics.last_activity_at`) have NOT been merged to main. We:

1. Reset the worktree DB (`alembic downgrade base`).
2. Delete the two group-chat migrations:
   `7b81f04dce1f_add_topics_and_topic_participants_.py` and
   `2b6db4bfe7ac_user_sandbox_topic_id_partial_unique_.py`.
3. Re-run `alembic revision --autogenerate` once for the full
   final shape (topics + topic_participants + conversation_participants
   + conversation.topic_id + conversation.is_group_chat + UserSandbox
   scope_type/scope_id).
4. Hand-edit the autogen output for the partial unique predicate
   (autogen does not detect partial predicates — same constraint as
   shipped).

No production data to backfill. The PR remains a single ship.

---

## Access control matrix

`P(topic)` = caller is in `topic_participants` for the conversation's
topic.
`P(conv)` = caller is in `conversation_participants` for this
conversation.
`C(conv)` = caller is the conversation's `creator_user_id`.
`O(topic)` = caller is owner in `topic_participants`.

| Operation | Personal 1:1 | Standalone group chat | Topic conversation |
|---|---|---|---|
| **View** | C(conv) | P(conv) | P(topic) ∨ P(conv) |
| **SSE subscribe** | C(conv) | P(conv) | P(topic) ∨ P(conv) |
| **Send message** | C(conv) | P(conv) | P(topic) ∨ P(conv) (auto-joins on first send) |
| **HITL respond** | C(conv) | P(conv) | P(conv) only (not topic-only) |
| **Rename / delete** | C(conv) | C(conv) | C(conv) ∨ O(topic) |
| **Invite to conv** | n/a | any P(conv) | any P(conv) ∨ P(topic) |
| **Manage topic members** | n/a | n/a | O(topic) |
| **Delete topic** | n/a | n/a | O(topic) |

Key consequences:

- **HITL is conv-participant-only.** Topic-only members (those who
  haven't sent in this conv) cannot answer AskUser / SandboxConfirm.
  Rationale: HITL changes downstream agent behavior; the actor should
  be a real participant, not a passing browser.
- **Send-message auto-joins.** When `P(topic) ∧ ¬P(conv)` sends a
  message, the handler inserts a `conversation_participants` row in the
  same transaction.
- **Invite is any-participant.** Both standalone group chat and topic
  conversations: any current conv participant can pull in more people.
  No "creator-only invite" gate. (Topic membership is still owner-only.)

---

## SSE & visibility — answering "how does push work?"

SSE is **client-initiated**: the client opens
`GET /conversations/{id}/stream` and the server keeps the connection
open, writing events as they happen. There's no separate "push channel"
the server reaches into.

So allowing topic-only members to "see live updates while browsing" is
an **authorization decision**, not a push-mechanism change:

- Browser member opens the conv page → frontend starts the SSE request.
- Backend gates the SSE handler on `P(topic) ∨ P(conv)`.
- Connection stays open; events flow.

Membership-state events (new participants, removed participants, topic
metadata changes) flow over the same SSE stream and respect the same
gate.

---

## `is_group_chat` — the unified signal

Every "should this run behave as group chat?" decision reads
`Conversation.is_group_chat` directly:

| Behavior | Triggered when `is_group_chat = True` |
|---|---|
| Personal memory injection | **skipped** |
| Sender attribution metadata | **written** (`sender_user_id`, `sender_display_name`) |
| `[Name]: ` prefix at provider boundary | **rendered** by cubepi |
| Sender badge in UI | **shown** above user messages |

The signal is **decoupled from topic membership**. A topic with 5
topic_participants where conv X has 1 conversation_participant: conv X
behaves as personal (no group-chat treatment) until a second
participant joins. Then it flips.

`is_group_chat` is maintained by `ConversationRepository` whenever a
participant is added. The repo computes the new count and updates the
column in the same UPDATE statement (idempotent — re-running with the
same state is a no-op).

---

## Auto-join semantics

**Rule**: when a `P(topic) ∧ ¬P(conv)` sends a message in conv X, the
`send_message` handler atomically:

1. Inserts a row into `conversation_participants(conv_id=X, user_id=caller)`.
2. Updates `Conversation.is_group_chat` if the count crossed 1 → 2.
3. Proceeds with the message persistence as normal.

The insert is idempotent on the `(conv_id, user_id)` unique constraint
— a race where two messages arrive simultaneously results in one
`IntegrityError` swallowed silently after re-checking membership.

**No leave action.** `conversation_participants` is append-only:
- Once you've participated, the row stays as history of "people who
  spoke in this conversation".
- "Leaving the topic" still works (removes you from
  `topic_participants` → you lose topic-wide sidebar visibility) but
  any conv you actually participated in stays accessible because the
  conv_participants row keeps you in `P(conv)`.
- Re-sending after a long gap is a no-op (you're already in the table).

Rationale: the conv participant list is **descriptive** ("who has
spoken here"), not **subscriptive** ("who wants to receive events").
SSE handles the live-watch dimension. Adding a leave action would
require either deleting rows (loses history) or a third state ("left
but participated") — complexity for no concrete user need.

---

## Sandbox resolution (polymorphic scope)

`SandboxManager.get_or_create_for(scope_type, scope_id, *, org_id,
workspace_id)`.

The route helper `_resolve_sandbox_scope(session, ctx, conversation_id)`
returns `(scope_type, scope_id)`:

```
conversation_id is None
    → ('user', ctx.user.id)

conversation belongs to dedicated-mode topic, caller is P(topic)
    → ('topic', topic_id)

conversation belongs to creator-mode topic (or unspecified), caller is P(topic)
    → ('user', topic.creator_user_id)

conversation has no topic AND is_group_chat=True (standalone group)
    → ('conversation', conversation_id)

conversation has no topic AND is_group_chat=False (1:1)
    → ('user', ctx.user.id)
```

Authorization: caller must satisfy view access (above matrix); otherwise
404.

### Upgrade-path sandbox handling

**1:1 → standalone group chat** (no topic; just invite people):
- Sandbox stays keyed by `('user', creator_user_id)` until participants
  cross 1 → 2. On the transition, if a sandbox row exists, we
  UPDATE its scope to `('conversation', conv_id)`. New invitees see
  the same sandbox.

**Standalone group chat → topic**:
- UPDATE `user_sandboxes SET scope_type='topic', scope_id=:topic_id
  WHERE scope_type='conversation' AND scope_id=:conv_id`. The running
  sandbox is inherited; files don't move.

**1:1 / standalone group chat → topic** (single click upgrade):
- Same flow: create topic, link conv, then sandbox UPDATE as above.

These UPDATEs run in the same transaction as the upgrade endpoint so
either everything moves or nothing does.

---

## API changes

### New endpoint: invite to conversation (no topic required)

```
POST /api/v1/ws/{ws}/conversations/{conv_id}/participants
    Body: { user_ids: string[] }
    → 201 { participants: ConversationParticipant[] }

    Any P(conv) can invite. Validates workspace membership of each
    invitee. Inserts conversation_participants rows. Maintains
    is_group_chat. Returns the inserted rows.

DELETE — not implemented (no leave action; see Auto-join semantics).
```

### New endpoint: list conversation participants

```
GET /api/v1/ws/{ws}/conversations/{conv_id}/participants
    → 200 { items: ConversationParticipant[] }

    P(conv) ∨ P(topic) ∨ C(conv).
```

### Modified endpoint: upgrade conversation

The existing `/upgrade-to-topic` endpoint is renamed and split into two
distinct surfaces:

```
POST /api/v1/ws/{ws}/conversations/{conv_id}/invite-to-group
    Body: { user_ids: string[] }
    → 201 { conversation, participants }

    Convenience wrapper: invite N users to the current conversation
    (creates conversation_participants rows). Available on a 1:1 or
    existing group chat. Does NOT create a topic.

POST /api/v1/ws/{ws}/conversations/{conv_id}/upgrade-to-topic
    Body: { title, sandbox_mode?, member_user_ids[] }
    → 201 { topic, conversation, participants }

    Creates a Topic row, links the conversation, adds topic
    participants. Sandbox UPDATE runs in the same transaction (see
    above). Returns the topic + updated conversation. Blocked when the
    conversation has external bindings (IM / scheduled / trigger).
```

Both upgrades respect the same external-binding guard.

### Modified endpoint: topic-scoped conversation create

```
POST /api/v1/ws/{ws}/topics/{topic_id}/conversations
    Body: { title?, member_user_ids?[] }
    → 201 { conversation, participants }

    The creator is automatically inserted into conversation_participants
    (their first-message will not trigger auto-join again).
    member_user_ids initialize the conversation_participants list (each
    invitee must be in topic_participants).
```

### Removed surface

`UpgradeToTopicRequest.sandbox_mode = None` no longer silently defaults
to creator mode at the runtime layer. The frontend dialog requires
choosing dedicated or creator; null is rejected with 400.

---

## Frontend

### Sidebar grouping

The sidebar shows three kinds of entries:

1. **Personal conversations** (`topic_id IS NULL ∧ is_group_chat = False`)
2. **Standalone group chats** (`topic_id IS NULL ∧ is_group_chat = True`)
   — rendered like personal conversations but with a small group icon
3. **Topics** (`topic_id IS NOT NULL`) — expandable tree node; child
   conversations rendered nested

Mixed sort by `last_activity_at`. Standalone group chats are flat
entries (not in any topic).

### Invite affordance

Each conversation header gets an "Invite" button (`UserPlus` icon):

| Conversation kind | Click action |
|---|---|
| Personal 1:1 | Open `InviteToConversationDialog` → calls `/invite-to-group` (creates standalone group chat) |
| Standalone group chat | Open `InviteToConversationDialog` → calls `/invite-to-group` (adds participants) |
| Topic conversation | Open `InviteToConversationDialog` → calls `POST /conversations/{id}/participants` (conv-level invite) |

Each conversation header also gets a "Promote to topic" button
(`Layers` icon) visible only for `topic_id IS NULL` conversations.
Clicking opens `UpgradeToTopicDialog`. Standalone group chats can be
promoted to a topic without losing participants.

### Conversation header — participant strip

For `is_group_chat = True` conversations, the header renders a small
avatar group of the **conversation participants** (not topic
participants). Click to open a `ConversationMemberPanel` showing the
current conv participants list and the "Invite" form.

For topic conversations, an adjacent badge shows topic name + topic
member count, click → topic-level `MemberPanel`.

### Sender attribution UI

Unchanged from the previous design: `is_group_chat` user messages render
a `SenderBadge` above the bubble. The signal flips conv-by-conv, so a
quiet topic conv stays badge-less until a second voice joins.

---

## Permissions summary (rules-as-defined)

| Operation | Allowed when |
|---|---|
| Rename / delete personal 1:1 | `C(conv)` |
| Rename / delete standalone group chat | `C(conv)` |
| Rename / delete topic conversation | `C(conv) ∨ O(topic)` |
| Invite to standalone group chat | any `P(conv)` |
| Invite to topic conversation | any `P(conv) ∨ P(topic)` |
| Add member to topic | `O(topic)` |
| Remove member from topic | `O(topic)` (or member self) |
| Promote topic member to owner | `O(topic)` |
| Rename / archive topic | `O(topic)` |
| Send message in topic conversation | `P(topic) ∨ P(conv)` (auto-joins) |
| Answer HITL | `P(conv)` only |
| Subscribe SSE | `P(topic) ∨ P(conv)` |

---

## Schema changes — final list

**New table**:
- `conversation_participants` (id, org_id, workspace_id, conversation_id,
  user_id, joined_at; uniq on (conversation_id, user_id))

**Conversation column**:
- `is_group_chat: bool` (default False, maintained by repo)

**UserSandbox**:
- Drop `topic_id` column
- Drop `uq_user_sandbox_active_topic` partial unique
- Drop existing `uq_user_sandbox_active` partial unique
- Add `scope_type: str(20)`, `scope_id: str(20)`
- Add `uq_user_sandbox_active_scope` partial unique on
  `(org_id, workspace_id, scope_type, scope_id) WHERE status IN
  ('provisioning','running')`

**Public ID prefix**:
- Add `PREFIX_CPM = "cpm"` (conversation_participant)

**Single new migration** generated via `alembic revision --autogenerate`
after dropping the two shipped group-chat migrations and resetting the
worktree DB.

---

## What stays unchanged from the shipped design

- `topics` / `topic_participants` shape
- `Topic.last_activity_at` + monotonic bump
- `Topic.sandbox_mode` + dedicated/creator semantics
- Sender attribution at cubepi provider boundary
- Memory isolation rules (now keyed on `is_group_chat` not participant
  count)
- IM / triggers / scheduled-task guards (refuse topic conversations)
- Frontend cubepi pin (post-merge main)

---

## Out of scope (deferred)

- @mention with topic participants vs conv participants
- Conversation-level memory items
- Per-conversation sandbox mode override (currently topic-level only)
- Notification / red-dot for unread conversations
- Leave conversation action (intentionally not implemented; see
  Auto-join semantics)
- IM / triggers / scheduled-task topic-awareness (v2)

---

## Implementation phasing

Single PR (#250 extended), single migration. No staged rollout.

1. Reset migrations + regenerate
2. Backend: model + repo + auto-join + scope refactor
3. Backend: routes (new invite endpoint, rename upgrade)
4. Backend: tests
5. Frontend: types + store actions
6. Frontend: sidebar grouping + dialogs + participant strip
7. Three rounds of code review (as per project workflow)

Estimated incremental delta on top of the current branch: ~600 LOC
backend, ~400 LOC frontend, plus migration regeneration.
