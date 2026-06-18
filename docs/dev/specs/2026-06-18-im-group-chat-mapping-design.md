# IM Group Chat ↔ Topic Mapping

## Scope

Bridge external IM group chats (Slack channels, Feishu groups, Discord
guild channels) to cubebox topics. Admin binds a channel to a bot
account and chooses a mapping mode; the runtime creates topics and
conversations on the fly as users @-mention the bot.

**In scope (v1):**

- Per-channel mapping mode: `isolated` (current per-user behavior) vs
  `shared` (channel → topic, thread → conversation).
- Lazy topic creation on first @bot in shared mode.
- Thread-scoped conversations under the topic.
- Auto-join: first @bot resolves identity and adds the user as topic
  participant + conversation participant.
- HITL open to all resolved participants (align with native group chat).
- Sandbox mode selection at binding time (dedicated / creator).
- Channel Bindings management UI in bot account detail panel.

**Out of scope (v1):**

- Batch member sync (pulling the full channel member list from IM).
- Backfilling IM message history into cubebox.
- Non-@mention triggers (keyword, auto-reply).
- Cross-bot channel bindings (one channel, one bot).
- Topic archival when a channel is deleted/archived in IM.

---

## Data Model

### New table: `im_channel_bindings`

Stores the admin-configured mapping for each channel a bot account
operates in. One row per (account, channel) pair.

| Column | Type | Notes |
|---|---|---|
| `id` | `str` | PK, public-id prefix `icb` |
| `org_id` | `str` FK | OrgScopedMixin |
| `workspace_id` | `str` FK | OrgScopedMixin |
| `account_id` | `str` FK → `im_connector_accounts.id` | ON DELETE CASCADE |
| `channel_id` | `str(128)` | Platform channel/group ID |
| `channel_name` | `str(255)` | Display name, updated on each inbound event |
| `mode` | `str(16)` | `"isolated"` (default) or `"shared"` |
| `sandbox_mode` | `str(16)` nullable | Only for shared mode: `"dedicated"` (default) or `"creator"` |
| `topic_id` | `str` FK → `topics.id` nullable | Populated lazily on first @bot in shared mode |
| `created_at` | `timestamptz` | |
| `updated_at` | `timestamptz` | |

Unique index: `uq_im_channel_binding` on `(account_id, channel_id)`.

The binding is created when the admin explicitly configures a channel.
Channels without a binding row default to `isolated` mode (current
behavior — zero-migration path for existing deployments).

### IMThreadLink changes

No schema changes. The existing `(account_id, channel_id, scope_key)`
unique index continues to work. In shared mode the scope_key values
change from `"u:{sender}"` to `"ch"` (channel-level default
conversation) or `"t:{thread_id}"` (thread-scoped conversation).

---

## Scope Key Strategy

The connector's `parse_inbound` determines the scope key. Today it
always includes the sender (`make_participant_scope`). In shared mode it
must switch to channel/thread scoping.

| Mode | IM context | scope_key | scope_kind | Cubebox result |
|---|---|---|---|---|
| isolated | DM | `"dm"` | `dm` | Per-user conversation (unchanged) |
| isolated | Channel @bot | `"u:{sender}"` | `channel` | Per-user conversation (unchanged) |
| isolated | Thread @bot | `"u:{sender}\|t:{thread}"` | `thread` | Per-user-per-thread conversation (unchanged) |
| shared | DM | `"dm"` | `dm` | Per-user conversation (DM never shared) |
| shared | Channel @bot (no thread) | `"ch"` | `channel` | Channel-level default conversation under topic |
| shared | Thread @bot | `"t:{thread}"` | `thread` | Thread-scoped conversation under topic |

DM always uses isolated behavior regardless of binding mode — there is
no "group" in a DM.

### Scope key resolution flow

`parse_inbound` needs the binding mode to choose the right scope key.
The binding is looked up by `(account_id, channel_id)` — a cheap
indexed read that can be cached in-process per account for the duration
of the gateway connection (invalidated on binding CRUD).

---

## Topic & Conversation Lifecycle (shared mode)

### First @bot in a channel (no binding.topic_id yet)

1. `ingest_inbound_event` sees `binding.mode == "shared"` and
   `binding.topic_id IS NULL`.
2. Within the same transaction:
   a. Create a `Topic` (title = `channel_name`, sandbox_mode from
      binding, creator = the acting_user of the bot account).
   b. Add the resolved sender as `TopicParticipant(role="member")`.
      The acting_user is added as `TopicParticipant(role="owner")`.
   c. Create the first `Conversation` under the topic.
   d. Add the sender as `ConversationParticipant`.
   e. Update `binding.topic_id`.
   f. Create the `IMThreadLink` as usual.
3. Enqueue `IMRunQueueItem`.

### Subsequent @bot in same channel (binding.topic_id set)

1. `ingest_inbound_event` resolves binding → has `topic_id`.
2. `get_or_create_thread_link` finds or creates the thread link.
   - If creating: also create a new `Conversation` under the topic,
     add sender as `ConversationParticipant`.
   - If existing: load the conversation; if sender is not yet a
     `ConversationParticipant`, add them (auto-join).
3. If sender is not yet a `TopicParticipant`, add them.
4. Enqueue `IMRunQueueItem`.

### Thread handling

- **Channel-level @bot** (no thread_ts): Uses scope_key `"ch"` — maps
  to a single "main" conversation under the topic. The outbound reply
  creates a new thread in the IM channel (so the channel doesn't get
  flooded). The thread_ts of that reply is recorded in the thread link's
  `reply_to_id` for future reference.
- **Thread @bot**: Uses scope_key `"t:{thread_ts}"` — each thread maps
  to its own conversation under the topic.

---

## Identity & Participant Management

### Identity resolution (unchanged)

The existing `resolve_or_reject` flow works as-is. Each inbound event
resolves the sender's IM identity to a cubebox user. Unresolved users
get the `/link` prompt.

### Auto-join on first message

When a resolved user first interacts in a shared-mode channel:

1. **TopicParticipant**: Added as `role="member"` if not already present.
2. **ConversationParticipant**: Added via `ensure_participant` (existing
   idempotent upsert) which also maintains `is_group_chat`.

This mirrors the native group chat's "auto-join on first send" pattern.

### DM identity and sandbox

DM conversations always use the resolved user's personal sandbox
(`scope_type="user"`, `scope_id=user_id`). This is the existing
behavior and is not affected by channel binding configuration.

---

## Sandbox Resolution

| Scenario | scope_type | scope_id |
|---|---|---|
| DM (any mode) | `user` | resolved user's ID |
| Isolated channel/thread | `user` | resolved user's ID |
| Shared channel/thread, `sandbox_mode="dedicated"` | `topic` | `topic_id` |
| Shared channel/thread, `sandbox_mode="creator"` | `user` | `acting_user_id` of the bot account |

For shared/dedicated: all participants share one sandbox keyed to the
topic — same as native topic conversations with `sandbox_mode="dedicated"`.

For shared/creator: the agent runs use the bot account's acting_user's
personal sandbox. This means the IM channel effectively shares the same
environment as that user's personal conversations.

### Sandbox mode explanation (shown in UI)

When configuring a shared-mode binding:

> **Dedicated** (default): A sandbox is created exclusively for this
> channel. All participants share it. Best for collaborative work where
> the group needs a clean shared environment.
>
> **Use bot owner's sandbox**: The agent uses the personal sandbox of
> the user who connected this bot ({acting_user_email}). The channel
> shares that user's existing files and environment. Best when you want
> the bot to operate in a pre-configured environment.

---

## HITL in Shared Mode

Native group chat already allows any `ConversationParticipant` to answer
AskUser / SandboxConfirm — no single-user lock, first responder wins
via `claim_resume` CAS.

The IM layer currently has an additional `awaiting_responder` Redis gate
that locks HITL to one `open_id`. In shared mode this gate is skipped:

- The HITL card/buttons are posted to the thread (visible to everyone).
- Any user in the channel can click the button.
- The card-action handler validates that the responder has a resolved
  identity (is a `ConversationParticipant`), then calls
  `resume_run_with_answer` — which uses the same first-responder CAS
  as the native path.
- In isolated mode the `awaiting_responder` gate continues to work
  as today.

---

## Outbound Rendering

No changes to the `OutboundRunTailer` or `OpDispatcher` protocol. The
tailer already sends output to whatever channel/thread the
`IMRunQueueItem` specifies.

The only behavioral change: when a channel-level @bot (no thread)
triggers a run in shared mode, the **first outbound message creates a
new IM thread** as its reply. The thread_ts is stored in
`queue_item.reply_to_id` so subsequent streaming updates and HITL
cards go to the same thread.

This is already how Slack/Feishu connectors work when `reply_to_id`
is set — no connector-level changes needed. The change is in
`ingest_inbound_event`: for shared-mode channel-level messages, set
`reply_in_thread=True` on the queue item so the renderer opens a thread.

---

## Worker & Resume Guards

`worker.py` lines 119-129 and `resume.py` lines 101-109 currently
refuse to dispatch runs for conversations with `topic_id IS NOT NULL`
or `is_group_chat = True`. These guards were added as v1 safety rails.

For shared-mode IM runs, the conversation will have a `topic_id` and
possibly `is_group_chat=True`. The guards need to be relaxed:

- **Worker**: Allow dispatch when the queue item's source is IM and the
  conversation's topic was created by an IM channel binding (check
  `binding.topic_id == conversation.topic_id`).
- **Resume**: Same relaxation for HITL resume on IM-triggered runs.

The guards remain active for non-IM paths (scheduled tasks, triggers)
until those are explicitly wired up for group chat support.

---

## API

### Channel Bindings CRUD (workspace-scoped)

All under `POST/GET/PATCH/DELETE /api/v1/ws/{ws}/im/accounts/{account_id}/channel-bindings/`.

**List bindings**: `GET .../channel-bindings/`
Returns all bindings for the account. Includes `topic_id` and
`channel_name` if populated.

**Create binding**: `POST .../channel-bindings/`
```json
{
  "channel_id": "C07...",
  "channel_name": "project-alpha",
  "mode": "shared",
  "sandbox_mode": "dedicated"
}
```
Validates: channel_id not already bound to this account; mode is valid;
sandbox_mode required when mode is shared.

**Update binding**: `PATCH .../channel-bindings/{binding_id}`
```json
{ "mode": "isolated" }
```
Switching from shared → isolated does not delete the topic. The topic
remains accessible in cubebox but new IM messages go to per-user
conversations. Switching from isolated → shared creates the topic on
the next @bot.

**Delete binding**: `DELETE .../channel-bindings/{binding_id}`
Removes the binding. Does not delete the topic or conversations.

---

## Frontend

### Bot Account Detail: Channel Bindings Tab

Add a "Channels" tab to the existing `ImAccountDetailPanel`. Content:

- Table of channel bindings: channel name, mode badge
  (`isolated`/`shared`), sandbox mode (if shared), topic link (if
  created), created date.
- "Add Channel" button → dialog:
  - Channel ID input (text field — we don't have a channel picker since
    the bot doesn't enumerate channels).
  - Channel name input.
  - Mode radio: Isolated (default) / Shared.
  - Sandbox mode radio (shown when shared selected): Dedicated (default) /
    Use bot owner's sandbox. With the explanation text from the Sandbox
    section above.
- Row actions: Edit mode, Delete binding.

### No changes to sidebar or conversation UI

Topic conversations created by IM bindings are regular topics. They
appear in the sidebar for participants who have cubebox accounts. No
special IM-specific UI in the conversation view.

---

## Migration Path

- Existing deployments have zero `im_channel_bindings` rows.
- All channels default to isolated mode (absence of a binding row =
  isolated). Zero behavior change on upgrade.
- Admin opts in per-channel by creating a binding with `mode="shared"`.

---

## Testing

**E2E tests** (against real test DB):

1. Create an account + channel binding (shared mode) → first inbound
   event → verify topic created, conversation created, thread link
   created with correct scope_key, sender added as participant.
2. Second inbound event in same thread → verify reuses same conversation,
   sender auto-joined.
3. Different user in same channel, new thread → verify new conversation
   under same topic, second user added as topic participant.
4. HITL in shared mode → verify any participant can resume.
5. Isolated mode binding → verify per-user scope_key, no topic created.
6. Mode switch shared → isolated → verify new messages use per-user
   scope, existing topic untouched.
7. DM always isolated regardless of binding.

**Unit tests:**

1. Scope key selection logic (given binding mode + event context →
   correct scope_key).
2. Channel binding CRUD validation.
