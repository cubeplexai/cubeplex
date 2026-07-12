# Auto-Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the agent remember automatically — Layer 1: prompt the agent to call the existing `memory_save` tool proactively (personal scope); Layer 2: a per-conversation background pass that distills the conversation's recent history into personal memory (extract/merge/archive), gated cheaply and decoupled from the live turn.

**Architecture:** See `docs/dev/specs/2026-05-25-auto-memory-design.md`. Layer 1 is prompt authoring. Layer 2 is a new `MemoryConsolidationService` triggered by a cheap post-run Redis gate (per-conversation counter + `last_consolidated_at` + lock, atomic high-water-mark), running one `OneShotLLM` call whose JSON ops are validated/capped and applied via `MemoryService` with scope hard-coded `PERSONAL` and source attribution stamped.

**Tech Stack:** FastAPI, redis.asyncio (fakeredis in tests), cubepi `OneShotLLM`, SQLModel `MemoryService`/`MemoryRepository`, pytest.

**Key existing APIs (verified):**
- Enums (`cubeplex/models/memory.py`): `MemoryScope.{PERSONAL,WORKSPACE,ORG}`, `MemoryType.{PREFERENCE,PROJECT_FACT,PROCEDURE,CORRECTION,DECISION,ORG_POLICY}`, `MemoryStatus.{ACTIVE,ARCHIVED}`, `MemorySourceType.{CONVERSATION,TOOL_RESULT,ARTIFACT,MANUAL,IMPORT}`.
- `MemoryService(repo, *, user_id, org_id, workspace_id)` → `create(CreateMemoryInput)`, `update(memory_id, *, content=, type_=, confidence=, status=)`, `archive(memory_id)`. `CreateMemoryInput(scope, type, content, confidence=0.8, source_type=MANUAL, source_conversation_id=None, source_run_id=None, source_artifact_id=None, source_excerpt=None)`.
- `MemoryRepository(session, *, user_id, org_id, workspace_id).list(*, scope=, status=MemoryStatus.ACTIVE, limit=200)`.
- `OneShotLLM(provider, model).generate_once(*, system, messages, max_output_tokens) -> str`.
- `cubeplex.agents.checkpointer.init_checkpointer()` → `cp.load(conversation_id)` → `CheckpointData | None` with `.messages`.

---

## File Structure

- Modify `backend/cubeplex/prompts/memory.py` — add the always-injected authoring block + per-type triggers (Layer 1).
- Modify `backend/cubeplex/middleware/memory.py` — `transform_system_prompt` always injects the static authoring block; pinned block stays conditional (Layer 1).
- Modify `backend/cubeplex/models/memory.py` — add `MemorySourceType.CONSOLIDATION` (Layer 2 attribution).
- Create `backend/cubeplex/services/memory_consolidation.py` — Redis gate/lock helpers + the consolidation pass (Layer 2 core).
- Modify `backend/cubeplex/streams/run_manager.py` — per-run `note_run` + post-run gate → spawn tracked task; track tasks for drain (Layer 2 wiring).
- Tests: `backend/tests/unit/test_memory_consolidation_gate.py`, `backend/tests/unit/test_memory_consolidation_pass.py`, `backend/tests/unit/test_memory_authoring_prompt.py`, `backend/tests/e2e/test_auto_memory.py`.

---

## Task 1: Layer 1 — always-injected authoring block + per-type triggers

**Files:** Modify `backend/cubeplex/prompts/memory.py`, `backend/cubeplex/middleware/memory.py`; Test `backend/tests/unit/test_memory_authoring_prompt.py`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_memory_authoring_prompt.py`:

```python
import pytest

from cubeplex.prompts.memory import MEMORY_AUTHORING_BLOCK


def test_authoring_block_covers_every_type_trigger():
    block = MEMORY_AUTHORING_BLOCK
    # Mentions the tool + every memory type's when-to-save.
    assert "memory_save" in block
    for t in ("preference", "correction", "procedure", "project_fact", "decision", "org_policy"):
        assert t in block
    # Proactive saves are personal-only.
    assert "personal" in block.lower()
    # Explicit-ask rule present.
    assert "explicitly" in block.lower()


@pytest.mark.asyncio
async def test_transform_system_prompt_injects_authoring_without_pinned(monkeypatch):
    from cubeplex.middleware.memory import MemoryMiddleware

    # repo_factory whose pinned render is empty (no pinned memory).
    class _Repo:
        pass

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():
        yield _Repo()

    monkeypatch.setattr("cubeplex.middleware.memory._render_pinned", lambda repo: _empty())

    async def _empty():
        return ""

    mw = MemoryMiddleware(repo_factory=_factory)
    out = await mw.transform_system_prompt("BASE")
    assert "BASE" in out
    assert "memory_save" in out  # authoring block injected even with no pinned memory
```

- [ ] **Step 2: Run it, confirm FAIL**

Run: `cd backend && uv run pytest tests/unit/test_memory_authoring_prompt.py -v`
Expected: FAIL — `MEMORY_AUTHORING_BLOCK` doesn't exist.

- [ ] **Step 3: Add the authoring block** to `backend/cubeplex/prompts/memory.py` (append after `MEMORY_PROMPT_HEADER`):

```python
MEMORY_AUTHORING_BLOCK: str = """\
## Saving memory

You can persist durable knowledge with the `memory_save` tool so future
conversations benefit. Build this up over time — don't wait to be asked.

**Save PROACTIVELY (scope=personal) when you learn:**
- `preference` — the user's style or how they want you to collaborate.
- `correction` — the user corrects you ("no, don't do X"), OR confirms a
  non-obvious approach worked ("yes, exactly", accepting an unusual choice).
  Record *why*, so you can judge edge cases later. Watch for the quiet
  confirmations, not just explicit "no"s.
- `project_fact` / `decision` — who is doing what, why, or by when; or a settled
  decision. Convert relative dates to absolute (e.g. "Thursday" → "2026-03-05").
- `procedure` — a reusable workflow worth repeating.
- `org_policy` — an organization-level rule or policy.

**Scope:** proactive saves are ALWAYS `scope=personal`. Use `workspace`/`org`
ONLY when the user explicitly asks to share something with their team/org.

If the user explicitly asks you to remember something, save it immediately.

**Do NOT save:** things trivially derivable from the code or git history; secrets;
transient task state (use a plan/todo instead). Prefer updating an existing item
(`memory_update`) over creating a contradictory new one — check first with
`memory_search`.
"""
```

- [ ] **Step 4: Always inject it in `transform_system_prompt`**

In `backend/cubeplex/middleware/memory.py`, import the block and change
`transform_system_prompt` so the authoring block is appended unconditionally,
and the pinned block stays conditional. Replace the method body:

```python
    async def transform_system_prompt(
        self,
        system_prompt: str,
        *,
        signal: object = None,
    ) -> str:
        del signal
        from cubeplex.prompts.memory import MEMORY_AUTHORING_BLOCK

        async with self._repo_factory() as repo:
            pinned_text = await _render_pinned(repo)

        parts = [system_prompt] if system_prompt else []
        if pinned_text:
            parts.append(MEMORY_PROMPT_HEADER + pinned_text)
        parts.append(MEMORY_AUTHORING_BLOCK)
        return "\n\n".join(parts)
```

(`MEMORY_PROMPT_HEADER` is already imported in this file. The authoring block is
static — no timestamps/counts — so the cache-eligible prefix stays stable.)

- [ ] **Step 5: Run tests + typecheck**

Run: `cd backend && uv run pytest tests/unit/test_memory_authoring_prompt.py -v && uv run mypy cubeplex/middleware/memory.py cubeplex/prompts/memory.py`
Expected: 2 passed; mypy Success.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/prompts/memory.py backend/cubeplex/middleware/memory.py backend/tests/unit/test_memory_authoring_prompt.py
git commit -m "feat(memory): always-inject proactive-save authoring block (Layer 1)"
```

---

## Task 2: Add `MemorySourceType.CONSOLIDATION`

**Files:** Modify `backend/cubeplex/models/memory.py`.

- [ ] **Step 1: Add the enum value**

In `MemorySourceType`, add:

```python
    CONSOLIDATION = "consolidation"
```

- [ ] **Step 2: Check for a migration**

The column stores the StrEnum as a string. Confirm no native DB enum/CHECK needs
updating:

Run: `cd backend && uv run alembic check 2>&1 | tail -5`
Expected: "No new upgrade operations detected." If it reports a diff for the
memory table, generate it: `uv run alembic revision --autogenerate -m "add consolidation memory source"` and review (do NOT hand-edit).

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/models/memory.py backend/alembic/versions/ 2>/dev/null
git commit -m "feat(memory): add CONSOLIDATION memory source type"
```

---

## Task 3: Layer 2 — Redis gate/lock helpers (per conversation)

**Files:** Create `backend/cubeplex/services/memory_consolidation.py`; Test `backend/tests/unit/test_memory_consolidation_gate.py`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_memory_consolidation_gate.py`:

```python
import asyncio

import fakeredis.aioredis
import pytest

from cubeplex.services import memory_consolidation as mc


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


@pytest.mark.asyncio
async def test_note_run_increments_counter(redis):
    await mc.note_run(redis, "t", "conv1")
    await mc.note_run(redis, "t", "conv1")
    assert await mc._counter(redis, "t", "conv1") == 2


@pytest.mark.asyncio
async def test_should_consolidate_requires_both_gates(redis):
    import time as _t

    # Run gate: below minRuns → no (last=0 means "never", so the time gate is
    # already satisfied — only the run count is short here).
    await mc.note_run(redis, "t", "conv1")
    assert await mc.should_consolidate(redis, "t", "conv1", min_hours=0, min_runs=5) is False
    # Enough runs + never-consolidated → yes.
    for _ in range(4):
        await mc.note_run(redis, "t", "conv1")  # 5 total
    assert await mc.should_consolidate(redis, "t", "conv1", min_hours=0, min_runs=5) is True
    # Time gate: just consolidated (last≈now) → too soon, even with enough runs → no.
    await mc.mark_consolidated(redis, "t", "conv1", cutoff=_t.time(), consumed=0)
    for _ in range(5):
        await mc.note_run(redis, "t", "conv1")
    assert await mc.should_consolidate(redis, "t", "conv1", min_hours=999, min_runs=1) is False


@pytest.mark.asyncio
async def test_lock_excludes_concurrent_holder(redis):
    tok = await mc.acquire_lock(redis, "t", "conv1", ttl_s=30)
    assert tok is not None
    assert await mc.acquire_lock(redis, "t", "conv1", ttl_s=30) is None
    await mc.release_lock(redis, "t", "conv1", tok)
    assert await mc.acquire_lock(redis, "t", "conv1", ttl_s=30) is not None


@pytest.mark.asyncio
async def test_high_water_mark_keeps_runs_arriving_during_pass(redis):
    for _ in range(5):
        await mc.note_run(redis, "t", "conv1")
    n = await mc._counter(redis, "t", "conv1")  # capture N=5
    # A run arrives mid-pass:
    await mc.note_run(redis, "t", "conv1")  # counter now 6
    await mc.mark_consolidated(redis, "t", "conv1", cutoff=123.0, consumed=n)
    # 6 - 5 = 1 run still counted (the one that arrived during the pass).
    assert await mc._counter(redis, "t", "conv1") == 1
    assert await mc.get_last(redis, "t", "conv1") == 123.0
```

- [ ] **Step 2: Run it, confirm FAIL**

Run: `cd backend && uv run pytest tests/unit/test_memory_consolidation_gate.py -v`
Expected: FAIL — module/functions missing.

- [ ] **Step 3: Implement the helpers**

Create `backend/cubeplex/services/memory_consolidation.py` (gate/lock section):

```python
"""Per-conversation background memory consolidation (Layer 2).

A cheap Redis gate (per-conversation run counter + last-consolidated timestamp +
lock) decides when to run a single OneShotLLM pass that distills the
conversation's recent history into the user's personal memory.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from loguru import logger
from redis.asyncio import Redis

_TTL_S = 7 * 24 * 3600  # keep gate keys ~a week of inactivity


def _k(prefix: str, kind: str, conversation_id: str) -> str:
    return f"{prefix}:memcons:{kind}:{conversation_id}"


async def _counter(redis: Redis, prefix: str, conversation_id: str) -> int:
    raw = await redis.get(_k(prefix, "runs", conversation_id))
    return int(raw) if raw else 0


async def get_last(redis: Redis, prefix: str, conversation_id: str) -> float:
    raw = await redis.get(_k(prefix, "last", conversation_id))
    return float(raw) if raw else 0.0


async def note_run(redis: Redis, prefix: str, conversation_id: str) -> None:
    """Count one finished run for this conversation."""
    key = _k(prefix, "runs", conversation_id)
    await redis.incr(key)
    await redis.expire(key, _TTL_S)


async def should_consolidate(
    redis: Redis,
    prefix: str,
    conversation_id: str,
    *,
    min_hours: float,
    min_runs: int,
) -> bool:
    counter = await _counter(redis, prefix, conversation_id)
    if counter < min_runs:
        return False
    last = await get_last(redis, prefix, conversation_id)
    return (time.time() - last) >= min_hours * 3600


async def acquire_lock(
    redis: Redis, prefix: str, conversation_id: str, *, ttl_s: int
) -> str | None:
    """SET NX a holder token. Returns the token, or None if held."""
    token = uuid.uuid4().hex
    ok = await redis.set(_k(prefix, "lock", conversation_id), token, nx=True, ex=ttl_s)
    return token if ok else None


async def release_lock(
    redis: Redis, prefix: str, conversation_id: str, token: str
) -> None:
    """Release only if we still hold it (compare-and-delete)."""
    key = _k(prefix, "lock", conversation_id)
    cur = await redis.get(key)
    if cur is None:
        return
    # The injected Redis client may or may not use decode_responses; handle both.
    cur_str = cur.decode() if isinstance(cur, (bytes, bytearray)) else cur
    if cur_str == token:
        await redis.delete(key)


async def mark_consolidated(
    redis: Redis,
    prefix: str,
    conversation_id: str,
    *,
    cutoff: float,
    consumed: int,
) -> None:
    """High-water-mark: advance last to cutoff and DECRBY the consumed count
    (never reset-to-0), so runs that arrived during the pass stay counted."""
    last_key = _k(prefix, "last", conversation_id)
    await redis.set(last_key, repr(cutoff), ex=_TTL_S)
    if consumed > 0:
        await redis.decrby(_k(prefix, "runs", conversation_id), consumed)
```

- [ ] **Step 4: Run tests + mypy**

Run: `cd backend && uv run pytest tests/unit/test_memory_consolidation_gate.py -v && uv run mypy cubeplex/services/memory_consolidation.py`
Expected: 4 passed; mypy Success.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/services/memory_consolidation.py backend/tests/unit/test_memory_consolidation_gate.py
git commit -m "feat(memory): per-conversation consolidation gate + lock helpers"
```

---

## Task 4: Layer 2 — the consolidation pass (ops parse/validate/apply)

**Files:** Modify `backend/cubeplex/services/memory_consolidation.py`; Test `backend/tests/unit/test_memory_consolidation_pass.py`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_memory_consolidation_pass.py`:

```python
import json

import pytest

from cubeplex.services import memory_consolidation as mc
from cubeplex.models.memory import MemoryScope, MemorySourceType, MemoryType


class _Item:
    def __init__(self, scope):
        self.scope = scope


class _FakeRepo:
    def __init__(self, items):
        self._items = items  # id -> _Item

    async def get(self, memory_id):
        return self._items.get(memory_id)


class _FakeService:
    def __init__(self, items=None):
        self.created = []
        self.updated = []
        self.archived = []
        self.repo = _FakeRepo(items or {})

    async def create(self, inp):
        self.created.append(inp)

    async def update(self, memory_id, *, content=None, **kw):
        self.updated.append((memory_id, content))

    async def archive(self, memory_id):
        self.archived.append(memory_id)


def test_parse_ops_rejects_malformed():
    assert mc.parse_ops("not json", max_ops=10) is None
    assert mc.parse_ops('{"ops": "x"}', max_ops=10) is None
    # Over cap → reject whole batch.
    big = json.dumps({"ops": [{"action": "archive", "id": f"m{i}"} for i in range(11)]})
    assert mc.parse_ops(big, max_ops=10) is None


def test_parse_ops_filters_invalid_ops_keeps_valid():
    raw = json.dumps({"ops": [
        {"action": "extract", "type": "preference", "content": "likes dark mode"},
        {"action": "extract", "type": "BOGUS", "content": "x"},          # bad type
        {"action": "merge", "id": "m1", "content": "merged"},
        {"action": "merge"},                                              # missing id/content
        {"action": "archive", "id": "m2"},
        {"action": "frobnicate", "id": "m3"},                            # bad action
    ]})
    ops = mc.parse_ops(raw, max_ops=10)
    assert ops is not None
    kinds = [o["action"] for o in ops]
    assert kinds == ["extract", "merge", "archive"]


@pytest.mark.asyncio
async def test_apply_ops_forces_personal_scope_and_source():
    svc = _FakeService(items={
        "m1": _Item(MemoryScope.PERSONAL),
        "m2": _Item(MemoryScope.PERSONAL),
    })
    ops = [
        {"action": "extract", "type": "preference", "content": "likes dark mode"},
        {"action": "merge", "id": "m1", "content": "merged"},
        {"action": "archive", "id": "m2"},
    ]
    await mc.apply_ops(svc, ops, conversation_id="conv1", run_id="run1")
    assert len(svc.created) == 1
    inp = svc.created[0]
    assert inp.scope == MemoryScope.PERSONAL
    assert inp.type == MemoryType.PREFERENCE
    assert inp.source_type == MemorySourceType.CONSOLIDATION
    assert inp.source_conversation_id == "conv1"
    assert svc.updated == [("m1", "merged")]
    assert svc.archived == ["m2"]


@pytest.mark.asyncio
async def test_apply_ops_skips_non_personal_or_missing_targets():
    # m-ws is a workspace item the user can read; m-gone doesn't exist.
    svc = _FakeService(items={"m-ws": _Item(MemoryScope.WORKSPACE)})
    ops = [
        {"action": "merge", "id": "m-ws", "content": "x"},   # shared → skip
        {"action": "archive", "id": "m-gone"},               # missing → skip
    ]
    await mc.apply_ops(svc, ops, conversation_id="conv1", run_id=None)
    assert svc.updated == []
    assert svc.archived == []
```

- [ ] **Step 2: Run it, confirm FAIL**

Run: `cd backend && uv run pytest tests/unit/test_memory_consolidation_pass.py -v`
Expected: FAIL — `parse_ops`/`apply_ops` missing.

- [ ] **Step 3: Implement parse + apply** (append to `memory_consolidation.py`):

```python
from cubeplex.models.memory import MemoryScope, MemorySourceType, MemoryType
from cubeplex.services.memory import CreateMemoryInput, MemoryService

_VALID_TYPES = {t.value for t in MemoryType}


def parse_ops(raw: str, *, max_ops: int) -> list[dict[str, Any]] | None:
    """Parse + validate the LLM's JSON op envelope. Returns the list of valid
    ops, or None to reject the whole batch (bad JSON / wrong shape / over cap)."""
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return None
    ops = doc.get("ops") if isinstance(doc, dict) else None
    if not isinstance(ops, list) or len(ops) > max_ops:
        return None

    valid: list[dict[str, Any]] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        action = op.get("action")
        if action == "extract":
            if op.get("type") in _VALID_TYPES and isinstance(op.get("content"), str) and op["content"].strip():
                valid.append(op)
        elif action == "merge":
            if isinstance(op.get("id"), str) and isinstance(op.get("content"), str) and op["content"].strip():
                valid.append(op)
        elif action == "archive":
            if isinstance(op.get("id"), str):
                valid.append(op)
    return valid


async def apply_ops(
    service: MemoryService,
    ops: list[dict[str, Any]],
    *,
    conversation_id: str,
    run_id: str | None,
) -> None:
    """Apply ops. Scope is hard-coded PERSONAL on create (the op schema has no
    scope); merge/archive targets are verified to be the user's PERSONAL items
    before mutation, so a hallucinated id can't touch a shared (workspace/org)
    item the user merely has read access to. Source stamped CONSOLIDATION."""
    for op in ops:
        action = op["action"]
        try:
            if action == "extract":
                await service.create(
                    CreateMemoryInput(
                        scope=MemoryScope.PERSONAL,
                        type=MemoryType(op["type"]),
                        content=op["content"].strip(),
                        source_type=MemorySourceType.CONSOLIDATION,
                        source_conversation_id=conversation_id,
                        source_run_id=run_id,
                    )
                )
            elif action in ("merge", "archive"):
                # Guard: only mutate the user's own PERSONAL items.
                target = await service.repo.get(op["id"])
                if target is None or target.scope != MemoryScope.PERSONAL:
                    continue
                if action == "merge":
                    await service.update(op["id"], content=op["content"].strip())
                else:
                    await service.archive(op["id"])
        except Exception:
            # One bad op (e.g. id no longer accessible) must not abort the batch.
            logger.warning("consolidation op failed: {}", op, exc_info=True)
```

(`json` and `logger` are in the module header imports added in Task 3 Step 3.)

- [ ] **Step 4: Run tests + mypy**

Run: `cd backend && uv run pytest tests/unit/test_memory_consolidation_pass.py -v && uv run mypy cubeplex/services/memory_consolidation.py`
Expected: 3 passed; mypy Success.

- [ ] **Step 5: Implement the orchestrator `run_consolidation`** (append; ties gate+lock+history+LLM+ops together):

```python
DEFAULT_MIN_HOURS = 6.0
DEFAULT_MIN_RUNS = 5
MAX_OPS = 20
HISTORY_MSG_CAP = 40
LOCK_TTL_S = 120
EXTRACT_MODEL_MAX_TOKENS = 1500

CONSOLIDATION_SYSTEM = """\
You distill a conversation into durable PERSONAL memory for one user. Output ONLY
a JSON object: {"ops": [...]}. Each op is one of:
- {"action":"extract","type":<preference|correction|procedure|project_fact|decision|org_policy>,"content":"..."}
- {"action":"merge","id":"<existing memory id>","content":"<updated text>"}
- {"action":"archive","id":"<existing memory id>"}
Rules: only durable facts worth recalling in FUTURE conversations; never secrets
or transient task state; prefer merge over a contradictory new extract; dedup
against the existing items provided; at most %d ops. If nothing is worth saving,
return {"ops": []}.
""" % MAX_OPS


async def run_consolidation(
    *,
    redis: Redis,
    prefix: str,
    conversation_id: str,
    user_id: str,
    org_id: str | None,
    workspace_id: str | None,
    one_shot,            # OneShotLLM
    session_maker,       # async_session_maker
    min_hours: float = DEFAULT_MIN_HOURS,
    min_runs: int = DEFAULT_MIN_RUNS,
) -> None:
    """Best-effort. Never raises into the caller."""
    from cubepi.providers.base import TextContent, UserMessage

    from cubeplex.agents.checkpointer import init_checkpointer
    from cubeplex.repositories.memory import MemoryRepository

    token = await acquire_lock(redis, prefix, conversation_id, ttl_s=LOCK_TTL_S)
    if token is None:
        return
    cutoff = time.time()
    consumed = await _counter(redis, prefix, conversation_id)
    try:
        # 1. Load this conversation's recent history (window-capped, newest kept).
        async with init_checkpointer() as cp:
            data = await cp.load(conversation_id)
        if data is None or not data.messages:
            await mark_consolidated(redis, prefix, conversation_id, cutoff=cutoff, consumed=consumed)
            return
        recent = data.messages[-HISTORY_MSG_CAP:]
        history_text = _render_history(recent)

        # 2. Load existing personal memory for dedup context.
        async with session_maker() as s:
            repo = MemoryRepository(s, user_id=user_id, org_id=org_id, workspace_id=workspace_id)
            existing = await repo.list(scope=MemoryScope.PERSONAL, status=MemoryStatus.ACTIVE, limit=200)
        existing_text = "\n".join(f"- [{m.id}] ({m.type.value}) {m.content}" for m in existing)

        # 3. One LLM pass.
        prompt = (
            f"Existing personal memory items:\n{existing_text or '(none)'}\n\n"
            f"Conversation transcript:\n{history_text}"
        )
        raw = await one_shot.generate_once(
            system=CONSOLIDATION_SYSTEM,
            messages=[UserMessage(content=[TextContent(text=prompt)])],
            max_output_tokens=EXTRACT_MODEL_MAX_TOKENS,
        )
        ops = parse_ops(raw, max_ops=MAX_OPS)
        if ops:
            async with session_maker() as s:
                repo = MemoryRepository(s, user_id=user_id, org_id=org_id, workspace_id=workspace_id)
                service = MemoryService(repo, user_id=user_id, org_id=org_id, workspace_id=workspace_id)
                await apply_ops(service, ops, conversation_id=conversation_id, run_id=None)

        await mark_consolidated(redis, prefix, conversation_id, cutoff=cutoff, consumed=consumed)
    except Exception:
        logger.warning("memory consolidation failed for {}", conversation_id, exc_info=True)
        # Leave `last` unchanged; do NOT decrement the counter → retries next eligible run.
    finally:
        await release_lock(redis, prefix, conversation_id, token)


def _render_history(messages: list[Any]) -> str:
    lines: list[str] = []
    for m in messages:
        role = getattr(m, "role", "?")
        content = getattr(m, "content", None)
        text = ""
        if isinstance(content, list):
            text = " ".join(getattr(b, "text", "") for b in content if getattr(b, "type", "") == "text")
        lines.append(f"{role}: {text}".strip())
    return "\n".join(l for l in lines if l)
```

Need imports at top: `from cubeplex.models.memory import MemoryStatus`. Add it to the model import line.

- [ ] **Step 6: Run all consolidation tests + mypy**

Run: `cd backend && uv run pytest tests/unit/test_memory_consolidation_gate.py tests/unit/test_memory_consolidation_pass.py -v && uv run mypy cubeplex/services/memory_consolidation.py`
Expected: green; mypy Success.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/services/memory_consolidation.py backend/tests/unit/test_memory_consolidation_pass.py
git commit -m "feat(memory): consolidation pass — ops parse/validate/apply + orchestrator"
```

---

## Task 5: Wire Layer 2 into the run lifecycle (gate + tracked background task)

**Files:** Modify `backend/cubeplex/streams/run_manager.py`.

- [ ] **Step 1: Add a tracked task set in `RunManager.__init__`**

After `self._agents: dict[str, Any] = {}` add:

```python
        self._consolidation_tasks: set[asyncio.Task[None]] = set()
```

- [ ] **Step 2: Add the post-run gate hook**

Add a method on `RunManager`:

```python
    async def _maybe_consolidate_memory(
        self, *, conversation_id: str, ctx: RunContext
    ) -> None:
        """Cheap per-run gate; spawn a tracked background consolidation task when
        due. Never raises into the run path."""
        try:
            from cubeplex.config import config as _cfg
            from cubeplex.services import memory_consolidation as mc

            if not _cfg.get("memory.consolidation.enabled", True):
                return
            await mc.note_run(self._redis, self._key_prefix, conversation_id)
            min_hours = float(_cfg.get("memory.consolidation.min_hours", mc.DEFAULT_MIN_HOURS))
            min_runs = int(_cfg.get("memory.consolidation.min_runs", mc.DEFAULT_MIN_RUNS))
            if not await mc.should_consolidate(
                self._redis, self._key_prefix, conversation_id,
                min_hours=min_hours, min_runs=min_runs,
            ):
                return

            from cubeplex.db.engine import async_session_maker
            from cubeplex.llm.factory import LLMFactory

            # Resolve the provider with ORG context (session + org_id +
            # encryption_backend) exactly like the live run path
            # (run_manager.py:796) — a bare LLMFactory() would ignore per-org
            # provider config and encrypted credentials.
            async with async_session_maker() as _llm_session:
                factory = LLMFactory(
                    session=_llm_session,
                    org_id=ctx.org_id,
                    encryption_backend=self._app.state.encryption_backend,
                )
                provider_name, model_id, provider_config = (
                    await factory.resolve_default_provider_and_config()
                )
                await _llm_session.commit()

            from cubepi import Model

            provider = factory.build_cubepi_provider(provider_config, cache_policy=None)
            from cubeplex.llm.oneshot import OneShotLLM

            one_shot = OneShotLLM(provider, Model(id=model_id, provider=provider_name))

            task = asyncio.create_task(
                mc.run_consolidation(
                    redis=self._redis,
                    prefix=self._key_prefix,
                    conversation_id=conversation_id,
                    user_id=ctx.user_id,
                    org_id=ctx.org_id,
                    workspace_id=ctx.workspace_id,
                    one_shot=one_shot,
                    session_maker=async_session_maker,
                    min_hours=min_hours,
                    min_runs=min_runs,
                ),
                name=f"memcons:{conversation_id}",
            )
            self._consolidation_tasks.add(task)
            task.add_done_callback(self._consolidation_tasks.discard)
        except Exception:
            logger.warning("memory consolidation gate failed", exc_info=True)
```

- [ ] **Step 3: Call it at the end of a successful run**

In `_execute_run`, after the DoneEvent + `update_run_meta(status="completed")` block (the success path, before the `except`/`finally`), add:

```python
            await self._maybe_consolidate_memory(conversation_id=conversation_id, ctx=ctx)
```

- [ ] **Step 4: Drain consolidation tasks on shutdown**

Cancel in-flight consolidation tasks at the **very start of `drain`**, BEFORE its
`if self._tasks_empty.is_set(): return` early-return — otherwise, when no run
tasks remain (the common case at shutdown), `drain` returns immediately and the
consolidation cancellation is skipped:

```python
    async def drain(self, timeout_seconds: float) -> None:
        # Best-effort: stop background consolidation first, regardless of whether
        # any run tasks remain (drain returns early below when _tasks is empty).
        for t in list(self._consolidation_tasks):
            t.cancel()
        for t in list(self._consolidation_tasks):
            with suppress(asyncio.CancelledError):
                await t
        # ... existing drain body (the _tasks_empty early-return + run-task wait) ...
```

- [ ] **Step 5: Typecheck + the gate's own tests still pass**

Run: `cd backend && uv run mypy cubeplex/streams/run_manager.py && uv run pytest tests/unit/test_memory_consolidation_gate.py -q`
Expected: mypy Success; gate tests green. (No new unit test here — wiring is covered by the E2E in Task 6; `_maybe_consolidate_memory` is best-effort glue.)

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/streams/run_manager.py
git commit -m "feat(runs): post-run memory-consolidation gate + tracked background task"
```

---

## Task 6: Verification sweep + E2E

**Files:** Create `backend/tests/e2e/test_auto_memory.py`.

- [ ] **Step 1: Worktree E2E config + test DB** (if not already): copy `.env` +
`config.development.local.yaml` from main; migrate the worktree test DB:
`CUBEPLEX_DATABASE__NAME=cubeplex_test_feat_auto_memory ENV_FOR_DYNACONF=test uv run alembic upgrade head`.

- [ ] **Step 2: E2E — Layer 2 produces personal memory**

Create `backend/tests/e2e/test_auto_memory.py`:

```python
"""E2E: forcing a consolidation pass distills history into personal memory."""

import pytest

pytestmark = pytest.mark.real_llm


@pytest.mark.asyncio
async def test_consolidation_extracts_personal_memory(member_client) -> None:
    client, ws_id = member_client
    # Create a conversation and state a durable preference across a couple turns.
    resp = await client.post(f"/api/v1/ws/{ws_id}/conversations", params={"title": "automem"})
    resp.raise_for_status()
    conv_id = resp.json()["id"]

    from tests.e2e.conftest import collect_sse_events

    await collect_sse_events(
        client,
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        {"content": "For future reference: I always want responses in metric units and British spelling."},
    )

    # Force a consolidation pass directly (bypass the time/run gate).
    from cubeplex.config import config
    from cubeplex.db.engine import async_session_maker
    from cubeplex.cache import get_redis_client  # or however the app exposes redis
    from cubeplex.llm.factory import LLMFactory
    from cubeplex.llm.oneshot import OneShotLLM
    from cubeplex.services import memory_consolidation as mc
    from cubepi import Model

    # (Resolve prefix/user/org/ws the same way the run did; in the test, read them
    # from the member_client fixture context. Pseudocode — adapt to the fixture's
    # exposed ids.)
    # ... build one_shot + call mc.run_consolidation(force by min_hours=0,min_runs=0) ...

    # Then assert a personal memory item now exists mentioning the preference.
```

NOTE: this E2E's exact fixture wiring (how to get `prefix`, `user_id`, `org_id`,
`workspace_id`, and a redis handle in-test) must match `tests/e2e/conftest.py`.
Implement by reading that conftest; assert via the memory list endpoint or
`MemoryRepository` that a `personal` item referencing "metric"/"British" exists
with `source_type == consolidation`. If the fixture can't expose the ids cleanly,
assert through the public memory API instead.

- [ ] **Step 3: Run changed-module + E2E**

Run: `cd backend && uv run pytest tests/unit/test_memory_authoring_prompt.py tests/unit/test_memory_consolidation_gate.py tests/unit/test_memory_consolidation_pass.py -q`
Then (real LLM): `uv run pytest tests/e2e/test_auto_memory.py -q`

- [ ] **Step 4: Full sweep**

Run: `cd /home/chris/cubeplex/.worktrees/feat/auto-memory && make check-ci`

- [ ] **Step 5:** `/finishing-a-development-branch` → PR → `/pr-codex-review-loop`.

---

## Self-Review notes

- **Spec coverage:** Layer 1 (always-inject authoring + per-type triggers + proactive-personal-only) → Task 1. CONSOLIDATION source → Task 2. Gate/lock/HWM → Task 3. Op schema/validate/cap + scope-hardcoded-PERSONAL + source attribution + window cap → Task 4. Post-run trigger + tracked/drained task → Task 5. Tests/E2E → Tasks 1/3/4/6.
- **Per-conversation pivot** (spec Blocker 1): Tasks 3-5 key all gate state on `conversation_id` and load `cp.load(conversation_id)`.
- **Atomic HWM** (spec Blocker 2): Task 3 `mark_consolidated` uses `DECRBY consumed` (not reset); Task 4 captures `cutoff`/`consumed` before the pass; failure path leaves `last` + counter so it retries.
- **Personal-only enforcement** (spec Blocker 3): Task 1 prompt restricts proactive to personal; Task 4 `apply_ops` hard-codes `MemoryScope.PERSONAL` on create AND guards merge/archive — it fetches the target via `repo.get` (which scopes by `_can_read`) and skips anything whose `scope != PERSONAL`, so a hallucinated id can't mutate a shared (workspace/org) item the user happens to have read access to.
- **Type consistency:** `note_run`/`should_consolidate`/`acquire_lock`/`release_lock`/`mark_consolidated`/`get_last`/`_counter`/`parse_ops`/`apply_ops`/`run_consolidation` names are identical across Tasks 3-5 and the tests.
- **Open question (window cap):** fixed at `HISTORY_MSG_CAP = 40` newest messages (Task 4); thresholds `min_hours=6`/`min_runs=5` config-overridable (Task 5).
- **E2E caveat:** Task 6 Step 2 is intentionally adapt-to-conftest (the fixture's id exposure isn't known here) — the implementer must wire it against the real `tests/e2e/conftest.py`, asserting through the memory API if needed.
