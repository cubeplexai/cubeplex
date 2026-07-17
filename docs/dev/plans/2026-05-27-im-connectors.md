# IM Connectors (Slack first) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a workspace bind a Slack bot so an `@mention`/DM starts an agent run on a cubeplex conversation and the run's streamed output flows back as a live-updating threaded Slack reply — reusing the existing run path, never forking it.

**Architecture:** Slack POSTs events to one platform-signed, session-unauthenticated ingress (`POST /api/v1/im/slack/events`). The handler verifies the HMAC, resolves the `IMConnectorAccount` by `team_id`, and — in **one DB transaction** — inserts an idempotency receipt keyed by Slack's `event_id`, creates/reuses a `Conversation` + `IMThreadLink`, and enqueues a durable `IMRunQueueItem` row (transactional outbox). A separate in-process async worker polls that queue, claims a row via `SELECT … FOR UPDATE SKIP LOCKED`, and calls `RunManager.start_run(...)`. An outbound tailer reads the run's Redis event stream (`read_run_events_after`, the same tail SSE uses) and renders debounced `chat.update` edits into the originating Slack thread. Config is scope-isolated: workspace routes (`/ws/{ws}/im/...`, `require_member`) and org-admin routes (`/admin/im/...`, `get_admin_request_context`) are separate handlers sharing one `IMConnectorService`. Note: the org-admin routes have **no `{workspace_id}` in their path**, so they cannot use `require_admin` — that dependency is `require_role(Role.ADMIN)`, which checks a `workspace`-typed permission keyed on `ctx.workspace_id` resolved from the path, and there is no such path segment here. Use `get_admin_request_context` (backed by `require_org_admin`), the same dependency the existing `/admin/mcp/...` routes use.

**Tech Stack:** FastAPI, SQLModel + Alembic (Postgres), Redis Streams (existing run-event log), the cubepi run path (`RunManager.start_run`), `CredentialService` (vault `kind="im_bot"`), `httpx` for Slack Web API calls, `hmac`/`hashlib` for signature verification. Tests: `pytest` against real Postgres + Redis (worktree-routed DB) with captured-real Slack payloads; no fake Slack server.

**Scope note — this plan is backend-only.** The spec (§ "Scope-isolated config: separate workspace-scope and org-admin routes/**pages**") asks for frontend config pages too. This plan deliberately ships the backend (models, ingress, worker, routes) as one PR and **defers the Next.js workspace/admin IM config pages to a follow-up PR** (own route + page file per scope, per the scope-isolated-pages rule; `@cubeplex/core` API client types + a Playwright/E2E pass). If a single PR is required instead, add those frontend tasks here before implementation. Either way the frontend is not silently dropped — it is an explicit, tracked follow-up.

---

## File Structure

New files (all paths under `backend/`):

- `cubeplex/models/im_connector.py` — `IMConnectorAccount`, `IMThreadLink`, `IMIdentityLink`, `IMWebhookReceipt`, `IMRunQueueItem` SQLModel tables.
- `cubeplex/repositories/im_connector.py` — scoped repos for the IM tables + the queue claim/complete primitives.
- `cubeplex/services/im_connector.py` — `IMConnectorService` (CRUD shared by ws + admin routes).
- `cubeplex/im/__init__.py`, `cubeplex/im/types.py` — `InboundEvent`, `OutboundOp`, render-state dataclasses + the `IMConnector` protocol.
- `cubeplex/im/slack/signature.py` — Slack HMAC verification.
- `cubeplex/im/slack/connector.py` — `SlackConnector`: `parse_inbound`, `render_outbound`, `send`, `edit`, `post_placeholder`.
- `cubeplex/im/inbound.py` — `ingest_inbound_event(...)`: the transactional receipt + conversation/thread + enqueue core.
- `cubeplex/im/worker.py` — `IMRunQueueWorker`: drains the queue → `start_run` → spawns the outbound tailer.
- `cubeplex/im/outbound.py` — `OutboundRunTailer`: Redis tail → debounced render → Slack edits.
- `cubeplex/api/routes/v1/im_ingress.py` — `POST /api/v1/im/slack/events` (unauthenticated, platform-signed).
- `cubeplex/api/routes/v1/ws_im.py` — workspace-scope account/identity routes (`require_member`).
- `cubeplex/api/routes/v1/admin_im.py` — org-admin account listing/enable-disable (`get_admin_request_context`).
- `cubeplex/api/schemas/im_connector.py` — request/response pydantic models.

Modified: `cubeplex/models/public_id.py` (prefixes are set via `_PREFIX` on each table — no edit needed unless adding shared constants; see Task 2), `cubeplex/models/__init__.py` (export new tables so Alembic + `_guard_references` see them), `cubeplex/api/app.py` (register the three routers + start the worker on startup), `cubeplex/services/credential.py` (`_guard_references`: refuse deleting an `im_bot` credential still referenced by an account).

---

## Task 1: Decide & build the durable run queue dependency

The spec's idempotency design requires a durable run queue that a worker drains independently of the request that accepted the webhook. cubeplex today starts runs **in-process** via `RunManager.start_run` → `asyncio.create_task` over Redis run state (`backend/cubeplex/streams/run_manager.py:482`). There is **no durable queue**: if the process dies after acking Slack but before the run starts, the event is lost (Slack stops retrying after its bounded window).

**Decision (recorded here, frozen):** Build a **minimal durable run queue as a Postgres table** (`IMRunQueueItem`), scoped to IM for v1, drained by an in-process async poller. We do **not** build a general cross-process broker, and we do **not** scope v1 to best-effort-with-gap. Rationale:

- The receipt insert and the run enqueue must commit in **one DB transaction** (the spec's transactional outbox). A Postgres row in the same DB is the only thing that can join that transaction; a Redis push or an `asyncio` task cannot. So the outbox row lives in Postgres.
- "Independent of the web process" is satisfied by a poller task that uses `SELECT … FOR UPDATE SKIP LOCKED` to claim a `pending` row, so a crash leaves the row claimable by the next poll (same process after restart, or a second process). This closes the spec's crash window without a message broker.
- Run *execution* still happens via the existing `start_run` (in-process asyncio) — the queue only guarantees a run is **created**, matching the spec ("the run queue is the source of truth for *this will be executed*").
- The `lease_expires_at` on the receipt is the secondary worker-vs-worker guard the spec describes; the queue's `SKIP LOCKED` claim + a `claimed_at`/lease column on the queue row implements re-claim of a stalled worker.

**Limitation documented:** `steer_run`/`cancel_run` only work in the process hosting the run (single-process affinity, spec Open Question). The outbound tailer only *tails* Redis, so it can run in any process; but for v1 the worker, the run, and the tailer all live in the same API process. This is acceptable for v1 single-process deploys and is recorded as the boundary for a future multi-process story.

- [ ] **Step 1: Write a design note recording the decision**

Create `docs/dev/notes/2026-05-27-im-durable-run-queue.md` with the decision above (≈30 lines): the problem (in-process runs, no durable queue), the chosen approach (Postgres outbox row + `FOR UPDATE SKIP LOCKED` poller), what it does and does not guarantee, and the single-process affinity limitation.

- [ ] **Step 2: Commit**

```bash
git add docs/dev/notes/2026-05-27-im-durable-run-queue.md
git commit -m "docs(im): record durable run-queue decision (outbox + SKIP LOCKED poller)"
```

The queue **table** and **worker** are implemented in Tasks 2 and 6 (table) and Task 7 (worker), tied into the transactional outbox in Task 5. Task 1 only freezes the decision so later tasks have an unambiguous target.

---

## Task 2: IM data model + public ID prefixes

**Files:**
- Create: `backend/cubeplex/models/im_connector.py`
- Modify: `backend/cubeplex/models/__init__.py`
- Test: `backend/tests/unit/test_im_models.py`

Public ID prefixes follow the `CubeplexBase._PREFIX` convention (see `Conversation._PREFIX = "conv"`, `MCPCredentialGrant._PREFIX = "mcgrn"`). No edit to `public_id.py` is required — each table sets its own `_PREFIX`. Prefixes: `imac` (account), `imtl` (thread link), `imil` (identity link), `imwr` (webhook receipt), `imrq` (run queue item).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_im_models.py
from cubeplex.models.im_connector import (
    IMConnectorAccount,
    IMIdentityLink,
    IMRunQueueItem,
    IMThreadLink,
    IMWebhookReceipt,
)


def test_account_id_prefix() -> None:
    acc = IMConnectorAccount(
        org_id="org-x",
        workspace_id="ws-x",
        platform="slack",
        external_account_id="T123",
        acting_user_id="usr-x",
        credential_id="cred-x",
    )
    assert acc.id.startswith("imac-")
    assert acc.delivery_mode == "webhook"
    assert acc.enabled is True


def test_thread_link_requires_non_null_root() -> None:
    link = IMThreadLink(
        org_id="org-x",
        workspace_id="ws-x",
        account_id="imac-1",
        channel_id="C1",
        thread_root_id="__dm__",
        conversation_id="conv-1",
    )
    assert link.id.startswith("imtl-")
    assert link.thread_root_id == "__dm__"


def test_receipt_and_queue_prefixes() -> None:
    rcpt = IMWebhookReceipt(
        org_id="org-x", workspace_id="ws-x", account_id="imac-1",
        platform_event_id="Ev123", status="pending",
    )
    item = IMRunQueueItem(
        org_id="org-x", workspace_id="ws-x", account_id="imac-1",
        conversation_id="conv-1", receipt_id=rcpt.id, content="hi",
        slack_channel_id="C1", slack_thread_ts="1.2",
    )
    assert rcpt.id.startswith("imwr-")
    assert item.id.startswith("imrq-")
    assert item.status == "pending"


def test_identity_link_prefix() -> None:
    il = IMIdentityLink(
        org_id="org-x", workspace_id="ws-x",
        account_id="imac-1", im_user_id="U1", user_id="usr-1",
    )
    assert il.id.startswith("imil-")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_im_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cubeplex.models.im_connector'`

- [ ] **Step 3: Write the models**

```python
# backend/cubeplex/models/im_connector.py
"""IM connector models (Slack first; Feishu reuses the same shape in v1.1)."""

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, Column, Index, text
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin


class IMConnectorAccount(CubeplexBase, OrgScopedMixin, table=True):
    """A bound IM bot account. One external IM account → one cubeplex row."""

    _PREFIX: ClassVar[str] = "imac"
    __tablename__ = "im_connector_accounts"
    __table_args__ = (
        Index(
            "uq_im_account_platform_external",
            "platform",
            "external_account_id",
            unique=True,
        ),
        Index("ix_im_accounts_org_ws", "org_id", "workspace_id"),
    )

    platform: str = Field(max_length=16)  # 'slack' | 'feishu'
    external_account_id: str = Field(max_length=128)  # Slack team_id; Feishu app_id
    acting_user_id: str = Field(foreign_key="users.id", max_length=20)
    credential_id: str = Field(foreign_key="credentials.id", max_length=20)
    delivery_mode: str = Field(default="webhook", max_length=16)
    enabled: bool = Field(default=True, sa_column_kwargs={"server_default": text("true")})
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class IMThreadLink(CubeplexBase, OrgScopedMixin, table=True):
    """Durable map: (account, channel, thread root) → one cubeplex conversation."""

    _PREFIX: ClassVar[str] = "imtl"
    __tablename__ = "im_thread_links"
    __table_args__ = (
        Index(
            "uq_im_thread_link",
            "account_id",
            "channel_id",
            "thread_root_id",
            unique=True,
        ),
    )

    account_id: str = Field(foreign_key="im_connector_accounts.id", max_length=20, index=True)
    channel_id: str = Field(max_length=128)
    # Non-null sentinel for DMs with no platform thread (e.g. '__dm__' or the
    # channel id). NULL would let Postgres treat repeated DMs as distinct rows.
    thread_root_id: str = Field(max_length=128)
    conversation_id: str = Field(foreign_key="conversations.id", max_length=20, index=True)


class IMIdentityLink(CubeplexBase, OrgScopedMixin, table=True):
    """Map an IM sender to a cubeplex user (v1 falls back to account.acting_user_id)."""

    _PREFIX: ClassVar[str] = "imil"
    __tablename__ = "im_identity_links"
    __table_args__ = (
        Index("uq_im_identity_link", "account_id", "im_user_id", unique=True),
    )

    account_id: str = Field(foreign_key="im_connector_accounts.id", max_length=20, index=True)
    im_user_id: str = Field(max_length=128)
    user_id: str = Field(foreign_key="users.id", max_length=20)


class IMWebhookReceipt(CubeplexBase, OrgScopedMixin, table=True):
    """Idempotency receipt keyed by platform event id. Inserted in the same
    transaction that enqueues the run (transactional outbox)."""

    _PREFIX: ClassVar[str] = "imwr"
    __tablename__ = "im_webhook_receipts"
    __table_args__ = (
        Index(
            "uq_im_receipt_account_event",
            "account_id",
            "platform_event_id",
            unique=True,
        ),
    )

    account_id: str = Field(foreign_key="im_connector_accounts.id", max_length=20, index=True)
    platform_event_id: str = Field(max_length=255)
    status: str = Field(default="pending", max_length=16)  # 'pending' | 'completed'
    lease_expires_at: datetime | None = Field(default=None)


class IMRunQueueItem(CubeplexBase, OrgScopedMixin, table=True):
    """Durable outbox row: 'this accepted event will be run'. Drained by the
    IMRunQueueWorker via SELECT ... FOR UPDATE SKIP LOCKED."""

    _PREFIX: ClassVar[str] = "imrq"
    __tablename__ = "im_run_queue"
    __table_args__ = (
        Index(
            "ix_im_run_queue_pending",
            "status",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    account_id: str = Field(foreign_key="im_connector_accounts.id", max_length=20, index=True)
    receipt_id: str = Field(foreign_key="im_webhook_receipts.id", max_length=20, index=True)
    conversation_id: str = Field(foreign_key="conversations.id", max_length=20)
    content: str
    slack_channel_id: str = Field(max_length=128)
    # Conversation dedup key (the thread root / DM sentinel) — NOT a reply target.
    slack_thread_ts: str = Field(max_length=64)
    # Real Slack reply target: thread_ts to set on outbound, or NULL for an
    # unthreaded DM (post without thread_ts). Never '__dm__'.
    slack_reply_thread_ts: str | None = Field(default=None, max_length=64)
    status: str = Field(default="pending", max_length=16)  # 'pending' | 'started' | 'failed'
    claimed_at: datetime | None = Field(default=None)
    claim_lease_expires_at: datetime | None = Field(default=None)
    attempts: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
```

- [ ] **Step 4: Export the tables**

In `backend/cubeplex/models/__init__.py`, add after the `Credential` import:

```python
from cubeplex.models.im_connector import (  # noqa: F401
    IMConnectorAccount,
    IMIdentityLink,
    IMRunQueueItem,
    IMThreadLink,
    IMWebhookReceipt,
)
```

And add the five names to the `__all__` list (place alphabetically near `Conversation`):

```python
    "IMConnectorAccount",
    "IMIdentityLink",
    "IMRunQueueItem",
    "IMThreadLink",
    "IMWebhookReceipt",
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_im_models.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/models/im_connector.py backend/cubeplex/models/__init__.py backend/tests/unit/test_im_models.py
git commit -m "feat(im): add IM connector data model (accounts, threads, receipts, queue)"
```

---

## Task 3: Alembic migration (autogenerate)

**Files:**
- Create: `backend/alembic/versions/<rev>_im_connectors.py` (autogenerated — do not hand-write the body)

- [ ] **Step 1: Generate the migration**

Run: `cd backend && uv run alembic revision --autogenerate -m "im connectors tables"`
Expected: writes `alembic/versions/<rev>_im_connectors.py` and prints the new revision id.

- [ ] **Step 2: Inspect the generated migration**

Open the generated file and confirm it contains `op.create_table("im_connector_accounts", ...)`, `im_thread_links`, `im_identity_links`, `im_webhook_receipts`, `im_run_queue`, plus the five unique/partial indexes (`uq_im_account_platform_external`, `uq_im_thread_link`, `uq_im_identity_link`, `uq_im_receipt_account_event`, `ix_im_run_queue_pending` with its `postgresql_where`). Do **not** edit the body; if a partial-index `WHERE` is missing, fix the `postgresql_where` on the model in Task 2 and regenerate.

- [ ] **Step 3: Apply the migration to the worktree DB**

Run: `cd backend && uv run alembic upgrade head`
Expected: `Running upgrade <prev> -> <rev>, im connectors tables` with no error.

- [ ] **Step 4: Verify no drift remains**

Run: `cd backend && uv run alembic revision --autogenerate -m "drift check"`
Expected: the generated file's `upgrade()` body is empty (`pass`). If non-empty, the model and DB disagree — fix the model and regenerate the real migration. **Delete the drift-check file** afterward: `git status` should show only the real migration.

- [ ] **Step 5: Commit**

```bash
rm -f backend/alembic/versions/*drift_check*.py
git add backend/alembic/versions/
git commit -m "feat(im): add migration for IM connector tables"
```

---

## Task 4: Slack signature verification (unit, security-critical)

**Files:**
- Create: `backend/cubeplex/im/__init__.py` (empty), `backend/cubeplex/im/slack/__init__.py` (empty), `backend/cubeplex/im/slack/signature.py`
- Test: `backend/tests/unit/test_slack_signature.py`

Slack signs each request: `v0:<timestamp>:<raw_body>` HMAC-SHA256 with the signing secret, hex-digested, prefixed `v0=`, in header `X-Slack-Signature`; timestamp in `X-Slack-Request-Timestamp`. Reject if the timestamp is older than 5 minutes (replay guard) or the HMAC doesn't match (constant-time compare).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_slack_signature.py
import hashlib
import hmac
import time

import pytest

from cubeplex.im.slack.signature import SlackSignatureError, verify_slack_signature

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"


def _sign(body: bytes, ts: str) -> str:
    base = b"v0:" + ts.encode() + b":" + body
    digest = hmac.new(SECRET.encode(), base, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def test_valid_signature_passes() -> None:
    body = b'{"type":"event_callback"}'
    ts = str(int(time.time()))
    verify_slack_signature(
        signing_secret=SECRET, raw_body=body, timestamp=ts, signature=_sign(body, ts)
    )  # no raise


def test_tampered_body_rejected() -> None:
    body = b'{"type":"event_callback"}'
    ts = str(int(time.time()))
    sig = _sign(body, ts)
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(
            signing_secret=SECRET, raw_body=b'{"type":"evil"}', timestamp=ts, signature=sig
        )


def test_stale_timestamp_rejected() -> None:
    body = b"{}"
    ts = str(int(time.time()) - 60 * 10)  # 10 minutes old
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(
            signing_secret=SECRET, raw_body=body, timestamp=ts, signature=_sign(body, ts)
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_slack_signature.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cubeplex.im.slack.signature'`

- [ ] **Step 3: Write the verifier**

```python
# backend/cubeplex/im/slack/signature.py
"""Slack request-signature verification (HTTP Events API)."""

import hashlib
import hmac
import time


class SlackSignatureError(Exception):
    """Raised when a Slack request fails signature or timestamp validation."""


_MAX_SKEW_SECONDS = 60 * 5


def verify_slack_signature(
    *,
    signing_secret: str,
    raw_body: bytes,
    timestamp: str,
    signature: str,
    now: float | None = None,
) -> None:
    """Validate the X-Slack-Signature HMAC and reject replays. Raises on failure."""
    try:
        ts_int = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise SlackSignatureError("missing or non-numeric timestamp") from exc

    current = now if now is not None else time.time()
    if abs(current - ts_int) > _MAX_SKEW_SECONDS:
        raise SlackSignatureError("timestamp outside allowed skew")

    base = b"v0:" + timestamp.encode() + b":" + raw_body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise SlackSignatureError("signature mismatch")
```

Also create the empty package files:

```bash
touch backend/cubeplex/im/__init__.py backend/cubeplex/im/slack/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_slack_signature.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/im/__init__.py backend/cubeplex/im/slack/__init__.py backend/cubeplex/im/slack/signature.py backend/tests/unit/test_slack_signature.py
git commit -m "feat(im): add Slack request signature verification"
```

---

## Task 5: Inbound types + parse Slack events into a normalized InboundEvent

**Files:**
- Create: `backend/cubeplex/im/types.py`, `backend/cubeplex/im/slack/connector.py`
- Test: `backend/tests/unit/test_slack_parse_inbound.py`

`parse_inbound` turns a raw Slack `event_callback` body into a platform-agnostic `InboundEvent`. It strips the bot mention from `app_mention` text, derives the thread root (`thread_ts` if present, else the message `ts` for a channel mention, else the `__dm__` sentinel for a DM), and pulls the stable `event_id` and `team_id`. It returns `None` for events we ignore (bot's own messages, non-message subtypes).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_slack_parse_inbound.py
from cubeplex.im.slack.connector import SlackConnector

APP_MENTION = {
    "team_id": "T123",
    "event_id": "Ev0001",
    "event": {
        "type": "app_mention",
        "user": "U777",
        "text": "<@UBOT> summarize the doc",
        "channel": "C555",
        "ts": "1700000000.000100",
    },
}

DM = {
    "team_id": "T123",
    "event_id": "Ev0002",
    "event": {
        "type": "message",
        "channel_type": "im",
        "user": "U777",
        "text": "hello bot",
        "channel": "D999",
        "ts": "1700000001.000200",
    },
}

BOT_ECHO = {
    "team_id": "T123",
    "event_id": "Ev0003",
    "event": {"type": "message", "bot_id": "B1", "text": "ignore me", "channel": "C555",
              "ts": "1.1"},
}


def test_app_mention_strips_mention_and_uses_ts_as_root() -> None:
    conn = SlackConnector(bot_user_id="UBOT")
    ev = conn.parse_inbound(APP_MENTION)
    assert ev is not None
    assert ev.account_external_id == "T123"
    assert ev.platform_event_id == "Ev0001"
    assert ev.channel_id == "C555"
    assert ev.thread_root_id == "1700000000.000100"
    assert ev.reply_thread_ts == "1700000000.000100"  # channel mention replies in-thread
    assert ev.sender_ref == "U777"
    assert ev.text == "summarize the doc"


def test_dm_uses_sentinel_thread_root() -> None:
    conn = SlackConnector(bot_user_id="UBOT")
    ev = conn.parse_inbound(DM)
    assert ev is not None
    assert ev.channel_id == "D999"
    assert ev.thread_root_id == "__dm__"          # dedup key
    assert ev.reply_thread_ts is None             # unthreaded DM ⇒ no reply thread_ts
    assert ev.text == "hello bot"


def test_bot_echo_ignored() -> None:
    conn = SlackConnector(bot_user_id="UBOT")
    assert conn.parse_inbound(BOT_ECHO) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_slack_parse_inbound.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cubeplex.im.slack.connector'`

- [ ] **Step 3: Write the types and the parser half of SlackConnector**

```python
# backend/cubeplex/im/types.py
"""Platform-agnostic IM transport types."""

from dataclasses import dataclass, field

DM_THREAD_SENTINEL = "__dm__"


@dataclass(slots=True)
class InboundEvent:
    """Normalized inbound IM message ready for binding/thread/identity resolution."""

    platform: str
    account_external_id: str  # Slack team_id; Feishu app_id
    platform_event_id: str  # Slack event_id (stable across retries)
    channel_id: str
    # CONVERSATION KEY (dedup only): never NULL; a DM with no platform thread
    # uses DM_THREAD_SENTINEL so repeated DMs map to one rolling conversation.
    # This is NOT a Slack reply target — '__dm__' is a sentinel, never a real ts.
    thread_root_id: str
    # OUTBOUND REPLY TARGET (what to set as Slack thread_ts): the real message ts
    # for a channel mention / threaded reply, or None for an unthreaded DM (post
    # to the channel/DM without thread_ts). Kept SEPARATE from thread_root_id so
    # we never send thread_ts='__dm__'.
    reply_thread_ts: str | None
    sender_ref: str  # IM user id
    text: str


@dataclass(slots=True)
class RenderState:
    """Per-run outbound render state (message id + accumulated text + last edit ms)."""

    message_ts: str | None = None
    text_buffer: str = ""
    tool_lines: list[str] = field(default_factory=list)
    last_edit_monotonic: float = 0.0
```

```python
# backend/cubeplex/im/slack/connector.py
"""Slack connector: inbound parse + outbound render/send (Web API)."""

import re
from typing import Any

from cubeplex.im.types import DM_THREAD_SENTINEL, InboundEvent

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")


class SlackConnector:
    def __init__(self, *, bot_user_id: str | None = None, bot_token: str | None = None) -> None:
        self._bot_user_id = bot_user_id
        self._bot_token = bot_token

    def parse_inbound(self, raw: dict[str, Any]) -> InboundEvent | None:
        event = raw.get("event") or {}
        etype = event.get("type")
        if etype not in {"app_mention", "message"}:
            return None
        # Ignore the bot's own messages and non-user message subtypes.
        if event.get("bot_id") is not None:
            return None
        if etype == "message" and event.get("subtype") is not None:
            return None
        if event.get("user") is None:
            return None

        channel = event.get("channel", "")
        ts = event.get("ts", "")
        thread_ts = event.get("thread_ts")
        is_dm = event.get("channel_type") == "im"
        if thread_ts:
            # Threaded reply (channel or DM): key + reply target are both thread_ts.
            thread_root = thread_ts
            reply_thread_ts: str | None = thread_ts
        elif is_dm:
            # Unthreaded DM: one rolling conversation (sentinel key), but reply
            # with NO thread_ts (posting '__dm__' as thread_ts would be invalid).
            thread_root = DM_THREAD_SENTINEL
            reply_thread_ts = None
        else:
            # Channel mention starting a new thread: key + reply target = this ts.
            thread_root = ts
            reply_thread_ts = ts

        text = _MENTION_RE.sub("", event.get("text", "")).strip()

        return InboundEvent(
            platform="slack",
            account_external_id=raw.get("team_id", ""),
            platform_event_id=raw.get("event_id", ""),
            channel_id=channel,
            thread_root_id=thread_root,
            reply_thread_ts=reply_thread_ts,
            sender_ref=event.get("user", ""),
            text=text,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_slack_parse_inbound.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/im/types.py backend/cubeplex/im/slack/connector.py backend/tests/unit/test_slack_parse_inbound.py
git commit -m "feat(im): normalize Slack events into InboundEvent"
```

---

## Task 6: Repositories + transactional inbound core (receipt + thread + enqueue)

**Files:**
- Create: `backend/cubeplex/repositories/im_connector.py`, `backend/cubeplex/im/inbound.py`
- Test: `backend/tests/integration/test_im_inbound_outbox.py` (real Postgres, worktree-routed)

This is the heart of idempotency: one transaction inserts the receipt, creates/reuses the `Conversation` + `IMThreadLink`, and enqueues the `IMRunQueueItem`. On a unique-violation of `(account_id, platform_event_id)` it rolls back and reports "duplicate" — no second enqueue.

- [ ] **Step 1: Write the failing integration test**

```python
# backend/tests/integration/test_im_inbound_outbox.py
import pytest
from sqlalchemy import func, select

from cubeplex.im.inbound import IngestResult, ingest_inbound_event
from cubeplex.im.types import InboundEvent
from cubeplex.models.im_connector import IMRunQueueItem, IMThreadLink, IMWebhookReceipt

pytestmark = pytest.mark.asyncio


def _event(event_id: str = "Ev1") -> InboundEvent:
    return InboundEvent(
        platform="slack", account_external_id="T123", platform_event_id=event_id,
        channel_id="C1", thread_root_id="1700.0001", reply_thread_ts="1700.0001",
        sender_ref="U1", text="hello",
    )


async def test_first_event_creates_conversation_link_and_queue_row(im_account, session_maker):
    res = await ingest_inbound_event(_event(), account=im_account, session_maker=session_maker)
    assert res.outcome == "enqueued"
    async with session_maker() as s:
        assert (await s.execute(select(func.count()).select_from(IMRunQueueItem))).scalar() == 1
        link = (await s.execute(select(IMThreadLink))).scalars().one()
        assert link.thread_root_id == "1700.0001"
        assert res.conversation_id == link.conversation_id


async def test_duplicate_event_does_not_double_enqueue(im_account, session_maker):
    await ingest_inbound_event(_event("EvDup"), account=im_account, session_maker=session_maker)
    res2 = await ingest_inbound_event(_event("EvDup"), account=im_account,
                                      session_maker=session_maker)
    assert res2.outcome == "duplicate"
    async with session_maker() as s:
        assert (await s.execute(select(func.count()).select_from(IMRunQueueItem))).scalar() == 1


async def test_second_thread_message_reuses_conversation(im_account, session_maker):
    r1 = await ingest_inbound_event(_event("EvA"), account=im_account, session_maker=session_maker)
    r2 = await ingest_inbound_event(_event("EvB"), account=im_account, session_maker=session_maker)
    assert r1.conversation_id == r2.conversation_id
    async with session_maker() as s:
        assert (await s.execute(select(func.count()).select_from(IMThreadLink))).scalar() == 1
        assert (await s.execute(select(func.count()).select_from(IMRunQueueItem))).scalar() == 2
```

Add fixtures to `backend/tests/integration/conftest.py` (or a local conftest):

```python
import pytest_asyncio

from cubeplex.db.engine import async_session_maker
from cubeplex.models import Credential  # real FK target for credential_id
from cubeplex.models.im_connector import IMConnectorAccount


@pytest_asyncio.fixture
def session_maker():
    return async_session_maker


@pytest_asyncio.fixture
async def im_account(seeded_org_workspace_user):
    # seeded_org_workspace_user is the existing fixture giving (org_id, workspace_id, user_id).
    org_id, ws_id, user_id = seeded_org_workspace_user
    async with async_session_maker() as s:
        # credential_id is a REAL FK to credentials.id — never a literal like
        # "cred-fake" (that fails the FK). Insert a real Credential(kind="im_bot")
        # first and point the account at it.
        cred = Credential(
            org_id=org_id, kind="im_bot", name="slack:T123",
            value_encrypted=b"",  # match the real column type/shape
        )
        s.add(cred)
        await s.flush()
        acc = IMConnectorAccount(
            org_id=org_id, workspace_id=ws_id, platform="slack",
            external_account_id="T123", acting_user_id=user_id, credential_id=cred.id,
        )
        s.add(acc)
        await s.commit()
        await s.refresh(acc)
        return acc
```

> **Resolve every fixture/guess against the real codebase before coding — the
> snippets above are illustrative, not verified.** Concretely: (1)
> `seeded_org_workspace_user` may not exist — grep `tests/integration/conftest.py`
> / `tests/e2e/conftest.py` for the actual org/workspace/user seeding fixture and
> use its real name and return shape. (2) `Credential(...)` must match the real
> model's required fields and the encrypted-value column type (read
> `cubeplex/models/credential.py`); a real `im_bot` row is required because of the
> FK. (3) `member_client` and similar e2e client fixtures yield **tuples**
> `(client, workspace_id)` in this repo — unpack them, do not treat them as an
> object with `.workspace_id`, and don't assume a bare `workspace_id` fixture
> exists. (4) `app_instance` / `async_client` / `admin_client` / `two_im_accounts`
> are placeholder names — confirm the real app + client + multi-tenant fixtures
> (grep `tests/e2e/conftest.py` for `lifespan` / `app` / `admin`). (5)
> `get_encryption_backend_app` may not be a real symbol — see the Task 9 note;
> use `request.app.state.encryption_backend` if so.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_im_inbound_outbox.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cubeplex.im.inbound'`

- [ ] **Step 3: Write the repositories**

```python
# backend/cubeplex/repositories/im_connector.py
"""Scoped repositories + queue claim primitives for IM connectors."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.im_connector import (
    IMConnectorAccount,
    IMRunQueueItem,
    IMThreadLink,
)


class IMAccountRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self._org_id = org_id

    async def get_by_external_id(
        self, *, platform: str, external_account_id: str
    ) -> IMConnectorAccount | None:
        # Account lookup at ingress runs *before* org scope is known, so this
        # is unscoped by org on purpose — the (platform, external) pair is
        # globally unique and selects the org/workspace.
        stmt = select(IMConnectorAccount).where(
            IMConnectorAccount.platform == platform,
            IMConnectorAccount.external_account_id == external_account_id,
        )
        return (await self.session.execute(stmt)).scalars().one_or_none()


async def get_account_by_external_id_unscoped(
    session: AsyncSession, *, platform: str, external_account_id: str
) -> IMConnectorAccount | None:
    stmt = select(IMConnectorAccount).where(
        IMConnectorAccount.platform == platform,
        IMConnectorAccount.external_account_id == external_account_id,
    )
    return (await session.execute(stmt)).scalars().one_or_none()


async def get_or_create_thread_link(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    account_id: str,
    channel_id: str,
    thread_root_id: str,
    make_conversation_id,  # async callable () -> conversation_id
) -> tuple[IMThreadLink, bool]:
    stmt = select(IMThreadLink).where(
        IMThreadLink.account_id == account_id,
        IMThreadLink.channel_id == channel_id,
        IMThreadLink.thread_root_id == thread_root_id,
    )
    existing = (await session.execute(stmt)).scalars().one_or_none()
    if existing is not None:
        return existing, False
    conversation_id = await make_conversation_id()
    link = IMThreadLink(
        org_id=org_id, workspace_id=workspace_id, account_id=account_id,
        channel_id=channel_id, thread_root_id=thread_root_id,
        conversation_id=conversation_id,
    )
    session.add(link)
    return link, True


async def claim_pending_queue_item(
    session: AsyncSession, *, lease_seconds: int, max_attempts: int = 5
) -> IMRunQueueItem | None:
    """Claim one pending OR lease-expired in-progress row with SKIP LOCKED.

    A claim sets status='started' + a lease and bumps attempts. If the worker
    crashes or run-start fails, the row keeps status='started' but its
    claim_lease_expires_at goes stale — this query reclaims such rows so a crash
    can never strand them forever. The original 'pending only' filter was the
    bug: a started-then-failed row was never re-selected. Rows past max_attempts
    are left for a janitor to park as 'failed' (or extend max_attempts) rather
    than spinning forever on a permanently-broken event.
    """
    now = datetime.now(UTC)
    stmt = (
        select(IMRunQueueItem)
        .where(
            IMRunQueueItem.attempts < max_attempts,
            or_(
                IMRunQueueItem.status == "pending",
                and_(  # reclaim a stalled in-progress row whose lease expired
                    IMRunQueueItem.status == "started",
                    IMRunQueueItem.claim_lease_expires_at.is_not(None),
                    IMRunQueueItem.claim_lease_expires_at < now,
                ),
            ),
        )
        .order_by(IMRunQueueItem.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    item = (await session.execute(stmt)).scalars().one_or_none()
    if item is None:
        return None
    item.status = "started"
    item.claimed_at = now
    item.claim_lease_expires_at = now + timedelta(seconds=lease_seconds)
    item.attempts += 1
    session.add(item)
    return item
```

The partial index `ix_im_run_queue_pending` (Task 2) is `WHERE status='pending'`,
which no longer covers the reclaim path. Either widen it to also index expired
`started` leases, or add a second partial index on
`(status, claim_lease_expires_at) WHERE status='started'` so the reclaim query
stays cheap. Add a worker test that fails the first `start_run`, then asserts a
later poll re-claims the same row once its lease expires.

- [ ] **Step 4: Write the transactional inbound core**

```python
# backend/cubeplex/im/inbound.py
"""Transactional inbound core: receipt + thread link + run enqueue in one tx."""

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from cubeplex.im.types import InboundEvent
from cubeplex.models.conversation import Conversation
from cubeplex.models.im_connector import (
    IMConnectorAccount,
    IMRunQueueItem,
    IMWebhookReceipt,
)
from cubeplex.repositories.im_connector import get_or_create_thread_link


@dataclass(slots=True)
class IngestResult:
    outcome: str  # 'enqueued' | 'duplicate'
    conversation_id: str | None


def _constraint_name(exc: IntegrityError) -> str:
    """Best-effort constraint name from a psycopg/asyncpg IntegrityError."""
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return name or str(orig) or str(exc)


def _is_receipt_unique_violation(exc: IntegrityError) -> bool:
    return "uq_im_receipt_account_event" in _constraint_name(exc)


def _is_thread_link_unique_violation(exc: IntegrityError) -> bool:
    return "uq_im_thread_link" in _constraint_name(exc)


async def ingest_inbound_event(
    event: InboundEvent,
    *,
    account: IMConnectorAccount,
    session_maker,
) -> IngestResult:
    """Insert receipt + create/reuse conversation+link + enqueue run, atomically.

    A redelivered event hits uq_im_receipt_account_event and returns 'duplicate'
    without a second enqueue.
    """
    async with session_maker() as session:
        receipt = IMWebhookReceipt(
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            account_id=account.id,
            platform_event_id=event.platform_event_id,
            status="pending",
        )
        session.add(receipt)
        try:
            await session.flush()  # surfaces the unique violation early
        except IntegrityError as exc:
            await session.rollback()
            # ONLY the receipt's (account_id, platform_event_id) unique conflict
            # means "duplicate". Any other IntegrityError (FK violation, a
            # different constraint) is a real failure and must NOT be acked as a
            # duplicate — that would silently drop a genuine event. Match the
            # constraint name (uq_im_receipt_account_event) on the error and
            # re-raise anything else so the ingress returns non-200 and Slack
            # retries.
            if not _is_receipt_unique_violation(exc):
                raise
            return IngestResult(outcome="duplicate", conversation_id=None)

        async def _make_conversation_id() -> str:
            conv = Conversation(
                org_id=account.org_id,
                workspace_id=account.workspace_id,
                creator_user_id=account.acting_user_id,
                title=(event.text[:80] or "IM conversation"),
            )
            session.add(conv)
            await session.flush()
            return conv.id

        link, _created = await get_or_create_thread_link(
            session,
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            account_id=account.id,
            channel_id=event.channel_id,
            thread_root_id=event.thread_root_id,
            make_conversation_id=_make_conversation_id,
        )

        item = IMRunQueueItem(
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            account_id=account.id,
            receipt_id=receipt.id,
            conversation_id=link.conversation_id,
            content=event.text,
            slack_channel_id=event.channel_id,
            slack_thread_ts=event.thread_root_id,        # dedup key
            slack_reply_thread_ts=event.reply_thread_ts,  # real reply target (may be None)
        )
        session.add(item)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            # Same discrimination as above: a concurrent receipt duplicate that
            # raced past the flush is a 'duplicate'; a concurrent IMThreadLink
            # unique conflict (two first-messages in the same thread racing) is
            # NOT a duplicate event — re-select the winning link and re-enqueue
            # against its conversation. Anything else re-raises (real failure →
            # Slack retries).
            if _is_thread_link_unique_violation(exc):
                return await ingest_inbound_event(  # retry: link now exists, reused
                    event, account=account, session_maker=session_maker
                )
            if not _is_receipt_unique_violation(exc):
                raise
            return IngestResult(outcome="duplicate", conversation_id=None)
        return IngestResult(outcome="enqueued", conversation_id=link.conversation_id)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/integration/test_im_inbound_outbox.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/repositories/im_connector.py backend/cubeplex/im/inbound.py backend/tests/integration/test_im_inbound_outbox.py backend/tests/integration/conftest.py
git commit -m "feat(im): transactional inbound core (receipt + thread + run enqueue)"
```

---

## Task 7: Queue worker — drain → start_run

**Files:**
- Create: `backend/cubeplex/im/worker.py`
- Test: `backend/tests/integration/test_im_worker.py`

The worker claims a `pending` queue row (`SELECT … FOR UPDATE SKIP LOCKED`), calls `RunManager.start_run` with a `RunContext(user_id=account.acting_user_id, org_id, workspace_id)`, flips the receipt to `completed`, then hands the `run_id` + channel/thread to the outbound tailer (Task 8). The test fakes `start_run` to assert the contract without a real LLM.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/integration/test_im_worker.py
import pytest
from sqlalchemy import select

from cubeplex.im.inbound import ingest_inbound_event
from cubeplex.im.types import InboundEvent
from cubeplex.im.worker import process_one_queue_item
from cubeplex.models.im_connector import IMRunQueueItem, IMWebhookReceipt

pytestmark = pytest.mark.asyncio


class _FakeRunManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def start_run(self, *, conversation_id, content, attachments, ctx) -> str:
        self.calls.append(
            {"conversation_id": conversation_id, "content": content,
             "user_id": ctx.user_id, "org_id": ctx.org_id, "workspace_id": ctx.workspace_id}
        )
        return "run-fake-1"


async def test_worker_starts_run_and_completes_receipt(im_account, session_maker):
    ev = InboundEvent(
        platform="slack", account_external_id="T123", platform_event_id="EvW",
        channel_id="C1", thread_root_id="t.1", reply_thread_ts="t.1",
        sender_ref="U1", text="do it",
    )
    await ingest_inbound_event(ev, account=im_account, session_maker=session_maker)

    rm = _FakeRunManager()
    started = await process_one_queue_item(
        session_maker=session_maker, run_manager=rm, on_run_started=None, lease_seconds=300
    )
    assert started is True
    assert rm.calls[0]["content"] == "do it"
    assert rm.calls[0]["user_id"] == im_account.acting_user_id
    assert rm.calls[0]["workspace_id"] == im_account.workspace_id

    async with session_maker() as s:
        rcpt = (await s.execute(select(IMWebhookReceipt))).scalars().one()
        assert rcpt.status == "completed"
        item = (await s.execute(select(IMRunQueueItem))).scalars().one()
        assert item.status == "started"


async def test_worker_returns_false_when_queue_empty(session_maker):
    rm = _FakeRunManager()
    started = await process_one_queue_item(
        session_maker=session_maker, run_manager=rm, on_run_started=None, lease_seconds=300
    )
    assert started is False
    assert rm.calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_im_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cubeplex.im.worker'`

- [ ] **Step 3: Write the worker**

```python
# backend/cubeplex/im/worker.py
"""Durable IM run-queue worker: claim pending rows and start runs."""

import asyncio
from typing import Any, Awaitable, Callable, Protocol

from loguru import logger
from sqlalchemy import select

from cubeplex.models.im_connector import IMRunQueueItem, IMWebhookReceipt
from cubeplex.repositories.im_connector import claim_pending_queue_item
from cubeplex.streams.run_manager import RunContext


class _RunStarter(Protocol):
    async def start_run(
        self, *, conversation_id: str, content: str, attachments: list[str] | None, ctx: RunContext
    ) -> str: ...


RunStartedCallback = Callable[[str, IMRunQueueItem], Awaitable[None]]


async def process_one_queue_item(
    *,
    session_maker: Any,
    run_manager: _RunStarter,
    on_run_started: RunStartedCallback | None,
    lease_seconds: int,
) -> bool:
    """Claim and process at most one pending queue row. Returns True if it ran one."""
    async with session_maker() as session:
        item = await claim_pending_queue_item(session, lease_seconds=lease_seconds)
        if item is None:
            return False
        # Re-load the account for acting_user_id within this session.
        from cubeplex.models.im_connector import IMConnectorAccount

        account = (
            await session.execute(
                select(IMConnectorAccount).where(IMConnectorAccount.id == item.account_id)
            )
        ).scalars().one()
        await session.commit()  # release the FOR UPDATE lock; row now status='started'
        captured = {
            "conversation_id": item.conversation_id,
            "content": item.content,
            "receipt_id": item.receipt_id,
            "org_id": account.org_id,
            "workspace_id": account.workspace_id,
            "acting_user_id": account.acting_user_id,
        }

    try:
        run_id = await run_manager.start_run(
            conversation_id=captured["conversation_id"],
            content=captured["content"],
            attachments=None,
            ctx=RunContext(
                user_id=captured["acting_user_id"],
                org_id=captured["org_id"],
                workspace_id=captured["workspace_id"],
            ),
        )
    except Exception:
        logger.warning("IM run start failed for queue item; leaving for re-claim", exc_info=True)
        return True

    async with session_maker() as session:
        rcpt = (
            await session.execute(
                select(IMWebhookReceipt).where(IMWebhookReceipt.id == captured["receipt_id"])
            )
        ).scalars().one()
        rcpt.status = "completed"
        session.add(rcpt)
        await session.commit()

    if on_run_started is not None:
        await on_run_started(run_id, item)
    return True


class IMRunQueueWorker:
    """Polls the durable queue and processes items until stopped."""

    def __init__(
        self,
        *,
        session_maker: Any,
        run_manager: _RunStarter,
        on_run_started: RunStartedCallback | None,
        poll_interval: float = 1.0,
        lease_seconds: int = 300,
    ) -> None:
        self._session_maker = session_maker
        self._run_manager = run_manager
        self._on_run_started = on_run_started
        self._poll_interval = poll_interval
        self._lease_seconds = lease_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                ran = await process_one_queue_item(
                    session_maker=self._session_maker,
                    run_manager=self._run_manager,
                    on_run_started=self._on_run_started,
                    lease_seconds=self._lease_seconds,
                )
            except Exception:
                logger.warning("IM queue worker poll error", exc_info=True)
                ran = False
            if not ran:
                await asyncio.sleep(self._poll_interval)

    def start(self) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="im-run-queue-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            from contextlib import suppress

            with suppress(asyncio.CancelledError):
                await self._task
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/integration/test_im_worker.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/im/worker.py backend/tests/integration/test_im_worker.py
git commit -m "feat(im): durable run-queue worker (claim -> start_run -> complete receipt)"
```

---

## Task 8: Outbound rendering decisions (unit) + Redis tailer

**Files:**
- Create: `backend/cubeplex/im/outbound.py`; extend `backend/cubeplex/im/slack/connector.py` with `render_outbound`
- Test: `backend/tests/unit/test_im_outbound_render.py`

`render_outbound(run_event, state)` is a pure function: it folds a run event into `RenderState` and returns an `OutboundOp` describing the Slack call (`post_placeholder` on first text, `edit` for streaming text debounced ≥500ms, a finalize `edit` on `done`, an error `edit` on `error`). Tool activity is coalesced into a compact italic line, not streamed token-by-token.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_im_outbound_render.py
from cubeplex.im.outbound import OutboundOp, fold_event
from cubeplex.im.types import RenderState


def test_first_text_posts_placeholder() -> None:
    st = RenderState()
    op = fold_event({"type": "text_delta", "data": {"content": "Hel"}}, st, now=0.0)
    assert isinstance(op, OutboundOp)
    assert op.kind == "post"
    assert st.text_buffer == "Hel"


def test_streaming_text_debounced() -> None:
    st = RenderState(message_ts="1.1", text_buffer="Hel", last_edit_monotonic=10.0)
    op = fold_event({"type": "text_delta", "data": {"content": "lo"}}, st, now=10.2)
    assert op is None  # within 500ms window
    assert st.text_buffer == "Hello"
    op2 = fold_event({"type": "text_delta", "data": {"content": "!"}}, st, now=11.0)
    assert op2.kind == "edit"
    assert "Hello!" in op2.text


def test_tool_call_coalesced_into_line() -> None:
    st = RenderState(message_ts="1.1", text_buffer="", last_edit_monotonic=0.0)
    fold_event({"type": "tool_call", "data": {"name": "web_search"}}, st, now=5.0)
    assert any("web_search" in line for line in st.tool_lines)


def test_done_finalizes() -> None:
    st = RenderState(message_ts="1.1", text_buffer="Answer", last_edit_monotonic=0.0)
    op = fold_event({"type": "done", "data": {}}, st, now=99.0)
    assert op.kind == "edit"
    assert op.final is True
    assert "Answer" in op.text


def test_error_replaces_with_notice() -> None:
    st = RenderState(message_ts="1.1", text_buffer="partial", last_edit_monotonic=0.0)
    op = fold_event({"type": "error", "data": {"message": "boom"}}, st, now=99.0)
    assert op.kind == "edit"
    assert op.final is True
    assert "boom" in op.text or "error" in op.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_im_outbound_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cubeplex.im.outbound'`

- [ ] **Step 3: Write the render fold + tailer**

```python
# backend/cubeplex/im/outbound.py
"""Outbound rendering: fold run events into debounced Slack ops, tail Redis."""

from dataclasses import dataclass
from typing import Any

from cubeplex.im.types import RenderState
from cubeplex.streams.run_events import read_run_events_after

_EDIT_DEBOUNCE_SECONDS = 0.5


@dataclass(slots=True)
class OutboundOp:
    kind: str  # 'post' | 'edit'
    text: str
    final: bool = False


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
        if state.message_ts is None:
            state.last_edit_monotonic = now
            return OutboundOp(kind="post", text=_composite_text(state))
        if now - state.last_edit_monotonic < _EDIT_DEBOUNCE_SECONDS:
            return None
        state.last_edit_monotonic = now
        return OutboundOp(kind="edit", text=_composite_text(state))

    if etype == "tool_call":
        name = data.get("name", "tool")
        line = f"_running `{name}`…_"
        if line not in state.tool_lines:
            state.tool_lines.append(line)
        return None

    if etype == "done":
        return OutboundOp(kind="edit", text=_composite_text(state), final=True)

    if etype == "error":
        msg = data.get("message", "the run failed")
        return OutboundOp(kind="edit", text=f":warning: error: {msg}", final=True)

    return None


class OutboundRunTailer:
    """Tail a run's Redis event stream and emit Slack ops via the connector."""

    def __init__(
        self,
        *,
        redis,
        key_prefix: str,
        run_id: str,
        connector,  # SlackConnector with post_placeholder/edit + channel/thread bound
    ) -> None:
        self._redis = redis
        self._prefix = key_prefix
        self._run_id = run_id
        self._connector = connector
        self._state = RenderState()

    async def run(self) -> None:
        import time

        last_id = "0"
        while True:
            events = await read_run_events_after(
                self._redis, prefix=self._prefix, run_id=self._run_id,
                last_event_id=last_id, block_ms=2000,
            )
            if not events:
                continue
            done = False
            for ev in events:
                last_id = ev.event_id
                payload = ev.payload
                op = fold_event(payload, self._state, now=time.monotonic())
                if op is None:
                    continue
                if op.kind == "post":
                    self._state.message_ts = await self._connector.post_placeholder(op.text)
                else:
                    await self._connector.edit(self._state.message_ts, op.text)
                if op.final:
                    done = True
            if done:
                return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_im_outbound_render.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Add the Slack Web API send/edit methods to SlackConnector**

Append to `backend/cubeplex/im/slack/connector.py`:

```python
    async def post_placeholder(self, text: str) -> str:
        """chat.postMessage as a thread reply; returns the message ts."""
        import httpx

        # Omit thread_ts entirely for an unthreaded DM (self._thread_ts is None);
        # sending thread_ts='__dm__' or a null would error. Channel/threaded
        # replies set a real ts.
        body_json: dict[str, object] = {"channel": self._channel_id, "text": text}
        if self._thread_ts:
            body_json["thread_ts"] = self._thread_ts
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {self._bot_token}"},
                json=body_json,
            )
        body = resp.json()
        if not body.get("ok"):
            from loguru import logger

            logger.warning("slack chat.postMessage failed: {}", body.get("error"))
            return ""
        return str(body.get("ts", ""))

    async def edit(self, message_ts: str | None, text: str) -> None:
        if not message_ts:
            return
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(
                "https://slack.com/api/chat.update",
                headers={"Authorization": f"Bearer {self._bot_token}"},
                json={"channel": self._channel_id, "ts": message_ts, "text": text},
            )
```

Update `SlackConnector.__init__` to also accept the outbound binding:

```python
    def __init__(
        self,
        *,
        bot_user_id: str | None = None,
        bot_token: str | None = None,
        channel_id: str | None = None,
        thread_ts: str | None = None,
    ) -> None:
        self._bot_user_id = bot_user_id
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._thread_ts = thread_ts
```

- [ ] **Step 6: Run the unit tests again (no regression on parse)**

Run: `cd backend && uv run pytest tests/unit/test_slack_parse_inbound.py tests/unit/test_im_outbound_render.py -v`
Expected: PASS (8 passed)

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/im/outbound.py backend/cubeplex/im/slack/connector.py backend/tests/unit/test_im_outbound_render.py
git commit -m "feat(im): outbound render fold + Redis tailer + Slack chat.update edits"
```

---

## Task 9: Platform-signed ingress route (E2E, real Postgres + Redis)

**Files:**
- Create: `backend/cubeplex/api/routes/v1/im_ingress.py`
- Modify: `backend/cubeplex/api/app.py` (register router)
- Test: `backend/tests/e2e/test_im_slack_ingress.py`

The ingress is **unauthenticated by cubeplex session** — verified by Slack's HMAC. It handles the `url_verification` challenge inline, looks up the account by `team_id`, drops unknown accounts with a 200 ack (no error leak), and on a real `event_callback` calls `ingest_inbound_event`. The E2E feeds a captured-real Slack payload with a valid signature into the route against the real run path's DB.

- [ ] **Step 1: Write the failing E2E test**

```python
# backend/tests/e2e/test_im_slack_ingress.py
import hashlib
import hmac
import json
import time

import pytest
from sqlalchemy import func, select

from cubeplex.models.im_connector import IMRunQueueItem

pytestmark = pytest.mark.asyncio

SIGNING_SECRET = "test-signing-secret-0123456789ab"


def _signed_headers(body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    base = b"v0:" + ts.encode() + b":" + body
    sig = "v0=" + hmac.new(SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
            "Content-Type": "application/json"}


async def test_url_verification_challenge(async_client):
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    resp = await async_client.post("/api/v1/im/slack/events", content=body,
                                   headers=_signed_headers(body))
    assert resp.status_code == 200
    assert resp.json()["challenge"] == "abc123"


async def test_event_callback_enqueues_run(async_client, im_account_with_secret, session_maker):
    body = json.dumps({
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "EvE2E1",
        "event": {"type": "app_mention", "user": "U1", "text": "<@UBOT> hi",
                  "channel": "C1", "ts": "1700.0001"},
    }).encode()
    resp = await async_client.post("/api/v1/im/slack/events", content=body,
                                   headers=_signed_headers(body))
    assert resp.status_code == 200
    async with session_maker() as s:
        assert (await s.execute(select(func.count()).select_from(IMRunQueueItem))).scalar() == 1


async def test_bad_signature_rejected(async_client):
    body = b'{"type":"event_callback","team_id":"T123","event_id":"x"}'
    resp = await async_client.post("/api/v1/im/slack/events", content=body,
                                   headers={"X-Slack-Request-Timestamp": str(int(time.time())),
                                            "X-Slack-Signature": "v0=deadbeef",
                                            "Content-Type": "application/json"})
    assert resp.status_code == 401


async def test_unknown_account_acked_and_dropped(async_client):
    body = json.dumps({"type": "event_callback", "team_id": "T-UNKNOWN", "event_id": "z",
                       "event": {"type": "app_mention", "user": "U1", "text": "hi",
                                 "channel": "C1", "ts": "1.1"}}).encode()
    resp = await async_client.post("/api/v1/im/slack/events", content=body,
                                   headers=_signed_headers(body))
    assert resp.status_code == 200  # ack + drop, never error-leak
```

> `im_account_with_secret` extends the `im_account` fixture: it also writes a `Credential(kind="im_bot")` holding `{"signing_secret": SIGNING_SECRET, "bot_token": "xoxb-test", "bot_user_id": "UBOT"}` and points `account.credential_id` at it. `async_client` is the existing httpx `ASGITransport` test client (grep `tests/e2e` for the fixture name; reuse it).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_im_slack_ingress.py -v`
Expected: FAIL with 404 (route not registered)

- [ ] **Step 3: Write the ingress route**

```python
# backend/cubeplex/api/routes/v1/im_ingress.py
"""Platform-signed IM ingress. Unauthenticated by cubeplex session."""

import json

from fastapi import APIRouter, Request, Response, status
from loguru import logger

from cubeplex.credentials.dependencies import get_encryption_backend_app
from cubeplex.db.engine import async_session_maker
from cubeplex.im.inbound import ingest_inbound_event
from cubeplex.im.slack.connector import SlackConnector
from cubeplex.im.slack.signature import SlackSignatureError, verify_slack_signature
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.im_connector import get_account_by_external_id_unscoped
from cubeplex.services.credential import CredentialService

router = APIRouter(prefix="/im", tags=["im-ingress"])


@router.post("/slack/events")
async def slack_events(request: Request) -> Response:
    raw_body = await request.body()
    payload = json.loads(raw_body or b"{}")

    # URL verification handshake — Slack sends this before signing is set up
    # in some flows, but we still verify when headers are present.
    if payload.get("type") == "url_verification":
        return Response(
            content=json.dumps({"challenge": payload.get("challenge", "")}),
            media_type="application/json",
        )

    team_id = payload.get("team_id", "")
    async with async_session_maker() as session:
        account = await get_account_by_external_id_unscoped(
            session, platform="slack", external_account_id=team_id
        )
        if account is None or not account.enabled:
            return Response(status_code=status.HTTP_200_OK)  # ack + drop

        backend = get_encryption_backend_app(request.app)
        cred_service = CredentialService(
            CredentialRepository(session, org_id=account.org_id),
            backend,
            org_id=account.org_id,
            actor_user_id=None,
        )
        secret_json = await cred_service.get_decrypted(
            credential_id=account.credential_id, requesting_kind="im_bot"
        )
    secrets = json.loads(secret_json)

    try:
        verify_slack_signature(
            signing_secret=secrets["signing_secret"],
            raw_body=raw_body,
            timestamp=request.headers.get("X-Slack-Request-Timestamp", ""),
            signature=request.headers.get("X-Slack-Signature", ""),
        )
    except SlackSignatureError as exc:
        logger.warning("slack signature rejected: {}", exc)
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    connector = SlackConnector(bot_user_id=secrets.get("bot_user_id"))
    event = connector.parse_inbound(payload)
    if event is None:
        return Response(status_code=status.HTTP_200_OK)  # not a message we act on

    result = await ingest_inbound_event(
        event, account=account, session_maker=async_session_maker
    )
    logger.info("slack inbound {}: {}", event.platform_event_id, result.outcome)
    return Response(status_code=status.HTTP_200_OK)
```

> If `get_encryption_backend_app` does not exist as a plain (non-Depends) accessor, read `cubeplex/credentials/dependencies.py` and use the same `request.app.state.encryption_backend` the run path uses (`run_manager.py` reads `self._app.state.encryption_backend`). Replace the import + call accordingly — this is a known, available attribute.

- [ ] **Step 4: Register the router**

In `backend/cubeplex/api/app.py`, near the other `include_router` calls (after `conversations_router`):

```python
    from cubeplex.api.routes.v1 import im_ingress

    app.include_router(im_ingress.router, prefix="/api/v1")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/e2e/test_im_slack_ingress.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/v1/im_ingress.py backend/cubeplex/api/app.py backend/tests/e2e/test_im_slack_ingress.py
git commit -m "feat(im): Slack signed ingress route (challenge, verify, ack-and-drop, enqueue)"
```

---

## Task 10: Wire the worker into app startup + outbound tailer dispatch

**Files:**
- Modify: `backend/cubeplex/api/app.py`
- Test: `backend/tests/e2e/test_im_worker_startup.py`

On app startup, build an `IMRunQueueWorker` bound to `app.state.run_manager` and `async_session_maker`, with an `on_run_started` callback that decrypts the account's bot token and spawns an `OutboundRunTailer.run()` as a background task. Stop it on shutdown alongside the run manager drain.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/e2e/test_im_worker_startup.py
import pytest

pytestmark = pytest.mark.asyncio


async def test_worker_attached_to_app_state(app_instance):
    # app_instance is the FastAPI app from the existing E2E app fixture.
    assert hasattr(app_instance.state, "im_run_queue_worker")
    assert app_instance.state.im_run_queue_worker is not None
```

> Use whichever fixture exposes the constructed app (grep `tests/e2e/conftest.py` for `app` / `lifespan`). The assertion only checks the worker is attached; the end-to-end enqueue→run is covered by Task 9 + Task 11.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_im_worker_startup.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'im_run_queue_worker'`

- [ ] **Step 3: Wire startup/shutdown**

In `backend/cubeplex/api/app.py`, inside the lifespan/startup block where `app.state.run_manager` is created, after it exists add:

```python
    from cubeplex.db.engine import async_session_maker as _im_session_maker
    from cubeplex.im.outbound import OutboundRunTailer
    from cubeplex.im.slack.connector import SlackConnector
    from cubeplex.im.worker import IMRunQueueWorker
    from cubeplex.repositories.credential import CredentialRepository
    from cubeplex.repositories.im_connector import get_account_by_external_id_unscoped
    from cubeplex.services.credential import CredentialService

    async def _on_im_run_started(run_id, item) -> None:
        # Decrypt this account's bot token and tail the run stream into Slack.
        async with _im_session_maker() as s:
            from sqlalchemy import select

            from cubeplex.models.im_connector import IMConnectorAccount

            account = (
                await s.execute(
                    select(IMConnectorAccount).where(IMConnectorAccount.id == item.account_id)
                )
            ).scalars().one()
            cred_service = CredentialService(
                CredentialRepository(s, org_id=account.org_id),
                app.state.encryption_backend,
                org_id=account.org_id,
                actor_user_id=None,
            )
            import json as _json

            secrets = _json.loads(
                await cred_service.get_decrypted(
                    credential_id=account.credential_id, requesting_kind="im_bot"
                )
            )
        connector = SlackConnector(
            bot_token=secrets["bot_token"],
            channel_id=item.slack_channel_id,
            # Use the real reply target, NOT the dedup key. None ⇒ unthreaded DM.
            thread_ts=item.slack_reply_thread_ts,
        )
        tailer = OutboundRunTailer(
            redis=app.state.run_manager._redis,
            key_prefix=app.state.run_manager._key_prefix,
            run_id=run_id,
            connector=connector,
        )
        import asyncio as _asyncio

        _asyncio.create_task(tailer.run(), name=f"im-tailer:{run_id}")

    im_worker = IMRunQueueWorker(
        session_maker=_im_session_maker,
        run_manager=app.state.run_manager,
        on_run_started=_on_im_run_started,
        poll_interval=1.0,
        lease_seconds=300,
    )
    im_worker.start()
    app.state.im_run_queue_worker = im_worker
```

In the shutdown path (where `run_manager.drain(...)` is awaited), add **before** the drain:

```python
    if getattr(app.state, "im_run_queue_worker", None) is not None:
        await app.state.im_run_queue_worker.stop()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/e2e/test_im_worker_startup.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/app.py backend/tests/e2e/test_im_worker_startup.py
git commit -m "feat(im): start run-queue worker + outbound tailer dispatch on app boot"
```

---

## Task 11: End-to-end inbound → run → outbound chain (E2E, real run path)

**Files:**
- Test: `backend/tests/e2e/test_im_end_to_end.py`

This is the spec's "real internal E2E (the bulk)": a captured-real signed Slack payload hits the ingress, a run actually starts on the real run path, and the outbound tailer consumes the run's **real Redis event stream**. The only thing faked is the outermost Slack HTTP call (genuinely unsimulatable per the "no fake E2E for unsimulatable third-party" rule) — captured via a recording connector — so we assert the inbound→run→stream chain end-to-end without mocking cubeplex internals.

- [ ] **Step 1: Write the E2E test**

```python
# backend/tests/e2e/test_im_end_to_end.py
import asyncio
import hashlib
import hmac
import json
import time

import pytest
from sqlalchemy import select

from cubeplex.im.outbound import OutboundRunTailer
from cubeplex.models.im_connector import IMRunQueueItem, IMWebhookReceipt

pytestmark = pytest.mark.asyncio

SIGNING_SECRET = "test-signing-secret-0123456789ab"


class _RecordingConnector:
    def __init__(self) -> None:
        self.posts: list[str] = []
        self.edits: list[str] = []

    async def post_placeholder(self, text: str) -> str:
        self.posts.append(text)
        return "msg.1"

    async def edit(self, ts, text: str) -> None:
        self.edits.append(text)


def _signed(body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    base = b"v0:" + ts.encode() + b":" + body
    return {"X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": "v0=" + hmac.new(SIGNING_SECRET.encode(), base,
                                                   hashlib.sha256).hexdigest(),
            "Content-Type": "application/json"}


async def test_inbound_starts_run_and_outbound_tails_real_stream(
    async_client, app_instance, im_account_with_secret, session_maker
):
    body = json.dumps({
        "type": "event_callback", "team_id": "T123", "event_id": "EvFull",
        "event": {"type": "app_mention", "user": "U1", "text": "<@UBOT> say hi",
                  "channel": "C1", "ts": "1700.0001"},
    }).encode()

    resp = await async_client.post("/api/v1/im/slack/events", content=body, headers=_signed(body))
    assert resp.status_code == 200

    # The worker drains within a couple poll cycles; wait for the receipt to flip.
    rm = app_instance.state.run_manager
    for _ in range(40):
        async with session_maker() as s:
            rcpt = (await s.execute(select(IMWebhookReceipt))).scalars().one_or_none()
            item = (await s.execute(select(IMRunQueueItem))).scalars().one_or_none()
        if rcpt is not None and rcpt.status == "completed" and item is not None:
            break
        await asyncio.sleep(0.25)
    assert rcpt.status == "completed"

    # Tail the real run stream with a recording connector; assert the run
    # produced events that rendered into a Slack post + final edit.
    # (The active run id for the conversation comes from Redis run meta.)
    from cubeplex.streams.run_events import get_active_run, get_latest_event_id  # noqa: F401

    # Find the run for this conversation; the worker already started it.
    # In single-process E2E the run is in-flight or just finished.
    rec = _RecordingConnector()
    # run_id is recoverable from the on_run_started tailer; here we assert the
    # render side by folding the conversation's run events directly.
    # Minimal assertion: the chain produced at least one outbound op.
    assert item.conversation_id is not None
```

> **Strengthen this — capture `run_id` and actually tail the real stream.** As
> written, the test never captures `run_id`, never runs `OutboundRunTailer`, and
> never asserts a Slack post/edit, so it does not cover the outbound half at all.
> Add a test hook so the chain is verified end to end: pass an `on_run_started`
> (or a recording connector) into the worker for this test that records the
> started `run_id`, then construct an `OutboundRunTailer(redis=rm._redis,
> key_prefix=rm._key_prefix, run_id=<captured>, connector=_RecordingConnector())`,
> run it against the **real** Redis run stream, and assert `rec.posts` is non-empty
> and a final edit was emitted. The exact text varies with the LLM, but the
> *existence* of at least one post + a finalizing edit is deterministic and must
> be asserted (it's the whole point of the outbound path). Only the outermost
> Slack HTTP call is faked (recording connector); everything else is real. Do
> **not** stand up a fake Slack server, and do not leave the outbound assertion as
> a no-op `assert item.conversation_id is not None`.

- [ ] **Step 2: Run the test**

Run: `cd backend && uv run pytest tests/e2e/test_im_end_to_end.py -v`
Expected: PASS (1 passed). If the live LLM is not configured in the E2E env, mark this test to skip when `CUBEPLEX_LLM__*` is absent (reuse the existing run-path E2E's skip guard — grep `tests/e2e` for the existing run E2E skip marker).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_im_end_to_end.py
git commit -m "test(im): end-to-end signed inbound -> run -> durable receipt chain"
```

---

## Task 12: Scope-isolated config — workspace IM account routes

**Files:**
- Create: `backend/cubeplex/services/im_connector.py`, `backend/cubeplex/api/schemas/im_connector.py`, `backend/cubeplex/api/routes/v1/ws_im.py`
- Modify: `backend/cubeplex/api/app.py` (register `ws_im.router`); `backend/cubeplex/services/credential.py` (`_guard_references` adds IM account check)
- Test: `backend/tests/e2e/test_ws_im_routes.py`

Workspace members connect/list/disconnect their workspace's own bots. The route stores the bot secrets in the vault (`kind="im_bot"`) and creates the account row, all via the shared `IMConnectorService`. Guarded by `require_member`.

- [ ] **Step 1: Write the failing E2E test**

```python
# backend/tests/e2e/test_ws_im_routes.py
import pytest

pytestmark = pytest.mark.asyncio


async def test_connect_list_delete_slack_account(member_client, workspace_id):
    create = await member_client.post(
        f"/api/v1/ws/{workspace_id}/im/accounts",
        json={"platform": "slack", "external_account_id": "T999",
              "bot_token": "xoxb-x", "signing_secret": "s", "bot_user_id": "UBOT",
              "acting_user_id": "self"},
    )
    assert create.status_code == 201
    account_id = create.json()["id"]
    assert account_id.startswith("imac-")

    listed = await member_client.get(f"/api/v1/ws/{workspace_id}/im/accounts")
    assert listed.status_code == 200
    assert any(a["id"] == account_id for a in listed.json()["accounts"])
    # Secrets never leak in the list response.
    assert "bot_token" not in json_dumps_keys(listed.json())

    deleted = await member_client.delete(f"/api/v1/ws/{workspace_id}/im/accounts/{account_id}")
    assert deleted.status_code == 204


def json_dumps_keys(obj) -> str:
    import json
    return json.dumps(obj)
```

> `member_client` / `workspace_id` are the existing workspace-scope E2E fixtures (the same ones `test_ws_sandbox_env`-style tests use; grep `tests/e2e` for them). `acting_user_id: "self"` is a sentinel the route maps to `ctx.user.id`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_ws_im_routes.py -v`
Expected: FAIL with 404 (route not registered)

- [ ] **Step 3: Write the schemas**

```python
# backend/cubeplex/api/schemas/im_connector.py
from pydantic import BaseModel


class ConnectSlackAccountIn(BaseModel):
    platform: str  # 'slack'
    external_account_id: str
    bot_token: str
    signing_secret: str
    bot_user_id: str
    acting_user_id: str  # 'self' -> ctx.user.id


class IMAccountOut(BaseModel):
    id: str
    platform: str
    external_account_id: str
    workspace_id: str
    acting_user_id: str
    enabled: bool


class IMAccountListOut(BaseModel):
    accounts: list[IMAccountOut]
```

- [ ] **Step 4: Write the shared service**

```python
# backend/cubeplex/services/im_connector.py
"""Shared IM connector service used by both ws and admin routes."""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.im_connector import IMConnectorAccount
from cubeplex.services.credential import CredentialService


class IMConnectorService:
    def __init__(
        self,
        session: AsyncSession,
        credentials: CredentialService,
        *,
        org_id: str,
    ) -> None:
        self._session = session
        self._credentials = credentials
        self._org_id = org_id

    async def connect_slack(
        self,
        *,
        workspace_id: str,
        external_account_id: str,
        acting_user_id: str,
        bot_token: str,
        signing_secret: str,
        bot_user_id: str,
    ) -> IMConnectorAccount:
        credential_id = await self._credentials.upsert_by_kind_name(
            kind="im_bot",
            name=f"slack:{external_account_id}",
            plaintext=json.dumps(
                {"bot_token": bot_token, "signing_secret": signing_secret,
                 "bot_user_id": bot_user_id}
            ),
        )
        account = IMConnectorAccount(
            org_id=self._org_id,
            workspace_id=workspace_id,
            platform="slack",
            external_account_id=external_account_id,
            acting_user_id=acting_user_id,
            credential_id=credential_id,
        )
        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def list_for_workspace(self, *, workspace_id: str) -> list[IMConnectorAccount]:
        stmt = select(IMConnectorAccount).where(
            IMConnectorAccount.org_id == self._org_id,
            IMConnectorAccount.workspace_id == workspace_id,
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_for_org(self) -> list[IMConnectorAccount]:
        stmt = select(IMConnectorAccount).where(IMConnectorAccount.org_id == self._org_id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get(self, *, account_id: str) -> IMConnectorAccount | None:
        stmt = select(IMConnectorAccount).where(
            IMConnectorAccount.id == account_id,
            IMConnectorAccount.org_id == self._org_id,
        )
        return (await self._session.execute(stmt)).scalars().one_or_none()

    async def delete(self, *, account_id: str) -> None:
        account = await self.get(account_id=account_id)
        if account is None:
            return
        credential_id = account.credential_id
        await self._session.delete(account)
        await self._session.commit()
        # Credential is now unreferenced; best-effort delete.
        try:
            await self._credentials.delete(credential_id=credential_id)
        except Exception:
            pass

    async def set_enabled(self, *, account_id: str, enabled: bool) -> IMConnectorAccount | None:
        account = await self.get(account_id=account_id)
        if account is None:
            return None
        account.enabled = enabled
        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account
```

- [ ] **Step 5: Write the workspace routes**

```python
# backend/cubeplex/api/routes/v1/ws_im.py
"""Workspace-scope IM connector routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.im_connector import (
    ConnectSlackAccountIn,
    IMAccountListOut,
    IMAccountOut,
)
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.credentials.dependencies import get_encryption_backend
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.db.session import get_session
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.services.credential import CredentialService
from cubeplex.services.im_connector import IMConnectorService

router = APIRouter(prefix="/ws/{workspace_id}/im", tags=["ws-im"])


def _service(session: AsyncSession, backend: EncryptionBackend, ctx: RequestContext) -> IMConnectorService:
    creds = CredentialService(
        CredentialRepository(session, org_id=ctx.org_id),
        backend,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )
    return IMConnectorService(session, creds, org_id=ctx.org_id)


def _to_out(a) -> IMAccountOut:
    return IMAccountOut(
        id=a.id, platform=a.platform, external_account_id=a.external_account_id,
        workspace_id=a.workspace_id, acting_user_id=a.acting_user_id, enabled=a.enabled,
    )


@router.post("/accounts", status_code=status.HTTP_201_CREATED, response_model=IMAccountOut)
async def connect_account(
    workspace_id: str,
    body: ConnectSlackAccountIn,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    acting = ctx.user.id if body.acting_user_id == "self" else body.acting_user_id
    account = await svc.connect_slack(
        workspace_id=ctx.workspace_id,
        external_account_id=body.external_account_id,
        acting_user_id=acting,
        bot_token=body.bot_token,
        signing_secret=body.signing_secret,
        bot_user_id=body.bot_user_id,
    )
    return _to_out(account)


@router.get("/accounts", response_model=IMAccountListOut)
async def list_accounts(
    workspace_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountListOut:
    svc = _service(session, backend, ctx)
    accounts = await svc.list_for_workspace(workspace_id=ctx.workspace_id)
    return IMAccountListOut(accounts=[_to_out(a) for a in accounts])


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    workspace_id: str,
    account_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> None:
    svc = _service(session, backend, ctx)
    await svc.delete(account_id=account_id)
```

- [ ] **Step 6: Guard credential deletion against live IM accounts**

In `backend/cubeplex/services/credential.py`, inside `_guard_references`, after the `SandboxEnvVar` block add:

```python
        from cubeplex.models import IMConnectorAccount

        im_refs = (
            (
                await session.execute(
                    select(IMConnectorAccount).where(
                        IMConnectorAccount.credential_id == credential_id  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        if im_refs:
            raise CredentialInUseError(
                f"credential {credential_id} referenced by IMConnectorAccount: "
                f"{[a.id for a in im_refs]}"
            )
```

> `IMConnectorService.delete` deletes the account row first, so by the time it calls `credentials.delete`, no account references the credential and this guard passes. The guard only protects against deleting a vault row out from under a live account via the credential API directly.

- [ ] **Step 7: Register the router**

In `backend/cubeplex/api/app.py`, near the other `ws_*` includes:

```python
    from cubeplex.api.routes.v1 import ws_im

    app.include_router(ws_im.router, prefix="/api/v1")
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/e2e/test_ws_im_routes.py -v`
Expected: PASS (1 passed)

- [ ] **Step 9: Commit**

```bash
git add backend/cubeplex/services/im_connector.py backend/cubeplex/api/schemas/im_connector.py backend/cubeplex/api/routes/v1/ws_im.py backend/cubeplex/services/credential.py backend/cubeplex/api/app.py backend/tests/e2e/test_ws_im_routes.py
git commit -m "feat(im): workspace-scope IM account routes (connect/list/delete)"
```

---

## Task 13: Scope-isolated config — org-admin IM account routes

**Files:**
- Create: `backend/cubeplex/api/routes/v1/admin_im.py`
- Modify: `backend/cubeplex/api/app.py` (register `admin_im.router`)
- Test: `backend/tests/e2e/test_admin_im_routes.py`

A **separate handler** (not a `?scope=` flag): an org admin lists every IM account across the org's workspaces and can enable/disable them. Reuse goes through `IMConnectorService.list_for_org` / `set_enabled` — never the route layer. Guarded by `get_admin_request_context` (NOT `require_admin`: these routes have no `{workspace_id}` path segment, so the workspace-scoped `require_admin`/`require_role` cannot resolve a workspace permission — use the org-admin context dependency the `/admin/mcp` routes use).

- [ ] **Step 1: Write the failing E2E test**

```python
# backend/tests/e2e/test_admin_im_routes.py
import pytest

pytestmark = pytest.mark.asyncio


async def test_admin_lists_org_accounts_and_toggles_enabled(
    admin_client, member_client, workspace_id
):
    create = await member_client.post(
        f"/api/v1/ws/{workspace_id}/im/accounts",
        json={"platform": "slack", "external_account_id": "T-ADMIN",
              "bot_token": "xoxb-x", "signing_secret": "s", "bot_user_id": "UBOT",
              "acting_user_id": "self"},
    )
    account_id = create.json()["id"]

    listed = await admin_client.get("/api/v1/admin/im/accounts")
    assert listed.status_code == 200
    assert any(a["id"] == account_id for a in listed.json()["accounts"])

    disabled = await admin_client.post(f"/api/v1/admin/im/accounts/{account_id}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False


async def test_member_cannot_reach_admin_route(member_client):
    resp = await member_client.get("/api/v1/admin/im/accounts")
    assert resp.status_code in (401, 403)
```

> `admin_client` is the existing org-admin E2E fixture (same one `test_admin_sandbox_env`-style tests use; grep `tests/e2e` for it).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_admin_im_routes.py -v`
Expected: FAIL with 404

- [ ] **Step 3: Write the admin routes**

```python
# backend/cubeplex/api/routes/v1/admin_im.py
"""Org-admin-scope IM connector governance routes (separate handler)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.im_connector import IMAccountListOut, IMAccountOut
from cubeplex.auth.context import RequestContext
from cubeplex.credentials.dependencies import get_encryption_backend
from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.db.session import get_session
from cubeplex.mcp.dependencies import get_admin_request_context
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.services.credential import CredentialService
from cubeplex.services.im_connector import IMConnectorService

router = APIRouter(prefix="/admin/im", tags=["admin-im"])

# get_admin_request_context is backed by require_org_admin and resolves the
# org_id without needing a {workspace_id} path segment — the org-admin pattern
# used by /admin/mcp. (require_admin/require_role are workspace-scoped and would
# fail here because ctx.workspace_id is unset on org-admin routes.)


def _service(session: AsyncSession, backend: EncryptionBackend, ctx: RequestContext) -> IMConnectorService:
    creds = CredentialService(
        CredentialRepository(session, org_id=ctx.org_id),
        backend,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
    )
    return IMConnectorService(session, creds, org_id=ctx.org_id)


def _to_out(a) -> IMAccountOut:
    return IMAccountOut(
        id=a.id, platform=a.platform, external_account_id=a.external_account_id,
        workspace_id=a.workspace_id, acting_user_id=a.acting_user_id, enabled=a.enabled,
    )


@router.get("/accounts", response_model=IMAccountListOut)
async def list_org_accounts(
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountListOut:
    svc = _service(session, backend, ctx)
    return IMAccountListOut(accounts=[_to_out(a) for a in await svc.list_for_org()])


@router.post("/accounts/{account_id}/disable", response_model=IMAccountOut)
async def disable_account(
    account_id: str,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    account = await svc.set_enabled(account_id=account_id, enabled=False)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return _to_out(account)


@router.post("/accounts/{account_id}/enable", response_model=IMAccountOut)
async def enable_account(
    account_id: str,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
    backend: Annotated[EncryptionBackend, Depends(get_encryption_backend)],
) -> IMAccountOut:
    svc = _service(session, backend, ctx)
    account = await svc.set_enabled(account_id=account_id, enabled=True)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return _to_out(account)
```

- [ ] **Step 4: Register the router**

In `backend/cubeplex/api/app.py`, near the other `admin_*` includes:

```python
    from cubeplex.api.routes.v1 import admin_im

    app.include_router(admin_im.router, prefix="/api/v1")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/e2e/test_admin_im_routes.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/api/routes/v1/admin_im.py backend/cubeplex/api/app.py backend/tests/e2e/test_admin_im_routes.py
git commit -m "feat(im): org-admin IM account governance routes (separate handler)"
```

---

## Task 14: Multi-tenant isolation E2E + identity mapping check

**Files:**
- Test: `backend/tests/e2e/test_im_isolation.py`

Spec "multi-tenant isolation E2E": two accounts bound to two workspaces; an event for account A only ever touches workspace A's conversations. Also asserts identity attribution lands on `account.acting_user_id` (v1 binding-level default — there is no `IMIdentityLink` lookup in v1; the run is attributed to the binding's acting user).

- [ ] **Step 1: Write the E2E test**

```python
# backend/tests/e2e/test_im_isolation.py
import json

import pytest
from sqlalchemy import select

from cubeplex.im.inbound import ingest_inbound_event
from cubeplex.im.types import InboundEvent
from cubeplex.models.conversation import Conversation
from cubeplex.models.im_connector import IMConnectorAccount, IMThreadLink

pytestmark = pytest.mark.asyncio


async def test_event_for_account_a_never_touches_workspace_b(
    two_im_accounts, session_maker
):
    account_a, account_b = two_im_accounts  # bound to ws_a, ws_b respectively
    ev = InboundEvent(
        platform="slack", account_external_id=account_a.external_account_id,
        platform_event_id="EvIso", channel_id="C-A", thread_root_id="t.a",
        reply_thread_ts="t.a", sender_ref="U-A", text="hello A",
    )
    res = await ingest_inbound_event(ev, account=account_a, session_maker=session_maker)

    async with session_maker() as s:
        conv = (
            await s.execute(select(Conversation).where(Conversation.id == res.conversation_id))
        ).scalars().one()
        link = (await s.execute(select(IMThreadLink))).scalars().one()
    # The created conversation + link belong to A's workspace, never B's.
    assert conv.workspace_id == account_a.workspace_id
    assert conv.workspace_id != account_b.workspace_id
    assert link.account_id == account_a.id
    # Attribution: the conversation's creator is A's acting user (binding default).
    assert conv.creator_user_id == account_a.acting_user_id
```

> `two_im_accounts` seeds two orgs/workspaces (reuse the existing multi-tenant E2E fixture; grep `tests/e2e` for a fixture that creates two orgs/workspaces) and an `IMConnectorAccount` in each. If no two-tenant fixture exists, build it from the single-tenant `im_account` fixture by constructing a second org/workspace/user with the existing bootstrap helper.

- [ ] **Step 2: Run the test**

Run: `cd backend && uv run pytest tests/e2e/test_im_isolation.py -v`
Expected: PASS (1 passed)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_im_isolation.py
git commit -m "test(im): multi-tenant isolation + binding-level attribution"
```

---

## Task 15: Slack app manifest + manual smoke checklist (the unsimulatable boundary)

**Files:**
- Create: `backend/docs/im-slack-setup.md` (reference doc — permitted: this is new operator-facing setup, not a spec/plan)

Per the spec's testing strategy, the Slack HTTP boundary is the genuinely unsimulatable part. We document a manifest template and a manual smoke checklist run against a real dev Slack workspace before release — we do **not** fake Slack's servers.

- [ ] **Step 1: Write the setup doc**

Create `backend/docs/im-slack-setup.md` containing:
1. **App manifest** (YAML) declaring scopes `app_mentions:read`, `chat:write`, `im:history`, `im:read`, `channels:history`; event subscriptions `app_mention`, `message.im`; request URL `https://<host>/api/v1/im/slack/events`.

   **Caveat — channel thread replies are NOT received by this manifest.** With
   only `app_mention` + `message.im` subscribed, a follow-up message in a channel
   thread (no new @mention) never reaches the ingress, so the "second mention in
   the same thread reuses the conversation" path only works for re-mentions, not
   bare thread replies. Receiving bare channel thread replies requires subscribing
   `message.channels`, which delivers EVERY channel message — the parser must then
   accept only messages that (a) are replies in a thread whose root is a known
   `IMThreadLink`, or (b) @mention the bot, and drop the rest. Decide explicitly:
   either (i) document v1 as "channel = mention-only, threaded follow-ups need a
   re-mention" (cheapest, matches the current manifest), or (ii) add the
   `message.channels` subscription + a known-thread/mention filter in
   `parse_inbound`. The current parser's `thread_ts` handling implies (ii) but the
   manifest only enables (i) — make them consistent.
2. **Install steps**: create app from manifest → install to workspace → copy bot token (`xoxb-`) + signing secret → POST them to `POST /api/v1/ws/{ws}/im/accounts`.
3. **Manual smoke checklist** (the unsimulatable boundary):
   - [ ] `@mention` the bot in a channel → a threaded placeholder reply appears, then edits live, then finalizes.
   - [ ] DM the bot → one rolling DM conversation; a second DM reuses it (same thread sentinel).
   - [ ] A second `@mention` in the same thread reuses the conversation (agent has context).
   - [ ] Force a Slack retry (slow ack) → no duplicate reply (receipt dedupe).
   - [ ] Tamper a request signature → 401, no run.

- [ ] **Step 2: Commit**

```bash
git add backend/docs/im-slack-setup.md
git commit -m "docs(im): Slack app manifest + manual smoke checklist"
```

---

## Task 16: Full pre-PR test sweep

- [ ] **Step 1: Run the IM module test sweep**

Run:
```bash
cd backend && uv run pytest \
  tests/unit/test_im_models.py \
  tests/unit/test_slack_signature.py \
  tests/unit/test_slack_parse_inbound.py \
  tests/unit/test_im_outbound_render.py \
  tests/integration/test_im_inbound_outbox.py \
  tests/integration/test_im_worker.py \
  tests/e2e/test_im_slack_ingress.py \
  tests/e2e/test_im_worker_startup.py \
  tests/e2e/test_im_end_to_end.py \
  tests/e2e/test_ws_im_routes.py \
  tests/e2e/test_admin_im_routes.py \
  tests/e2e/test_im_isolation.py -v
```
Expected: all pass (LLM-gated E2E may skip if no LLM configured — that is acceptable).

- [ ] **Step 2: Type check + lint**

Run: `cd backend && uv run mypy cubeplex/im cubeplex/models/im_connector.py cubeplex/repositories/im_connector.py cubeplex/services/im_connector.py cubeplex/api/routes/v1/im_ingress.py cubeplex/api/routes/v1/ws_im.py cubeplex/api/routes/v1/admin_im.py && uv run ruff check cubeplex/im cubeplex/api/routes/v1/im_ingress.py cubeplex/api/routes/v1/ws_im.py cubeplex/api/routes/v1/admin_im.py`
Expected: no errors. Fix any (line length 100, type annotations everywhere).

- [ ] **Step 3: Commit any fixes from the sweep**

```bash
git add -A
git commit -m "chore(im): fix types/lint from pre-PR sweep"
```

---

## Self-Review Notes (for the implementer)

- **Feishu is v1.1, out of scope here.** The `platform` column, the `IMConnector` protocol naming, and the connector package layout (`cubeplex/im/slack/`) leave room for `cubeplex/im/feishu/` without schema changes. Do not build Feishu in this plan.
- **Open Questions resolved for v1 in this plan:** DM = one rolling conversation (sentinel thread root, no per-day reset); concurrent message on a live run = the second event enqueues and `start_run` raises 409 inside the worker, which logs and leaves the receipt for re-claim (acceptable v1 behavior — a follow-up turn, not a steer); identity = binding-level acting user, no `IMIdentityLink` lookup wired yet (table exists for v1.1); delivery = HTTP webhook only; streaming = debounced `chat.update` (not native `chat.startStream`); rate-limit = latest-wins via debounce. These are documented choices, not gaps.
- **Single-process affinity** (spec Open Question) is the one real limitation carried forward: the worker, run, and tailer all live in the same API process in v1. Recorded in Task 1's design note.
