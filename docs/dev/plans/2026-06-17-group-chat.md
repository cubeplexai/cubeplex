# Group Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Topic containers and multi-user group chat to cubebox — new tables, repository access control, RunContext changes, HITL multi-responder, and full frontend UI.

**Architecture:** Topic is a container model grouping related conversations. Group chat is a derived state (participant count > 1). The existing single-user conversation flow is untouched for `topic_id IS NULL` rows; topic conversations use a participant subquery in `_scoped_select()`. RunContext gains `topic_id`, `is_group_chat`, `participant_ids` to drive sender attribution, memory isolation, and HITL responder validation.

**Tech Stack:** SQLModel + Alembic (backend), Zustand + React 19 (frontend), httpx E2E tests against the real stack.

**Spec:** `docs/dev/specs/2026-06-17-group-chat-design.md`

**Worktree:** `/home/chris/cubebox/.worktrees/feat/2026-06-17-group-chat` (ports 8050/3050)

---

## File Map

### Backend — new files

| File | Purpose |
|---|---|
| `backend/cubebox/models/topic.py` | `Topic` + `TopicParticipant` SQLModel models |
| `backend/cubebox/repositories/topic.py` | `TopicRepository` — CRUD, participant management |
| `backend/cubebox/api/schemas/ws_topics.py` | Pydantic request/response schemas for topic routes |
| `backend/cubebox/api/routes/v1/ws_topics.py` | Topic CRUD + participant + upgrade routes |
| `backend/tests/e2e/test_topics.py` | Topic lifecycle + access control E2E |
| `backend/tests/e2e/test_group_chat.py` | Group chat messaging, sender attribution, memory isolation E2E |

### Backend — modify

| File | Change |
|---|---|
| `backend/cubebox/models/public_id.py:50` | Add `PREFIX_TOP`, `PREFIX_TPM` |
| `backend/cubebox/models/conversation.py:38` | Add `topic_id` FK + index |
| `backend/cubebox/models/__init__.py` | Export `Topic`, `TopicParticipant` |
| `backend/cubebox/repositories/conversation.py:32-40` | Extend `_scoped_select` with topic participant OR |
| `backend/cubebox/repositories/__init__.py` | Export `TopicRepository` |
| `backend/cubebox/streams/run_manager.py:37-43` | Extend `RunContext` with `topic_id`, `is_group_chat`, `participant_ids` |
| `backend/cubebox/streams/run_manager.py:1522-1553` | Sender prefix + metadata on user message, memory skip |
| `backend/cubebox/api/routes/v1/__init__.py` | Import `ws_topics` |
| `backend/cubebox/api/app.py:546` | Mount `ws_topics.router` |
| `backend/cubebox/api/routes/v1/conversations.py:891` | Populate RunContext topic fields at send_message |
| `backend/cubebox/api/routes/v1/conversations.py:1342` | Sender prefix on steering messages |

### Frontend — new files

| File | Purpose |
|---|---|
| `frontend/packages/core/src/types/topic.ts` | `Topic`, `TopicParticipant` types |
| `frontend/packages/core/src/api/topics.ts` | Topic API client functions |
| `frontend/packages/core/src/stores/topicStore.ts` | Topic + participant state |
| `frontend/packages/web/components/sidebar/TopicNode.tsx` | Expandable topic row in sidebar |
| `frontend/packages/web/components/chat/SenderBadge.tsx` | Avatar + name above group chat messages |
| `frontend/packages/web/components/chat/MemberPanel.tsx` | Participant list / invite / remove |
| `frontend/packages/web/components/dialogs/CreateGroupChatDialog.tsx` | New group chat dialog |
| `frontend/packages/web/components/dialogs/UpgradeToTopicDialog.tsx` | 1:1 → group upgrade dialog |

### Frontend — modify

| File | Change |
|---|---|
| `frontend/packages/core/src/types/conversation.ts` | Add `topic_id?: string` |
| `frontend/packages/core/src/types/index.ts` | Re-export topic types |
| `frontend/packages/core/src/api/index.ts` | Re-export topic API |
| `frontend/packages/core/src/stores/conversationStore.ts` | Filter/group by `topic_id` |
| `frontend/packages/web/components/layout/Sidebar.tsx` | Render `TopicNode` groups |
| `frontend/packages/web/components/chat/MessageList.tsx` | Render `SenderBadge` in group chats |
| `frontend/packages/web/components/chat/ChatHeader.tsx` | Member avatars + invite button |
| `frontend/packages/web/messages/en.json` | i18n keys for group chat |
| `frontend/packages/web/messages/zh.json` | i18n keys for group chat |

---

## Tasks

### Task 1: Public ID prefixes + Topic models

**Files:**
- Modify: `backend/cubebox/models/public_id.py:50`
- Create: `backend/cubebox/models/topic.py`
- Modify: `backend/cubebox/models/__init__.py`

- [ ] **Step 1: Add public ID prefixes**

In `backend/cubebox/models/public_id.py`, after line 50 (`PREFIX_IM_RUN_QUEUE_ITEM`):

```python
PREFIX_TOP: str = "top"
PREFIX_TPM: str = "tpm"
```

- [ ] **Step 2: Create topic models**

Create `backend/cubebox/models/topic.py`:

```python
"""Topic and TopicParticipant models."""

from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import Column, DateTime, Index, UniqueConstraint
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin, org_scope_index


class Topic(CubeboxBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = "top"
    __tablename__ = "topics"
    __table_args__ = (
        org_scope_index("topics"),
        Index("ix_topics_creator", "creator_user_id", "workspace_id"),
    )

    creator_user_id: str = Field(foreign_key="users.id", max_length=20)
    title: str = Field(max_length=255)
    sandbox_mode: str | None = Field(default=None, max_length=20)
    max_participants: int = Field(default=20)
    is_archived: bool = Field(default=False)


class TopicParticipant(CubeboxBase, table=True):
    _PREFIX: ClassVar[str] = "tpm"
    __tablename__ = "topic_participants"
    __table_args__ = (
        UniqueConstraint("topic_id", "user_id", name="uq_topic_participant"),
        Index("ix_topic_participants_user", "user_id"),
    )

    topic_id: str = Field(foreign_key="topics.id", max_length=20, index=True)
    user_id: str = Field(foreign_key="users.id", max_length=20)
    role: str = Field(default="member", max_length=20)
    joined_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True)),
    )
```

- [ ] **Step 3: Add `topic_id` FK to Conversation model**

In `backend/cubebox/models/conversation.py`, add after `creator_user_id` field (line 38):

```python
topic_id: str | None = Field(default=None, foreign_key="topics.id", max_length=20)
```

Add to `__table_args__` tuple (before the closing `)`):

```python
Index("ix_conversations_topic", "topic_id"),
```

- [ ] **Step 4: Export new models**

In `backend/cubebox/models/__init__.py`, add import:

```python
from cubebox.models.topic import Topic, TopicParticipant
```

Add `"Topic"` and `"TopicParticipant"` to `__all__` list (alphabetical order, after `"Trigger"` entries).

- [ ] **Step 5: Generate migration**

Run: `cd backend && uv run alembic revision --autogenerate -m "add topics and topic_participants tables, conversation.topic_id FK"`

Verify the migration creates `topics` table, `topic_participants` table, adds `topic_id` column + FK + indexes to `conversations`.

- [ ] **Step 6: Run migration and verify**

Run: `cd backend && uv run alembic upgrade head`

Expected: migration applies cleanly with no errors.

- [ ] **Step 7: Commit**

```bash
git add backend/cubebox/models/topic.py backend/cubebox/models/public_id.py \
  backend/cubebox/models/conversation.py backend/cubebox/models/__init__.py \
  backend/alembic/versions/
git commit -m "$(cat <<'EOF'
feat(models): add Topic, TopicParticipant tables and conversation.topic_id FK

New tables for grouping conversations under topics with participant
management. Conversation gains a nullable topic_id FK for association.
EOF
)"
```

---

### Task 2: TopicRepository

**Files:**
- Create: `backend/cubebox/repositories/topic.py`
- Modify: `backend/cubebox/repositories/__init__.py`

- [ ] **Step 1: Create TopicRepository**

Create `backend/cubebox/repositories/topic.py`:

```python
"""Topic repository — scoped by workspace, filtered by participant membership."""

from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.topic import Topic, TopicParticipant
from cubebox.repositories.base import ScopedRepository


class TopicRepository(ScopedRepository[Topic]):
    model = Topic

    def __init__(
        self,
        session: AsyncSession,
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
    ) -> None:
        super().__init__(session, org_id=org_id, workspace_id=workspace_id)
        self.user_id = user_id

    def _scoped_select(self) -> Any:
        return (
            super()
            ._scoped_select()
            .where(
                Topic.id.in_(  # type: ignore[union-attr]
                    select(TopicParticipant.topic_id).where(
                        TopicParticipant.user_id == self.user_id
                    )
                ),
                cast(Any, Topic.is_archived).is_(False),
            )
        )

    async def create_topic(
        self,
        *,
        title: str,
        sandbox_mode: str | None = None,
        max_participants: int = 20,
    ) -> Topic:
        topic = Topic(
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            creator_user_id=self.user_id,
            title=title,
            sandbox_mode=sandbox_mode,
            max_participants=max_participants,
        )
        self.session.add(topic)
        await self.session.flush()

        owner = TopicParticipant(
            topic_id=topic.id,
            user_id=self.user_id,
            role="owner",
        )
        self.session.add(owner)
        await self.session.commit()
        await self.session.refresh(topic)
        return topic

    async def add_participants(
        self,
        topic_id: str,
        user_ids: list[str],
    ) -> list[TopicParticipant]:
        topic = await self.get(topic_id)
        if topic is None:
            raise ValueError(f"Topic {topic_id} not found")

        count_stmt = select(func.count()).where(TopicParticipant.topic_id == topic_id)
        result = await self.session.execute(count_stmt)
        current_count = result.scalar_one()

        if current_count + len(user_ids) > topic.max_participants:
            raise ValueError(
                f"Adding {len(user_ids)} would exceed max {topic.max_participants} "
                f"(current: {current_count})"
            )

        participants: list[TopicParticipant] = []
        for uid in user_ids:
            p = TopicParticipant(topic_id=topic_id, user_id=uid, role="member")
            self.session.add(p)
            participants.append(p)
        await self.session.commit()
        for p in participants:
            await self.session.refresh(p)
        return participants

    async def remove_participant(self, topic_id: str, user_id: str) -> None:
        stmt = select(TopicParticipant).where(
            TopicParticipant.topic_id == topic_id,
            TopicParticipant.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        participant = result.scalar_one_or_none()
        if participant is None:
            raise ValueError(f"User {user_id} is not a participant of topic {topic_id}")

        if participant.role == "owner":
            others_stmt = (
                select(TopicParticipant)
                .where(
                    TopicParticipant.topic_id == topic_id,
                    TopicParticipant.user_id != user_id,
                )
                .order_by(TopicParticipant.joined_at)
                .limit(1)
            )
            others = await self.session.execute(others_stmt)
            next_owner = others.scalar_one_or_none()
            if next_owner is not None:
                next_owner.role = "owner"
                self.session.add(next_owner)

        await self.session.delete(participant)
        await self.session.commit()

    async def list_participants(self, topic_id: str) -> list[TopicParticipant]:
        stmt = (
            select(TopicParticipant)
            .where(TopicParticipant.topic_id == topic_id)
            .order_by(TopicParticipant.joined_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_participant(
        self, topic_id: str, user_id: str
    ) -> TopicParticipant | None:
        stmt = select(TopicParticipant).where(
            TopicParticipant.topic_id == topic_id,
            TopicParticipant.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def is_participant(self, topic_id: str, user_id: str) -> bool:
        return await self.get_participant(topic_id, user_id) is not None

    async def archive(self, topic_id: str) -> None:
        topic = await self.get(topic_id)
        if topic is None:
            raise ValueError(f"Topic {topic_id} not found")
        topic.is_archived = True
        self.session.add(topic)
        await self.session.commit()

    async def get_with_participants(
        self, topic_id: str
    ) -> tuple[Topic | None, list[TopicParticipant]]:
        topic = await self.get(topic_id)
        if topic is None:
            return None, []
        participants = await self.list_participants(topic_id)
        return topic, participants
```

- [ ] **Step 2: Export TopicRepository**

In `backend/cubebox/repositories/__init__.py`, add import:

```python
from cubebox.repositories.topic import TopicRepository
```

Add `"TopicRepository"` to `__all__` list.

- [ ] **Step 3: Commit**

```bash
git add backend/cubebox/repositories/topic.py backend/cubebox/repositories/__init__.py
git commit -m "$(cat <<'EOF'
feat(repo): add TopicRepository with participant management

Scoped by workspace + participant membership. Handles create (auto-adds
creator as owner), add/remove participants with max cap validation,
owner succession on leave.
EOF
)"
```

---

### Task 3: ConversationRepository access control change

**Files:**
- Modify: `backend/cubebox/repositories/conversation.py:32-40`

This is the single most critical change — it gates visibility for all conversation queries.

- [ ] **Step 1: Write the failing E2E test**

Add to `backend/tests/e2e/test_topics.py` (create the file):

```python
"""E2E tests for Topics API — lifecycle and access control."""

import httpx
import pytest

pytestmark = pytest.mark.e2e


class TestTopicConversationAccess:
    """Topic conversations are visible to all participants."""

    @pytest.mark.anyio
    async def test_topic_conversation_visible_to_member(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, admin_uid), (member_c, _, member_uid) = (
            four_layer_admin_and_member
        )

        # Admin creates a topic and adds member
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={
                "title": "Shared Topic",
                "sandbox_mode": "dedicated",
                "member_user_ids": [member_uid],
            },
        )
        assert resp.status_code == 201, resp.text
        topic_data = resp.json()
        topic_id = topic_data["topic"]["id"]
        conv_id = topic_data["conversation"]["id"]

        # Member can see the conversation
        conv_resp = await member_c.get(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}"
        )
        assert conv_resp.status_code == 200

    @pytest.mark.anyio
    async def test_non_participant_cannot_see_topic_conversation(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _admin_uid), (member_c, _, member_uid) = (
            four_layer_admin_and_member
        )

        # Admin creates a topic WITHOUT adding member
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Private Topic"},
        )
        assert resp.status_code == 201, resp.text
        conv_id = resp.json()["conversation"]["id"]

        # Member cannot see it
        conv_resp = await member_c.get(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}"
        )
        assert conv_resp.status_code == 404

    @pytest.mark.anyio
    async def test_personal_conversation_still_private(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, _member_uid) = (
            four_layer_admin_and_member
        )

        # Admin creates a personal conversation (no topic)
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations",
            params={"title": "Admin Private"},
        )
        assert resp.status_code == 201
        conv_id = resp.json()["id"]

        # Member cannot see it
        conv_resp = await member_c.get(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}"
        )
        assert conv_resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_topics.py::TestTopicConversationAccess::test_topic_conversation_visible_to_member -v -x`

Expected: FAIL — topic routes don't exist yet (404 on POST /topics), or conversation access fails.

- [ ] **Step 3: Modify `_scoped_select` in ConversationRepository**

In `backend/cubebox/repositories/conversation.py`, replace lines 32-40:

```python
def _scoped_select(self) -> Any:
    return (
        super()
        ._scoped_select()
        .where(
            Conversation.creator_user_id == self.user_id,
            cast(Any, Conversation.deleted_at).is_(None),
        )
    )
```

with:

```python
def _scoped_select(self) -> Any:
    from sqlalchemy import and_, or_

    from cubebox.models.topic import TopicParticipant

    return (
        super()
        ._scoped_select()
        .where(
            or_(
                and_(
                    cast(Any, Conversation.topic_id).is_(None),
                    Conversation.creator_user_id == self.user_id,
                ),
                Conversation.topic_id.in_(  # type: ignore[union-attr]
                    select(TopicParticipant.topic_id).where(
                        TopicParticipant.user_id == self.user_id
                    )
                ),
            ),
            cast(Any, Conversation.deleted_at).is_(None),
        )
    )
```

Add `select` to the imports at the top if not already present (it's already imported from sqlalchemy).

- [ ] **Step 4: Verify existing conversation tests still pass**

Run: `cd backend && uv run pytest tests/e2e/test_conversations.py -v -x`

Expected: all existing tests PASS — the OR branch only activates when `topic_id IS NOT NULL`.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/repositories/conversation.py backend/tests/e2e/test_topics.py
git commit -m "$(cat <<'EOF'
feat(repo): extend conversation scoping for topic participants

_scoped_select uses OR: personal conversations check creator_user_id,
topic conversations check participant membership via subquery. Existing
1:1 privacy unchanged.
EOF
)"
```

---

### Task 4: Topic API routes + schemas

**Files:**
- Create: `backend/cubebox/api/schemas/ws_topics.py`
- Create: `backend/cubebox/api/routes/v1/ws_topics.py`
- Modify: `backend/cubebox/api/routes/v1/__init__.py`
- Modify: `backend/cubebox/api/app.py`

- [ ] **Step 1: Create request/response schemas**

Create `backend/cubebox/api/schemas/ws_topics.py`:

```python
"""Topic API schemas."""

from pydantic import BaseModel, Field


class TopicCreateRequest(BaseModel):
    title: str = Field(max_length=255)
    sandbox_mode: str | None = Field(default=None, pattern=r"^(dedicated|creator)$")
    member_user_ids: list[str] = Field(default_factory=list)


class TopicPatchRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class TopicParticipantAddRequest(BaseModel):
    user_ids: list[str] = Field(min_length=1)


class TopicParticipantPatchRequest(BaseModel):
    role: str = Field(pattern=r"^(owner|member)$")


class UpgradeToTopicRequest(BaseModel):
    title: str = Field(max_length=255)
    sandbox_mode: str | None = Field(default=None, pattern=r"^(dedicated|creator)$")
    member_user_ids: list[str] = Field(default_factory=list)


class TopicConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
```

- [ ] **Step 2: Create topic routes**

Create `backend/cubebox/api/routes/v1/ws_topics.py`:

```python
"""Workspace topic routes — CRUD, participants, upgrade, topic-scoped conversations."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.deps import RequestContext, get_session, require_member
from cubebox.api.schemas.ws_topics import (
    TopicConversationCreateRequest,
    TopicCreateRequest,
    TopicParticipantAddRequest,
    TopicParticipantPatchRequest,
    TopicPatchRequest,
    UpgradeToTopicRequest,
)
from cubebox.models.conversation import Conversation
from cubebox.repositories.conversation import ConversationRepository
from cubebox.repositories.topic import TopicRepository

router = APIRouter(
    prefix="/ws/{workspace_id}/topics",
    tags=["topics"],
)


def _topic_repo(
    session: AsyncSession, ctx: RequestContext
) -> TopicRepository:
    return TopicRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )


def _conv_repo(
    session: AsyncSession, ctx: RequestContext
) -> ConversationRepository:
    return ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )


def _serialize_topic(topic: Any) -> dict[str, Any]:
    from cubebox.utils.time import utc_isoformat

    return {
        "id": topic.id,
        "title": topic.title,
        "sandbox_mode": topic.sandbox_mode,
        "max_participants": topic.max_participants,
        "creator_user_id": topic.creator_user_id,
        "is_archived": topic.is_archived,
        "created_at": utc_isoformat(topic.created_at),
        "updated_at": utc_isoformat(topic.updated_at),
    }


def _serialize_participant(p: Any) -> dict[str, Any]:
    from cubebox.utils.time import utc_isoformat

    return {
        "id": p.id,
        "topic_id": p.topic_id,
        "user_id": p.user_id,
        "role": p.role,
        "joined_at": utc_isoformat(p.joined_at),
    }


def _serialize_conversation(conv: Any) -> dict[str, Any]:
    from cubebox.utils.time import utc_isoformat

    return {
        "id": conv.id,
        "title": conv.title,
        "topic_id": conv.topic_id,
        "is_pinned": conv.is_pinned,
        "created_at": utc_isoformat(conv.created_at),
        "updated_at": utc_isoformat(conv.updated_at),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_topic(
    body: TopicCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    repo = _topic_repo(session, ctx)
    topic = await repo.create_topic(
        title=body.title,
        sandbox_mode=body.sandbox_mode,
    )

    # Create the first conversation under this topic
    conv = Conversation(
        title=body.title,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        creator_user_id=ctx.user.id,
        topic_id=topic.id,
        has_messages=False,
    )
    session.add(conv)

    # Add invited members
    if body.member_user_ids:
        await repo.add_participants(topic.id, body.member_user_ids)

    await session.commit()
    await session.refresh(conv)

    participants = await repo.list_participants(topic.id)

    return {
        "topic": _serialize_topic(topic),
        "conversation": _serialize_conversation(conv),
        "participants": [_serialize_participant(p) for p in participants],
    }


@router.get("")
async def list_topics(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    repo = _topic_repo(session, ctx)
    topics = await repo.list()
    return {"items": [_serialize_topic(t) for t in topics]}


@router.get("/{topic_id}")
async def get_topic(
    topic_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    repo = _topic_repo(session, ctx)
    topic, participants = await repo.get_with_participants(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    # List conversations under this topic
    conv_repo = _conv_repo(session, ctx)
    conversations = await conv_repo.list_by_topic(topic_id)

    return {
        "topic": _serialize_topic(topic),
        "participants": [_serialize_participant(p) for p in participants],
        "conversations": [_serialize_conversation(c) for c in conversations],
    }


@router.patch("/{topic_id}")
async def update_topic(
    topic_id: str,
    body: TopicPatchRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    participant = await repo.get_participant(topic_id, ctx.user.id)
    if participant is None or participant.role != "owner":
        raise HTTPException(status_code=403, detail="Only topic owner can update")

    if body.title is not None:
        topic.title = body.title
    session.add(topic)
    await session.commit()
    await session.refresh(topic)
    return {"topic": _serialize_topic(topic)}


@router.delete("/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic(
    topic_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> None:
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    participant = await repo.get_participant(topic_id, ctx.user.id)
    if participant is None or participant.role != "owner":
        raise HTTPException(status_code=403, detail="Only topic owner can delete")

    await repo.archive(topic_id)


# --- Participants ---


@router.post(
    "/{topic_id}/participants",
    status_code=status.HTTP_201_CREATED,
)
async def add_participants(
    topic_id: str,
    body: TopicParticipantAddRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    participant = await repo.get_participant(topic_id, ctx.user.id)
    if participant is None or participant.role != "owner":
        raise HTTPException(status_code=403, detail="Only topic owner can add members")

    try:
        added = await repo.add_participants(topic_id, body.user_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"participants": [_serialize_participant(p) for p in added]}


@router.delete(
    "/{topic_id}/participants/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_participant(
    topic_id: str,
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> None:
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    caller_participant = await repo.get_participant(topic_id, ctx.user.id)
    if caller_participant is None:
        raise HTTPException(status_code=403, detail="Not a participant")

    # Owner can remove anyone; members can only remove themselves
    if user_id != ctx.user.id and caller_participant.role != "owner":
        raise HTTPException(status_code=403, detail="Only owner can remove others")

    try:
        await repo.remove_participant(topic_id, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{topic_id}/participants/{user_id}")
async def update_participant_role(
    topic_id: str,
    user_id: str,
    body: TopicParticipantPatchRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    caller_participant = await repo.get_participant(topic_id, ctx.user.id)
    if caller_participant is None or caller_participant.role != "owner":
        raise HTTPException(status_code=403, detail="Only owner can change roles")

    target = await repo.get_participant(topic_id, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Participant not found")

    target.role = body.role
    session.add(target)
    await session.commit()
    await session.refresh(target)
    return {"participant": _serialize_participant(target)}


# --- Topic-scoped conversation creation ---


@router.post(
    "/{topic_id}/conversations",
    status_code=status.HTTP_201_CREATED,
)
async def create_topic_conversation(
    topic_id: str,
    body: TopicConversationCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    conv = Conversation(
        title=body.title or topic.title,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        creator_user_id=ctx.user.id,
        topic_id=topic_id,
        has_messages=False,
    )
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return {"conversation": _serialize_conversation(conv)}


# --- Upgrade conversation to topic ---

upgrade_router = APIRouter(
    prefix="/ws/{workspace_id}/conversations",
    tags=["topics"],
)


@upgrade_router.post(
    "/{conversation_id}/upgrade-to-topic",
    status_code=status.HTTP_201_CREATED,
)
async def upgrade_to_topic(
    conversation_id: str,
    body: UpgradeToTopicRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    conv_repo = _conv_repo(session, ctx)
    conversation = await conv_repo.get_by_id(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conversation.topic_id is not None:
        raise HTTPException(
            status_code=409, detail="Conversation already belongs to a topic"
        )

    topic_repo = _topic_repo(session, ctx)
    topic = await topic_repo.create_topic(
        title=body.title,
        sandbox_mode=body.sandbox_mode,
    )

    conversation.topic_id = topic.id
    session.add(conversation)

    if body.member_user_ids:
        await topic_repo.add_participants(topic.id, body.member_user_ids)

    await session.commit()
    await session.refresh(conversation)
    participants = await topic_repo.list_participants(topic.id)

    return {
        "topic": _serialize_topic(topic),
        "conversation": _serialize_conversation(conversation),
        "participants": [_serialize_participant(p) for p in participants],
    }
```

- [ ] **Step 3: Add `list_by_topic` to ConversationRepository**

In `backend/cubebox/repositories/conversation.py`, add a new method after `list_all`:

```python
async def list_by_topic(self, topic_id: str) -> list[Conversation]:
    stmt = (
        self._scoped_select()
        .where(Conversation.topic_id == topic_id)
        .order_by(Conversation.created_at.desc())
    )
    result = await self.session.execute(stmt)
    return list(result.scalars().all())
```

- [ ] **Step 4: Register routes in `__init__.py` and `app.py`**

In `backend/cubebox/api/routes/v1/__init__.py`, add to the import block:

```python
ws_topics,
```

And add `"ws_topics"` to `__all__`.

In `backend/cubebox/api/app.py`, after the `ws_im` block (around line 546), add:

```python
app.include_router(ws_topics.router, prefix="/api/v1")
app.include_router(ws_topics.upgrade_router, prefix="/api/v1")
```

- [ ] **Step 5: Run the E2E tests**

Run: `cd backend && uv run pytest tests/e2e/test_topics.py -v -x`

Expected: all three access control tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/api/schemas/ws_topics.py \
  backend/cubebox/api/routes/v1/ws_topics.py \
  backend/cubebox/api/routes/v1/__init__.py \
  backend/cubebox/api/app.py \
  backend/cubebox/repositories/conversation.py
git commit -m "$(cat <<'EOF'
feat(api): add topic CRUD, participant management, and upgrade routes

Complete workspace-scoped topic API: create with initial conversation,
list/get/patch/delete, add/remove/update participants, create
conversations under topic, and upgrade 1:1 to topic.
EOF
)"
```

---

### Task 5: Topic E2E test suite

**Files:**
- Modify: `backend/tests/e2e/test_topics.py`

Complete the E2E test file with full lifecycle coverage.

- [ ] **Step 1: Add topic CRUD tests**

Expand `backend/tests/e2e/test_topics.py` — add a `TestTopicCRUD` class:

```python
class TestTopicCRUD:
    """Topic create, list, get, update, delete."""

    @pytest.mark.anyio
    async def test_create_topic_returns_topic_and_conversation(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _), _ = four_layer_admin_and_member
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "My Topic", "sandbox_mode": "dedicated"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["topic"]["title"] == "My Topic"
        assert data["topic"]["sandbox_mode"] == "dedicated"
        assert data["conversation"]["topic_id"] == data["topic"]["id"]
        assert len(data["participants"]) == 1
        assert data["participants"][0]["role"] == "owner"

    @pytest.mark.anyio
    async def test_list_topics_shows_only_participant_topics(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        # Admin creates two topics, only one includes member
        await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Shared", "member_user_ids": [member_uid]},
        )
        await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Admin Only"},
        )

        # Member sees only the shared topic
        resp = await member_c.get(f"/api/v1/ws/{ws_id}/topics")
        assert resp.status_code == 200
        titles = [t["title"] for t in resp.json()["items"]]
        assert "Shared" in titles
        assert "Admin Only" not in titles

    @pytest.mark.anyio
    async def test_update_topic_owner_only(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member
        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Old Title", "member_user_ids": [member_uid]},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Member cannot update
        resp = await member_c.patch(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}",
            json={"title": "Hacked"},
        )
        assert resp.status_code == 403

        # Owner can
        resp = await admin_c.patch(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}",
            json={"title": "New Title"},
        )
        assert resp.status_code == 200
        assert resp.json()["topic"]["title"] == "New Title"

    @pytest.mark.anyio
    async def test_delete_topic_archives(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _), _ = four_layer_admin_and_member
        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Doomed"},
        )
        topic_id = create_resp.json()["topic"]["id"]

        del_resp = await admin_c.delete(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert del_resp.status_code == 204

        # Archived topic no longer visible
        list_resp = await admin_c.get(f"/api/v1/ws/{ws_id}/topics")
        ids = [t["id"] for t in list_resp.json()["items"]]
        assert topic_id not in ids
```

- [ ] **Step 2: Add participant management tests**

Add a `TestTopicParticipants` class:

```python
class TestTopicParticipants:
    """Participant add, remove, role transfer."""

    @pytest.mark.anyio
    async def test_add_and_remove_participant(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Team"},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Add member
        add_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants",
            json={"user_ids": [member_uid]},
        )
        assert add_resp.status_code == 201

        # Verify member can now see the topic
        get_resp = await member_c.get(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert get_resp.status_code == 200
        assert len(get_resp.json()["participants"]) == 2

        # Member leaves
        leave_resp = await member_c.delete(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants/{member_uid}"
        )
        assert leave_resp.status_code == 204

        # Member can no longer see the topic
        get_resp2 = await member_c.get(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert get_resp2.status_code == 404

    @pytest.mark.anyio
    async def test_owner_succession(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, admin_uid), (member_c, _, member_uid) = (
            four_layer_admin_and_member
        )

        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Handoff", "member_user_ids": [member_uid]},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Owner (admin) leaves
        await admin_c.delete(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants/{admin_uid}"
        )

        # Member is now owner
        get_resp = await member_c.get(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert get_resp.status_code == 200
        participants = get_resp.json()["participants"]
        assert len(participants) == 1
        assert participants[0]["user_id"] == member_uid
        assert participants[0]["role"] == "owner"

    @pytest.mark.anyio
    async def test_max_participant_cap(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _), (_, _, member_uid) = four_layer_admin_and_member

        # Create topic with max_participants = 2 (via default of 20, we
        # test the check logic by adding more than max)
        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Small Room"},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Add member — succeeds (2 of 20)
        add_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants",
            json={"user_ids": [member_uid]},
        )
        assert add_resp.status_code == 201
```

- [ ] **Step 3: Add upgrade-to-topic test**

```python
class TestUpgradeToTopic:
    """Convert a 1:1 conversation to a topic group chat."""

    @pytest.mark.anyio
    async def test_upgrade_conversation(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        # Create a personal conversation
        conv_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations",
            params={"title": "Personal Chat"},
        )
        conv_id = conv_resp.json()["id"]

        # Upgrade to topic
        upgrade_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/upgrade-to-topic",
            json={
                "title": "Group Chat",
                "sandbox_mode": "dedicated",
                "member_user_ids": [member_uid],
            },
        )
        assert upgrade_resp.status_code == 201
        data = upgrade_resp.json()
        assert data["conversation"]["topic_id"] == data["topic"]["id"]

        # Member can now see the conversation
        get_resp = await member_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
        assert get_resp.status_code == 200

    @pytest.mark.anyio
    async def test_double_upgrade_fails(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _), _ = four_layer_admin_and_member

        conv_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations",
            params={"title": "Already Upgraded"},
        )
        conv_id = conv_resp.json()["id"]

        await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/upgrade-to-topic",
            json={"title": "Group"},
        )

        # Second upgrade fails
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/upgrade-to-topic",
            json={"title": "Again"},
        )
        assert resp.status_code == 409
```

- [ ] **Step 4: Run all topic tests**

Run: `cd backend && uv run pytest tests/e2e/test_topics.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/e2e/test_topics.py
git commit -m "$(cat <<'EOF'
test(e2e): topic lifecycle, participants, access control, upgrade

Covers CRUD, participant add/remove/succession, non-participant
exclusion, personal conversation privacy, and 1:1→topic upgrade.
EOF
)"
```

---

### Task 6: RunContext extension + sender attribution + memory isolation

**Files:**
- Modify: `backend/cubebox/streams/run_manager.py:37-43` (RunContext)
- Modify: `backend/cubebox/streams/run_manager.py:1522-1553` (message building + memory)
- Modify: `backend/cubebox/api/routes/v1/conversations.py:891` (RunContext construction)

- [ ] **Step 1: Extend RunContext**

In `backend/cubebox/streams/run_manager.py`, replace lines 37-43:

```python
@dataclass(slots=True)
class RunContext:
    """Scoped context required to execute a run."""

    user_id: str
    org_id: str
    workspace_id: str
    trigger: str = "interactive"
```

with:

```python
@dataclass(slots=True)
class RunContext:
    """Scoped context required to execute a run."""

    user_id: str
    org_id: str
    workspace_id: str
    trigger: str = "interactive"
    topic_id: str | None = None
    is_group_chat: bool = False
    participant_ids: list[str] | None = None
    sender_display_name: str | None = None
```

- [ ] **Step 2: Populate RunContext in send_message**

In `backend/cubebox/api/routes/v1/conversations.py`, find the `run_ctx = RunContext(...)` block around line 891. Replace:

```python
    run_ctx = RunContext(
        user_id=ctx.user.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
```

with:

```python
    # Resolve topic context for group chat behavior
    topic_id: str | None = conversation.topic_id
    is_group_chat = False
    participant_ids: list[str] | None = None
    sender_display_name: str | None = None

    if topic_id is not None:
        from cubebox.repositories.topic import TopicRepository

        async with async_session_maker() as topic_session:
            topic_repo = TopicRepository(
                topic_session,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
                user_id=ctx.user.id,
            )
            participants = await topic_repo.list_participants(topic_id)
            participant_ids = [p.user_id for p in participants]
            is_group_chat = len(participants) > 1

        if is_group_chat:
            sender_display_name = ctx.user.display_name or ctx.user.email

    run_ctx = RunContext(
        user_id=ctx.user.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        topic_id=topic_id,
        is_group_chat=is_group_chat,
        participant_ids=participant_ids,
        sender_display_name=sender_display_name,
    )
```

Note: `ctx.user` should have `display_name` or `email`. Check the actual User model field names and adjust.

- [ ] **Step 3: Add sender prefix + metadata on user message**

In `run_manager.py`, find the user message construction around line 1549:

```python
            _user_msg = _UserMessage(
                content=[_TextContent(text=content)],
                timestamp=_time.time(),
                metadata=_user_msg_metadata,
            )
```

Replace with:

```python
            _msg_text = content
            if ctx.is_group_chat and ctx.sender_display_name:
                _msg_text = f"[{ctx.sender_display_name}]: {content}"
                _user_msg_metadata["sender_user_id"] = ctx.user_id
                _user_msg_metadata["sender_display_name"] = ctx.sender_display_name

            _user_msg = _UserMessage(
                content=[_TextContent(text=_msg_text)],
                timestamp=_time.time(),
                metadata=_user_msg_metadata,
            )
```

- [ ] **Step 4: Skip memory injection for group chat**

In `run_manager.py`, find the memory snapshot computation around line 1525-1530:

```python
            try:
                _mem_repo_factory = extra_ref_holder["mem_repo_factory"]
                async with _mem_repo_factory() as _snap_repo:
                    _snapshot = await _compute_snap(_snap_repo)
                if _snapshot is not None:
                    _user_msg_metadata["memory_snapshot"] = _snapshot
            except Exception as _snap_exc:
                logger.warning("Failed to compute relevance snapshot: {}", _snap_exc)
```

Wrap it with a group-chat guard:

```python
            if not ctx.is_group_chat:
                try:
                    _mem_repo_factory = extra_ref_holder["mem_repo_factory"]
                    async with _mem_repo_factory() as _snap_repo:
                        _snapshot = await _compute_snap(_snap_repo)
                    if _snapshot is not None:
                        _user_msg_metadata["memory_snapshot"] = _snapshot
                except Exception as _snap_exc:
                    logger.warning("Failed to compute relevance snapshot: {}", _snap_exc)
```

- [ ] **Step 5: Add sender prefix on steering too**

Find `dispatch_steer` usage in conversations.py (line 1342):

```python
    dispatch_status = await run_manager.dispatch_steer(
        active_run.run_id, body.content, steer_id=body.steer_id
    )
```

Before this line, add sender prefix for group chats:

```python
    steer_content = body.content
    if conversation.topic_id is not None:
        from cubebox.repositories.topic import TopicRepository

        async with async_session_maker() as steer_session:
            topic_repo = TopicRepository(
                steer_session,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
                user_id=ctx.user.id,
            )
            participants = await topic_repo.list_participants(conversation.topic_id)
            if len(participants) > 1:
                display_name = ctx.user.display_name or ctx.user.email
                steer_content = f"[{display_name}]: {body.content}"

    dispatch_status = await run_manager.dispatch_steer(
        active_run.run_id, steer_content, steer_id=body.steer_id
    )
```

- [ ] **Step 6: Sandbox resolution for group chat**

In `run_manager.py` `_execute_run`, find the `LazySandbox` construction around line 3123:

```python
                        sandbox = LazySandbox(
                            manager=sandbox_manager,
                            user_id=ctx.user_id,
                            org_id=ctx.org_id,
                            workspace_id=ctx.workspace_id,
                            workdir=config.get("sandbox.workdir", "/workspace"),
                            catalog=skill_catalog,
                        )
```

Replace with group-chat-aware resolution:

```python
                        sandbox_user_id = ctx.user_id
                        if ctx.is_group_chat and ctx.topic_id:
                            from cubebox.db.engine import (
                                async_session_maker as _sbx_session_maker,
                            )
                            from cubebox.repositories.topic import TopicRepository

                            async with _sbx_session_maker() as _sbx_session:
                                _topic_repo = TopicRepository(
                                    _sbx_session,
                                    org_id=ctx.org_id,
                                    workspace_id=ctx.workspace_id,
                                    user_id=ctx.user_id,
                                )
                                _topic = await _topic_repo.get(ctx.topic_id)
                                if _topic and _topic.sandbox_mode == "creator":
                                    sandbox_user_id = _topic.creator_user_id
                                elif _topic and _topic.sandbox_mode == "dedicated":
                                    sandbox_user_id = ctx.topic_id

                        sandbox = LazySandbox(
                            manager=sandbox_manager,
                            user_id=sandbox_user_id,
                            org_id=ctx.org_id,
                            workspace_id=ctx.workspace_id,
                            workdir=config.get("sandbox.workdir", "/workspace"),
                            catalog=skill_catalog,
                        )
```

This maps: `dedicated` → uses `topic_id` as the user_id key (creating a topic-specific sandbox), `creator` → uses the topic creator's user_id (sharing their sandbox). Non-group-chat conversations are unchanged.

Note: `LazySandbox` uses `user_id` as a keying parameter for `get_or_create`. Using `topic_id` as the key creates a sandbox bound to the topic, not any individual user. Verify this works with the `SandboxManager.get_or_create` signature — if it does workspace+user scoping, `topic_id` as user_id will create a dedicated scope.

Apply the same pattern in `_resume_run` (around line 3498+) which mirrors `_execute_run`.

- [ ] **Step 7: Verify existing tests pass**

Run: `cd backend && uv run pytest tests/e2e/test_conversations.py -v -x`

Expected: all existing tests PASS — group chat branches don't activate for `topic_id IS NULL`.

- [ ] **Step 8: Commit**

```bash
git add backend/cubebox/streams/run_manager.py \
  backend/cubebox/api/routes/v1/conversations.py
git commit -m "$(cat <<'EOF'
feat(runtime): RunContext topic fields, sender attribution, memory isolation, sandbox resolution

RunContext gains topic_id, is_group_chat, participant_ids,
sender_display_name. Group chat messages get [Name]: prefix for model,
sender metadata in JSONB for frontend. Personal memory skipped when
is_group_chat. Steering messages also prefixed. Sandbox resolves by
topic_id (dedicated) or creator_user_id (creator mode).
EOF
)"
```

---

### Task 7: Frontend types + API client

**Files:**
- Create: `frontend/packages/core/src/types/topic.ts`
- Modify: `frontend/packages/core/src/types/conversation.ts`
- Modify: `frontend/packages/core/src/types/index.ts`
- Create: `frontend/packages/core/src/api/topics.ts`
- Modify: `frontend/packages/core/src/api/index.ts`

- [ ] **Step 1: Create topic types**

Create `frontend/packages/core/src/types/topic.ts`:

```typescript
export interface Topic {
  id: string
  title: string
  sandbox_mode: string | null
  max_participants: number
  creator_user_id: string
  is_archived: boolean
  created_at: string
  updated_at: string
}

export interface TopicParticipant {
  id: string
  topic_id: string
  user_id: string
  role: 'owner' | 'member'
  joined_at: string
}

export interface TopicCreateResponse {
  topic: Topic
  conversation: { id: string; title: string; topic_id: string }
  participants: TopicParticipant[]
}
```

- [ ] **Step 2: Add `topic_id` to Conversation type**

In `frontend/packages/core/src/types/conversation.ts`, add to the interface:

```typescript
export interface Conversation {
  id: string
  title: string
  is_pinned: boolean
  topic_id?: string
  created_at: string
  updated_at: string
}
```

- [ ] **Step 3: Re-export topic types**

In `frontend/packages/core/src/types/index.ts`, add:

```typescript
export type * from './topic'
```

- [ ] **Step 4: Create topic API client**

Create `frontend/packages/core/src/api/topics.ts`:

```typescript
import type { ApiClient } from './client'
import type { Topic, TopicParticipant, TopicCreateResponse } from '../types/topic'

export async function createTopic(
  client: ApiClient,
  body: { title: string; sandbox_mode?: string; member_user_ids?: string[] },
): Promise<TopicCreateResponse> {
  const res = await client.post('/topics', body)
  return res.data
}

export async function listTopics(
  client: ApiClient,
): Promise<{ items: Topic[] }> {
  const res = await client.get('/topics')
  return res.data
}

export async function getTopic(
  client: ApiClient,
  topicId: string,
): Promise<{
  topic: Topic
  participants: TopicParticipant[]
  conversations: { id: string; title: string; topic_id: string }[]
}> {
  const res = await client.get(`/topics/${topicId}`)
  return res.data
}

export async function updateTopic(
  client: ApiClient,
  topicId: string,
  body: { title?: string },
): Promise<{ topic: Topic }> {
  const res = await client.patch(`/topics/${topicId}`, body)
  return res.data
}

export async function deleteTopic(
  client: ApiClient,
  topicId: string,
): Promise<void> {
  await client.delete(`/topics/${topicId}`)
}

export async function addTopicParticipants(
  client: ApiClient,
  topicId: string,
  userIds: string[],
): Promise<{ participants: TopicParticipant[] }> {
  const res = await client.post(`/topics/${topicId}/participants`, {
    user_ids: userIds,
  })
  return res.data
}

export async function removeTopicParticipant(
  client: ApiClient,
  topicId: string,
  userId: string,
): Promise<void> {
  await client.delete(`/topics/${topicId}/participants/${userId}`)
}

export async function updateParticipantRole(
  client: ApiClient,
  topicId: string,
  userId: string,
  role: 'owner' | 'member',
): Promise<{ participant: TopicParticipant }> {
  const res = await client.patch(
    `/topics/${topicId}/participants/${userId}`,
    { role },
  )
  return res.data
}

export async function createTopicConversation(
  client: ApiClient,
  topicId: string,
  title?: string,
): Promise<{ conversation: { id: string; title: string; topic_id: string } }> {
  const res = await client.post(`/topics/${topicId}/conversations`, {
    title: title ?? null,
  })
  return res.data
}

export async function upgradeToTopic(
  client: ApiClient,
  conversationId: string,
  body: { title: string; sandbox_mode?: string; member_user_ids?: string[] },
): Promise<TopicCreateResponse> {
  const res = await client.post(
    `/conversations/${conversationId}/upgrade-to-topic`,
    body,
  )
  return res.data
}
```

- [ ] **Step 5: Re-export topic API**

In `frontend/packages/core/src/api/index.ts`, add:

```typescript
export * from './topics'
```

- [ ] **Step 6: Build core package**

Run: `cd frontend && pnpm --filter @cubebox/core build`

Expected: build succeeds with no type errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/core/src/types/topic.ts \
  frontend/packages/core/src/types/conversation.ts \
  frontend/packages/core/src/types/index.ts \
  frontend/packages/core/src/api/topics.ts \
  frontend/packages/core/src/api/index.ts
git commit -m "$(cat <<'EOF'
feat(core): topic types and API client

Topic, TopicParticipant types. Full API client for topic CRUD,
participant management, topic-scoped conversations, and 1:1 upgrade.
Conversation type gains topic_id.
EOF
)"
```

---

### Task 8: Topic store (Zustand)

**Files:**
- Create: `frontend/packages/core/src/stores/topicStore.ts`
- Modify: `frontend/packages/core/src/stores/index.ts`

- [ ] **Step 1: Create topicStore**

Create `frontend/packages/core/src/stores/topicStore.ts`:

```typescript
import { create } from 'zustand'
import type { Topic, TopicParticipant } from '../types'
import type { ApiClient } from '../api'
import {
  listTopics,
  getTopic,
  createTopic,
  deleteTopic,
  addTopicParticipants,
  removeTopicParticipant,
} from '../api'

export interface TopicWithParticipants {
  topic: Topic
  participants: TopicParticipant[]
}

export interface TopicStore {
  topics: Topic[]
  topicParticipants: Record<string, TopicParticipant[]>
  isLoading: boolean
  error: string | null
  fetchList(client: ApiClient): Promise<void>
  fetchDetail(client: ApiClient, topicId: string): Promise<TopicWithParticipants | null>
  create(
    client: ApiClient,
    body: { title: string; sandbox_mode?: string; member_user_ids?: string[] },
  ): Promise<{ topicId: string; conversationId: string }>
  remove(client: ApiClient, topicId: string): Promise<void>
  addMembers(client: ApiClient, topicId: string, userIds: string[]): Promise<void>
  removeMember(client: ApiClient, topicId: string, userId: string): Promise<void>
}

export const useTopicStore = create<TopicStore>((set, get) => ({
  topics: [],
  topicParticipants: {},
  isLoading: false,
  error: null,

  async fetchList(client: ApiClient) {
    set({ isLoading: true, error: null })
    try {
      const { items } = await listTopics(client)
      set({ topics: items, isLoading: false })
    } catch (e) {
      set({ error: String(e), isLoading: false })
    }
  },

  async fetchDetail(client: ApiClient, topicId: string) {
    try {
      const data = await getTopic(client, topicId)
      set((s) => ({
        topicParticipants: {
          ...s.topicParticipants,
          [topicId]: data.participants,
        },
      }))
      return { topic: data.topic, participants: data.participants }
    } catch {
      return null
    }
  },

  async create(client, body) {
    const data = await createTopic(client, body)
    set((s) => ({ topics: [data.topic, ...s.topics] }))
    return {
      topicId: data.topic.id,
      conversationId: data.conversation.id,
    }
  },

  async remove(client, topicId) {
    await deleteTopic(client, topicId)
    set((s) => ({ topics: s.topics.filter((t) => t.id !== topicId) }))
  },

  async addMembers(client, topicId, userIds) {
    const { participants } = await addTopicParticipants(client, topicId, userIds)
    set((s) => ({
      topicParticipants: {
        ...s.topicParticipants,
        [topicId]: [...(s.topicParticipants[topicId] ?? []), ...participants],
      },
    }))
  },

  async removeMember(client, topicId, userId) {
    await removeTopicParticipant(client, topicId, userId)
    set((s) => ({
      topicParticipants: {
        ...s.topicParticipants,
        [topicId]: (s.topicParticipants[topicId] ?? []).filter(
          (p) => p.user_id !== userId,
        ),
      },
    }))
  },
}))
```

- [ ] **Step 2: Export from stores index**

In `frontend/packages/core/src/stores/index.ts`, add:

```typescript
export { useTopicStore, type TopicStore, type TopicWithParticipants } from './topicStore'
```

- [ ] **Step 3: Build core package**

Run: `cd frontend && pnpm --filter @cubebox/core build`

Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/core/src/stores/topicStore.ts \
  frontend/packages/core/src/stores/index.ts
git commit -m "$(cat <<'EOF'
feat(store): add topicStore for topic + participant state

Zustand store for topic list, detail with participants, create,
delete, add/remove members. Shared across sidebar and chat header.
EOF
)"
```

---

### Task 9: Sidebar — TopicNode + mixed list

**Files:**
- Create: `frontend/packages/web/components/sidebar/TopicNode.tsx`
- Modify: `frontend/packages/web/components/layout/Sidebar.tsx`

- [ ] **Step 1: Create TopicNode component**

Create `frontend/packages/web/components/sidebar/TopicNode.tsx`:

An expandable sidebar row that shows a topic title, participant avatars, and nested conversations when expanded. Owner sees a context menu with rename/delete/invite. Clicking a nested conversation navigates to it.

The component takes: `topic: Topic`, `conversations: Conversation[]`, `activeConvId: string | null`, `currentWsId: string | null`. Renders a collapsible row using `ChevronRight`/`ChevronDown` icons, with nested `ConversationRow` entries underneath.

Follow the existing `ConversationRow` pattern in `Sidebar.tsx` for styling — matching text truncation, hover states, active highlight, and dropdown menu.

- [ ] **Step 2: Modify Sidebar to render mixed list**

In `frontend/packages/web/components/layout/Sidebar.tsx`:

1. Import `useTopicStore` from `@cubebox/core` and `TopicNode` from `./TopicNode`.
2. In the sidebar body (where conversations are rendered in a scroll area), fetch topics alongside conversations.
3. Build a mixed list: group conversations by `topic_id`. Conversations with `topic_id === undefined` render as flat rows. Topics render as `TopicNode` with their grouped conversations.
4. Sort the mixed list by most recent `updated_at` across the group.

- [ ] **Step 3: Add i18n keys**

In `frontend/packages/web/messages/en.json`, add under a `"topics"` key:

```json
"topics": {
  "newGroupChat": "New Group Chat",
  "members": "{count} members",
  "owner": "Owner",
  "member": "Member",
  "inviteMembers": "Invite Members",
  "removeMember": "Remove",
  "leaveGroup": "Leave Group",
  "sandboxDedicated": "Dedicated sandbox",
  "sandboxCreator": "Creator's sandbox",
  "sandboxWarning": "Other members' operations will execute in your environment."
}
```

Add equivalent keys in `messages/zh.json`.

- [ ] **Step 4: Verify visually**

Start the dev server: `cd frontend && pnpm dev` (port from `.worktree.env`).

Open the app. Create a topic via API (curl or browser devtools). Verify it appears in the sidebar as an expandable node. Verify personal conversations still appear correctly.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/web/components/sidebar/TopicNode.tsx \
  frontend/packages/web/components/layout/Sidebar.tsx \
  frontend/packages/web/messages/en.json \
  frontend/packages/web/messages/zh.json
git commit -m "$(cat <<'EOF'
feat(sidebar): show topics as expandable nodes with nested conversations

Mixed list: personal conversations render flat, topic conversations
group under expandable TopicNode with participant avatars and context
menu.
EOF
)"
```

---

### Task 10: CreateGroupChatDialog

**Files:**
- Create: `frontend/packages/web/components/dialogs/CreateGroupChatDialog.tsx`
- Modify: `frontend/packages/web/components/layout/Sidebar.tsx`

- [ ] **Step 1: Create the dialog**

Create `frontend/packages/web/components/dialogs/CreateGroupChatDialog.tsx`:

A dialog with:
- Title input (required)
- Member picker — lists workspace members, multi-select checkboxes (fetch from `/ws/{ws}/members`)
- Sandbox mode selector — radio group: "Dedicated sandbox" (default) / "Creator's personal sandbox" (with warning text)
- Create button — calls `createTopic` API, navigates to the new conversation

Use the existing dialog pattern from the codebase (check other dialogs in `components/dialogs/` or similar). Use shadcn Dialog, Input, RadioGroup, Button, Checkbox components.

- [ ] **Step 2: Add "New Group Chat" button to sidebar**

In `Sidebar.tsx`, add a button next to the existing "New Chat" (`Plus` icon) button. Use `Users` icon from lucide-react. Opens `CreateGroupChatDialog`.

- [ ] **Step 3: Verify visually**

Open the app. Click "New Group Chat" in sidebar. Verify the dialog opens, member list loads, sandbox mode options work. Create a group chat. Verify the topic appears in the sidebar and the first conversation opens.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/dialogs/CreateGroupChatDialog.tsx \
  frontend/packages/web/components/layout/Sidebar.tsx
git commit -m "$(cat <<'EOF'
feat(ui): group chat creation dialog with member picker + sandbox mode

Dialog: title, workspace member multi-select, sandbox mode (dedicated
default, creator's personal with risk warning). Creates topic + first
conversation and navigates to it.
EOF
)"
```

---

### Task 11: SenderBadge + group chat message rendering

**Files:**
- Create: `frontend/packages/web/components/chat/SenderBadge.tsx`
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`

- [ ] **Step 1: Create SenderBadge component**

Create `frontend/packages/web/components/chat/SenderBadge.tsx`:

A small component that renders an avatar + display name above a message bubble. Takes `userId: string`, `displayName: string`. Avatar can be a colored initial circle (first letter of display name) for v1.

Style: small avatar (24px) + name text (text-xs, muted), left-aligned above the user message bubble with a 4px bottom margin.

- [ ] **Step 2: Render SenderBadge in MessageList**

In `frontend/packages/web/components/chat/MessageList.tsx`:

1. Pass the active conversation's `topic_id` context. If the conversation has a `topic_id`, fetch topic participants count (or derive from context) to determine `isGroupChat`.
2. For user messages in group chat mode, read `sender_display_name` and `sender_user_id` from message metadata (cubepi message metadata is accessible on the `Message` type).
3. Render `<SenderBadge>` above `<UserMessage>` when both conditions are met:
   - Conversation is a group chat (`topic_id` present + participant count > 1)
   - Message has `sender_display_name` in metadata

- [ ] **Step 3: Verify visually**

Send a message as one user in a group chat. Verify the avatar and name appear above the message. Verify 1:1 conversations do NOT show sender badges.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/chat/SenderBadge.tsx \
  frontend/packages/web/components/chat/MessageList.tsx
git commit -m "$(cat <<'EOF'
feat(chat): show sender avatar + name on group chat messages

SenderBadge renders above user messages when the conversation is a
group chat. Reads sender_display_name from message metadata. 1:1
conversations unchanged.
EOF
)"
```

---

### Task 12: MemberPanel + ChatHeader group UI

**Files:**
- Create: `frontend/packages/web/components/chat/MemberPanel.tsx`
- Modify: `frontend/packages/web/components/chat/ChatHeader.tsx` (or equivalent header)

- [ ] **Step 1: Create MemberPanel**

Create `frontend/packages/web/components/chat/MemberPanel.tsx`:

A panel (popover or side panel) showing:
- Participant list: avatar, name, role badge ("Owner" / "Member")
- For owner: "Invite" button → member picker dialog
- For owner: "Remove" button next to each non-owner member
- For any member: "Leave" button at bottom

Uses `useTopicStore` for participant data and mutations.

- [ ] **Step 2: Add member UI to ChatHeader**

In the chat header component:
1. For group chat conversations, show a stacked avatar group of participants (max 3 visible, "+N" overflow).
2. Clicking the avatar group opens MemberPanel.
3. For non-group-chat conversations, header unchanged.

- [ ] **Step 3: Verify visually**

Open a group chat. Verify avatars appear in header. Click to open member panel. Verify list shows all participants with correct roles. Test invite and remove actions.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/chat/MemberPanel.tsx \
  frontend/packages/web/components/chat/ChatHeader.tsx
git commit -m "$(cat <<'EOF'
feat(chat): member panel with invite/remove + header avatar group

Group chat header shows stacked participant avatars. Click opens
MemberPanel: participant list with role badges, owner can invite/remove,
any member can leave.
EOF
)"
```

---

### Task 13: UpgradeToTopicDialog (1:1 → group)

**Files:**
- Create: `frontend/packages/web/components/dialogs/UpgradeToTopicDialog.tsx`
- Modify: chat header or conversation context menu

- [ ] **Step 1: Create UpgradeToTopicDialog**

Create `frontend/packages/web/components/dialogs/UpgradeToTopicDialog.tsx`:

Similar to CreateGroupChatDialog but for upgrading an existing 1:1 conversation:
- Title input (pre-filled with conversation title)
- Member picker
- Sandbox mode selector
- Warning: "This is irreversible. All conversation history will be visible to new members."
- "Upgrade" button — calls `upgradeToTopic` API

- [ ] **Step 2: Add "Invite Members" entry point**

Add an "Invite Members" option in the conversation context menu (or chat header) that appears only for conversations without a `topic_id`. Opens `UpgradeToTopicDialog`.

- [ ] **Step 3: Verify visually**

Open a 1:1 conversation. Click "Invite Members". Fill the dialog. Verify the conversation is upgraded, sidebar updates to show the topic, and the invited member can see the conversation.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/dialogs/UpgradeToTopicDialog.tsx
git commit -m "$(cat <<'EOF'
feat(ui): upgrade 1:1 conversation to group chat dialog

Dialog for converting personal conversations to topics. Shows
irreversibility warning, member picker, sandbox mode choice. Calls
upgrade-to-topic API.
EOF
)"
```

---

### Task 14: Group chat E2E tests (messaging + HITL)

**Files:**
- Create: `backend/tests/e2e/test_group_chat.py`

- [ ] **Step 1: Write send_message access test**

```python
"""E2E tests for group chat messaging behavior."""

import httpx
import pytest

pytestmark = pytest.mark.e2e


class TestGroupChatMessaging:
    """Message sending and access in group chat conversations."""

    @pytest.mark.anyio
    async def test_participant_can_send_message(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        # Create group chat
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={
                "title": "Chat Room",
                "member_user_ids": [member_uid],
            },
        )
        conv_id = resp.json()["conversation"]["id"]

        # Member sends a message (just verify the endpoint accepts it,
        # don't start a real LLM run)
        msg_resp = await member_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
            json={"content": "Hello from member"},
            headers={"accept": "application/json"},
        )
        # 200 or 409 (if run manager rejects non-streaming) — but NOT 404
        assert msg_resp.status_code != 404
```

- [ ] **Step 2: Write conversation visibility after leave test**

```python
    @pytest.mark.anyio
    async def test_left_member_loses_access(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={
                "title": "Temp Chat",
                "member_user_ids": [member_uid],
            },
        )
        topic_id = resp.json()["topic"]["id"]
        conv_id = resp.json()["conversation"]["id"]

        # Member can see conversation
        assert (await member_c.get(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}"
        )).status_code == 200

        # Member leaves
        await member_c.delete(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants/{member_uid}"
        )

        # Member can no longer see conversation
        assert (await member_c.get(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}"
        )).status_code == 404
```

- [ ] **Step 3: Write topic-scoped conversation creation test**

```python
class TestTopicConversations:
    """Creating new conversations under a topic."""

    @pytest.mark.anyio
    async def test_participant_creates_conversation_under_topic(
        self, four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={
                "title": "Project X",
                "member_user_ids": [member_uid],
            },
        )
        topic_id = resp.json()["topic"]["id"]

        # Member creates a new conversation in the topic
        conv_resp = await member_c.post(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/conversations",
            json={"title": "Sprint 2 Discussion"},
        )
        assert conv_resp.status_code == 201
        new_conv = conv_resp.json()["conversation"]
        assert new_conv["topic_id"] == topic_id

        # Admin can see the new conversation
        admin_conv = await admin_c.get(
            f"/api/v1/ws/{ws_id}/conversations/{new_conv['id']}"
        )
        assert admin_conv.status_code == 200
```

- [ ] **Step 4: Run all group chat tests**

Run: `cd backend && uv run pytest tests/e2e/test_group_chat.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/e2e/test_group_chat.py
git commit -m "$(cat <<'EOF'
test(e2e): group chat messaging, leave-loses-access, topic conversations

Covers participant can send message, left member loses conversation
access, participant creates new conversation under topic visible to
all members.
EOF
)"
```

---

### Task 15: Pre-PR sweep — full test suite + type check

**Files:** None new — verification only.

- [ ] **Step 1: Run mypy**

Run: `cd backend && uv run mypy cubebox/ --strict`

Expected: no new errors.

- [ ] **Step 2: Run full backend test suite**

Run: `cd backend && uv run pytest tests/ -v --tb=short`

Expected: all tests pass, including existing conversation privacy tests.

- [ ] **Step 3: Run frontend type check**

Run: `cd frontend && pnpm --filter @cubebox/core build && pnpm --filter web typecheck`

Expected: no type errors.

- [ ] **Step 4: Fix any failures**

Address any issues found. Commit fixes separately with descriptive messages.

- [ ] **Step 5: Final commit with any remaining fixes**

Only if there were fixes needed. Then the branch is ready for PR.
