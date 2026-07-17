# IM Group Chat ↔ Topic Mapping — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bridge IM group chats to cubeplex topics via per-channel binding configuration, so multiple users in a Slack/Feishu/Discord channel share a single topic with thread-scoped conversations.

**Architecture:** A new `im_channel_bindings` table stores admin-configured per-channel mode (`isolated`/`shared`). In shared mode, inbound events use channel/thread scope keys instead of per-user, and lazily create topics + conversations + participants. Worker/resume guards are relaxed for IM-bound topic conversations.

**Tech Stack:** SQLModel + Alembic (model/migration), FastAPI (CRUD API), cubepi RunContext (sandbox resolution), Slack/Feishu/Discord connectors (scope key selection).

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Create | `cubeplex/models/im_channel_binding.py` | `IMChannelBinding` SQLModel + table definition |
| Modify | `cubeplex/models/public_id.py` | Add `PREFIX_IM_CHANNEL_BINDING = "icb"` |
| Create | `alembic/versions/XXXX_add_im_channel_bindings.py` | Migration for new table |
| Create | `cubeplex/repositories/im_channel_binding.py` | Binding CRUD + lookup helpers |
| Modify | `cubeplex/im/types.py` | Add `BindingMode` literals, no new scope helpers needed |
| Modify | `cubeplex/im/slack/connector.py` | Accept binding mode, switch scope key strategy |
| Modify | `cubeplex/im/feishu/connector.py` | Accept binding mode, switch scope key strategy |
| Modify | `cubeplex/im/discord/connector.py` | Accept binding mode, switch scope key strategy |
| Modify | `cubeplex/im/inbound.py` | Shared-mode topic/conversation/participant creation |
| Modify | `cubeplex/im/worker.py` | Relax topic/group-chat guard for IM shared mode |
| Modify | `cubeplex/im/resume.py` | Relax topic/group-chat guard for IM shared mode |
| Modify | `cubeplex/im/outbound.py` | Skip `awaiting_responder` gate in shared mode |
| Create | `cubeplex/api/schemas/im_channel_binding.py` | Pydantic schemas for binding CRUD |
| Modify | `cubeplex/api/routes/v1/ws_im.py` | Channel binding CRUD routes |
| Create | `tests/e2e/test_im_channel_binding_crud.py` | E2E: binding API CRUD |
| Create | `tests/e2e/test_im_shared_mode_ingest.py` | E2E: shared-mode inbound lifecycle |
| Create | `tests/unit/test_im_scope_key_selection.py` | Unit: scope key selection logic |

---

### Task 1: Model + Public ID Prefix

**Files:**
- Modify: `backend/cubeplex/models/public_id.py:50` (after `PREFIX_IM_RUN_QUEUE_ITEM`)
- Create: `backend/cubeplex/models/im_channel_binding.py`

- [ ] **Step 1: Add public ID prefix**

In `cubeplex/models/public_id.py`, add the new prefix constant after line 50 (`PREFIX_IM_RUN_QUEUE_ITEM`):

```python
PREFIX_IM_CHANNEL_BINDING: str = "icb"
```

- [ ] **Step 2: Create the IMChannelBinding model**

Create `cubeplex/models/im_channel_binding.py`:

```python
"""IM channel binding: per-channel mapping mode configuration."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import Column, DateTime, Index
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase, OrgScopedMixin
from cubeplex.models.public_id import PREFIX_IM_CHANNEL_BINDING


class IMChannelBinding(CubeplexBase, OrgScopedMixin, table=True):
    """Admin-configured mapping for one (account, channel) pair.

    mode="isolated" preserves current per-user behavior.
    mode="shared" maps the channel to a topic, threads to conversations.
    """

    _PREFIX: ClassVar[str] = PREFIX_IM_CHANNEL_BINDING
    __tablename__ = "im_channel_bindings"
    __table_args__ = (
        Index(
            "uq_im_channel_binding",
            "account_id",
            "channel_id",
            unique=True,
        ),
        Index("ix_im_channel_binding_account", "account_id"),
    )

    account_id: str = Field(
        foreign_key="im_connector_accounts.id",
        max_length=20,
        ondelete="CASCADE",
    )
    channel_id: str = Field(max_length=128)
    channel_name: str = Field(default="", max_length=255)
    mode: str = Field(default="isolated", max_length=16)
    sandbox_mode: str | None = Field(default=None, max_length=16, nullable=True)
    topic_id: str | None = Field(
        default=None,
        foreign_key="topics.id",
        max_length=20,
        nullable=True,
    )
```

- [ ] **Step 3: Register the model in the models package**

Check `cubeplex/models/__init__.py` — if IM models are imported there for Alembic discovery, add:

```python
from cubeplex.models.im_channel_binding import IMChannelBinding  # noqa: F401
```

- [ ] **Step 4: Generate the Alembic migration**

Run from `backend/`:

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run alembic revision --autogenerate -m "add im_channel_bindings table"
```

Verify the generated migration creates the `im_channel_bindings` table with the correct columns, indexes, and foreign keys.

- [ ] **Step 5: Run the migration**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run alembic upgrade head
```

- [ ] **Step 6: Commit**

```bash
git add cubeplex/models/public_id.py cubeplex/models/im_channel_binding.py cubeplex/models/__init__.py alembic/versions/*im_channel_bindings*
git commit -m "feat(im): add IMChannelBinding model and migration"
```

---

### Task 2: Channel Binding Repository

**Files:**
- Create: `backend/cubeplex/repositories/im_channel_binding.py`

- [ ] **Step 1: Write the E2E test for binding CRUD**

Create `backend/tests/e2e/test_im_channel_binding_crud.py`:

```python
"""E2E tests for IMChannelBinding repository operations."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.im_channel_binding import IMChannelBinding
from cubeplex.repositories.im_channel_binding import IMChannelBindingRepository


@pytest.fixture
def binding_repo(
    db_session: AsyncSession,
    test_org_id: str,
    test_workspace_id: str,
) -> IMChannelBindingRepository:
    return IMChannelBindingRepository(
        db_session,
        org_id=test_org_id,
        workspace_id=test_workspace_id,
    )


@pytest.mark.asyncio
async def test_create_and_get_binding(
    binding_repo: IMChannelBindingRepository,
    im_account: str,  # fixture that creates an IMConnectorAccount, returns id
) -> None:
    binding = await binding_repo.create(
        account_id=im_account,
        channel_id="C001",
        channel_name="general",
        mode="shared",
        sandbox_mode="dedicated",
    )
    assert binding.id.startswith("icb-")
    assert binding.mode == "shared"
    assert binding.sandbox_mode == "dedicated"
    assert binding.topic_id is None

    fetched = await binding_repo.get_by_account_channel(
        account_id=im_account,
        channel_id="C001",
    )
    assert fetched is not None
    assert fetched.id == binding.id


@pytest.mark.asyncio
async def test_list_by_account(
    binding_repo: IMChannelBindingRepository,
    im_account: str,
) -> None:
    await binding_repo.create(
        account_id=im_account,
        channel_id="C001",
        channel_name="general",
        mode="shared",
        sandbox_mode="dedicated",
    )
    await binding_repo.create(
        account_id=im_account,
        channel_id="C002",
        channel_name="random",
        mode="isolated",
    )
    bindings = await binding_repo.list_by_account(account_id=im_account)
    assert len(bindings) == 2


@pytest.mark.asyncio
async def test_update_mode(
    binding_repo: IMChannelBindingRepository,
    im_account: str,
) -> None:
    binding = await binding_repo.create(
        account_id=im_account,
        channel_id="C001",
        channel_name="general",
        mode="shared",
        sandbox_mode="dedicated",
    )
    updated = await binding_repo.update(
        binding_id=binding.id,
        mode="isolated",
    )
    assert updated is not None
    assert updated.mode == "isolated"


@pytest.mark.asyncio
async def test_delete_binding(
    binding_repo: IMChannelBindingRepository,
    im_account: str,
) -> None:
    binding = await binding_repo.create(
        account_id=im_account,
        channel_id="C001",
        channel_name="general",
        mode="isolated",
    )
    deleted = await binding_repo.delete(binding_id=binding.id)
    assert deleted is True
    assert await binding_repo.get_by_account_channel(
        account_id=im_account,
        channel_id="C001",
    ) is None


@pytest.mark.asyncio
async def test_unique_constraint(
    binding_repo: IMChannelBindingRepository,
    im_account: str,
) -> None:
    await binding_repo.create(
        account_id=im_account,
        channel_id="C001",
        channel_name="general",
        mode="isolated",
    )
    with pytest.raises(ValueError, match="already bound"):
        await binding_repo.create(
            account_id=im_account,
            channel_id="C001",
            channel_name="general-dup",
            mode="shared",
            sandbox_mode="dedicated",
        )
```

Note: The `im_account` fixture needs to be created (or adapted from existing IM test fixtures). It should create an `IMConnectorAccount` row and return its `id`. Check `tests/e2e/conftest.py` or `tests/conftest.py` for existing IM fixtures; if none exist, create one inline in this test file.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/e2e/test_im_channel_binding_crud.py -v
```

Expected: ImportError — `IMChannelBindingRepository` does not exist yet.

- [ ] **Step 3: Implement the repository**

Create `cubeplex/repositories/im_channel_binding.py`:

```python
"""IM channel binding repository — CRUD + lookup for channel→mode mapping."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.im_channel_binding import IMChannelBinding
from cubeplex.repositories.base import ScopedRepository


class IMChannelBindingRepository(ScopedRepository[IMChannelBinding]):
    model = IMChannelBinding

    def __init__(
        self,
        session: AsyncSession,
        *,
        org_id: str,
        workspace_id: str,
    ) -> None:
        super().__init__(session, org_id=org_id, workspace_id=workspace_id)

    async def create(
        self,
        *,
        account_id: str,
        channel_id: str,
        channel_name: str = "",
        mode: str = "isolated",
        sandbox_mode: str | None = None,
    ) -> IMChannelBinding:
        binding = IMChannelBinding(
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            account_id=account_id,
            channel_id=channel_id,
            channel_name=channel_name,
            mode=mode,
            sandbox_mode=sandbox_mode,
        )
        self.session.add(binding)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            if "uq_im_channel_binding" in str(exc):
                raise ValueError(
                    f"Channel {channel_id} is already bound to account {account_id}"
                ) from exc
            raise
        return binding

    async def get_by_account_channel(
        self,
        *,
        account_id: str,
        channel_id: str,
    ) -> IMChannelBinding | None:
        stmt = select(IMChannelBinding).where(
            IMChannelBinding.account_id == account_id,  # type: ignore[arg-type]
            IMChannelBinding.channel_id == channel_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_account(
        self,
        *,
        account_id: str,
    ) -> list[IMChannelBinding]:
        stmt = (
            self._scoped_select()
            .where(cast(Any, IMChannelBinding.account_id) == account_id)
            .order_by(cast(Any, IMChannelBinding.created_at).desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def update(
        self,
        *,
        binding_id: str,
        mode: str | None = None,
        sandbox_mode: str | None = ...,  # type: ignore[assignment]
        channel_name: str | None = None,
    ) -> IMChannelBinding | None:
        binding = await self.get(binding_id)
        if binding is None:
            return None
        if mode is not None:
            binding.mode = mode
        if sandbox_mode is not ...:
            binding.sandbox_mode = sandbox_mode
        if channel_name is not None:
            binding.channel_name = channel_name
        self.session.add(binding)
        await self.session.flush()
        return binding

    async def delete(self, *, binding_id: str) -> bool:
        binding = await self.get(binding_id)
        if binding is None:
            return False
        await self.session.delete(binding)
        await self.session.flush()
        return True

    async def set_topic_id(
        self,
        *,
        binding_id: str,
        topic_id: str,
    ) -> None:
        binding = await self.get(binding_id)
        if binding is None:
            raise ValueError(f"Binding {binding_id} not found")
        binding.topic_id = topic_id
        self.session.add(binding)
        await self.session.flush()
```

- [ ] **Step 4: Run tests**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/e2e/test_im_channel_binding_crud.py -v
```

Expected: All 5 tests pass. If fixtures are missing (`db_session`, `test_org_id`, `test_workspace_id`, `im_account`), adapt them from existing E2E test conftest patterns.

- [ ] **Step 5: Commit**

```bash
git add cubeplex/repositories/im_channel_binding.py tests/e2e/test_im_channel_binding_crud.py
git commit -m "feat(im): add IMChannelBinding repository with CRUD"
```

---

### Task 3: Scope Key Selection in Connectors

**Files:**
- Modify: `backend/cubeplex/im/types.py:17` (add type literals)
- Modify: `backend/cubeplex/im/slack/connector.py:57` (`parse_inbound`)
- Modify: `backend/cubeplex/im/feishu/connector.py:91` (`parse_inbound`)
- Modify: `backend/cubeplex/im/discord/connector.py:81` (`parse_inbound`)
- Create: `backend/tests/unit/test_im_scope_key_selection.py`

- [ ] **Step 1: Add binding mode type to types.py**

In `cubeplex/im/types.py`, add after the imports (before line 17):

```python
from typing import Literal

BindingMode = Literal["isolated", "shared"]
```

- [ ] **Step 2: Write the unit test for scope key selection**

Create `backend/tests/unit/test_im_scope_key_selection.py`:

```python
"""Unit tests for scope key selection based on binding mode."""

from cubeplex.im.slack.connector import SlackConnector
from cubeplex.im.types import DM_SCOPE_KEY


def _make_slack_event(
    *,
    user: str = "U001",
    channel: str = "C001",
    ts: str = "1234.5678",
    thread_ts: str = "",
    channel_type: str = "channel",
    event_type: str = "app_mention",
    text: str = "<@BOTID> hello",
) -> dict:
    d = {
        "user": user,
        "channel": channel,
        "ts": ts,
        "channel_type": channel_type,
        "type": event_type,
        "text": text,
    }
    if thread_ts:
        d["thread_ts"] = thread_ts
    return d


class TestSlackScopeKeySelection:
    """Scope key selection for Slack connector."""

    def test_dm_always_isolated(self) -> None:
        conn = SlackConnector(bot_user_id="BOTID")
        event = conn.parse_inbound(
            _make_slack_event(channel_type="im", event_type="message"),
            binding_mode="shared",
        )
        assert event is not None
        assert event.scope_key == DM_SCOPE_KEY
        assert event.scope_kind == "dm"

    def test_channel_mention_isolated_mode(self) -> None:
        conn = SlackConnector(bot_user_id="BOTID")
        event = conn.parse_inbound(
            _make_slack_event(),
            binding_mode="isolated",
        )
        assert event is not None
        assert event.scope_key == "u:U001"
        assert event.scope_kind == "channel"

    def test_channel_mention_shared_mode(self) -> None:
        conn = SlackConnector(bot_user_id="BOTID")
        event = conn.parse_inbound(
            _make_slack_event(),
            binding_mode="shared",
        )
        assert event is not None
        assert event.scope_key == "ch"
        assert event.scope_kind == "channel"

    def test_thread_mention_isolated_mode(self) -> None:
        conn = SlackConnector(bot_user_id="BOTID")
        event = conn.parse_inbound(
            _make_slack_event(thread_ts="1111.0000"),
            binding_mode="isolated",
        )
        assert event is not None
        assert event.scope_key == "u:U001|t:1111.0000"
        assert event.scope_kind == "thread"

    def test_thread_mention_shared_mode(self) -> None:
        conn = SlackConnector(bot_user_id="BOTID")
        event = conn.parse_inbound(
            _make_slack_event(thread_ts="1111.0000"),
            binding_mode="shared",
        )
        assert event is not None
        assert event.scope_key == "t:1111.0000"
        assert event.scope_kind == "thread"

    def test_default_binding_mode_is_isolated(self) -> None:
        """When no binding_mode is passed, behavior matches isolated."""
        conn = SlackConnector(bot_user_id="BOTID")
        event = conn.parse_inbound(_make_slack_event())
        assert event is not None
        assert event.scope_key == "u:U001"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/unit/test_im_scope_key_selection.py -v
```

Expected: FAIL — `parse_inbound` doesn't accept `binding_mode`.

- [ ] **Step 4: Modify SlackConnector.parse_inbound**

In `cubeplex/im/slack/connector.py`:

1. Add import at the top:
```python
from cubeplex.im.types import (
    BindingMode,
    DM_SCOPE_KEY,
    InboundEvent,
    make_channel_scope,
    make_participant_scope,
    make_thread_participant_scope,
    make_thread_scope,
)
```

2. Change the `parse_inbound` signature (line 57) to accept `binding_mode`:
```python
def parse_inbound(
    self, raw: dict[str, Any], binding_mode: BindingMode = "isolated"
) -> InboundEvent | None:
```

3. In the DM branch (line 95), keep unchanged — DM always isolated.

4. In the thread branch (lines 110-124), switch scope key based on mode:
```python
# Thread replies (thread_ts present and different from ts)
if event_type == "app_mention" and thread_ts and thread_ts != ts:
    if binding_mode == "shared":
        scope_key = make_thread_scope(thread_ts)
    else:
        scope_key = make_thread_participant_scope(user, thread_ts)
    return InboundEvent(
        platform="slack",
        account_external_id="",
        platform_event_id=platform_event_id,
        channel_id=channel,
        scope_key=scope_key,
        scope_kind="thread",
        reply_to_id=thread_ts,
        inbound_message_id=ts,
        sender_ref=sender_ref,
        sender_open_id=sender_open_id,
        text=text,
    )
```

5. In the channel mention branch (lines 127-140), switch scope key:
```python
# Channel @mention (not in a thread)
if event_type == "app_mention":
    if binding_mode == "shared":
        scope_key = make_channel_scope()
    else:
        scope_key = make_participant_scope(user)
    return InboundEvent(
        platform="slack",
        account_external_id="",
        platform_event_id=platform_event_id,
        channel_id=channel,
        scope_key=scope_key,
        scope_kind="channel",
        reply_to_id=ts,
        inbound_message_id=ts,
        sender_ref=sender_ref,
        sender_open_id=sender_open_id,
        text=text,
    )
```

- [ ] **Step 5: Modify FeishuConnector.parse_inbound**

In `cubeplex/im/feishu/connector.py`:

1. Add imports:
```python
from cubeplex.im.types import (
    BindingMode,
    DM_SCOPE_KEY,
    InboundEvent,
    RenderState,
    make_channel_scope,
    make_participant_scope,
)
```

2. Change `parse_inbound` signature to accept `binding_mode`:
```python
def parse_inbound(
    self, raw: dict[str, Any], binding_mode: BindingMode = "isolated"
) -> InboundEvent | None:
```

3. In the non-DM branch (around line 151-157), switch scope key:
```python
else:
    if not self._group_message_mentions_bot(message):
        return None
    if not sender_ref:
        return None
    if binding_mode == "shared":
        scope_key = make_channel_scope()
        scope_kind = "channel"
    else:
        scope_key = make_participant_scope(sender_ref)
        scope_kind = "participant"
    reply_target = message_id
```

Note: Feishu doesn't have thread_ts-style threading in group chats (replies go to the same chat). The channel-level scope is sufficient for v1. Thread support can be added if Feishu adds thread semantics.

- [ ] **Step 6: Modify DiscordConnector.parse_inbound**

In `cubeplex/im/discord/connector.py`:

1. Add imports (add `BindingMode`, `make_channel_scope`, `make_thread_scope`).

2. Change signature:
```python
def parse_inbound(
    self, message: Any, binding_mode: BindingMode = "isolated"
) -> InboundEvent | None:
```

3. In the thread branch, switch scope key based on mode. In the channel branch, switch similarly. Pattern is identical to Slack.

- [ ] **Step 7: Run unit tests**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/unit/test_im_scope_key_selection.py -v
```

Expected: All 6 tests pass.

- [ ] **Step 8: Run existing IM tests to verify no regression**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/ -k "im" -v --timeout=60
```

Expected: All existing tests pass (the default `binding_mode="isolated"` preserves existing behavior).

- [ ] **Step 9: Commit**

```bash
git add cubeplex/im/types.py cubeplex/im/slack/connector.py cubeplex/im/feishu/connector.py cubeplex/im/discord/connector.py tests/unit/test_im_scope_key_selection.py
git commit -m "feat(im): scope key selection based on channel binding mode"
```

---

### Task 4: Shared-Mode Inbound — Topic, Conversation & Participant Lifecycle

**Files:**
- Modify: `backend/cubeplex/im/inbound.py:72` (`ingest_inbound_event`)
- Create: `backend/tests/e2e/test_im_shared_mode_ingest.py`

This is the core task. `ingest_inbound_event` must:
1. Look up the channel binding for `(account_id, channel_id)`.
2. If binding is `shared` and no `topic_id` yet → create Topic + TopicParticipant + Conversation + ConversationParticipant within the transaction.
3. If binding is `shared` and `topic_id` exists → auto-join sender as TopicParticipant + ConversationParticipant.
4. Pass the binding mode to the connector's `parse_inbound` (done by callers — gateway/webhook handlers).

- [ ] **Step 1: Write the E2E test**

Create `backend/tests/e2e/test_im_shared_mode_ingest.py`:

```python
"""E2E tests for shared-mode inbound topic/conversation lifecycle."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.im.inbound import IngestResult, ingest_inbound_event
from cubeplex.im.types import InboundEvent, make_channel_scope, make_thread_scope
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_participant import ConversationParticipant
from cubeplex.models.im_channel_binding import IMChannelBinding
from cubeplex.models.im_connector import IMConnectorAccount, IMThreadLink
from cubeplex.models.topic import Topic, TopicParticipant


@pytest.mark.asyncio
async def test_first_shared_message_creates_topic(
    db_session_maker,
    shared_binding: IMChannelBinding,
    im_account: IMConnectorAccount,
) -> None:
    """First @bot in a shared-mode channel creates a topic and conversation."""
    event = InboundEvent(
        platform="slack",
        account_external_id=im_account.external_account_id,
        platform_event_id="evt_001",
        channel_id=shared_binding.channel_id,
        scope_key=make_channel_scope(),
        scope_kind="channel",
        reply_to_id="1234.5678",
        inbound_message_id="1234.5678",
        sender_ref="U_SENDER_1",
        sender_open_id="U_SENDER_1",
        text="hello bot",
    )
    result = await ingest_inbound_event(
        event,
        account=im_account,
        session_maker=db_session_maker,
    )
    assert result.outcome == "enqueued"
    assert result.conversation_id is not None

    # Verify topic was created and binding updated
    async with db_session_maker() as session:
        binding = (
            await session.execute(
                select(IMChannelBinding).where(
                    IMChannelBinding.id == shared_binding.id
                )
            )
        ).scalar_one()
        assert binding.topic_id is not None

        topic = (
            await session.execute(
                select(Topic).where(Topic.id == binding.topic_id)
            )
        ).scalar_one()
        assert topic.title == shared_binding.channel_name

        # Verify topic participant (acting_user as owner)
        participants = (
            await session.execute(
                select(TopicParticipant).where(
                    TopicParticipant.topic_id == topic.id
                )
            )
        ).scalars().all()
        assert len(participants) >= 1


@pytest.mark.asyncio
async def test_subsequent_shared_message_reuses_topic(
    db_session_maker,
    shared_binding_with_topic: IMChannelBinding,
    im_account: IMConnectorAccount,
) -> None:
    """Second @bot in the same channel reuses the existing topic."""
    event = InboundEvent(
        platform="slack",
        account_external_id=im_account.external_account_id,
        platform_event_id="evt_002",
        channel_id=shared_binding_with_topic.channel_id,
        scope_key=make_channel_scope(),
        scope_kind="channel",
        reply_to_id="2222.0000",
        inbound_message_id="2222.0000",
        sender_ref="U_SENDER_2",
        sender_open_id="U_SENDER_2",
        text="another message",
    )
    result = await ingest_inbound_event(
        event,
        account=im_account,
        session_maker=db_session_maker,
    )
    assert result.outcome == "enqueued"


@pytest.mark.asyncio
async def test_thread_creates_separate_conversation(
    db_session_maker,
    shared_binding_with_topic: IMChannelBinding,
    im_account: IMConnectorAccount,
) -> None:
    """Thread @bot creates a new conversation under the same topic."""
    thread_id = "3333.0000"
    event = InboundEvent(
        platform="slack",
        account_external_id=im_account.external_account_id,
        platform_event_id="evt_003",
        channel_id=shared_binding_with_topic.channel_id,
        scope_key=make_thread_scope(thread_id),
        scope_kind="thread",
        reply_to_id=thread_id,
        inbound_message_id="3333.1111",
        sender_ref="U_SENDER_1",
        sender_open_id="U_SENDER_1",
        text="thread message",
    )
    result = await ingest_inbound_event(
        event,
        account=im_account,
        session_maker=db_session_maker,
    )
    assert result.outcome == "enqueued"

    # Thread conversation is different from the channel-level one
    async with db_session_maker() as session:
        links = (
            await session.execute(
                select(IMThreadLink).where(
                    IMThreadLink.account_id == im_account.id,
                    IMThreadLink.channel_id == shared_binding_with_topic.channel_id,
                )
            )
        ).scalars().all()
        conv_ids = {link.conversation_id for link in links}
        assert len(conv_ids) >= 2  # channel-level + thread


@pytest.mark.asyncio
async def test_isolated_mode_no_topic(
    db_session_maker,
    isolated_binding: IMChannelBinding,
    im_account: IMConnectorAccount,
) -> None:
    """Isolated-mode binding does not create a topic."""
    event = InboundEvent(
        platform="slack",
        account_external_id=im_account.external_account_id,
        platform_event_id="evt_004",
        channel_id=isolated_binding.channel_id,
        scope_key="u:U_SENDER_1",
        scope_kind="channel",
        reply_to_id="4444.0000",
        inbound_message_id="4444.0000",
        sender_ref="U_SENDER_1",
        sender_open_id="U_SENDER_1",
        text="isolated message",
    )
    result = await ingest_inbound_event(
        event,
        account=im_account,
        session_maker=db_session_maker,
    )
    assert result.outcome == "enqueued"

    async with db_session_maker() as session:
        binding = (
            await session.execute(
                select(IMChannelBinding).where(
                    IMChannelBinding.id == isolated_binding.id
                )
            )
        ).scalar_one()
        assert binding.topic_id is None
```

Note: The fixtures `shared_binding`, `shared_binding_with_topic`, `isolated_binding`, `im_account`, and `db_session_maker` need to be created. They should set up the test database state: create an `IMConnectorAccount`, then `IMChannelBinding` rows with the appropriate modes. These can be conftest-local to the test file.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/e2e/test_im_shared_mode_ingest.py -v
```

Expected: FAIL — `ingest_inbound_event` doesn't handle shared mode yet.

- [ ] **Step 3: Modify ingest_inbound_event**

In `cubeplex/im/inbound.py`, the key changes:

1. Add imports at the top:
```python
from cubeplex.models.im_channel_binding import IMChannelBinding
from cubeplex.models.topic import Topic, TopicParticipant
from cubeplex.models.conversation_participant import ConversationParticipant
from cubeplex.repositories.im_channel_binding import IMChannelBindingRepository
```

2. After the identity resolution block (after line 145), add the binding lookup and shared-mode logic:

```python
        # Look up channel binding
        binding = (
            await session.execute(
                select(IMChannelBinding).where(
                    IMChannelBinding.account_id == account.id,  # type: ignore[arg-type]
                    IMChannelBinding.channel_id == event.channel_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()

        is_shared = binding is not None and binding.mode == "shared"
```

3. Modify `_make_conversation_id` to handle shared-mode topic creation. When `is_shared` is true and `binding.topic_id` is None, create the topic + participants in the same transaction:

```python
        async def _make_conversation_id() -> str:
            topic_id: str | None = None
            if is_shared and binding is not None:
                if binding.topic_id is None:
                    # First @bot in shared mode — create topic
                    topic = Topic(
                        org_id=account.org_id,
                        workspace_id=account.workspace_id,
                        creator_user_id=account.acting_user_id,
                        title=binding.channel_name or event.channel_id,
                        sandbox_mode=binding.sandbox_mode or "dedicated",
                        max_participants=100,
                    )
                    session.add(topic)
                    await session.flush()
                    # Owner = bot's acting_user
                    session.add(TopicParticipant(
                        topic_id=topic.id,
                        user_id=account.acting_user_id,
                        role="owner",
                    ))
                    # Sender as member (if different from acting_user)
                    if effective_user_id != account.acting_user_id:
                        session.add(TopicParticipant(
                            topic_id=topic.id,
                            user_id=effective_user_id,
                            role="member",
                        ))
                    await session.flush()
                    binding.topic_id = topic.id
                    session.add(binding)
                    topic_id = topic.id
                else:
                    topic_id = binding.topic_id
                    # Auto-join sender as topic participant
                    existing_tp = (
                        await session.execute(
                            select(TopicParticipant).where(
                                TopicParticipant.topic_id == topic_id,
                                TopicParticipant.user_id == effective_user_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing_tp is None:
                        session.add(TopicParticipant(
                            topic_id=topic_id,
                            user_id=effective_user_id,
                            role="member",
                        ))

            conv = Conversation(
                org_id=account.org_id,
                workspace_id=account.workspace_id,
                creator_user_id=effective_user_id,
                title=(event.text[:80] or "IM conversation"),
                topic_id=topic_id,
            )
            session.add(conv)
            await session.flush()

            # Auto-join sender as conversation participant
            if is_shared:
                session.add(ConversationParticipant(
                    org_id=account.org_id,
                    workspace_id=account.workspace_id,
                    conversation_id=conv.id,
                    user_id=effective_user_id,
                ))
                await session.flush()

            return conv.id
```

4. After `get_or_create_thread_link` returns an existing link (not created), auto-join the sender as conversation participant if shared mode:

```python
        link, created = await get_or_create_thread_link(...)

        # For existing shared-mode links, auto-join sender
        if not created and is_shared:
            existing_cp = (
                await session.execute(
                    select(ConversationParticipant).where(
                        ConversationParticipant.conversation_id == link.conversation_id,
                        ConversationParticipant.user_id == effective_user_id,
                    )
                )
            ).scalar_one_or_none()
            if existing_cp is None:
                session.add(ConversationParticipant(
                    org_id=account.org_id,
                    workspace_id=account.workspace_id,
                    conversation_id=link.conversation_id,
                    user_id=effective_user_id,
                ))
            # Also auto-join as topic participant
            if binding is not None and binding.topic_id is not None:
                existing_tp = (
                    await session.execute(
                        select(TopicParticipant).where(
                            TopicParticipant.topic_id == binding.topic_id,
                            TopicParticipant.user_id == effective_user_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing_tp is None:
                    session.add(TopicParticipant(
                        topic_id=binding.topic_id,
                        user_id=effective_user_id,
                        role="member",
                    ))
```

5. Update channel_name on the binding when we see an inbound event (keep it fresh):

```python
        if binding is not None and event.channel_id:
            # channel_name is updated lazily — no separate sync needed
            pass  # channel_name comes from the API create/update, not inbound
```

- [ ] **Step 4: Run E2E tests**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/e2e/test_im_shared_mode_ingest.py -v
```

Expected: All 4 tests pass.

- [ ] **Step 5: Run existing IM inbound tests to verify no regression**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/e2e/ -k "im" -v --timeout=60
```

Expected: All pass (isolated-mode paths unchanged because `binding` is None for channels with no binding row).

- [ ] **Step 6: Commit**

```bash
git add cubeplex/im/inbound.py tests/e2e/test_im_shared_mode_ingest.py
git commit -m "feat(im): shared-mode topic/conversation lifecycle in ingest"
```

---

### Task 5: Worker Guard Relaxation

**Files:**
- Modify: `backend/cubeplex/im/worker.py:104-129`

- [ ] **Step 1: Understand the current guard**

Lines 104-129 of `worker.py` refuse to dispatch runs where the conversation has `topic_id IS NOT NULL` or `is_group_chat = True`. In shared mode, conversations have both set. We need to allow dispatch when the conversation's topic was created by an IM channel binding.

- [ ] **Step 2: Modify the guard**

Replace the guard block (lines 104-129) with:

```python
        from cubeplex.models.conversation import Conversation

        conv_row = (
            await session.execute(
                select(Conversation).where(
                    Conversation.id == item.conversation_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if conv_row is not None and (conv_row.topic_id is not None or conv_row.is_group_chat):
            # Check if this is an IM-bound shared-mode topic — those are allowed
            from cubeplex.models.im_channel_binding import IMChannelBinding

            im_bound = False
            if conv_row.topic_id is not None:
                im_bound = (
                    await session.execute(
                        select(IMChannelBinding.id).where(
                            IMChannelBinding.topic_id == conv_row.topic_id,  # type: ignore[arg-type]
                        )
                    )
                ).scalar_one_or_none() is not None

            if not im_bound:
                logger.warning(
                    "[IM worker] refusing to dispatch run for queue item {} — "
                    "conversation {} is a topic / group chat (v1 scope)",
                    item.id,
                    item.conversation_id,
                )
                await mark_queue_item_completed(session, item_id=item.id)
                await mark_receipt_failed(session, receipt_id=item.receipt_id)
                await session.commit()
                return True
```

- [ ] **Step 3: Run existing IM tests**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/ -k "im" -v --timeout=60
```

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add cubeplex/im/worker.py
git commit -m "feat(im): relax worker guard for shared-mode IM topic conversations"
```

---

### Task 6: Resume Guard Relaxation

**Files:**
- Modify: `backend/cubeplex/im/resume.py:101-109`

- [ ] **Step 1: Modify the resume guard**

Replace the guard block at lines 101-109 with the same IM-binding check pattern:

```python
    if topic_id is not None or is_group_chat:
        from sqlalchemy import select
        from cubeplex.db.engine import async_session_maker as _sm
        from cubeplex.models.im_channel_binding import IMChannelBinding

        im_bound = False
        if topic_id is not None:
            async with _sm() as session:
                im_bound = (
                    await session.execute(
                        select(IMChannelBinding.id).where(
                            IMChannelBinding.topic_id == topic_id,  # type: ignore[arg-type]
                        )
                    )
                ).scalar_one_or_none() is not None

        if not im_bound:
            logger.warning(
                "[resume] refusing IM resume for topic / group-chat conversation {} "
                "(run_id={}) — topic-aware IM resume not implemented (v1 scope)",
                conversation_id,
                run_id,
            )
            return False
```

- [ ] **Step 2: Run tests**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/ -k "im" -v --timeout=60
```

Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add cubeplex/im/resume.py
git commit -m "feat(im): relax resume guard for shared-mode IM topic conversations"
```

---

### Task 7: HITL — Skip awaiting_responder in Shared Mode

**Files:**
- Modify: `backend/cubeplex/im/outbound.py:476-525` (`maybe_register_awaiting_responder`)

The `awaiting_responder` Redis gate locks HITL to one `open_id`. In shared mode, any channel participant should be able to answer.

- [ ] **Step 1: Add shared_mode flag to OutboundRunTailer**

The tailer needs to know whether the run is shared-mode. Add a `shared_mode: bool = False` parameter to `OutboundRunTailer.__init__` and store it as `self._shared_mode`.

Find the `__init__` method in `OutboundRunTailer` class and add the parameter:

```python
def __init__(
    self,
    *,
    # ... existing params ...
    shared_mode: bool = False,
) -> None:
    # ... existing assignments ...
    self._shared_mode = shared_mode
```

- [ ] **Step 2: Skip awaiting_responder registration in shared mode**

In `maybe_register_awaiting_responder` (around line 476), add an early return:

```python
async def maybe_register_awaiting_responder(self, *, ev_payload: dict[str, Any]) -> None:
    # In shared mode, any channel participant can answer — skip the per-user gate
    if self._shared_mode:
        return
    # ... rest of existing implementation ...
```

- [ ] **Step 3: Wire shared_mode when creating the tailer**

Find where `OutboundRunTailer` is instantiated for IM runs (this is in the gateway/worker code that spawns the tailer). Pass `shared_mode=True` when the conversation's topic is IM-bound. This requires looking up the `IMChannelBinding` from the queue item's `channel_id` and `account_id`.

Search for the tailer construction site:
```bash
grep -rn "OutboundRunTailer(" cubeplex/im/
```

At each construction site, add logic to determine `shared_mode`:

```python
# Determine if this run is in shared mode
from cubeplex.models.im_channel_binding import IMChannelBinding
binding = (
    await session.execute(
        select(IMChannelBinding).where(
            IMChannelBinding.account_id == queue_item.account_id,
            IMChannelBinding.channel_id == queue_item.channel_id,
            IMChannelBinding.mode == "shared",
        )
    )
).scalar_one_or_none()
shared_mode = binding is not None
```

Pass `shared_mode=shared_mode` to the `OutboundRunTailer` constructor.

- [ ] **Step 4: Run tests**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/ -k "im" -v --timeout=60
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add cubeplex/im/outbound.py
git commit -m "feat(im): skip awaiting_responder gate in shared mode"
```

---

### Task 8: Channel Binding API Routes

**Files:**
- Create: `backend/cubeplex/api/schemas/im_channel_binding.py`
- Modify: `backend/cubeplex/api/routes/v1/ws_im.py`

- [ ] **Step 1: Create Pydantic schemas**

Create `cubeplex/api/schemas/im_channel_binding.py`:

```python
"""Pydantic schemas for IM channel binding CRUD."""

from pydantic import BaseModel, Field


class ChannelBindingCreateIn(BaseModel):
    channel_id: str = Field(min_length=1, max_length=128)
    channel_name: str = Field(default="", max_length=255)
    mode: str = Field(default="isolated", pattern="^(isolated|shared)$")
    sandbox_mode: str | None = Field(
        default=None, pattern="^(dedicated|creator)$"
    )


class ChannelBindingUpdateIn(BaseModel):
    mode: str | None = Field(default=None, pattern="^(isolated|shared)$")
    sandbox_mode: str | None = Field(
        default=None, pattern="^(dedicated|creator)$"
    )
    channel_name: str | None = Field(default=None, max_length=255)


class ChannelBindingOut(BaseModel):
    id: str
    account_id: str
    channel_id: str
    channel_name: str
    mode: str
    sandbox_mode: str | None
    topic_id: str | None
    created_at: str
    updated_at: str


class ChannelBindingListOut(BaseModel):
    bindings: list[ChannelBindingOut]
```

- [ ] **Step 2: Add CRUD routes to ws_im.py**

In `cubeplex/api/routes/v1/ws_im.py`, add the channel binding routes:

```python
from cubeplex.api.schemas.im_channel_binding import (
    ChannelBindingCreateIn,
    ChannelBindingListOut,
    ChannelBindingOut,
    ChannelBindingUpdateIn,
)
from cubeplex.models.im_channel_binding import IMChannelBinding
from cubeplex.repositories.im_channel_binding import IMChannelBindingRepository
from cubeplex.utils.time import utc_isoformat


def _binding_to_out(b: IMChannelBinding) -> ChannelBindingOut:
    return ChannelBindingOut(
        id=b.id,
        account_id=b.account_id,
        channel_id=b.channel_id,
        channel_name=b.channel_name,
        mode=b.mode,
        sandbox_mode=b.sandbox_mode,
        topic_id=b.topic_id,
        created_at=utc_isoformat(b.created_at),
        updated_at=utc_isoformat(b.updated_at),
    )


@router.get(
    "/accounts/{account_id}/channel-bindings",
    response_model=ChannelBindingListOut,
)
async def list_channel_bindings(
    workspace_id: str,
    account_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChannelBindingListOut:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    repo = IMChannelBindingRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )
    bindings = await repo.list_by_account(account_id=account_id)
    return ChannelBindingListOut(bindings=[_binding_to_out(b) for b in bindings])


@router.post(
    "/accounts/{account_id}/channel-bindings",
    status_code=status.HTTP_201_CREATED,
    response_model=ChannelBindingOut,
)
async def create_channel_binding(
    workspace_id: str,
    account_id: str,
    body: ChannelBindingCreateIn,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChannelBindingOut:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    if body.mode == "shared" and body.sandbox_mode is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sandbox_mode is required when mode is shared",
        )
    repo = IMChannelBindingRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )
    try:
        binding = await repo.create(
            account_id=account_id,
            channel_id=body.channel_id,
            channel_name=body.channel_name,
            mode=body.mode,
            sandbox_mode=body.sandbox_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return _binding_to_out(binding)


@router.patch(
    "/accounts/{account_id}/channel-bindings/{binding_id}",
    response_model=ChannelBindingOut,
)
async def update_channel_binding(
    workspace_id: str,
    account_id: str,
    binding_id: str,
    body: ChannelBindingUpdateIn,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChannelBindingOut:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    repo = IMChannelBindingRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )
    updated = await repo.update(
        binding_id=binding_id,
        mode=body.mode,
        sandbox_mode=body.sandbox_mode,
        channel_name=body.channel_name,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="binding not found")
    await session.commit()
    return _binding_to_out(updated)


@router.delete(
    "/accounts/{account_id}/channel-bindings/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_channel_binding(
    workspace_id: str,
    account_id: str,
    binding_id: str,
    ctx: Annotated[RequestContext, Depends(require_member)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    if workspace_id != ctx.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="workspace mismatch")
    repo = IMChannelBindingRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )
    deleted = await repo.delete(binding_id=binding_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="binding not found")
    await session.commit()
```

- [ ] **Step 3: Run mypy**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run mypy cubeplex/api/routes/v1/ws_im.py cubeplex/api/schemas/im_channel_binding.py cubeplex/repositories/im_channel_binding.py --strict
```

Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add cubeplex/api/schemas/im_channel_binding.py cubeplex/api/routes/v1/ws_im.py
git commit -m "feat(im): channel binding CRUD API routes"
```

---

### Task 9: Sandbox Resolution for Shared Mode

**Files:**
- Modify: The sandbox resolution path in the worker/run-context setup

The spec defines sandbox scoping:
- Shared + `dedicated` → `scope_type="topic"`, `scope_id=topic_id`
- Shared + `creator` → `scope_type="user"`, `scope_id=acting_user_id`
- DM / isolated → `scope_type="user"`, `scope_id=resolved_user_id` (unchanged)

- [ ] **Step 1: Find the sandbox resolution code**

```bash
grep -rn "scope_type\|scope_id\|SandboxScope\|sandbox_scope" cubeplex/im/worker.py cubeplex/streams/run_manager.py cubeplex/sandbox/ | head -30
```

The sandbox scope is determined in the `RunContext` passed to `start_run`. Look at how it's built in `worker.py` around lines 142-150.

- [ ] **Step 2: Add sandbox scope override for shared mode**

In `worker.py`, after the IM-binding check and before building `RunContext`, resolve the sandbox scope:

```python
        # Resolve sandbox scope for shared-mode IM
        sandbox_scope_type = "user"
        sandbox_scope_id = effective_user_id
        if conv_row is not None and conv_row.topic_id is not None and im_bound:
            binding_row = (
                await session.execute(
                    select(IMChannelBinding).where(
                        IMChannelBinding.topic_id == conv_row.topic_id,  # type: ignore[arg-type]
                    )
                )
            ).scalar_one_or_none()
            if binding_row is not None and binding_row.sandbox_mode == "dedicated":
                sandbox_scope_type = "topic"
                sandbox_scope_id = conv_row.topic_id
            elif binding_row is not None and binding_row.sandbox_mode == "creator":
                sandbox_scope_type = "user"
                sandbox_scope_id = account.acting_user_id
```

Then pass these to the `RunContext`. Check the `RunContext` dataclass to see if it accepts sandbox scope fields. If it does, use them. If not, the sandbox resolution may happen deeper in the stack — trace it and make the appropriate change.

- [ ] **Step 3: Run tests**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/e2e/test_sandbox_topic_isolation.py -v
```

Expected: Existing sandbox tests still pass.

- [ ] **Step 4: Commit**

```bash
git add cubeplex/im/worker.py
git commit -m "feat(im): sandbox scope resolution for shared-mode IM"
```

---

### Task 10: Gateway/Webhook Integration — Pass Binding Mode to Connectors

**Files:**
- The Slack/Feishu/Discord gateway or webhook handler files that call `connector.parse_inbound`

The connector `parse_inbound` methods now accept `binding_mode`, but the callers need to look up the binding and pass it.

- [ ] **Step 1: Find all parse_inbound call sites**

```bash
grep -rn "parse_inbound\b" cubeplex/im/ --include="*.py" | grep -v "def parse_inbound" | grep -v test
```

- [ ] **Step 2: At each call site, look up the binding mode**

For each call site where `parse_inbound` is called:
1. The gateway/webhook handler already has `account_id` available.
2. The raw event contains the `channel_id`.
3. Look up the binding: query `IMChannelBinding` for `(account_id, channel_id)`.
4. Pass `binding_mode=binding.mode if binding else "isolated"` to `parse_inbound`.

The binding lookup needs a database session. If the gateway handler doesn't have one, open a read-only session for the lookup.

For performance: the binding can be cached in-process per account (as noted in the spec). A simple dict keyed by `(account_id, channel_id)` with TTL or manual invalidation on CRUD is sufficient. For v1, a per-event DB lookup is acceptable — it's one indexed read.

- [ ] **Step 3: Run full IM test suite**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/ -k "im" -v --timeout=120
```

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add cubeplex/im/
git commit -m "feat(im): pass binding mode to connector parse_inbound at gateway/webhook sites"
```

---

### Frontend (separate plan)

The spec includes a "Channel Bindings" tab in the bot account detail panel. This plan covers backend only — the frontend plan should be written after the backend API is stable and testable. The API contract is defined in Task 8 (schemas + routes).

---

### Task 11: Pre-PR Full Test Sweep

- [ ] **Step 1: Run mypy on entire backend**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run mypy cubeplex/ --strict
```

- [ ] **Step 2: Run full test suite**

```bash
cd /home/chris/cubeplex/.worktrees/feat/2026-06-18-im-group-chat-mapping/backend
uv run pytest tests/ -v --timeout=120
```

- [ ] **Step 3: Fix any issues found**

- [ ] **Step 4: Final commit if needed**

```bash
git add -u
git commit -m "fix: address mypy and test issues from full sweep"
```
