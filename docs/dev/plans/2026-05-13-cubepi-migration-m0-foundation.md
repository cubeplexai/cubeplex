# cubepi Migration M0 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the dual-track foundation in cubeplex for cubepi-based main agent: add cubepi as a path dependency, run alembic migration creating cubepi's Postgres tables (64 hash partitions), add `agents/checkpointer_pi.py` thin wrapper around `cubepi.PostgresCheckpointer`, add `LLMFactory.build_cubepi_provider()`, add `CUBEPLEX_AGENTS__RUNTIME` flag plumbing. After M0, both langgraph and cubepi runtime paths can coexist; later milestones flesh out the cubepi path; M6 deletes langgraph.

**Architecture:** No behavior change for users with `runtime=langgraph` (the default). The flag is consumed by call sites added in M1+ (in M0 we only stand up the prerequisites: deps, DB schema, factory methods, config). Everything new is additive — no existing file gets a destructive change in M0.

**Tech Stack:** uv path dep, Alembic, SQLAlchemy 2.0, asyncpg, pydantic-settings (dynaconf-backed), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-05-13-cubepi-main-agent-migration-design.md` (Spec B)

**Companion plan (cubepi side):** `~/cubepi/docs/plans/2026-05-13-cubepi-cubeplex-readiness-plan.md` (Plan A)

**Prerequisite:** Plan A D1 (PostgresCheckpointer), D5 (Message.metadata), D9 ([postgres] extra) must be implemented in cubepi (path dep can pick them up without releasing).

---

## File Map

### Files to modify

| File | What changes |
|---|---|
| `backend/pyproject.toml` | Add `cubepi[postgres,mcp]` via `[tool.uv.sources]` path dep |
| `backend/alembic/env.py` | Import `cubepi.checkpointer.postgres.models.cubepi_metadata`; set `target_metadata = [SQLModel.metadata, cubepi_metadata]` |
| `backend/cubeplex/config.py` | Add `AgentRuntimeConfig.runtime: Literal["langgraph","cubepi"]` |
| `backend/cubeplex/llm/factory.py` | Add `build_cubepi_provider(provider_config)` method routing by `api` field; keep existing `build_langchain_model()` |
| `backend/config.development.yaml` | Add `agents.runtime: "langgraph"` (explicit default) |
| `backend/config.test.yaml` | Add `agents.runtime: "cubepi"` (CI uses cubepi path; tests verify cubepi works) |

### Files to create

| File | Purpose |
|---|---|
| `backend/alembic/versions/<rev>_add_cubepi_checkpointer_tables.py` | autogen + manual partition DDL + version row |
| `backend/cubeplex/agents/checkpointer_pi.py` | Thin wrapper around `cubepi.PostgresCheckpointer` with cubeplex connection pooling |
| `backend/tests/unit/test_agent_runtime_config.py` | Unit test for `AgentRuntimeConfig` |
| `backend/tests/unit/test_llm_factory_cubepi.py` | Unit tests for `build_cubepi_provider` |
| `backend/tests/e2e/test_cubepi_checkpointer_integration.py` | E2E test the alembic-created schema works with PostgresCheckpointer |

---

## Pre-flight

### Task M0.0: Verify worktree + cubepi readiness

**Files:** none

- [ ] **Step 1: Verify in worktree**

Run: `pwd`
Expected: `/home/chris/cubeplex/.worktrees/feat/integrate-cubepi`

Run: `cat .worktree.env | grep CUBEPLEX_API__PORT`
Expected: a non-default port (NOT 8000).

- [ ] **Step 2: Verify cubepi has Plan A D1+D5+D9 implemented**

Run: `cd ~/cubepi && git log --oneline | head -20 && grep -l PostgresCheckpointer cubepi/checkpointer/postgres/*.py`
Expected: see `PostgresCheckpointer` defined and recent commits referencing D1/D5/D9. If not present, **STOP** — Plan A is the dependency; finish it first.

- [ ] **Step 3: Verify baseline test pass on this branch**

Run: `cd /home/chris/cubeplex/.worktrees/feat/integrate-cubepi/backend && uv run pytest tests/ -q --tb=no`
Expected: all current tests pass. Note the count — every M0 task must keep it non-decreasing.

---

## Task M0.1: Add cubepi path dependency

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Inspect current pyproject.toml structure**

Run: `head -50 backend/pyproject.toml`
Expected: see existing `dependencies = [...]` and `[tool.uv.sources]` (if present).

- [ ] **Step 2: Add `cubepi` to dependencies with extras**

Edit `backend/pyproject.toml`. In the `dependencies` array, add:

```toml
dependencies = [
    # ... existing deps ...
    "cubepi[postgres,mcp]",
]
```

- [ ] **Step 3: Configure path source**

Add (or extend if exists) `[tool.uv.sources]`:

```toml
[tool.uv.sources]
cubepi = { path = "/home/chris/cubepi", editable = true }
```

- [ ] **Step 4: Sync deps**

Run: `cd backend && uv sync --all-extras`
Expected: installs cubepi from local path. Verify with:

```bash
uv pip list | grep cubepi
```
Expected: `cubepi 0.2.x` (or whatever the local version is, pointing to ~/cubepi).

- [ ] **Step 5: Smoke import**

Run: `cd backend && uv run python -c "from cubepi.checkpointer.postgres import PostgresCheckpointer, cubepi_metadata; print('ok', list(cubepi_metadata.tables.keys()))"`
Expected: `ok ['cubepi_threads', 'cubepi_messages', 'cubepi_schema_version']`

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "feat(deps): add cubepi[postgres,mcp] as path dependency

cubepi is the eventual replacement for LangGraph (see Spec B). During
migration, both run side-by-side controlled by config.agents.runtime
flag. path dep points to local ~/cubepi until cubepi releases."
```

---

## Task M0.2: Wire cubepi metadata into alembic env

**Files:**
- Modify: `backend/alembic/env.py`
- Test: `backend/tests/unit/test_alembic_env.py` (smoke import)

- [ ] **Step 1: Write smoke test**

Create `backend/tests/unit/test_alembic_env.py`:

```python
"""Smoke test: alembic env.py loads with cubepi_metadata included."""

import importlib.util
from pathlib import Path


def test_env_module_loads_with_cubepi_metadata() -> None:
    """alembic env.py must import cubepi_metadata and include it in target_metadata."""
    env_path = Path(__file__).parent.parent.parent / "alembic" / "env.py"
    assert env_path.exists()

    text = env_path.read_text()
    # The import must be present
    assert "cubepi.checkpointer.postgres" in text, (
        "alembic env.py must import from cubepi.checkpointer.postgres"
    )
    assert "cubepi_metadata" in text, (
        "alembic env.py must reference cubepi_metadata"
    )
    # target_metadata must be a list, not a single SQLModel.metadata
    assert "target_metadata = [" in text, (
        "target_metadata must be a list to combine cubeplex + cubepi metadata"
    )
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd backend && uv run pytest tests/unit/test_alembic_env.py -v`
Expected: 1 fail — alembic env.py doesn't yet reference cubepi.

- [ ] **Step 3: Edit alembic env.py**

Edit `backend/alembic/env.py`. Find the imports section (top of file) and the `target_metadata = ...` line:

```python
# Existing imports
from cubeplex.models import (  # noqa: F401
    # ... existing model imports ...
)
from sqlmodel import SQLModel

# NEW: import cubepi metadata so autogen sees its tables
from cubepi.checkpointer.postgres.models import (  # noqa: F401
    cubepi_metadata,
)

# Existing line was:
# target_metadata = SQLModel.metadata
# Replace with:
target_metadata = [SQLModel.metadata, cubepi_metadata]
```

- [ ] **Step 4: Run smoke test**

Run: `cd backend && uv run pytest tests/unit/test_alembic_env.py -v`
Expected: PASS.

- [ ] **Step 5: Run `alembic check` to verify env loads**

Run: `cd backend && uv run alembic check`
Expected: either "no new revisions needed" OR "new upgrade operations detected" listing CREATE TABLE for `cubepi_threads`, `cubepi_messages`, `cubepi_schema_version`. The "detected" output is what we want — confirms autogen sees the new tables.

- [ ] **Step 6: Run full suite**

Run: `cd backend && uv run pytest tests/ -q --tb=no`
Expected: baseline + 1.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/env.py backend/tests/unit/test_alembic_env.py
git commit -m "feat(alembic): include cubepi_metadata in target_metadata

target_metadata is now a list [SQLModel.metadata, cubepi_metadata]
so autogen produces DDL for cubepi tables alongside cubeplex tables.
alembic supports list targets natively."
```

---

## Task M0.3: Generate alembic migration for cubepi tables

**Files:**
- Create: `backend/alembic/versions/<rev>_add_cubepi_checkpointer_tables.py`

- [ ] **Step 1: Confirm DB is at current head**

Run: `cd backend && uv run alembic current`
Expected: shows current head revision (e.g. `4be6ed892e02 (head)`).

If migrations haven't been run yet on this worktree's DB, run:
```bash
uv run alembic upgrade head
```

- [ ] **Step 2: Generate the autogen migration**

Run: `cd backend && uv run alembic revision --autogenerate -m "add cubepi checkpointer tables"`
Expected: creates a new file under `backend/alembic/versions/` with `upgrade()` and `downgrade()`. Note the filename.

- [ ] **Step 3: Inspect the autogen output**

Open the generated file. Expected content (paraphrased):

```python
def upgrade() -> None:
    op.create_table(
        "cubepi_threads",
        # ... columns ...
    )
    op.create_table(
        "cubepi_messages",
        # ... columns ...
        # likely WITHOUT postgresql_partition_by clause — autogen may not emit it
    )
    op.create_table(
        "cubepi_schema_version",
        # ...
    )
    # ... GIN index for metadata ...
```

Note: alembic autogenerate may NOT emit `postgresql_partition_by` —
the partition declaration on the parent table needs to be in
`op.create_table(..., postgresql_partition_by="HASH (thread_id)")`.

- [ ] **Step 4: Patch the migration for partitioning + helpers**

Edit the generated migration. Two changes:

1. Add `postgresql_partition_by="HASH (thread_id)"` kwarg to the
   `op.create_table("cubepi_messages", ...)` call.

2. After all table creates, add helper calls:

```python
from cubepi.checkpointer.postgres.alembic_helpers import (
    create_message_partitions_op,
    write_schema_version_op,
)


def upgrade() -> None:
    # ... autogen op.create_table calls (with postgresql_partition_by added) ...

    # cubepi-provided helpers:
    op.execute(create_message_partitions_op())
    op.execute(write_schema_version_op())


def downgrade() -> None:
    # In reverse — drop the 64 partitions first, then parent table.
    # Helper not provided; write inline:
    for i in range(64):
        op.execute(f"DROP TABLE IF EXISTS cubepi_messages_p{i:02d}")
    op.drop_table("cubepi_schema_version")
    op.drop_table("cubepi_messages")
    op.drop_table("cubepi_threads")
```

- [ ] **Step 5: Apply the migration**

Run: `cd backend && uv run alembic upgrade head`
Expected: applies the migration. Verify in DB:

```bash
PGOPTIONS="-c search_path=public" psql -h localhost -p 5432 \
  -d cubeplex_feat_integrate_cubepi \
  -c "\dt cubepi_*"
```

Expected: lists `cubepi_threads`, `cubepi_messages`,
`cubepi_messages_p00` through `cubepi_messages_p63`,
`cubepi_schema_version`.

```bash
psql -h localhost -p 5432 -d cubeplex_feat_integrate_cubepi \
  -c "SELECT version FROM cubepi_schema_version"
```

Expected: `1`.

- [ ] **Step 6: Test downgrade path**

Run: `cd backend && uv run alembic downgrade -1`
Expected: drops all cubepi tables cleanly.

Run: `cd backend && uv run alembic upgrade head`
Expected: re-applies.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/
git commit -m "feat(alembic): add migration for cubepi checkpointer tables

- cubepi_threads + cubepi_messages (HASH partition by thread_id, 64 partitions)
- cubepi_schema_version pinned to 1 via cubepi.checkpointer.postgres.alembic_helpers
- GIN index on cubepi_messages.metadata for jsonb_path_ops
- downgrade drops all child partitions + parent + version table"
```

---

## Task M0.4: Add `AgentRuntimeConfig` flag

**Files:**
- Modify: `backend/cubeplex/config.py`
- Modify: `backend/config.development.yaml`
- Modify: `backend/config.test.yaml`
- Test: `backend/tests/unit/test_agent_runtime_config.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/unit/test_agent_runtime_config.py`:

```python
"""Tests for AgentRuntimeConfig flag."""

import pytest

from cubeplex.config import AgentRuntimeConfig


def test_default_runtime_is_langgraph() -> None:
    cfg = AgentRuntimeConfig()
    assert cfg.runtime == "langgraph"


def test_runtime_accepts_cubepi() -> None:
    cfg = AgentRuntimeConfig(runtime="cubepi")
    assert cfg.runtime == "cubepi"


def test_runtime_rejects_invalid() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentRuntimeConfig(runtime="something-else")


def test_global_config_exposes_agents_runtime() -> None:
    """The global config object must have config.agents.runtime accessible."""
    from cubeplex.config import config
    assert hasattr(config, "agents")
    assert hasattr(config.agents, "runtime")
    assert config.agents.runtime in ("langgraph", "cubepi")
```

- [ ] **Step 2: Run tests to verify failures**

Run: `cd backend && uv run pytest tests/unit/test_agent_runtime_config.py -v`
Expected: 4 failures — `AgentRuntimeConfig` doesn't exist.

- [ ] **Step 3: Add `AgentRuntimeConfig` to `config.py`**

In `backend/cubeplex/config.py`, find existing `BaseSettings` model classes (e.g. `LLMConfig`, `AuthConfig`). Add:

```python
from typing import Literal


class AgentRuntimeConfig(BaseModel):
    """Which agent runtime to use.

    Set via CUBEPLEX_AGENTS__RUNTIME env var or config.<env>.yaml's
    `agents.runtime` key. Default is `langgraph` (current production).
    `cubepi` enables the in-development cubepi-based runtime (see Spec B).
    """
    runtime: Literal["langgraph", "cubepi"] = "langgraph"
```

Find the main `Config` / `Settings` class and add the `agents` field:

```python
class Config(BaseModel):
    # ... existing fields ...
    agents: AgentRuntimeConfig = Field(default_factory=AgentRuntimeConfig)
```

If cubeplex's config layer uses dynaconf, ensure the field is loaded
via dynaconf's settings (consult existing patterns in the file).

- [ ] **Step 4: Update YAML configs**

`backend/config.development.yaml` — add:
```yaml
agents:
  runtime: "langgraph"
```

`backend/config.test.yaml` — add:
```yaml
agents:
  runtime: "cubepi"
```

(Test config uses cubepi so the cubepi-path code paths get exercise
in CI as M1+ lands code there.)

- [ ] **Step 5: Run tests**

Run: `cd backend && uv run pytest tests/unit/test_agent_runtime_config.py -v`
Expected: 4 pass.

- [ ] **Step 6: Run full suite**

Run: `cd backend && uv run pytest tests/ -q --tb=no`
Expected: baseline + 4.

- [ ] **Step 7: Commit**

```bash
git add backend/cubeplex/config.py backend/config.*.yaml backend/tests/unit/test_agent_runtime_config.py
git commit -m "feat(config): add AgentRuntimeConfig.runtime flag (langgraph|cubepi)

Controls which agent runtime path is used. Default langgraph; test
config flips to cubepi so CI exercises the new path as it's built out.
Set via CUBEPLEX_AGENTS__RUNTIME env or config.yaml agents.runtime."
```

---

## Task M0.5: Add `checkpointer_pi.py` thin wrapper

**Files:**
- Create: `backend/cubeplex/agents/checkpointer_pi.py`
- Test: `backend/tests/e2e/test_cubepi_checkpointer_integration.py`

- [ ] **Step 1: Write integration test (failing)**

Create `backend/tests/e2e/test_cubepi_checkpointer_integration.py`:

```python
"""Integration test: cubeplex uses cubepi.PostgresCheckpointer against
the alembic-created schema in the test database.

Requires alembic upgrade head to have run on the test DB. The test
fixture in conftest handles that.
"""

import pytest
from cubepi.providers.base import TextContent, UserMessage

from cubeplex.agents.checkpointer_pi import init_cubepi_checkpointer
from cubeplex.config import config


@pytest.mark.asyncio
async def test_cubepi_checkpointer_round_trip_against_real_schema() -> None:
    """Connecting cubepi.PostgresCheckpointer to the cubeplex test DB
    must succeed (schema version check passes) and round-trip messages."""
    dsn = (
        f"postgresql://{config.database.user}:{config.database.password}"
        f"@{config.database.host}:{config.database.port}/{config.database.name}"
    )
    async with init_cubepi_checkpointer(dsn) as cp:
        msg = UserMessage(
            content=[TextContent(text="hello")],
            metadata={"test": True},
        )
        await cp.append("t-m0-integration", [msg])
        data = await cp.load("t-m0-integration")
        assert data is not None
        assert len(data.messages) == 1
        assert data.messages[0].metadata == {"test": True}
```

- [ ] **Step 2: Run test — confirm failure**

Run: `cd backend && uv run pytest tests/e2e/test_cubepi_checkpointer_integration.py -v`
Expected: import error — `init_cubepi_checkpointer` doesn't exist.

- [ ] **Step 3: Write checkpointer_pi.py**

Create `backend/cubeplex/agents/checkpointer_pi.py`:

```python
"""cubepi-backed Postgres checkpointer for cubeplex.

Thin wrapper around cubepi.PostgresCheckpointer. Owns the connection
pool lifecycle and exposes a context-manager init for use in cubeplex's
agent factory.

This module is invoked when config.agents.runtime == "cubepi". For
runtime == "langgraph", cubeplex/agents/checkpointer.py (the existing
LangGraph AsyncPostgresSaver wrapper) is used instead.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from cubepi.checkpointer.postgres import PostgresCheckpointer

from cubeplex.config import config as _config


def _build_dsn() -> str:
    """Construct the Postgres DSN from cubeplex config."""
    db = _config.database
    return (
        f"postgresql://{db.user}:{db.password}@{db.host}:{db.port}/{db.name}"
    )


@asynccontextmanager
async def init_cubepi_checkpointer(
    dsn: str | None = None,
    *,
    min_pool_size: int = 1,
    max_pool_size: int = 10,
) -> AsyncIterator[PostgresCheckpointer]:
    """Open a cubepi.PostgresCheckpointer for cubeplex's DB.

    Usage::

        async with init_cubepi_checkpointer() as cp:
            agent = Agent(..., checkpointer=cp)
            ...

    Args:
        dsn: explicit Postgres DSN; defaults to cubeplex config.
        min_pool_size / max_pool_size: asyncpg pool sizing.

    Yields:
        Open PostgresCheckpointer; schema version verified on entry.
    """
    dsn = dsn or _build_dsn()
    cp = PostgresCheckpointer(
        dsn=dsn,
        min_pool_size=min_pool_size,
        max_pool_size=max_pool_size,
    )
    async with cp:
        yield cp
```

- [ ] **Step 4: Run test**

Run: `cd backend && uv run pytest tests/e2e/test_cubepi_checkpointer_integration.py -v`
Expected: PASS — assuming alembic has run on the test DB.

If it fails with `CubepiSchemaUninitialized`, the alembic upgrade
didn't reach the new revision on the test DB; run:
```bash
cd backend && uv run alembic upgrade head
```
on the test DB and re-run.

- [ ] **Step 5: Run full suite**

Run: `cd backend && uv run pytest tests/ -q --tb=no`
Expected: baseline + 1.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/agents/checkpointer_pi.py backend/tests/e2e/test_cubepi_checkpointer_integration.py
git commit -m "feat(agents): add checkpointer_pi.py wrapping cubepi.PostgresCheckpointer

Thin async-context wrapper that:
- builds DSN from cubeplex config
- opens cubepi.PostgresCheckpointer with the cubeplex-shaped pool config
- relies on cubepi's __aenter__ to verify cubepi_schema_version row

Consumed in M1 when agent factory dispatches by runtime flag."
```

---

## Task M0.6: Add `build_cubepi_provider` in LLMFactory

**Files:**
- Modify: `backend/cubeplex/llm/factory.py`
- Test: `backend/tests/unit/test_llm_factory_cubepi.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/unit/test_llm_factory_cubepi.py`:

```python
"""Unit tests for LLMFactory.build_cubepi_provider (M0)."""

import pytest

from cubeplex.llm.factory import LLMFactory
from cubeplex.llm.config import LLMConfig, ModelConfig, ProviderConfig


def _mk_factory(provider_configs: list[ProviderConfig]) -> LLMFactory:
    return LLMFactory(
        llm_config=LLMConfig(
            default_model="test/test",
            providers={p.name: p for p in provider_configs},
            models={},
        )
    )


def test_build_cubepi_provider_routes_anthropic() -> None:
    from cubepi.providers.anthropic import AnthropicProvider
    factory = _mk_factory([
        ProviderConfig(
            name="anthropic",
            api="anthropic",
            base_url="https://api.anthropic.com",
            api_key="sk-test",
        ),
    ])
    provider = factory.build_cubepi_provider(
        factory.llm_config.providers["anthropic"]
    )
    assert isinstance(provider, AnthropicProvider)


def test_build_cubepi_provider_routes_openai_completions() -> None:
    from cubepi.providers.openai import OpenAIProvider
    factory = _mk_factory([
        ProviderConfig(
            name="deepseek",
            api="openai-completions",
            base_url="https://api.deepseek.com",
            api_key="sk-test",
        ),
    ])
    provider = factory.build_cubepi_provider(
        factory.llm_config.providers["deepseek"]
    )
    assert isinstance(provider, OpenAIProvider)


def test_build_cubepi_provider_routes_openai_responses() -> None:
    from cubepi.providers.openai_responses import OpenAIResponsesProvider
    factory = _mk_factory([
        ProviderConfig(
            name="oai",
            api="openai-responses",  # NEW api literal we accept for cubepi route
            base_url="https://api.openai.com",
            api_key="sk-test",
        ),
    ])
    provider = factory.build_cubepi_provider(
        factory.llm_config.providers["oai"]
    )
    assert isinstance(provider, OpenAIResponsesProvider)


def test_build_cubepi_provider_unknown_api_raises() -> None:
    factory = _mk_factory([
        ProviderConfig(
            name="weird",
            api="some-unknown-api",
            base_url="https://x.com",
            api_key="sk",
        ),
    ])
    with pytest.raises(ValueError, match="unsupported api"):
        factory.build_cubepi_provider(
            factory.llm_config.providers["weird"]
        )


def test_build_cubepi_provider_anthropic_accepts_cache_policy() -> None:
    """When constructing Anthropic provider, factory must wire in cubeplex's
    CacheMarkerPolicy. We don't have the cubeplex policy yet (M1 task), so
    for M0 just verify the keyword is accepted."""
    factory = _mk_factory([
        ProviderConfig(
            name="anthropic",
            api="anthropic",
            base_url="https://api.anthropic.com",
            api_key="sk-test",
        ),
    ])
    provider = factory.build_cubepi_provider(
        factory.llm_config.providers["anthropic"],
        cache_policy=None,  # M1 will pass CubeplexCacheMarkerPolicy()
    )
    # Default policy preserves v0.2 behavior
    from cubepi.providers.anthropic import DefaultCacheMarkerPolicy
    assert isinstance(provider._cache_policy, DefaultCacheMarkerPolicy)
```

- [ ] **Step 2: Run tests to verify failures**

Run: `cd backend && uv run pytest tests/unit/test_llm_factory_cubepi.py -v`
Expected: 5 failures — `build_cubepi_provider` doesn't exist.

- [ ] **Step 3: Implement `build_cubepi_provider`**

In `backend/cubeplex/llm/factory.py`, add:

```python
from typing import Any

# At top of file or near other imports
from cubepi.providers.anthropic import (
    AnthropicProvider,
    CacheMarkerPolicy,
)
from cubepi.providers.openai import OpenAIProvider
from cubepi.providers.openai_responses import OpenAIResponsesProvider


class LLMFactory:
    # ... existing methods ...

    def build_cubepi_provider(
        self,
        provider_config: ProviderConfig,
        *,
        cache_policy: CacheMarkerPolicy | None = None,
    ) -> Any:
        """Build a cubepi.Provider instance from a ProviderConfig.

        Routes by `provider_config.api`:
          - "anthropic"           → cubepi.AnthropicProvider
          - "openai-completions"  → cubepi.OpenAIProvider
          - "openai-responses"    → cubepi.OpenAIResponsesProvider

        cache_policy (Anthropic only): forwarded to AnthropicProvider.
        If None, AnthropicProvider's DefaultCacheMarkerPolicy is used.

        For openai-compatible endpoints with reasoning quirks, pass
        payload_quirks via a future wrapper (not in M0).
        """
        api = provider_config.api
        if api == "anthropic":
            return AnthropicProvider(
                api_key=provider_config.api_key,
                base_url=provider_config.base_url,
                cache_policy=cache_policy,
            )
        if api == "openai-completions":
            return OpenAIProvider(
                api_key=provider_config.api_key,
                base_url=provider_config.base_url,
            )
        if api == "openai-responses":
            return OpenAIResponsesProvider(
                api_key=provider_config.api_key,
                base_url=provider_config.base_url,
            )
        raise ValueError(f"unsupported api for cubepi provider: {api!r}")
```

If `ProviderConfig.api` doesn't currently accept the literal
`"openai-responses"`, extend its `Literal` type:

```python
# cubeplex/llm/config.py (find ProviderConfig)
class ProviderConfig(BaseModel):
    # ...
    api: Literal["anthropic", "openai-completions", "openai-responses"]
    # ...
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/unit/test_llm_factory_cubepi.py -v`
Expected: 5 pass.

- [ ] **Step 5: Run full suite**

Run: `cd backend && uv run pytest tests/ -q --tb=no`
Expected: baseline + 5.

- [ ] **Step 6: Commit**

```bash
git add backend/cubeplex/llm/factory.py backend/cubeplex/llm/config.py backend/tests/unit/test_llm_factory_cubepi.py
git commit -m "feat(llm): add LLMFactory.build_cubepi_provider routing by api

Builds cubepi.{AnthropicProvider, OpenAIProvider, OpenAIResponsesProvider}
from a ProviderConfig based on the api field. Accepts optional cache_policy
to pass through to AnthropicProvider (cubeplex's policy is wired in M1).

build_langchain_model (existing) remains the path for langgraph runtime."
```

---

## Task M0.7: Verify the full M0 surface together

**Files:** none new

- [ ] **Step 1: Run all M0 tests**

Run: `cd backend && uv run pytest tests/unit/test_alembic_env.py tests/unit/test_agent_runtime_config.py tests/unit/test_llm_factory_cubepi.py tests/e2e/test_cubepi_checkpointer_integration.py -v`
Expected: all 14 pass (1 + 4 + 5 + 1, plus a couple of detail tests).

- [ ] **Step 2: Run full test suite**

Run: `cd backend && uv run pytest tests/ -q --tb=no`
Expected: baseline + all new tests pass. No existing tests broken.

- [ ] **Step 3: Run `make check`**

Run: `cd backend && make check`
Expected: format clean, lint clean, type-check clean, all tests pass.

- [ ] **Step 4: Confirm config dispatch ready**

Manual smoke: with `CUBEPLEX_AGENTS__RUNTIME=cubepi` set, verify the
LLMFactory can build a cubepi provider via the config flow:

```bash
cd backend
CUBEPLEX_AGENTS__RUNTIME=cubepi uv run python -c "
from cubeplex.config import config
from cubeplex.llm.factory import LLMFactory
assert config.agents.runtime == 'cubepi'
print('runtime:', config.agents.runtime)
print('factory has build_cubepi_provider:', hasattr(LLMFactory, 'build_cubepi_provider'))
"
```

Expected:
```
runtime: cubepi
factory has build_cubepi_provider: True
```

- [ ] **Step 5: Run alembic doctor**

Run: `./scripts/worktree-env doctor`
Expected: alembic at head (the new revision).

- [ ] **Step 6: Commit checkpoint tag**

```bash
git tag m0-foundation-done
```

(Optional — provides a reference point for M1's start.)

---

## Self-review checklist

After completing all M0 tasks:

- [ ] cubepi is installed in cubeplex's venv (`uv pip list | grep cubepi`) ✅
- [ ] alembic env.py imports cubepi_metadata; autogen sees cubepi tables ✅
- [ ] alembic revision created the 3 base tables + 64 message partitions + schema_version row=1 ✅
- [ ] alembic downgrade cleanly drops everything ✅
- [ ] `cubeplex/agents/checkpointer_pi.py` exists, round-trips a UserMessage against the real test DB ✅
- [ ] `cubeplex/llm/factory.py` has `build_cubepi_provider` routing all 3 api values ✅
- [ ] `config.agents.runtime` accepts `langgraph` | `cubepi`, defaults to `langgraph` ✅
- [ ] `config.test.yaml` sets runtime to `cubepi` for CI ✅
- [ ] All new tests pass; no existing tests regressed ✅
- [ ] `make check` clean ✅
- [ ] No `*_pi.py` files contain stub/placeholder bodies — every function does what its docstring says ✅
- [ ] Nothing actually runs the cubepi runtime path yet — that comes in M1. M0 only stands up the substrate. ✅

## Spec coverage map (Spec B section "M0 — Foundation")

| Spec requirement | Implementing task |
|---|---|
| Add `cubepi[postgres,mcp]` via `[tool.uv.sources]` path | M0.1 |
| alembic env.py imports cubepi_metadata + list target_metadata | M0.2 |
| New alembic revision: autogen + manual partition DDL + version row | M0.3 |
| `cubeplex/agents/checkpointer_pi.py` thin wrapper | M0.5 |
| `LLMFactory.build_cubepi_provider(provider_config)` routing by api | M0.6 |
| `AgentRuntimeConfig.runtime` flag | M0.4 |

All M0 spec items covered. M1 begins when M0 is merged and stable.

## Handoff to M1

M1 will:
- Add `agents/graph_pi.py` (create_cubeplex_cubepi_agent)
- Add `agents/stream_pi.py` (cubepi event → cubeplex SSE translator)
- Add `agents/convert_pi.py` (cubepi.Message ↔ wire format)
- Add `llm/cache_markers_pi.py` (CubeplexCacheMarkerPolicy)
- Dispatch in API route by config.agents.runtime

M1's plan should be written when M0 is merged and stable — see
`docs/superpowers/specs/2026-05-13-cubepi-main-agent-migration-design.md`
§ "M1 — Agent core skeleton".
