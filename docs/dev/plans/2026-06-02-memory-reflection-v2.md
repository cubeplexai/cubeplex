# Memory Reflection v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace v1 in-band memory reflection with a detached out-of-band run that publishes results through a new user-scoped event channel.

**Architecture:** After main run's `AgentEndEvent` fires, `run_manager` schedules a fire-and-forget `ReflectionRunner.reflect` task. The task constructs a fresh cubepi `Agent` (cheap model, memory tools only) seeded with the last turn + memory snapshot. Memory writes flow through the normal `MemoryService` (tagged `source_type=REFLECTION` via ContextVar). Successful writes publish a `UserEvent` row that fans out via in-process pub/sub to `/api/v1/user/events` SSE subscribers. Frontend hook at the app shell receives events globally and renders inline memory chips in the conversation timeline.

**Tech Stack:** FastAPI / SQLModel / Alembic / asyncio / cubepi Agent / Zustand / Next.js EventSource

**Spec:** `docs/dev/specs/2026-06-02-memory-reflection-v2-design.md`

---

## File structure

**Backend new files:**
- `backend/cubeplex/models/user_event.py` — `UserEvent` SQLModel
- `backend/cubeplex/repositories/user_event.py` — list/insert/mark_read
- `backend/cubeplex/services/user_event.py` — `UserEventService` + DTOs
- `backend/cubeplex/services/user_event_bus.py` — in-process pub/sub
- `backend/cubeplex/services/reflection_runner.py` — orchestrator
- `backend/cubeplex/services/reflection_context.py` — `ContextVar` for source attribution
- `backend/cubeplex/prompts/reflection_system.py` — system-prompt template for reflection agent
- `backend/cubeplex/api/routes/v1/user_events.py` — SSE + read endpoints
- `backend/alembic/versions/<rev>_add_user_events.py` — migration
- `backend/tests/unit/test_reflection_runner.py`
- `backend/tests/unit/test_user_event_bus.py`
- `backend/tests/integration/test_user_events_api.py`
- `backend/tests/integration/test_reflection_flow.py`

**Backend modified files:**
- `backend/cubeplex/models/memory.py` — add `MemorySourceType.REFLECTION`
- `backend/cubeplex/models/public_id.py` — add `PREFIX_USER_EVENT = "uev"`
- `backend/cubeplex/tools/builtin/memory.py` — honor reflection ContextVar on save/update
- `backend/cubeplex/streams/run_manager.py` — schedule reflection task after AgentEndEvent; remove `ReflectionMiddleware()`
- `backend/cubeplex/api/routes/v1/__init__.py` — register user_events router

**Backend deletions (v1 cleanup):**
- `backend/cubeplex/middleware/reflection.py`
- `backend/cubeplex/prompts/reflection.py`
- `backend/tests/unit/test_reflection_middleware.py`

**Frontend new files:**
- `frontend/packages/core/src/sse/userEventClient.ts`
- `frontend/packages/core/src/stores/memoryEventStore.ts`
- `frontend/packages/core/src/hooks/useUserEvents.ts`
- `frontend/packages/web/src/components/memory/MemoryUpdateChip.tsx`
- `frontend/packages/web/src/components/memory/MemoryUpdateToast.tsx`
- `frontend/packages/web/tests/memory-update-chip.test.tsx`
- `frontend/packages/web/tests/e2e/memory-reflection.spec.ts`

**Frontend modified files:**
- `frontend/packages/web/src/app/layout.tsx` (or equivalent shell) — mount `useUserEvents`
- `frontend/packages/web/src/components/conversation/Timeline.tsx` (or equivalent) — render chip after matching `run_id`

---

## Phase 1: Backend foundation

### Task 1: Add `MemorySourceType.REFLECTION` + reflection ContextVar

**Files:**
- Modify: `backend/cubeplex/models/memory.py`
- Create: `backend/cubeplex/services/reflection_context.py`
- Modify: `backend/cubeplex/tools/builtin/memory.py`
- Test: `backend/tests/unit/test_reflection_context.py`

- [ ] **Step 1: Write failing test**

```python
# backend/tests/unit/test_reflection_context.py
import pytest

from cubeplex.models.memory import MemorySourceType
from cubeplex.services.reflection_context import (
    reflection_source_active,
    set_reflection_source,
)


def test_reflection_source_default_inactive() -> None:
    assert reflection_source_active() is False


def test_reflection_source_scoped_context() -> None:
    assert reflection_source_active() is False
    with set_reflection_source():
        assert reflection_source_active() is True
    assert reflection_source_active() is False


def test_reflection_enum_value() -> None:
    assert MemorySourceType.REFLECTION.value == "reflection"
```

- [ ] **Step 2: Run test, expect ImportError / fail**

Run: `cd backend && uv run pytest tests/unit/test_reflection_context.py -v`
Expected: ImportError on `reflection_source_active` / `set_reflection_source`.

- [ ] **Step 3: Add `REFLECTION` to `MemorySourceType`**

In `backend/cubeplex/models/memory.py`, add to the `MemorySourceType` StrEnum:

```python
class MemorySourceType(StrEnum):
    CONVERSATION = "conversation"
    TOOL_RESULT = "tool_result"
    ARTIFACT = "artifact"
    MANUAL = "manual"
    IMPORT = "import"
    CONSOLIDATION = "consolidation"
    REFLECTION = "reflection"
```

- [ ] **Step 4: Create reflection_context module**

```python
# backend/cubeplex/services/reflection_context.py
"""ContextVar gate for tagging memory writes made during a reflection run.

Set inside ReflectionRunner around the reflection Agent's prompt; read by
the memory_save / memory_update tools to override source_type. Using a
ContextVar (not a tool argument) means the main agent cannot impersonate
reflection-sourced writes.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_active: ContextVar[bool] = ContextVar("reflection_source_active", default=False)


def reflection_source_active() -> bool:
    return _active.get()


@contextmanager
def set_reflection_source() -> Iterator[None]:
    token = _active.set(True)
    try:
        yield
    finally:
        _active.reset(token)
```

- [ ] **Step 5: Run unit tests, expect pass**

Run: `cd backend && uv run pytest tests/unit/test_reflection_context.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Wire into memory tools**

In `backend/cubeplex/tools/builtin/memory.py`, inside `_memory_save_execute`, change the `CreateMemoryInput` construction to honor the ContextVar:

```python
from cubeplex.services.reflection_context import reflection_source_active

# ...inside _memory_save_execute, replacing the existing CreateMemoryInput(...)
src_type = MemorySourceType.REFLECTION if reflection_source_active() else MemorySourceType.CONVERSATION
item = await svc.create(
    CreateMemoryInput(
        scope=args.scope,
        type=args.type,
        content=args.content,
        confidence=args.confidence,
        source_type=src_type,
        source_conversation_id=conversation_id,
        source_run_id=run_id,
    )
)
```

Note: previously `CreateMemoryInput` defaulted `source_type=MANUAL`. Main-agent calls
should be `CONVERSATION` (they're triggered by user dialogue, not user manually
adding a memory). Update the default behavior accordingly.

`_memory_update_execute` does not currently set `source_type`; no plumbing needed
there — updates carry the original item's source.

Add the import for `reflection_source_active` and `MemorySourceType` at the top of the file.

- [ ] **Step 7: Add test for tool behavior under ContextVar**

```python
# Append to backend/tests/unit/test_reflection_context.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from cubeplex.models.memory import MemoryScope, MemorySourceType, MemoryType
from cubeplex.tools.builtin.memory import create_memory_tools


@pytest.mark.asyncio
async def test_memory_save_uses_reflection_source_when_active() -> None:
    svc = MagicMock()
    svc.create = AsyncMock(return_value=MagicMock(id="mem_abc"))
    factory_cm = MagicMock()
    factory_cm.__aenter__ = AsyncMock(return_value=svc)
    factory_cm.__aexit__ = AsyncMock(return_value=None)

    tools = create_memory_tools(
        service_factory=lambda: factory_cm,
        conversation_id="conv_x",
        run_id="run_y",
    )
    save_tool = next(t for t in tools if t.name == "memory_save")

    args = save_tool.parameters(
        scope=MemoryScope.PERSONAL,
        type=MemoryType.PREFERENCE,
        content="prefers Chinese",
        confidence=0.9,
    )

    from cubeplex.services.reflection_context import set_reflection_source
    with set_reflection_source():
        await save_tool.execute("tc1", args)

    call = svc.create.call_args.args[0]
    assert call.source_type == MemorySourceType.REFLECTION
```

- [ ] **Step 8: Run tests + ruff + mypy**

Run:
```bash
cd backend && uv run pytest tests/unit/test_reflection_context.py -v && uv run ruff check cubeplex/services/reflection_context.py cubeplex/tools/builtin/memory.py cubeplex/models/memory.py && uv run mypy cubeplex
```
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/cubeplex/services/reflection_context.py backend/cubeplex/models/memory.py backend/cubeplex/tools/builtin/memory.py backend/tests/unit/test_reflection_context.py
git commit -m "feat(memory): add REFLECTION source type + ContextVar gate for attribution"
```

---

### Task 2: `UserEvent` model + migration + public_id prefix

**Files:**
- Modify: `backend/cubeplex/models/public_id.py`
- Create: `backend/cubeplex/models/user_event.py`
- Modify: `backend/cubeplex/models/__init__.py` (re-export)
- Create: `backend/alembic/versions/<rev>_add_user_events.py` (autogen)
- Test: `backend/tests/unit/test_user_event_model.py`

- [ ] **Step 1: Add public_id prefix**

In `backend/cubeplex/models/public_id.py`, add:

```python
PREFIX_USER_EVENT = "uev"
```

(Find the existing prefix list and append in alphabetical order to match style.)

- [ ] **Step 2: Write failing test**

```python
# backend/tests/unit/test_user_event_model.py
from datetime import UTC, datetime

from cubeplex.models.user_event import UserEvent, UserEventType


def test_user_event_construct() -> None:
    e = UserEvent(
        user_id="usr_abc",
        workspace_id="ws_def",
        type=UserEventType.MEMORY_UPDATED,
        payload={"items": [{"op": "save", "memory_id": "mem_x"}]},
    )
    assert e.id.startswith("uev_")
    assert e.read_at is None
    assert e.type == UserEventType.MEMORY_UPDATED
```

- [ ] **Step 3: Create model**

```python
# backend/cubeplex/models/user_event.py
"""UserEvent — user-scoped async notification (memory updates, etc.)."""

from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import Column, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from cubeplex.models.mixins import CubeplexBase
from cubeplex.models.public_id import PREFIX_USER_EVENT


class UserEventType(StrEnum):
    MEMORY_UPDATED = "memory_updated"


class UserEvent(CubeplexBase, table=True):
    _PREFIX: ClassVar[str] = PREFIX_USER_EVENT
    __tablename__ = "user_events"
    __table_args__ = (
        Index("ix_user_events_user_created", "user_id", "created_at"),
        Index("ix_user_events_unread", "user_id", "read_at"),
    )

    user_id: str = Field(foreign_key="users.id", max_length=20)
    workspace_id: str | None = Field(default=None, foreign_key="workspaces.id", max_length=20)
    type: UserEventType = Field()
    payload: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    read_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
```

- [ ] **Step 4: Re-export from models/__init__.py**

Add to `backend/cubeplex/models/__init__.py`:
```python
from cubeplex.models.user_event import UserEvent, UserEventType
```
(Match existing alphabetical / grouped re-export style.)

- [ ] **Step 5: Generate migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "add user_events table"
```

Inspect the generated file: verify it creates the `user_events` table with both indexes. **Do not hand-edit** (per project rules) unless autogen got something wrong — only the timestamp column should need attention; verify it uses `timestamptz` (autogen for SQLAlchemy `DateTime(timezone=True)` does this automatically).

- [ ] **Step 6: Run migration locally + run model test**

```bash
cd backend && uv run alembic upgrade head && uv run pytest tests/unit/test_user_event_model.py -v
```
Expected: migration applies cleanly; test passes.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/models/user_event.py backend/cubeplex/models/public_id.py backend/cubeplex/models/__init__.py backend/alembic/versions/*_add_user_events.py backend/tests/unit/test_user_event_model.py
git commit -m "feat(models): add user_events table for user-scoped async notifications"
```

---

### Task 3: `UserEventBus` (pub/sub + DB write-through) + repository + service

**Files:**
- Create: `backend/cubeplex/repositories/user_event.py`
- Create: `backend/cubeplex/services/user_event.py`
- Create: `backend/cubeplex/services/user_event_bus.py`
- Test: `backend/tests/unit/test_user_event_bus.py`
- Test: `backend/tests/unit/test_user_event_service.py`

- [ ] **Step 1: Write failing pub/sub test**

```python
# backend/tests/unit/test_user_event_bus.py
import asyncio
import pytest

from cubeplex.services.user_event_bus import UserEventBus, UserEventDTO
from cubeplex.models.user_event import UserEventType


@pytest.mark.asyncio
async def test_subscriber_receives_published_event() -> None:
    bus = UserEventBus()
    received: list[UserEventDTO] = []

    async def consume() -> None:
        async for ev in bus.subscribe("usr_x"):
            received.append(ev)
            if len(received) == 1:
                break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)  # let consumer subscribe

    await bus.publish_local(
        UserEventDTO(
            id="uev_1",
            user_id="usr_x",
            workspace_id=None,
            type=UserEventType.MEMORY_UPDATED,
            payload={"items": []},
            created_at_iso="2026-06-02T00:00:00+00:00",
        )
    )

    await asyncio.wait_for(consumer, timeout=1.0)
    assert received[0].id == "uev_1"


@pytest.mark.asyncio
async def test_other_user_events_not_delivered() -> None:
    bus = UserEventBus()

    async def consume() -> list[UserEventDTO]:
        out: list[UserEventDTO] = []
        async for ev in bus.subscribe("usr_x"):
            out.append(ev)
            return out
        return out

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)

    await bus.publish_local(
        UserEventDTO(
            id="uev_2", user_id="usr_y", workspace_id=None,
            type=UserEventType.MEMORY_UPDATED, payload={}, created_at_iso="",
        )
    )
    await asyncio.sleep(0.05)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
```

- [ ] **Step 2: Run test, expect ImportError**

Run: `cd backend && uv run pytest tests/unit/test_user_event_bus.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement bus**

```python
# backend/cubeplex/services/user_event_bus.py
"""In-process pub/sub for user-scoped async events.

Single-instance only — when cubeplex scales horizontally, swap the body for
Redis pub/sub keeping the same publish_local / subscribe interface.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator

from cubeplex.models.user_event import UserEventType


@dataclass(frozen=True)
class UserEventDTO:
    id: str
    user_id: str
    workspace_id: str | None
    type: UserEventType
    payload: dict[str, Any]
    created_at_iso: str


class UserEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[UserEventDTO]]] = {}
        self._lock = asyncio.Lock()

    async def publish_local(self, event: UserEventDTO) -> None:
        """Fan out to live subscribers. Caller is responsible for DB persist."""
        async with self._lock:
            queues = list(self._subscribers.get(event.user_id, ()))
        for q in queues:
            q.put_nowait(event)

    async def subscribe(self, user_id: str) -> AsyncIterator[UserEventDTO]:
        q: asyncio.Queue[UserEventDTO] = asyncio.Queue()
        async with self._lock:
            self._subscribers.setdefault(user_id, set()).add(q)
        try:
            while True:
                yield await q.get()
        finally:
            async with self._lock:
                bucket = self._subscribers.get(user_id)
                if bucket is not None:
                    bucket.discard(q)
                    if not bucket:
                        del self._subscribers[user_id]
```

- [ ] **Step 4: Run pub/sub tests**

Run: `cd backend && uv run pytest tests/unit/test_user_event_bus.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Write failing service test**

```python
# backend/tests/unit/test_user_event_service.py
import pytest

from cubeplex.models.user_event import UserEventType
from cubeplex.services.user_event import (
    PublishUserEventInput,
    UserEventService,
)
from cubeplex.services.user_event_bus import UserEventBus
from cubeplex.repositories.user_event import UserEventRepository


@pytest.mark.asyncio
async def test_publish_writes_and_broadcasts(session_factory) -> None:  # fixture from conftest
    bus = UserEventBus()
    async with session_factory() as session:
        repo = UserEventRepository(session)
        svc = UserEventService(repo=repo, bus=bus)
        ev = await svc.publish(
            PublishUserEventInput(
                user_id="usr_x",
                workspace_id=None,
                type=UserEventType.MEMORY_UPDATED,
                payload={"items": []},
            )
        )
        assert ev.id.startswith("uev_")
        # verify DB persistence
        listed = await repo.list_for_user("usr_x", since_id=None, limit=10)
        assert any(r.id == ev.id for r in listed)


@pytest.mark.asyncio
async def test_list_since_id_filters(session_factory) -> None:
    bus = UserEventBus()
    async with session_factory() as session:
        repo = UserEventRepository(session)
        svc = UserEventService(repo=repo, bus=bus)
        e1 = await svc.publish(PublishUserEventInput(
            user_id="usr_x", workspace_id=None,
            type=UserEventType.MEMORY_UPDATED, payload={"n": 1},
        ))
        e2 = await svc.publish(PublishUserEventInput(
            user_id="usr_x", workspace_id=None,
            type=UserEventType.MEMORY_UPDATED, payload={"n": 2},
        ))
        rows = await repo.list_for_user("usr_x", since_id=e1.id, limit=10)
        ids = [r.id for r in rows]
        assert e2.id in ids
        assert e1.id not in ids
```

Note: use whatever existing fixture provides a DB session in unit tests. Match the
pattern used by `backend/tests/unit/test_memory_service.py` (or closest analog).

- [ ] **Step 6: Implement repository and service**

```python
# backend/cubeplex/repositories/user_event.py
from __future__ import annotations

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from cubeplex.models.user_event import UserEvent


class UserEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, ev: UserEvent) -> UserEvent:
        self.session.add(ev)
        await self.session.flush()
        return ev

    async def list_for_user(
        self,
        user_id: str,
        *,
        since_id: str | None,
        limit: int = 100,
    ) -> list[UserEvent]:
        stmt = select(UserEvent).where(UserEvent.user_id == user_id)
        if since_id is not None:
            stmt = stmt.where(UserEvent.id > since_id)  # public IDs are sortable
        stmt = stmt.order_by(UserEvent.created_at).limit(limit)
        result = await self.session.exec(stmt)
        return list(result.all())

    async def mark_read(self, ev_id: str, user_id: str) -> UserEvent | None:
        stmt = select(UserEvent).where(UserEvent.id == ev_id, UserEvent.user_id == user_id)
        row = (await self.session.exec(stmt)).first()
        if row is None:
            return None
        from datetime import UTC, datetime
        row.read_at = datetime.now(UTC)
        await self.session.flush()
        return row
```

> Note on `since_id`: if public IDs are not strictly sortable, switch to `created_at` comparison + tiebreak by `id`. Check `cubeplex/models/public_id.py` — if IDs are ulid/ksuid-like they're already time-ordered.

```python
# backend/cubeplex/services/user_event.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cubeplex.models.user_event import UserEvent, UserEventType
from cubeplex.repositories.user_event import UserEventRepository
from cubeplex.services.user_event_bus import UserEventBus, UserEventDTO
from cubeplex.utils.time import utc_isoformat


@dataclass
class PublishUserEventInput:
    user_id: str
    workspace_id: str | None
    type: UserEventType
    payload: dict[str, Any]


class UserEventService:
    def __init__(self, *, repo: UserEventRepository, bus: UserEventBus) -> None:
        self.repo = repo
        self.bus = bus

    async def publish(self, inp: PublishUserEventInput) -> UserEvent:
        ev = UserEvent(
            user_id=inp.user_id,
            workspace_id=inp.workspace_id,
            type=inp.type,
            payload=inp.payload,
        )
        await self.repo.add(ev)
        await self.bus.publish_local(
            UserEventDTO(
                id=ev.id,
                user_id=ev.user_id,
                workspace_id=ev.workspace_id,
                type=ev.type,
                payload=ev.payload,
                created_at_iso=utc_isoformat(ev.created_at),
            )
        )
        return ev
```

- [ ] **Step 7: Run service tests, expect pass**

Run: `cd backend && uv run pytest tests/unit/test_user_event_bus.py tests/unit/test_user_event_service.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/cubeplex/repositories/user_event.py backend/cubeplex/services/user_event.py backend/cubeplex/services/user_event_bus.py backend/tests/unit/test_user_event_bus.py backend/tests/unit/test_user_event_service.py
git commit -m "feat(events): add UserEventBus (pubsub) + UserEventService (persist + broadcast)"
```

---

### Task 4: SSE endpoint + read endpoint

**Files:**
- Create: `backend/cubeplex/api/routes/v1/user_events.py`
- Modify: `backend/cubeplex/api/routes/v1/__init__.py` (or wherever routers are registered)
- Test: `backend/tests/integration/test_user_events_api.py`

- [ ] **Step 1: Wire bus into app singleton**

The bus is in-process; find where other singletons (e.g. `RunManager`) are constructed and wire `UserEventBus()` there. Most likely in `backend/cubeplex/main.py` or `backend/cubeplex/dependencies.py`. Expose via FastAPI `Depends`.

```python
# in dependencies module
_bus: UserEventBus | None = None

def get_user_event_bus() -> UserEventBus:
    global _bus
    if _bus is None:
        _bus = UserEventBus()
    return _bus
```

(Match existing dependency-injection patterns — likely there's already a registry; prefer that.)

- [ ] **Step 2: Write failing integration test**

```python
# backend/tests/integration/test_user_events_api.py
import asyncio
import json
import pytest
from httpx import AsyncClient

from cubeplex.models.user_event import UserEventType
from cubeplex.services.user_event import PublishUserEventInput, UserEventService


@pytest.mark.asyncio
async def test_sse_receives_live_event(authed_client: AsyncClient, current_user_id, user_event_service: UserEventService):
    # use existing fixtures for an authenticated httpx client + the logged-in user id
    async with authed_client.stream("GET", "/api/v1/user/events") as resp:
        assert resp.status_code == 200
        # publish after subscription is open
        async def fire():
            await asyncio.sleep(0.1)
            await user_event_service.publish(PublishUserEventInput(
                user_id=current_user_id, workspace_id=None,
                type=UserEventType.MEMORY_UPDATED, payload={"items": [{"op": "save"}]},
            ))
        asyncio.create_task(fire())

        received: dict | None = None
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                received = json.loads(line.removeprefix("data: "))
                break

        assert received is not None
        assert received["type"] == "memory_updated"


@pytest.mark.asyncio
async def test_mark_read(authed_client, current_user_id, user_event_service):
    ev = await user_event_service.publish(PublishUserEventInput(
        user_id=current_user_id, workspace_id=None,
        type=UserEventType.MEMORY_UPDATED, payload={},
    ))
    resp = await authed_client.post(f"/api/v1/user/events/{ev.id}/read")
    assert resp.status_code == 204
```

Adapt fixture names (`authed_client`, `current_user_id`) to what the existing
integration suite uses (`backend/tests/integration/conftest.py`).

- [ ] **Step 3: Run, expect 404**

Run: `cd backend && uv run pytest tests/integration/test_user_events_api.py -v`
Expected: 404 / endpoint not registered.

- [ ] **Step 4: Implement endpoints**

```python
# backend/cubeplex/api/routes/v1/user_events.py
"""User-scoped async event channel — SSE stream + mark-read."""

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from cubeplex.api.dependencies import get_current_user_id, get_session
from cubeplex.dependencies import get_user_event_bus
from cubeplex.repositories.user_event import UserEventRepository
from cubeplex.services.user_event_bus import UserEventBus
from cubeplex.utils.time import utc_isoformat

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/user", tags=["user-events"])


HEARTBEAT_INTERVAL_SEC = 30.0


@router.get("/events")
async def stream_user_events(
    since: str | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    bus: UserEventBus = Depends(get_user_event_bus),
    session=Depends(get_session),
) -> StreamingResponse:
    repo = UserEventRepository(session)

    async def gen() -> AsyncIterator[bytes]:
        # 1. Replay from DB
        if since is not None:
            replay = await repo.list_for_user(user_id, since_id=since, limit=200)
            for row in replay:
                yield _sse_format(row.type.value, {
                    "id": row.id,
                    "type": row.type.value,
                    "workspace_id": row.workspace_id,
                    "payload": row.payload,
                    "created_at": utc_isoformat(row.created_at),
                })
        # 2. Live subscription
        sub = bus.subscribe(user_id)
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(sub.__anext__(), timeout=HEARTBEAT_INTERVAL_SEC)
                except asyncio.TimeoutError:
                    yield b": ping\n\n"
                    continue
                yield _sse_format(ev.type.value, {
                    "id": ev.id, "type": ev.type.value,
                    "workspace_id": ev.workspace_id,
                    "payload": ev.payload,
                    "created_at": ev.created_at_iso,
                })
        finally:
            await sub.aclose()  # type: ignore[attr-defined]

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@router.post("/events/{event_id}/read", status_code=204)
async def mark_event_read(
    event_id: str,
    user_id: str = Depends(get_current_user_id),
    session=Depends(get_session),
) -> Response:
    repo = UserEventRepository(session)
    row = await repo.mark_read(event_id, user_id)
    if row is None:
        raise HTTPException(404, "event not found")
    return Response(status_code=204)


def _sse_format(event_type: str, data: dict) -> bytes:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()
```

- [ ] **Step 5: Register router**

In `backend/cubeplex/api/routes/v1/__init__.py` (or the FastAPI `include_router`
location), add the user_events router. Match the existing registration style.

- [ ] **Step 6: Run tests, expect pass**

Run: `cd backend && uv run pytest tests/integration/test_user_events_api.py -v`
Expected: 2 PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/api/routes/v1/user_events.py backend/cubeplex/api/routes/v1/__init__.py backend/cubeplex/dependencies.py backend/tests/integration/test_user_events_api.py
git commit -m "feat(api): /api/v1/user/events SSE channel + mark-read endpoint"
```

---

## Phase 2: Reflection runner

### Task 5: Reflection system prompt

**Files:**
- Create: `backend/cubeplex/prompts/reflection_system.py`

- [ ] **Step 1: Write the prompt module**

```python
# backend/cubeplex/prompts/reflection_system.py
"""System prompt for the detached memory-reflection agent.

The reflection agent runs in isolation after a main conversation turn
completes. It sees only the last turn (user msg + assistant reply + tool
summaries) plus the current memory snapshot. Its job: extract anything
worth remembering and call memory_save / memory_update once.
"""

REFLECTION_SYSTEM_PROMPT: str = """\
You are a memory-curation assistant. Your only job is to review the last \
turn of a conversation and decide whether anything new is worth remembering.

You have three tools:
- memory_search: check whether a fact is already stored.
- memory_save:   add a new memory.
- memory_update: refine an existing memory.

Heuristics for what to save:
- The user expressed a preference ("I prefer X", "always do Y", "I like…").
- The user corrected you, or pushed back on something you did.
- The user stated a durable fact about themselves, their team, or their project \
that would change how you respond next time.
- The user shared a decision that should outlast this conversation.

Do NOT save:
- Restatements of facts you already have (search first).
- Ephemeral context that only matters for this run.
- Speculative or low-confidence inferences.

Scope: use 'personal' unless the user explicitly said to share with the team.

Output: call memory_search / memory_save / memory_update as needed, then end. \
If nothing is worth saving, end immediately without calling any tool. Do not \
explain — the user will not see your text.
"""
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubeplex/prompts/reflection_system.py
git commit -m "feat(prompts): add reflection-agent system prompt for detached runs"
```

---

### Task 6: `ReflectionRunner` service

**Files:**
- Create: `backend/cubeplex/services/reflection_runner.py`
- Test: `backend/tests/unit/test_reflection_runner.py`

- [ ] **Step 1: Write failing happy-path test**

```python
# backend/tests/unit/test_reflection_runner.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from cubeplex.models.user_event import UserEventType
from cubeplex.services.reflection_runner import (
    ReflectionInput,
    ReflectionRunner,
    ReflectionTurn,
)


@pytest.mark.asyncio
async def test_reflect_publishes_event_when_memory_saved(
    monkeypatch, user_event_service_mock, agent_factory_mock
):
    # agent_factory_mock returns an Agent whose prompt() emits one tool_execution_end
    # for memory_save with memory_id="mem_abc"
    runner = ReflectionRunner(
        user_event_service=user_event_service_mock,
        agent_factory=agent_factory_mock,
        memory_service_factory=MagicMock(),
        timeout_sec=5.0,
    )
    await runner.reflect(
        ReflectionInput(
            conversation_id="conv_x",
            run_id="run_y",
            user_id="usr_z",
            workspace_id=None,
            turn=ReflectionTurn(
                user_message="我喜欢简洁的回答",
                assistant_message="收到。",
                tool_summaries=[],
            ),
        )
    )
    pub = user_event_service_mock.publish
    pub.assert_called_once()
    inp = pub.call_args.args[0]
    assert inp.type == UserEventType.MEMORY_UPDATED
    assert inp.payload["items"][0]["memory_id"] == "mem_abc"


@pytest.mark.asyncio
async def test_reflect_no_publish_when_no_memory_saved(
    user_event_service_mock, agent_factory_silent
):
    runner = ReflectionRunner(
        user_event_service=user_event_service_mock,
        agent_factory=agent_factory_silent,
        memory_service_factory=MagicMock(),
        timeout_sec=5.0,
    )
    await runner.reflect(ReflectionInput(
        conversation_id="conv_x", run_id="run_y", user_id="usr_z", workspace_id=None,
        turn=ReflectionTurn(user_message="hi", assistant_message="hi", tool_summaries=[]),
    ))
    user_event_service_mock.publish.assert_not_called()


@pytest.mark.asyncio
async def test_reflect_drops_silently_on_timeout(
    user_event_service_mock, agent_factory_hanging
):
    runner = ReflectionRunner(
        user_event_service=user_event_service_mock,
        agent_factory=agent_factory_hanging,
        memory_service_factory=MagicMock(),
        timeout_sec=0.1,
    )
    # should NOT raise
    await runner.reflect(ReflectionInput(
        conversation_id="c", run_id="r", user_id="u", workspace_id=None,
        turn=ReflectionTurn(user_message="x", assistant_message="y", tool_summaries=[]),
    ))
    user_event_service_mock.publish.assert_not_called()
```

(Fixtures `user_event_service_mock`, `agent_factory_mock`, `agent_factory_silent`,
`agent_factory_hanging` should be added to a local conftest scoped to this test
file. Use `FauxProvider` from cubepi to build the underlying agents in fixtures;
emit the appropriate event sequence to simulate each case.)

- [ ] **Step 2: Run test, expect ImportError**

Run: `cd backend && uv run pytest tests/unit/test_reflection_runner.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement the runner**

```python
# backend/cubeplex/services/reflection_runner.py
"""Out-of-band memory reflection — runs after AgentEndEvent.

Spawns a detached cubepi Agent (cheap model, memory tools only) seeded with
the last conversation turn plus the current memory snapshot. Captures any
memory_save / memory_update tool executions and publishes a UserEvent so
the frontend can surface the change.

Failure semantics: fire-and-forget. Timeout, LLM errors, and memory write
errors are logged and swallowed; never propagate to the main conversation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from cubepi import Agent, AgentEvent

from cubeplex.models.user_event import UserEventType
from cubeplex.prompts.reflection_system import REFLECTION_SYSTEM_PROMPT
from cubeplex.services.reflection_context import set_reflection_source
from cubeplex.services.user_event import PublishUserEventInput, UserEventService

logger = logging.getLogger(__name__)


@dataclass
class ReflectionTurn:
    user_message: str
    assistant_message: str
    tool_summaries: list[dict[str, str]] = field(default_factory=list)
    # each: {"name": "...", "args_summary": "...", "outcome": "ok"|"error"}


@dataclass
class ReflectionInput:
    conversation_id: str
    run_id: str
    user_id: str
    workspace_id: str | None
    turn: ReflectionTurn


# Agent factory signature: given a ReflectionInput, build & return an Agent
# whose tools include memory_save/memory_update/memory_search bound to the
# user's MemoryService. Concrete factory wired in run_manager / DI setup.
AgentFactory = Callable[[ReflectionInput], Agent]


class ReflectionRunner:
    def __init__(
        self,
        *,
        user_event_service: UserEventService,
        agent_factory: AgentFactory,
        memory_service_factory: Any,  # held for potential introspection
        timeout_sec: float = 30.0,
    ) -> None:
        self._svc = user_event_service
        self._make_agent = agent_factory
        self._timeout = timeout_sec
        self._seen_runs: set[str] = set()  # idempotency

    async def reflect(self, inp: ReflectionInput) -> None:
        if inp.run_id in self._seen_runs:
            return
        self._seen_runs.add(inp.run_id)
        try:
            await asyncio.wait_for(self._reflect_impl(inp), timeout=self._timeout)
        except asyncio.TimeoutError:
            logger.warning("reflection timed out for run_id=%s", inp.run_id)
        except Exception:
            logger.exception("reflection failed for run_id=%s", inp.run_id)

    async def _reflect_impl(self, inp: ReflectionInput) -> None:
        agent = self._make_agent(inp)
        seed = self._build_seed_prompt(inp.turn)

        items: list[dict[str, Any]] = []

        def listener(event: AgentEvent) -> None:
            if event.type != "tool_execution_end":
                return
            name = getattr(event, "tool_name", None)
            if name not in ("memory_save", "memory_update"):
                return
            payload = self._extract_memory_result(event)
            if payload is not None:
                items.append({
                    "op": "save" if name == "memory_save" else "update",
                    **payload,
                })

        unsub = agent.subscribe(listener)
        try:
            with set_reflection_source():
                await agent.prompt(seed)
            await agent.wait_for_idle()
        finally:
            unsub()

        if not items:
            return

        await self._svc.publish(PublishUserEventInput(
            user_id=inp.user_id,
            workspace_id=inp.workspace_id,
            type=UserEventType.MEMORY_UPDATED,
            payload={
                "conversation_id": inp.conversation_id,
                "run_id": inp.run_id,
                "items": items,
            },
        ))

    def _build_seed_prompt(self, turn: ReflectionTurn) -> str:
        # Pack the last turn into a single user-message string. The reflection
        # system prompt frames the task; this just gives it the material.
        tools_block = ""
        if turn.tool_summaries:
            tools_block = "\n\nTools called in this turn:\n" + "\n".join(
                f"- {t['name']}({t.get('args_summary','')}) -> {t.get('outcome','ok')}"
                for t in turn.tool_summaries
            )
        return (
            "Last turn for review:\n\n"
            f"USER: {turn.user_message}\n\n"
            f"ASSISTANT: {turn.assistant_message}"
            f"{tools_block}"
        )

    def _extract_memory_result(self, event: AgentEvent) -> dict[str, Any] | None:
        # tool_execution_end carries the AgentToolResult; memory_save returns
        # {"status": "saved", "memory_id": "..."} as JSON text content.
        try:
            result = getattr(event, "result", None)
            if result is None or not result.content:
                return None
            text = result.content[0].text  # type: ignore[attr-defined]
            obj = json.loads(text)
        except Exception:
            return None
        if obj.get("status") not in ("saved", "updated"):
            return None
        return {k: obj[k] for k in ("memory_id",) if k in obj}
```

> **Note on cubepi event field names:** verify `tool_execution_end` exposes
> `tool_name` and `result` directly. If field names differ, adjust accordingly
> — check `cubepi.agent.types` or the cubepi `AgentEvent` discriminated union.

- [ ] **Step 4: Run tests, expect pass**

Run: `cd backend && uv run pytest tests/unit/test_reflection_runner.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/services/reflection_runner.py backend/tests/unit/test_reflection_runner.py
git commit -m "feat(memory): add ReflectionRunner — detached out-of-band memory reflection"
```

---

### Task 7: Wire trigger in `run_manager` + remove v1 ReflectionMiddleware

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py`
- Delete: `backend/cubeplex/middleware/reflection.py`
- Delete: `backend/cubeplex/prompts/reflection.py`
- Delete: `backend/tests/unit/test_reflection_middleware.py`
- Test: `backend/tests/integration/test_reflection_flow.py`

- [ ] **Step 1: Write failing integration test**

```python
# backend/tests/integration/test_reflection_flow.py
import asyncio
import pytest

from cubeplex.models.memory import MemorySourceType
from cubeplex.models.user_event import UserEventType


@pytest.mark.asyncio
async def test_natural_completion_triggers_reflection_and_emits_event(
    faux_run_manager, test_user, test_workspace, memory_repo, user_event_repo
):
    """E2E-ish: run a faux conversation that produces a preference-shaped message,
    verify (a) MemoryItem written with source_type=REFLECTION,
    (b) UserEvent of type MEMORY_UPDATED appears for this user.
    """
    await faux_run_manager.run_one_turn(
        user_id=test_user.id, workspace_id=test_workspace.id,
        prompt="I prefer concise answers.",
    )

    # reflection is fire-and-forget — poll briefly
    for _ in range(50):
        memories = await memory_repo.list_for_user(test_user.id)
        events = await user_event_repo.list_for_user(test_user.id, since_id=None, limit=10)
        if memories and events:
            break
        await asyncio.sleep(0.1)

    assert any(m.source_type == MemorySourceType.REFLECTION for m in memories)
    assert any(e.type == UserEventType.MEMORY_UPDATED for e in events)


@pytest.mark.asyncio
async def test_aborted_run_skips_reflection(faux_run_manager, ...):
    await faux_run_manager.run_one_turn_then_abort(...)
    # reflection should NOT have been scheduled
    ...
```

Adapt to existing run_manager test infrastructure. Hook fixtures into the
test conftest. This test is the contract — Step 2-onward implements it.

- [ ] **Step 2: Locate `AgentEndEvent` handling in run_manager**

Find the spot in `backend/cubeplex/streams/run_manager.py` where a main run
completes naturally (after the cubepi agent loop exits with a normal stop_reason
— NOT on abort, error, or HITL suspend). Likely near the existing
`AgentEndEvent` consumption (search for `agent_end` or `AgentEndEvent`).

- [ ] **Step 3: Build the agent factory closure**

In the same context where the run_manager has access to `user_id`,
`workspace_id`, `conversation_id`, the memory service factory, and the model
config: define a closure that builds a reflection Agent given a
`ReflectionInput`. Concretely:

```python
def make_reflection_agent(inp: ReflectionInput) -> Agent:
    memory_tools = create_memory_tools(
        service_factory=memory_service_factory_for(inp.user_id, inp.workspace_id),
        conversation_id=inp.conversation_id,
        run_id=inp.run_id,
    )
    # Inject memory snapshot via the same middleware used in main runs
    memory_snapshot_mw = MemorySnapshotMiddleware(...)  # match existing wiring
    return Agent(
        provider=anthropic_provider,  # or whatever cheap-model provider
        model=Model(id=settings.reflection_model_id),  # config-driven
        system_prompt=REFLECTION_SYSTEM_PROMPT,
        tools=memory_tools,
        middleware=[memory_snapshot_mw],
    )
```

The exact wiring matches how main-conversation agents are constructed
elsewhere in run_manager — reuse the same helpers.

- [ ] **Step 4: Schedule reflection on natural completion**

After the main run's loop exits cleanly:

```python
if stop_reason in ("natural", "stop", "should_stop"):
    last_user = _extract_last_user_message(messages)
    final_assistant = _extract_final_assistant(messages)
    tool_summaries = _summarize_tool_calls(this_run_tool_executions)
    asyncio.create_task(
        self.reflection_runner.reflect(ReflectionInput(
            conversation_id=conversation_id,
            run_id=run_id,
            user_id=user_id,
            workspace_id=workspace_id,
            turn=ReflectionTurn(
                user_message=last_user,
                assistant_message=final_assistant,
                tool_summaries=tool_summaries,
            ),
        ))
    )
```

Helpers `_extract_last_user_message` / `_extract_final_assistant` /
`_summarize_tool_calls` are small pure functions — add them in the same file
or a sibling util module.

- [ ] **Step 5: Remove v1 ReflectionMiddleware**

In `run_manager.py`, locate the middleware-stack assembly and remove the
`ReflectionMiddleware()` entry (was Middleware #12, after TodoListMiddleware).
Delete its import.

- [ ] **Step 6: Delete v1 artifacts**

```bash
rm backend/cubeplex/middleware/reflection.py
rm backend/cubeplex/prompts/reflection.py
rm backend/tests/unit/test_reflection_middleware.py
```

- [ ] **Step 7: Run integration test**

Run: `cd backend && uv run pytest tests/integration/test_reflection_flow.py -v`
Expected: both tests PASS.

- [ ] **Step 8: Run full backend test suite (sweep)**

Run: `cd backend && uv run pytest -x`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py backend/tests/integration/test_reflection_flow.py
git rm backend/cubeplex/middleware/reflection.py backend/cubeplex/prompts/reflection.py backend/tests/unit/test_reflection_middleware.py
git commit -m "feat(memory): wire ReflectionRunner trigger; remove v1 in-band middleware"
```

---

## Phase 3: Frontend

### Task 8: User-events SSE client + `useUserEvents` hook + Zustand store

**Files:**
- Create: `frontend/packages/core/src/sse/userEventClient.ts`
- Create: `frontend/packages/core/src/stores/memoryEventStore.ts`
- Create: `frontend/packages/core/src/hooks/useUserEvents.ts`
- Modify: `frontend/packages/web/src/app/layout.tsx` (or equivalent shell)

- [ ] **Step 1: Write the SSE client**

```typescript
// frontend/packages/core/src/sse/userEventClient.ts
import type { UserEvent } from "@cubeplex/core";

export type UserEventHandler = (event: UserEvent) => void;

export interface UserEventClient {
  start(handler: UserEventHandler): void;
  stop(): void;
}

const STORAGE_KEY = "cubeplex.userEvents.lastSeenId";

export function createUserEventClient(baseUrl: string): UserEventClient {
  let es: EventSource | null = null;
  let stopped = false;

  return {
    start(handler) {
      const since = localStorage.getItem(STORAGE_KEY) ?? "";
      const url = `${baseUrl}/api/v1/user/events${since ? `?since=${since}` : ""}`;
      es = new EventSource(url, { withCredentials: true });
      es.addEventListener("memory_updated", (ev: MessageEvent) => {
        const parsed = JSON.parse(ev.data) as UserEvent;
        localStorage.setItem(STORAGE_KEY, parsed.id);
        handler(parsed);
      });
      es.onerror = () => {
        if (stopped) return;
        // EventSource auto-reconnects; nothing extra to do.
      };
    },
    stop() {
      stopped = true;
      es?.close();
      es = null;
    },
  };
}
```

- [ ] **Step 2: Add `UserEvent` type to core**

Match the backend payload shape. Add to `frontend/packages/core/src/types/userEvent.ts`:

```typescript
export interface MemoryUpdateItem {
  op: "save" | "update";
  memory_id: string;
}

export interface MemoryUpdatedPayload {
  conversation_id: string;
  run_id: string;
  items: MemoryUpdateItem[];
}

export interface UserEvent {
  id: string;
  type: "memory_updated";
  workspace_id: string | null;
  payload: MemoryUpdatedPayload;
  created_at: string;
}
```

Re-export from the core barrel.

- [ ] **Step 3: Implement `memoryEventStore`**

```typescript
// frontend/packages/core/src/stores/memoryEventStore.ts
import { create } from "zustand";
import type { UserEvent } from "@cubeplex/core";

interface MemoryEventState {
  byConversation: Record<string, UserEvent[]>;
  add: (ev: UserEvent) => void;
  markRead: (id: string) => void;
}

export const useMemoryEventStore = create<MemoryEventState>((set) => ({
  byConversation: {},
  add: (ev) =>
    set((s) => {
      const conv = ev.payload.conversation_id;
      const existing = s.byConversation[conv] ?? [];
      if (existing.some((e) => e.id === ev.id)) return s;  // dedupe
      return {
        byConversation: { ...s.byConversation, [conv]: [...existing, ev] },
      };
    }),
  markRead: (id) =>
    set((s) => {
      const next: Record<string, UserEvent[]> = {};
      for (const [k, list] of Object.entries(s.byConversation)) {
        next[k] = list.filter((e) => e.id !== id);
      }
      return { byConversation: next };
    }),
}));
```

- [ ] **Step 4: Implement `useUserEvents` hook**

```typescript
// frontend/packages/core/src/hooks/useUserEvents.ts
import { useEffect } from "react";
import { createUserEventClient } from "../sse/userEventClient";
import { useMemoryEventStore } from "../stores/memoryEventStore";

export function useUserEvents(apiBaseUrl: string) {
  const add = useMemoryEventStore((s) => s.add);
  useEffect(() => {
    const client = createUserEventClient(apiBaseUrl);
    client.start((ev) => {
      if (ev.type === "memory_updated") add(ev);
    });
    return () => client.stop();
  }, [apiBaseUrl, add]);
}
```

- [ ] **Step 5: Mount in app shell**

In `frontend/packages/web/src/app/layout.tsx` (or the actual root client
component used as the auth-gated app shell — match existing patterns), add a
client component that calls `useUserEvents(apiBaseUrl)` once. Render
nothing.

- [ ] **Step 6: Verify with manual smoke**

Run:
```bash
cd frontend && pnpm dev
```
Open browser, log in. With backend running, manually POST a `UserEvent`
through a test endpoint or by invoking the service directly via a debug
script. Confirm the EventSource connection is open in DevTools Network tab
and the store receives the event.

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/core/src/sse/userEventClient.ts frontend/packages/core/src/stores/memoryEventStore.ts frontend/packages/core/src/hooks/useUserEvents.ts frontend/packages/core/src/types/userEvent.ts frontend/packages/core/src/index.ts frontend/packages/web/src/app/layout.tsx
git commit -m "feat(frontend): user-events SSE client + Zustand store + app-shell mount"
```

---

### Task 9: Inline memory chip + toast fallback

**Files:**
- Create: `frontend/packages/web/src/components/memory/MemoryUpdateChip.tsx`
- Create: `frontend/packages/web/src/components/memory/MemoryUpdateToast.tsx`
- Modify: existing conversation timeline component (locate via
  `frontend/packages/web/src/components/conversation/`)
- Test: `frontend/packages/web/tests/memory-update-chip.test.tsx`

- [ ] **Step 1: Write failing chip test**

```typescript
// frontend/packages/web/tests/memory-update-chip.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryUpdateChip } from "@/components/memory/MemoryUpdateChip";

test("renders item count and content preview", () => {
  render(<MemoryUpdateChip eventId="uev_1" items={[
    { op: "save", memory_id: "mem_a" },
  ]} onClick={() => {}} />);
  expect(screen.getByText(/已记住/i)).toBeInTheDocument();
});

test("calls onClick when activated", () => {
  const onClick = jest.fn();
  render(<MemoryUpdateChip eventId="uev_1" items={[{ op: "save", memory_id: "m" }]} onClick={onClick} />);
  fireEvent.click(screen.getByRole("button"));
  expect(onClick).toHaveBeenCalled();
});
```

- [ ] **Step 2: Implement chip component**

Concrete design pass uses the `frontend-design` skill — for the plan stub,
shape:

```tsx
// frontend/packages/web/src/components/memory/MemoryUpdateChip.tsx
import { cn } from "@/lib/utils";

interface Props {
  eventId: string;
  items: { op: "save" | "update"; memory_id: string }[];
  onClick: () => void;
}

export function MemoryUpdateChip({ items, onClick }: Props) {
  const verb = items[0]?.op === "update" ? "已更新" : "已记住";
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs",
        "bg-muted text-muted-foreground hover:text-foreground",
        "transition-colors"
      )}
    >
      <span aria-hidden>💭</span>
      <span>{verb} {items.length} 条记忆</span>
    </button>
  );
}
```

- [ ] **Step 3: Render chip in conversation timeline**

In the timeline component, after each run boundary, look up
`memoryEventStore.byConversation[conversationId]` for events whose
`payload.run_id` matches the just-rendered run. Render a `MemoryUpdateChip`
inline between turns.

On click:
- Mark the event as read: `POST /api/v1/user/events/{id}/read`.
- Optionally navigate to the memory panel filtered to the saved item(s).

- [ ] **Step 4: Implement toast fallback**

```tsx
// frontend/packages/web/src/components/memory/MemoryUpdateToast.tsx
import { useEffect } from "react";
import { toast } from "sonner";  // or whichever toast lib is in use
import { useMemoryEventStore } from "@cubeplex/core";
import { useRouter, usePathname } from "next/navigation";

export function MemoryUpdateToastBridge() {
  const events = useMemoryEventStore((s) => s.byConversation);
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    for (const [convId, list] of Object.entries(events)) {
      for (const ev of list) {
        const visible = pathname?.includes(`/conversation/${convId}`);
        if (visible) continue;  // chip will render in-place
        toast("Memory updated", {
          action: { label: "View", onClick: () => router.push(`/conversation/${convId}`) },
        });
      }
    }
  }, [events, pathname, router]);

  return null;
}
```

Mount the bridge once at the app shell next to `useUserEvents`.

- [ ] **Step 5: Run frontend tests**

Run:
```bash
cd frontend && pnpm test packages/web/tests/memory-update-chip.test.tsx
```
Expected: 2 PASS.

- [ ] **Step 6: Manual smoke**

```bash
cd frontend && pnpm dev
# In browser, send a preference message; observe chip appearing inline
# after AgentEndEvent (within ~5s typically).
```

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/web/src/components/memory/ frontend/packages/web/tests/memory-update-chip.test.tsx frontend/packages/web/src/components/conversation/
git commit -m "feat(frontend): inline memory chip + off-conversation toast for user events"
```

---

## Phase 4: Integration test

### Task 10: E2E test — preference message → memory chip

**Files:**
- Create: `frontend/packages/web/tests/e2e/memory-reflection.spec.ts`

- [ ] **Step 1: Write the test**

```typescript
// frontend/packages/web/tests/e2e/memory-reflection.spec.ts
import { test, expect } from "@playwright/test";

test("preference message triggers reflection and surfaces memory chip", async ({ page }) => {
  await page.goto("/");
  // assumes a logged-in fixture state; adjust to project conventions
  await page.getByRole("textbox", { name: /message/i }).fill(
    "我比较喜欢简洁的回答。"
  );
  await page.getByRole("button", { name: /send/i }).click();

  // wait for assistant reply to finish
  await page.waitForSelector("[data-testid='assistant-message']", { timeout: 30_000 });

  // chip appears within ~10s after AgentEndEvent
  await expect(page.getByRole("button", { name: /已记住/i }))
    .toBeVisible({ timeout: 15_000 });
});
```

- [ ] **Step 2: Run against worktree stack**

```bash
# from inside the worktree, with .worktree.env loaded:
cd backend && uv run python main.py &
cd frontend && pnpm dev &
sleep 5
cd frontend && pnpm exec playwright test packages/web/tests/e2e/memory-reflection.spec.ts
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/tests/e2e/memory-reflection.spec.ts
git commit -m "test(e2e): memory reflection chip end-to-end"
```

---

## Final review checklist

Before opening PR for review:

- [ ] `uv run pytest` clean (full backend suite)
- [ ] `pnpm test` clean (frontend unit + integration)
- [ ] `pnpm exec playwright test memory-reflection.spec.ts` PASS
- [ ] `uv run ruff check && uv run mypy cubeplex` clean
- [ ] `pnpm typecheck && pnpm lint` clean
- [ ] Smoke: real backend + frontend, send preference message, see chip
- [ ] Smoke: send a non-preference message, confirm NO chip appears
- [ ] Smoke: navigate away mid-reflection, verify toast appears on next page

After verification: rebase or merge cleanup, push, reply to v2 design review threads with the final implementation summary.
