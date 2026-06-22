# Schedule & Trigger Destinations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `ScheduledTask` and `Trigger` so new-conversation runs can target a `Topic`, and so runs created from inside an IM channel post back to the same channel/scope (surviving `/new`) via live `IMThreadLink` resolution.

**Architecture:** Discriminator-based destinations on both rows (`target_mode` / `conversation_policy` gains a third value `im_channel`). IM dispatch resolves the live conversation via the same path as inbound messages (extracted into `resolve_im_conversation`); outbound reuses the existing `IMRunQueueItem` + worker hook pipeline by synthesizing an `IMWebhookReceipt` + queue item per fire. Topic targeting is a single optional FK column wired through to `ConversationRepository.create`.

**Tech Stack:** Python 3.13, FastAPI, SQLModel + Alembic, pydantic v2, cubepi (pinned), Next.js + React 19 frontend, Playwright.

**Spec:** `docs/dev/specs/2026-06-23-schedule-trigger-destinations-design.md`

---

## File Map

### Backend — created

- `backend/cubebox/im/conversation_resolver.py` — shared `resolve_im_conversation` helper used by IM inbound + schedule/trigger dispatch.
- `backend/cubebox/im/run_handoff.py` — `dispatch_im_channel_run` helper: writes synthetic `IMWebhookReceipt` + `IMRunQueueItem`, calls `RunManager.start_run`, invokes per-platform `on_run_started`.
- `backend/cubebox/services/schedule_target_spec.py` — `ScheduleTargetSpec.validate` pure function shared by Pydantic schemas + agent tools + service layer.
- `backend/cubebox/tools/builtin/create_scheduled_task.py` — agent tool factory.
- `backend/cubebox/tools/builtin/create_trigger.py` — agent tool factory.
- `backend/alembic/versions/<rev1>_add_destination_columns_to_scheduled_tasks.py`
- `backend/alembic/versions/<rev2>_add_destination_columns_to_triggers.py`
- `backend/tests/e2e/test_scheduled_task_destinations.py`
- `backend/tests/e2e/test_trigger_destinations.py`
- `backend/tests/unit/test_schedule_target_spec.py`
- `backend/tests/unit/test_resolve_im_conversation.py`

### Backend — modified

- `backend/cubebox/models/scheduled_task.py` — add fields; widen `target_mode` literal.
- `backend/cubebox/models/trigger.py` — same shape with `conversation_policy`.
- `backend/cubebox/repositories/conversation.py:98` — add `topic_id` kwarg to `create`.
- `backend/cubebox/im/inbound.py:218-250` — replace `_make_conversation_id` body with call to `resolve_im_conversation`.
- `backend/cubebox/schedules/dispatch.py:56-139` — delete `NotImplementedError` at L71-77; wire `topic_id`; add `im_channel` branch.
- `backend/cubebox/triggers/pipeline.py:37-151` — wire `topic_id`; add `im_channel` branch.
- `backend/cubebox/api/schemas/ws_scheduled_tasks.py` — new fields + model_validator.
- `backend/cubebox/api/schemas/trigger.py` — same.
- `backend/cubebox/api/routes/v1/ws_scheduled_tasks.py:82,116-132` — accept new fields on create + patch; reject target_mode change.
- `backend/cubebox/api/routes/v1/ws_triggers.py:99,195-240` — same; add list filter params.
- `backend/cubebox/services/scheduled_task.py` — patch logic uses validator.
- `backend/cubebox/services/trigger.py` — same.

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
- Modify: `backend/cubebox/models/scheduled_task.py:22-72`

- [ ] **Step 1: Update the SQLModel class**

```python
# backend/cubebox/models/scheduled_task.py
TARGET_MODES = ("fixed", "new_each_run", "im_channel")

class ScheduledTask(CubeboxBase, OrgScopedMixin, table=True):
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
```

Add ON DELETE behavior via `sa_column=Column(...)` if SQLModel's `Field(foreign_key=...)` shortcut doesn't expose it; cross-check with how `Conversation.topic_id` does it at `models/conversation.py:40`.

- [ ] **Step 2: Generate the migration**

```bash
cd backend && uv run alembic revision --autogenerate \
  -m "add destination columns to scheduled_tasks"
```

- [ ] **Step 3: Hand-edit the migration**

Autogen will produce `add_column` calls. Hand-add:

```python
# Drop existing CHECK on target_mode (if any) and add new one
op.drop_constraint("ck_scheduled_tasks_target_mode", "scheduled_tasks", type_="check")
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
psql cubebox_feat_2026_06_23_schedule_trigger_destinations \
  -c "\d scheduled_tasks" | tee ../tmp/stask-schema.log | tail -40
```

Expected output includes the four new columns and both new check constraints.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/models/scheduled_task.py backend/alembic/versions/*_add_destination_columns_to_scheduled_tasks.py
git commit -m "feat(schedules): add topic_id and im_channel destination columns"
```

---

## Task 2 — Migration + model: `triggers`

**Files:**
- Create: `backend/alembic/versions/<rev>_add_destination_columns_to_triggers.py`
- Modify: `backend/cubebox/models/trigger.py:13-72`

- [ ] **Step 1: Update the SQLModel class**

```python
# backend/cubebox/models/trigger.py
class Trigger(CubeboxBase, OrgScopedMixin, table=True):
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
```

- [ ] **Step 2: Generate the migration**

```bash
cd backend && uv run alembic revision --autogenerate \
  -m "add destination columns to triggers"
```

- [ ] **Step 3: Hand-edit the migration**

```python
op.drop_constraint("ck_triggers_conversation_policy", "triggers", type_="check")
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
psql cubebox_feat_2026_06_23_schedule_trigger_destinations -c "\d triggers" | tail -40
```

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/models/trigger.py backend/alembic/versions/*_add_destination_columns_to_triggers.py
git commit -m "feat(triggers): add topic_id and im_channel destination columns"
```

---

## Task 3 — `ConversationRepository.create` accepts `topic_id`

**Files:**
- Modify: `backend/cubebox/repositories/conversation.py:98`
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
# backend/cubebox/repositories/conversation.py:98
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
git add backend/cubebox/repositories/conversation.py backend/tests/e2e/test_conversation_repository.py
git commit -m "feat(conversations): accept topic_id on repository.create"
```

---

## Task 4 — Extract `resolve_im_conversation` helper

**Files:**
- Create: `backend/cubebox/im/conversation_resolver.py`
- Modify: `backend/cubebox/im/inbound.py:218-250`
- Test: `backend/tests/unit/test_resolve_im_conversation.py`

- [ ] **Step 1: Write the failing unit test (mocked DB)**

```python
# backend/tests/unit/test_resolve_im_conversation.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from cubebox.im.conversation_resolver import resolve_im_conversation

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

- [ ] **Step 3: Implement the helper**

```python
# backend/cubebox/im/conversation_resolver.py
from typing import Literal
from sqlmodel.ext.asyncio.session import AsyncSession

from cubebox.models.conversation import Conversation
from cubebox.models.im_channel_binding import IMChannelBinding
from cubebox.models.im_connector import IMConnectorAccount
from cubebox.repositories.im_connector import get_or_create_thread_link


async def resolve_im_conversation(
    session: AsyncSession,
    account: IMConnectorAccount,
    *,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    effective_user_id: str,
    origin: Literal["inbound", "schedule", "trigger"],
    title_hint: str = "IM conversation",
) -> str:
    binding = await session.exec(
        select(IMChannelBinding).where(
            IMChannelBinding.account_id == account.id,
            IMChannelBinding.channel_id == channel_id,
        )
    ).first()

    topic_id = binding.topic_id if binding is not None else None
    is_shared = (binding is not None and binding.mode == "shared")

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
        # ConversationParticipant for shared mode handled by caller if needed.
        return conv.id

    link, _ = await get_or_create_thread_link(
        session,
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        make_conversation_id=_mint_conversation_id,
    )
    return link.conversation_id
```

- [ ] **Step 4: Refactor `_make_conversation_id` in `im/inbound.py`**

Replace `inbound.py:218-250` to call `resolve_im_conversation` rather than building its own closure. Preserve the `ConversationParticipant` insert for `is_shared` case (the helper doesn't own it).

- [ ] **Step 5: Run unit + IM inbound tests**

```bash
cd backend && uv run pytest tests/unit/test_resolve_im_conversation.py tests/e2e/test_im_inbound_outbox.py --no-cov -x 2>&1 | tee ../tmp/resolve.log | tail -20
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/im/conversation_resolver.py backend/cubebox/im/inbound.py backend/tests/unit/test_resolve_im_conversation.py
git commit -m "refactor(im): extract resolve_im_conversation for reuse by dispatchers"
```

---

## Task 5 — `ScheduleTargetSpec.validate` pure function

**Files:**
- Create: `backend/cubebox/services/schedule_target_spec.py`
- Test: `backend/tests/unit/test_schedule_target_spec.py`

- [ ] **Step 1: Write the failing test matrix**

```python
# backend/tests/unit/test_schedule_target_spec.py
import pytest
from cubebox.services.schedule_target_spec import (
    ScheduleTargetSpec, ScheduleTargetError,
)

# (target_mode, target_conv, topic, im_acct, im_ch, im_scope, should_pass)
CASES = [
    ("fixed", "conv_1", None, None, None, None, True),
    ("fixed", None, None, None, None, None, False),                # missing conv
    ("fixed", "conv_1", "top_1", None, None, None, False),         # topic forbidden
    ("fixed", "conv_1", None, "imac_1", "C", "dm", False),         # im forbidden
    ("new_each_run", None, None, None, None, None, True),
    ("new_each_run", None, "top_1", None, None, None, True),
    ("new_each_run", "conv_1", None, None, None, None, False),
    ("new_each_run", None, None, "imac_1", "C", "dm", False),
    ("im_channel", None, None, "imac_1", "C", "dm", True),
    ("im_channel", "conv_1", None, "imac_1", "C", "dm", False),
    ("im_channel", None, "top_1", "imac_1", "C", "dm", False),
    ("im_channel", None, None, None, "C", "dm", False),            # missing acct
]

@pytest.mark.parametrize("case", CASES)
def test_schedule_target_spec_matrix(case):
    target_mode, conv, topic, acct, ch, scope, ok = case
    spec = ScheduleTargetSpec(
        target_mode=target_mode,
        target_conversation_id=conv,
        topic_id=topic,
        im_account_id=acct,
        im_channel_id=ch,
        im_scope_key=scope,
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
# backend/cubebox/services/schedule_target_spec.py
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

    def validate(self) -> None:
        m = self.target_mode
        if m == "fixed":
            if not self.target_conversation_id:
                raise ScheduleTargetError("target_conversation_id required for fixed")
            if self.topic_id:
                raise ScheduleTargetError("topic_id not allowed for fixed")
            if any((self.im_account_id, self.im_channel_id, self.im_scope_key)):
                raise ScheduleTargetError("im_* fields not allowed for fixed")
        elif m == "new_each_run":
            if self.target_conversation_id:
                raise ScheduleTargetError("target_conversation_id not allowed for new_each_run")
            if any((self.im_account_id, self.im_channel_id, self.im_scope_key)):
                raise ScheduleTargetError("im_* fields not allowed for new_each_run")
        elif m == "im_channel":
            if self.target_conversation_id:
                raise ScheduleTargetError("target_conversation_id not allowed for im_channel")
            if self.topic_id:
                raise ScheduleTargetError("topic_id not allowed for im_channel")
            if not (self.im_account_id and self.im_channel_id and self.im_scope_key):
                raise ScheduleTargetError(
                    "im_account_id, im_channel_id, im_scope_key all required for im_channel"
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
git add backend/cubebox/services/schedule_target_spec.py backend/tests/unit/test_schedule_target_spec.py
git commit -m "feat(schedules): add ScheduleTargetSpec validator shared by API and tools"
```

---

## Task 6 — Pydantic schema updates: `ws_scheduled_tasks.py`

**Files:**
- Modify: `backend/cubebox/api/schemas/ws_scheduled_tasks.py`

- [ ] **Step 1: Update create / patch request models**

```python
# backend/cubebox/api/schemas/ws_scheduled_tasks.py
TargetMode = Literal["fixed", "new_each_run", "im_channel"]

class ScheduledTaskCreateRequest(BaseModel):
    # ...existing fields...
    target_mode: TargetMode
    target_conversation_id: str | None = None
    topic_id: str | None = None
    im_account_id: str | None = None
    im_channel_id: str | None = None
    im_scope_key: str | None = None

    @model_validator(mode="after")
    def _validate_target(self):
        ScheduleTargetSpec(
            target_mode=self.target_mode,
            target_conversation_id=self.target_conversation_id,
            topic_id=self.topic_id,
            im_account_id=self.im_account_id,
            im_channel_id=self.im_channel_id,
            im_scope_key=self.im_scope_key,
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
cd backend && uv run python -c "from cubebox.api.schemas.ws_scheduled_tasks import ScheduledTaskCreateRequest" 2>&1
```

Expected: no traceback.

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/api/schemas/ws_scheduled_tasks.py
git commit -m "feat(api): add destination fields to scheduled task schemas"
```

---

## Task 7 — Pydantic schema updates: `trigger.py`

Mirror Task 6 on `backend/cubebox/api/schemas/trigger.py`. Discriminator is `conversation_policy` ∈ `{new_each_time, im_channel}`. Use `TriggerTargetSpec` from Task 5.

- [ ] **Steps 1-4:** Identical shape to Task 6.
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(api): add destination fields to trigger schemas"
```

---

## Task 8 — Schedule REST: accept new fields + reject mode change

**Files:**
- Modify: `backend/cubebox/api/routes/v1/ws_scheduled_tasks.py:82-132`
- Modify: `backend/cubebox/services/scheduled_task.py` (update method)

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

In `routes/v1/ws_scheduled_tasks.py:patch_task`:

```python
if body.target_mode is not None:
    raise HTTPException(
        status_code=422,
        detail="target_mode cannot be changed via PATCH; delete and recreate",
    )
```

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

## Task 10 — `dispatch_im_channel_run` helper

**Files:**
- Create: `backend/cubebox/im/run_handoff.py`
- Test: covered by Task 14's e2e tests (helper has no unit-testable boundary without integration)

This helper is the missing piece between "we have a conv and a prompt" and "the IM tailer streams responses back to the channel."

- [ ] **Step 1: Implement**

```python
# backend/cubebox/im/run_handoff.py
from typing import Literal
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from cubebox.models.im_connector import (
    IMConnectorAccount, IMIdentityLink, IMRunQueueItem, IMWebhookReceipt,
)
from cubebox.streams.run_manager import RunManager
from cubebox.streams.run_context import RunContext


async def dispatch_im_channel_run(
    session: AsyncSession,
    *,
    account: IMConnectorAccount,
    conversation_id: str,
    content: str,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    owner_user_id: str,
    origin: Literal["schedule", "trigger"],
    origin_key: str,       # f"schedule:{task.id}:{run_row.id}" — unique per fire
    run_manager: RunManager,
    on_run_started,        # per-platform hook
) -> str:
    # 1. Look up owner's IM identity (may be None).
    identity = (await session.exec(
        select(IMIdentityLink).where(
            IMIdentityLink.account_id == account.id,
            IMIdentityLink.user_id == owner_user_id,
        )
    )).first()
    sender_im_user_id = identity.im_user_id if identity is not None else None

    # 2. Synthetic webhook receipt.
    receipt = IMWebhookReceipt(
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        platform_event_id=origin_key,
        status="completed",
    )
    session.add(receipt)
    await session.flush()

    # 3. Outbox row.
    item = IMRunQueueItem(
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
        status="started",          # we'll start the run inline
    )
    session.add(item)
    await session.flush()

    # 4. Start the run.
    ctx = RunContext(
        user_id=owner_user_id,
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        conversation_id=conversation_id,
        trigger=origin,
    )
    run_id = await run_manager.start_run(
        conversation_id=conversation_id,
        content=content,
        ctx=ctx,
    )

    # 5. Fire the per-platform hook (worker would normally do this).
    # The queue item is kept in memory; on_run_started gets both
    # (run_id, item) verbatim — no IMRunQueueItem.run_id column needed.
    await on_run_started(run_id, item)
    return run_id
```

Verified at plan-write time that `IMRunQueueItem` has no `run_id` column and the worker passes `(run_id, captured_item)` via in-memory args (`backend/cubebox/im/worker.py:242-254`). No `IMRunQueueItem` schema change in this feature.

- [ ] **Step 2: Identify the on_run_started hook resolver**

Look at how `im/worker.py:286` resolves the per-platform hook today (likely a dict keyed on `account.platform`). Expose the same resolver as a function `get_platform_on_run_started(platform: str)` for `dispatch_im_channel_run` to call. If the per-platform hook is constructed only at app startup (closure over runtime state), refactor minimally so the same closure is reachable from a schedule/trigger dispatcher path — preserve existing behavior for the worker.

- [ ] **Step 3: Commit**

```bash
git add backend/cubebox/im/run_handoff.py
git commit -m "feat(im): add dispatch_im_channel_run for non-inbound IM runs"
```

---

## Task 11 — Schedule dispatch: wire topic + add im_channel branch

**Files:**
- Modify: `backend/cubebox/schedules/dispatch.py:56-139`

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

- [ ] **Step 3: Add `im_channel` branch**

```python
# dispatch.py: in resolve_target() after the existing branches
elif task.target_mode == "im_channel":
    account = await session.get(IMConnectorAccount, task.im_account_id)
    if account is None:
        await _record_failed_run(
            session, task,
            reason="im_account_unlinked",
        )
        return None       # caller skips start_run
    conv_id = await resolve_im_conversation(
        session, account,
        channel_id=task.im_channel_id,
        scope_key=task.im_scope_key,
        scope_kind=_derive_scope_kind(task.im_scope_key),
        effective_user_id=task.owner_user_id,
        origin="schedule",
        title_hint=f"Scheduled: {task.prompt[:80]}",
    )
    return ConvTarget(conv_id=conv_id, im_mode=True, account=account)
```

- [ ] **Step 4: Branch the dispatch entry to call `dispatch_im_channel_run` when im_mode**

```python
async def dispatch_scheduled_run(session, task):
    target = await resolve_target(session, task)
    if target is None:
        return
    if target.im_mode:
        on_hook = get_platform_on_run_started(target.account.platform)
        run_id = await dispatch_im_channel_run(
            session, account=target.account,
            conversation_id=target.conv_id,
            content=task.prompt,
            channel_id=task.im_channel_id,
            scope_key=task.im_scope_key,
            scope_kind=_derive_scope_kind(task.im_scope_key),
            owner_user_id=task.owner_user_id,
            origin="schedule",
            origin_key=f"schedule:{task.id}:{run_row.id}",
            run_manager=run_manager,
            on_run_started=on_hook,
        )
    else:
        # existing inline path
        run_id = await run_manager.start_run(...)
    # write ScheduledTaskRun row (existing logic)
```

- [ ] **Step 5: Run schedule tests (existing + new will be added in Task 14)**

```bash
cd backend && uv run pytest tests/e2e/test_scheduled_tasks*.py --no-cov 2>&1 | tee ../tmp/sched-dispatch.log | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/schedules/dispatch.py
git commit -m "feat(schedules): support topic_id and im_channel destinations in dispatcher"
```

---

## Task 12 — Trigger pipeline: wire topic + add im_channel branch

**Files:**
- Modify: `backend/cubebox/triggers/pipeline.py:37-151`

Mirror Task 11 on the trigger pipeline. The pipeline already always creates a new conv at `pipeline.py:102`; add `topic_id=trigger.topic_id` there. Then add the `im_channel` branch above the existing new-conv block.

- [ ] **Steps 1-5: Same shape as Task 11.**

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(triggers): support topic_id and im_channel destinations in pipeline"
```

---

## Task 13 — Agent tool: `create_scheduled_task`

**Files:**
- Create: `backend/cubebox/tools/builtin/create_scheduled_task.py`
- Modify: agent factory wiring (search for `make_view_images_tool` to find the pattern)

- [ ] **Step 1: Implement the factory**

```python
# backend/cubebox/tools/builtin/create_scheduled_task.py
from cubepi import tool

from cubebox.models.im_connector import IMThreadLink
from cubebox.services.scheduled_task import ScheduledTaskService
from cubebox.services.schedule_target_spec import ScheduleTargetSpec


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
            else:
                target_mode = "fixed"
                target_conversation_id = conversation_id
                im_account_id = im_channel_id = im_scope_key = None
        elif target_mode == "im_channel":
            assert link is not None, "im_channel target requires IM origin"
            im_account_id = link.account_id
            im_channel_id = link.channel_id
            im_scope_key  = link.scope_key
        else:
            im_account_id = im_channel_id = im_scope_key = None

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
}

export interface ScheduledTaskListFilters {
  topic_id?: string;
  im_account_id?: string;
  im_channel_id?: string;
}
```

- [ ] **Step 2: Build @cubebox/core**

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
