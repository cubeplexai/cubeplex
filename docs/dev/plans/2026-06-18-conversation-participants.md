# Conversation Participants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` (or `superpowers:subagent-driven-development`) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the shipped group-chat data model into a two-tier ACL: keep `topic_participants` as the topic eligibility gate, and add `conversation_participants` as the per-conversation actor list. Adds standalone group chat (no topic required) and unifies sandbox scoping under a polymorphic key.

**Architecture:** `conversation_participants` table + `Conversation.is_group_chat` cache + auto-join on first message + polymorphic `UserSandbox.(scope_type, scope_id)`. Migrations are reset and regenerated in one piece — no production data to backfill.

**Tech Stack:** SQLModel + Alembic (Postgres) + FastAPI on the backend; React 19 + Zustand + Next 16 (Turbopack) on the frontend; cubepi pinned to merged main.

**Reference**: [docs/dev/specs/2026-06-18-conversation-participants-design.md](../specs/2026-06-18-conversation-participants-design.md).

---

### Task 1: Reset migrations, public-id prefix, model changes

**Files:**
- Modify: `backend/cubebox/models/public_id.py`
- Modify: `backend/cubebox/models/conversation.py`
- Modify: `backend/cubebox/models/user_sandbox.py`
- Create: `backend/cubebox/models/conversation_participant.py`
- Modify: `backend/cubebox/models/__init__.py`
- Delete: `backend/alembic/versions/7b81f04dce1f_add_topics_and_topic_participants_.py`
- Delete: `backend/alembic/versions/2b6db4bfe7ac_user_sandbox_topic_id_partial_unique_.py`
- Create: `backend/alembic/versions/<new>_group_chat_and_conversation_participants.py`

- [ ] **Step 1: Add the public-id prefix**

In `public_id.py` add to the prefix constants:

```python
PREFIX_CPM = "cpm"  # conversation_participants
```

- [ ] **Step 2: Add `is_group_chat` to Conversation**

In `models/conversation.py` add after `is_pinned`:

```python
    is_group_chat: bool = Field(default=False)
```

- [ ] **Step 3: Replace `UserSandbox.topic_id` with polymorphic scope**

In `models/user_sandbox.py`:

```python
# Remove these:
#   topic_id: str | None = Field(...)
#   the second partial unique index on (org_id, workspace_id, topic_id)
#   the existing uq_user_sandbox_active partial unique (will be replaced)

# Add these fields:
scope_type: str = Field(max_length=20)  # 'user' | 'conversation' | 'topic'
scope_id: str = Field(max_length=20)
```

`__table_args__` replaces both old partial uniques with one:

```python
Index(
    "uq_user_sandbox_active_scope",
    "org_id", "workspace_id", "scope_type", "scope_id",
    unique=True,
    postgresql_where=text("status IN ('provisioning','running')"),
    sqlite_where=text("status IN ('provisioning','running')"),
),
```

- [ ] **Step 4: Create the `ConversationParticipant` model**

New file `backend/cubebox/models/conversation_participant.py`:

```python
"""Conversation participant — per-conversation actor list."""

from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import Column, DateTime, Index, UniqueConstraint
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin


class ConversationParticipant(CubeboxBase, OrgScopedMixin, table=True):
    """A user who has actively participated in a conversation.

    Append-only: rows are created on first send and never removed. SSE
    subscription is governed separately (see access control matrix in
    docs/dev/specs/2026-06-18-conversation-participants-design.md).
    """

    _PREFIX: ClassVar[str] = "cpm"
    __tablename__ = "conversation_participants"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "user_id", name="uq_conversation_participant"
        ),
        Index("ix_conversation_participants_user", "user_id"),
    )

    conversation_id: str = Field(
        foreign_key="conversations.id", max_length=20, index=True
    )
    user_id: str = Field(foreign_key="users.id", max_length=20)
    joined_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
```

- [ ] **Step 5: Export the model**

In `backend/cubebox/models/__init__.py`:

```python
from cubebox.models.conversation_participant import ConversationParticipant
```

Add `"ConversationParticipant"` to `__all__`.

- [ ] **Step 6: Delete the two shipped group-chat migrations**

```bash
rm backend/alembic/versions/7b81f04dce1f_add_topics_and_topic_participants_*.py
rm backend/alembic/versions/2b6db4bfe7ac_user_sandbox_topic_id_partial_unique_*.py
```

- [ ] **Step 7: Reset the worktree DB**

```bash
cd backend && uv run alembic downgrade base
```

Expected: clean state without group-chat tables.

- [ ] **Step 8: Generate one migration**

```bash
uv run alembic revision --autogenerate \
  -m "group_chat_and_conversation_participants"
```

Inspect the migration. It should:
- CREATE TABLE `topics` (with `last_activity_at`)
- CREATE TABLE `topic_participants`
- CREATE TABLE `conversation_participants`
- ALTER TABLE `conversations` ADD `topic_id`, `is_group_chat`
- ALTER TABLE `user_sandboxes` ADD `scope_type`, `scope_id`; DROP `topic_id` (if present)
- CREATE INDEX `uq_user_sandbox_active_scope` (partial; **autogen will NOT add the predicate** — hand-add `postgresql_where=sa.text("status IN ('provisioning','running')")`)
- DROP old `uq_user_sandbox_active` if present

Hand-edit the partial-unique predicate per the project's known autogen gap.

- [ ] **Step 9: Apply and verify**

```bash
uv run alembic upgrade head
```

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat(models): two-tier ACL — conversation_participants + polymorphic sandbox scope

Resets the shipped group-chat migrations and regenerates a single
migration capturing the final shape: topics + topic_participants +
conversation_participants + Conversation.is_group_chat + UserSandbox
polymorphic (scope_type, scope_id)."
```

---

### Task 2: ConversationParticipantRepository + is_group_chat maintenance

**Files:**
- Create: `backend/cubebox/repositories/conversation_participant.py`
- Modify: `backend/cubebox/repositories/conversation.py`
- Modify: `backend/cubebox/repositories/__init__.py`

- [ ] **Step 1: Create the repository**

```python
"""Conversation participant repository — append-only membership."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import update

from cubebox.models.conversation import Conversation
from cubebox.models.conversation_participant import ConversationParticipant
from cubebox.repositories.base import ScopedRepository


class ConversationParticipantRepository(ScopedRepository[ConversationParticipant]):
    model = ConversationParticipant

    def __init__(
        self,
        session: AsyncSession,
        *,
        org_id: str,
        workspace_id: str,
    ) -> None:
        super().__init__(session, org_id=org_id, workspace_id=workspace_id)

    async def list_user_ids(self, conversation_id: str) -> list[str]:
        stmt = select(cast(Any, ConversationParticipant.user_id)).where(
            cast(Any, ConversationParticipant.conversation_id) == conversation_id,
        )
        return [str(uid) for uid in (await self.session.execute(stmt)).scalars().all()]

    async def is_participant(self, conversation_id: str, user_id: str) -> bool:
        stmt = select(func.count()).where(
            cast(Any, ConversationParticipant.conversation_id) == conversation_id,
            cast(Any, ConversationParticipant.user_id) == user_id,
        )
        return (await self.session.execute(stmt)).scalar_one() > 0

    async def ensure_participant(
        self, conversation_id: str, user_id: str
    ) -> ConversationParticipant | None:
        """Append the row idempotently. Maintains Conversation.is_group_chat.

        Returns the inserted row, or None if the user was already a
        participant (race or no-op call). On the same transaction, flips
        Conversation.is_group_chat to True when the count crosses 1 → 2.
        """
        if await self.is_participant(conversation_id, user_id):
            return None
        row = ConversationParticipant(
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError:
            # Lost the uniq race — somebody else inserted between our
            # is_participant check and flush. Treat as a no-op.
            await self.session.rollback()
            return None

        # Maintain the is_group_chat cache. Count AFTER the flush.
        count_stmt = select(func.count()).where(
            cast(Any, ConversationParticipant.conversation_id) == conversation_id,
        )
        count = (await self.session.execute(count_stmt)).scalar_one()
        await self.session.execute(
            update(Conversation)
            .where(cast(Any, Conversation.id) == conversation_id)
            .values(is_group_chat=count > 1)
        )
        return row

    async def add_many(
        self, conversation_id: str, user_ids: list[str]
    ) -> list[ConversationParticipant]:
        """Append multiple participants idempotently. Returns the rows actually inserted."""
        added: list[ConversationParticipant] = []
        for uid in user_ids:
            row = await self.ensure_participant(conversation_id, uid)
            if row is not None:
                added.append(row)
        return added
```

- [ ] **Step 2: Export**

In `repositories/__init__.py`:

```python
from cubebox.repositories.conversation_participant import (
    ConversationParticipantRepository,
)
```

Add to `__all__`.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat(repo): ConversationParticipantRepository + is_group_chat maintenance

Append-only repo with ensure_participant() idempotency. Flips
Conversation.is_group_chat on the 1→2 transition in the same
transaction so the hot path can read a single bool."
```

---

### Task 3: ConversationRepository._scoped_select rewrite

**Files:**
- Modify: `backend/cubebox/repositories/conversation.py`

- [ ] **Step 1: Write the failing E2E test**

In `backend/tests/e2e/test_conversation_participants.py` (new file):

```python
"""Three-branch _scoped_select: creator, topic participant, conv participant."""

import httpx
import pytest


@pytest.mark.anyio
async def test_conv_participant_sees_standalone_group_chat(
    four_layer_admin_and_member,
) -> None:
    (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

    # Admin creates a personal conversation.
    conv = (
        await admin_c.post(f"/api/v1/ws/{ws_id}/conversations")
    ).json()
    conv_id = conv["id"]

    # Admin invites member → becomes standalone group chat.
    resp = await admin_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/invite-to-group",
        json={"user_ids": [member_uid]},
    )
    assert resp.status_code == 201, resp.text

    # Member can now see the conversation in their list.
    member_list = (await member_c.get(f"/api/v1/ws/{ws_id}/conversations")).json()
    assert any(c["id"] == conv_id for c in member_list["items"])
```

- [ ] **Step 2: Run to verify it fails (404 / endpoint missing)**

```bash
uv run pytest tests/e2e/test_conversation_participants.py -v --tb=short
```

Expected: FAIL on `invite-to-group` 404.

- [ ] **Step 3: Modify `_scoped_select`**

The new OR clause has three branches:

```python
def _scoped_select(self) -> Any:
    from cubebox.models.conversation_participant import ConversationParticipant
    from cubebox.models.topic import Topic, TopicParticipant

    return (
        super()
        ._scoped_select()
        .where(
            cast(Any, Conversation.deleted_at).is_(None),
            or_(
                # Branch 1: personal conv, caller is the creator
                and_(
                    cast(Any, Conversation.topic_id).is_(None),
                    cast(Any, Conversation.creator_user_id) == self.user_id,
                ),
                # Branch 2: standalone group chat (no topic), caller is a conv participant
                and_(
                    cast(Any, Conversation.topic_id).is_(None),
                    cast(Any, Conversation.id).in_(
                        select(ConversationParticipant.conversation_id)
                        .where(
                            cast(Any, ConversationParticipant.user_id) == self.user_id
                        )
                    ),
                ),
                # Branch 3: topic conv, caller is topic participant (and topic not archived)
                cast(Any, Conversation.topic_id).in_(
                    select(TopicParticipant.topic_id)
                    .join(Topic, cast(Any, Topic.id) == TopicParticipant.topic_id)
                    .where(
                        cast(Any, TopicParticipant.user_id) == self.user_id,
                        cast(Any, Topic.is_archived).is_(False),
                    )
                ),
                # Branch 4: topic conv where caller is conv participant
                # (covers people invited only to a single conv inside a topic)
                cast(Any, Conversation.id).in_(
                    select(ConversationParticipant.conversation_id)
                    .where(cast(Any, ConversationParticipant.user_id) == self.user_id)
                ),
            ),
        )
    )
```

Note the fourth branch — round-3 review's "non-topic-participant invited to a specific conv inside a topic" case. With `conversation_participants` we can finally express it cleanly.

- [ ] **Step 4: Update `update_title_if_current`**

The same OR clause logic needs to apply to the title-update WHERE. Replace with:

```python
stmt = (
    update(Conversation)
    .where(
        cast(Any, Conversation.id) == conversation_id,
        cast(Any, Conversation.title) == current_title,
        or_(
            cast(Any, Conversation.creator_user_id) == self.user_id,
            cast(Any, Conversation.id).in_(
                select(ConversationParticipant.conversation_id)
                .where(cast(Any, ConversationParticipant.user_id) == self.user_id)
            ),
            cast(Any, Conversation.topic_id).in_(
                select(TopicParticipant.topic_id)
                .join(Topic, cast(Any, Topic.id) == TopicParticipant.topic_id)
                .where(
                    cast(Any, TopicParticipant.user_id) == self.user_id,
                    cast(Any, Topic.is_archived).is_(False),
                )
            ),
        ),
    )
    .values(title=new_title)
)
```

- [ ] **Step 5: Update `list_all` count_stmt to derive from _scoped_select**

Already done in the shipped design — verify it still uses `self._scoped_select().subquery()`.

- [ ] **Step 6: Run existing conv tests, verify no regression**

```bash
uv run pytest tests/e2e/test_conversations.py tests/e2e/test_conversation_privacy.py -v --tb=short
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(repo): three-branch scoped_select for conversation_participants

Conversations are visible if: caller is creator (personal),
caller is in conversation_participants (standalone group chat OR
single-conv invite inside a topic), or caller is in topic_participants
with topic.is_archived=False. update_title_if_current mirrors the
same OR logic."
```

---

### Task 4: UserSandbox polymorphic scope refactor

**Files:**
- Modify: `backend/cubebox/repositories/user_sandbox.py`
- Modify: `backend/cubebox/sandbox/manager.py`
- Modify: `backend/cubebox/sandbox/lazy.py`

- [ ] **Step 1: Replace by_user / by_topic lookups with by_scope**

In `repositories/user_sandbox.py`, replace `get_active_by_user`, `get_active_by_topic`, `get_active_for_scope`, `get_resumable_by_user`, `get_resumable_by_topic`, `get_resumable_for_scope` with two methods:

```python
async def get_active_by_scope(
    self, *, scope_type: str, scope_id: str
) -> UserSandbox | None:
    stmt = (
        self._scoped_select()
        .where(
            UserSandbox.scope_type == scope_type,
            UserSandbox.scope_id == scope_id,
            cast(Any, UserSandbox.status).in_(["provisioning", "running"]),
        )
        .order_by(cast(Any, UserSandbox.created_at).desc())
    )
    return (await self.session.execute(stmt)).scalar_one_or_none()


async def get_resumable_by_scope(
    self, *, scope_type: str, scope_id: str
) -> UserSandbox | None:
    stmt = (
        self._scoped_select()
        .where(
            UserSandbox.scope_type == scope_type,
            UserSandbox.scope_id == scope_id,
            cast(Any, UserSandbox.status).in_(["paused", "resuming"]),
        )
        .order_by(cast(Any, UserSandbox.created_at).desc())
    )
    return (await self.session.execute(stmt)).scalar_one_or_none()
```

Update `reserve()` to accept `scope_type` and `scope_id` (not `user_id`/`topic_id`).

- [ ] **Step 2: Add scope-rekey method**

```python
async def rekey(
    self,
    *,
    from_scope_type: str,
    from_scope_id: str,
    to_scope_type: str,
    to_scope_id: str,
) -> None:
    """Re-scope the active sandbox row in place.

    Used by the upgrade endpoints: when a 1:1 becomes a standalone
    group chat (user → conversation) or when a standalone group chat
    becomes a topic (conversation → topic), the same running sandbox
    is inherited under the new scope key. One UPDATE, no file
    movement.
    """
    stmt = (
        update(UserSandbox)
        .where(
            cast(Any, UserSandbox.org_id) == self.org_id,
            cast(Any, UserSandbox.workspace_id) == self.workspace_id,
            cast(Any, UserSandbox.scope_type) == from_scope_type,
            cast(Any, UserSandbox.scope_id) == from_scope_id,
            cast(Any, UserSandbox.status).in_(
                ["provisioning", "running", "paused", "resuming"]
            ),
        )
        .values(scope_type=to_scope_type, scope_id=to_scope_id)
    )
    await self.session.execute(stmt)
```

- [ ] **Step 3: Update LazySandbox + SandboxManager**

In `sandbox/lazy.py`:

```python
class LazySandbox:
    def __init__(
        self,
        *,
        manager: SandboxManager,
        scope_type: str,
        scope_id: str,
        org_id: str,
        workspace_id: str,
        # ... rest unchanged
    ) -> None:
        ...
```

In `sandbox/manager.py`, `get_or_create_for` and all the callsites change signature from `(user_id, topic_id=None)` to `(scope_type, scope_id)`. Audit every `_by_user` / `_by_topic` callsite — replace with `_by_scope`. **Verify the race-loss poll** at the lines previously identified (~477/485) still works correctly when both loser and winner have the same `(scope_type, scope_id)`.

- [ ] **Step 4: Update ws_sandbox route helper**

In `backend/cubebox/api/routes/v1/ws_sandbox.py`, rewrite `_resolve_sandbox_scope` to return `(scope_type, scope_id)`:

```python
async def _resolve_sandbox_scope(
    session: AsyncSession, ctx: RequestContext, conversation_id: str | None
) -> tuple[str, str]:
    if conversation_id is None:
        return "user", ctx.user.id

    # Load conv + topic + is_group_chat in one shot.
    conv_stmt = select(
        cast(Any, Conversation.topic_id),
        cast(Any, Conversation.creator_user_id),
        cast(Any, Conversation.is_group_chat),
    ).where(
        cast(Any, Conversation.id) == conversation_id,
        cast(Any, Conversation.workspace_id) == ctx.workspace_id,
    )
    row = (await session.execute(conv_stmt)).first()
    if row is None:
        raise HTTPException(404, "Conversation not found")
    topic_id, creator_user_id, is_group_chat = row

    if topic_id is None:
        # Personal or standalone group chat.
        await _assert_personal_or_conv_access(session, ctx, conversation_id, creator_user_id)
        if is_group_chat:
            return "conversation", conversation_id
        return "user", ctx.user.id

    # Topic conv: authorize + look up mode.
    await _assert_topic_access(session, ctx, topic_id, conversation_id)
    topic_stmt = select(
        cast(Any, Topic.sandbox_mode), cast(Any, Topic.creator_user_id)
    ).where(
        cast(Any, Topic.id) == topic_id,
        cast(Any, Topic.is_archived).is_(False),
    )
    topic_row = (await session.execute(topic_stmt)).first()
    if topic_row is None:
        raise HTTPException(404, "Conversation not found")
    mode, topic_creator_user_id = topic_row

    if (mode or "creator") == "dedicated":
        return "topic", topic_id
    return "user", str(topic_creator_user_id or ctx.user.id)
```

Authorization helpers `_assert_personal_or_conv_access` and
`_assert_topic_access` check the matrix from the spec — caller is
creator/conv-participant for personal; caller is
topic-participant/conv-participant for topic conv. Each raises 404 on
mismatch.

Update all `manager.get_or_create(...)` calls in the route handlers to
pass `(scope_type, scope_id)` from the resolved tuple.

- [ ] **Step 5: Update run_manager `_resolve_sandbox_target`**

```python
@staticmethod
def _resolve_sandbox_target(ctx: RunContext) -> tuple[str, str]:
    """Return (scope_type, scope_id) for the sandbox lookup."""
    if ctx.topic_id is None:
        if ctx.is_group_chat:
            return "conversation", ctx.conversation_id
        return "user", ctx.user_id
    effective_mode = ctx.sandbox_mode or "creator"
    if effective_mode == "dedicated":
        return "topic", ctx.topic_id
    return "user", ctx.topic_creator_user_id or ctx.user_id
```

Add `conversation_id: str` to `RunContext` if absent. Update the two LazySandbox call sites (`_execute_run`, `_resume_run`).

- [ ] **Step 6: Update tests**

`tests/unit/test_user_sandbox_repo.py` and integration sandbox tests: replace `by_user` / `by_topic` calls with `by_scope`.

- [ ] **Step 7: Run sandbox tests**

```bash
uv run pytest tests/unit/test_user_sandbox_repo.py tests/integration/sandbox/ -v --tb=short
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(sandbox): polymorphic scope (scope_type, scope_id)

Replaces nullable topic_id with a single (scope_type, scope_id) tuple
on UserSandbox. Lookups, reserves, and the race-loss poll all take
the tuple. Adds rekey() so the upgrade endpoints can promote a
running sandbox from conversation-scope to topic-scope in one
UPDATE without file movement."
```

---

### Task 5: API — invite-to-group + list participants + upgrade refactor

**Files:**
- Modify: `backend/cubebox/api/routes/v1/conversations.py`
- Modify: `backend/cubebox/api/schemas/` (new request models)

- [ ] **Step 1: Add request schemas**

In `api/schemas/conversations.py` (or wherever the existing conv schemas live):

```python
class InviteToGroupRequest(BaseModel):
    user_ids: list[str] = Field(min_length=1, max_length=20)


class ListConversationParticipantsResponse(BaseModel):
    items: list[ConversationParticipantOut]
```

- [ ] **Step 2: New endpoint — invite to group**

```python
@router.post(
    "/{conversation_id}/invite-to-group",
    status_code=status.HTTP_201_CREATED,
)
async def invite_to_conversation(
    conversation_id: str,
    body: InviteToGroupRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    # Authorization: caller must be a P(conv) or creator (which makes them P(conv))
    conv = await _load_conversation_for_caller(session, ctx, conversation_id)
    if conv is None:
        raise HTTPException(404, "Conversation not found")

    # Standalone group-chat invariant: if conv currently has 0 participants
    # rows but is a personal 1:1, seed the creator first so the caller's
    # access doesn't accidentally fall off.
    cp_repo = ConversationParticipantRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )
    if not await cp_repo.is_participant(conversation_id, conv.creator_user_id):
        await cp_repo.ensure_participant(conversation_id, conv.creator_user_id)

    # Validate workspace membership of invitees.
    membership_repo = MembershipRepository(session)
    for uid in body.user_ids:
        role = await membership_repo.get_role(user_id=uid, workspace_id=ctx.workspace_id)
        if role is None:
            raise HTTPException(400, f"User {uid} is not a member of this workspace")

    added = await cp_repo.add_many(conversation_id, body.user_ids)

    # Sandbox rekey on the 1 → 2 transition: if the personal sandbox
    # for the creator just became "shared by a group chat", re-scope
    # it from user-keyed to conversation-keyed so subsequent file
    # operations from any participant hit the same row.
    if conv.topic_id is None:
        await session.refresh(conv)  # pick up is_group_chat flip
        if conv.is_group_chat:
            sbx_repo = UserSandboxRepository(
                session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
            )
            await sbx_repo.rekey(
                from_scope_type="user",
                from_scope_id=conv.creator_user_id,
                to_scope_type="conversation",
                to_scope_id=conversation_id,
            )

    await session.commit()
    return {
        "participants": await _hydrate_conv_participants(session, added),
        "conversation": _serialize_conversation(conv),
    }
```

- [ ] **Step 3: New endpoint — list conversation participants**

```python
@router.get("/{conversation_id}/participants")
async def list_conversation_participants(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    conv = await _load_conversation_for_caller(session, ctx, conversation_id)
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    cp_repo = ConversationParticipantRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )
    rows = await cp_repo.list_user_ids(conversation_id)
    # Hydrate display names via a single User join.
    return {"items": await _hydrate_conv_participants_by_uids(session, rows)}
```

- [ ] **Step 4: Rewire `upgrade_conversation_to_topic`**

The existing handler already creates a Topic and links it. Add to the same transaction:

```python
# After topic + topic_participants insertion:
cp_repo = ConversationParticipantRepository(
    session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
)
# Copy current topic_participants into the conversation's
# conversation_participants so they are tagged as actors.
for tp_uid in [p.user_id for p in await topic_repo.list_participants(topic.id)]:
    await cp_repo.ensure_participant(conversation_id, tp_uid)

# Sandbox rekey: standalone group chat (or personal) → topic.
sbx_repo = UserSandboxRepository(
    session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
)
if conversation.is_group_chat:
    await sbx_repo.rekey(
        from_scope_type="conversation",
        from_scope_id=conversation_id,
        to_scope_type="topic",
        to_scope_id=topic.id,
    )
else:
    # Personal 1:1 upgrading directly to topic: rekey from user-scope.
    await sbx_repo.rekey(
        from_scope_type="user",
        from_scope_id=ctx.user.id,
        to_scope_type="topic",
        to_scope_id=topic.id,
    )
```

- [ ] **Step 5: Update topic-scoped conv creation**

In `ws_topics.py::create_topic_conversation`, after the conv insert, add the creator into `conversation_participants` so their first message doesn't auto-join itself:

```python
cp_repo = ConversationParticipantRepository(
    session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
)
await cp_repo.ensure_participant(conv.id, ctx.user.id)
```

- [ ] **Step 6: Topic-scoped conv invite (member_user_ids on creation body)**

Extend `TopicConversationCreateRequest` with optional `member_user_ids: list[str]`. After creator insert, also seed those as conversation_participants.

- [ ] **Step 7: Run E2E tests**

```bash
uv run pytest tests/e2e/test_conversation_participants.py tests/e2e/test_topics.py -v --tb=short
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(api): invite-to-group + list-conv-participants + upgrade rekey

POST /conversations/{id}/invite-to-group inserts conv_participant rows
and rekeys the sandbox from user-scope to conversation-scope on the
1→2 transition. upgrade-to-topic also rekeys (conv-scope → topic-scope
OR user-scope → topic-scope depending on prior state). Topic-scoped
conv creation seeds the creator as a participant."
```

---

### Task 6: send_message + steer + HITL — auto-join + is_group_chat-driven

**Files:**
- Modify: `backend/cubebox/api/routes/v1/conversations.py`
- Modify: `backend/cubebox/streams/run_manager.py`

- [ ] **Step 1: Auto-join in send_message**

In the send_message handler, after loading the conversation:

```python
# Auto-join: a topic participant (or anyone with view access via
# _scoped_select) who has not previously sent a message in this conv
# gets added to conversation_participants atomically with the send.
cp_repo = ConversationParticipantRepository(
    session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
)
await cp_repo.ensure_participant(conversation.id, ctx.user.id)
# Refresh to pick up any is_group_chat flip from the ensure call.
await session.refresh(conversation)
```

- [ ] **Step 2: Read `is_group_chat` straight off the conversation row**

The topic-resolution block in send_message no longer needs the
participant count — replace with:

```python
is_group_chat = bool(conversation.is_group_chat)
# topic_id, sandbox_mode, topic_creator_user_id loaded from Topic if
# topic_id is not None (same shape as today).
```

- [ ] **Step 3: HITL gating — `P(conv)` only**

In `submit_sandbox_confirm` and `submit_ask_user_answer`, after loading
conversation:

```python
cp_repo = ConversationParticipantRepository(
    session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
)
if not await cp_repo.is_participant(conversation.id, ctx.user.id):
    raise HTTPException(404, "Conversation not found")
```

Note this is **stricter** than send_message: topic-only members cannot
answer HITL even though they could send a message (which would
auto-join them).

- [ ] **Step 4: SSE access — topic ∨ conv**

The SSE stream handler reuses `_scoped_select` via the conv repo, so
it already permits `P(topic) ∨ P(conv)` once Task 3 lands. Verify by
test (Task 7) — no code change here unless the handler bypasses the
repo.

- [ ] **Step 5: Drop participant-count derivation from RunContext**

In `RunContext`, `participant_ids` and `sender_display_name` are only
needed for memory / sender attribution gates. With
`Conversation.is_group_chat` driving those, the topic-resolution block
shrinks:

```python
@dataclass(slots=True)
class RunContext:
    user_id: str
    org_id: str
    workspace_id: str
    conversation_id: str  # NEW — needed for sandbox conv-scope
    trigger: str = "interactive"
    topic_id: str | None = None
    is_group_chat: bool = False  # comes from Conversation.is_group_chat
    sender_display_name: str | None = None
    sandbox_mode: str | None = None
    topic_creator_user_id: str | None = None
```

(`participant_ids` removed.) Update all 4 RunContext construction
sites: send_message, steer, sandbox_confirm, ask_user_answer.

- [ ] **Step 6: Steer auto-join**

Same as send_message — steering a paused run also counts as
participating. Add `ensure_participant` in the steer handler.

- [ ] **Step 7: Run E2E tests**

```bash
uv run pytest tests/e2e/test_conversation_participants.py tests/e2e/test_group_chat.py -v --tb=short
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(runtime): auto-join on first send + is_group_chat-driven gates

send_message and steer auto-join the caller into
conversation_participants idempotently before the run dispatch.
RunContext.is_group_chat now comes from Conversation.is_group_chat
(stored bool) instead of a participant count, decoupling group-chat
behavior from topic membership. HITL endpoints reject topic-only
callers (P(conv) required). SSE remains P(topic) ∨ P(conv)."
```

---

### Task 7: Backend E2E tests

**Files:**
- Modify: `backend/tests/e2e/test_conversation_participants.py`
- Modify: `backend/tests/e2e/test_group_chat.py`

- [ ] **Step 1: Standalone group chat tests**

Add to `test_conversation_participants.py`:

- `test_invite_promotes_personal_to_group_chat` — 1:1 + invite → `is_group_chat=True`, sandbox rekeyed user→conversation
- `test_invite_idempotent` — re-invite same uid is no-op (201 with empty items)
- `test_invite_validates_workspace_membership` — invite outsider → 400
- `test_invitee_can_see_and_send` — invitee receives view + send rights
- `test_non_invitee_404s_on_send` — random workspace member can't send
- `test_send_auto_joins_topic_participant` — P(topic) sends in conv X → `conversation_participants` gets a row

- [ ] **Step 2: HITL boundary tests**

- `test_topic_participant_cannot_answer_hitl_until_they_send` — P(topic) without P(conv) → 404 on submit_sandbox_confirm
- `test_p_conv_can_answer_hitl` — after auto-join, same call succeeds

- [ ] **Step 3: SSE access tests**

- `test_topic_participant_can_subscribe_sse_without_sending` — opens SSE stream, receives one event from another participant's message, never sent themselves, `conversation_participants` row never created

- [ ] **Step 4: Sandbox scope tests**

- `test_standalone_group_chat_dedicated_sandbox_keys_by_conversation` — invite N people, dedicated → `scope_type='conversation'`
- `test_upgrade_to_topic_rekeys_sandbox` — standalone group chat + sandbox → upgrade-to-topic → same sandbox row, now `scope_type='topic'`
- `test_personal_to_topic_direct_rekey` — 1:1 + sandbox → upgrade-to-topic (no intermediate group chat) → rekey from user→topic

- [ ] **Step 5: Run + Commit**

```bash
uv run pytest tests/e2e/test_conversation_participants.py tests/e2e/test_group_chat.py -v --tb=short
git add -A
git commit -m "test(e2e): conversation_participants + standalone group chat + sandbox rekey

Covers the four-branch _scoped_select, auto-join on first send,
HITL P(conv)-only, SSE P(topic) ∨ P(conv), and sandbox rekey on
both upgrade paths (personal→group, group→topic, personal→topic)."
```

---

### Task 8: Frontend types + topic/conversation stores

**Files:**
- Modify: `frontend/packages/core/src/types/conversation.ts`
- Create: `frontend/packages/core/src/types/conversation-participant.ts`
- Modify: `frontend/packages/core/src/types/index.ts`
- Modify: `frontend/packages/core/src/api/conversations.ts`
- Create: `frontend/packages/core/src/api/conversation-participants.ts`
- Modify: `frontend/packages/core/src/stores/conversationStore.ts`

- [ ] **Step 1: Type changes**

```ts
// types/conversation.ts
export interface Conversation {
  // ... existing fields
  topic_id?: string | null
  is_group_chat: boolean
}

// types/conversation-participant.ts
export interface ConversationParticipant {
  id: string
  conversation_id: string
  user_id: string
  joined_at: string
  display_name?: string | null
  email?: string | null
}
```

- [ ] **Step 2: API client functions**

```ts
// api/conversation-participants.ts
export async function inviteToGroup(
  client: ApiClient,
  conversationId: string,
  userIds: string[],
): Promise<{ participants: ConversationParticipant[]; conversation: Conversation }> {
  const res = await client.post(
    `/api/v1/conversations/${conversationId}/invite-to-group`,
    { user_ids: userIds },
  )
  return await res.json()
}

export async function listConversationParticipants(
  client: ApiClient,
  conversationId: string,
): Promise<{ items: ConversationParticipant[] }> {
  const res = await client.get(`/api/v1/conversations/${conversationId}/participants`)
  return await res.json()
}
```

Export from `api/index.ts`.

- [ ] **Step 3: ConversationStore additions**

```ts
interface ConversationStore {
  // ... existing fields
  conversationParticipants: Record<string, ConversationParticipant[]>

  inviteToGroup(client: ApiClient, conversationId: string, userIds: string[]): Promise<void>
  fetchConversationParticipants(client: ApiClient, conversationId: string): Promise<void>
}
```

Implementation: call the API, set `conversationParticipants[conv_id]`,
update the corresponding `conversations` entry with `is_group_chat:
true` from the response.

- [ ] **Step 4: Build + Commit**

```bash
pnpm --filter @cubebox/core build
git add -A
git commit -m "feat(core): conversation_participants types + store actions"
```

---

### Task 9: Frontend — sidebar grouping with three kinds of entries

**Files:**
- Modify: `frontend/packages/web/components/layout/Sidebar.tsx`

- [ ] **Step 1: Extend MixedEntry**

```ts
type MixedEntry =
  | { kind: 'conversation'; conversation: Conversation; sortKey: number }
  | { kind: 'group-chat'; conversation: Conversation; sortKey: number }
  | { kind: 'topic'; topic: Topic; conversations: Conversation[]; sortKey: number }
```

- [ ] **Step 2: Split conversations in buildMixedList**

Standalone group chats (`topic_id IS NULL ∧ is_group_chat`) become their own kind. Personal conversations stay as 'conversation'.

- [ ] **Step 3: Render group chat row**

Show a small `Users` icon next to the title. Optionally render a tiny participant count badge. Click navigates to the conversation page like a normal row.

- [ ] **Step 4: Build + verify**

```bash
pnpm --filter web build
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(sidebar): standalone group chat rendered as its own kind"
```

---

### Task 10: Frontend — InviteToConversationDialog + header button

**Files:**
- Create: `frontend/packages/web/components/dialogs/InviteToConversationDialog.tsx`
- Modify: `frontend/packages/web/components/layout/AppShell.tsx`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`

- [ ] **Step 1: Create the dialog**

Same shape as `CreateGroupChatDialog` but only collects member ids. Calls `inviteToGroup(client, conversationId, userIds)`.

- [ ] **Step 2: Reuse WorkspaceMemberPicker**

Filter out users who are already in `conversationParticipants[conv_id]`.

- [ ] **Step 3: Header button**

In the conversation header (AppShell), add a `UserPlus` button visible
when the conversation is loaded. Click → opens dialog.

The same button also handles topic conversations: when
`conversation.topic_id != null`, calls a different endpoint
(`POST /conversations/{id}/participants` — the topic-conv invite). Same
component, two API targets.

- [ ] **Step 4: i18n**

```json
{
  "conversation": {
    "invite": { "title": "Invite participants", "button": "Invite", ... }
  }
}
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(ui): invite-to-group dialog + conversation header button"
```

---

### Task 11: Frontend — header avatar strip + ConversationMemberPanel

**Files:**
- Create: `frontend/packages/web/components/chat/ConversationMemberStrip.tsx`
- Create: `frontend/packages/web/components/chat/ConversationMemberPanel.tsx`
- Modify: `frontend/packages/web/components/layout/AppShell.tsx`

- [ ] **Step 1: ConversationMemberStrip**

Stacked avatars of `conversationParticipants[conv_id]`. Click opens
`ConversationMemberPanel`.

Rendered when `conversation.is_group_chat`. Reads from
`useConversationStore().conversationParticipants[conv_id]`. Fetches via
`fetchConversationParticipants` on mount if absent.

- [ ] **Step 2: ConversationMemberPanel**

Lists current conv participants. No remove action (no leave
semantic). Inline "Invite more" link → opens
`InviteToConversationDialog`.

- [ ] **Step 3: Mount in AppShell**

Render the strip in the conversation header when `conversation` is set
and `is_group_chat=true`. Place next to the existing topic group badge
(if the conv is also in a topic, show both: topic badge first, then
conv strip).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(ui): conversation member strip + panel for group chat"
```

---

### Task 12: Frontend — split upgrade dialogs

**Files:**
- Modify: `frontend/packages/web/components/dialogs/UpgradeToTopicDialog.tsx`
- Modify: `frontend/packages/web/components/layout/AppShell.tsx`

- [ ] **Step 1: Rename + scope**

`UpgradeToTopicDialog` keeps its current behavior but the entry button
changes to a `Layers` icon labeled "Promote to topic". Visible only
when `conversation.topic_id IS NULL`.

The button next to it (UserPlus) is the
`InviteToConversationDialog` from Task 10. Both available on
standalone group chats.

- [ ] **Step 2: Updated copy**

Clarify in en/zh that promoting a standalone group chat to a topic
makes it a persistent container with the same participants.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat(ui): split upgrade affordances (invite vs promote-to-topic)"
```

---

### Task 13: Three rounds of code review + fixes

Per CLAUDE.md project workflow.

- [ ] **Round 1**: `/code-review --fix` on the cumulative diff
- [ ] **Round 2**: same after round-1 commits
- [ ] **Round 3**: same after round-2 commits

Each round commits its fixes as a separate commit
(`fix: code-review round N`).

---

### Task 14: Pre-PR sweep

- [ ] **Step 1: Unit + integration**

```bash
cd backend && uv run pytest tests/unit tests/integration --tb=short -q
```

- [ ] **Step 2: E2E**

```bash
uv run pytest tests/e2e --tb=short -q
```

Triage failures: regressions (must fix), pre-existing on main (document).

- [ ] **Step 3: Frontend build + lint**

```bash
cd frontend && pnpm --filter @cubebox/core build && pnpm --filter web build && pnpm --filter web lint
```

- [ ] **Step 4: Push + update PR description**

```bash
git push
gh pr edit 250 --body "$(cat <<'EOF'
... updated body reflecting the two-tier ACL refactor ...
EOF
)"
```

---

## Self-review checklist (run after writing the plan)

- Spec coverage: every section of the spec maps to at least one task. ✓
- Placeholder scan: no TBD / TODO / "fill in".
- Type consistency: ConversationParticipant, scope_type/scope_id, RunContext.conversation_id used consistently across tasks.
- Migration story is unambiguous (reset + single regenerate).
- Permissions table from spec is implemented in Tasks 5–6.

---

## Known follow-ups (deferred, documented)

- @mention with topic-vs-conv distinction
- Per-conversation memory
- Per-conversation sandbox_mode override (currently topic-level only)
- IM / triggers / scheduled-task topic-awareness (v2)
- Leave-conversation action (intentionally absent)
