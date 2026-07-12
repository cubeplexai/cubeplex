# Schedule & Trigger Destinations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `ScheduledTask` and `Trigger` so new-conversation runs can target a `Topic`, and so runs created from inside an IM channel post back to the same channel/scope (surviving `/new`) via live `IMThreadLink` resolution.

**Architecture:** Discriminator-based destinations on both rows (`target_mode` / `conversation_policy` gains a third value `im_channel`). IM dispatch resolves the live conversation via the same path as inbound messages (extracted into `resolve_im_conversation`); outbound reuses the existing `IMRunQueueItem` + worker hook pipeline by synthesizing an `IMWebhookReceipt` + queue item per fire. Topic targeting is a single optional FK column wired through to `ConversationRepository.create`.

**Tech Stack:** Python 3.13, FastAPI, SQLModel + Alembic, pydantic v2, cubepi (pinned), Next.js + React 19 frontend, Playwright.

**Spec:** `docs/dev/specs/2026-06-23-schedule-trigger-destinations-design.md`

---

## File Map

### Backend — created

- `backend/cubeplex/im/conversation_resolver.py` — shared `resolve_im_conversation` helper used by IM inbound + schedule/trigger dispatch.
- `backend/cubeplex/im/run_handoff.py` — `enqueue_im_channel_run` helper: writes a synthetic `IMWebhookReceipt` and an `IMRunQueueItem(status='pending')` so the existing `IMRunQueueWorker` picks them up. Does **not** call `RunManager.start_run` or the platform tailer hook — the worker owns those.
- `backend/cubeplex/services/schedule_target_spec.py` — `ScheduleTargetSpec.validate` pure function shared by Pydantic schemas + agent tools + service layer.
- `backend/cubeplex/tools/builtin/create_scheduled_task.py` — agent tool factory.
- `backend/cubeplex/tools/builtin/create_trigger.py` — agent tool factory.
- `backend/alembic/versions/<rev1>_add_destination_columns_to_scheduled_tasks.py`
- `backend/alembic/versions/<rev2>_add_destination_columns_to_triggers.py`
- `backend/tests/e2e/test_scheduled_task_destinations.py`
- `backend/tests/e2e/test_trigger_destinations.py`
- `backend/tests/unit/test_schedule_target_spec.py`
- `backend/tests/unit/test_resolve_im_conversation.py`

### Backend — modified

- `backend/cubeplex/models/scheduled_task.py` — add fields; widen `target_mode` literal.
- `backend/cubeplex/models/trigger.py` — same shape with `conversation_policy`.
- `backend/cubeplex/repositories/conversation.py:98` — add `topic_id` kwarg to `create`.
- `backend/cubeplex/im/inbound.py:218-250` — replace `_make_conversation_id` body with call to `resolve_im_conversation`.
- `backend/cubeplex/schedules/dispatch.py:56-139` — delete `NotImplementedError` at L71-77; wire `topic_id`; add `im_channel` branch.
- `backend/cubeplex/triggers/pipeline.py:37-151` — wire `topic_id`; add `im_channel` branch.
- `backend/cubeplex/api/schemas/ws_scheduled_tasks.py` — new fields + model_validator.
- `backend/cubeplex/api/schemas/trigger.py` — same.
- `backend/cubeplex/api/routes/v1/ws_scheduled_tasks.py:82,116-132` — accept new fields on create + patch; reject target_mode change.
- `backend/cubeplex/api/routes/v1/ws_triggers.py:99,195-240` — same; add list filter params.
- `backend/cubeplex/services/scheduled_task.py` — patch logic uses validator.
- `backend/cubeplex/services/trigger.py` — same.

### Frontend — modified

- `frontend/packages/core/src/types/scheduled-task.ts` — add `topic_id`, `im_account_id`, `im_channel_id`, `im_scope_key`; widen `target_mode`.
- `frontend/packages/core/src/types/trigger.ts` — same shape.
- `frontend/packages/core/src/api/scheduled-tasks.ts` — accept list filters.
- `frontend/packages/core/src/api/triggers.ts` — same.
- `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/components/ScheduledTaskFormDialog.tsx` — destination 3-radio + topic picker.
- `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/components/ScheduleEditor.tsx` — possibly touched if destination UX lives here.
- `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/lib/schedulePayload.ts` — payload mapping.
- `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/page.tsx` (list) — destination column.
- `frontend/packages/web/app/(app)/w/[wsId]/triggers/page.tsx` — destination 2-radio + topic picker + list column.
- `frontend/packages/web/app/(app)/w/[wsId]/triggers/[id]/page.tsx` — destination edit + im_channel read-only state.

### Frontend — tests

- `frontend/packages/web/tests/e2e/schedule-destination.spec.ts`
- `frontend/packages/web/tests/e2e/trigger-destination.spec.ts`

---

## Task 1 — Migration + model: `scheduled_tasks`

**Files:**
- Create: `backend/alembic/versions/<rev>_add_destination_columns_to_scheduled_tasks.py`
- Modify: `backend/cubeplex/models/scheduled_task.py:22-72`

- [ ] **Step 1: Update the SQLModel class**

```python
# backend/cubeplex/models/scheduled_task.py
TARGET_MODES = ("fixed", "new_each_run", "im_channel")

class ScheduledTask(CubeplexBase, OrgScopedMixin, table=True):
    # ...existing fields...
    target_mode: Literal["fixed", "new_each_run", "im_channel"] = Field(
        default="new_each_run",
        sa_column=Column(String(20), nullable=False),
    )
    target_conversation_id: str | None = Field(
        default=None, foreign_key="conversations.id", max_length=20, nullable=True,
    )
    topic_id: str | None = Field(
        default=None, foreign_key="topics.id", max_length=20, nullable=True,
        sa_column_kwargs={"index": True},
    )
    im_account_id: str | None = Field(
        default=None, foreign_key="im_connector_accounts.id", max_length=20, nullable=True,
    )
    im_channel_id: str | None = Field(default=None, max_length=128, nullable=True)
    im_scope_key: str | None = Field(default=None, max_length=255, nullable=True)
    im_scope_kind: str | None = Field(default=None, max_length=32, nullable=True)
```

`im_scope_kind` is stored alongside `im_scope_key` because the kind
cannot be recovered by parsing the key prefix (connectors emit
`scope_kind ∈ {"dm", "channel", "thread", "group", "participant",
"thread_participant"}` for overlapping key shapes). The agent tool
that creates the schedule reads both fields off `IMThreadLink`, so the
real value is always available at write time.

Add ON DELETE behavior via `sa_column=Column(...)` if SQLModel's `Field(foreign_key=...)` shortcut doesn't expose it; cross-check with how `Conversation.topic_id` does it at `models/conversation.py:40`.

- [ ] **Step 2: Generate the migration**

```bash
cd backend && uv run alembic revision --autogenerate \
  -m "add destination columns to scheduled_tasks"
```

- [ ] **Step 3: Hand-edit the migration**

Autogen will produce `add_column` calls. Hand-add the constraints +
indexes. **Do not** emit `op.drop_constraint("ck_scheduled_tasks_target_mode", ...)`
— no such constraint exists today; the drop would crash the migration.

```python
# Value-space CHECK on the discriminator (created fresh, never dropped).
op.create_check_constraint(
    "ck_scheduled_tasks_target_mode",
    "scheduled_tasks",
    "target_mode IN ('fixed', 'new_each_run', 'im_channel')",
)

# Minimal shape CHECK (matches spec §Constraints)
op.create_check_constraint(
    "ck_scheduled_tasks_target_shape",
    "scheduled_tasks",
    """
    (target_mode = 'fixed'       AND target_conversation_id IS NOT NULL
                                 AND im_account_id IS NULL)
 OR (target_mode = 'new_each_run' AND target_conversation_id IS NULL
                                  AND im_account_id IS NULL)
 OR (target_mode = 'im_channel'   AND target_conversation_id IS NULL)
    """,
)

op.create_index(
    "ix_scheduled_tasks_im_channel",
    "scheduled_tasks",
    ["im_account_id", "im_channel_id"],
)
```

Set FK `ondelete="SET NULL"` on `topic_id` and `im_account_id`.

- [ ] **Step 4: Apply and verify**

```bash
cd backend && uv run alembic upgrade head 2>&1 | tee ../tmp/migrate-stask.log | tail -10
```

Expected: `Running upgrade ... -> <rev>` with no errors.

- [ ] **Step 5: Verify column shape**

```bash
psql cubeplex_feat_2026_06_23_schedule_trigger_destinations \
  -c "\d scheduled_tasks" | tee ../tmp/stask-schema.log | tail -40
```

Expected output includes the four new columns and both new check constraints.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/models/scheduled_task.py backend/alembic/versions/*_add_destination_columns_to_scheduled_tasks.py
git commit -m "feat(schedules): add topic_id and im_channel destination columns"
```

---

## Task 2 — Migration + model: `triggers`

**Files:**
- Create: `backend/alembic/versions/<rev>_add_destination_columns_to_triggers.py`
- Modify: `backend/cubeplex/models/trigger.py:13-72`

- [ ] **Step 1: Update the SQLModel class**

```python
# backend/cubeplex/models/trigger.py
class Trigger(CubeplexBase, OrgScopedMixin, table=True):
    # ...existing fields...
    conversation_policy: Literal["new_each_time", "im_channel"] = Field(
        default="new_each_time",
        sa_column=Column(String(20), nullable=False),
    )
    topic_id: str | None = Field(
        default=None, foreign_key="topics.id", max_length=20, nullable=True,
        sa_column_kwargs={"index": True},
    )
    im_account_id: str | None = Field(
        default=None, foreign_key="im_connector_accounts.id", max_length=20, nullable=True,
    )
    im_channel_id: str | None = Field(default=None, max_length=128, nullable=True)
    im_scope_key: str | None = Field(default=None, max_length=255, nullable=True)
    im_scope_kind: str | None = Field(default=None, max_length=32, nullable=True)
```

- [ ] **Step 2: Generate the migration**

Task 1's migration must already be applied (via Task 1 Step 4) before
running this. Autogen will then chain `down_revision` to T1's
revision, keeping a linear history. If you skip T1's `upgrade head`,
both migrations end up rooted at the same prior head and you have to
hand-edit `down_revision` to recover.

```bash
cd backend && uv run alembic revision --autogenerate \
  -m "add destination columns to triggers"
```

- [ ] **Step 3: Hand-edit the migration**

No `drop_constraint` — `triggers` has no existing CHECK on
`conversation_policy` today.

```python
op.create_check_constraint(
    "ck_triggers_conversation_policy",
    "triggers",
    "conversation_policy IN ('new_each_time', 'im_channel')",
)

op.create_check_constraint(
    "ck_triggers_target_shape",
    "triggers",
    """
    (conversation_policy = 'new_each_time' AND im_account_id IS NULL)
 OR (conversation_policy = 'im_channel')
    """,
)

op.create_index(
    "ix_triggers_im_channel",
    "triggers",
    ["im_account_id", "im_channel_id"],
)
```

- [ ] **Step 4: Apply and verify**

```bash
cd backend && uv run alembic upgrade head 2>&1 | tee ../tmp/migrate-trig.log | tail -10
psql cubeplex_feat_2026_06_23_schedule_trigger_destinations -c "\d triggers" | tail -40
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/models/trigger.py backend/alembic/versions/*_add_destination_columns_to_triggers.py
git commit -m "feat(triggers): add topic_id and im_channel destination columns"
```

---

## Task 3 — `ConversationRepository.create` accepts `topic_id`

**Files:**
- Modify: `backend/cubeplex/repositories/conversation.py:98`
- Test: `backend/tests/e2e/test_conversation_repository.py` (existing file)

- [ ] **Step 1: Add the failing test**

Add to `backend/tests/e2e/test_conversation_repository.py`:

```python
async def test_create_with_topic_id(async_session: AsyncSession,
                                    seeded_workspace, seeded_topic):
    repo = ConversationRepository(
        async_session,
        org_id=seeded_workspace.org_id,
        workspace_id=seeded_workspace.id,
        user_id=seeded_workspace.creator_user_id,
    )
    conv = await repo.create(title="Test", topic_id=seeded_topic.id)
    assert conv.topic_id == seeded_topic.id
```

- [ ] **Step 2: Run, expect TypeError or AttributeError**

```bash
cd backend && uv run pytest tests/e2e/test_conversation_repository.py::test_create_with_topic_id --no-cov -x 2>&1 | tail -10
```

Expected: FAIL with `unexpected keyword argument 'topic_id'`.

- [ ] **Step 3: Implement**

```python
# backend/cubeplex/repositories/conversation.py:98
async def create(
    self,
    title: str,
    *,
    draft: bool = False,
    topic_id: str | None = None,
) -> Conversation:
    conv = Conversation(
        title=title,
        org_id=self.org_id,
        workspace_id=self.workspace_id,
        creator_user_id=self.user_id,
        has_messages=not draft,
        topic_id=topic_id,
    )
    return await self.add(conv)
```

- [ ] **Step 4: Run, expect pass**

```bash
cd backend && uv run pytest tests/e2e/test_conversation_repository.py::test_create_with_topic_id --no-cov -x 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/repositories/conversation.py backend/tests/e2e/test_conversation_repository.py
git commit -m "feat(conversations): accept topic_id on repository.create"
```

---

## Task 4 — Extract `resolve_im_conversation` helper

**Files:**
- Create: `backend/cubeplex/im/conversation_resolver.py`
- Modify: `backend/cubeplex/im/inbound.py:218-250`
- Test: `backend/tests/unit/test_resolve_im_conversation.py`

- [ ] **Step 1: Write the failing unit test (mocked DB)**

```python
# backend/tests/unit/test_resolve_im_conversation.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from cubeplex.im.conversation_resolver import resolve_im_conversation

@pytest.mark.asyncio
async def test_reuses_link_when_present():
    session = MagicMock()
    account = MagicMock(id="imac_1", org_id="org_1", workspace_id="ws_1",
                        platform="slack")
    binding = None  # no binding row
    link = MagicMock(conversation_id="conv_1")
    link.conversation.deleted_at = None

    with mock_threadlink_lookup(returning=(link, False)):
        conv_id = await resolve_im_conversation(
            session, account, channel_id="C1", scope_key="dm",
            effective_user_id="user_1", origin="schedule",
        )
    assert conv_id == "conv_1"
```

Two more tests: `test_creates_fresh_when_link_missing`, `test_mints_new_conv_when_underlying_deleted`.

- [ ] **Step 2: Run, expect ImportError**

```bash
cd backend && uv run pytest tests/unit/test_resolve_im_conversation.py --no-cov -x 2>&1 | tail -10
```

- [ ] **Step 3: Implement the helper (full inbound side-effect set)**

The helper must replicate everything `im/inbound.py:160-292` does for a
real inbound message — lazy topic creation, TopicParticipant inserts
(owner + member), ConversationParticipant insert during conv mint, and
the post-link participant top-up. It also returns binding-derived
fields (`topic_id`, `is_group_chat`, `sandbox_mode`) so callers can
build a correct `RunContext`.

```python
# backend/cubeplex/im/conversation_resolver.py
from dataclasses import dataclass
from typing import Literal
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from cubeplex.models.conversation import Conversation, ConversationParticipant
from cubeplex.models.im_channel_binding import IMChannelBinding
from cubeplex.models.im_connector import IMConnectorAccount
from cubeplex.models.topic import Topic, TopicParticipant
from cubeplex.repositories.im_connector import get_or_create_thread_link


@dataclass(frozen=True)
class ResolvedIMConversation:
    conversation_id: str
    topic_id: str | None
    is_group_chat: bool
    sandbox_mode: str | None


async def resolve_im_conversation(
    session: AsyncSession,
    account: IMConnectorAccount,
    *,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    effective_user_id: str,
    title_hint: str,
    origin: Literal["inbound", "schedule", "trigger"],
) -> ResolvedIMConversation:
    # 1. Look up binding.
    binding = (await session.execute(
        select(IMChannelBinding).where(
            IMChannelBinding.account_id == account.id,
            IMChannelBinding.channel_id == channel_id,
        )
    )).scalar_one_or_none()

    is_shared = binding is not None and binding.mode == "shared"
    topic_id: str | None = None
    sandbox_mode = binding.sandbox_mode if binding is not None else None

    # 2-3. Shared-mode topic and participant bookkeeping
    #      (mirrors inbound.py:162-216 verbatim).
    if is_shared:
        assert binding is not None
        if binding.topic_id is None:
            topic = Topic(
                org_id=account.org_id,
                workspace_id=account.workspace_id,
                creator_user_id=account.acting_user_id,
                title=binding.channel_name or channel_id,
                sandbox_mode=binding.sandbox_mode or "dedicated",
                max_participants=100,
            )
            session.add(topic)
            await session.flush()
            binding.topic_id = topic.id
            session.add(binding)
            session.add(TopicParticipant(
                topic_id=topic.id,
                user_id=account.acting_user_id,
                role="owner",
            ))
            if effective_user_id != account.acting_user_id:
                session.add(TopicParticipant(
                    topic_id=topic.id,
                    user_id=effective_user_id,
                    role="member",
                ))
            await session.flush()
        else:
            existing_tp = (await session.execute(
                select(TopicParticipant).where(
                    TopicParticipant.topic_id == binding.topic_id,
                    TopicParticipant.user_id == effective_user_id,
                )
            )).scalar_one_or_none()
            if existing_tp is None:
                session.add(TopicParticipant(
                    topic_id=binding.topic_id,
                    user_id=effective_user_id,
                    role="member",
                ))
                await session.flush()
        topic_id = binding.topic_id

    # 4. Mint conversation via thread link.
    async def _mint_conversation_id() -> str:
        conv = Conversation(
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            creator_user_id=effective_user_id,
            title=title_hint[:80] or "IM conversation",
            topic_id=topic_id,
            is_group_chat=is_shared,
        )
        session.add(conv)
        await session.flush()
        if is_shared:
            session.add(ConversationParticipant(
                org_id=account.org_id,
                workspace_id=account.workspace_id,
                conversation_id=conv.id,
                user_id=effective_user_id,
            ))
            await session.flush()
        return conv.id

    link, created = await get_or_create_thread_link(
        session,
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        make_conversation_id=_mint_conversation_id,
    )

    # 5. Post-link participant top-up (mirrors inbound.py:252-292).
    if not created and is_shared:
        assert binding is not None
        existing_cp = (await session.execute(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == link.conversation_id,
                ConversationParticipant.user_id == effective_user_id,
            )
        )).scalar_one_or_none()
        if existing_cp is None:
            session.add(ConversationParticipant(
                org_id=account.org_id,
                workspace_id=account.workspace_id,
                conversation_id=link.conversation_id,
                user_id=effective_user_id,
            ))
        if binding.topic_id is not None:
            existing_tp = (await session.execute(
                select(TopicParticipant).where(
                    TopicParticipant.topic_id == binding.topic_id,
                    TopicParticipant.user_id == effective_user_id,
                )
            )).scalar_one_or_none()
            if existing_tp is None:
                session.add(TopicParticipant(
                    topic_id=binding.topic_id,
                    user_id=effective_user_id,
                    role="member",
                ))
        await session.flush()

    return ResolvedIMConversation(
        conversation_id=link.conversation_id,
        topic_id=topic_id,
        is_group_chat=is_shared,
        sandbox_mode=sandbox_mode,
    )
```

- [ ] **Step 4: Refactor `im/inbound.py:160-292`**

Replace the entire block (binding lookup → shared-mode bookkeeping →
`_make_conversation_id` → `get_or_create_thread_link` → post-link
top-up) with a single call to `resolve_im_conversation`. The
`IMRunQueueItem` / `IMWebhookReceipt` writes immediately after stay
exactly as they were (real inbound has a real `event.inbound_message_id`
and webhook event id, which the helper does not invent).

- [ ] **Step 5: Run unit + IM inbound tests**

```bash
cd backend && uv run pytest tests/unit/test_resolve_im_conversation.py tests/e2e/test_im_inbound_outbox.py --no-cov -x 2>&1 | tee ../tmp/resolve.log | tail -20
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/im/conversation_resolver.py backend/cubeplex/im/inbound.py backend/tests/unit/test_resolve_im_conversation.py
git commit -m "refactor(im): extract resolve_im_conversation for reuse by dispatchers"
```

---

## Task 5 — `ScheduleTargetSpec.validate` pure function

**Files:**
- Create: `backend/cubeplex/services/schedule_target_spec.py`
- Test: `backend/tests/unit/test_schedule_target_spec.py`

- [ ] **Step 1: Write the failing test matrix**

```python
# backend/tests/unit/test_schedule_target_spec.py
import pytest
from cubeplex.services.schedule_target_spec import (
    ScheduleTargetSpec, ScheduleTargetError,
)

# (target_mode, target_conv, topic, im_acct, im_ch, im_scope, im_kind, should_pass)
CASES = [
    ("fixed", "conv_1", None, None, None, None, None, True),
    ("fixed", None, None, None, None, None, None, False),                    # missing conv
    ("fixed", "conv_1", "top_1", None, None, None, None, False),             # topic forbidden
    ("fixed", "conv_1", None, "imac_1", "C", "dm", "dm", False),             # im forbidden
    ("new_each_run", None, None, None, None, None, None, True),
    ("new_each_run", None, "top_1", None, None, None, None, True),
    ("new_each_run", "conv_1", None, None, None, None, None, False),
    ("new_each_run", None, None, "imac_1", "C", "dm", "dm", False),
    ("im_channel", None, None, "imac_1", "C", "dm", "dm", True),
    ("im_channel", "conv_1", None, "imac_1", "C", "dm", "dm", False),
    ("im_channel", None, "top_1", "imac_1", "C", "dm", "dm", False),
    ("im_channel", None, None, None, "C", "dm", "dm", False),                # missing acct
    ("im_channel", None, None, "imac_1", "C", "dm", None, False),            # missing kind
]

@pytest.mark.parametrize("case", CASES)
def test_schedule_target_spec_matrix(case):
    target_mode, conv, topic, acct, ch, scope, kind, ok = case
    spec = ScheduleTargetSpec(
        target_mode=target_mode,
        target_conversation_id=conv,
        topic_id=topic,
        im_account_id=acct,
        im_channel_id=ch,
        im_scope_key=scope,
        im_scope_kind=kind,
    )
    if ok:
        spec.validate()
    else:
        with pytest.raises(ScheduleTargetError):
            spec.validate()
```

- [ ] **Step 2: Run, expect ImportError**

```bash
cd backend && uv run pytest tests/unit/test_schedule_target_spec.py --no-cov 2>&1 | tail -5
```

- [ ] **Step 3: Implement**

```python
# backend/cubeplex/services/schedule_target_spec.py
from dataclasses import dataclass
from typing import Literal

class ScheduleTargetError(ValueError):
    pass

TargetMode = Literal["fixed", "new_each_run", "im_channel"]
ConversationPolicy = Literal["new_each_time", "im_channel"]

@dataclass(frozen=True)
class ScheduleTargetSpec:
    target_mode: str
    target_conversation_id: str | None = None
    topic_id: str | None = None
    im_account_id: str | None = None
    im_channel_id: str | None = None
    im_scope_key: str | None = None
    im_scope_kind: str | None = None

    def validate(self) -> None:
        m = self.target_mode
        im_fields = (
            self.im_account_id, self.im_channel_id,
            self.im_scope_key, self.im_scope_kind,
        )
        if m == "fixed":
            if not self.target_conversation_id:
                raise ScheduleTargetError("target_conversation_id required for fixed")
            if self.topic_id:
                raise ScheduleTargetError("topic_id not allowed for fixed")
            if any(im_fields):
                raise ScheduleTargetError("im_* fields not allowed for fixed")
        elif m == "new_each_run":
            if self.target_conversation_id:
                raise ScheduleTargetError("target_conversation_id not allowed for new_each_run")
            if any(im_fields):
                raise ScheduleTargetError("im_* fields not allowed for new_each_run")
        elif m == "im_channel":
            if self.target_conversation_id:
                raise ScheduleTargetError("target_conversation_id not allowed for im_channel")
            if self.topic_id:
                raise ScheduleTargetError("topic_id not allowed for im_channel")
            if not all(im_fields):
                raise ScheduleTargetError(
                    "im_account_id, im_channel_id, im_scope_key, im_scope_kind "
                    "all required for im_channel"
                )
        else:
            raise ScheduleTargetError(f"unknown target_mode: {m!r}")
```

Provide an analogous `TriggerTargetSpec` in the same file (different value space on the discriminator field but otherwise identical rules; map `new_each_time` ↔ `new_each_run` semantically).

- [ ] **Step 4: Run, expect pass on the 12 parametrized cases**

```bash
cd backend && uv run pytest tests/unit/test_schedule_target_spec.py --no-cov 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/services/schedule_target_spec.py backend/tests/unit/test_schedule_target_spec.py
git commit -m "feat(schedules): add ScheduleTargetSpec validator shared by API and tools"
```

---

## Task 6 — Pydantic schema updates: `ws_scheduled_tasks.py`

**Files:**
- Modify: `backend/cubeplex/api/schemas/ws_scheduled_tasks.py`

- [ ] **Step 1: Update create / patch request models**

```python
# backend/cubeplex/api/schemas/ws_scheduled_tasks.py
TargetMode = Literal["fixed", "new_each_run", "im_channel"]

class ScheduledTaskCreateRequest(BaseModel):
    # ...existing fields...
    target_mode: TargetMode
    target_conversation_id: str | None = None
    topic_id: str | None = None
    im_account_id: str | None = None
    im_channel_id: str | None = None
    im_scope_key: str | None = None
    im_scope_kind: str | None = None

    @model_validator(mode="after")
    def _validate_target(self):
        ScheduleTargetSpec(
            target_mode=self.target_mode,
            target_conversation_id=self.target_conversation_id,
            topic_id=self.topic_id,
            im_account_id=self.im_account_id,
            im_channel_id=self.im_channel_id,
            im_scope_key=self.im_scope_key,
            im_scope_kind=self.im_scope_kind,
        ).validate()
        return self


class ScheduledTaskPatchRequest(BaseModel):
    # All fields optional; reject target_mode at the route layer.
    prompt: str | None = None
    cron_expr: str | None = None
    interval_seconds: int | None = None
    run_at: datetime | None = None
    timezone: str | None = None
    topic_id: str | None = None
    # target_mode / target_conversation_id / im_* are NOT patchable
    # (they appear here only to reject with 422 if present)
    target_mode: TargetMode | None = None
```

- [ ] **Step 2: Add response field exposure**

`ScheduledTaskOut` (or whatever the response model is called) grows `topic_id`, `im_account_id`, `im_channel_id`, `im_scope_key`.

- [ ] **Step 3: Run schema tests if any exist; otherwise verify imports**

```bash
cd backend && uv run python -c "from cubeplex.api.schemas.ws_scheduled_tasks import ScheduledTaskCreateRequest" 2>&1
```

Expected: no traceback.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/api/schemas/ws_scheduled_tasks.py
git commit -m "feat(api): add destination fields to scheduled task schemas"
```

---

## Task 7 — Pydantic schema updates: `trigger.py`

Mirror Task 6 on `backend/cubeplex/api/schemas/trigger.py`. Discriminator is `conversation_policy` ∈ `{new_each_time, im_channel}`. Use `TriggerTargetSpec` from Task 5.

- [ ] **Steps 1-4:** Identical shape to Task 6.
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(api): add destination fields to trigger schemas"
```

---

## Task 8 — Schedule REST: accept new fields + reject mode change

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/ws_scheduled_tasks.py:82-132`
- Modify: `backend/cubeplex/services/scheduled_task.py` (update method)

- [ ] **Step 1: Add failing test for PATCH-mode-change rejection**

```python
# backend/tests/e2e/test_scheduled_task_destinations.py (new file)
async def test_patch_rejects_target_mode_change(authed_client, seeded_task):
    resp = await authed_client.patch(
        f"/api/v1/ws/{seeded_task.workspace_id}/scheduled-tasks/{seeded_task.id}",
        json={"target_mode": "im_channel"},
    )
    assert resp.status_code == 422
    assert "target_mode" in resp.text
```

- [ ] **Step 2: Run, expect either pass-with-wrong-shape (current behavior is silent ignore) or fail differently**

```bash
cd backend && uv run pytest tests/e2e/test_scheduled_task_destinations.py::test_patch_rejects_target_mode_change --no-cov -x 2>&1 | tail -5
```

- [ ] **Step 3: Implement**

In `routes/v1/ws_scheduled_tasks.py:patch_task`. Use
`body.model_fields_set` (or check membership in the
`exclude_unset=True` dump) — `is not None` would silently allow
explicit `null` payloads through.

```python
if "target_mode" in body.model_fields_set:
    raise HTTPException(
        status_code=422,
        detail="target_mode cannot be changed via PATCH; delete and recreate",
    )
```

Also reject any of `target_conversation_id`, `im_account_id`,
`im_channel_id`, `im_scope_key`, `im_scope_kind` in `model_fields_set`
— these are all mode-bound and cannot be changed independently.
`topic_id` is the only destination-related field PATCH may alter, and
only when the existing row has `target_mode='new_each_run'`.

And add list filter params:

```python
@router.get("")
async def list_tasks(
    workspace_id: str,
    topic_id: str | None = Query(None),
    im_account_id: str | None = Query(None),
    im_channel_id: str | None = Query(None),
    ...,
):
    ...
```

The service layer's `list` method grows matching filters.

- [ ] **Step 4: Run test, expect pass**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(api): scheduled-tasks accept destination fields, lock target_mode on PATCH"
```

---

## Task 9 — Trigger REST: same shape

Mirror Task 8 on `routes/v1/ws_triggers.py:99-240`. Add the same filter params, reject `conversation_policy` PATCH.

- [ ] **Steps 1-4:** Same shape as Task 8.
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(api): triggers accept destination fields, lock conversation_policy on PATCH"
```

---

## Task 10 — `enqueue_im_channel_run` helper

**Files:**
- Create: `backend/cubeplex/im/run_handoff.py`

The helper writes a synthetic `IMWebhookReceipt` and an
`IMRunQueueItem(status='pending')` and returns. It **does not** call
`RunManager.start_run` or `_on_run_started` — those run inside the
existing `IMRunQueueWorker` closure constructed in `runtime.py:173`,
which captures Redis state / gateway cache / secret cache / etc. that
the schedule/trigger dispatcher cannot reconstruct. Letting the worker
pick the row up means we reuse the entire correct outbound pipeline.

- [ ] **Step 1: Implement**

```python
# backend/cubeplex/im/run_handoff.py
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from cubeplex.models.im_connector import (
    IMConnectorAccount, IMIdentityLink, IMRunQueueItem, IMWebhookReceipt,
)


async def enqueue_im_channel_run(
    session: AsyncSession,
    *,
    account: IMConnectorAccount,
    conversation_id: str,
    content: str,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    owner_user_id: str,
    platform_event_id: str,
) -> None:
    """Enqueue a synthetic inbound row that the IMRunQueueWorker will drain.

    Idempotent on platform_event_id: callers should pass a deterministic
    key (e.g. f"schedule:{scheduled_task_run.id}") so that retried
    dispatcher ticks do not double-enqueue.
    """
    identity = (await session.execute(
        select(IMIdentityLink).where(
            IMIdentityLink.account_id == account.id,
            IMIdentityLink.user_id == owner_user_id,
        )
    )).scalar_one_or_none()
    sender_im_user_id = identity.im_user_id if identity is not None else None

    # Short-circuit if a prior tick already inserted the receipt.
    existing = (await session.execute(
        select(IMWebhookReceipt).where(
            IMWebhookReceipt.account_id == account.id,
            IMWebhookReceipt.platform_event_id == platform_event_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        # Receipt exists; the matching IMRunQueueItem is also already
        # present (both writes happen in the same transaction).
        return

    receipt = IMWebhookReceipt(
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        platform_event_id=platform_event_id,
        status="completed",
    )
    session.add(receipt)
    await session.flush()

    session.add(IMRunQueueItem(
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        receipt_id=receipt.id,
        conversation_id=conversation_id,
        content=content,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        reply_to_id=None,
        inbound_message_id=None,
        sender_im_user_id=sender_im_user_id,
        sender_open_id=None,
        status="pending",         # worker drains this
    ))
    await session.flush()
```

Verified facts:
- `IMRunQueueItem.status` default is `'pending'`; worker filters on
  `status='pending'` (`models/im_connector.py:181`, partial index
  `ix_im_run_queue_pending`).
- `_on_run_started` is a runtime-built closure
  (`im/runtime.py:173-207`) and **cannot** be reconstructed safely
  outside the lifespan startup path. The worker calls it after
  `start_run` (`im/worker.py:242-254`).
- The schedule/trigger dispatcher writes the queue item inside its own
  DB transaction; on `commit()` the worker's `claim_pending_queue_item`
  picks it up on the next poll tick (default 1 second).

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/im/run_handoff.py
git commit -m "feat(im): add enqueue_im_channel_run for synthetic outbound enqueueing"
```

---

## Task 11 — Schedule dispatch: wire topic + add im_channel branch

**Files:**
- Modify: `backend/cubeplex/schedules/dispatch.py:56-139`

- [ ] **Step 1: Delete the NotImplementedError block**

Remove `dispatch.py:71-77` (the `raise NotImplementedError("topic-aware schedule dispatch is not implemented in v1")` clause).

- [ ] **Step 2: Wire topic_id through `new_each_run`**

```python
# dispatch.py: in resolve_target() new_each_run branch
conv = await conv_repo.create(
    title=task.title or f"Scheduled: {task.id}",
    topic_id=task.topic_id,        # NEW
)
return conv
```

- [ ] **Step 3: Modify the schedule poller's `_dispatch_one` to short-circuit before pre-stamping for `im_channel`**

The existing `_dispatch_one` (`schedules/poller.py:224-313`) pre-stamps
a `run_id` on the `ScheduledTaskRun` row at line 247-249, commits, and
relies on `dispatch_scheduled_run` returning a `DispatchResult` whose
`conversation_id` is then back-filled. For `im_channel` mode, no real
`run_id` exists at dispatch time (the IM worker assigns one later),
and the agent run is not started inline. So `_dispatch_one` needs a
small detection branch:

```python
# poller.py:_dispatch_one, before line 247 (pre-stamp).
task = await session.get(ScheduledTask, row.scheduled_task_id)
# ... existing 'task gone' / 'task expired' checks ...

if task.target_mode == "im_channel":
    # IM-mode dispatch owns the row's terminal state itself.
    await dispatch_scheduled_run(
        task=task, run_manager=self._run_manager, run_row=row,
        session=session,
    )
    return

# Existing path (fixed / new_each_run):
pre_run_id = str(uuid7())
row.run_id = pre_run_id
await session.commit()
result = await dispatch_scheduled_run(...)
# ...existing post-dispatch UPDATE...
```

The IM-mode branch passes `row` and `session` into
`dispatch_scheduled_run` so the IM branch can mutate the row + commit
in the same transaction as the receipt + queue inserts. The existing
caller signature for `dispatch_scheduled_run` widens accordingly.

- [ ] **Step 4: Implement the `im_channel` branch inside `dispatch_scheduled_run`**

```python
# schedules/dispatch.py: dispatch_scheduled_run grows an early branch
# that uses the run_row + session passed in by the poller for the
# im_channel case.
if task.target_mode == "im_channel":
    account = await session.get(IMConnectorAccount, task.im_account_id)
    if account is None:
        run_row.state = "failed"
        run_row.detail = "im_account_unlinked"
        await session.commit()
        return
    resolved = await resolve_im_conversation(
        session, account,
        channel_id=task.im_channel_id,
        scope_key=task.im_scope_key,
        scope_kind=task.im_scope_kind,    # persisted column on the row
        effective_user_id=task.owner_user_id,
        title_hint=f"Scheduled: {task.prompt[:80]}",
        origin="schedule",
    )

    await enqueue_im_channel_run(
        session, account=account,
        conversation_id=resolved.conversation_id,
        content=task.prompt,
        channel_id=task.im_channel_id,
        scope_key=task.im_scope_key,
        scope_kind=task.im_scope_kind,
        owner_user_id=task.owner_user_id,
        platform_event_id=f"schedule:{run_row.id}",
    )

    # Fire-and-forget: handoff to IM worker has succeeded. The IM
    # worker owns end-to-end run lifecycle from here. Schedule does
    # not re-fire on worker failure (the user would see duplicate
    # messages in the channel) — operators debug via IM worker logs
    # and the IMRunQueueItem row.
    run_row.conversation_id = resolved.conversation_id
    run_row.state = "succeeded"
    run_row.detail = "im_channel_enqueued"
    await session.commit()
    return
```

No `_derive_scope_kind` helper is needed — the kind is persisted
alongside the key when the row is created (web UI cannot create
`im_channel` rows from scratch, and the agent tool reads kind + key
together off `IMThreadLink`).

- [ ] **Step 5: Run schedule tests (existing + new in later tasks)**

```bash
cd backend && uv run pytest tests/e2e/test_scheduled_tasks*.py --no-cov 2>&1 | tee ../tmp/sched-dispatch.log | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/schedules/dispatch.py backend/cubeplex/schedules/poller.py
git commit -m "feat(schedules): support topic_id and im_channel destinations in dispatcher"
```

---

## Task 12 — Trigger pipeline: wire topic + add im_channel branch

**Files:**
- Modify: `backend/cubeplex/triggers/pipeline.py:37-151`

The pipeline already always creates a new conv at `pipeline.py:102`;
add `topic_id=trigger.topic_id` there for the `new_each_time` path.
Then add the `im_channel` branch above the new-conv block. Use
`TriggerEvent.status` / `TriggerEvent.last_error` for failure (mirrors
existing `TriggerEvent` columns at `models/trigger.py:98,101`); use
`platform_event_id=f"trigger:{event_row.id}"` for idempotency.

- [ ] **Step 1: Wire `topic_id` into the new-conv path**

```python
# pipeline.py around line 102:
conv = await conv_repo.create(
    title=f"Triggered: {trigger.name}",
    draft=True,
    topic_id=trigger.topic_id,
)
```

- [ ] **Step 2: Add the `im_channel` branch**

```python
if trigger.conversation_policy == "im_channel":
    account = await session.get(IMConnectorAccount, trigger.im_account_id)
    if account is None:
        event_row.status = "failed"
        event_row.last_error = "im_account_unlinked"
        # _bump_counters(trigger, events_failed=1) — call the existing helper.
        await session.commit()
        return
    resolved = await resolve_im_conversation(
        session, account,
        channel_id=trigger.im_channel_id,
        scope_key=trigger.im_scope_key,
        scope_kind=trigger.im_scope_kind,        # persisted column
        effective_user_id=trigger.run_as_user_id,
        title_hint=f"Triggered: {trigger.name}",
        origin="trigger",
    )
    event_row.resulting_conversation_id = resolved.conversation_id

    await enqueue_im_channel_run(
        session, account=account,
        conversation_id=resolved.conversation_id,
        content=rendered_prompt,
        channel_id=trigger.im_channel_id,
        scope_key=trigger.im_scope_key,
        scope_kind=trigger.im_scope_kind,
        owner_user_id=trigger.run_as_user_id,
        platform_event_id=f"trigger:{event_row.id}",
    )

    # Fire-and-forget. Existing terminal status; bump events_success.
    event_row.status = "accepted"
    # _bump_counters(trigger, events_success=1) — call the existing
    # helper at `triggers/pipeline.py` (search for `_bump_counters`
    # and copy the call shape used by the `new_each_time` success
    # path).
    await session.commit()
    return
```

No new `TriggerEvent.status` value introduced — `'accepted'` is the
existing terminal-success state.

- [ ] **Step 3: Run trigger tests**

```bash
cd backend && uv run pytest tests/e2e/test_trigger*.py --no-cov 2>&1 | tail -20
```

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(triggers): support topic_id and im_channel destinations in pipeline"
```

---

## Task 13 — Agent tool: `create_scheduled_task`

**Files:**
- Create: `backend/cubeplex/tools/builtin/create_scheduled_task.py`
- Modify: agent factory wiring (search for `make_view_images_tool` to find the pattern)

- [ ] **Step 1: Implement the factory**

```python
# backend/cubeplex/tools/builtin/create_scheduled_task.py
from cubepi import tool

from cubeplex.models.im_connector import IMThreadLink
from cubeplex.services.scheduled_task import ScheduledTaskService
from cubeplex.services.schedule_target_spec import ScheduleTargetSpec


def make_create_scheduled_task_tool(
    *, session, org_id, workspace_id, user_id, conversation_id,
):
    @tool(name="create_scheduled_task", description="""
        Create a scheduled task that runs a prompt on a cron / interval / once schedule.
        By default, when called from inside an IM conversation, the result is posted
        back into that same IM channel and survives `/new`. When called outside IM,
        the schedule runs in the current conversation. Pass `target_mode='new_each_run'`
        to instead spin up a fresh conversation each run (optionally under a topic).
    """)
    async def create_scheduled_task(
        prompt: str,
        schedule_kind: str,                  # "cron" | "interval" | "once"
        cron_expr: str | None = None,
        interval_seconds: int | None = None,
        run_at: str | None = None,
        timezone: str | None = None,
        target_mode: str | None = None,      # auto-derived if None
        target_conversation_id: str | None = None,
        topic_id: str | None = None,
    ) -> dict:
        # Detect IM origin via IMThreadLink on current conversation.
        link = (await session.exec(
            select(IMThreadLink).where(IMThreadLink.conversation_id == conversation_id)
        )).first()

        # Derive defaults if target_mode not provided.
        if target_mode is None:
            if link is not None:
                target_mode = "im_channel"
                im_account_id = link.account_id
                im_channel_id = link.channel_id
                im_scope_key  = link.scope_key
                im_scope_kind = link.scope_kind
            else:
                target_mode = "fixed"
                target_conversation_id = conversation_id
                im_account_id = im_channel_id = im_scope_key = im_scope_kind = None
        elif target_mode == "im_channel":
            if link is None:
                raise ValueError("im_channel target requires IM origin")
            im_account_id = link.account_id
            im_channel_id = link.channel_id
            im_scope_key  = link.scope_key
            im_scope_kind = link.scope_kind
        else:
            im_account_id = im_channel_id = im_scope_key = im_scope_kind = None

        # If new_each_run + no explicit topic, inherit the current conv's topic.
        if target_mode == "new_each_run" and topic_id is None:
            current_conv = await session.get(Conversation, conversation_id)
            if current_conv and current_conv.topic_id:
                topic_id = current_conv.topic_id

        ScheduleTargetSpec(
            target_mode=target_mode,
            target_conversation_id=target_conversation_id,
            topic_id=topic_id,
            im_account_id=im_account_id,
            im_channel_id=im_channel_id,
            im_scope_key=im_scope_key,
            im_scope_kind=im_scope_kind,
        ).validate()

        svc = ScheduledTaskService(session, org_id, workspace_id, user_id)
        task = await svc.create(
            prompt=prompt,
            schedule_kind=schedule_kind,
            cron_expr=cron_expr, interval_seconds=interval_seconds, run_at=run_at,
            timezone=timezone,
            target_mode=target_mode,
            target_conversation_id=target_conversation_id,
            topic_id=topic_id,
            im_account_id=im_account_id,
            im_channel_id=im_channel_id,
            im_scope_key=im_scope_key,
            im_scope_kind=im_scope_kind,
        )
        return {"id": task.id, "target_mode": task.target_mode}

    return create_scheduled_task
```

- [ ] **Step 2: Wire into agent factory**

Find where the agent's tool list is assembled per conversation (search for `make_view_images_tool` callsite). Append `make_create_scheduled_task_tool(...)` with the same DI args.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(tools): add create_scheduled_task agent tool with auto IM-origin"
```

---

## Task 14 — Agent tool: `create_trigger`

Mirror Task 13 for triggers. Trigger has no "current conversation" fallback (triggers don't run inside a conversation — they're created from inside one but fire later in a fresh one), so the default derivation is simpler: IM origin ⇒ `im_channel`; otherwise `new_each_time`.

- [ ] **Step 1: Implement factory.**
- [ ] **Step 2: Wire into agent factory.**
- [ ] **Step 3: Commit.**

```bash
git commit -m "feat(tools): add create_trigger agent tool with auto IM-origin"
```

---

## Task 15 — Backend e2e: schedule + topic

**Files:**
- Modify: `backend/tests/e2e/test_scheduled_task_destinations.py`

- [ ] **Step 1: Write the test**

```python
async def test_new_each_run_with_topic_creates_conv_in_topic(
    authed_client, seeded_workspace, seeded_topic, fake_clock,
):
    # POST /scheduled-tasks  with target_mode=new_each_run, topic_id
    resp = await authed_client.post(
        f"/api/v1/ws/{seeded_workspace.id}/scheduled-tasks",
        json={
            "prompt": "Daily status check",
            "schedule_kind": "interval",
            "interval_seconds": 60,
            "target_mode": "new_each_run",
            "topic_id": seeded_topic.id,
        },
    )
    assert resp.status_code == 200
    task_id = resp.json()["id"]

    # Fire the schedule
    await dispatch_scheduled_run(session, await session.get(ScheduledTask, task_id))
    await session.commit()

    # Latest ScheduledTaskRun's conversation has topic_id == T
    run_row = (await session.exec(
        select(ScheduledTaskRun)
        .where(ScheduledTaskRun.scheduled_task_id == task_id)
        .order_by(ScheduledTaskRun.created_at.desc())
    )).first()
    conv = await session.get(Conversation, run_row.conversation_id)
    assert conv.topic_id == seeded_topic.id
```

- [ ] **Step 2: Run, expect pass given Tasks 3+6+8+11 are done**

```bash
cd backend && uv run pytest tests/e2e/test_scheduled_task_destinations.py::test_new_each_run_with_topic_creates_conv_in_topic --no-cov -x 2>&1 | tee ../tmp/t15.log | tail -10
```

- [ ] **Step 3: Commit**

---

## Task 16 — Backend e2e: schedule im_channel + IMThreadLink reuse / /new rotation

```python
async def test_im_channel_reuses_existing_link(seeded_im_account, ...):
    # Setup: write IMThreadLink with conv_id=C1.
    # Create schedule with target_mode=im_channel + (account, channel_id, scope_key).
    # Fire → assert ScheduledTaskRun.conversation_id == C1.

async def test_im_channel_creates_fresh_after_new(seeded_im_account, ...):
    # Same setup, but delete the IMThreadLink before firing.
    # Fire → assert new conv created and a new IMThreadLink points to it.
```

- [ ] **Steps as in Task 15.**
- [ ] **Commit.**

---

## Task 17 — Backend e2e: shared mode topic inheritance

```python
async def test_im_channel_shared_mode_inherits_binding_topic(...):
    # Setup IMChannelBinding(mode='shared', topic_id=T) but no IMThreadLink.
    # Fire schedule in im_channel mode → assert new conv has topic_id=T.
```

- [ ] **Steps and commit.**

---

## Task 18 — Backend e2e: im_account deletion + topic deletion + validation 422 suite

Three tests (or one parametrized) covering:

- `test_im_account_deletion_marks_run_failed`
- `test_topic_deletion_sets_topic_id_null_and_continues`
- `test_validation_rejects_im_channel_with_topic` (POST 422)
- `test_validation_rejects_fixed_without_conversation` (POST 422)

- [ ] **Steps and commit.**

---

## Task 19 — Backend e2e: list filters + trigger parity

```python
async def test_list_filter_by_topic_id(authed_client, seeded_workspace, ...): ...
async def test_list_filter_by_im_account_and_channel(...): ...
```

Trigger parity: copy Tasks 15-18 into `test_trigger_destinations.py`, replacing `target_mode` with `conversation_policy` and the schedule dispatcher call with `TriggerPipeline.fire`.

- [ ] **Steps and commit (two commits — filters separate from trigger parity).**

---

## Task 20 — Frontend types and API client updates

**Files:**
- Modify: `frontend/packages/core/src/types/scheduled-task.ts`
- Modify: `frontend/packages/core/src/types/trigger.ts`
- Modify: `frontend/packages/core/src/api/scheduled-tasks.ts`
- Modify: `frontend/packages/core/src/api/triggers.ts`

- [ ] **Step 1: Add new fields to TS types**

```ts
// frontend/packages/core/src/types/scheduled-task.ts
export type TargetMode = "fixed" | "new_each_run" | "im_channel";

export interface ScheduledTask {
  // ...existing fields...
  target_mode: TargetMode;
  target_conversation_id: string | null;
  topic_id: string | null;
  im_account_id: string | null;
  im_channel_id: string | null;
  im_scope_key: string | null;
  im_scope_kind: string | null;
}

export interface ScheduledTaskListFilters {
  topic_id?: string;
  im_account_id?: string;
  im_channel_id?: string;
}
```

- [ ] **Step 2: Build @cubeplex/core**

```bash
cd frontend/packages/core && pnpm build 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(core): add destination fields to scheduled-task and trigger types"
```

---

## Task 21 — Frontend: schedule form (destination radio + topic picker)

**Files:**
- Modify: `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/components/ScheduledTaskFormDialog.tsx`
- Modify: `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/components/ScheduleEditor.tsx`
- Modify: `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/lib/schedulePayload.ts`

- [ ] **Step 1: Replace single "target conversation" input with destination radio**

```tsx
<RadioGroup value={targetMode} onValueChange={setTargetMode}>
  <RadioGroupItem value="fixed" label="This conversation" />
  <RadioGroupItem value="new_each_run" label="New conversation each run" />
  <RadioGroupItem value="im_channel" label="IM channel" disabled
                  tooltip="Created from IM only" />
</RadioGroup>

{targetMode === "new_each_run" && (
  <TopicPicker value={topicId} onChange={setTopicId} clearable />
)}
```

- [ ] **Step 2: Update payload mapper** in `lib/schedulePayload.ts` to emit the new fields conditionally per mode.

- [ ] **Step 3: For im_channel rows opened in edit mode, render destination section read-only**

```tsx
const isReadOnlyDestination = task.target_mode === "im_channel";
{isReadOnlyDestination ? (
  <ReadOnlyImChannelDestination
    accountId={task.im_account_id!}
    channelId={task.im_channel_id!}
    scopeKey={task.im_scope_key!}
  />
) : (
  <DestinationRadio ... />
)}
```

PATCH payload omits destination fields entirely when read-only.

- [ ] **Step 4: Lint + typecheck**

```bash
cd frontend && pnpm lint 2>&1 | tee ../tmp/fe-lint.log | tail -5
pnpm -F web typecheck 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(web): schedule form destination 3-radio + topic picker"
```

---

## Task 22 — Frontend: schedule list destination column

**Files:**
- Modify: `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/page.tsx`
- Create: `frontend/packages/web/app/(app)/w/[wsId]/scheduled-tasks/components/DestinationCell.tsx`

- [ ] **Step 1: Implement `DestinationCell.tsx`**

```tsx
export function DestinationCell({ task }: { task: ScheduledTask }) {
  if (task.target_mode === "fixed") {
    return <ConversationChip id={task.target_conversation_id!} />;
  }
  if (task.target_mode === "new_each_run") {
    return task.topic_id
      ? <TopicChip id={task.topic_id} />
      : <span className="text-muted-foreground">New conversation</span>;
  }
  // im_channel
  return <IMChannelChip accountId={task.im_account_id!}
                        channelId={task.im_channel_id!} />;
}
```

- [ ] **Step 2: Add the column to the list table.**

- [ ] **Step 3: Lint + typecheck.**

- [ ] **Step 4: Commit.**

---

## Task 23 — Frontend: trigger form + list

Mirror Tasks 21+22 on `frontend/packages/web/app/(app)/w/[wsId]/triggers/page.tsx` (list) and `[id]/page.tsx` (detail). Trigger form is inline (no FormDialog); the radio has only two options (`new_each_time` + disabled `im_channel`).

- [ ] **Steps and commits.**

---

## Task 24 — Playwright e2e: schedule destination flows

**Files:**
- Create: `frontend/packages/web/tests/e2e/schedule-destination.spec.ts`

- [ ] **Step 1: Flow 1 — create schedule with topic**

```ts
test("creates schedule pinned to topic", async ({ page, workspace }) => {
  await page.goto(`/w/${workspace.id}/scheduled-tasks`);
  await page.getByRole("button", { name: "New scheduled task" }).click();

  await page.getByLabel("Prompt").fill("Daily check");
  await page.getByLabel("Cron").fill("0 9 * * *");

  await page.getByRole("radio", { name: "New conversation each run" }).click();
  await page.getByRole("combobox", { name: "Topic" }).click();
  await page.getByRole("option", { name: workspace.topics[0].title }).click();

  // Capture the POST body
  const [postReq] = await Promise.all([
    page.waitForRequest(r => r.url().endsWith("/scheduled-tasks") && r.method() === "POST"),
    page.getByRole("button", { name: "Create" }).click(),
  ]);
  const body = postReq.postDataJSON();
  expect(body.target_mode).toBe("new_each_run");
  expect(body.topic_id).toBe(workspace.topics[0].id);

  // List page shows the topic chip
  await expect(page.getByRole("row", { name: /Daily check/ })
                  .getByText(workspace.topics[0].title)).toBeVisible();
});
```

- [ ] **Step 2: Flow 2 — open an im_channel schedule (seeded), verify read-only**

```ts
test("im_channel schedule has read-only destination", async ({ page, workspace, db }) => {
  const task = await db.seedScheduledTask({
    workspace_id: workspace.id,
    target_mode: "im_channel",
    im_account_id: workspace.imAccount.id,
    im_channel_id: "C123",
    im_scope_key: "dm",
    prompt: "Daily IM ping",
    cron_expr: "0 9 * * *",
  });
  await page.goto(`/w/${workspace.id}/scheduled-tasks`);
  await page.getByRole("row", { name: /Daily IM ping/ }).click();

  // Destination block disabled
  await expect(page.getByRole("radio", { name: "This conversation" })).toBeDisabled();
  // Prompt still editable
  await page.getByLabel("Prompt").fill("Updated IM ping");

  const [patchReq] = await Promise.all([
    page.waitForRequest(r => r.method() === "PATCH" &&
                              r.url().endsWith(`/scheduled-tasks/${task.id}`)),
    page.getByRole("button", { name: "Save" }).click(),
  ]);
  const body = patchReq.postDataJSON();
  expect(body).toHaveProperty("prompt");
  expect(body).not.toHaveProperty("target_mode");
  expect(body).not.toHaveProperty("im_account_id");
});
```

- [ ] **Step 3: Run**

```bash
cd frontend && npx playwright test schedule-destination.spec.ts 2>&1 | tail -10
```

- [ ] **Step 4: Commit.**

---

## Final checklist (run before declaring done)

- [ ] `cd backend && uv run pytest tests/unit tests/e2e --no-cov 2>&1 | tee ../tmp/backend-full.log | tail -10` — green.
- [ ] `cd frontend && pnpm lint && pnpm -F web typecheck && pnpm -F core build` — green.
- [ ] Manually fire a real schedule from inside a Slack/Feishu/Discord conversation, observe the bot posts back into the channel.
- [ ] Manually run `/new` in IM, then wait for the schedule to fire; confirm the new conversation thread appears in the same channel.
- [ ] No orphan rows in `tmp/` accidentally committed (`git status` clean).

## Out of scope (do NOT do as part of this work)

- Topic detail page UI.
- IM channel admin page.
- `enabled / paused` field on schedules.
- Cleanup sweep for orphan schedules (`im_account_id IS NULL AND target_mode='im_channel'`).
- Web UI to create `im_channel` rows directly.
- Multi-destination (broadcast) schedules.
