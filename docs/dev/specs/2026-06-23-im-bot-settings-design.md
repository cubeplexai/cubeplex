# IM Bot Settings: account-level routing + topic mode

Date: 2026-06-23
Status: Draft
Slug: im-bot-settings

## Problem

Two gaps, one theme — **how an IM bot turns inbound messages into cubeplex
conversations is not configurable from the product.**

1. **Topic grouping is invisible / not configurable.** Today a bot's
   conversations only roll up under a cubeplex `Topic` when a channel is in
   `shared` mode. In the default `isolated` mode every conversation is
   `topic_id = NULL` — a flat, standalone "personal conversation" per
   sender. There is no way to say "group this bot's chats under a Topic"
   and no Topic identity (avatar / name) tying them to the bot.

2. **shared / isolated has no UI.** `IMChannelBinding` CRUD exists in the
   backend (`ws_im.py`) but the frontend never calls it — `im.ts` has no
   channel-binding functions and `ImAccountDetailPanel` shows none. So the
   routing mode is only settable by hitting the API or editing the DB.

## Decision: configuration is account-level, not per-channel

A bot is bound to a workspace and behaves uniformly. Per-channel
differentiation is handled by **creating a second bot**, not by per-channel
config. Therefore:

- All bot behavior lives on `IMConnectorAccount.config` (the existing,
  currently-empty JSON column) and applies to **every** channel the bot is
  in.
- The UI never shows `channel_id`. No channel list, no per-channel rows.
- **`IMChannelBinding` is removed entirely** (see "Why the table goes
  away"). Project has not shipped; we cut over cleanly, no compat shim.

## User-facing settings (two knobs)

Stored under `account.config["bot_settings"]`, validated by a Pydantic
model:

```python
class IMBotSettings(BaseModel):
    routing_mode: Literal["isolated", "shared"] = "isolated"
    topic_mode: Literal["topic", "flat"] = "topic"
    sandbox_mode: str | None = None   # required when routing_mode == "shared"
```

- **routing_mode**
  - `isolated` (default): each sender gets their own conversation
    (per-sender). Current default behavior.
  - `shared`: everyone in a channel shares one conversation (per-channel).
- **topic_mode**
  - `topic` (default): each conversation rolls up under a cubeplex `Topic`.
  - `flat`: no Topic — standalone personal conversations (today's isolated
    behavior).

`shared` implies a Topic regardless of `topic_mode` (a shared channel is
already topic-shaped). `topic_mode` is the meaningful knob for `isolated`.

## Topic identity via `attributes`

`Topic` and `Conversation` each get a JSON `attributes` column for source
metadata. IM-created topics carry an `im` sub-object:

```json
{"im": {"platform": "feishu", "account_id": "ima_…",
        "bot_name": "…", "bot_avatar_url": "…",
        "channel_id": "…", "channel_name": "…", "scope_kind": "dm|participant"}}
```

- Topic **title**: bot name (DM) or channel name (group). Plain text.
- Topic **avatar** in the sidebar: read `attributes.im.bot_avatar_url`.
  `Topic` gets no avatar column — the existing sidebar renders participant
  avatars; the bot avatar comes from `attributes` instead. No bot user /
  avatar-sync needed.

## Topic granularity & ownership (from product Q&A)

- `isolated` + `topic`: **one Topic per (account, channel, sender)**, owned
  by the resolved sender (`creator_user_id = effective_user_id`),
  visibility unchanged (personal — only that user sees it). The bot is NOT
  a Topic participant; its identity lives in `attributes.im`.
- `shared`: one Topic per (account, channel), as today.
- DM: one Topic per (account, sender) — the DM partner.

## `/new` under topic mode: persistent topic, rotated conversation (option A)

The Topic **persists across `/new`**. `/new` starts a fresh `Conversation`
*under the same Topic*, so a sender's whole history with the bot stays
grouped. This requires a durable topic anchor that survives `/new` (today
`/new` deletes the `IMThreadLink`).

**Anchor: add `topic_id` (nullable FK) to `IMThreadLink`.** The link row is
keyed `(account_id, channel_id, scope_key)` — exactly the per-sender (or
per-channel, in shared) granularity we need.

`/new` behavior becomes mode-dependent:
- `flat`: keep today's behavior — delete the link, next message creates a
  fresh topicless conversation. (reset_command.py already shipped this.)
- `topic`: **repoint, don't delete** — create a new `Conversation` under the
  link's existing `topic_id`, set `link.conversation_id` to it. The Topic
  and the link row survive.

## Why the table goes away

`IMChannelBinding` currently serves three roles; each gets a better home:

| Role today | New home |
|---|---|
| per-channel routing mode (`mode`) | `account.config.bot_settings.routing_mode` (account-level) |
| shared-mode topic anchor (`topic_id`) | `IMThreadLink.topic_id` (durable, survives `/new`) |
| `sandbox_mode`, reverse-looked-up **by topic_id** in `worker.py` / `resume.py` to configure the run + flag IM-origin | `Topic.sandbox_mode` (existing column, written from `settings.sandbox_mode` at Topic creation) + `topic.attributes.im` as the IM-origin marker |

The reverse lookup ("given a topic, is it IM-bound and what sandbox?") is
the subtle one: `worker.py` and `resume.py` queried
`IMChannelBinding.topic_id == topic_id`. Replacement: load the `Topic`
(already loaded for `creator_user_id`), read `Topic.sandbox_mode` directly,
and treat `"im" in topic.attributes` as the IM-origin flag — no account
round-trip, no binding.

**Default-behavior change:** with `topic_mode` defaulting to `topic`, an
account with no config now creates a per-sender Topic on first message
(previously isolated/no-binding produced a topicless conversation). This is
the intended product default ("默认创建一个 topic").

## Affected code

Backend:
- `models/topic.py`, `models/conversation.py`: add `attributes` JSON column.
- `models/im_thread_link.py`: add `topic_id` nullable FK.
- `models/im_channel_binding.py` + `repositories/im_channel_binding.py` +
  `repositories/__init__.py` + `models/__init__.py`: **delete**.
- `im/conversation_resolver.py`: read `account` settings instead of
  `IMChannelBinding`; generalize lazy-Topic to `isolated + topic`; write
  `attributes.im`; set `IMThreadLink.topic_id`.
- `im/types.py`: `lookup_binding_mode` / `is_shared_mode_for_tailer` read
  account settings (and, for the tailer's topic-id fallback,
  `topic.attributes.im`).
- `im/worker.py`, `im/resume.py`: swap the `IMChannelBinding`-by-topic
  lookups for `topic.attributes.im` + account settings.
- `im/feishu/reset_command.py`: mode-aware `/new` (repoint vs delete).
- `api/routes/v1/ws_im.py`: drop the channel-binding routes; add
  `GET/PUT /accounts/{id}/settings` (workspace-scoped) reading/writing
  `account.config.bot_settings`.
- alembic: one migration — add columns, drop `im_channel_bindings`.

Frontend:
- `packages/core/src/api/im.ts`: add `wsGetImBotSettings` / `wsUpdateImBotSettings`;
  drop the (never-built) channel-binding surface.
- `components/im/ImAccountDetailPanel.tsx`: a settings section — two selects
  (routing_mode, topic_mode), sandbox_mode when shared, and a live preview
  of the resulting Topic title.

## Migration / data note

`im_channel_bindings` rows that exist in dev DBs are dropped. Any topic
linkage they held is not migrated (dev-only data; no shipped tenants). The
migration must also backfill `attributes.im` onto existing IM-origin topics
if we want pre-existing isolated topics to render bot avatars — **out of
scope for v1**; existing topics keep working, just without the new avatar.

## Open sub-decisions (resolve during implementation)

1. **Avatar source for Feishu.** `bot_avatar_url` — fetch from
   `contact.v3` / bot info at connect time and cache on the account, or
   lazily on first topic creation? Lean: fetch at connect, store in
   `account.config`.
2. **`channel_name` for group topics.** Today caller-supplied; with the
   table gone, fetch lazily via `im.v1.chats.get` on first topic creation
   and cache in `attributes.im.channel_name`.
3. **`get_or_create_thread_link` soft-delete repoint** interaction with the
   new `topic_id` column — ensure repoint preserves `topic_id`.

## Rollout / PR split

- **PR1 (backend core):** `attributes` columns, `IMThreadLink.topic_id`,
  `IMBotSettings`, resolver + types + worker + resume rewrite, remove
  `IMChannelBinding`, mode-aware `/new`, migration. e2e: isolated+topic
  creates a per-sender topic; `/new` rotates conversation under same topic;
  shared still works; worker/resume recover sandbox from `attributes`.
- **PR2 (settings API + UI):** account-settings route, `im.ts`,
  `ImAccountDetailPanel` settings section + title preview. e2e: SSR/flow as
  applicable; backend e2e for the settings route RBAC + persistence.
```
