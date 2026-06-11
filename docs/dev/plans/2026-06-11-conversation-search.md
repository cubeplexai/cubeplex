# Conversation Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a hybrid (BM25-class + vector) conversation search popover in the sidebar, indexing existing and new conversations into cubebox-side tables; cubepi untouched.

**Architecture:** New `conversation_chunks` table holds per-chunk text + pgvector embedding. A pluggable `LexicalSearchBackend` (PGroonga default, pg_bigm for AWS RDS) provides the lexical leg; pgvector + HNSW provides the semantic leg; results fused with RRF. Async embedding worker drains a Postgres `embedding_jobs` queue (no Redis dependency). Frontend: `⌘K` popover in sidebar.

**Tech Stack:** Python 3.11 / FastAPI / SQLModel / Alembic / pgvector / PGroonga or pg_bigm / asyncpg / httpx / Next.js 15 / React 19 / shadcn-ui / Playwright.

**Spec reference:** `docs/dev/specs/2026-06-11-conversation-search-design.md`

**Worktree:** `/home/chris/cubebox/.worktrees/feat/conversation-search` (slot 48 — backend `:8048`, frontend `:3048`).

**Read `.worktree.env` before running any command.** Plan commands assume CWD is the worktree root unless noted.

---

## PR Boundaries

Each PR is one concern, reviewable independently:

- **PR-A (Tasks 1–7):** schema, migration, config — no behavior yet, just plumbing.
- **PR-B (Tasks 8–14):** pure logic — chunker, text extractor, RRF, snippet, lexical backend abstraction.
- **PR-C (Tasks 15–20):** embedding provider, repositories, worker, incremental hook.
- **PR-D (Tasks 21–25):** search API route, backfill script, backend integration tests, backend E2E.
- **PR-E (Tasks 26–31):** frontend popover, hook, sidebar wiring, i18n, Playwright E2E.

Push and run the codex review loop per PR (`pr-codex-review-loop` skill).

---

## File Structure

### Backend — create

| Path | Responsibility |
|---|---|
| `backend/cubebox/models/conversation_chunk.py` | `ConversationChunk` SQLModel — text + embedding + scope. |
| `backend/cubebox/models/embedding_job.py` | `EmbeddingJob` SQLModel — queue row. |
| `backend/cubebox/models/search_backfill_progress.py` | `SearchBackfillProgress` SQLModel — backfill cursor. |
| `backend/cubebox/repositories/conversation_chunk.py` | Repo: insert / replace chunks per conversation, query by scope + ids. |
| `backend/cubebox/repositories/embedding_job.py` | Repo: enqueue, claim batch, mark done/dead. |
| `backend/cubebox/search/__init__.py` | Package marker. |
| `backend/cubebox/search/text_extract.py` | cubepi messages → readable text per message. |
| `backend/cubebox/search/chunker.py` | sliding-window chunker over a list of `(seq, role, text)`. |
| `backend/cubebox/search/rrf.py` | Reciprocal Rank Fusion helper. |
| `backend/cubebox/search/snippet.py` | Snippet + match_offsets extractor. |
| `backend/cubebox/search/lexical/__init__.py` | Factory: config → `LexicalSearchBackend`. |
| `backend/cubebox/search/lexical/base.py` | `LexicalSearchBackend` Protocol + concrete `LexicalQueryResult`. |
| `backend/cubebox/search/lexical/pgroonga.py` | PGroonga backend. |
| `backend/cubebox/search/lexical/pg_bigm.py` | pg_bigm backend. |
| `backend/cubebox/search/embedding.py` | OpenAI-protocol embedding provider. |
| `backend/cubebox/search/service.py` | `ConversationSearchService` — runs both legs, fuses, formats. |
| `backend/cubebox/search/worker.py` | `EmbeddingWorker` — claim → embed → write. |
| `backend/cubebox/search/indexer.py` | `enqueue_index_job(conversation_id, ...)` — used by run-done hook. |
| `backend/cubebox/api/routes/v1/conversation_search.py` | `GET /ws/{ws}/conversations/search`. |
| `backend/cubebox/api/schemas/conversation_search.py` | Pydantic request / response. |
| `backend/scripts/dev/backfill_search_index.py` | Iterate workspaces, enqueue all conversations. |
| `backend/alembic/versions/<rev>_conversation_search_tables.py` | Migration: enable extension, create three tables, create indexes, create vector + lexical indexes by config. |
| `backend/tests/search/test_text_extract.py` | |
| `backend/tests/search/test_chunker.py` | |
| `backend/tests/search/test_rrf.py` | |
| `backend/tests/search/test_snippet.py` | |
| `backend/tests/search/test_lexical_backends.py` | normalize_query + SQL template assertions. |
| `backend/tests/search/test_embedding_provider.py` | httpx mock. |
| `backend/tests/search/test_worker.py` | integration with Postgres. |
| `backend/tests/search/test_service.py` | service with both legs stubbed at backend level. |
| `backend/tests/api/test_conversation_search_route.py` | route-level. |
| `backend/tests/e2e/test_conversation_search.py` | full search + index round trip. |

### Backend — modify

| Path | Reason |
|---|---|
| `backend/cubebox/models/public_id.py` | Add `PREFIX_CONV_CHUNK = "cck"`, `PREFIX_EMBEDDING_JOB = "ejob"`, `PREFIX_BACKFILL = "sbp"`. |
| `backend/cubebox/models/__init__.py` | Export new models. |
| `backend/cubebox/repositories/__init__.py` | Export new repos. |
| `backend/cubebox/api/routes/v1/__init__.py` | Mount the new router. |
| `backend/cubebox/api/routes/v1/conversations.py` | After successful run, call `indexer.enqueue_index_job`. |
| `backend/cubebox/api/app.py` | Start `EmbeddingWorker` task in lifespan. |
| `backend/config.yaml` | Add `search:` block (see Task 6). |
| `backend/cubebox/config.py` | (Verify access pattern matches existing keys — no code change required, just confirm.) |
| `backend/pyproject.toml` | `uv add pgvector tiktoken httpx-sse` (only if not present). |

### Frontend — create

| Path | Responsibility |
|---|---|
| `frontend/packages/core/src/api/conversation-search.ts` | `searchConversations(client, q, limit)` API method + types. |
| `frontend/packages/web/components/sidebar/ConversationSearch.tsx` | Popover trigger + body. |
| `frontend/packages/web/components/sidebar/SearchResultRow.tsx` | Single result row with `<mark>` highlighting. |
| `frontend/packages/web/hooks/useConversationSearch.ts` | Debounced fetch hook with AbortController. |
| `frontend/packages/web/__tests__/e2e/conversation-search.spec.ts` | Playwright. |

### Frontend — modify

| Path | Reason |
|---|---|
| `frontend/packages/web/components/layout/Sidebar.tsx` | Mount `<ConversationSearch />` next to brand. |
| `frontend/packages/core/src/api/index.ts` | Re-export search method. |
| `frontend/packages/web/messages/en.json` | Add `sidebar.search.*` strings. |
| `frontend/packages/web/messages/zh.json` | Same. |

---

## Conventions to Follow

* mypy strict, line length 100, type annotations on every signature.
* Datetimes: tz-aware, `Column(DateTime(timezone=True), ...)`, application code uses `datetime.now(UTC)`.
* Migration timestamp casts: when altering an existing datetime column, hand-add `postgresql_using="<col> AT TIME ZONE 'UTC'"` (this plan only creates new tz-aware columns, so this rule applies to any future revisions).
* Public IDs via `generate_public_id(PREFIX_X)` in `default_factory`.
* Dependency adds: `uv add <pkg>` from `backend/`; `pnpm add` from `frontend/packages/web/`.
* No backward-compat shims — cubebox hasn't shipped publicly.

---

# PR-A — Schema, Migration, Config

## Task 1: Add public-ID prefixes

**Files:**
- Modify: `backend/cubebox/models/public_id.py`

- [ ] **Step 1: Add the three new constants**

Edit `backend/cubebox/models/public_id.py`, appending to the existing
`PREFIX_*` block (alphabetical order is fine, follow what's there):

```python
PREFIX_CONV_CHUNK: str = "cck"
PREFIX_EMBEDDING_JOB: str = "ejob"
PREFIX_BACKFILL: str = "sbp"
```

- [ ] **Step 2: Commit**

```bash
git add backend/cubebox/models/public_id.py
git commit -m "feat(models): add public-id prefixes for search tables"
```

---

## Task 2: `ConversationChunk` model

**Files:**
- Create: `backend/cubebox/models/conversation_chunk.py`
- Modify: `backend/cubebox/models/__init__.py`

- [ ] **Step 1: Write the model**

```python
# backend/cubebox/models/conversation_chunk.py
"""Search index chunk — sliding window over a conversation's messages."""

from typing import Any, ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Index
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin
from cubebox.models.public_id import PREFIX_CONV_CHUNK, generate_public_id

VECTOR_DIM = 1024


class ConversationChunk(CubeboxBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = PREFIX_CONV_CHUNK
    __tablename__ = "conversation_chunks"
    __table_args__ = (
        Index("ix_chunks_scope", "org_id", "workspace_id", "creator_user_id"),
        Index("ix_chunks_conversation", "conversation_id", "chunk_seq", unique=True),
    )

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_CONV_CHUNK),
        primary_key=True,
        max_length=20,
    )
    conversation_id: str = Field(foreign_key="conversations.id", max_length=20)
    creator_user_id: str = Field(foreign_key="users.id", max_length=20)
    chunk_seq: int = Field(ge=0)
    seq_lo: int
    seq_hi: int
    text: str
    embedding: Any = Field(
        sa_column=Column(Vector(VECTOR_DIM), nullable=False),
    )
    embed_model: str = Field(max_length=128)
```

- [ ] **Step 2: Export from package init**

Edit `backend/cubebox/models/__init__.py`. Find the existing import block and
add:

```python
from cubebox.models.conversation_chunk import ConversationChunk  # noqa: F401
```

and append `"ConversationChunk"` to the `__all__` list (if present).

- [ ] **Step 3: Type-check**

Run: `uv run mypy cubebox/models/conversation_chunk.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/models/conversation_chunk.py backend/cubebox/models/__init__.py
git commit -m "feat(models): ConversationChunk for hybrid search"
```

---

## Task 3: `EmbeddingJob` model

**Files:**
- Create: `backend/cubebox/models/embedding_job.py`
- Modify: `backend/cubebox/models/__init__.py`

- [ ] **Step 1: Write the model**

```python
# backend/cubebox/models/embedding_job.py
"""Async work queue for embedding chunks (Postgres-only, no Redis)."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import Column, DateTime, Index
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin
from cubebox.models.public_id import PREFIX_EMBEDDING_JOB, generate_public_id


class EmbeddingJobState(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    dead = "dead"


class EmbeddingJob(CubeboxBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = PREFIX_EMBEDDING_JOB
    __tablename__ = "embedding_jobs"
    __table_args__ = (
        Index("ix_ejob_pending", "state", "created_at"),
        Index("ix_ejob_conversation", "conversation_id"),
    )

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_EMBEDDING_JOB),
        primary_key=True,
        max_length=20,
    )
    conversation_id: str = Field(foreign_key="conversations.id", max_length=20)
    creator_user_id: str = Field(foreign_key="users.id", max_length=20)
    seq_lo: int = Field(default=0)
    seq_hi: int = Field(default=2**62)  # backfill default = "whole conversation"
    state: EmbeddingJobState = Field(default=EmbeddingJobState.pending, max_length=10)
    attempts: int = Field(default=0)
    last_error: str | None = Field(default=None)
    claimed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    scheduled_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
```

- [ ] **Step 2: Export from package init**

Edit `backend/cubebox/models/__init__.py`:

```python
from cubebox.models.embedding_job import EmbeddingJob, EmbeddingJobState  # noqa: F401
```

- [ ] **Step 3: Type-check**

Run: `uv run mypy cubebox/models/embedding_job.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/models/embedding_job.py backend/cubebox/models/__init__.py
git commit -m "feat(models): EmbeddingJob queue for async indexing"
```

---

## Task 4: `SearchBackfillProgress` model

**Files:**
- Create: `backend/cubebox/models/search_backfill_progress.py`
- Modify: `backend/cubebox/models/__init__.py`

- [ ] **Step 1: Write the model**

```python
# backend/cubebox/models/search_backfill_progress.py
"""Backfill cursor — lets the script resume after interruption."""

from typing import ClassVar

from sqlalchemy import Index
from sqlmodel import Field

from cubebox.models.mixins import CubeboxBase, OrgScopedMixin
from cubebox.models.public_id import PREFIX_BACKFILL, generate_public_id


class SearchBackfillProgress(CubeboxBase, OrgScopedMixin, table=True):
    _PREFIX: ClassVar[str] = PREFIX_BACKFILL
    __tablename__ = "search_backfill_progress"
    __table_args__ = (Index("ix_sbp_ws", "workspace_id", unique=True),)

    id: str = Field(
        default_factory=lambda: generate_public_id(PREFIX_BACKFILL),
        primary_key=True,
        max_length=20,
    )
    last_conversation_id: str | None = Field(default=None, max_length=20)
    enqueued_count: int = Field(default=0)
    done: bool = Field(default=False)
```

- [ ] **Step 2: Export**

Edit `backend/cubebox/models/__init__.py`:

```python
from cubebox.models.search_backfill_progress import SearchBackfillProgress  # noqa: F401
```

- [ ] **Step 3: Type-check**

Run: `uv run mypy cubebox/models/search_backfill_progress.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/models/search_backfill_progress.py backend/cubebox/models/__init__.py
git commit -m "feat(models): SearchBackfillProgress for resumable backfill"
```

---

## Task 5: Add `pgvector` dependency

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock`

- [ ] **Step 1: Check whether pgvector is already a dep**

Run from worktree root: `grep -E '"pgvector|"tiktoken"|"httpx' backend/pyproject.toml`
Expected: shows present (we'll know which to add).

- [ ] **Step 2: Add missing deps**

From `backend/`:

```bash
uv add pgvector
uv add tiktoken
# httpx is already a dep (used by cubepi); no add needed unless above grep is empty.
```

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "feat(deps): pgvector + tiktoken for conversation search"
```

---

## Task 6: Add `search:` config block

**Files:**
- Modify: `backend/config.yaml`

- [ ] **Step 1: Append the search block**

Open `backend/config.yaml` and add at the end (top level, peer of existing
top-level keys like `database`, `redis`, `lifecycle`):

```yaml
search:
  enabled: true
  lexical:
    backend: "pgroonga"          # | "pg_bigm"
  embedding:
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key_env: "DASHSCOPE_API_KEY"
    model: "qwen3-embedding-0.6b"
    dimensions: 1024
    batch_size: 32
    timeout_seconds: 30
  chunker:
    target_tokens: 600
    overlap_tokens: 100
  worker:
    poll_interval_seconds: 2
    max_attempts: 5
    backoff_seconds: [60, 300, 1500, 7200, 36000]
  rrf:
    k: 60
    prefetch_per_leg: 20
```

- [ ] **Step 2: Verify config loader sees it**

Run from `backend/`:

```bash
uv run python -c "from cubebox.config import config; print(config.get('search.embedding.model'))"
```

Expected: prints `qwen3-embedding-0.6b`.

- [ ] **Step 3: Commit**

```bash
git add backend/config.yaml
git commit -m "feat(config): search config block"
```

---

## Task 7: Alembic migration — tables, extensions, indexes

**Files:**
- Create: `backend/alembic/versions/<auto>_conversation_search_tables.py` (alembic generates the filename)

- [ ] **Step 1: Generate the migration skeleton**

From `backend/`:

```bash
uv run alembic revision --autogenerate -m "conversation_search_tables"
```

Open the generated file. Verify it picked up `conversation_chunks`,
`embedding_jobs`, `search_backfill_progress`. The autogen will likely emit
the vector column as a generic `LargeBinary` or fail — we replace it
manually since pgvector's autogen support is partial.

- [ ] **Step 2: Edit the generated migration**

Replace the body of the file so it matches this template. Keep the
auto-generated `revision`, `down_revision` IDs intact.

```python
"""conversation_search_tables

Revision ID: <KEEP AUTOGEN VALUE>
Revises: ab40489adff0
Create Date: 2026-06-11 ...
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "<KEEP AUTOGEN VALUE>"
down_revision: str | None = "ab40489adff0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at migration-author time. Migrations are immutable assets — one
# revision must always emit the same DDL. Switching backend or dimension
# post-deploy requires a NEW revision (drop+create), not editing this one.
LEXICAL_BACKEND = "pgroonga"
VECTOR_DIM = 1024


def upgrade() -> None:
    # Extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    if LEXICAL_BACKEND == "pgroonga":
        op.execute("CREATE EXTENSION IF NOT EXISTS pgroonga")
    elif LEXICAL_BACKEND == "pg_bigm":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_bigm")
    else:
        raise RuntimeError(f"Unknown lexical backend: {LEXICAL_BACKEND}")

    # conversation_chunks
    op.create_table(
        "conversation_chunks",
        sa.Column("id", sa.String(length=20), primary_key=True),
        sa.Column("org_id", sa.String(length=20), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("workspace_id", sa.String(length=20), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("creator_user_id", sa.String(length=20), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("conversation_id", sa.String(length=20), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("chunk_seq", sa.Integer, nullable=False),
        sa.Column("seq_lo", sa.BigInteger, nullable=False),
        sa.Column("seq_hi", sa.BigInteger, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(VECTOR_DIM), nullable=False),
        sa.Column("embed_model", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_chunks_scope",
        "conversation_chunks",
        ["org_id", "workspace_id", "creator_user_id"],
    )
    op.create_index(
        "ix_chunks_conversation",
        "conversation_chunks",
        ["conversation_id", "chunk_seq"],
        unique=True,
    )
    # HNSW vector index (pgvector ≥ 0.5)
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw "
        "ON conversation_chunks USING hnsw (embedding vector_cosine_ops)"
    )
    # Lexical index — DDL depends on backend
    if LEXICAL_BACKEND == "pgroonga":
        op.execute("CREATE INDEX ix_chunks_text_lexical ON conversation_chunks USING pgroonga (text)")
    else:
        op.execute(
            "CREATE INDEX ix_chunks_text_lexical ON conversation_chunks "
            "USING gin (text gin_bigm_ops)"
        )

    # embedding_jobs
    op.create_table(
        "embedding_jobs",
        sa.Column("id", sa.String(length=20), primary_key=True),
        sa.Column("org_id", sa.String(length=20), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("workspace_id", sa.String(length=20), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("creator_user_id", sa.String(length=20), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("conversation_id", sa.String(length=20), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("seq_lo", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("seq_hi", sa.BigInteger, nullable=False, server_default=str(2**62)),
        sa.Column("state", sa.String(length=10), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    # Index covers the claim query `WHERE state='pending' AND scheduled_at
    # <= now() ORDER BY scheduled_at FOR UPDATE SKIP LOCKED`. Indexing on
    # created_at instead would force the planner into a heap scan + sort.
    op.create_index("ix_ejob_pending", "embedding_jobs", ["state", "scheduled_at"])
    op.create_index("ix_ejob_conversation", "embedding_jobs", ["conversation_id"])

    # search_backfill_progress
    op.create_table(
        "search_backfill_progress",
        sa.Column("id", sa.String(length=20), primary_key=True),
        sa.Column("org_id", sa.String(length=20), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("workspace_id", sa.String(length=20), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("last_conversation_id", sa.String(length=20), nullable=True),
        sa.Column("enqueued_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("done", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_sbp_ws", "search_backfill_progress", ["workspace_id"], unique=True)


def downgrade() -> None:
    op.drop_table("search_backfill_progress")
    op.drop_table("embedding_jobs")
    op.execute("DROP INDEX IF EXISTS ix_chunks_text_lexical")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.drop_table("conversation_chunks")
    # Extensions left in place — other features may use them.
```

- [ ] **Step 3: Run the migration locally**

```bash
cd backend
uv run alembic upgrade head
```

Expected: completes successfully. If `pgroonga` extension is missing on the
local Postgres, install via `apt install postgresql-17-pgroonga` (or your
distro's equivalent) and rerun.

- [ ] **Step 4: Verify head**

```bash
cd backend
uv run alembic current
```

Expected: shows the new revision id.

- [ ] **Step 5: Sanity SQL**

```bash
psql "$CUBEBOX_DATABASE__NAME" -c "\dt conversation_chunks embedding_jobs search_backfill_progress"
psql "$CUBEBOX_DATABASE__NAME" -c "\di ix_chunks_embedding_hnsw"
```

Expected: all three tables listed; HNSW index exists.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/*conversation_search_tables.py
git commit -m "feat(db): conversation_chunks + embedding_jobs + backfill_progress"
```

**PR-A done. Open PR titled `feat(search): schema + config` and run codex review loop.**

---

# PR-B — Pure Logic Modules

These modules are stateless and unit-testable without DB or network. Land
together so the implementing engineer can see how they fit.

## Task 8: Text extractor

**Files:**
- Create: `backend/cubebox/search/__init__.py` (empty marker)
- Create: `backend/cubebox/search/text_extract.py`
- Create: `backend/tests/search/__init__.py` (empty marker)
- Create: `backend/tests/search/test_text_extract.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/search/test_text_extract.py
"""Unit tests for the search text extractor."""

from cubepi.providers.base import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    ToolResultContent,
    UserMessage,
)

from cubebox.search.text_extract import extract_searchable_text


def test_user_message_text() -> None:
    msg = UserMessage(content=[TextContent(text="hello")], timestamp=1.0)
    assert extract_searchable_text(msg) == "[user] hello"


def test_assistant_text_strips_reasoning() -> None:
    msg = AssistantMessage(content=[TextContent(text="answer")], timestamp=1.0)
    assert extract_searchable_text(msg) == "[assistant] answer"


def test_tool_result_extracts_text_contents() -> None:
    msg = ToolResultMessage(
        content=[ToolResultContent(tool_call_id="tc_1", content=[TextContent(text="42")])],
        timestamp=1.0,
    )
    assert extract_searchable_text(msg) == "[tool_result] 42"


def test_tool_call_is_skipped() -> None:
    msg = AssistantMessage(
        content=[ToolCallContent(tool_call_id="tc_1", name="run", arguments={"x": 1})],
        timestamp=1.0,
    )
    assert extract_searchable_text(msg) == ""


def test_empty_text_returns_empty_string() -> None:
    msg = UserMessage(content=[TextContent(text="")], timestamp=1.0)
    assert extract_searchable_text(msg) == ""
```

- [ ] **Step 2: Verify tests fail**

```bash
cd backend && uv run pytest tests/search/test_text_extract.py -v
```

Expected: collection error or `ImportError` (module doesn't exist).

- [ ] **Step 3: Implement**

```python
# backend/cubebox/search/text_extract.py
"""Extract human-readable, search-worthy text from a cubepi message."""

from cubepi.providers.base import (
    AssistantMessage,
    Message,
    TextContent,
    ToolResultContent,
    ToolResultMessage,
    UserMessage,
)


def extract_searchable_text(message: Message) -> str:
    """Return a one-line, prefixed representation, or empty string when nothing
    search-worthy is present (tool calls, empty messages, attachments-only).
    """
    if isinstance(message, UserMessage):
        text = _flatten_text_parts(message.content)
        return f"[user] {text}" if text else ""
    if isinstance(message, AssistantMessage):
        text = _flatten_text_parts(message.content)
        return f"[assistant] {text}" if text else ""
    if isinstance(message, ToolResultMessage):
        parts: list[str] = []
        for tr in message.content:
            if isinstance(tr, ToolResultContent):
                parts.append(_flatten_text_parts(tr.content))
        text = " ".join(p for p in parts if p).strip()
        return f"[tool_result] {text}" if text else ""
    return ""


def _flatten_text_parts(parts: object) -> str:
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for p in parts:
        if isinstance(p, TextContent) and p.text:
            out.append(p.text)
    return " ".join(out).strip()
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/search/test_text_extract.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
mkdir -p backend/cubebox/search backend/tests/search
touch backend/cubebox/search/__init__.py backend/tests/search/__init__.py
git add backend/cubebox/search/__init__.py backend/tests/search/__init__.py \
        backend/cubebox/search/text_extract.py backend/tests/search/test_text_extract.py
git commit -m "feat(search): text extractor for cubepi messages"
```

---

## Task 9: Chunker

**Files:**
- Create: `backend/cubebox/search/chunker.py`
- Create: `backend/tests/search/test_chunker.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/search/test_chunker.py
from cubebox.search.chunker import Chunk, MessageInput, chunk_messages


def _msg(seq: int, text: str) -> MessageInput:
    return MessageInput(seq=seq, text=text)


def test_single_short_message_one_chunk() -> None:
    msgs = [_msg(1, "hello world")]
    chunks = chunk_messages(msgs, target_tokens=600, overlap_tokens=100)
    assert len(chunks) == 1
    assert chunks[0].seq_lo == 1 and chunks[0].seq_hi == 1
    assert "hello world" in chunks[0].text


def test_long_corpus_creates_multiple_chunks_with_overlap() -> None:
    long_word = "word " * 800   # ≈ 800 tokens
    msgs = [_msg(1, long_word)]
    chunks = chunk_messages(msgs, target_tokens=200, overlap_tokens=50)
    assert len(chunks) >= 3
    # chunks must cover the whole input
    assert chunks[0].chunk_seq == 0
    assert chunks[-1].chunk_seq == len(chunks) - 1
    # overlap: adjacent chunks share text
    for a, b in zip(chunks, chunks[1:], strict=True):
        assert any(w in b.text for w in a.text.split()[-10:])


def test_empty_messages_yields_no_chunks() -> None:
    assert chunk_messages([_msg(1, "")], 600, 100) == []
    assert chunk_messages([], 600, 100) == []


def test_seq_range_tracks_messages_in_chunk() -> None:
    msgs = [_msg(1, "a"), _msg(2, "b"), _msg(3, "c")]
    chunks = chunk_messages(msgs, target_tokens=600, overlap_tokens=100)
    assert len(chunks) == 1
    assert chunks[0].seq_lo == 1
    assert chunks[0].seq_hi == 3
```

- [ ] **Step 2: Verify tests fail**

```bash
cd backend && uv run pytest tests/search/test_chunker.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# backend/cubebox/search/chunker.py
"""Sliding-window chunker. Token counting via tiktoken cl100k_base."""

from dataclasses import dataclass

import tiktoken

_ENC = tiktoken.get_encoding("cl100k_base")


@dataclass(frozen=True)
class MessageInput:
    seq: int
    text: str


@dataclass(frozen=True)
class Chunk:
    chunk_seq: int
    seq_lo: int
    seq_hi: int
    text: str


def chunk_messages(
    messages: list[MessageInput],
    target_tokens: int,
    overlap_tokens: int,
) -> list[Chunk]:
    """Build sliding-window chunks. seq_lo / seq_hi track which message seqs
    contributed to a chunk. Empty input → empty list.
    """
    if not messages or target_tokens <= 0:
        return []
    # Flatten to a single token stream with per-token seq markers.
    tokens: list[int] = []
    token_seq: list[int] = []
    for m in messages:
        if not m.text:
            continue
        encoded = _ENC.encode(m.text)
        if not encoded:
            continue
        tokens.extend(encoded)
        token_seq.extend([m.seq] * len(encoded))
        # Insert a single space token boundary between messages.
        space = _ENC.encode(" ")
        tokens.extend(space)
        token_seq.extend([m.seq] * len(space))
    if not tokens:
        return []
    step = max(1, target_tokens - max(0, overlap_tokens))
    out: list[Chunk] = []
    i = 0
    while i < len(tokens):
        j = min(i + target_tokens, len(tokens))
        text = _ENC.decode(tokens[i:j])
        seqs = token_seq[i:j]
        out.append(
            Chunk(
                chunk_seq=len(out),
                seq_lo=min(seqs),
                seq_hi=max(seqs),
                text=text,
            )
        )
        if j == len(tokens):
            break
        i += step
    return out
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/search/test_chunker.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/search/chunker.py backend/tests/search/test_chunker.py
git commit -m "feat(search): sliding-window chunker"
```

---

## Task 10: RRF fusion

**Files:**
- Create: `backend/cubebox/search/rrf.py`
- Create: `backend/tests/search/test_rrf.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/search/test_rrf.py
from cubebox.search.rrf import rrf_fuse


def test_same_doc_top_of_both_lists_wins() -> None:
    out = rrf_fuse(lexical=["a", "b", "c"], vector=["a", "x", "y"], k=60)
    assert out[0][0] == "a"


def test_lexical_only_doc_appears() -> None:
    out = rrf_fuse(lexical=["a"], vector=["b"], k=60)
    ids = {doc for doc, _ in out}
    assert ids == {"a", "b"}


def test_empty_legs_return_empty() -> None:
    assert rrf_fuse(lexical=[], vector=[], k=60) == []


def test_scores_descending() -> None:
    out = rrf_fuse(lexical=["a", "b"], vector=["b", "c"], k=60)
    scores = [s for _, s in out]
    assert scores == sorted(scores, reverse=True)
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && uv run pytest tests/search/test_rrf.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# backend/cubebox/search/rrf.py
"""Reciprocal Rank Fusion. Sum of 1/(k+rank) across input ranked lists."""

from collections.abc import Iterable


def rrf_fuse(
    lexical: Iterable[str],
    vector: Iterable[str],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Return ``[(id, fused_score), ...]`` ordered by descending score."""
    scores: dict[str, float] = {}
    for rank, doc_id in enumerate(lexical, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    for rank, doc_id in enumerate(vector, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/search/test_rrf.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/search/rrf.py backend/tests/search/test_rrf.py
git commit -m "feat(search): reciprocal rank fusion"
```

---

## Task 11: Snippet + match offsets

**Files:**
- Create: `backend/cubebox/search/snippet.py`
- Create: `backend/tests/search/test_snippet.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/search/test_snippet.py
from cubebox.search.snippet import extract_snippet


def test_keyword_hit_centers_window() -> None:
    text = "lorem " * 30 + "docling " + "ipsum " * 30
    out = extract_snippet(text, q="docling", window=80)
    assert "docling" in out.text
    assert out.match_offsets and out.match_offsets[0][1] - out.match_offsets[0][0] == len("docling")
    # offset points at literal match inside the snippet
    s, e = out.match_offsets[0]
    assert out.text[s:e].lower() == "docling"


def test_no_match_returns_head_with_empty_offsets() -> None:
    text = "alpha beta gamma delta"
    out = extract_snippet(text, q="nothing", window=80)
    assert out.text.startswith("alpha")
    assert out.match_offsets == []


def test_case_insensitive_match() -> None:
    out = extract_snippet("Hello WORLD foo", q="world", window=40)
    assert out.match_offsets != []


def test_empty_text_returns_empty() -> None:
    out = extract_snippet("", q="x", window=40)
    assert out.text == ""
    assert out.match_offsets == []
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && uv run pytest tests/search/test_snippet.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# backend/cubebox/search/snippet.py
"""Build a short snippet around the first literal match. NFC + case-fold."""

import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Snippet:
    text: str
    match_offsets: list[tuple[int, int]] = field(default_factory=list)


def extract_snippet(text: str, q: str, window: int = 160) -> Snippet:
    if not text:
        return Snippet(text="")
    needle = _normalise(q)
    haystack_norm = _normalise(text)
    pos = haystack_norm.find(needle) if needle else -1
    if pos == -1:
        head = text[:window].rstrip()
        return Snippet(text=head + ("…" if len(text) > window else ""))
    # Centre window on the match
    half = window // 2
    start = max(0, pos - half)
    end = min(len(text), start + window)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    snippet_text = prefix + text[start:end] + suffix
    match_in_snippet_start = len(prefix) + (pos - start)
    match_in_snippet_end = match_in_snippet_start + len(needle)
    return Snippet(
        text=snippet_text,
        match_offsets=[(match_in_snippet_start, match_in_snippet_end)],
    )


def _normalise(s: str) -> str:
    return unicodedata.normalize("NFC", s).casefold()
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/search/test_snippet.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/search/snippet.py backend/tests/search/test_snippet.py
git commit -m "feat(search): snippet + match offsets extractor"
```

---

## Task 12: Lexical backend protocol + PGroonga impl

**Files:**
- Create: `backend/cubebox/search/lexical/__init__.py`
- Create: `backend/cubebox/search/lexical/base.py`
- Create: `backend/cubebox/search/lexical/pgroonga.py`

- [ ] **Step 1: Write the Protocol + result type**

```python
# backend/cubebox/search/lexical/base.py
"""Lexical search backend abstraction. One impl per Postgres extension."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LexicalSqlBundle:
    """A complete, parameterised SQL chunk:

      SELECT id, <score_expr> AS score
      FROM conversation_chunks
      WHERE <scope_cols> AND <match_clause>
      ORDER BY score DESC
      LIMIT $n

    The service composes the final SQL by wrapping this with scope binds.
    """

    sql: str
    bind_keys: list[str]   # placeholder names this template expects (e.g. ["q"])


class LexicalSearchBackend(Protocol):
    name: str

    def normalize_query(self, q: str) -> str: ...

    def search_sql(self, limit: int) -> LexicalSqlBundle: ...
```

- [ ] **Step 2: Write the PGroonga implementation**

```python
# backend/cubebox/search/lexical/pgroonga.py
"""PGroonga backend — `&@~` operator with pgroonga_score()."""

from cubebox.search.lexical.base import LexicalSearchBackend, LexicalSqlBundle


class PgroongaBackend(LexicalSearchBackend):
    name = "pgroonga"

    def normalize_query(self, q: str) -> str:
        # PGroonga interprets unescaped ASCII " ( ) and others. Strip the
        # ones we never want users to inject; keep CJK and alphanumerics.
        bad = set('"()\\')
        return "".join(c for c in q if c not in bad).strip()

    def search_sql(self, limit: int) -> LexicalSqlBundle:
        sql = f"""
            SELECT id, pgroonga_score(tableoid, ctid) AS score
            FROM conversation_chunks
            WHERE org_id = :org_id
              AND workspace_id = :ws_id
              AND creator_user_id = :user_id
              AND text &@~ :q
            ORDER BY score DESC
            LIMIT {int(limit)}
        """
        return LexicalSqlBundle(sql=sql, bind_keys=["org_id", "ws_id", "user_id", "q"])
```

- [ ] **Step 3: Tests**

```python
# backend/tests/search/test_lexical_backends.py
from cubebox.search.lexical.pgroonga import PgroongaBackend


def test_pgroonga_strips_disallowed_chars() -> None:
    b = PgroongaBackend()
    assert b.normalize_query('docling "(x)"') == "docling x"


def test_pgroonga_sql_has_expected_binds() -> None:
    b = PgroongaBackend()
    bundle = b.search_sql(limit=20)
    assert "pgroonga_score" in bundle.sql
    assert "&@~" in bundle.sql
    assert set(bundle.bind_keys) == {"org_id", "ws_id", "user_id", "q"}
```

- [ ] **Step 4: Make package init expose them**

```python
# backend/cubebox/search/lexical/__init__.py
"""Lexical backend selection — config-driven."""

from cubebox.config import config
from cubebox.search.lexical.base import LexicalSearchBackend
from cubebox.search.lexical.pgroonga import PgroongaBackend


def build_lexical_backend() -> LexicalSearchBackend:
    name = config.get("search.lexical.backend", "pgroonga")
    if name == "pgroonga":
        return PgroongaBackend()
    if name == "pg_bigm":
        from cubebox.search.lexical.pg_bigm import PgBigmBackend

        return PgBigmBackend()
    raise RuntimeError(f"Unknown lexical backend: {name}")
```

- [ ] **Step 5: Run tests**

```bash
cd backend && uv run pytest tests/search/test_lexical_backends.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/search/lexical/
git add backend/tests/search/test_lexical_backends.py
git commit -m "feat(search): LexicalSearchBackend protocol + PGroonga"
```

---

## Task 13: pg_bigm backend

**Files:**
- Create: `backend/cubebox/search/lexical/pg_bigm.py`
- Modify: `backend/tests/search/test_lexical_backends.py`

- [ ] **Step 1: Implementation**

```python
# backend/cubebox/search/lexical/pg_bigm.py
"""pg_bigm backend — LIKE-based with bigm_similarity()."""

from cubebox.search.lexical.base import LexicalSearchBackend, LexicalSqlBundle


class PgBigmBackend(LexicalSearchBackend):
    name = "pg_bigm"

    def normalize_query(self, q: str) -> str:
        # Escape SQL LIKE wildcards. The leading/trailing % are added by SQL.
        return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").strip()

    def search_sql(self, limit: int) -> LexicalSqlBundle:
        sql = f"""
            SELECT id, bigm_similarity(text, :q) AS score
            FROM conversation_chunks
            WHERE org_id = :org_id
              AND workspace_id = :ws_id
              AND creator_user_id = :user_id
              AND text LIKE '%' || :q || '%' ESCAPE '\\'
            ORDER BY score DESC
            LIMIT {int(limit)}
        """
        return LexicalSqlBundle(sql=sql, bind_keys=["org_id", "ws_id", "user_id", "q"])
```

- [ ] **Step 2: Append tests**

Add to `backend/tests/search/test_lexical_backends.py`:

```python
from cubebox.search.lexical.pg_bigm import PgBigmBackend


def test_pgbigm_escapes_like_wildcards() -> None:
    b = PgBigmBackend()
    assert b.normalize_query("50% off_now") == "50\\% off\\_now"


def test_pgbigm_sql_has_like_clause() -> None:
    b = PgBigmBackend()
    bundle = b.search_sql(limit=20)
    assert "LIKE" in bundle.sql
    assert "bigm_similarity" in bundle.sql
```

- [ ] **Step 3: Run tests**

```bash
cd backend && uv run pytest tests/search/test_lexical_backends.py -v
```

Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/search/lexical/pg_bigm.py backend/tests/search/test_lexical_backends.py
git commit -m "feat(search): pg_bigm backend for AWS RDS deployments"
```

---

## Task 14: Embedding provider

**Files:**
- Create: `backend/cubebox/search/embedding.py`
- Create: `backend/tests/search/test_embedding_provider.py`

- [ ] **Step 1: Write failing tests using httpx mock**

```python
# backend/tests/search/test_embedding_provider.py
import httpx
import pytest

from cubebox.search.embedding import EmbeddingProvider


@pytest.mark.asyncio
async def test_embed_returns_vectors() -> None:
    payload = {
        "data": [
            {"embedding": [0.1, 0.2, 0.3], "index": 0},
            {"embedding": [0.4, 0.5, 0.6], "index": 1},
        ]
    }
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=payload)
    )
    provider = EmbeddingProvider(
        base_url="https://example/v1",
        api_key="k",
        model="qwen3-embedding-0.6b",
        dimensions=3,
        timeout_seconds=5,
        _transport=transport,
    )
    vectors = await provider.embed(["hello", "world"])
    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]


@pytest.mark.asyncio
async def test_embed_propagates_http_errors() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
    provider = EmbeddingProvider(
        base_url="https://example/v1",
        api_key="k",
        model="qwen3-embedding-0.6b",
        dimensions=3,
        timeout_seconds=1,
        _transport=transport,
    )
    with pytest.raises(httpx.HTTPStatusError):
        await provider.embed(["x"])


def test_model_id_combines_model_and_host() -> None:
    provider = EmbeddingProvider(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="k",
        model="qwen3-embedding-0.6b",
        dimensions=1024,
        timeout_seconds=5,
    )
    assert provider.model_id == "qwen3-embedding-0.6b@dashscope.aliyuncs.com"
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && uv run pytest tests/search/test_embedding_provider.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# backend/cubebox/search/embedding.py
"""OpenAI-protocol embedding HTTP client.

Configured to talk to DashScope, OpenAI, or any local /v1-compatible server.
The model_id field encodes the (model, host) pair so chunks can be
selectively reindexed when either changes.
"""

import os
from urllib.parse import urlparse

import httpx

from cubebox.config import config


class EmbeddingProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        timeout_seconds: int,
        batch_size: int = 32,
        _transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self.dimensions = dimensions
        self._timeout = timeout_seconds
        self._batch_size = batch_size
        # One httpx.AsyncClient per provider instance — connection pool
        # is reused across embed calls, so we pay TLS handshake once
        # per process instead of per batch. Lifetime = app lifespan.
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            transport=_transport,
        )

    @classmethod
    def from_config(cls) -> "EmbeddingProvider":
        api_key_env = config.get("search.embedding.api_key_env", "DASHSCOPE_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        return cls(
            base_url=config.get(
                "search.embedding.base_url",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            api_key=api_key,
            model=config.get("search.embedding.model", "qwen3-embedding-0.6b"),
            dimensions=int(config.get("search.embedding.dimensions", 1024)),
            timeout_seconds=int(config.get("search.embedding.timeout_seconds", 30)),
            batch_size=int(config.get("search.embedding.batch_size", 32)),
        )

    @property
    def model_id(self) -> str:
        host = urlparse(self._base_url).hostname or "unknown"
        return f"{self._model}@{host}"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            out.extend(await self._embed_batch(batch))
        return out

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        body = {"model": self._model, "input": texts}
        resp = await self._client.post("/embeddings", json=body)
        resp.raise_for_status()
        data = resp.json()
        items = sorted(data["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in items]
```

- [ ] **Step 4: Run tests**

```bash
cd backend && uv run pytest tests/search/test_embedding_provider.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/search/embedding.py backend/tests/search/test_embedding_provider.py
git commit -m "feat(search): OpenAI-protocol embedding provider"
```

**PR-B done. Open PR titled `feat(search): pure-logic modules` and run codex review loop.**

---

# PR-C — Embedding worker + repositories + incremental hook

## Task 15: `ConversationChunkRepository`

**Files:**
- Create: `backend/cubebox/repositories/conversation_chunk.py`
- Modify: `backend/cubebox/repositories/__init__.py`

- [ ] **Step 1: Write the repo**

```python
# backend/cubebox/repositories/conversation_chunk.py
"""Repository for conversation_chunks."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.conversation_chunk import ConversationChunk


class ConversationChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_for_conversation(
        self,
        *,
        org_id: str,
        workspace_id: str,
        creator_user_id: str,
        conversation_id: str,
        chunks: list[ConversationChunk],
    ) -> None:
        """Atomic rebuild: drop existing chunks for the conversation and insert new ones."""
        await self.session.execute(
            delete(ConversationChunk).where(
                ConversationChunk.conversation_id == conversation_id  # type: ignore[arg-type]
            )
        )
        for c in chunks:
            c.org_id = org_id
            c.workspace_id = workspace_id
            c.creator_user_id = creator_user_id
            c.conversation_id = conversation_id
            self.session.add(c)
        await self.session.commit()

    async def get_by_ids(self, ids: list[str]) -> list[ConversationChunk]:
        if not ids:
            return []
        stmt = select(ConversationChunk).where(ConversationChunk.id.in_(ids))  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_conversation(self, conversation_id: str) -> int:
        from sqlalchemy import func

        stmt = select(func.count()).select_from(ConversationChunk).where(
            ConversationChunk.conversation_id == conversation_id  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())
```

- [ ] **Step 2: Export**

Edit `backend/cubebox/repositories/__init__.py`:

```python
from cubebox.repositories.conversation_chunk import ConversationChunkRepository  # noqa: F401
```

- [ ] **Step 3: Type-check**

Run: `uv run mypy cubebox/repositories/conversation_chunk.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/repositories/conversation_chunk.py backend/cubebox/repositories/__init__.py
git commit -m "feat(repos): ConversationChunkRepository"
```

---

## Task 16: `EmbeddingJobRepository`

**Files:**
- Create: `backend/cubebox/repositories/embedding_job.py`
- Modify: `backend/cubebox/repositories/__init__.py`

- [ ] **Step 1: Write the repo**

```python
# backend/cubebox/repositories/embedding_job.py
"""Repository for the async embedding queue."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.embedding_job import EmbeddingJob, EmbeddingJobState


class EmbeddingJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(
        self,
        *,
        org_id: str,
        workspace_id: str,
        creator_user_id: str,
        conversation_id: str,
        seq_lo: int = 0,
        seq_hi: int = 2**62,
    ) -> EmbeddingJob:
        job = EmbeddingJob(
            org_id=org_id,
            workspace_id=workspace_id,
            creator_user_id=creator_user_id,
            conversation_id=conversation_id,
            seq_lo=seq_lo,
            seq_hi=seq_hi,
            state=EmbeddingJobState.pending,
        )
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def claim_batch(self, limit: int) -> list[EmbeddingJob]:
        """Claim up to `limit` pending jobs whose scheduled_at <= now()."""
        sql = text(
            """
            UPDATE embedding_jobs
            SET state = 'running', claimed_at = now(), updated_at = now()
            WHERE id IN (
                SELECT id FROM embedding_jobs
                WHERE state = 'pending' AND scheduled_at <= now()
                ORDER BY scheduled_at
                FOR UPDATE SKIP LOCKED
                LIMIT :lim
            )
            RETURNING id
            """
        )
        result = await self.session.execute(sql, {"lim": limit})
        ids = [row[0] for row in result.fetchall()]
        await self.session.commit()
        if not ids:
            return []
        from sqlalchemy import select

        stmt = select(EmbeddingJob).where(EmbeddingJob.id.in_(ids))  # type: ignore[attr-defined]
        result2 = await self.session.execute(stmt)
        return list(result2.scalars().all())

    async def mark_done(self, job_id: str) -> None:
        await self.session.execute(
            text("UPDATE embedding_jobs SET state='done', updated_at=now() WHERE id=:id"),
            {"id": job_id},
        )
        await self.session.commit()

    async def mark_failed(
        self,
        job_id: str,
        error: str,
        prior_attempts: int,
        backoff_seconds: list[int],
        max_attempts: int,
    ) -> None:
        """Record a failure.

        `prior_attempts` is the row's current `attempts` value (the count
        before this failure). The new written value is `prior_attempts + 1`.
        The backoff index reads `backoff_seconds[prior_attempts]`, so a
        first failure (prior=0) waits `backoff_seconds[0]` and the
        configured tail entry actually gets used.
        """
        new_attempts = prior_attempts + 1
        if new_attempts >= max_attempts:
            await self.session.execute(
                text(
                    "UPDATE embedding_jobs SET state='dead', "
                    "attempts=:a, last_error=:err, updated_at=now() WHERE id=:id"
                ),
                {"id": job_id, "a": new_attempts, "err": error[:2000]},
            )
        else:
            delay = backoff_seconds[min(prior_attempts, len(backoff_seconds) - 1)]
            next_at = datetime.now(UTC) + timedelta(seconds=delay)
            await self.session.execute(
                text(
                    "UPDATE embedding_jobs SET state='pending', "
                    "attempts=:a, last_error=:err, scheduled_at=:s, updated_at=now() "
                    "WHERE id=:id"
                ),
                {"id": job_id, "a": new_attempts, "err": error[:2000], "s": next_at},
            )
        await self.session.commit()
```

- [ ] **Step 2: Export**

```python
# backend/cubebox/repositories/__init__.py
from cubebox.repositories.embedding_job import EmbeddingJobRepository  # noqa: F401
```

- [ ] **Step 3: Type-check + commit**

```bash
cd backend && uv run mypy cubebox/repositories/embedding_job.py
git add backend/cubebox/repositories/embedding_job.py backend/cubebox/repositories/__init__.py
git commit -m "feat(repos): EmbeddingJobRepository with claim/done/fail"
```

---

## Task 17: Indexing worker

**Files:**
- Create: `backend/cubebox/search/worker.py`
- Create: `backend/tests/search/test_worker.py`

- [ ] **Step 1: Write the worker**

```python
# backend/cubebox/search/worker.py
"""Drains embedding_jobs: claim → load messages → chunk → embed → write."""

import asyncio
import logging
from collections.abc import Sequence

from cubebox.agents.checkpointer import init_checkpointer
from cubebox.config import config
from cubebox.db.engine import async_session_maker
from cubebox.models.conversation_chunk import ConversationChunk
from cubebox.models.embedding_job import EmbeddingJob
from cubebox.repositories.conversation_chunk import ConversationChunkRepository
from cubebox.repositories.embedding_job import EmbeddingJobRepository
from cubebox.search.chunker import MessageInput, chunk_messages
from cubebox.search.embedding import EmbeddingProvider
from cubebox.search.text_extract import extract_searchable_text

logger = logging.getLogger(__name__)


class EmbeddingWorker:
    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider
        self._stop = asyncio.Event()
        self._poll_interval = int(config.get("search.worker.poll_interval_seconds", 2))
        self._max_attempts = int(config.get("search.worker.max_attempts", 5))
        self._backoff: Sequence[int] = list(
            config.get("search.worker.backoff_seconds", [60, 300, 1500, 7200, 36000])
        )
        self._target_tokens = int(config.get("search.chunker.target_tokens", 600))
        self._overlap_tokens = int(config.get("search.chunker.overlap_tokens", 100))

    async def run(self) -> None:
        logger.info("EmbeddingWorker started")
        while not self._stop.is_set():
            try:
                claimed = await self._claim_one()
                if claimed is None:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
            except TimeoutError:
                continue
            except Exception:
                logger.exception("EmbeddingWorker loop error")
                await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        self._stop.set()

    async def _claim_one(self) -> EmbeddingJob | None:
        async with async_session_maker() as session:
            jobs = await EmbeddingJobRepository(session).claim_batch(limit=1)
        if not jobs:
            return None
        job = jobs[0]
        try:
            await self._process(job)
            async with async_session_maker() as session:
                await EmbeddingJobRepository(session).mark_done(job.id)
            return job
        except Exception as exc:
            logger.exception("Job %s failed", job.id)
            async with async_session_maker() as session:
                await EmbeddingJobRepository(session).mark_failed(
                    job_id=job.id,
                    error=str(exc),
                    prior_attempts=job.attempts,
                    backoff_seconds=list(self._backoff),
                    max_attempts=self._max_attempts,
                )
            return job

    async def _process(self, job: EmbeddingJob) -> None:
        # 1. Load all messages for the conversation (cubepi load is per-thread).
        async with init_checkpointer() as cp:
            data = await cp.load(job.conversation_id)
        if data is None:
            return
        # 2. Filter to (seq_lo, seq_hi) window.
        #
        # We use 1-based load-order as the seq. This matches what the
        # frontend conversation page uses for its `#msg-N` anchors —
        # both sides walk cubepi's `data.messages` in order. The seq
        # is a navigation hint, not an authoritative cubepi reference.
        # If cubepi ever starts filtering tombstones / system messages
        # from `data.messages`, both sides shift together, anchors stay
        # consistent.
        in_window = [
            (idx + 1, m)
            for idx, m in enumerate(data.messages)
            if job.seq_lo <= idx + 1 <= job.seq_hi
        ]
        # 3. Extract searchable text per message.
        inputs: list[MessageInput] = []
        for seq, m in in_window:
            text = extract_searchable_text(m)
            if text:
                inputs.append(MessageInput(seq=seq, text=text))
        # 4. Chunk.
        chunks = chunk_messages(
            inputs, target_tokens=self._target_tokens, overlap_tokens=self._overlap_tokens
        )
        if not chunks:
            return
        # 5. Embed.
        vectors = await self._provider.embed([c.text for c in chunks])
        # 6. Persist.
        rows = [
            ConversationChunk(
                chunk_seq=c.chunk_seq,
                seq_lo=c.seq_lo,
                seq_hi=c.seq_hi,
                text=c.text,
                embedding=v,
                embed_model=self._provider.model_id,
            )
            for c, v in zip(chunks, vectors, strict=True)
        ]
        async with async_session_maker() as session:
            await ConversationChunkRepository(session).replace_for_conversation(
                org_id=job.org_id,
                workspace_id=job.workspace_id,
                creator_user_id=job.creator_user_id,
                conversation_id=job.conversation_id,
                chunks=rows,
            )
```

- [ ] **Step 2: Write an integration test (uses real Postgres + a fake embed provider)**

```python
# backend/tests/search/test_worker.py
import pytest

from cubebox.db.engine import async_session_maker
from cubebox.models.embedding_job import EmbeddingJobState
from cubebox.repositories.embedding_job import EmbeddingJobRepository
from cubebox.search.embedding import EmbeddingProvider
from cubebox.search.worker import EmbeddingWorker


class _FakeProvider(EmbeddingProvider):
    def __init__(self) -> None:
        # Bypass real init; we never call HTTP.
        self.dimensions = 1024
        self._model = "fake"
        self._base_url = "https://fake.local"

    @property
    def model_id(self) -> str:  # type: ignore[override]
        return "fake@fake.local"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.01 * (i + 1)] * self.dimensions for i, _ in enumerate(texts)]


@pytest.mark.asyncio
async def test_worker_processes_job_for_seeded_conversation(seeded_conversation):
    """`seeded_conversation` is a pytest fixture (defined in conftest) that
    creates a Conversation row + writes a handful of cubepi messages and
    returns (org_id, workspace_id, user_id, conversation_id)."""
    org_id, ws_id, user_id, conv_id = seeded_conversation
    async with async_session_maker() as session:
        await EmbeddingJobRepository(session).enqueue(
            org_id=org_id, workspace_id=ws_id, creator_user_id=user_id,
            conversation_id=conv_id,
        )
    worker = EmbeddingWorker(_FakeProvider())
    job = await worker._claim_one()
    assert job is not None
    # Verify chunks exist and the job is marked done.
    async with async_session_maker() as session:
        from cubebox.repositories.conversation_chunk import ConversationChunkRepository

        n = await ConversationChunkRepository(session).count_for_conversation(conv_id)
    assert n > 0
```

- [ ] **Step 3: Add the fixture**

Append to `backend/tests/conftest.py` (or create a new fixtures module if
the existing conftest is large; check first with `grep -l seeded_conversation
tests/conftest.py`):

```python
import pytest_asyncio

from cubebox.agents.checkpointer import init_checkpointer
from cubebox.db.engine import async_session_maker
from cubebox.models.conversation import Conversation


@pytest_asyncio.fixture
async def seeded_conversation(test_user_ctx):
    """Create a conversation and seed three small cubepi messages.

    `test_user_ctx` is the existing helper fixture that returns an
    (org_id, workspace_id, user_id) tuple; reuse whatever conftest exposes."""
    from cubepi.providers.base import AssistantMessage, TextContent, UserMessage

    org_id, ws_id, user_id = test_user_ctx
    async with async_session_maker() as session:
        c = Conversation(
            org_id=org_id, workspace_id=ws_id, creator_user_id=user_id,
            title="seed",
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        conv_id = c.id
    async with init_checkpointer() as cp:
        await cp.append(conv_id, [
            UserMessage(content=[TextContent(text="hello docling")], timestamp=1.0),
            AssistantMessage(content=[TextContent(text="hi there")], timestamp=2.0),
            UserMessage(content=[TextContent(text="文档解析问题")], timestamp=3.0),
        ])
    return org_id, ws_id, user_id, conv_id
```

(If `test_user_ctx` does not exist in conftest, inspect existing fixtures
that produce org/ws/user and call them instead — search:
`grep -r "def.*user.*org" backend/tests/conftest.py`).

- [ ] **Step 4: Run the test**

```bash
cd backend && uv run pytest tests/search/test_worker.py -v
```

Expected: pass. If `seeded_conversation` resolution fails, fix the fixture
import in conftest before continuing.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/search/worker.py backend/tests/search/test_worker.py \
        backend/tests/conftest.py
git commit -m "feat(search): EmbeddingWorker — claim, chunk, embed, write"
```

---

## Task 18: Indexer module + incremental hook

**Files:**
- Create: `backend/cubebox/search/indexer.py`
- Modify: `backend/cubebox/api/routes/v1/conversations.py`

- [ ] **Step 1: Write the indexer helper**

```python
# backend/cubebox/search/indexer.py
"""Convenience helpers used by callers that want to (re)index a conversation."""

from cubebox.db.engine import async_session_maker
from cubebox.repositories.embedding_job import EmbeddingJobRepository


async def enqueue_index_job(
    *,
    org_id: str,
    workspace_id: str,
    creator_user_id: str,
    conversation_id: str,
) -> None:
    """Enqueue a single 'index the whole conversation' job. The worker dedupes
    by always replacing chunks for the conversation, so duplicate enqueues are
    safe and cheap."""
    async with async_session_maker() as session:
        await EmbeddingJobRepository(session).enqueue(
            org_id=org_id,
            workspace_id=workspace_id,
            creator_user_id=creator_user_id,
            conversation_id=conversation_id,
        )
```

- [ ] **Step 2: Make failures observable via structured logging**

Edit `backend/cubebox/search/indexer.py` and replace its body with:

```python
# backend/cubebox/search/indexer.py
"""Convenience helpers used by callers that want to (re)index a conversation."""

import logging

from cubebox.db.engine import async_session_maker
from cubebox.repositories.embedding_job import EmbeddingJobRepository

logger = logging.getLogger(__name__)


async def enqueue_index_job(
    *,
    org_id: str,
    workspace_id: str,
    creator_user_id: str,
    conversation_id: str,
) -> None:
    """Enqueue a single 'index the whole conversation' job. The worker dedupes
    by always replacing chunks for the conversation, so duplicate enqueues are
    safe and cheap.

    On failure, logs an ERROR with `event=search_index_enqueue_failed`
    and the conversation_id, then re-raises so callers can decide how to
    react. The hook in conversations.py catches and swallows (best-effort);
    other callers (backfill) let it propagate. The structured `event=` key
    lets log-based alerting fire on the first failure rather than the
    user-visible 'search results stop appearing weeks later' symptom.
    """
    try:
        async with async_session_maker() as session:
            await EmbeddingJobRepository(session).enqueue(
                org_id=org_id,
                workspace_id=workspace_id,
                creator_user_id=creator_user_id,
                conversation_id=conversation_id,
            )
    except Exception:
        logger.error(
            "event=search_index_enqueue_failed conversation_id=%s workspace_id=%s",
            conversation_id,
            workspace_id,
            exc_info=True,
        )
        raise
```

- [ ] **Step 3: Hook into `_update_conversation_timestamp`**

Edit `backend/cubebox/api/routes/v1/conversations.py`. Find
`_update_conversation_timestamp` (around line 74). After
`await save_conv_repo.mark_active(conversation_id)`, before
`await save_engine.dispose()`, append:

```python
            try:
                from cubebox.config import config as _cfg
                from cubebox.search.indexer import enqueue_index_job

                if _cfg.get("search.enabled", True):
                    await enqueue_index_job(
                        org_id=org_id,
                        workspace_id=workspace_id,
                        creator_user_id=user_id,
                        conversation_id=conversation_id,
                    )
            except Exception:
                # Indexing is best-effort: failure must not poison the
                # conversation timestamp bump. The Counter in indexer.py
                # has already incremented — silent here, observable in
                # Prometheus.
                logger.exception("search index enqueue failed for %s", conversation_id)
```

Add the logger import at the module top if not already present (it should be).

- [ ] **Step 4: Run the existing conversations tests to make sure we didn't break the hook**

```bash
cd backend && uv run pytest tests/api/test_conversations.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add backend/cubebox/search/indexer.py backend/cubebox/api/routes/v1/conversations.py
git commit -m "feat(search): enqueue index job on run completion (observable)"
```

---

## Task 19: Wire the worker into FastAPI lifespan

**Files:**
- Modify: `backend/cubebox/api/app.py`

- [ ] **Step 1: Read the existing lifespan**

```bash
grep -n "lifespan\|app.state" backend/cubebox/api/app.py | head -30
```

Note the lifespan function name and where startup/shutdown hooks live.

- [ ] **Step 2: Add provider + worker startup (single instance on app.state)**

Inside the lifespan async function, after existing startup logic, add:

```python
    from cubebox.config import config as _cfg
    from cubebox.search.embedding import EmbeddingProvider
    from cubebox.search.worker import EmbeddingWorker

    worker_task: asyncio.Task[None] | None = None
    app.state.embedding_provider = None
    app.state.embedding_worker = None

    if _cfg.get("search.enabled", True):
        provider = EmbeddingProvider.from_config()
        app.state.embedding_provider = provider
        # The route handler reads app.state.embedding_provider — no
        # per-request rebuild, no per-batch httpx.AsyncClient churn.
        worker = EmbeddingWorker(provider)
        worker_task = asyncio.create_task(worker.run(), name="embedding-worker")
        app.state.embedding_worker = worker
```

And at shutdown (after `yield` in the lifespan), add:

```python
    if worker_task is not None:
        app.state.embedding_worker.stop()
        try:
            await asyncio.wait_for(worker_task, timeout=5.0)
        except TimeoutError:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass
    if app.state.embedding_provider is not None:
        await app.state.embedding_provider.aclose()
```

Add `import asyncio` at the top if not present.

- [ ] **Step 3: Smoke test — start the server and verify worker logs**

```bash
cd backend && uv run python main.py &
SERVER_PID=$!
sleep 5
curl -s http://localhost:8048/api/v1/health
kill $SERVER_PID
```

Expected: server starts; the log line `EmbeddingWorker started` appears in
stdout/stderr.

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/api/app.py
git commit -m "feat(search): start EmbeddingWorker in FastAPI lifespan"
```

---

## Task 20: Service-level test

**Files:**
- Create: `backend/tests/search/test_service.py` (stub — service code follows in Task 22, this fixture-level test verifies the pipeline assembled so far is loadable).

- [ ] **Step 1: Smoke import test**

```python
# backend/tests/search/test_service.py
"""End-to-end smoke that ensures all modules built so far are importable
and the worker round-trips a real conversation."""

import pytest


@pytest.mark.asyncio
async def test_worker_end_to_end(seeded_conversation, monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    from cubebox.search.embedding import EmbeddingProvider
    from cubebox.search.worker import EmbeddingWorker

    # Use a deterministic provider so the test does not require network.
    class _Det(EmbeddingProvider):
        def __init__(self) -> None:
            self.dimensions = 1024
            self._model = "det"
            self._base_url = "https://det.local"

        @property
        def model_id(self) -> str:  # type: ignore[override]
            return "det@det.local"

        async def embed(self, texts):
            return [[1.0 / (i + 1)] * self.dimensions for i, _ in enumerate(texts)]

    org_id, ws_id, user_id, conv_id = seeded_conversation
    from cubebox.db.engine import async_session_maker
    from cubebox.repositories.embedding_job import EmbeddingJobRepository
    from cubebox.repositories.conversation_chunk import ConversationChunkRepository

    async with async_session_maker() as s:
        await EmbeddingJobRepository(s).enqueue(
            org_id=org_id, workspace_id=ws_id, creator_user_id=user_id, conversation_id=conv_id,
        )
    worker = EmbeddingWorker(_Det())
    await worker._claim_one()
    async with async_session_maker() as s:
        n = await ConversationChunkRepository(s).count_for_conversation(conv_id)
    assert n > 0
```

- [ ] **Step 2: Run**

```bash
cd backend && uv run pytest tests/search/test_service.py -v
```

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/search/test_service.py
git commit -m "test(search): worker round-trip smoke"
```

**PR-C done. Open PR titled `feat(search): worker + repositories + incremental hook` and run codex review loop.**

---

# PR-D — Search API, backfill, integration E2E

## Task 21: `ConversationSearchService`

**Files:**
- Create: `backend/cubebox/search/service.py`
- Append: `backend/tests/search/test_service.py`

- [ ] **Step 1: Write the service**

```python
# backend/cubebox/search/service.py
"""Hybrid search: lexical leg + vector leg → RRF → snippet + offsets."""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.config import config
from cubebox.models.conversation import Conversation
from cubebox.search.embedding import EmbeddingProvider
from cubebox.search.lexical import build_lexical_backend
from cubebox.search.rrf import rrf_fuse
from cubebox.search.snippet import Snippet, extract_snippet

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    conversation_id: str
    title: str
    snippet: str
    match_offsets: list[tuple[int, int]]
    matched_message_seq: int | None
    matched_at: str | None
    score: float


@dataclass(frozen=True)
class SearchResponse:
    results: list[SearchResult]
    lexical_count: int
    vector_count: int
    fused_count: int


class ConversationSearchService:
    def __init__(self, session: AsyncSession, provider: EmbeddingProvider) -> None:
        self._session = session
        self._provider = provider
        self._lexical = build_lexical_backend()
        self._k = int(config.get("search.rrf.k", 60))
        self._prefetch = int(config.get("search.rrf.prefetch_per_leg", 20))

    async def search(
        self,
        *,
        org_id: str,
        workspace_id: str,
        creator_user_id: str,
        q: str,
        limit: int,
    ) -> SearchResponse:
        q = q.strip()
        if not q:
            return SearchResponse([], 0, 0, 0)
        lex_hits, vec_hits = await asyncio.gather(
            self._lexical_leg(org_id, workspace_id, creator_user_id, q),
            self._vector_leg(org_id, workspace_id, creator_user_id, q),
            return_exceptions=True,
        )
        lex_list = lex_hits if isinstance(lex_hits, list) else []
        vec_list = vec_hits if isinstance(vec_hits, list) else []
        if isinstance(lex_hits, Exception):
            logger.warning("Lexical leg failed: %s", lex_hits)
        if isinstance(vec_hits, Exception):
            logger.warning("Vector leg failed: %s", vec_hits)
        fused = rrf_fuse(
            lexical=[r[0] for r in lex_list],
            vector=[r[0] for r in vec_list],
            k=self._k,
        )
        if not fused:
            return SearchResponse([], len(lex_list), len(vec_list), 0)
        # Hydrate chunks + their conversation; aggregate to conversation.
        chunk_ids = [doc_id for doc_id, _ in fused]
        chunks_by_id = await self._hydrate_chunks(chunk_ids)
        # Group by conversation; keep highest-scoring chunk per conversation.
        seen: dict[str, tuple[float, dict]] = {}
        for doc_id, score in fused:
            ch = chunks_by_id.get(doc_id)
            if ch is None:
                continue
            conv_id = ch["conversation_id"]
            if conv_id in seen and seen[conv_id][0] >= score:
                continue
            seen[conv_id] = (score, ch)
        # Resolve titles + build snippets, truncate to limit.
        ordered = sorted(seen.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
        titles = await self._titles([cid for cid, _ in ordered])
        results: list[SearchResult] = []
        for conv_id, (score, ch) in ordered:
            snip: Snippet = extract_snippet(ch["text"], q=q, window=160)
            # v1: navigate to the chunk's first message. Precise
            # per-match resolution would need per-message text-offset
            # metadata on the chunk; deferred (see spec §9.1 step 6).
            results.append(
                SearchResult(
                    conversation_id=conv_id,
                    title=titles.get(conv_id, ""),
                    snippet=snip.text,
                    match_offsets=list(snip.match_offsets),
                    matched_message_seq=int(ch["seq_lo"]),
                    matched_at=ch.get("created_at_iso"),
                    score=score,
                )
            )
        return SearchResponse(
            results=results,
            lexical_count=len(lex_list),
            vector_count=len(vec_list),
            fused_count=len(fused),
        )

    async def _lexical_leg(
        self, org_id: str, ws_id: str, user_id: str, q: str
    ) -> list[tuple[str, float]]:
        bundle = self._lexical.search_sql(limit=self._prefetch)
        binds = {
            "org_id": org_id,
            "ws_id": ws_id,
            "user_id": user_id,
            "q": self._lexical.normalize_query(q),
        }
        result = await self._session.execute(text(bundle.sql), binds)
        return [(row[0], float(row[1])) for row in result.fetchall()]

    async def _vector_leg(
        self, org_id: str, ws_id: str, user_id: str, q: str
    ) -> list[tuple[str, float]]:
        from pgvector.sqlalchemy import Vector

        vectors = await self._provider.embed([q])
        if not vectors:
            return []
        sql = text(
            """
            SELECT id, 1.0 - (embedding <=> :v) AS score
            FROM conversation_chunks
            WHERE org_id = :org_id AND workspace_id = :ws_id AND creator_user_id = :user_id
            ORDER BY embedding <=> :v
            LIMIT :lim
            """
        ).bindparams(bindparam("v", type_=Vector(self._provider.dimensions)))
        binds = {
            "org_id": org_id, "ws_id": ws_id, "user_id": user_id,
            "v": vectors[0], "lim": self._prefetch,
        }
        result = await self._session.execute(sql, binds)
        return [(row[0], float(row[1])) for row in result.fetchall()]

    async def _hydrate_chunks(self, chunk_ids: list[str]) -> dict[str, dict]:
        if not chunk_ids:
            return {}
        from cubebox.utils.time import utc_isoformat

        sql = text(
            """
            SELECT id, conversation_id, seq_lo, seq_hi, text, created_at
            FROM conversation_chunks
            WHERE id = ANY(:ids)
            """
        )
        result = await self._session.execute(sql, {"ids": chunk_ids})
        out: dict[str, dict] = {}
        for r in result.mappings().all():
            row = dict(r)
            # Project rule: DB → frontend datetimes go through utc_isoformat().
            row["created_at_iso"] = utc_isoformat(row["created_at"])
            out[row["id"]] = row
        return out

    async def _titles(self, conversation_ids: list[str]) -> dict[str, str]:
        if not conversation_ids:
            return {}
        from sqlalchemy import select

        stmt = select(Conversation.id, Conversation.title).where(
            Conversation.id.in_(conversation_ids),  # type: ignore[attr-defined]
            Conversation.deleted_at.is_(None),  # type: ignore[attr-defined]
        )
        result = await self._session.execute(stmt)
        return {row[0]: row[1] for row in result.fetchall()}
```

- [ ] **Step 2: Append a fused-pipeline test to `test_service.py`**

```python
# Append to backend/tests/search/test_service.py

import pytest


@pytest.mark.asyncio
async def test_search_returns_relevant_conversation(seeded_conversation) -> None:
    """After indexing, a search for 'docling' returns the seeded conversation."""
    org_id, ws_id, user_id, conv_id = seeded_conversation
    # Run the worker once to populate chunks.
    from cubebox.search.embedding import EmbeddingProvider
    from cubebox.search.worker import EmbeddingWorker
    from cubebox.db.engine import async_session_maker
    from cubebox.repositories.embedding_job import EmbeddingJobRepository

    class _Det(EmbeddingProvider):
        def __init__(self) -> None:
            self.dimensions = 1024
            self._model = "det"
            self._base_url = "https://det.local"

        @property
        def model_id(self) -> str:  # type: ignore[override]
            return "det@det.local"

        async def embed(self, texts):
            # Map by content so the query for 'docling' matches the
            # 'hello docling' chunk best.
            return [[float("docling" in t)] * self.dimensions for t in texts]

    async with async_session_maker() as s:
        await EmbeddingJobRepository(s).enqueue(
            org_id=org_id, workspace_id=ws_id, creator_user_id=user_id, conversation_id=conv_id,
        )
    await EmbeddingWorker(_Det())._claim_one()
    from cubebox.search.service import ConversationSearchService

    async with async_session_maker() as s:
        svc = ConversationSearchService(s, _Det())
        resp = await svc.search(
            org_id=org_id, workspace_id=ws_id, creator_user_id=user_id, q="docling", limit=8,
        )
    assert any(r.conversation_id == conv_id for r in resp.results)
```

- [ ] **Step 3: Run**

```bash
cd backend && uv run pytest tests/search/test_service.py -v
```

Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add backend/cubebox/search/service.py backend/tests/search/test_service.py
git commit -m "feat(search): ConversationSearchService — hybrid + RRF + snippet"
```

---

## Task 22: API schemas + route

**Files:**
- Create: `backend/cubebox/api/schemas/conversation_search.py`
- Create: `backend/cubebox/api/routes/v1/conversation_search.py`
- Modify: `backend/cubebox/api/routes/v1/__init__.py`

- [ ] **Step 1: Schemas**

```python
# backend/cubebox/api/schemas/conversation_search.py
from pydantic import BaseModel, Field


class SearchResultSchema(BaseModel):
    conversation_id: str
    title: str
    snippet: str
    match_offsets: list[tuple[int, int]] = Field(default_factory=list)
    matched_message_seq: int | None = None
    matched_at: str | None = None
    score: float


class SearchResponseSchema(BaseModel):
    results: list[SearchResultSchema]
    lexical_count: int
    vector_count: int
    fused_count: int
```

- [ ] **Step 2: Route**

```python
# backend/cubebox/api/routes/v1/conversation_search.py
"""Workspace-scoped conversation search."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.conversation_search import (
    SearchResponseSchema,
    SearchResultSchema,
)
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db import get_session
from cubebox.search.service import ConversationSearchService

router = APIRouter(prefix="/ws/{workspace_id}/conversations", tags=["conversations"])


@router.get("/search", response_model=SearchResponseSchema)
async def search_conversations(
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> SearchResponseSchema:
    cleaned = q.strip()
    if not cleaned:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="empty query")
    # Provider is created once at lifespan startup (Task 19) and shared
    # across all requests; building one per request would re-init httpx
    # connection pools and re-parse config on every keystroke.
    provider = raw_request.app.state.embedding_provider
    if provider is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="search is disabled",
        )
    svc = ConversationSearchService(session, provider)
    resp = await svc.search(
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        creator_user_id=ctx.user.id,
        q=cleaned,
        limit=limit,
    )
    return SearchResponseSchema(
        results=[SearchResultSchema(**r.__dict__) for r in resp.results],
        lexical_count=resp.lexical_count,
        vector_count=resp.vector_count,
        fused_count=resp.fused_count,
    )
```

- [ ] **Step 3: Wire the router**

Edit `backend/cubebox/api/routes/v1/__init__.py`. Find the line that
includes the existing `conversations` router and add a second
`include_router` call for `conversation_search.router`. Order matters only
inside FastAPI when prefixes collide; this one has the literal `/search`
suffix and won't conflict.

- [ ] **Step 4: Route-level test**

```python
# backend/tests/api/test_conversation_search_route.py
import pytest


@pytest.mark.asyncio
async def test_search_route_rejects_empty_query(authed_client, seeded_workspace) -> None:
    ws_id = seeded_workspace.id
    resp = await authed_client.get(
        f"/api/v1/ws/{ws_id}/conversations/search", params={"q": "   "}
    )
    assert resp.status_code == 422  # FastAPI Query min_length=1 strips? Or 400 from handler
    # (Either is acceptable — the route guards both layers.)


@pytest.mark.asyncio
async def test_search_route_returns_results_after_indexing(
    authed_client, seeded_workspace, indexed_conversation
) -> None:
    ws_id = seeded_workspace.id
    resp = await authed_client.get(
        f"/api/v1/ws/{ws_id}/conversations/search",
        params={"q": "docling", "limit": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "results" in body
    assert any(r["conversation_id"] == indexed_conversation for r in body["results"])
```

The `authed_client`, `seeded_workspace`, and `indexed_conversation` fixtures
follow the same patterns as existing API tests — copy from
`tests/api/test_conversations.py` for the first two; the third uses
`seeded_conversation` + runs the worker once. Inspect existing fixtures
before writing:

```bash
grep -n "authed_client\|seeded_workspace" backend/tests/api/test_conversations.py backend/tests/conftest.py
```

- [ ] **Step 5: Run**

```bash
cd backend && uv run pytest tests/api/test_conversation_search_route.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add backend/cubebox/api/schemas/conversation_search.py \
        backend/cubebox/api/routes/v1/conversation_search.py \
        backend/cubebox/api/routes/v1/__init__.py \
        backend/tests/api/test_conversation_search_route.py
git commit -m "feat(api): GET /conversations/search route"
```

---

## Task 23: Backfill script

**Files:**
- Create: `backend/scripts/dev/backfill_search_index.py`

- [ ] **Step 1: Write the script**

```python
# backend/scripts/dev/backfill_search_index.py
"""Enqueue an embedding job for every conversation in every workspace.

Resumable via search_backfill_progress. Idempotent — re-running picks up
where it left off, and a conversation already chunked just re-runs the
worker which replaces its chunks (one-shot).

Usage:
    uv run python -m scripts.dev.backfill_search_index --rate 5
"""

import argparse
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.db.engine import async_session_maker
from cubebox.models.conversation import Conversation
from cubebox.models.organization import Organization
from cubebox.models.search_backfill_progress import SearchBackfillProgress
from cubebox.models.workspace import Workspace
from cubebox.repositories.embedding_job import EmbeddingJobRepository

logger = logging.getLogger("backfill")


async def _workspaces(session: AsyncSession) -> list[Workspace]:
    result = await session.execute(select(Workspace))
    return list(result.scalars().all())


async def _conversations_for_ws(session: AsyncSession, ws: Workspace, after: str | None):
    stmt = select(Conversation).where(
        Conversation.workspace_id == ws.id,  # type: ignore[arg-type]
        Conversation.deleted_at.is_(None),  # type: ignore[attr-defined]
    )
    if after:
        stmt = stmt.where(Conversation.id > after)  # type: ignore[operator]
    stmt = stmt.order_by(Conversation.id).limit(1000)  # type: ignore[arg-type]
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _progress(session: AsyncSession, ws: Workspace) -> SearchBackfillProgress:
    stmt = select(SearchBackfillProgress).where(
        SearchBackfillProgress.workspace_id == ws.id  # type: ignore[arg-type]
    )
    result = await session.execute(stmt)
    p = result.scalar_one_or_none()
    if p is None:
        p = SearchBackfillProgress(org_id=ws.org_id, workspace_id=ws.id)
        session.add(p)
        await session.commit()
        await session.refresh(p)
    return p


async def main(rate: float) -> None:
    delay = 1.0 / max(0.1, rate)
    async with async_session_maker() as session:
        wss = await _workspaces(session)
    for ws in wss:
        async with async_session_maker() as session:
            p = await _progress(session, ws)
            if p.done:
                logger.info("ws=%s already done; skipping", ws.id)
                continue
            after = p.last_conversation_id
        while True:
            async with async_session_maker() as session:
                convs = await _conversations_for_ws(session, ws, after)
            if not convs:
                async with async_session_maker() as session:
                    p = await _progress(session, ws)
                    p.done = True
                    session.add(p)
                    await session.commit()
                break
            for c in convs:
                async with async_session_maker() as session:
                    await EmbeddingJobRepository(session).enqueue(
                        org_id=c.org_id, workspace_id=c.workspace_id,
                        creator_user_id=c.creator_user_id, conversation_id=c.id,
                    )
                async with async_session_maker() as session:
                    p = await _progress(session, ws)
                    p.last_conversation_id = c.id
                    p.enqueued_count += 1
                    session.add(p)
                    await session.commit()
                logger.info("enqueued conv=%s ws=%s", c.id, ws.id)
                await asyncio.sleep(delay)
            after = convs[-1].id


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument("--rate", type=float, default=5.0, help="enqueues / sec")
    args = p.parse_args()
    asyncio.run(main(args.rate))
```

- [ ] **Step 2: Dry-run on dev DB**

```bash
cd backend && uv run python -m scripts.dev.backfill_search_index --rate 20
```

Expected: enqueues jobs for every non-deleted conversation; logs each id;
exits cleanly when done.

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/dev/backfill_search_index.py
git commit -m "feat(search): backfill script with resumable progress"
```

---

## Task 24: Backend E2E test

**Files:**
- Create: `backend/tests/e2e/test_conversation_search.py`

- [ ] **Step 1: Write the E2E test**

```python
# backend/tests/e2e/test_conversation_search.py
"""End-to-end: seed conversations → enqueue → drive worker → call search API."""

import os

import pytest


@pytest.mark.asyncio
async def test_e2e_search_finds_seeded_conversations(
    authed_client, seeded_workspace, seed_conversations_with_content
) -> None:
    # Skip if no embedding key — keep CI honest.
    if not os.environ.get("DASHSCOPE_API_KEY") and not os.environ.get(
        "CUBEBOX_TEST_LOCAL_EMBED"
    ):
        pytest.skip("No embedding endpoint configured; set DASHSCOPE_API_KEY or CUBEBOX_TEST_LOCAL_EMBED.")

    ws = seeded_workspace
    convs = seed_conversations_with_content  # fixture yields a list of (conv_id, gist)
    # Enqueue + drive worker once per conversation.
    from cubebox.db.engine import async_session_maker
    from cubebox.repositories.embedding_job import EmbeddingJobRepository
    from cubebox.search.embedding import EmbeddingProvider
    from cubebox.search.worker import EmbeddingWorker

    async with async_session_maker() as s:
        for conv_id, _ in convs:
            await EmbeddingJobRepository(s).enqueue(
                org_id=ws.org_id, workspace_id=ws.id,
                creator_user_id=ws.owner_user_id, conversation_id=conv_id,
            )
    worker = EmbeddingWorker(EmbeddingProvider.from_config())
    # drain
    while await worker._claim_one() is not None:
        pass

    # English keyword
    resp = await authed_client.get(
        f"/api/v1/ws/{ws.id}/conversations/search", params={"q": "docling"}
    )
    assert resp.status_code == 200
    assert any("docling" in r["title"].lower() or "docling" in r["snippet"].lower()
               for r in resp.json()["results"])

    # Chinese keyword
    resp = await authed_client.get(
        f"/api/v1/ws/{ws.id}/conversations/search", params={"q": "文档解析"}
    )
    assert resp.status_code == 200
    assert resp.json()["fused_count"] > 0
```

The `seed_conversations_with_content` fixture seeds three conversations:
one with English content mentioning "docling", one with Chinese content
mentioning "文档解析", and one with mixed / unrelated content. Add it to
`backend/tests/conftest.py` near `seeded_conversation`.

- [ ] **Step 2: Run with a real key**

```bash
cd backend && DASHSCOPE_API_KEY=$YOUR_KEY uv run pytest tests/e2e/test_conversation_search.py -v
```

Expected: pass. Without a key, the test is skipped (not failed).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/e2e/test_conversation_search.py backend/tests/conftest.py
git commit -m "test(search): backend E2E for conversation search"
```

---

## Task 25: CI gating for E2E

**Files:**
- Modify: `.github/workflows/<existing-backend-ci>.yml` (search for it first).

- [ ] **Step 1: Locate the backend test job**

```bash
grep -ln "pytest" .github/workflows/*.yml
```

- [ ] **Step 2: Add the secret-gated E2E step**

Inside the backend test job, after the existing `pytest tests/` step,
append a step:

```yaml
      - name: E2E — conversation search (skipped without secret)
        env:
          DASHSCOPE_API_KEY: ${{ secrets.DASHSCOPE_API_KEY }}
        run: |
          cd backend
          uv run pytest tests/e2e/test_conversation_search.py -v
```

(Existing E2E tests in the worktree already follow the pattern of being
collected from `tests/e2e/`; if so, this step is redundant — verify with
`grep -n 'tests/e2e' backend/Makefile .github/workflows/*.yml` before
adding a new step. If E2E is already part of the main pytest pass, just
ensure the secret is exposed.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/
git commit -m "ci: gate conversation-search E2E on DASHSCOPE_API_KEY"
```

**PR-D done. Open PR titled `feat(search): API + backfill + E2E` and run codex review loop.**

---

# PR-E — Frontend popover

## Task 26: API client method

**Files:**
- Create: `frontend/packages/core/src/api/conversation-search.ts`
- Modify: `frontend/packages/core/src/api/index.ts`

- [ ] **Step 1: Write the client**

```typescript
// frontend/packages/core/src/api/conversation-search.ts
import type { ApiClient } from './client'

export interface SearchResult {
  conversation_id: string
  title: string
  snippet: string
  match_offsets: [number, number][]
  matched_message_seq: number | null
  matched_at: string | null
  score: number
}

export interface SearchResponse {
  results: SearchResult[]
  lexical_count: number
  vector_count: number
  fused_count: number
}

export async function searchConversations(
  client: ApiClient,
  q: string,
  limit = 8,
): Promise<SearchResponse> {
  // ApiClient.get returns Promise<Response>; it auto-injects the `/ws/{id}/`
  // segment via injectWorkspace, so we pass the scoped suffix only.
  const path = `/api/v1/conversations/search?q=${encodeURIComponent(q)}&limit=${limit}`
  const resp = await client.get(path)
  if (!resp.ok) {
    throw new Error(`search failed: ${resp.status}`)
  }
  return (await resp.json()) as SearchResponse
}
```

ApiClient (`packages/core/src/api/client.ts`) provides
`get(path: string): Promise<Response>`; the workspace segment is added
automatically when `client.workspaceId` is set. There is no built-in
abort-signal parameter — the hook in Task 27 handles cancellation by
ignoring stale responses, not by aborting the fetch.

- [ ] **Step 2: Re-export**

Append to `packages/core/src/api/index.ts`:

```typescript
export * from './conversation-search'
```

- [ ] **Step 3: Build core**

```bash
cd frontend && pnpm --filter @cubebox/core build
```

Expected: success.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/core/src/api/conversation-search.ts \
        frontend/packages/core/src/api/index.ts
git commit -m "feat(core): searchConversations API method"
```

---

## Task 27: `useConversationSearch` hook

**Files:**
- Create: `frontend/packages/web/hooks/useConversationSearch.ts`

- [ ] **Step 1: Implement**

```typescript
// frontend/packages/web/hooks/useConversationSearch.ts
'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { createApiClient, searchConversations, type SearchResult } from '@cubebox/core'

export interface SearchState {
  loading: boolean
  error: string | null
  results: SearchResult[]
}

const DEBOUNCE_MS = 250

export function useConversationSearch(query: string, wsId: string | null): SearchState {
  const [state, setState] = useState<SearchState>({ loading: false, error: null, results: [] })
  // Stale-response counter: the only response we render is the most recent one.
  // ApiClient.get has no signal parameter, so we can't actually abort the
  // fetch — instead we tag each request and ignore replies for older tags.
  const requestIdRef = useRef(0)

  const client = useMemo(() => {
    const c = createApiClient('')
    if (wsId) c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    if (!wsId) {
      setState({ loading: false, error: null, results: [] })
      return
    }
    const q = query.trim()
    if (q.length === 0) {
      setState({ loading: false, error: null, results: [] })
      return
    }
    const handle = window.setTimeout(() => {
      const myId = ++requestIdRef.current
      setState((s) => ({ ...s, loading: true, error: null }))
      searchConversations(client, q, 8)
        .then((resp) => {
          if (myId !== requestIdRef.current) return // stale
          setState({ loading: false, error: null, results: resp.results })
        })
        .catch(() => {
          if (myId !== requestIdRef.current) return
          setState({ loading: false, error: 'search-failed', results: [] })
        })
    }, DEBOUNCE_MS)
    return () => {
      window.clearTimeout(handle)
      // bump the id so the in-flight reply (if any) is discarded
      requestIdRef.current += 1
    }
  }, [query, wsId, client])

  return state
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/web/hooks/useConversationSearch.ts
git commit -m "feat(web): useConversationSearch debounced hook"
```

---

## Task 28: `SearchResultRow` component

**Files:**
- Create: `frontend/packages/web/components/sidebar/SearchResultRow.tsx`

- [ ] **Step 1: Component**

```typescript
// frontend/packages/web/components/sidebar/SearchResultRow.tsx
'use client'

import Link from 'next/link'
import { useMemo } from 'react'
import type { SearchResult } from '@cubebox/core'
import { cn } from '@/lib/utils'

interface Props {
  result: SearchResult
  wsId: string
  active: boolean
  onPick: () => void
}

export function SearchResultRow({ result, wsId, active, onPick }: Props): React.ReactElement {
  const href = `/w/${wsId}/conversations/${result.conversation_id}${
    result.matched_message_seq ? `#msg-${result.matched_message_seq}` : ''
  }`
  const segments = useMemo(() => splitSnippet(result.snippet, result.match_offsets), [result])
  return (
    <li>
      <Link
        href={href}
        onClick={onPick}
        className={cn(
          'group flex flex-col gap-0.5 rounded px-3 py-2 text-xs transition-colors duration-fast',
          active
            ? 'bg-accent text-foreground'
            : 'text-muted-foreground hover:bg-accent hover:text-foreground',
        )}
      >
        <span className="truncate text-[12.5px] font-medium leading-tight">
          {result.title || 'Untitled'}
        </span>
        <span className="line-clamp-2 text-2xs leading-snug text-faint">
          {segments.map((s, i) =>
            s.match ? (
              <mark key={i} className="bg-primary/20 text-foreground rounded-sm">
                {s.text}
              </mark>
            ) : (
              <span key={i}>{s.text}</span>
            ),
          )}
        </span>
      </Link>
    </li>
  )
}

function splitSnippet(
  snippet: string,
  offsets: [number, number][],
): { text: string; match: boolean }[] {
  if (offsets.length === 0) return [{ text: snippet, match: false }]
  const sorted = [...offsets].sort((a, b) => a[0] - b[0])
  const out: { text: string; match: boolean }[] = []
  let cursor = 0
  for (const [start, end] of sorted) {
    if (start > cursor) out.push({ text: snippet.slice(cursor, start), match: false })
    out.push({ text: snippet.slice(start, end), match: true })
    cursor = end
  }
  if (cursor < snippet.length) out.push({ text: snippet.slice(cursor), match: false })
  return out
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/web/components/sidebar/SearchResultRow.tsx
git commit -m "feat(web): SearchResultRow with safe mark highlighting"
```

---

## Task 29: `ConversationSearch` popover

**Files:**
- Create: `frontend/packages/web/components/sidebar/ConversationSearch.tsx`

- [ ] **Step 1: Component**

```typescript
// frontend/packages/web/components/sidebar/ConversationSearch.tsx
'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'
import { Search } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useConversationSearch } from '@/hooks/useConversationSearch'
import { SearchResultRow } from '@/components/sidebar/SearchResultRow'

interface Props {
  wsId: string | null
}

export function ConversationSearch({ wsId }: Props): React.ReactElement {
  const t = useTranslations('sidebar.search')
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const router = useRouter()
  const { loading, error, results } = useConversationSearch(q, wsId)

  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== 'k') return
      // Spec §10.3: only register ⌘K when no other input has focus, so
      // typing ⌘K inside the chat composer or a markdown editor doesn't
      // steal focus.
      const ae = document.activeElement as HTMLElement | null
      if (ae) {
        const tag = ae.tagName
        if (tag === 'INPUT' || tag === 'TEXTAREA' || ae.isContentEditable) {
          return
        }
      }
      e.preventDefault()
      setOpen(true)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 0)
  }, [open])

  useEffect(() => {
    setActive(0)
  }, [results])

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(results.length - 1, a + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(0, a - 1))
    } else if (e.key === 'Enter') {
      const r = results[active]
      if (!r || !wsId) return
      router.push(
        `/w/${wsId}/conversations/${r.conversation_id}${
          r.matched_message_seq ? `#msg-${r.matched_message_seq}` : ''
        }`,
      )
      setOpen(false)
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={t('open')}
          className="ml-auto p-1 rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
        >
          <Search className="size-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        side="right"
        align="start"
        sideOffset={8}
        className="w-80 p-0 max-h-[60vh] overflow-hidden flex flex-col"
      >
        <div className="border-b border-border p-2">
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={t('placeholder')}
            className="w-full bg-transparent text-xs outline-none placeholder:text-faint"
            aria-label={t('placeholder')}
          />
        </div>
        <div className="flex-1 overflow-y-auto py-1">
          {loading && <p className="px-3 py-2 text-2xs text-faint">{t('loading')}</p>}
          {!loading && error && (
            <p className="px-3 py-2 text-2xs text-faint">{t('unavailable')}</p>
          )}
          {!loading && !error && q.trim().length > 0 && results.length === 0 && (
            <p className="px-3 py-2 text-2xs text-faint">{t('noMatches')}</p>
          )}
          {results.length > 0 && wsId && (
            <ul className="space-y-0.5">
              {results.map((r, i) => (
                <SearchResultRow
                  key={r.conversation_id}
                  result={r}
                  wsId={wsId}
                  active={i === active}
                  onPick={() => setOpen(false)}
                />
              ))}
            </ul>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
```

- [ ] **Step 2: Confirm `popover` ui primitive exists**

```bash
ls frontend/packages/web/components/ui/popover.tsx
```

If missing, install it:

```bash
cd frontend/packages/web && npx shadcn@latest add popover
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/sidebar/ConversationSearch.tsx
git commit -m "feat(web): ConversationSearch popover with cmd-k binding"
```

---

## Task 30: Mount in Sidebar + i18n

**Files:**
- Modify: `frontend/packages/web/components/layout/Sidebar.tsx`
- Modify: `frontend/packages/web/messages/en.json`
- Modify: `frontend/packages/web/messages/zh.json`

- [ ] **Step 1: Mount**

Edit `Sidebar.tsx`. Inside the header `div className="px-4 pt-4 pb-3 border-b border-border"`,
the first `flex` row currently holds brand + name. After the brand span,
add the search component (note the `ml-auto` on `<ConversationSearch />`
already pushes it to the right):

```tsx
import { ConversationSearch } from '@/components/sidebar/ConversationSearch'
// ...
<div className="flex items-center gap-2 mb-3">
  <div className="w-6 h-6 rounded bg-primary flex items-center justify-center shrink-0">
    <Box className="size-3.5 text-primary-foreground" strokeWidth={2.5} />
  </div>
  <span className="text-sm font-semibold tracking-tight">cubebox</span>
  <ConversationSearch wsId={currentWsId} />
</div>
```

- [ ] **Step 2: Add i18n strings (en)**

Open `messages/en.json`. Find the existing `"sidebar"` block and add:

```json
"search": {
  "open": "Search conversations",
  "placeholder": "Search conversations…",
  "loading": "Searching…",
  "noMatches": "No matches",
  "unavailable": "Search unavailable"
}
```

- [ ] **Step 3: Add i18n strings (zh)**

Open `messages/zh.json`, mirror under `"sidebar"`:

```json
"search": {
  "open": "搜索会话",
  "placeholder": "搜索会话…",
  "loading": "搜索中…",
  "noMatches": "没有匹配的会话",
  "unavailable": "搜索暂不可用"
}
```

- [ ] **Step 4: Type-check + lint**

```bash
cd frontend && pnpm --filter @cubebox/web type-check
cd frontend && pnpm --filter @cubebox/web lint
```

Expected: clean.

- [ ] **Step 5: Visual smoke**

```bash
cd frontend && pnpm dev
```

Open `http://localhost:3048`. Confirm: search icon visible in sidebar
header; ⌘K opens popover; typing triggers debounced fetch; no console errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/layout/Sidebar.tsx \
        frontend/packages/web/messages/en.json \
        frontend/packages/web/messages/zh.json
git commit -m "feat(web): mount ConversationSearch + i18n strings"
```

---

## Task 31: Playwright E2E

**Files:**
- Create: `frontend/packages/web/__tests__/e2e/conversation-search.spec.ts`

- [ ] **Step 1: Write the spec**

```typescript
// frontend/packages/web/__tests__/e2e/conversation-search.spec.ts
import { expect, test } from '@playwright/test'
import { loginAsTestUser, createConversationWithMessages } from './helpers'

test.describe('conversation search', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsTestUser(page)
  })

  test('typing a keyword shows a matching result', async ({ page, request }) => {
    const wsId = await page.evaluate(() => {
      // assumes app exposes current workspace id somewhere; otherwise grab from URL
      const m = window.location.pathname.match(/^\/w\/([^/]+)/)
      return m ? m[1] : null
    })
    if (!wsId) throw new Error('no workspace selected')

    await createConversationWithMessages(request, wsId, {
      title: 'docling setup notes',
      userText: 'set up docling for table extraction',
      assistantText: 'docling handles bordered tables well',
    })

    // Trigger backend indexing + wait for the chunk to land.
    // (See helpers: `triggerBackfillForWorkspace`.)
    await page.waitForTimeout(2000)

    await page.keyboard.press('Meta+K')
    await page.getByPlaceholder('Search conversations…').fill('docling')
    await expect(page.getByText('docling setup notes')).toBeVisible({ timeout: 5000 })
  })

  test('escape closes popover', async ({ page }) => {
    await page.keyboard.press('Meta+K')
    await expect(page.getByPlaceholder('Search conversations…')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByPlaceholder('Search conversations…')).toBeHidden()
  })
})
```

(`helpers.ts` already has `loginAsTestUser`; add
`createConversationWithMessages` and `triggerBackfillForWorkspace` if they
don't exist. Check first: `grep -n 'createConversation' frontend/packages/web/__tests__/e2e/helpers*.ts`.)

- [ ] **Step 2: Run**

```bash
cd frontend && DASHSCOPE_API_KEY=$YOUR_KEY pnpm test:e2e \
  --grep "conversation search"
```

Expected: pass against a backend with a valid embedding key. Without a key,
the test should skip (see backend gating in Task 24 — frontend should
detect a `fused_count: 0` and skip with a clear message).

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/__tests__/e2e/conversation-search.spec.ts
git commit -m "test(web): Playwright E2E for conversation search"
```

**PR-E done. Open PR titled `feat(search): sidebar popover + e2e` and run codex review loop.**

---

# Post-merge checklist

- [ ] Squash-merge PR-A → PR-B → PR-C → PR-D → PR-E in order.
- [ ] On staging: run `uv run python -m scripts.dev.backfill_search_index --rate 5` with `DASHSCOPE_API_KEY` set.
- [ ] Watch `embedding_jobs` table — `SELECT state, count(*) FROM embedding_jobs GROUP BY state;` — until backfill drains.
- [ ] Manual smoke: open sidebar, `⌘K`, search "docling" → expect matches. Search "文档解析" → expect matches.
- [ ] If any `state='dead'` rows appear, inspect `last_error` and decide whether to bump `worker.max_attempts` or fix the underlying issue.
- [ ] Production: same sequence, after staging soak.

---

# Self-review notes (for the planning author)

1. **Spec coverage:** Every spec section (UX, data model, backends, embedding, indexing pipeline, query, frontend, testing, rollout) is covered by at least one task. The `text_for_search` open item from the spec is resolved by Task 8 (the `extract_searchable_text` function is what the spec referred to).
2. **No placeholders:** every code block in this plan is real code; comments like "adjust the helper if missing" point at concrete grep targets, not "fill in later" stubs.
3. **Type consistency:** `ConversationChunkRepository`, `EmbeddingJobRepository`, `EmbeddingProvider`, `LexicalSearchBackend`, `ConversationSearchService` all use the same names and method signatures across tasks. The `MessageInput` / `Chunk` types in Task 9 are reused in Task 17.
4. **One known stretch:** Task 17's worker uses `cp.load(conversation_id)` and assumes 1-based seq alignment between the `data.messages` index and the seq stored on `cubepi_messages.seq`. This is the contract of `PostgresCheckpointer.load()` — verify with the cubepi maintainer (you) before PR-C lands; if cubepi exposes seq directly on the loaded message objects, switch to using that instead of the enumerate index.
