# Schedule & Trigger Destinations: Topic and IM Channel

**Status**: Design
**Date**: 2026-06-23
**Author**: xfgong

## Summary

Extend `ScheduledTask` and `Trigger` so that runs which create a fresh
conversation can target a specific `Topic`, and so that schedules/triggers
created from inside an IM conversation (Slack, Feishu, Discord, etc.) post
their results back into the originating IM channel — even after the user
runs `/new` in that channel.

The mental model: a schedule/trigger has a **destination**, which is one
of three discrete shapes. The current two-shape model (`fixed` to a
specific conversation, or `new_each_run`) gains a third (`im_channel`),
and `new_each_run` gains an optional `topic_id`. IM-channel destinations
follow the IM channel "live" — `/new`, shared-mode topic routing, and
isolated-mode per-user scoping all behave exactly as they do when a real
user message arrives, because dispatch reuses the same conversation
resolution that the inbound IM pipeline uses.

## Goals

- Schedules and triggers that create a new conversation can be tied to a
  `Topic` so the new conversation lands inside it.
- Schedules and triggers created from inside an IM conversation post back
  to the same IM channel/scope, surviving `/new`.
- Dispatch reuses the existing IM inbound conversation-resolution path —
  no second outbound mechanism, no duplicated channel-routing logic.
- Data integrity preserved across topic deletion and IM-account deletion;
  schedules degrade gracefully instead of disappearing.

## Non-goals

- Creating `im_channel`-mode schedules/triggers from the web UI. Only the
  agent-tool path inside IM produces these.
- Cross-platform IM routing (e.g. fire a schedule that posts to Slack
  *and* Discord). Each row binds to exactly one IM account.
- A Topic detail page or an IM channel detail page. Filtering existing
  list endpoints is enough until those pages exist.
- Owner-leaves-workspace handling — preserve current behavior.
- Real-LLM tests as part of the suite.

## Data model

### `scheduled_tasks`

`target_mode` is the discriminator. Its value space grows from
`"fixed" | "new_each_run"` to `"fixed" | "new_each_run" | "im_channel"`.

New nullable columns:

| Column | Type | FK | Used when |
|---|---|---|---|
| `topic_id` | text | → `topics.id` ON DELETE SET NULL | `new_each_run` |
| `im_account_id` | text | → `im_connector_accounts.id` ON DELETE SET NULL | `im_channel` |
| `im_channel_id` | text | — (external platform id) | `im_channel` |
| `im_scope_key` | text | — | `im_channel` |

### `triggers`

`conversation_policy` plays the same role as `target_mode` and gets the
same value expansion: `"new_each_time" | "im_channel"`. The same four
nullable columns are added with identical semantics. The existing
`target_type` field (`inline | managed_agent`) is orthogonal and
untouched.

### Constraints

Neither `scheduled_tasks` nor `triggers` carries a CHECK constraint on
the discriminator column today (verified via `\d scheduled_tasks` and
`\d triggers` against the development database). The migration simply
**adds** new constraints — no `drop_constraint` call is needed for the
discriminator. The value-space expansion of `target_mode` /
`conversation_policy` is enforced entirely by the new shape CHECKs
below.

DB-level CHECK constraints stay **minimal** to avoid colliding with
`ON DELETE SET NULL` cascades:

- `target_mode = 'fixed'` ⇒ `target_conversation_id IS NOT NULL`.
- `target_mode = 'im_channel'` ⇒ `target_conversation_id IS NULL`.
- `target_mode ∈ ('fixed', 'new_each_run')` ⇒ `im_account_id IS NULL`
  (mutually exclusive with IM mode).
- Same shape on `triggers` substituting `conversation_policy`.

"All three `im_*` are set when in `im_channel` mode" is **not** enforced
at the DB layer — Postgres CHECK is not deferrable, so it would block the
SET NULL cascade when a parent IM account is deleted. The completeness
invariant is enforced at the API and service layer on write, and the
dispatcher defensively treats a partially-NULL `im_*` as a failed run.

### Indexes

- `(topic_id)` on both tables — supports `?topic_id=...` filtering.
- `(im_account_id, im_channel_id)` on both tables — supports
  `?im_account_id=...&im_channel_id=...` filtering.

### Public-id prefixes

Unchanged: `stask`, `stkrn`, `trig`, `trev`.

## Dispatch flow

### `new_each_run` / `new_each_time` + `topic_id`

`ConversationRepository.create(...)` grows an optional `topic_id`
parameter. `schedules/dispatch.py` and `triggers/pipeline.py` pass the
row's `topic_id` through. The existing block at
`schedules/dispatch.py:71-77` that raises `NotImplementedError` for
topic-pinned fixed targets is deleted — the new constraint disallows
`topic_id` in `fixed` mode entirely.

### `im_channel` mode — enqueue, don't run inline

The IM outbound tailer (`runtime.py::_on_run_started`) is a closure
constructed at app startup capturing 8+ dependencies (Redis state, key
prefix, gateway cache, secret cache, session maker, `RunManager`,
config). A schedule/trigger dispatcher running outside that closure
cannot recreate it without re-plumbing the whole runtime. So
**schedule/trigger `im_channel` dispatch does not call `start_run` or
`on_run_started` itself** — it enqueues a normal `IMRunQueueItem` and
lets the existing `IMRunQueueWorker` do the run + tailer wiring.

Extract two helpers:

```
backend/cubebox/im/conversation_resolver.py

@dataclass(frozen=True)
class ResolvedIMConversation:
    conversation_id: str
    topic_id: str | None
    is_group_chat: bool
    sandbox_mode: str | None

async def resolve_im_conversation(
    session,
    account: IMConnectorAccount,
    *,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    effective_user_id: str,
    title_hint: str,
    origin: Literal["inbound", "schedule", "trigger"],
) -> ResolvedIMConversation:
    # Replicates the full inbound side-effect set in im/inbound.py:160-292:
    # 1. Read IMChannelBinding(account_id, channel_id).
    # 2. If shared mode and binding.topic_id is None:
    #    - Create Topic, set binding.topic_id.
    #    - Insert TopicParticipant(role=owner) for acting_user_id.
    #    - Insert TopicParticipant(role=member) for effective_user_id
    #      when different from acting_user_id.
    # 3. If shared mode and binding.topic_id is set:
    #    - Auto-join effective_user as TopicParticipant if missing.
    # 4. get_or_create_thread_link(...) with a make_conversation_id
    #    closure that mints Conversation(topic_id, is_group_chat=is_shared)
    #    AND inserts ConversationParticipant for effective_user_id when shared.
    # 5. If link was NOT newly created and shared mode: post-link
    #    participant top-up for effective_user_id (ConversationParticipant
    #    + TopicParticipant idempotent inserts).
    # 6. Return ResolvedIMConversation with binding-derived fields so the
    #    caller can build RunContext correctly.
```

```
backend/cubebox/im/run_handoff.py

async def enqueue_im_channel_run(
    session,
    *,
    account: IMConnectorAccount,
    conversation_id: str,
    content: str,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    owner_user_id: str,
    platform_event_id: str,   # idempotency key — see below
) -> None:
    # 1. Resolve sender_im_user_id from IMIdentityLink (may be None).
    # 2. INSERT IMWebhookReceipt(account_id, platform_event_id,
    #    status='completed').
    #    Unique (account_id, platform_event_id) guarantees idempotency:
    #    a retried dispatcher tick on the same scheduled-occurrence row
    #    cannot create a duplicate.
    # 3. INSERT IMRunQueueItem(account_id, receipt_id, conversation_id,
    #    content, channel_id, scope_key, scope_kind, sender_im_user_id,
    #    sender_open_id=None, reply_to_id=None, inbound_message_id=None,
    #    status='pending').
    # Both writes happen inside the calling transaction; on the
    # dispatcher's commit the worker can claim the row.
```

`im/inbound.py:160-292` is refactored to call `resolve_im_conversation`
for the conversation + participant work; the existing
`IMRunQueueItem`/`IMWebhookReceipt` writes there stay as-is (real
inbound has a real webhook event id).

Schedule dispatcher pseudo-flow:

```
schedule dispatch (target_mode == 'im_channel'):
    run_row = ScheduledTaskRun(
        scheduled_task_id=task.id, scheduled_for=tick_at,
        state='claimed', claimed_at=now,
        conversation_id=None, run_id=None,
    )
    session.add(run_row); session.flush()  # gets run_row.id

    account = session.get(IMConnectorAccount, task.im_account_id)
    if account is None:
        run_row.state = 'failed'
        run_row.detail = 'im_account_unlinked'
        session.commit(); return

    resolved = await resolve_im_conversation(
        session, account,
        channel_id=task.im_channel_id,
        scope_key=task.im_scope_key,
        scope_kind=derive_scope_kind(task.im_scope_key),
        effective_user_id=task.owner_user_id,
        title_hint=task.prompt[:80],
        origin='schedule',
    )
    run_row.conversation_id = resolved.conversation_id
    run_row.state = 'enqueued'
    # run_id stays NULL; the worker assigns it when it picks the item up.

    await enqueue_im_channel_run(
        session, account=account,
        conversation_id=resolved.conversation_id,
        content=task.prompt,
        channel_id=task.im_channel_id,
        scope_key=task.im_scope_key,
        scope_kind=derive_scope_kind(task.im_scope_key),
        owner_user_id=task.owner_user_id,
        platform_event_id=f"schedule:{run_row.id}",
    )
    await session.commit()
```

`fixed` and `new_each_run` keep calling `RunManager.start_run` inline as
they do today.

Trigger pipeline mirrors the same logic — `TriggerEvent` row, then
`enqueue_im_channel_run(platform_event_id=f"trigger:{event_row.id}")`.

### Observability for `im_channel` ScheduledTaskRun rows

Because run execution is async via the worker, `ScheduledTaskRun.run_id`
stays NULL after dispatch for `im_channel` mode. State transitions stop
at `state='enqueued'`. End-of-run reconciliation (claiming, retries,
success/failure) is owned by the IM worker on the queue item, not by
the schedule poller. This is acceptable for v1 — the user observes the
run by looking at the IM channel. A follow-up task can add a reconciler
that ties IMRunQueueItem terminal state back to ScheduledTaskRun via
the deterministic `platform_event_id` if richer observability is
needed.

### Outbound routing

Because we enqueue a real `IMRunQueueItem`, the existing worker calls
`RunManager.start_run` and then `_on_run_started`, which builds the
per-platform tailer. **No new outbound code path is introduced.**

## Agent tool: defaults and IM context

The "create scheduled task" / "create trigger" agent tools detect IM
origin by querying the current conversation's `IMThreadLink` rather than
relying on any new field in `cubepi`'s `RunContext`:

```
def detect_im_origin(session, conv_id) -> dict | None:
    link = session.query(IMThreadLink).filter_by(
        conversation_id=conv_id
    ).one_or_none()
    if link is None:
        return None
    return {
        "im_account_id": link.account_id,
        "im_channel_id": link.channel_id,
        "im_scope_key":  link.scope_key,
    }
```

This means **no changes to the cubepi pinned dependency are required**.
The tool reads `conversation_id` from cubepi's tool context as it
already does, then does one DB lookup.

Default derivation when the agent calls a create tool without an
explicit `target_mode`:

| Origin of the run | Current conversation has `topic_id`? | Default |
|---|---|---|
| IM (has IMThreadLink) | — | `im_channel` + `im_*` from link |
| Web/API (no IMThreadLink) | — | `fixed` + `target_conversation_id` = current conv |

If the agent passes `target_mode="new_each_run"` explicitly and omits
`topic_id`, and the current conversation has a non-null `topic_id`, the
tool fills in `topic_id` from the current conversation (so "create a
schedule in a new conversation" inside a topic stays inside that topic).

The agent can always override by passing `target_mode` and the
corresponding fields explicitly.

These defaults live in the tool implementation, not in DB triggers or
Pydantic defaults — so REST clients still have to supply a fully-formed
payload and the contracts stay symmetric for both paths.

## API and validation

### Schema changes

`backend/cubebox/api/schemas/ws_scheduled_tasks.py` and
`backend/cubebox/api/schemas/trigger.py` grow the new fields and a
`model_validator(mode="after")` that enforces:

- `fixed`: `target_conversation_id` required;
  `topic_id` / `im_*` must be null.
- `new_each_run` / `new_each_time`: `target_conversation_id` and `im_*`
  must be null; `topic_id` optional.
- `im_channel`: `target_conversation_id` and `topic_id` must be null;
  `im_account_id`, `im_channel_id`, `im_scope_key` all required.

The validator is extracted to a pure function `ScheduleTargetSpec.validate()`
in the service layer and reused by:

- The Pydantic create/update schemas.
- The agent-tool implementations after default derivation.

DB CHECK constraints (above) are the last-resort safety net.

### Routes

No new routes. The existing handlers in
`api/routes/v1/ws_scheduled_tasks.py` and `api/routes/v1/ws_triggers.py`
accept the new fields.

PATCH **does not allow** changing `target_mode` or
`conversation_policy`. The combination of fields valid in one mode is
not valid in another, and supporting cross-mode PATCH would require
either erasing the now-invalid fields or refusing partial input — both
are landmines. Users who want to change the destination type delete the
row and create a new one.

### List filtering

Existing list endpoints accept new optional query parameters:

- `?topic_id=<topic_public_id>`
- `?im_account_id=<account_public_id>&im_channel_id=<external_id>`

No new endpoints, no scope changes.

## Frontend

### What the web UI does

- Schedule create/edit form: replace single "target conversation" input
  with a 3-radio "destination":
  - **This conversation (fixed)** — existing default for non-topic
    conversations.
  - **New conversation each run** — shows a topic picker. Empty selection
    = no topic.
  - **IM channel** — disabled with tooltip "Created from IM only".
- Trigger create/edit form: same shape with two radios (`new_each_time`,
  `im_channel` disabled).
- Schedule/trigger list table grows a "Destination" column rendering:
  - `fixed` → conversation title chip.
  - `new_each_run` + topic → topic chip.
  - `new_each_run` no topic → "New conversation" label.
  - `im_channel` → IM platform icon + `IMChannelBinding.channel_name`
    (fallback to `channel_id`).

### What the web UI does **not** do

- No new way to create `im_channel`-mode rows from the web. PATCH is
  allowed on prompt / cron / timezone fields; the destination block is
  read-only on `im_channel` rows.
- Topic detail page does not exist yet; we do not add it as part of this
  work. The filter API is in place so future work can wire up the page
  cheaply.
- IM channel admin/detail page is out of scope for the same reason.

### Files most likely touched

- `frontend/packages/web/components/scheduled-tasks/` — form and list
  (exact filenames discovered during implementation).
- `frontend/packages/web/components/triggers/` — same.
- `frontend/packages/core/src/api/` — typed request/response models for
  scheduled tasks and triggers (the new fields).
- `frontend/packages/core/src/stores/` — list filters.

## Edge cases

| Case | Behavior |
|---|---|
| Topic deleted | `topic_id` SET NULL; schedule keeps running, destination column shows "New conversation". |
| IM account deleted | `im_account_id` SET NULL; next fire writes `ScheduledTaskRun(state='failed', detail='im_account_unlinked')` (or `TriggerEvent(status='failed', last_error='im_account_unlinked')` on the trigger side); schedule/trigger row preserved for cleanup. |
| Schedule dispatcher commit fails mid-flight | Whole tick is one DB transaction (`ScheduledTaskRun` + `IMWebhookReceipt` + `IMRunQueueItem` written together). A crash before `commit()` leaves zero rows; the next poller tick re-attempts cleanly. A crash after `commit()` is identical to "row already exists" — the unique constraint on `(scheduled_task_id, scheduled_for)` blocks duplicate insertion. |
| User runs `/new` then schedule fires | `IMThreadLink` is gone; `resolve_im_conversation` mints a fresh conversation and link. New thread appears in the IM channel — consistent with "channel survives, conversation can rotate." |
| Multiple schedules on same channel/scope | All resolve to the same conversation if the link exists; concurrent run handling is whatever `RunManager` already does. |
| IM binding switches `isolated` ↔ `shared` | Live binding: next fire sees the new mode. If shared mode pins a new topic, future schedule firings land there. |
| `IMChannelBinding` row deleted while account stays | `resolve_im_conversation` falls back to isolated-mode defaults (no topic inheritance, no sandbox-mode override). The schedule continues to fire; behavior matches "channel never had a binding configured." |
| Platform-side channel deleted | DB unchanged; outbound fails through existing connector error handling. Not addressed here. |
| Schedule owner leaves workspace | Existing behavior preserved. Out of scope. |
| PATCH attempts to change `target_mode` | Rejected with 422. User must delete + recreate. |
| PATCH a prompt/cron field on an `im_channel` row | Allowed. Destination fields are immutable. |

## Migration

Single alembic revision per area (one for schedules, one for triggers,
or one combined — implementation choice). `target_mode` and
`conversation_policy` are `text` columns today (no Postgres ENUM type
involved); there is also no existing CHECK constraint on these columns,
so the migration only **adds** constraints, never drops. For each
table:

1. Add the four new nullable columns with FKs (`ON DELETE SET NULL` on
   `topic_id` and `im_account_id`; `im_channel_id` / `im_scope_key` are
   plain text and need no FK).
2. Add the two indexes.
3. Add the minimal CHECK constraints (per the data-model section),
   including the value-space enforcement on the discriminator column.

All existing rows have `target_mode ∈ {fixed, new_each_run}` and the new
columns null, which is valid under the new constraints. No data
backfill.

`schedules/dispatch.py:71-77` (the `NotImplementedError` block for
topic-pinned fixed targets) is deleted in the same PR, after the
migration has been applied.

## Testing

### Backend e2e (`backend/tests/e2e/`)

Each test name corresponds to a real business invariant:

- `test_new_each_run_with_topic_creates_conv_in_topic`
- `test_im_channel_reuses_existing_link`
- `test_im_channel_creates_fresh_after_new` (delete IMThreadLink between
  setup and fire)
- `test_im_channel_shared_mode_inherits_binding_topic`
- `test_im_account_deletion_marks_run_failed`
- `test_topic_deletion_sets_topic_id_null_and_continues_running`
- `test_validation_rejects_im_channel_with_topic`
- `test_validation_rejects_fixed_without_conversation`
- `test_validation_rejects_patch_changing_target_mode`
- `test_list_filter_by_topic_id`
- `test_list_filter_by_im_account_and_channel`
- Parallel set for triggers.

### Backend unit

- `ScheduleTargetSpec.validate()` matrix across all
  `(target_mode, populated_fields)` combinations.
- `detect_im_origin()` with and without a matching `IMThreadLink`.
- `resolve_im_conversation()` three branches: link-exists-alive,
  link-exists-conv-soft-deleted, no-link.

### Frontend e2e

Two flows only (per CLAUDE.md's "test invariants, not DOM"):

- Create schedule via UI → switch to "New conversation each run" → pick
  topic → submit → verify the POST body and the destination chip on the
  list page.
- Open an `im_channel` schedule (seeded directly into the test DB) →
  verify destination block is disabled, prompt/cron are editable, and
  the PATCH body only carries the editable fields.

### Real-LLM and outbound

Not part of automated tests. Author runs a manual end-to-end smoke test
in a real IM (Slack/Feishu/Discord) after the PR is merged but before
calling the work done.

## Implementation order (sketch)

1. Migration + model column additions + minimal CHECK constraints.
2. `resolve_im_conversation` helper + refactor `_make_conversation_id`
   to use it. Tests for the helper.
3. Service-layer validator (`ScheduleTargetSpec.validate`) + Pydantic
   wiring.
4. Schedule dispatcher: `im_channel` branch + delete the old
   `NotImplementedError`. Tests.
5. Trigger pipeline: `im_channel` branch. Tests.
6. Agent tool default derivation (depends on whether tools exist
   today — confirmed during step 1).
7. Frontend form + list updates.
8. Manual smoke in IM, then PR.

## Out of scope / future work

- Topic detail page listing schedules/triggers (filter API is ready).
- IM channel admin page.
- `enabled` / `paused` boolean on schedules — separate concern.
- Multi-destination broadcasts (post to two IM channels at once).
- Web UI for creating `im_channel`-mode rows directly.
- Cleaning up schedule rows orphaned by FK SET NULL (manual today; a
  background sweep is a possible follow-up).
