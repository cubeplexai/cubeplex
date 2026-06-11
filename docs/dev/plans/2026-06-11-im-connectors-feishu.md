# IM Connectors (Feishu first) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Why Feishu first.** Slack-first was the original v1 (the now-frozen plan at
`docs/dev/plans/2026-05-27-im-connectors.md`). We are flipping to Feishu first
for basic functionality validation: the Feishu `lark_oapi` SDK supports a
**long-connection mode** that needs no public ingress, so the loop "@-mention
the bot → run starts → reply streams back" can be exercised end-to-end inside
a worktree without setting up a tunnel. Slack remains a follow-up plan that
reuses the same neutral data model and connector protocol shipped here.

**Goal:** Let a workspace bind a Feishu bot so an `@mention` / DM starts an agent
run on a cubebox conversation and the run's streamed output flows back as a
live-updating Feishu reply — with processing reactions (⏱️ on start, removed
on success, ❌ on failure) and artifact share-links — reusing the existing run
path, never forking it.

**Session boundary model (chat × user, not thread).** Feishu's "话题/thread" is
a fringe feature in practice; the dominant pattern is plain replies and quote
replies in the main chat. So a "Slack-style: each @mention starts a thread,
threads = conversations" model misroutes — every fresh @mention by the same
user becomes a brand-new conversation with no memory. v1 uses the
**(chat × user)** boundary in groups (one rolling conversation per
(group, sender)) and **(chat)** in DMs (one rolling conversation per DM). This
matches `hermes-agent/gateway/session.py:600` (`build_session_key`,
`group_sessions_per_user=True`), the prior-art adapter that has been through
real Feishu usage.

**Architecture.** Two delivery modes share one adapter:

1. **Long connection (Phase 0 + production for self-host).** The bot process
   holds an outbound WebSocket via `lark_oapi.ws.Client`. The SDK delivers
   typed events via a dispatcher; our handler normalizes them into a
   platform-agnostic `InboundEvent` and calls `ingest_inbound_event(...)`.
2. **HTTP webhook (production for cloud).** `POST /api/v1/im/feishu/events`
   verifies `verification_token` → handles `url_verification` → verifies
   `x-lark-signature` HMAC (`SHA256(timestamp+nonce+encrypt_key+body)`) →
   rejects encrypted payloads → normalizes the same way.

Both modes feed the same `ingest_inbound_event` core, which — in **one DB
transaction** — inserts an `IMWebhookReceipt` keyed by Feishu's `event_id`,
creates / reuses a `Conversation` + `IMThreadLink`, and enqueues a durable
`IMRunQueueItem` row (transactional outbox). A separate in-process async worker
polls that queue, claims a row via `SELECT … FOR UPDATE SKIP LOCKED`, and calls
`RunManager.start_run(...)`. An outbound tailer reads the run's Redis event
stream (`read_run_events_after`, the same tail SSE uses) and renders debounced
edits (`im.v1.message.update`) into the originating Feishu thread, posting
reaction emoji on processing start / success / failure, and dispatching
artifact events as native image messages or signed share-page links.

Config is scope-isolated: workspace routes (`/ws/{ws}/im/...`, `require_member`)
and org-admin routes (`/admin/im/...`, `get_admin_request_context`) are separate
handlers sharing one `IMConnectorService`.

### Connector-neutral session boundary: `scope_key`

The `IMThreadLink` table — the durable map from "IM session" → cubebox
`Conversation` — keys on `(account_id, channel_id, scope_key)`. **`scope_key`
is an opaque non-NULL string the connector owns**: cubebox guarantees its
uniqueness within `(account_id, channel_id)` but does not interpret its
contents. The connector encodes whatever boundary makes sense for its
platform. A separate `scope_kind` column (`'dm' | 'participant' | 'thread' |
'thread_participant'`) records what the connector chose, for observability
only — it is NOT in the unique index, so adding a new kind never collides
with existing rows. This avoids re-migrating the schema every time a new
platform with different session semantics is wired in.

The IMRunQueueItem mirrors the same shape: `scope_key` for the dedup/routing
key, plus `reply_to_id` (the real platform message id to reply against; may
be NULL for unthreaded sends). `scope_key` and `reply_to_id` are **two
different things**: the first is a session boundary the connector invents;
the second is a real platform identifier used in the outbound API call.

How each connector maps onto the neutral schema:

| Connector / scenario              | `scope_key`                       | `scope_kind`         | `reply_to_id`          |
|-----------------------------------|-----------------------------------|----------------------|------------------------|
| Feishu DM                         | `"dm"`                            | `dm`                 | None                   |
| Feishu group @mention             | `"u:<sender_union_id>"`           | `participant`        | inbound `message_id`   |
| Feishu group + 话题 (future)      | `"u:<union_id>|t:<thread_id>"`    | `thread_participant` | inbound `message_id`   |
| Slack DM                          | `"dm"`                            | `dm`                 | None                   |
| Slack channel @ → starts thread   | `"t:<thread_ts>"`                 | `thread`             | `thread_ts`            |
| Slack thread reply                | `"t:<thread_ts>"`                 | `thread`             | `thread_ts`            |
| Discord channel thread            | `"t:<thread_id>"`                 | `thread`             | `<thread_parent_id>`   |
| Telegram supergroup forum topic   | `"t:<topic_id>"`                  | `thread`             | `<topic_id>`           |
| WeCom group                       | `"u:<userid>"`                    | `participant`        | None                   |

This table is the contract for any future connector — the schema below was
designed against it and will not need migration when Slack lands.

**Tech Stack:** FastAPI, SQLModel + Alembic (Postgres), Redis Streams (existing
run-event log), the cubepi run path (`RunManager.start_run`), `CredentialService`
(vault `kind="im_bot"`), `lark-oapi` (official Feishu Python SDK) for both
long-connection and Web API calls, `hmac`/`hashlib` for webhook signature
verification. Tests: `pytest` against real Postgres + Redis (worktree-routed
DB) with captured-real Feishu payloads; no fake Feishu server.

**Scope:**

- **In scope:** neutral data model (works for any IM platform), Feishu
  long-connection + webhook ingress, full transactional outbox + worker +
  tailer, debounced text edits with adaptive backoff, processing reactions,
  artifact share-link mechanism + public artifact preview page, image-artifact
  inline upload, workspace/admin config routes.
- **Out of scope (this plan):** Feishu interactive cards (buttons /
  approvals — separate UX track), Feishu attachments (file/voice/video upload)
  for inbound, native streaming via Feishu Card v2 update_message stream API
  (debounced edit is sufficient for v1), the Next.js workspace/admin IM
  config pages (frontend follow-up PR per the scope-isolated-pages rule), and
  Slack (revived from the existing plan as a follow-up).

**Reference patterns (consulted, not copied):** The Feishu adapter at
`~/hermes-agent/gateway/platforms/feishu.py` (5144 lines) is the prior art we
rely on for signature algorithm, three-tier identity model, `/bot/v3/info`
bot-identity hydration, edit-debounce + adaptive backoff (0.8s default, doubles
on flood control to 10s cap), reaction-as-status UX, markdown-table → text
fallback, and the `lark_oapi` event dispatcher shape. Hermes is single-tenant
and persists dedup state to a JSON file; cubebox's multi-tenant
`IMWebhookReceipt` + Postgres outbox replaces that.

---

## File Structure

New files (all paths under `backend/`):

- `cubebox/models/im_connector.py` — `IMConnectorAccount`, `IMThreadLink`,
  `IMIdentityLink`, `IMWebhookReceipt`, `IMRunQueueItem` SQLModel tables.
  **Schema is connector-neutral** — `channel_id`, `scope_key` (opaque
  connector-owned session boundary), `scope_kind` (observability label),
  `reply_to_id` (real platform reply target). No `slack_*` / `feishu_*` /
  `thread_*` columns; the per-platform mapping is documented in the design
  intro above.
- `cubebox/repositories/im_connector.py` — scoped repos for the IM tables +
  the queue claim/complete primitives.
- `cubebox/services/im_connector.py` — `IMConnectorService` (CRUD shared by ws +
  admin routes).
- `cubebox/im/__init__.py`, `cubebox/im/types.py` — `InboundEvent`,
  `OutboundOp`, `RenderState`, `IMConnector` protocol.
- `cubebox/im/feishu/__init__.py`
- `cubebox/im/feishu/signature.py` — webhook HMAC verification.
- `cubebox/im/feishu/connector.py` — `FeishuConnector`: `parse_inbound`,
  `send`, `edit`, `add_reaction`, `remove_reaction`, image upload.
- `cubebox/im/feishu/long_connection.py` — `FeishuLongConnection`: `lark_oapi`
  WebSocket lifecycle (connect, hydrate bot identity, dispatch, reconnect).
- `cubebox/im/inbound.py` — `ingest_inbound_event(...)`: transactional receipt
  + conversation/thread + enqueue core.
- `cubebox/im/worker.py` — `IMRunQueueWorker`: drains the queue → `start_run` →
  spawns the outbound tailer.
- `cubebox/im/outbound.py` — `OutboundRunTailer`: Redis tail → render fold →
  Feishu edits + reactions + artifact dispatch.
- `cubebox/im/artifacts.py` — IM-side artifact dispatcher (decides:
  upload-as-image, attach-as-file, or share-link).
- `cubebox/api/routes/v1/im_ingress.py` — `POST /api/v1/im/feishu/events`
  (unauthenticated, platform-signed).
- `cubebox/api/routes/v1/ws_im.py` — workspace-scope account/identity routes
  (`require_member`).
- `cubebox/api/routes/v1/admin_im.py` — org-admin account listing /
  enable-disable (`get_admin_request_context`).
- `cubebox/api/routes/v1/artifact_share.py` — public `GET
  /api/v1/public/artifacts/share/{nonce}` preview page (no auth).
- `cubebox/api/schemas/im_connector.py` — request/response pydantic models.
- `backend/scripts/dev/feishu_long_connection_poc.py` — Phase 0 PoC script
  (deleted before PR; lives only to validate the SDK shape).

Modified:
- `cubebox/models/__init__.py` (export new tables).
- `cubebox/api/app.py` (register routers, start worker + long-connection on
  startup).
- `cubebox/services/credential.py` (`_guard_references`: refuse deleting an
  `im_bot` credential still referenced by an account).
- `cubebox/api/routes/v1/artifacts.py` (generalize `preview-token` route from
  Office-only to any artifact; reuse the existing Redis nonce mechanism).
- `pyproject.toml` (add `lark-oapi` dep via `uv add`).

---

## Task 0: Long-connection PoC — prove the SDK shape works in cubebox env

**Goal of this task:** Before committing schema or routes, exercise the
`lark_oapi` long-connection path end-to-end against a real Feishu app: bot
mention → event arrives in our handler → we call a stub `start_run` (just
logs the conversation key) → we send a one-shot reply. This validates
(a) the dep installs cleanly under uv, (b) the bot-identity hydration shape,
(c) the event payload structure we will normalize, (d) which Feishu user-ID
fields we actually get without elevated scopes. **No DB writes, no migrations,
no production code committed.**

The PoC script is a learning artifact; it gets deleted (Step 7 below) before
the PR. It exists to de-risk the schema decisions in Task 1.

- [ ] **Step 1: Add the dependency**

Run: `cd backend && uv add lark-oapi`
Expected: `pyproject.toml` and `uv.lock` updated; `python -c "import lark_oapi"`
succeeds inside the backend venv.

- [ ] **Step 2: Write the PoC script**

Create `backend/scripts/dev/feishu_long_connection_poc.py`. It reads
`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_DOMAIN` from env (default
domain `feishu` / fallback `lark`), opens a `lark_oapi.ws.Client`, registers a
`P2ImMessageReceiveV1` handler, and on each inbound message:

1. Logs `event_id`, `chat_id`, `chat_type`, `sender.sender_id.{open_id,
   union_id, user_id}`, raw message content.
2. Strips Feishu `<at>` tags from text, drops empty messages.
3. If text starts with `ping`, sends `pong + <event_id>` back via
   `client.im.v1.message.reply` to the originating message id.
4. Prints whether the `union_id` was populated (it is, by default — scope-free).

The script also calls `/bot/v3/info` on startup (`client.application.v6.application.get`
with the bot's own app id) to print the bot's own `open_id`, validating
the bot-identity hydration step. **This is exactly the prior-art pattern at
`~/hermes-agent/gateway/platforms/feishu.py:4509` (`_hydrate_bot_identity`).**

- [ ] **Step 3: Run against a real dev Feishu app**

The user creates a Feishu app at <https://open.feishu.cn>, enables the bot,
adds permissions `im:message`, `im:message:send_as_bot`, `im:resource`,
`im:message.group_at_msg`, `im:message.p2p_msg`, configures long-connection
event subscription (no callback URL), copies app id/secret into a local
`.env`, then runs:

```bash
cd backend && uv run python scripts/dev/feishu_long_connection_poc.py
```

Open Feishu, DM the bot "ping", then `@bot ping` in a group. Expected: two
lines of structured log + two `pong` replies. **Record in this checkbox the
exact fields populated in each event** (open_id present? union_id present?
chat_type values seen? thread_id present in groups?). These observations feed
Task 1's schema decisions.

- [ ] **Step 4: Confirm three-tier identity behaviour**

Specifically verify and record:
- `sender.sender_id.open_id` — present (default).
- `sender.sender_id.union_id` — present (default, scope-free).
- `sender.sender_id.user_id` — absent unless `contact:user.employee_id:readonly`
  scope granted.
- `mentions[].id.{open_id, union_id, name}` shape.

This is the canonical Feishu identity model documented in
`~/hermes-agent/gateway/platforms/feishu.py:14`. The plan assumes
`union_id` is the long-term stable identifier and `open_id` is the
mention-target.

- [ ] **Step 5: Confirm chat / thread keys**

Send: (a) DM, (b) channel @mention starting a new thread, (c) bare reply in
that thread. Record:
- DM: `chat_type=p2p`, `chat_id=oc_…`, `thread_id` likely `None`.
- New thread mention: `chat_type=group`, `chat_id=oc_…`, `thread_id=om_…`
  on the **inbound** event for first message; thread id matches the parent
  message id.
- Bare thread reply: `parent_id` / `root_id` / `thread_id` populated.

These confirm the conversation-key derivation for Task 5.

- [ ] **Step 6: Write a one-page PoC findings note**

Create `docs/dev/notes/2026-06-11-feishu-long-connection-poc.md` (~40 lines)
with the observations from Steps 3–5. This is the canonical reference for
"what the Feishu event actually looks like in practice", and it informs every
schema and parser decision after this. Include any surprises (field names that
differ from docs, fields that arrive empty under default scopes, etc.).

- [ ] **Step 7: Delete the PoC script and commit**

```bash
git rm backend/scripts/dev/feishu_long_connection_poc.py
git add docs/dev/notes/2026-06-11-feishu-long-connection-poc.md pyproject.toml uv.lock
git commit -m "feat(im): add lark-oapi dep; record Feishu long-connection PoC findings"
```

The dep stays; the script is deleted. The note is the durable artifact.

---

## Task 1: Neutral IM data model + public ID prefixes

The schema is platform-neutral. Three sets of columns appear in the per-link /
per-queue tables:

- **`channel_id`** (str): the platform chat / channel / DM id (Feishu
  `chat_id`, Slack `channel`, Discord `channel_id`, …). Always non-null.
- **`scope_key`** (str, **non-null**, **in unique index**): the connector-owned
  opaque session-boundary key. See the "Connector-neutral session boundary"
  section above. Cubebox guarantees uniqueness on
  `(account_id, channel_id, scope_key)` but does not parse the string.
- **`scope_kind`** (str): a label for the connector's chosen scope
  (`'dm' | 'participant' | 'thread' | 'thread_participant' | ...`). Used for
  observability and debugging, **not** in the unique index, so new kinds add
  freely.
- **`reply_to_id`** (str | NULL): the real platform message id to reply
  against. Distinct from `scope_key` — this is what the outbound API call
  needs, not the session boundary.
- **`inbound_message_id`** (str | NULL): the original user-message id, used
  to attach reactions to the right message (not the bot's reply).
- **`sender_im_user_id`** (str | NULL): the most stable sender id available
  (Feishu: union_id; Slack: Uxxx). Kept for tracing and the future identity
  link path.

No `slack_*` / `feishu_*` columns. The `platform` discriminator on
`IMConnectorAccount` is the only place the schema records what platform a
row belongs to.

Public ID prefixes follow the `CubeboxBase._PREFIX` convention. No edit to
`public_id.py` is required — each table sets its own `_PREFIX`. Prefixes:
`imac` (account), `imtl` (thread link), `imil` (identity link), `imwr`
(webhook receipt), `imrq` (run queue item).

**Files:**
- Create: `backend/cubebox/models/im_connector.py`
- Modify: `backend/cubebox/models/__init__.py`
- Test: `backend/tests/unit/test_im_models.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_im_models.py
from cubebox.models.im_connector import (
    IMConnectorAccount,
    IMIdentityLink,
    IMRunQueueItem,
    IMThreadLink,
    IMWebhookReceipt,
)


def test_account_id_prefix() -> None:
    acc = IMConnectorAccount(
        org_id="org-x", workspace_id="ws-x",
        platform="feishu", external_account_id="cli_a1b2",
        acting_user_id="usr-x", credential_id="cred-x",
    )
    assert acc.id.startswith("imac-")
    assert acc.delivery_mode == "long_connection"
    assert acc.enabled is True


def test_thread_link_uses_neutral_scope_key() -> None:
    dm = IMThreadLink(
        org_id="org-x", workspace_id="ws-x",
        account_id="imac-1", channel_id="oc_x",
        scope_key="dm", scope_kind="dm", conversation_id="conv-1",
    )
    group = IMThreadLink(
        org_id="org-x", workspace_id="ws-x",
        account_id="imac-1", channel_id="oc_g",
        scope_key="u:on_user1", scope_kind="participant", conversation_id="conv-2",
    )
    assert dm.id.startswith("imtl-")
    assert group.id.startswith("imtl-")
    # Schema does not encode platform-specific terms.
    assert not hasattr(dm, "thread_root_id")
    assert not hasattr(dm, "thread_ts")


def test_run_queue_item_has_neutral_columns() -> None:
    item = IMRunQueueItem(
        org_id="org-x", workspace_id="ws-x",
        account_id="imac-1", conversation_id="conv-1",
        receipt_id="imwr-1", content="hi",
        channel_id="oc_x",
        scope_key="u:on_user1", scope_kind="participant",
        reply_to_id="om_msg1", inbound_message_id="om_msg1",
        sender_im_user_id="on_user1",
    )
    assert item.id.startswith("imrq-")
    assert item.status == "pending"
    # Schema is connector-neutral: no slack_* / feishu_* / thread_* columns.
    assert not hasattr(item, "slack_channel_id")
    assert not hasattr(item, "feishu_chat_id")
    assert not hasattr(item, "thread_root_id")
    assert not hasattr(item, "reply_thread_ts")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_im_models.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the models**

```python
# backend/cubebox/models/im_connector.py
"""IM connector models. Platform-neutral schema (works for Feishu, Slack, …)."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, Index, text
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin


class IMConnectorAccount(CubeboxBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = "imac"
    __tablename__ = "im_connector_accounts"
    __table_args__ = (
        Index(
            "uq_im_account_platform_external",
            "platform", "external_account_id", unique=True,
        ),
        Index("ix_im_accounts_org_ws", "org_id", "workspace_id"),
    )

    platform: str = Field(max_length=16)             # 'feishu' | 'slack' | ...
    external_account_id: str = Field(max_length=128)  # Feishu app_id, Slack team_id, ...
    acting_user_id: str = Field(foreign_key="users.id", max_length=20)
    credential_id: str = Field(foreign_key="credentials.id", max_length=20)
    delivery_mode: str = Field(default="long_connection", max_length=24)  # 'long_connection' | 'webhook'
    enabled: bool = Field(default=True, sa_column_kwargs={"server_default": text("true")})
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class IMThreadLink(CubeboxBase, OrgScopedMixin, table=True):
    """Durable map: (account, channel, connector-owned scope_key) → one
    cubebox Conversation. The table name keeps the historical 'thread_links'
    label for back-compatibility with internal naming, but scope_key is the
    actual session-boundary contract — see the design intro above."""
    _PREFIX: ClassVar[str] = "imtl"
    __tablename__ = "im_thread_links"
    __table_args__ = (
        Index(
            "uq_im_scope_link",
            "account_id", "channel_id", "scope_key", unique=True,
        ),
    )

    account_id: str = Field(foreign_key="im_connector_accounts.id", max_length=20, index=True)
    channel_id: str = Field(max_length=128)
    # Connector-owned non-null opaque session-boundary key. Cubebox does not
    # interpret this string; the connector encodes whatever its platform
    # needs (e.g. 'dm', 'u:<union_id>', 't:<thread_ts>'). Non-null because
    # Postgres treats NULL as distinct in unique indexes.
    scope_key: str = Field(max_length=255)
    # Observability label only — NOT in the unique index. Adding a new kind
    # never breaks existing rows.
    scope_kind: str = Field(max_length=32)
    conversation_id: str = Field(foreign_key="conversations.id", max_length=20, index=True)


class IMIdentityLink(CubeboxBase, OrgScopedMixin, table=True):
    """Map an IM sender (preferred: union_id) to a cubebox user.
    v1 falls back to account.acting_user_id when no link exists."""
    _PREFIX: ClassVar[str] = "imil"
    __tablename__ = "im_identity_links"
    __table_args__ = (
        Index("uq_im_identity_link", "account_id", "im_user_id", unique=True),
    )

    account_id: str = Field(foreign_key="im_connector_accounts.id", max_length=20, index=True)
    # Whatever the platform's most-stable identifier is. For Feishu: union_id
    # (developer-scoped, present without elevated scopes; see PoC notes).
    im_user_id: str = Field(max_length=128)
    user_id: str = Field(foreign_key="users.id", max_length=20)


class IMWebhookReceipt(CubeboxBase, OrgScopedMixin, table=True):
    """Idempotency receipt keyed by platform event id. Inserted in the same
    transaction that enqueues the run (transactional outbox)."""
    _PREFIX: ClassVar[str] = "imwr"
    __tablename__ = "im_webhook_receipts"
    __table_args__ = (
        Index(
            "uq_im_receipt_account_event",
            "account_id", "platform_event_id", unique=True,
        ),
    )

    account_id: str = Field(foreign_key="im_connector_accounts.id", max_length=20, index=True)
    platform_event_id: str = Field(max_length=255)
    status: str = Field(default="pending", max_length=16)  # 'pending' | 'completed'
    lease_expires_at: datetime | None = Field(default=None)


class IMRunQueueItem(CubeboxBase, OrgScopedMixin, table=True):
    """Durable outbox row drained by IMRunQueueWorker via FOR UPDATE SKIP LOCKED."""
    _PREFIX: ClassVar[str] = "imrq"
    __tablename__ = "im_run_queue"
    __table_args__ = (
        Index(
            "ix_im_run_queue_pending",
            "status", "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_im_run_queue_started_lease",
            "status", "claim_lease_expires_at",
            postgresql_where=text("status = 'started'"),
        ),
    )

    account_id: str = Field(foreign_key="im_connector_accounts.id", max_length=20, index=True)
    receipt_id: str = Field(foreign_key="im_webhook_receipts.id", max_length=20, index=True)
    conversation_id: str = Field(foreign_key="conversations.id", max_length=20)
    content: str
    # Conversation key (see IMThreadLink). Neutral, connector-owned.
    channel_id: str = Field(max_length=128)
    scope_key: str = Field(max_length=255)
    scope_kind: str = Field(max_length=32)
    # The real platform reply target (Slack thread_ts; Feishu message_id; ...).
    # NULL for unthreaded sends (e.g. Feishu DM, Slack DM with no thread).
    reply_to_id: str | None = Field(default=None, max_length=128)
    # The originating user message id, recorded so the worker can attach
    # processing reactions to the *right* message (not the bot's reply).
    inbound_message_id: str | None = Field(default=None, max_length=128)
    sender_im_user_id: str | None = Field(default=None, max_length=128)
    status: str = Field(default="pending", max_length=16)   # 'pending' | 'started' | 'failed'
    claimed_at: datetime | None = Field(default=None)
    claim_lease_expires_at: datetime | None = Field(default=None)
    attempts: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
```

- [ ] **Step 4: Export tables**

Add to `backend/cubebox/models/__init__.py` (and `__all__`):

```python
from cubebox.models.im_connector import (  # noqa: F401
    IMConnectorAccount,
    IMIdentityLink,
    IMRunQueueItem,
    IMThreadLink,
    IMWebhookReceipt,
)
```

- [ ] **Step 5: Run + commit**

```bash
cd backend && uv run pytest tests/unit/test_im_models.py -v
git add backend/cubebox/models/im_connector.py backend/cubebox/models/__init__.py \
        backend/tests/unit/test_im_models.py
git commit -m "feat(im): neutral IM connector data model (accounts, threads, receipts, queue)"
```

---

## Task 2: Alembic migration (autogenerate)

- [ ] **Step 1: Generate**: `cd backend && uv run alembic revision --autogenerate -m "im connectors tables (neutral schema)"`
- [ ] **Step 2: Inspect** the generated file. Confirm all five tables and both partial indexes (`ix_im_run_queue_pending`, `ix_im_run_queue_started_lease`) are present with their `postgresql_where`.
- [ ] **Step 3: Apply**: `cd backend && uv run alembic upgrade head`
- [ ] **Step 4: Drift check**: `cd backend && uv run alembic revision --autogenerate -m "drift check"` — expect empty `upgrade()`. Delete drift file.
- [ ] **Step 5: Commit**:
  ```bash
  git add backend/alembic/versions/
  git commit -m "feat(im): add migration for IM connector tables"
  ```

---

## Task 3: Feishu webhook signature verification (unit, security-critical)

Feishu signature algorithm (from real adapter at
`~/hermes-agent/gateway/platforms/feishu.py:3362`):

```
content = timestamp + nonce + encrypt_key + body_string
signature = SHA256(content).hexdigest()
```

Headers: `x-lark-request-timestamp`, `x-lark-request-nonce`,
`x-lark-signature`. Constant-time compare. **No timestamp staleness window**
in Feishu's algorithm (unlike Slack's 5-min skew rule) — the
`verification_token` check is the dedicated replay defense and runs first.

**Files:**
- Create: `backend/cubebox/im/__init__.py` (empty), `backend/cubebox/im/feishu/__init__.py` (empty), `backend/cubebox/im/feishu/signature.py`
- Test: `backend/tests/unit/test_feishu_signature.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/unit/test_feishu_signature.py
import hashlib

import pytest

from cubebox.im.feishu.signature import (
    FeishuSignatureError,
    verify_feishu_signature,
    verify_verification_token,
)

ENCRYPT_KEY = "my-encrypt-key-32-chars-min------"
VTOKEN = "v-token-from-feishu-dashboard"


def _sign(*, ts: str, nonce: str, body: bytes) -> str:
    return hashlib.sha256(f"{ts}{nonce}{ENCRYPT_KEY}{body.decode()}".encode()).hexdigest()


def test_valid_signature_passes() -> None:
    body = b'{"schema":"2.0"}'
    verify_feishu_signature(
        encrypt_key=ENCRYPT_KEY,
        raw_body=body,
        timestamp="1700000000",
        nonce="abc",
        signature=_sign(ts="1700000000", nonce="abc", body=body),
    )  # no raise


def test_tampered_body_rejected() -> None:
    body = b'{"schema":"2.0"}'
    sig = _sign(ts="1700000000", nonce="abc", body=body)
    with pytest.raises(FeishuSignatureError):
        verify_feishu_signature(
            encrypt_key=ENCRYPT_KEY, raw_body=b'{"evil":true}',
            timestamp="1700000000", nonce="abc", signature=sig,
        )


def test_missing_headers_rejected() -> None:
    with pytest.raises(FeishuSignatureError):
        verify_feishu_signature(
            encrypt_key=ENCRYPT_KEY, raw_body=b"{}",
            timestamp="", nonce="abc", signature="x",
        )


def test_verification_token_constant_time_compare() -> None:
    verify_verification_token(expected=VTOKEN, incoming=VTOKEN)  # no raise
    with pytest.raises(FeishuSignatureError):
        verify_verification_token(expected=VTOKEN, incoming="other")
```

- [ ] **Step 2: Confirm failure**, then **Step 3: Implement**

```python
# backend/cubebox/im/feishu/signature.py
"""Feishu webhook signature + verification-token validation."""

import hashlib
import hmac


class FeishuSignatureError(Exception):
    """Raised when a Feishu request fails signature or token validation."""


def verify_verification_token(*, expected: str, incoming: str) -> None:
    """Constant-time compare of the verification token (second auth layer)."""
    if not expected or not incoming or not hmac.compare_digest(expected, incoming):
        raise FeishuSignatureError("invalid verification token")


def verify_feishu_signature(
    *,
    encrypt_key: str,
    raw_body: bytes,
    timestamp: str,
    nonce: str,
    signature: str,
) -> None:
    """Validate x-lark-signature HMAC. Algorithm: SHA256(ts + nonce + encrypt_key + body)."""
    if not timestamp or not nonce or not signature:
        raise FeishuSignatureError("missing signature headers")
    try:
        body_str = raw_body.decode("utf-8", errors="replace")
    except Exception as exc:
        raise FeishuSignatureError("body not decodable") from exc
    expected = hashlib.sha256(f"{timestamp}{nonce}{encrypt_key}{body_str}".encode()).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise FeishuSignatureError("signature mismatch")
```

- [ ] **Step 4: Run + commit**

```bash
cd backend && uv run pytest tests/unit/test_feishu_signature.py -v
touch backend/cubebox/im/__init__.py backend/cubebox/im/feishu/__init__.py
git add backend/cubebox/im/__init__.py backend/cubebox/im/feishu/__init__.py \
        backend/cubebox/im/feishu/signature.py backend/tests/unit/test_feishu_signature.py
git commit -m "feat(im): Feishu webhook signature + verification-token validation"
```

---

## Task 4: Inbound types + parse Feishu events into a neutral InboundEvent

`parse_inbound` turns a raw Feishu `im.message.receive_v1` payload into a
platform-agnostic `InboundEvent`. It:

- Strips `<at>` tags from text (drops empty-after-strip).
- **Derives the `scope_key` from the chat × user model**:
  - `chat_type == "p2p"` → `scope_key="dm"`, `scope_kind="dm"`. A DM is one
    rolling conversation per chat.
  - `chat_type == "group"` → `scope_key=f"u:{sender_union_id}"` (or
    `f"u:{sender_open_id}"` if union_id absent), `scope_kind="participant"`.
    A group is one rolling conversation per (group, sender). Future:
    话题/thread overlay → `f"u:{user}|t:{thread_id}"` with kind
    `thread_participant`; not built in v1 because 话题 is rare in real usage.
- **Derives the `reply_to_id`** (the real reply target for the outbound API):
  - DM: `None` (send as plain message to chat).
  - Group: the inbound `message_id` (Feishu's reply API needs a message id).
- Pulls the stable `event_id` from `header.event_id`.
- **Defense-in-depth mention gating in groups** (in case the subscription is
  accidentally widened from `group_at_msg` to `group_msg`): if
  `chat_type == "group"`, drop the event unless `mentions[]` contains
  `bot_open_id`. DM skips this check.
- Selects the most stable sender id available: prefer `union_id` (default,
  scope-free per the PoC notes); fallback to `open_id`. Same string goes into
  both `sender_ref` (for the future identity link) and `scope_key` in the
  group case.
- Returns `None` for: bot's own messages (`sender.sender_type=="app"` or
  `sender.open_id == bot.open_id`), empty-after-strip text, non-text message
  types in v1, group messages that don't mention the bot.

**Files:**
- Create: `backend/cubebox/im/types.py`, `backend/cubebox/im/feishu/connector.py`
- Test: `backend/tests/unit/test_feishu_parse_inbound.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/unit/test_feishu_parse_inbound.py
import json

from cubebox.im.feishu.connector import FeishuConnector

GROUP_MENTION = {
    "header": {"event_id": "evgrp01", "event_type": "im.message.receive_v1"},
    "event": {
        "sender": {"sender_id": {"open_id": "ou_user", "union_id": "on_user"},
                   "sender_type": "user"},
        "message": {
            "message_id": "om_msg1", "chat_id": "oc_chat1", "chat_type": "group",
            "message_type": "text",
            "content": json.dumps({"text": "<at user_id=\"ou_bot\">Bot</at> summarize"}),
            "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_bot", "union_id": "on_bot"},
                          "name": "Bot"}],
        },
    },
}

DM_NEW_THREAD = {
    "header": {"event_id": "evdm01", "event_type": "im.message.receive_v1"},
    "event": {
        "sender": {"sender_id": {"open_id": "ou_user", "union_id": "on_user"},
                   "sender_type": "user"},
        "message": {
            "message_id": "om_msg2", "chat_id": "oc_dm1", "chat_type": "p2p",
            "message_type": "text",
            "content": json.dumps({"text": "hello"}),
        },
    },
}

BOT_ECHO = {
    "header": {"event_id": "evb1", "event_type": "im.message.receive_v1"},
    "event": {
        "sender": {"sender_id": {"open_id": "ou_bot", "union_id": "on_bot"},
                   "sender_type": "app"},
        "message": {
            "message_id": "om_msg3", "chat_id": "oc_chat1", "chat_type": "group",
            "message_type": "text",
            "content": json.dumps({"text": "echo"}),
        },
    },
}


def test_group_mention_scope_is_per_participant() -> None:
    c = FeishuConnector(bot_open_id="ou_bot")
    ev = c.parse_inbound(GROUP_MENTION)
    assert ev is not None
    assert ev.account_external_id == ""  # filled by ingress from account lookup
    assert ev.platform_event_id == "evgrp01"
    assert ev.channel_id == "oc_chat1"
    # Session is per (group × sender): same user in same group reuses this conversation.
    assert ev.scope_key == "u:on_user"
    assert ev.scope_kind == "participant"
    assert ev.reply_to_id == "om_msg1"
    assert ev.sender_ref == "on_user"  # union_id preferred
    assert ev.sender_open_id == "ou_user"
    assert ev.inbound_message_id == "om_msg1"
    assert ev.text == "summarize"


def test_dm_scope_is_chat_level() -> None:
    c = FeishuConnector(bot_open_id="ou_bot")
    ev = c.parse_inbound(DM_NEW_THREAD)
    assert ev is not None
    assert ev.channel_id == "oc_dm1"
    assert ev.scope_key == "dm"            # one rolling conversation per DM
    assert ev.scope_kind == "dm"
    assert ev.reply_to_id is None          # send as plain message, no reply target
    assert ev.text == "hello"


def test_group_message_without_bot_mention_dropped() -> None:
    """Defense in depth: if Feishu subscription is widened from group_at_msg
    to group_msg by misconfiguration, the parser must still drop messages
    that do not @ the bot."""
    raw = {
        "header": {"event_id": "evnomention", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user", "union_id": "on_user"},
                       "sender_type": "user"},
            "message": {
                "message_id": "om_chatter", "chat_id": "oc_chat1", "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "just chatting, no bot involved"}),
            },
        },
    }
    c = FeishuConnector(bot_open_id="ou_bot")
    assert c.parse_inbound(raw) is None


def test_bot_echo_ignored() -> None:
    c = FeishuConnector(bot_open_id="ou_bot")
    assert c.parse_inbound(BOT_ECHO) is None
```

- [ ] **Step 2: Confirm failure**, then **Step 3: Implement types + parser**

```python
# backend/cubebox/im/types.py
"""Platform-agnostic IM transport types."""

from dataclasses import dataclass, field

# A DM has only "the chat" as its scope — no thread, no per-participant cut.
DM_SCOPE_KEY = "dm"


def make_participant_scope(sender_ref: str) -> str:
    """Group session keyed by sender (Feishu groups, WeCom, future per-user
    rooms). Centralized so every connector composes the same byte-for-byte
    string — a typo (`"u :x"` vs `"u:x"`) would silently fork sessions."""
    return f"u:{sender_ref}"


def make_thread_scope(thread_id: str) -> str:
    """Thread/topic-scoped session (Slack threads, Discord threads, Telegram
    forum topics)."""
    return f"t:{thread_id}"


def make_thread_participant_scope(sender_ref: str, thread_id: str) -> str:
    """Combined scope: thread sub-divided per participant (rare; reserved
    for future Feishu 话题 overlay)."""
    return f"u:{sender_ref}|t:{thread_id}"


@dataclass(slots=True)
class InboundEvent:
    """Normalized inbound IM message ready for binding / scope / identity resolution.

    Field roles:
    - scope_key: CONNECTOR-OWNED SESSION BOUNDARY KEY. Opaque non-NULL string.
      cubebox guarantees uniqueness on (account_id, channel_id, scope_key);
      the connector decides how to derive the string from its platform's
      semantics (DM, per-participant in group, per-thread, or any composition).
      See the plan's "Connector-neutral session boundary" section.
    - scope_kind: Observability label for the chosen scope
      ('dm' | 'participant' | 'thread' | 'thread_participant' | ...).
      NOT used for dedup.
    - reply_to_id: OUTBOUND REPLY TARGET. The real platform message id to
      reply against, or None for an unthreaded send.
    - inbound_message_id: The originating user message id (used to attach
      processing reactions to the user's message, not the bot's reply).
    - sender_ref: Most stable sender id available (Feishu: union_id preferred).
    - sender_open_id: App-scoped id (used for mention gating only).
    """

    platform: str
    account_external_id: str
    platform_event_id: str
    channel_id: str
    scope_key: str
    scope_kind: str
    reply_to_id: str | None
    inbound_message_id: str
    sender_ref: str
    sender_open_id: str | None
    text: str


@dataclass(slots=True)
class RenderState:
    """Per-run outbound render state."""
    message_id: str | None = None
    text_buffer: str = ""
    tool_lines: list[str] = field(default_factory=list)
    last_edit_monotonic: float = 0.0
    edit_interval: float = 0.8                    # adaptive: starts at 0.8s, doubles on flood up to 10s
    consecutive_flood_strikes: int = 0
    edits_disabled: bool = False                  # true after 3 consecutive flood errors
    reaction_in_progress_id: str | None = None    # the ⏱️ reaction id, for later removal
    posted_artifacts: set[str] = field(default_factory=set)  # artifact ids already announced
    reply_to_id: str | None = None                # bound at tailer start; passed to post_placeholder
    # Originating user message id, propagated from IMRunQueueItem.inbound_message_id.
    # Used by reaction calls (Task 10) to attach the processing / failure
    # reaction to the *user's* message, NOT the bot's reply (state.message_id).
    inbound_message_id: str | None = None
```

```python
# backend/cubebox/im/feishu/connector.py
"""Feishu connector: inbound parse + outbound send/edit/react (lark_oapi)."""

import json
import re
from typing import Any

from cubebox.im.types import DM_SCOPE_KEY, InboundEvent, make_participant_scope

# Matches Feishu inline mention markup: <at user_id="ou_xxx">name</at>
_AT_TAG_RE = re.compile(r"<at[^>]*>.*?</at>", re.DOTALL)


class FeishuConnector:
    """Connector for one Feishu account.

    Construction comes in two stages:
    - Inbound parsing only needs `bot_open_id` (for mention gating).
    - Outbound calls (send / edit / react) need a bound `lark_oapi.Client`
      plus `channel_id` and (optional) `reply_to_id`, set via
      `bind_outbound()` before use.
    """

    def __init__(
        self,
        *,
        bot_open_id: str | None = None,
        client: Any = None,
        channel_id: str | None = None,
        reply_to_id: str | None = None,
    ) -> None:
        self._bot_open_id = bot_open_id
        self._client = client
        self._channel_id = channel_id
        self._reply_to_id = reply_to_id

    # ----- inbound -----

    def parse_inbound(self, raw: dict[str, Any]) -> InboundEvent | None:
        header = raw.get("header") or {}
        event = raw.get("event") or {}
        sender = event.get("sender") or {}
        message = event.get("message") or {}

        if header.get("event_type") != "im.message.receive_v1":
            return None
        # Ignore the bot's own messages (sender_type='app' or sender open_id matches).
        sender_id = sender.get("sender_id") or {}
        sender_open_id = sender_id.get("open_id")
        if sender.get("sender_type") == "app":
            return None
        if self._bot_open_id is not None and sender_open_id == self._bot_open_id:
            return None
        # v1 supports text only.
        if message.get("message_type") != "text":
            return None

        # Feishu wraps message.content as a JSON string {"text": "..."}.
        try:
            content_obj = json.loads(message.get("content", "{}"))
        except json.JSONDecodeError:
            return None
        text = _AT_TAG_RE.sub("", content_obj.get("text", "")).strip()
        if not text:
            return None

        chat_id = message.get("chat_id", "")
        message_id = message.get("message_id", "")
        chat_type = message.get("chat_type", "")

        # Sender ref: prefer union_id (stable, scope-free); fallback to open_id.
        sender_ref = sender_id.get("union_id") or sender_open_id or ""

        # Session scope (chat × user in groups; chat in DMs).
        if chat_type == "p2p":
            scope_key = DM_SCOPE_KEY
            scope_kind = "dm"
            reply_target: str | None = None  # plain DM send, no reply target
        else:
            # Group: defense-in-depth mention gating in case the Feishu
            # subscription is misconfigured to deliver every group message.
            if not self._group_message_mentions_bot(message):
                return None
            if not sender_ref:
                return None  # cannot scope a group session without a sender id
            scope_key = make_participant_scope(sender_ref)
            scope_kind = "participant"
            reply_target = message_id  # group replies always target the inbound msg

        return InboundEvent(
            platform="feishu",
            account_external_id="",  # ingress fills this from account lookup
            platform_event_id=header.get("event_id", ""),
            channel_id=chat_id,
            scope_key=scope_key,
            scope_kind=scope_kind,
            reply_to_id=reply_target,
            inbound_message_id=message_id,
            sender_ref=sender_ref,
            sender_open_id=sender_open_id,
            text=text,
        )

    def _group_message_mentions_bot(self, message: dict[str, Any]) -> bool:
        if self._bot_open_id is None:
            return True  # bot identity not hydrated yet; let it through (PoC path)
        for mention in message.get("mentions") or []:
            mid = (mention.get("id") or {}).get("open_id")
            if mid and mid == self._bot_open_id:
                return True
        return False

    def bind_outbound(
        self,
        *,
        client: Any,
        channel_id: str,
        reply_to_id: str | None,
    ) -> None:
        self._client = client
        self._channel_id = channel_id
        self._reply_to_id = reply_to_id
```

- [ ] **Step 4: Run + commit**

```bash
cd backend && uv run pytest tests/unit/test_feishu_parse_inbound.py -v
git add backend/cubebox/im/types.py backend/cubebox/im/feishu/connector.py \
        backend/tests/unit/test_feishu_parse_inbound.py
git commit -m "feat(im): normalize Feishu events into neutral InboundEvent"
```

---

## Task 5: Repositories + transactional inbound core (receipt + thread + enqueue)

Identical mechanics to the Slack plan's Task 6 — see
`docs/dev/plans/2026-05-27-im-connectors.md:663` for the full rationale. The
short version:

- Open one transaction.
- Insert receipt; if unique-violation on `(account_id, platform_event_id)`,
  this is a redelivered event → ack as `duplicate`, no second enqueue.
- Create / reuse `Conversation` + `IMThreadLink`.
- Insert `IMRunQueueItem` referencing the receipt + conversation + neutral
  reply target.
- Commit.

Failure discrimination on `IntegrityError`: only the receipt unique constraint
means "duplicate"; any other constraint (FK, thread-link race) re-raises or
retries deliberately.

**Files:**
- Create: `backend/cubebox/repositories/im_connector.py`, `backend/cubebox/im/inbound.py`
- Test: `backend/tests/integration/test_im_inbound_outbox.py`

- [ ] **Step 1: Write the failing integration test**

```python
# backend/tests/integration/test_im_inbound_outbox.py
import pytest
from sqlalchemy import func, select

from cubebox.im.inbound import ingest_inbound_event
from cubebox.im.types import InboundEvent
from cubebox.models.im_connector import IMRunQueueItem, IMThreadLink

pytestmark = pytest.mark.asyncio


def _event(event_id: str = "ev1", scope_key: str = "u:on_user1") -> InboundEvent:
    return InboundEvent(
        platform="feishu", account_external_id="cli_a1b2",
        platform_event_id=event_id,
        channel_id="oc_chat1",
        scope_key=scope_key, scope_kind="participant",
        reply_to_id="om_msg1", inbound_message_id="om_msg1",
        sender_ref="on_user1", sender_open_id="ou_user1", text="hello",
    )


async def test_first_event_creates_conversation_link_and_queue(im_account, session_maker):
    res = await ingest_inbound_event(_event(), account=im_account, session_maker=session_maker)
    assert res.outcome == "enqueued"
    async with session_maker() as s:
        assert (await s.execute(select(func.count()).select_from(IMRunQueueItem))).scalar() == 1
        link = (await s.execute(select(IMThreadLink))).scalars().one()
        assert link.scope_key == "u:on_user1"
        assert link.scope_kind == "participant"


async def test_duplicate_event_does_not_double_enqueue(im_account, session_maker):
    await ingest_inbound_event(_event("dup"), account=im_account, session_maker=session_maker)
    res2 = await ingest_inbound_event(_event("dup"), account=im_account, session_maker=session_maker)
    assert res2.outcome == "duplicate"
    async with session_maker() as s:
        assert (await s.execute(select(func.count()).select_from(IMRunQueueItem))).scalar() == 1


async def test_same_sender_in_same_group_reuses_conversation(im_account, session_maker):
    """Chat × user session model: A's second @ in the same group joins A's
    existing conversation, not a new one. This is the core boundary that
    'thread-per-message' would have gotten wrong in Feishu's real UX."""
    r1 = await ingest_inbound_event(_event("evA"), account=im_account, session_maker=session_maker)
    r2 = await ingest_inbound_event(_event("evB"), account=im_account, session_maker=session_maker)
    assert r1.conversation_id == r2.conversation_id


async def test_different_senders_in_same_group_get_distinct_conversations(im_account, session_maker):
    """A and B in the same group each get their own rolling conversation;
    they do not bleed into each other's context."""
    r_a = await ingest_inbound_event(
        _event("evA", scope_key="u:on_userA"), account=im_account, session_maker=session_maker,
    )
    r_b = await ingest_inbound_event(
        _event("evB", scope_key="u:on_userB"), account=im_account, session_maker=session_maker,
    )
    assert r_a.conversation_id != r_b.conversation_id
```

> Fixtures (`im_account`, `session_maker`, `seeded_org_workspace_user`): resolve
> against the real codebase before writing them — see the warning at the
> Slack plan's Task 6 Step 1 about not assuming names exist.

- [ ] **Step 2: Confirm failure**, then **Steps 3–4: Write repos + transactional core**

The general structure mirrors the Slack plan's `repositories/im_connector.py`
and `im/inbound.py` — transactional outbox, `claim_pending_queue_item` with
`FOR UPDATE SKIP LOCKED`, IntegrityError discrimination, retry-on-thread-link
race. **But do not copy line ranges from the sister plan** — the schema rename
introduces multiple stale identifiers that a verbatim copy will silently
leave broken. The pieces that MUST be respelled in this plan's terms:

```python
# backend/cubebox/repositories/im_connector.py

async def get_or_create_thread_link(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    account_id: str,
    channel_id: str,
    scope_key: str,
    scope_kind: str,          # NEW — non-null on the model; must be passed on insert
    make_conversation_id,     # async callable () -> conversation_id
) -> tuple[IMThreadLink, bool]:
    stmt = select(IMThreadLink).where(
        IMThreadLink.account_id == account_id,
        IMThreadLink.channel_id == channel_id,
        IMThreadLink.scope_key == scope_key,    # lookup keys on scope_key only
    )
    existing = (await session.execute(stmt)).scalars().one_or_none()
    if existing is not None:
        return existing, False
    conversation_id = await make_conversation_id()
    link = IMThreadLink(
        org_id=org_id, workspace_id=workspace_id, account_id=account_id,
        channel_id=channel_id,
        scope_key=scope_key, scope_kind=scope_kind,   # both NOT NULL on the model
        conversation_id=conversation_id,
    )
    session.add(link)
    return link, True
```

```python
# backend/cubebox/im/inbound.py — IntegrityError discrimination

def _is_thread_link_unique_violation(exc: IntegrityError) -> bool:
    # The new schema's unique index is uq_im_scope_link, NOT uq_im_thread_link
    # (the latter is the Slack-plan name that no longer exists). A copy-paste
    # of the Slack helper here will silently never match, and concurrent
    # first-events on the same scope will surface as 500s instead of the
    # intended retry-into-existing-link path.
    return "uq_im_scope_link" in _constraint_name(exc)


def _is_receipt_unique_violation(exc: IntegrityError) -> bool:
    return "uq_im_receipt_account_event" in _constraint_name(exc)
```

```python
# backend/cubebox/im/inbound.py — IMRunQueueItem construction with neutral fields

item = IMRunQueueItem(
    org_id=account.org_id,
    workspace_id=account.workspace_id,
    account_id=account.id,
    receipt_id=receipt.id,
    conversation_id=link.conversation_id,
    content=event.text,
    channel_id=event.channel_id,                # was: slack_channel_id
    scope_key=event.scope_key,                   # was: slack_thread_ts (dedup key role)
    scope_kind=event.scope_kind,                 # NEW — NOT NULL on the model
    reply_to_id=event.reply_to_id,               # was: slack_reply_thread_ts (real reply target)
    inbound_message_id=event.inbound_message_id,
    sender_im_user_id=event.sender_ref,
)
```

The call to `get_or_create_thread_link` from `ingest_inbound_event` must
pass `scope_key=event.scope_key, scope_kind=event.scope_kind` — otherwise
the new NOT NULL `scope_kind` column rejects the first-message insert
for every (chat, sender) pair.

Everything else (the outer transaction shape, `_constraint_name` helper,
`claim_pending_queue_item` with `SKIP LOCKED` reclaim path) is platform-
neutral and can be lifted from the Slack plan unchanged.

The queue claim primitive (`claim_pending_queue_item`) is identical and
includes the reclaim-on-stale-lease path — copy that as-is.

- [ ] **Step 5: Run + commit**

```bash
cd backend && uv run pytest tests/integration/test_im_inbound_outbox.py -v
git add backend/cubebox/repositories/im_connector.py backend/cubebox/im/inbound.py \
        backend/tests/integration/test_im_inbound_outbox.py backend/tests/integration/conftest.py
git commit -m "feat(im): transactional inbound core (receipt + thread + run enqueue)"
```

---

## Task 6: Queue worker — drain → start_run

Same mechanics as the Slack plan's Task 7. Worker claims a row via
`FOR UPDATE SKIP LOCKED`, calls `RunManager.start_run(...)` with
`RunContext(user_id=account.acting_user_id, org_id, workspace_id)`, flips the
receipt to `completed`, fires `on_run_started(run_id, item)` so the app can
spawn the outbound tailer.

- [ ] **Step 1: Test** — mirror the sister Slack plan's `test_im_worker.py` (the test of `process_one_queue_item` + the no-queue-empty case under "## Task 7: Queue worker"), update fields to neutral schema, no other changes.
- [ ] **Step 2: Implement** — copy the `cubebox/im/worker.py` block (the `IMRunQueueWorker` class + `process_one_queue_item`) from the sister Slack plan's Task 7 verbatim. The worker is platform-neutral; no Slack assumptions in it. Update the one import (`from cubebox.models.im_connector import IMRunQueueItem, IMWebhookReceipt`) — schema column names already match.
- [ ] **Step 3: Run + commit**:
  ```bash
  cd backend && uv run pytest tests/integration/test_im_worker.py -v
  git add backend/cubebox/im/worker.py backend/tests/integration/test_im_worker.py
  git commit -m "feat(im): durable run-queue worker (claim -> start_run -> complete receipt)"
  ```

---

## Task 7: Feishu long-connection inbound mode

Wire up `lark_oapi.ws.Client` so a configured Feishu account opens a
long-connection on app startup and routes `im.message.receive_v1` events
into `ingest_inbound_event`. The connector handles its own reconnection (the
official SDK retries internally), and `connect()` hydrates the bot identity
via `application.v6.application.get` before the dispatcher accepts events.

**Files:**
- Create: `backend/cubebox/im/feishu/long_connection.py`
- Test: `backend/tests/unit/test_feishu_long_connection_handler.py` (unit-tests the **dispatch lambda**, not the SDK)

The long-connection mode runs alongside the durable queue: an event arrives
on the WebSocket → `parse_inbound` → `ingest_inbound_event` → queue worker
picks it up → `start_run`. The WebSocket is just a transport for the
**inbound** half; the run path and the outbound tailer don't know which
inbound mode delivered the event.

- [ ] **Step 1: Unit-test the dispatch lambda in isolation**

```python
# backend/tests/unit/test_feishu_long_connection_handler.py
import pytest

from cubebox.im.feishu.long_connection import build_event_handler

pytestmark = pytest.mark.asyncio


class _RecordingIngest:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, event, account, session_maker):
        self.calls.append({"event_id": event.platform_event_id, "text": event.text})

        class _R:
            outcome = "enqueued"
            conversation_id = "conv-x"

        return _R()


class _FakeAccount:
    id = "imac-1"
    platform = "feishu"
    external_account_id = "cli_a1b2"


async def test_handler_routes_message_event_into_ingest():
    ingest = _RecordingIngest()
    handler = build_event_handler(
        account=_FakeAccount(),
        bot_open_id="ou_bot",
        ingest=ingest,
        session_maker=None,
    )
    # Synthesize the parsed event payload shape the SDK delivers (a SimpleNamespace
    # mirroring P2ImMessageReceiveV1.event_data — see PoC notes for exact attrs).
    # The handler shells out to FeishuConnector.parse_inbound, which expects a
    # dict. The long_connection module is responsible for converting SDK
    # objects to dicts before calling parse_inbound.
    # ... (concrete shape comes from PoC notes; this test pins the contract).
```

> The lark_oapi SDK delivers events as Python objects, not raw dicts. The
> handler module **must** convert them via a helper (e.g. `lark.JSON.marshal`)
> before passing to `parse_inbound`, otherwise the dispatch crashes on
> attribute vs key access. Pin this in the test.

- [ ] **Step 2: Implement**

```python
# backend/cubebox/im/feishu/long_connection.py
"""Feishu long-connection (WebSocket) inbound transport."""

import asyncio
import json
from typing import Any, Awaitable, Callable

from loguru import logger

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
    LARK_AVAILABLE = True
except ImportError:
    LARK_AVAILABLE = False
    lark = None  # type: ignore[assignment]
    P2ImMessageReceiveV1 = None  # type: ignore[assignment]

from cubebox.im.feishu.connector import FeishuConnector

IngestCallable = Callable[..., Awaitable[Any]]


def build_event_handler(
    *,
    account: Any,
    bot_open_id: str,
    ingest: IngestCallable,
    session_maker: Any,
    loop: asyncio.AbstractEventLoop,
) -> Any:
    """Build a lark_oapi event dispatcher that routes message-receive events
    into the transactional ingest core.

    `loop` is the running asyncio loop captured at startup; the SDK callback
    fires on its own worker thread and MUST cross back via
    `asyncio.run_coroutine_threadsafe(coro, loop)`. Using
    `asyncio.get_event_loop()` from the SDK thread raises `RuntimeError` on
    Python 3.12+ when there is no loop attached to that thread (cubebox is on
    3.13). Hermes' equivalent helper is `_submit_on_loop` →
    `safe_schedule_threadsafe`; see `~/hermes-agent/gateway/platforms/feishu.py:2547`.
    """
    if not LARK_AVAILABLE:
        raise RuntimeError("lark_oapi not installed")

    connector = FeishuConnector(bot_open_id=bot_open_id)

    def _on_message(data: P2ImMessageReceiveV1) -> None:
        # The SDK delivers a typed object whose marshal output is JUST the
        # event body — there is no webhook-style `{header: ..., event: ...}`
        # envelope, so parse_inbound's first guard would always fail if we
        # passed `raw` directly. Reconstruct the envelope so the parser sees
        # exactly the shape it sees on the webhook path.
        try:
            event_dict = json.loads(lark.JSON.marshal(data.event))
            event_id = getattr(getattr(data, "header", None), "event_id", "") or ""
        except Exception:
            logger.exception("[Feishu LC] failed to marshal inbound event")
            return
        raw = {
            "header": {"event_id": event_id, "event_type": "im.message.receive_v1"},
            "event": event_dict,
        }
        event = connector.parse_inbound(raw)
        if event is None:
            return
        # Fill the account external id (parse_inbound leaves it blank by design;
        # the long-connection delivery is by definition bound to this account).
        event.account_external_id = account.external_account_id

        # Cross from the SDK worker thread back onto the asyncio loop. Captured
        # at startup; never call `get_event_loop()` from this thread.
        asyncio.run_coroutine_threadsafe(_ingest_and_log(event), loop)

    async def _ingest_and_log(event):
        try:
            res = await ingest(event, account=account, session_maker=session_maker)
            logger.info("[Feishu LC] inbound {}: {}", event.platform_event_id, res.outcome)
        except Exception:
            logger.exception("[Feishu LC] ingest failed for {}", event.platform_event_id)

    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_on_message)
        .build()
    )


class FeishuLongConnection:
    """Hold one lark_oapi WebSocket client per IM account."""

    def __init__(
        self,
        *,
        account: Any,
        app_id: str,
        app_secret: str,
        bot_open_id: str,
        ingest: IngestCallable,
        session_maker: Any,
        domain: str = "feishu",  # 'feishu' (cn) | 'lark' (intl)
    ) -> None:
        if not LARK_AVAILABLE:
            raise RuntimeError("lark_oapi not installed")
        self._account = account
        self._app_id = app_id
        self._app_secret = app_secret
        self._bot_open_id = bot_open_id
        self._ingest = ingest
        self._session_maker = session_maker
        self._domain = domain
        self._ws_future: asyncio.Future[None] | None = None
        self._client: Any = None

    async def connect(self) -> None:
        from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN

        domain = LARK_DOMAIN if self._domain == "lark" else FEISHU_DOMAIN
        # Capture the running loop NOW (we're on the main asyncio thread). The
        # event handler closes over it and uses run_coroutine_threadsafe to
        # hop back from the SDK worker thread. Capturing here ensures the
        # handler isn't constructed against a default loop that nobody runs.
        loop = asyncio.get_running_loop()
        handler = build_event_handler(
            account=self._account,
            bot_open_id=self._bot_open_id,
            ingest=self._ingest,
            session_maker=self._session_maker,
            loop=loop,
        )
        self._client = (
            lark.ws.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .domain(domain)
            .event_handler(handler)
            .log_level(lark.LogLevel.INFO)
            .build()
        )
        # Reuse the running loop we captured above. The SDK's start() is
        # blocking; run it in a thread executor against that loop.
        self._ws_future = loop.run_in_executor(None, self._client.start)

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.stop()
            except Exception:
                logger.debug("[Feishu LC] stop() raised", exc_info=True)
        if self._ws_future is not None:
            self._ws_future.cancel()
```

> Bot-identity hydration (`/bot/v3/info` → bot's own open_id) is done **once
> in `connect_feishu` at account-create time** (Task 15) and stored in the
> credential JSON, NOT on every app boot. Task 13 reads `bot_open_id` from
> the decrypted credential and passes it to `FeishuLongConnection`. The
> webhook ingress path (Task 12) reads it from the same place. The Slack
> plan's equivalent of this
> step is implicit (Slack sends the bot id in every event); Feishu doesn't,
> so we must hydrate up front.

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/unit/test_feishu_long_connection_handler.py -v
git add backend/cubebox/im/feishu/long_connection.py \
        backend/tests/unit/test_feishu_long_connection_handler.py
git commit -m "feat(im): Feishu long-connection inbound transport (lark_oapi)"
```

---

## Task 8: Outbound rendering decisions (unit) — text + tools + adaptive backoff

`fold_event(run_event, state, *, now)` is a pure function: it folds a run
event into `RenderState` and returns an `OutboundOp` describing the next IM
call (post placeholder / edit / no-op / final edit / error edit / artifact
dispatch). Key behavioural points:

- **Edit debounce default: 0.8s** (matches hermes; we are more conservative
  than the Slack plan's 0.5s because Feishu's `im.v1.message.update` rate
  limit is tighter and the prior-art adapter learned this).
- **Adaptive backoff**: on a flood-control error reply from
  `im.v1.message.update`, the connector raises a typed exception; `fold_event`
  doubles `state.edit_interval` up to 10s. After 3 consecutive flood strikes,
  `state.edits_disabled = True` — text deltas stop emitting edit ops; the
  final `done` event still emits one final edit (best-effort) so the user
  sees a complete answer.
- **Tool activity coalesced**: each unique tool name appears once as
  `_running \`name\`…_`. Not streamed token-by-token.
- **Artifact events** emit a dedicated `OutboundOp(kind="artifact")` carrying
  the artifact dict; the connector decides whether to upload-as-image or post
  a share-link (Task 11).
- **Idempotency**: each artifact id added to `state.posted_artifacts` so a
  re-emit (e.g. an `action=updated` event for the same id) only re-posts if
  it's an update, not a duplicate creation.

**Files:**
- Create: `backend/cubebox/im/outbound.py`
- Test: `backend/tests/unit/test_im_outbound_render.py`

- [ ] **Step 1: Write failing test** covering: first text → post; debounce window suppresses; debounce elapsed → edit; tool_call coalesced; done → final edit; error → final error edit; adaptive backoff doubles interval on flood; 3rd flood disables edits; artifact event emits an `OutboundOp(kind="artifact")` with the artifact payload.

- [ ] **Step 2: Implement**

```python
# backend/cubebox/im/outbound.py
"""Outbound rendering: fold run events into debounced IM ops, tail Redis."""

from dataclasses import dataclass
from typing import Any

from cubebox.im.types import RenderState

_EDIT_INTERVAL_DEFAULT = 0.8
_EDIT_INTERVAL_MAX = 10.0
_MAX_FLOOD_STRIKES = 3


@dataclass(slots=True)
class OutboundOp:
    kind: str          # 'post' | 'edit' | 'artifact' | 'no_op'
    text: str = ""
    final: bool = False
    artifact: dict[str, Any] | None = None


def _composite_text(state: RenderState) -> str:
    parts: list[str] = []
    if state.tool_lines:
        parts.append("\n".join(state.tool_lines))
    if state.text_buffer:
        parts.append(state.text_buffer)
    return "\n\n".join(parts) if parts else "…"


def fold_event(event: dict[str, Any], state: RenderState, *, now: float) -> OutboundOp | None:
    etype = event.get("type")
    data = event.get("data") or {}

    if etype == "text_delta":
        state.text_buffer += data.get("content", "")
        if state.message_id is None:
            state.last_edit_monotonic = now
            return OutboundOp(kind="post", text=_composite_text(state))
        if state.edits_disabled:
            return None
        if now - state.last_edit_monotonic < state.edit_interval:
            return None
        state.last_edit_monotonic = now
        return OutboundOp(kind="edit", text=_composite_text(state))

    if etype == "tool_call":
        name = data.get("name", "tool")
        line = f"_running `{name}`…_"
        if line not in state.tool_lines:
            state.tool_lines.append(line)
        return None

    if etype == "artifact":
        artifact = data.get("artifact") or {}
        art_id = artifact.get("id", "")
        action = data.get("action", "created")
        if not art_id:
            return None
        already = art_id in state.posted_artifacts
        if already and action == "created":
            return None
        state.posted_artifacts.add(art_id)
        return OutboundOp(kind="artifact", artifact=artifact)

    if etype == "done":
        # If no placeholder was ever posted (run finished before any text_delta —
        # e.g. tool-only run, fast guardrail rejection, or edits_disabled
        # silently dropping every delta), emit a 'post' instead of 'edit'.
        # 'edit' against state.message_id is None would crash or no-op silently.
        kind = "post" if state.message_id is None else "edit"
        return OutboundOp(kind=kind, text=_composite_text(state), final=True)

    if etype == "error":
        msg = data.get("message", "the run failed")
        kind = "post" if state.message_id is None else "edit"
        return OutboundOp(kind=kind, text=f"⚠️ error: {msg}", final=True)

    return None


def note_flood_strike(state: RenderState) -> None:
    """Called by the tailer when an edit hit Feishu's rate limit."""
    state.consecutive_flood_strikes += 1
    state.edit_interval = min(state.edit_interval * 2, _EDIT_INTERVAL_MAX)
    if state.consecutive_flood_strikes >= _MAX_FLOOD_STRIKES:
        state.edits_disabled = True


def note_edit_success(state: RenderState) -> None:
    state.consecutive_flood_strikes = 0
```

The `OutboundRunTailer` class lives here too and orchestrates: tail Redis →
`fold_event` → call connector send/edit/react/artifact-dispatch. Implementation
mirrors the sister Slack plan's `OutboundRunTailer` (its Task 8 §Step 3) with three changes:

1. After a successful first `post`, store the returned `message_id` in
   `state.message_id`.
2. After a successful edit, call `note_edit_success(state)`; on a flood
   exception, call `note_flood_strike(state)` and move on.
3. On `OutboundOp(kind="artifact")`, dispatch into the artifact handler
   (Task 11) — `await artifact_dispatcher.handle(state, op.artifact)`.

- [ ] **Step 3: Commit**

```bash
cd backend && uv run pytest tests/unit/test_im_outbound_render.py -v
git add backend/cubebox/im/outbound.py backend/tests/unit/test_im_outbound_render.py
git commit -m "feat(im): outbound render fold + adaptive edit backoff"
```

---

## Task 9: Feishu connector send / edit / image upload via lark_oapi

Extend `FeishuConnector` with outbound methods.

**Synchronous SDK calls MUST be wrapped in `asyncio.to_thread`.** The
`lark_oapi` Web API client is synchronous (blocking HTTP). Each connector
method below is declared `async def` but actually invokes a sync SDK call;
without `to_thread`, every edit / reaction / image upload freezes the event
loop for the duration of the HTTP round-trip (100–400ms at p50, multi-second
under flood control). That stalls the queue worker, every other tailer in
the same process, the long-connection dispatcher's ingest hop, and the
FastAPI request loop — symptoms look like "Feishu duplicate retries because
we missed the 3-second ack window" and "edits clump unpredictably". Hermes'
prior art does this wrap in every send/edit call
(`~/hermes-agent/gateway/platforms/feishu.py:1842`,
`response = await asyncio.to_thread(self._client.im.v1.message.update, request)`).
Apply the same discipline here.

- `post_placeholder(text) -> str`: returns `message_id`. Uses
  `client.im.v1.message.reply` against `self._reply_to_id` when set (groups
  use the inbound `message_id` as the reply target so the reply renders as a
  quote-reply in the main chat — Feishu's most natural UX). When
  `self._reply_to_id is None` (DMs), uses `client.im.v1.message.create` with
  `receive_id=self._channel_id` and `receive_id_type="chat_id"` for a plain
  send. **Both calls go through `asyncio.to_thread(self._client.im.v1.message.<op>, request)`.**
- `edit(message_id, text)`: `client.im.v1.message.update`. Raises
  `FeishuRateLimitError` on flood/quota responses so the tailer can call
  `note_flood_strike(state)`.
- `upload_image(local_path) -> image_key`: `client.im.v1.image.create`
  (`image_type="message"`). Returns the `image_key` used in subsequent
  `send_image_message`.
- `send_image_message(image_key)`: sends a `msg_type="image"` reply to the
  same thread.
- `send_text_message(text)`: a non-edit send (used for share-links and
  artifact captions that should be their own message bubble, not edits to
  the streaming reply).
- **Post payload selection**: detect markdown tables (regex) → fall back to
  `text` type; otherwise prefer `post` type for richer rendering, with a
  best-effort plain-text fallback on `MsgContentInvalid`. This mirrors
  `~/hermes-agent/gateway/platforms/feishu.py:4310` (`_build_outbound_payload`).

**Files:**
- Modify: `backend/cubebox/im/feishu/connector.py`
- Test: `backend/tests/unit/test_feishu_outbound_payload.py` (pure payload-construction
  tests; the `lark_oapi` HTTP call itself is the unsimulatable boundary covered by Task 16 smoke)

- [ ] **Step 1: Test the payload selection logic** (markdown table → text fallback; plain text → text; markdown prose → post). Pure unit, no network.

- [ ] **Step 2: Implement.** Define a typed `FeishuRateLimitError(Exception)`. The Lark SDK signals rate-limit via response codes `99991400`, `99991401`, `230020` (per the SDK docs / hermes prior art) — match on response code, raise the typed exception, let the tailer handle.

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/unit/test_feishu_outbound_payload.py -v
git add backend/cubebox/im/feishu/connector.py backend/tests/unit/test_feishu_outbound_payload.py
git commit -m "feat(im): Feishu connector send/edit/image-upload via lark_oapi"
```

---

## Task 10: Reactions — processing status UX

**This is a required v1 feature** (per user direction).

### Hook on the connector, not Feishu vocabulary in the tailer

Processing-status UX is platform-specific: Feishu uses reactions, Slack uses
`assistant.threads.setStatus` ("is typing…"), Telegram uses `sendChatAction`.
The `OutboundRunTailer` is meant to be platform-agnostic, so it must not
contain calls like `add_reaction(..., "thinking-face")` directly — that
Feishu vocabulary would leak across every future connector. Instead the
tailer calls **three connector-level hooks** and lets each connector
implement them with its own primitive:

```python
class IMConnector(Protocol):
    async def on_processing_start(self, state: RenderState) -> None: ...
    async def on_processing_complete(self, state: RenderState) -> None: ...
    async def on_processing_failed(self, state: RenderState) -> None: ...
```

`FeishuConnector` implements them via `im.v1.message.reaction`; SlackConnector
will implement them via `setStatus`. The tailer never sees a platform string.
This matches the prior-art shape at
`~/hermes-agent/gateway/platforms/feishu.py:2965`.

### Feishu implementation

- `on_processing_start`: `add_reaction(state.inbound_message_id, "thinking-face")`.
  Save the returned `reaction_id` to `state.reaction_in_progress_id`.
- `on_processing_complete`: `remove_reaction(...)`.
- `on_processing_failed`: `remove_reaction(...)`, then
  `add_reaction(..., "CROSS-MARK")`.

**Critical: handle the case where `add_reaction` itself failed.** If a
fresh install lacks the `im:message.reaction:write` scope, the first call
raises. `state.reaction_in_progress_id` stays `None`. Then on `error`, the
naive `remove_reaction(inbound_message_id, None)` raises a second time and
masks the original run error. Both `remove_reaction` and the connector's
`on_processing_complete` / `on_processing_failed` must **no-op when
reaction_in_progress_id is None**:

```python
async def on_processing_failed(self, state: RenderState) -> None:
    if state.reaction_in_progress_id is not None:
        try:
            await self.remove_reaction(state.inbound_message_id, state.reaction_in_progress_id)
        except Exception:
            logger.warning("[Feishu] remove_reaction failed; continuing", exc_info=True)
        state.reaction_in_progress_id = None
    try:
        await self.add_reaction(state.inbound_message_id, "CROSS-MARK")
    except Exception:
        logger.warning("[Feishu] add CROSS-MARK failed", exc_info=True)
```

Same defensive pattern for `on_processing_complete`. The tailer calls
the hook unconditionally; the connector decides whether each call is safe.

**Files:**
- Modify: `backend/cubebox/im/feishu/connector.py` (add `add_reaction` / `remove_reaction` low-level methods + the three `on_processing_*` hooks).
- Modify: `backend/cubebox/im/outbound.py` (`OutboundRunTailer` calls the connector hooks at start/done/error — no Feishu strings).
- Test: `backend/tests/unit/test_im_reactions.py` — assert the tailer invokes `on_processing_start` / `on_processing_complete` / `on_processing_failed` on a recording connector at the right moments; assert FeishuConnector's hook no-ops when the prior reaction is None.

- [ ] **Step 1: Write failing test**

```python
# backend/tests/unit/test_im_reactions.py
import pytest

from cubebox.im.outbound import OutboundRunTailer
from cubebox.im.types import RenderState

pytestmark = pytest.mark.asyncio


class _RecordingConnector:
    def __init__(self) -> None:
        self.posts: list[str] = []
        self.edits: list[str] = []
        self.reactions_added: list[tuple[str, str]] = []
        self.reactions_removed: list[tuple[str, str]] = []
        self._next_id = 0

    async def post_placeholder(self, text):
        self.posts.append(text)
        return "om_reply1"

    async def edit(self, mid, text):
        self.edits.append(text)

    async def add_reaction(self, message_id, reaction_type):
        self._next_id += 1
        rid = f"r-{self._next_id}"
        self.reactions_added.append((message_id, reaction_type))
        return rid

    async def remove_reaction(self, message_id, reaction_id):
        self.reactions_removed.append((message_id, reaction_id))


async def test_processing_reaction_added_on_start_and_removed_on_done(...):
    """Tailer must call add_reaction('om_inbound1', THINKING) on start
    and remove that same reaction on a 'done' event."""
    # ... (assert reactions_added has THINKING, reactions_removed matches it)
```

- [ ] **Step 2: Implement** the reaction lifecycle as connector hooks called from `OutboundRunTailer.run()`:
  - Before entering the event loop: `await self._connector.on_processing_start(state)`.
  - On `done` (terminal): `await self._connector.on_processing_complete(state)`.
  - On `error` (terminal): `await self._connector.on_processing_failed(state)`.

  The tailer never references "thinking-face" / "CROSS-MARK" / `im.v1.message.reaction` — those strings live in `FeishuConnector` only. Slack's eventual connector will implement the same three hooks against `assistant.threads.setStatus`.

- [ ] **Step 3: Run + commit**

```bash
cd backend && uv run pytest tests/unit/test_im_reactions.py -v
git add backend/cubebox/im/feishu/connector.py backend/cubebox/im/outbound.py \
        backend/tests/unit/test_im_reactions.py
git commit -m "feat(im): processing status reactions (in-progress / success / failure)"
```

---

## Task 11: Artifact dispatch — inline image, share-link for the rest

When the run emits an `artifact` event, the IM side decides how to show it:

1. **`artifact_type == "image"`** → fetch the file from the artifact store,
   upload via Feishu `im.v1.image.create`, post `msg_type="image"` reply in
   the same thread. Inline, no click required.
2. **`artifact_type == "file"`** (with a `mime_type` Feishu supports) →
   upload via `im.v1.file.create`, post `msg_type="file"`. (Optional v1: if
   the file is small enough; otherwise treat as #3.)
3. **Everything else** (`website`, `code`, `document`, `data`, `skill`) →
   mint a signed share token, post a short message
   `📎 {name} · {type} · view → {share_url}`.

**Share-link mechanism — extract a service helper, NOT a copy of the route.**
`backend/cubebox/api/routes/v1/artifacts.py:193` already implements a Redis
nonce + `/api/v1/public/artifacts/dl/{nonce}/{filename}` pattern (designed
for Office Online Viewer, currently restricted to `.docx`/`.xlsx`/`.pptx`).
Two consumers need it now: the workspace-scoped HTTP route (`require_member`)
**and the IM outbound tailer** (a background task with no user session).

The HTTP route cannot be the integration point for the tailer — the tailer
has no `RequestContext` and is not a workspace member. Extract the
core into a service function and have both consumers call it directly:

```python
# backend/cubebox/services/artifact_share.py — NEW
"""Mint and validate public artifact share tokens (no auth context needed)."""

import secrets
import orjson

SHARE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


async def mint_share_token(
    *,
    redis,
    key_prefix: str,
    org_id: str,
    workspace_id: str,
    conversation_id: str,
    artifact_id: str,
    version: int,
    ttl_seconds: int = SHARE_TTL_SECONDS,
) -> str:
    """Return a fresh nonce that maps to (org, ws, conv, artifact, version)
    in Redis for `ttl_seconds`. Caller composes the public URL."""
    nonce = secrets.token_hex(32)
    payload = orjson.dumps({
        "org_id": org_id, "workspace_id": workspace_id,
        "conversation_id": conversation_id, "artifact_id": artifact_id,
        "version": version,
    })
    key = f"{key_prefix}:share:{nonce}"
    await redis.set(key, payload, ex=ttl_seconds)
    return nonce


async def resolve_share_token(
    *, redis, key_prefix: str, nonce: str,
) -> dict | None:
    raw = await redis.get(f"{key_prefix}:share:{nonce}")
    return orjson.loads(raw) if raw is not None else None
```

Three thin consumers wrap this:

- A new endpoint `POST /artifacts/{id}/share-token` (workspace-scoped,
  `require_member`): authenticates the caller, looks up the artifact via the
  scoped repo, calls `mint_share_token`, returns the assembled URL.
- A new public preview page `GET /api/v1/public/artifacts/share/{nonce}`:
  calls `resolve_share_token`, looks up the artifact via an UNSCOPED repo
  call (org_id/workspace_id come from the resolved payload), renders HTML.
- The IM tailer's `IMArtifactDispatcher` calls `mint_share_token` directly
  with `(org_id, workspace_id, conversation_id, artifact_id, version)` from
  the `IMRunQueueItem` + the `artifact` event payload — no HTTP hop, no
  session, no `require_member`.

Tokens are bound to `(org_id, workspace_id, conversation_id, artifact_id,
version)`; once expired the share link 404s with a "this link has expired"
page.

**Files:**
- Create: `backend/cubebox/services/artifact_share.py` (`mint_share_token` / `resolve_share_token`).
- Modify: `backend/cubebox/api/routes/v1/artifacts.py` (relax `OFFICE_EXTENSIONS` gate to a separate `OFFICE_VIEWER_EXTENSIONS` for the existing Office route; **add new** `share-token` route that delegates to `mint_share_token`).
- Create: `backend/cubebox/api/routes/v1/artifact_share.py` (public preview page that delegates to `resolve_share_token`).
- Create: `backend/cubebox/im/artifacts.py` (the dispatcher used by the tailer).
- Test: `backend/tests/integration/test_artifact_share_token.py` (real Redis), `backend/tests/unit/test_im_artifact_dispatch.py`.

- [ ] **Step 1: Add the share-token route (thin wrapper around the service)**

```python
# inside backend/cubebox/api/routes/v1/artifacts.py


@router.post("/{artifact_id}/share-token")
async def create_share_token(
    conversation_id: str,
    artifact_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rh: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, str]:
    """Issue a public, time-limited share URL for any artifact_type."""
    await _require_conversation(session, ctx, conversation_id)
    repo = ArtifactRepository(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")

    nonce = await mint_share_token(
        redis=rh.client, key_prefix=rh.key_prefix,
        org_id=ctx.org_id, workspace_id=ctx.workspace_id,
        conversation_id=conversation_id, artifact_id=artifact_id,
        version=artifact.version,
    )
    base = str(config.get("api.public_url", "") or request.base_url).rstrip("/")
    return {"share_url": f"{base}/api/v1/public/artifacts/share/{nonce}"}
```

- [ ] **Step 2: Add the public preview page**

```python
# backend/cubebox/api/routes/v1/artifact_share.py
"""Public artifact preview page (no auth, nonce-validated)."""

import orjson
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.db.session import get_session
from cubebox.dependencies import RedisHandle, redis_dep
from cubebox.repositories.artifact import ArtifactRepository

router = APIRouter(prefix="/public/artifacts", tags=["artifact-share"])


@router.get("/share/{nonce}", response_class=HTMLResponse)
async def share_page(
    nonce: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    rh: RedisHandle = Depends(redis_dep),
) -> HTMLResponse:
    key = f"{rh.key_prefix}:share:{nonce}"
    raw = await rh.client.get(key)
    if raw is None:
        raise HTTPException(status_code=404, detail="share link expired or invalid")
    payload = orjson.loads(raw)
    repo = ArtifactRepository(
        session, org_id=payload["org_id"], workspace_id=payload["workspace_id"],
    )
    artifact = await repo.get_by_id(payload["artifact_id"])
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    # Render the artifact via a Jinja template (new — minimal viewer).
    return HTMLResponse(_render_share_html(artifact, payload["version"]))


def _render_share_html(artifact, version: int) -> str:
    """Render a single-page read-only artifact preview.

    Layout: header (artifact name + type badge), main area:
    - image: <img src="data:..." />  (fetched via internal artifact-content API)
    - code/document/data/skill: <pre><code> with PrismJS highlight
    - website: <iframe src=...> sandboxed
    """
    ...  # implementation tracked here; uses the existing artifact-content endpoint
```

> The share page is a **single static HTML** with PrismJS (CDN) for syntax
> highlighting and an `<iframe sandbox>` for website-type artifacts. No
> React, no auth, no JS framework — keeps the public surface area minimal.
> The artifact bytes are fetched from the existing internal endpoint
> (`/api/v1/public/artifacts/dl/{nonce}/{filename}` for the file payload —
> we mint a sibling nonce keyed to the same share session).

- [ ] **Step 3: IM dispatcher**

```python
# backend/cubebox/im/artifacts.py
"""IM-side artifact dispatcher."""

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class IMArtifactDispatcher:
    """Decides how to surface an artifact event in the IM thread.

    The dispatcher runs inside the outbound tailer (a background task with
    no user session), so it calls the share-token SERVICE function directly
    — NOT the HTTP endpoint, which requires require_member. Bound at tailer
    construction with the IMRunQueueItem's (org_id, workspace_id,
    conversation_id) so the share token is scoped correctly.
    """

    connector: Any           # FeishuConnector bound to this thread
    redis: Any               # rh.client for the run
    redis_key_prefix: str
    public_base_url: str     # e.g. config.get("api.public_url")
    org_id: str
    workspace_id: str
    conversation_id: str

    async def handle(self, artifact: dict[str, Any]) -> None:
        atype = artifact.get("artifact_type", "")
        name = artifact.get("name", "artifact")
        if atype == "image":
            # Download from artifact store, upload to Feishu, post image message.
            local_path = await self._fetch_to_tmp(artifact)
            image_key = await self.connector.upload_image(local_path)
            await self.connector.send_image_message(image_key)
            return
        # Everything else: mint a share token via the service, post the URL.
        from cubebox.services.artifact_share import mint_share_token

        nonce = await mint_share_token(
            redis=self.redis, key_prefix=self.redis_key_prefix,
            org_id=self.org_id, workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
            artifact_id=artifact["id"],
            version=artifact.get("version", 1),
        )
        share_url = f"{self.public_base_url.rstrip('/')}/api/v1/public/artifacts/share/{nonce}"
        await self.connector.send_text_message(
            f"📎 *{name}* · {atype} · [view →]({share_url})"
        )

    async def _fetch_to_tmp(self, artifact: dict[str, Any]) -> str:
        """Pull the artifact file from the artifact store into a temp path."""
        ...  # uses the same internal API the existing artifacts route uses
```

- [ ] **Step 4: Tests**

- Unit: `test_im_artifact_dispatch.py` — given `artifact_type="image"`, the
  dispatcher calls `upload_image` + `send_image_message`; given any other
  type, it calls `share_token_fn` then `send_text_message` with the URL.
- Integration: `test_artifact_share_token.py` — POST `/artifacts/{id}/share-token`
  returns a URL; GET that URL returns 200 + correct artifact name in the
  body; an expired/invalid nonce returns 404.

- [ ] **Step 5: Run + commit**

```bash
cd backend && uv run pytest tests/unit/test_im_artifact_dispatch.py \
                         tests/integration/test_artifact_share_token.py -v
git add backend/cubebox/api/routes/v1/artifacts.py \
        backend/cubebox/api/routes/v1/artifact_share.py \
        backend/cubebox/im/artifacts.py \
        backend/tests/unit/test_im_artifact_dispatch.py \
        backend/tests/integration/test_artifact_share_token.py
git commit -m "feat(im): artifact dispatch (inline image; share-link for other types)"
```

---

## Task 12: Feishu webhook ingress route (production path #2)

Webhook ingress is the secondary delivery mode — present in v1 so cloud
deploys with public ingress can use it. Local / firewall-bound deploys use
long-connection (Task 7).

**Files:**
- Create: `backend/cubebox/api/routes/v1/im_ingress.py`
- Modify: `backend/cubebox/api/app.py` (register router)
- Test: `backend/tests/e2e/test_im_feishu_ingress.py`

The handler follows the order from hermes' prior art
(`gateway/platforms/feishu.py:3264`), which has been hardened by real traffic:

```
1. Body size guard (Content-Length, then post-read).
2. Content-Type must be application/json.
3. Parse JSON.
4. Verification token (constant-time compare) — REJECTS BEFORE url_verification.
   (security: don't echo attacker-supplied challenge data without auth.)
5. If type == "url_verification": return {"challenge": ...}.
6. x-lark-signature HMAC verification.
7. Reject "encrypt" payloads (we don't support encrypted webhook bodies in v1).
8. Dispatch on header.event_type — im.message.receive_v1 → parse_inbound → ingest.
```

- [ ] **Steps**: failing E2E test (verification-token rejection, url_verification echo, bad-signature rejection, valid-event enqueue, unknown-account ack-and-drop) → implement → register → run → commit. Mirror the Slack plan's Task 9 structure.

```bash
git commit -m "feat(im): Feishu signed webhook ingress route"
```

---

## Task 13: Wire long-connection + worker + outbound tailer into app startup

On app startup:
1. Construct the `IMRunQueueWorker` with an `on_run_started` callback (closure
   over `app.state`).
2. **For every enabled Feishu account with `delivery_mode='long_connection'`**:
   decrypt the credential, **read `bot_open_id` from the stored credential
   JSON** (already hydrated at `connect_feishu` time — see Task 15), construct
   `FeishuLongConnection(bot_open_id=...)`, call `connect()`. Run the
   connect calls concurrently via `asyncio.gather(*, return_exceptions=True)`
   so one slow/bad account does not stall the others.
3. The worker's `on_run_started(run_id, item)` constructs an
   `OutboundRunTailer` bound to the account's connector with the **neutral
   queue-row fields** (channel_id, reply_to_id, inbound_message_id — NOT
   `slack_*` names from the sister plan).

On shutdown: stop the long-connection clients, then stop the worker, then
let `RunManager` drain.

**Files:**
- Modify: `backend/cubebox/api/app.py`
- Test: `backend/tests/e2e/test_im_worker_startup.py` (assert `app.state.im_run_queue_worker` exists; if any account has long-connection enabled, also assert `app.state.im_long_connections` has the right shape).

- [ ] **Step 1: Failing test** — assert `app.state.im_run_queue_worker` and `app.state.im_long_connections` after startup.

- [ ] **Step 2: Implement `_on_im_run_started` (explicit, Feishu-specific)**

```python
async def _on_im_run_started(run_id: str, item: IMRunQueueItem) -> None:
    async with _im_session_maker() as s:
        account = (await s.execute(
            select(IMConnectorAccount).where(IMConnectorAccount.id == item.account_id)
        )).scalars().one()
        creds = build_credential_service(s, app.state.encryption_backend,
                                         org_id=account.org_id, actor_user_id=None)
        secrets = json.loads(await creds.get_decrypted(
            credential_id=account.credential_id, requesting_kind="im_bot",
        ))

    connector = FeishuConnector(
        bot_open_id=secrets["bot_open_id"],         # hydrated at connect_feishu time
        client=_build_lark_client(secrets),         # SAME client used by the long-conn for this account, reused
        channel_id=item.channel_id,                 # NEUTRAL — NOT slack_channel_id
        reply_to_id=item.reply_to_id,               # NEUTRAL — NOT slack_reply_thread_ts
    )
    state = RenderState(
        reply_to_id=item.reply_to_id,
        inbound_message_id=item.inbound_message_id,  # propagated for reaction routing (Task 10)
    )
    tailer = OutboundRunTailer(
        redis=app.state.run_manager._redis,
        key_prefix=app.state.run_manager._key_prefix,
        run_id=run_id,
        connector=connector,
        state=state,
    )
    asyncio.create_task(tailer.run(), name=f"im-tailer:{run_id}")
```

Notes for the implementer:

- `build_credential_service` is the canonical factory at
  `cubebox/credentials/dependencies.py:21`. Do NOT instantiate
  `CredentialService(...)` directly — every site under `cubebox/im/` uses
  the factory so future signature changes do not silently regress.
- `_build_lark_client(secrets)` returns the same `lark_oapi.Client` shape
  used by the long-connection module. Reuse a single client per account
  (cache on `app.state.im_long_connections[account.id].client`) instead of
  rebuilding per turn — building a new client per `on_run_started` defeats
  HTTP keep-alive AND repeats KDF decryption pressure.
- The Slack-plan sister doc's `_on_run_started` references `item.slack_*`
  field names that **no longer exist**. Do not copy that block verbatim.

- [ ] **Step 3: Concurrent long-connection startup**

```python
async def _connect_one(account):
    try:
        creds = ...; secrets = ...        # decrypt
        lc = FeishuLongConnection(
            account=account, app_id=secrets["app_id"], app_secret=secrets["app_secret"],
            bot_open_id=secrets["bot_open_id"], ingest=ingest_inbound_event,
            session_maker=_im_session_maker, domain=secrets.get("domain", "feishu"),
        )
        await lc.connect()
        app.state.im_long_connections[account.id] = lc
    except Exception:
        logger.exception("[Feishu LC] failed to connect account {}", account.id)

await asyncio.gather(*(_connect_one(a) for a in enabled_long_conn_accounts),
                     return_exceptions=True)
```

- [ ] **Step 4: Run + commit**

```bash
git commit -m "feat(im): start worker + long-connection clients + outbound tailers on app boot"
```

---

## Task 14: End-to-end inbound → run → outbound chain (E2E, real run path)

The bulk of the system test, per spec. Real Postgres + Redis + run path. The
only thing faked is the outermost Feishu HTTP boundary (replaced by a
`_RecordingConnector` that records `post_placeholder` / `edit` / `add_reaction`
calls). Two variants:

- **Variant A — webhook ingress path**: POST a signed Feishu payload to
  `/api/v1/im/feishu/events`. Assert the receipt flips to `completed`, a
  conversation + thread link exist, and the outbound recorder saw at least
  one `post` + reaction added + (eventually) a `done` final edit + reaction
  removed.
- **Variant B — long-connection path**: directly drive the long-connection
  handler's `_on_message` callback (the unit-level pin from Task 7) but with
  the real ingest + worker + tailer wired up. Same assertions.

If the live LLM is not configured, the test skips via the existing run-path
E2E skip marker.

```bash
git commit -m "test(im): end-to-end inbound -> run -> outbound chain (webhook + long-conn)"
```

---

## Task 15: Scope-isolated config routes (workspace + admin) + identity guard

Identical structure to the Slack plan's Tasks 12 + 13. The
`IMConnectorService` and its `connect_*` methods are parameterized by
platform; the `connect_feishu` method takes
`{app_id, app_secret, encrypt_key, verification_token, domain}` (instead of
Slack's bot_token/signing_secret/bot_user_id) and writes them as a JSON
credential payload.

**Critical: `bot_open_id` must be hydrated and stored at connect time, not
re-fetched per inbound event.** `connect_feishu` calls Feishu's
`/bot/v3/info` (via `lark_oapi`'s `application.v6.application.get`) immediately
after the credential is decrypted, captures the bot's own `open_id`, and
writes it into the credential JSON alongside the secrets. Reasons:

- The webhook ingress path (Task 12) must instantiate `FeishuConnector` with
  a real `bot_open_id` so the defense-in-depth group-mention gate fires and
  the bot does NOT respond to its own echoed messages. Without hydration at
  connect time, the ingress route has no source for this value — the rest
  of the credential payload doesn't contain it.
- The long-connection startup (Task 13) also reads `bot_open_id` from the
  credential rather than calling `/bot/v3/info` again on every boot, so
  `connect_feishu` is the single point where this lookup happens.

Required credential JSON shape:

```json
{
  "app_id": "cli_a1b2",
  "app_secret": "<secret>",
  "encrypt_key": "<32+ char key>",
  "verification_token": "<dashboard token>",
  "domain": "feishu",
  "bot_open_id": "ou_xxxx"
}
```

`bot_open_id` is hydrated by `connect_feishu` itself; the caller does NOT
supply it. Re-hydration on credential rotation is a follow-up (v1 keeps the
hydrated value through the credential's lifetime).

**Files:**
- Create: `backend/cubebox/services/im_connector.py`,
  `backend/cubebox/api/schemas/im_connector.py`,
  `backend/cubebox/api/routes/v1/ws_im.py`,
  `backend/cubebox/api/routes/v1/admin_im.py`
- Modify: `backend/cubebox/services/credential.py` (`_guard_references` blocks
  deletion of an `im_bot` credential referenced by an `IMConnectorAccount`).
- Tests: `tests/e2e/test_ws_im_routes.py`, `tests/e2e/test_admin_im_routes.py`, `tests/e2e/test_im_isolation.py`.

Routes (note: workspace POST takes Feishu's app credentials, not Slack's):

```http
POST /api/v1/ws/{ws}/im/accounts
{
  "platform": "feishu",
  "external_account_id": "cli_a1b2",
  "app_id": "cli_a1b2",
  "app_secret": "...",
  "encrypt_key": "...",
  "verification_token": "...",
  "domain": "feishu",
  "delivery_mode": "long_connection",
  "acting_user_id": "self"
}

GET    /api/v1/ws/{ws}/im/accounts
DELETE /api/v1/ws/{ws}/im/accounts/{id}

GET    /api/v1/admin/im/accounts
POST   /api/v1/admin/im/accounts/{id}/enable
POST   /api/v1/admin/im/accounts/{id}/disable
```

Org-admin routes use `get_admin_request_context` (not `require_admin` — the
admin routes have no `{workspace_id}` segment, see the Slack plan's note at
its Task 13).

- [ ] Steps as in Slack plan Tasks 12 + 13 + 14 — copy the structure with platform fields swapped.

```bash
git commit -m "feat(im): scope-isolated workspace + admin Feishu account routes"
git commit -m "test(im): multi-tenant isolation E2E"
```

---

## Task 16: Feishu app setup doc + manual smoke checklist (unsimulatable boundary)

The Feishu HTTP boundary is unsimulatable; per the spec's testing strategy we
document a manifest + a manual smoke checklist instead of standing up a fake
Feishu server.

**File:** `backend/docs/im-feishu-setup.md`

Sections:

1. **Create a Feishu app** (open.feishu.cn). Enable bot, set name + avatar.
2. **Permissions** (minimum scope for v1):
   - `im:message` (receive)
   - `im:message:send_as_bot`
   - `im:resource` (image upload)
   - `im:message.group_at_msg` (group @mentions delivered)
   - `im:message.p2p_msg` (DMs delivered)
   - `im:message.reaction:write` (processing reactions)
   - `contact:user.base:readonly` (resolve sender display names; optional)
3. **Event subscriptions**:
   - `im.message.receive_v1`
   - (later: `im.message.reaction.created_v1`, `card.action.trigger` —
     out of v1 scope.)
4. **Delivery mode**:
   - **Long connection** (recommended for self-host): no callback URL needed.
     Cubebox holds the WebSocket on startup.
   - **Webhook** (cloud deploys with public ingress): set Request URL to
     `https://<host>/api/v1/im/feishu/events`, copy `Encrypt Key` and
     `Verification Token` into the `connect_feishu` request body.
5. **Install** by getting `App ID` + `App Secret` from the credentials page,
   then `POST /api/v1/ws/{ws}/im/accounts` with the chosen `delivery_mode`.
6. **Manual smoke checklist** (run before merging to main; cannot be
   automated):
   - [ ] DM the bot "hello" → bot replies with a streamed response in the
         DM. ⏱️ reaction appears on the user's message during processing,
         removed on completion.
   - [ ] DM "draw me a chart" (or any prompt that generates an image
         artifact) → image appears inline as a Feishu image message.
   - [ ] DM "build a tiny website" (or any non-image artifact) →
         "📎 view →" link appears; clicking it opens the share preview page.
   - [ ] User A `@bot summarize` in a group → bot quote-replies. A then sends
         a fresh `@bot 改成精简版` (no re-quote) → bot is **still in A's
         conversation** (chat × user session, not thread-per-message).
   - [ ] User B `@bot ...` in the same group → bot answers in a **separate
         conversation** from A's; B's context never bleeds into A's and vice
         versa.
   - [ ] A pure non-@ message in the group → bot does NOT respond (subscription
         + parser mention gate both hold).
   - [ ] Tamper a webhook signature → 401, no run started, no DB rows
         created (verify in psql).
   - [ ] Force a Feishu retry (slow ack) → no duplicate reply (receipt
         dedupe).
   - [ ] Disable the account via the admin route → next mention is silently
         dropped (200 ack, no run).
   - [ ] Trigger an LLM error mid-run → ⏱️ reaction is removed, ❌ reaction
         appears on the user's message, error notice in the reply.

```bash
git add backend/docs/im-feishu-setup.md
git commit -m "docs(im): Feishu app setup + manual smoke checklist"
```

---

## Task 17: Update the spec's v1 scope

The spec at `docs/dev/specs/2026-05-27-im-connectors-design.md` currently says
"Slack first, Feishu as v1.1". We are flipping this. The spec edit is part of
this plan (not a separate PR) because a spec/plan mismatch is exactly the
"plain language, no invented jargon" + "spec/plan consistency" rule the
project enforces.

- [ ] Edit `docs/dev/specs/2026-05-27-im-connectors-design.md`:
  - § "v1 scope": replace "Slack first" with "Feishu first". Cite the
    reason: long-connection mode does not need public ingress, so basic
    functionality can be validated inside a worktree before tunnel setup.
  - § "v1 scope": add reaction support and artifact share-link as required
    v1 features (not future work).
  - § "Thread ↔ conversation mapping": **rewrite to the connector-neutral
    `scope_key` model**. Remove "thread root = (account_id, channel_id,
    thread_root_id)"; replace with the `(account_id, channel_id, scope_key)`
    uniqueness, the `scope_key`/`scope_kind` split, and the per-platform
    mapping table from this plan's intro. The Feishu default is **chat × user
    in groups, chat in DMs**, not thread-per-message.
  - § "Per-platform specifics → Feishu": note the three-tier identity model
    and that `union_id` is the preferred `IMIdentityLink.im_user_id` AND the
    `sender_ref` that feeds `scope_key` for groups.
  - § "Identity mapping": clarify that the v1 binding-level acting user is
    used for the cubebox `RunContext`, but the IM sender id (union_id) is
    independently used for `scope_key` — these are two different facets and
    must not be conflated.
  - § "Testing strategy": add the share-link page, the artifact-image inline
    upload, the chat × user session boundary, and reactions to the manual
    smoke checklist.
  - § "Inbound idempotency": no change needed; the `IMWebhookReceipt` model
    is unaffected by the scope_key rename.
  - Add a one-line note at the top of the "Slack" subsection: "Slack
    connector ships as a follow-up plan (`docs/dev/plans/2026-05-27-im-connectors.md`,
    currently frozen). Same neutral data model + connector protocol —
    Slack's `scope_key` is `'t:<thread_ts>'` (channel threads) or `'dm'`,
    Feishu's is `'u:<union_id>'` (groups) or `'dm'`; only the connector
    adapter differs."
- [ ] Commit:

```bash
git add docs/dev/specs/2026-05-27-im-connectors-design.md
git commit -m "docs(spec): flip IM connectors v1 to Feishu first; reactions + artifacts required"
```

---

## Task 18: Full pre-PR test sweep + mypy/ruff

- [ ] **Step 1: Module sweep**

```bash
cd backend && uv run pytest \
  tests/unit/test_im_models.py \
  tests/unit/test_feishu_signature.py \
  tests/unit/test_feishu_parse_inbound.py \
  tests/unit/test_feishu_long_connection_handler.py \
  tests/unit/test_feishu_outbound_payload.py \
  tests/unit/test_im_outbound_render.py \
  tests/unit/test_im_reactions.py \
  tests/unit/test_im_artifact_dispatch.py \
  tests/integration/test_im_inbound_outbox.py \
  tests/integration/test_im_worker.py \
  tests/integration/test_artifact_share_token.py \
  tests/e2e/test_im_feishu_ingress.py \
  tests/e2e/test_im_worker_startup.py \
  tests/e2e/test_im_end_to_end.py \
  tests/e2e/test_ws_im_routes.py \
  tests/e2e/test_admin_im_routes.py \
  tests/e2e/test_im_isolation.py -v
```

- [ ] **Step 2: Whole-repo mypy + ruff** (per the project's pre-commit ordering rule):

```bash
cd backend && uv run mypy . && uv run ruff check .
```

Expected: clean. Any failure here is a hard blocker — fix and re-sweep.

- [ ] **Step 3: Commit sweep fixes**:

```bash
git add -A
git commit -m "chore(im): fix types/lint from pre-PR sweep"
```

---

## Self-Review Notes (for the implementer)

- **Phase 0 PoC is for de-risking, not shipping.** Its only durable artifact
  is the findings note. The script gets deleted. If the PoC reveals that the
  schema we assumed needs to change (e.g. union_id is missing in some
  configuration we hit), update Task 1 before continuing.
- **The `scope_key` contract is the load-bearing decision in this plan.** It
  is what makes the schema stable across Slack, Discord, Telegram, WeCom
  without future migrations. Resist any temptation to leak Slack's `thread_ts`
  or Feishu's `union_id` into a typed column — the **only** typed information
  the schema carries about session boundaries is the opaque `scope_key`
  string plus the human-readable `scope_kind` label. New connectors add a
  row to the per-platform mapping table in the design intro; they do not add
  a column.
- **Slack stays exactly where it is.** The frozen plan at
  `docs/dev/plans/2026-05-27-im-connectors.md` will be rebased onto the
  neutral data model once Feishu lands. The bulk of its non-Slack-specific
  tasks (queue worker, tailer, render fold, scope-isolated routes) are
  identical and copy across with the column-name swap (`thread_root_id` →
  `scope_key`, `reply_thread_ts` → `reply_to_id`) done in Task 1. Slack's
  connector encodes `scope_key='t:<thread_ts>'` (channel threads) or `'dm'`
  (DMs) — no schema migration needed.
- **Long-connection vs webhook in one adapter.** Both modes feed the **same**
  `ingest_inbound_event` entry. The only platform-specific code that knows
  the difference is the startup glue (Task 13). That keeps the test surface
  shared.
- **Reactions are part of v1 per user direction.** Not optional, not "nice to
  have" — Task 10 must ship. The prior-art UX at hermes
  (`gateway/platforms/feishu.py:2965`) is well-validated; copy the exact
  reaction lifecycle.
- **Artifact share page is the only new public-internet route in this plan.**
  It runs without `require_member`; the nonce in Redis IS the auth. TTL 7d.
  Be conservative — if a token leaks, it leaks one artifact for one week, not
  the workspace.
- **Open questions deferred to follow-ups (not blockers for v1):**
  - Feishu interactive cards (button approvals — hermes has this; out of v1).
  - Native streaming via Feishu Card v2 (we use debounced edits in v1).
  - Inbound file/voice/image upload (we accept text only in v1).
  - Verified per-user identity linking (`/link` flow); v1 uses binding-level
    `acting_user_id` only.
  - Frontend workspace/admin IM config pages (separate Next.js PR).
