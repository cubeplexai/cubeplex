# MySQL → PostgreSQL Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace MySQL with PostgreSQL across the backend (engine, alembic, LangGraph checkpointer, worktree provisioning, CI). Use the official `langgraph-checkpoint-postgres` for thread state and `~/infra/postgresql` (postgres 18.2 on `localhost:5432`) as the dev/test cluster.

**Architecture:** Single driver — `psycopg` v3 — for app, alembic, and the LangGraph checkpointer. App lifespan owns one `AsyncConnectionPool`; an `AsyncPostgresSaver` is built on top once and reused for the process lifetime. Worktree isolation continues via per-worktree databases (`cubeplex_<slug>_dev|test`) on the shared cluster — same naming, different DB engine.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 / SQLModel, alembic, `psycopg[binary,pool]>=3.2`, `langgraph-checkpoint-postgres>=3.0.0`, postgres 18 (Docker via `~/infra/postgresql/docker-compose.yml` for dev; `postgres:18-alpine` service in CI).

**Spec:** `docs/superpowers/specs/2026-04-29-mysql-to-postgres-design.md`.

---

## File structure

Files this plan creates, modifies, or deletes:

| Action | Path | Responsibility |
| --- | --- | --- |
| Create | `.worktrees/feat/postgres-migration/` | Isolated worktree for the migration work |
| Modify | `backend/pyproject.toml` | Drop MySQL deps, add PG deps |
| Modify | `backend/config.yaml` | DB defaults: PG host/port/user |
| Modify | `backend/config.development.yaml` | (if it overrides DB) |
| Modify | `backend/config.test.yaml` | DB defaults for CI/test |
| Modify | `backend/config.production.yaml` | (if it overrides DB) |
| Modify | `backend/.env.example` | Documented env var defaults |
| Modify | `backend/cubeplex/db/engine.py` | `postgresql+psycopg://` URL, drop MySQL pool tuning |
| Modify | `backend/alembic/env.py` | `postgresql+psycopg://` URL |
| Delete | `backend/alembic/versions/*.py` (16 files) | Squash to fresh baseline |
| Create | `backend/alembic/versions/<new_id>_initial_postgres_schema.py` | Single PG baseline migration |
| Modify | `backend/cubeplex/agents/checkpointer.py` | `AsyncPostgresSaver` + module-level `AsyncConnectionPool` lifecycle |
| Modify | `backend/cubeplex/api/app.py` | Replace lifespan checkpointer setup; wire pool open/close |
| Modify | `backend/cubeplex/repositories/invite_token.py` | Update MySQL-flavored comment |
| Modify | `backend/cubeplex/utils/time.py` | Update MySQL-flavored comment |
| Modify | `scripts/worktree-env` | `mysql` CLI → `psql`; PG-style `CREATE/DROP DATABASE` |
| Modify | `.github/workflows/ci.yml` | Replace `mysql` service block with `postgres:18-alpine` |
| Modify | `AGENTS.md` (root) | Worktree section: MySQL schemas → PG databases |
| Modify | `backend/CLAUDE.md` | Pytest marker description |

---

## Execution conventions

- **Worktree:** every step from Task 2 onward runs inside `.worktrees/feat/postgres-migration` (the worktree created in Task 1). Always `cd` to that path before any command, and never assume default ports — always read `.worktree.env` first.
- **Commits:** one commit per task. Use the messages shown in each "Commit" step verbatim.
- **TDD where it bites:** schema/config changes don't have unit tests; the verification is "the integration check passes" (alembic upgrade, e2e, doctor). Where a real unit-testable behavior change exists (worktree-env quoting), there's a test.
- **Pre-commit hooks:** the repo runs `make check` (format/lint/mypy) on commit. Do not pass `--no-verify`. If a hook fails, fix the underlying issue.

---

### Task 1: Create the worktree and bring env files in

**Files:**
- Create: `.worktrees/feat/postgres-migration/`
- Copy in: `backend/.env`, `backend/config.development.local.yaml`

- [ ] **Step 1: Create worktree from latest origin/main**

Run from the **main repo root** (`/home/chris/cubeplex`):

```bash
./scripts/new-worktree feat/postgres-migration
```

Expected: worktree created at `.worktrees/feat/postgres-migration`, branch `feat/postgres-migration` checked out, `worktree-env init` runs, and `.worktree.env` is written with PG-shaped values once Task 8 lands. For now, `init` will still provision **MySQL** schemas (the script change is Task 8). That's fine — those schemas will be cleaned up at the end.

- [ ] **Step 2: Copy env files into the worktree**

```bash
cp backend/.env .worktrees/feat/postgres-migration/backend/.env
cp backend/config.development.local.yaml \
   .worktrees/feat/postgres-migration/backend/config.development.local.yaml
```

If either file does not exist in main, ask the user before continuing — these contain LLM/sandbox secrets needed by the test config.

- [ ] **Step 3: cd into the worktree and confirm location**

```bash
cd .worktrees/feat/postgres-migration
git rev-parse --show-toplevel  # absolute path to this worktree
git branch --show-current      # feat/postgres-migration
cat .worktree.env              # note the allocated ports
```

Record the slot/ports from `.worktree.env`; subsequent steps reference them as `<API_PORT>` (e.g., `8037`) and `<WEB_PORT>` (e.g., `3037`). The DB name will currently be `cubeplex_feat_postgres_migration_dev` / `_test` — same identifiers we'll use on PG.

- [ ] **Step 4: Verify ~/infra/postgresql is up**

```bash
PGPASSWORD=postgres psql -h localhost -U postgres -d postgres -c "SELECT version();"
```

Expected: a `PostgreSQL 18.2 on ...` line. If `psql` is not installed, install it (`sudo apt install postgresql-client`). If the server is not running, `cd ~/infra/postgresql && docker compose up -d`.

- [ ] **Step 5: No commit** — Task 1 is environment setup only, nothing tracked changes.

---

### Task 2: Swap dependencies in `pyproject.toml`

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Edit `backend/pyproject.toml`**

In the `dependencies = [...]` block, **remove** the line:

```toml
"langgraph-checkpoint-mysql[aiomysql]>=3.0.0",
```

**Add** these two lines (alphabetic position is fine; place near `langgraph-checkpoint-postgres` neighbours):

```toml
"langgraph-checkpoint-postgres>=3.0.0",
"psycopg[binary,pool]>=3.2",
```

- [ ] **Step 2: Lock and install**

```bash
cd backend
uv lock
uv sync --all-extras
```

Expected: `aiomysql` and `langgraph-checkpoint-mysql` are uninstalled; `psycopg`, `psycopg-binary`, `psycopg-pool`, `langgraph-checkpoint-postgres` are installed. `uv lock` regenerates `uv.lock`.

- [ ] **Step 3: Sanity import**

```bash
uv run python -c "
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
print('imports OK')
"
```

Expected: `imports OK`. Failure here means the version constraint is wrong — fix before proceeding.

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "chore(deps): replace mysql drivers with psycopg + langgraph-checkpoint-postgres"
```

---

### Task 3: Update config defaults to PG

**Files:**
- Modify: `backend/config.yaml`
- Modify: `backend/config.test.yaml`
- Modify: `backend/config.development.yaml` (only if it has a `database:` block)
- Modify: `backend/config.production.yaml` (only if it has a `database:` block)
- Modify: `backend/.env.example`

- [ ] **Step 1: `backend/config.yaml`**

Replace the existing `database:` block (currently at line ~179) with:

```yaml
  database:
    host: "localhost"
    port: 5432
    user: "postgres"
    password: "postgres"
    name: "cubeplex"
    pool_size: 10
    max_overflow: 20
    echo: false
```

- [ ] **Step 2: `backend/config.test.yaml`**

Replace the existing `database:` block with:

```yaml
  database:
    host: "127.0.0.1"
    port: 5432
    user: "postgres"
    password: "testpass"
    name: "cubeplex_test"
```

- [ ] **Step 3: Check the other two configs**

```bash
grep -n "database:" backend/config.development.yaml backend/config.production.yaml
```

If a `database:` block exists in either file, update its `port` to `5432`, `user` to `postgres`, and adjust `password` consistent with whatever override pattern the file uses. If no block, leave the file alone.

- [ ] **Step 4: `backend/.env.example`**

Replace the database section:

```env
# Database Configuration
CUBEPLEX_DATABASE__HOST=localhost
CUBEPLEX_DATABASE__PORT=5432
CUBEPLEX_DATABASE__USER=postgres
CUBEPLEX_DATABASE__PASSWORD=postgres
CUBEPLEX_DATABASE__NAME=cubeplex
```

Also remove the now-stale comment line `# CUBEPLEX_DATABASE__URL=postgresql://user:password@localhost:5432/cubeplex` (the new defaults make it redundant).

- [ ] **Step 5: Commit**

```bash
git add backend/config.yaml backend/config.test.yaml backend/.env.example
# Add the two extras only if Step 3 modified them:
git add backend/config.development.yaml backend/config.production.yaml 2>/dev/null
git commit -m "chore(config): switch database defaults to PostgreSQL on port 5432"
```

---

### Task 4: Update the SQLAlchemy engine

**Files:**
- Modify: `backend/cubeplex/db/engine.py`

- [ ] **Step 1: Rewrite `_build_database_url`**

Replace lines 16-25 (the function body) with:

```python
def _build_database_url() -> str:
    """Build database URL from individual config fields."""
    host = config.get("database.host", "localhost")
    port = config.get("database.port", 5432)
    user = config.get("database.user", "postgres")
    password = config.get("database.password", "")
    name = config.get("database.name", "cubeplex")
    encoded_password = quote_plus(password)
    return f"postgresql+psycopg://{user}:{encoded_password}@{host}:{port}/{name}"
```

- [ ] **Step 2: Rewrite `get_engine`**

Replace lines 28-45 with:

```python
def get_engine() -> AsyncEngine:
    """Get async database engine."""
    database_url = _build_database_url()
    pool_size = config.get("database.pool_size", 10)
    max_overflow = config.get("database.max_overflow", 20)
    echo = config.get("database.echo", False)

    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        echo=echo,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )
```

Note: `pool_recycle=280` is removed — that was a MySQL-specific guard around the 600s `wait_timeout`. PG has no equivalent default.

- [ ] **Step 3: Smoke-test connectivity from inside the worktree**

```bash
cd backend
PGPASSWORD=postgres psql -h localhost -U postgres -d postgres \
  -v ON_ERROR_STOP=1 \
  -c 'CREATE DATABASE "cubeplex_feat_postgres_migration_dev";' || true
PGPASSWORD=postgres psql -h localhost -U postgres -d postgres \
  -v ON_ERROR_STOP=1 \
  -c 'CREATE DATABASE "cubeplex_feat_postgres_migration_test";' || true
```

(`|| true` because the DBs may not exist yet, and `IF NOT EXISTS` isn't supported on `CREATE DATABASE`. The `pg_database` pre-check goes into `worktree-env` proper at Task 8.)

Then verify the engine builds and connects:

```bash
CUBEPLEX_DATABASE__NAME=cubeplex_feat_postgres_migration_dev \
  uv run python -c "
import asyncio
from cubeplex.db.engine import get_engine
from sqlalchemy import text

async def main():
    eng = get_engine()
    async with eng.connect() as c:
        r = await c.execute(text('SELECT 1'))
        print('connect ok:', r.scalar())
    await eng.dispose()

asyncio.run(main())
"
```

Expected: `connect ok: 1`.

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/db/engine.py
git commit -m "refactor(db): switch SQLAlchemy engine to postgresql+psycopg"
```

---

### Task 5: Update alembic env

**Files:**
- Modify: `backend/alembic/env.py`

- [ ] **Step 1: Rewrite `get_url()`**

Replace lines 70-83 with:

```python
def get_url() -> str:
    from urllib.parse import quote_plus

    host = app_config.get("database.host", "localhost")
    port = app_config.get("database.port", 5432)
    user = app_config.get("database.user", "postgres")
    password = app_config.get("database.password", "")
    name = app_config.get("database.name", "cubeplex")
    encoded_password = quote_plus(password)
    url = f"postgresql+psycopg://{user}:{encoded_password}@{host}:{port}/{name}"
    return url.replace("%", "%%")
```

The four-table `_CHECKPOINT_TABLES` filter and the `include_object` hook stay untouched.

- [ ] **Step 2: Verify `alembic current` runs against an empty PG database**

```bash
cd backend
CUBEPLEX_DATABASE__NAME=cubeplex_feat_postgres_migration_dev uv run alembic current
```

Expected: empty output (no migrations applied yet) and no traceback. Failure with `psycopg.OperationalError` means the URL is wrong; failure with import errors means a model module needs adjustment (unlikely at this stage).

- [ ] **Step 3: Commit**

```bash
git add backend/alembic/env.py
git commit -m "refactor(alembic): switch env URL to postgresql+psycopg"
```

---

### Task 6: Squash migrations to a fresh PG baseline

**Files:**
- Delete: every `*.py` under `backend/alembic/versions/` (keep `__pycache__/` ignored; `git rm` handles tracking).
- Create: one new migration generated by alembic.

- [ ] **Step 1: Drop and recreate the dev database for a clean autogenerate target**

```bash
PGPASSWORD=postgres psql -h localhost -U postgres -d postgres \
  -v ON_ERROR_STOP=1 \
  -c 'SELECT pg_terminate_backend(pid) FROM pg_stat_activity
       WHERE datname = '\''cubeplex_feat_postgres_migration_dev'\'';' \
  -c 'DROP DATABASE IF EXISTS "cubeplex_feat_postgres_migration_dev";' \
  -c 'CREATE DATABASE "cubeplex_feat_postgres_migration_dev";'
```

- [ ] **Step 2: Delete existing migration files**

```bash
cd backend
git rm alembic/versions/*.py
rm -rf alembic/versions/__pycache__
ls alembic/versions/  # should be empty
```

- [ ] **Step 3: Autogenerate the new baseline**

```bash
CUBEPLEX_DATABASE__NAME=cubeplex_feat_postgres_migration_dev \
  uv run alembic revision --autogenerate -m "initial postgres schema"
```

Expected: a single new file under `alembic/versions/` whose `down_revision = None` and `revision = '<some hex>'`. Open it.

- [ ] **Step 4: Hand-review the generated migration**

Open the file and confirm:

- `down_revision = None` (this is the new root).
- All identity ID columns are `sa.String(length=36)` (or `sqlmodel.AutoString` with length 36) — matching today's UUIDv7-as-string shape. If you see `postgresql.UUID(...)`, abort and check whether a model field was changed to `uuid.UUID`; revert that change and re-autogenerate.
- JSON columns use `sa.JSON()` (not `JSONB`). If `JSONB` appears, the model is using a PG-specific type — out of scope for this migration; revert.
- No imports from `sqlalchemy.dialects.mysql`. (There shouldn't be any, but verify.)
- The four LangGraph checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) are **not** in the migration — `include_object` excludes them.

If anything looks wrong, fix the underlying model and regenerate (delete the file, repeat Step 3). Do not hand-edit the migration into shape.

- [ ] **Step 5: Run the migration against an empty DB to validate**

```bash
PGPASSWORD=postgres psql -h localhost -U postgres -d postgres \
  -v ON_ERROR_STOP=1 \
  -c 'DROP DATABASE IF EXISTS "cubeplex_feat_postgres_migration_dev";' \
  -c 'CREATE DATABASE "cubeplex_feat_postgres_migration_dev";'

CUBEPLEX_DATABASE__NAME=cubeplex_feat_postgres_migration_dev \
  uv run alembic upgrade head
```

Expected: `Running upgrade  -> <new_id>, initial postgres schema` and exit code 0.

- [ ] **Step 6: Verify schema with a quick table count**

```bash
PGPASSWORD=postgres psql -h localhost -U postgres -d cubeplex_feat_postgres_migration_dev \
  -c "SELECT count(*) FROM information_schema.tables
      WHERE table_schema = 'public' AND table_type = 'BASE TABLE';"
```

Expected: a count >= 16 (the model tables: organizations, workspaces, memberships, users, conversations, attachments, artifacts, artifact_versions, agent_configs, billing_events, llm_billing_events, invite_tokens, skills, skill_versions, org_skill_installs, org_preinstalled_tombstones, user_sandboxes, workspace_skill_bindings, plus alembic_version). Confirm at a glance.

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/
git commit -m "chore(alembic): squash migration history to fresh postgres baseline"
```

---

### Task 7: Replace the LangGraph checkpointer

**Files:**
- Modify: `backend/cubeplex/agents/checkpointer.py`
- Modify: `backend/cubeplex/api/app.py`

- [ ] **Step 1: Rewrite `cubeplex/agents/checkpointer.py`**

Replace the entire file contents with:

```python
"""Checkpointer module for LangGraph conversation persistence."""

from __future__ import annotations

from urllib.parse import quote_plus

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from loguru import logger
from psycopg_pool import AsyncConnectionPool

from cubeplex.config import config

_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None


def _build_conn_string() -> str:
    host = config.get("database.host", "localhost")
    port = config.get("database.port", 5432)
    user = config.get("database.user", "postgres")
    password = config.get("database.password", "")
    name = config.get("database.name", "cubeplex")
    encoded_password = quote_plus(password)
    return f"postgresql://{user}:{encoded_password}@{host}:{port}/{name}"


async def init_checkpointer() -> AsyncPostgresSaver:
    """Open the shared connection pool and run idempotent setup.

    Called once at application startup from the FastAPI lifespan.
    """
    global _pool, _saver
    if _saver is not None:
        return _saver
    conn_str = _build_conn_string()
    pool_size = int(config.get("database.pool_size", 10))
    _pool = AsyncConnectionPool(
        conn_str,
        min_size=1,
        max_size=pool_size,
        kwargs={"autocommit": True, "prepare_threshold": None},
        open=False,
    )
    await _pool.open(wait=True)
    _saver = AsyncPostgresSaver(_pool)
    await _saver.setup()
    logger.info("LangGraph checkpointer initialized (pg pool max_size={})", pool_size)
    return _saver


async def shutdown_checkpointer() -> None:
    """Close the shared connection pool. Called from lifespan shutdown."""
    global _pool, _saver
    if _pool is not None:
        await _pool.close()
        logger.debug("Checkpointer connection pool closed")
    _pool = None
    _saver = None


async def create_checkpointer() -> AsyncPostgresSaver:
    """Return the shared checkpointer.

    Production path: lifespan has already called `init_checkpointer`.
    Fallback (unusual): if called before lifespan startup, we initialize
    on demand so test harnesses that bypass the lifespan still work.
    """
    if _saver is None:
        return await init_checkpointer()
    return _saver
```

Why these choices:

- `prepare_threshold=None` disables psycopg's prepared-statement cache. The PG checkpointer's `setup()` runs schema DDL that conflicts with cached plans across pool connections. Disabling matches the langgraph cookbook's recommendation.
- `autocommit=True` is required for `AsyncPostgresSaver` — its DML doesn't open transactions itself.
- The module-level singletons make `create_checkpointer()` a no-op fast path; tests that don't run the lifespan still get a working checkpointer (it'll lazy-init).

- [ ] **Step 2: Rewrite the lifespan checkpointer block in `cubeplex/api/app.py`**

Find the block at lines 157-183 (the `try: import aiomysql ...` clause) and replace it with:

```python
    # Initialize LangGraph checkpointer (creates pool + setup tables)
    try:
        from cubeplex.agents.checkpointer import init_checkpointer

        await init_checkpointer()
        logger.info("LangGraph checkpointer initialized")
    except Exception as e:
        logger.error("Failed to initialize LangGraph checkpointer: {}", str(e))
        raise
```

Note the change from `logger.warning(...)` to `logger.error(...)` + `raise`. The MySQL version swallowed setup failures because the per-request fallback would retry; the new pool-based design needs setup to succeed once at startup. Failing fast is correct.

- [ ] **Step 3: Wire the shutdown into the lifespan teardown**

In the same file's shutdown block (currently around lines 249-271), insert after the `logger.info("Application shutting down")` line:

```python
    from cubeplex.agents.checkpointer import shutdown_checkpointer

    await shutdown_checkpointer()
```

Place it before the existing `if _attachment_cleanup_task is not None: ...` block.

- [ ] **Step 4: Run the existing checkpointer e2e**

```bash
cd backend
CUBEPLEX_DATABASE__NAME=cubeplex_feat_postgres_migration_test \
  uv run pytest tests/e2e/test_agents.py -v -k "checkpointer or thread" -x
```

Expected: tests that exercise thread-state round-trips pass. If your worktree's test DB doesn't exist yet, create it via the same `psql ... CREATE DATABASE` pattern from Task 6 (the test DB will be auto-managed by `worktree-env` after Task 8).

If tests pass, the checkpointer wiring is correct. If they fail with `relation "checkpoints" does not exist`, `await saver.setup()` did not run — re-check the lifespan invocation.

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/agents/checkpointer.py backend/cubeplex/api/app.py
git commit -m "refactor(checkpointer): use AsyncPostgresSaver with shared pool"
```

---

### Task 8: Rewrite `scripts/worktree-env` for PostgreSQL

**Files:**
- Modify: `scripts/worktree-env`

The MySQL provisioning runs in three call sites: `_mysql_creds_from_env`, `_mysql_exec`, plus the schema CREATE/DROP/USE in `ensure_schemas`, `drop_schemas`, `doctor`, and `clean-orphans`.

- [ ] **Step 1: Replace `_mysql_creds_from_env()` (around line 271)**

Replace the function with:

```python
def _pg_creds_from_env() -> dict[str, str]:
    """Read PG creds from CUBEPLEX_DATABASE__* env vars, with sane defaults."""
    return {
        "host": os.environ.get("CUBEPLEX_DATABASE__HOST", "localhost"),
        "port": os.environ.get("CUBEPLEX_DATABASE__PORT", "5432"),
        "user": os.environ.get("CUBEPLEX_DATABASE__USER", "postgres"),
        "password": os.environ.get("CUBEPLEX_DATABASE__PASSWORD", "postgres"),
    }


def _quote_ident(name: str) -> str:
    """Quote a PG identifier for safe inclusion in DDL."""
    if '"' in name:
        raise ValueError(f"refusing to quote identifier containing double quote: {name!r}")
    return f'"{name}"'
```

- [ ] **Step 2: Replace `_mysql_exec()` with a `psql` wrapper**

Replace the function (around line 286) with:

```python
def _pg_exec(sql: str, db: str = "postgres") -> None:
    """Run a single SQL statement via the `psql` CLI against `db`."""
    creds = _pg_creds_from_env()
    cmd = [
        "psql",
        "-h", creds["host"],
        "-p", creds["port"],
        "-U", creds["user"],
        "-d", db,
        "-v", "ON_ERROR_STOP=1",
        "-tAc", sql,
    ]
    env = {**os.environ, "PGPASSWORD": creds["password"]}
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"psql command failed (exit {result.returncode}):\n"
            f"  cmd: {' '.join(cmd[:-1])} <sql>\n"
            f"  sql: {sql}\n"
            f"  stderr: {result.stderr.strip()}"
        )
```

- [ ] **Step 3: Replace `ensure_schemas` (around line 308)**

```python
def ensure_schemas(slug: str) -> None:
    """Create per-worktree dev/test databases if absent."""
    dev = db_dev_schema(slug)
    test = db_test_schema(slug)
    for db_name in (dev, test):
        # PG has no CREATE DATABASE IF NOT EXISTS — pre-check via pg_database.
        out = subprocess.run(
            [
                "psql",
                "-h", _pg_creds_from_env()["host"],
                "-p", _pg_creds_from_env()["port"],
                "-U", _pg_creds_from_env()["user"],
                "-d", "postgres",
                "-tAc",
                f"SELECT 1 FROM pg_database WHERE datname = '{db_name}';",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PGPASSWORD": _pg_creds_from_env()["password"]},
            check=True,
        )
        if out.stdout.strip() != "1":
            _pg_exec(f"CREATE DATABASE {_quote_ident(db_name)};")
    print(f"→ ensured PG databases: {dev}, {test}")
```

- [ ] **Step 4: Replace `drop_schemas` (around line 316)**

```python
def drop_schemas(slug: str) -> None:
    """Drop per-worktree dev/test databases, terminating live connections first."""
    dev = db_dev_schema(slug)
    test = db_test_schema(slug)
    for db_name in (dev, test):
        # Evict any connections still pinned to the DB before DROP.
        _pg_exec(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{db_name}';"
        )
        _pg_exec(f"DROP DATABASE IF EXISTS {_quote_ident(db_name)};")
    print(f"→ dropped PG databases: {dev}, {test}")
```

- [ ] **Step 5: Update `doctor` connectivity probes (around lines 580-588)**

Find the two MySQL `USE` blocks and replace each with:

```python
        try:
            _pg_exec("SELECT 1;", db=db_dev_schema(slug))
            print(f"✓ PG database {db_dev_schema(slug)} reachable")
        except RuntimeError as e:
            print(f"✗ PG database {db_dev_schema(slug)} unreachable: {e}")
            ok = False
```

(and the analogous block for the test DB).

- [ ] **Step 6: Update `clean-orphans` (around lines 657-706)**

Replace the `mysql` schema query and its drop loop with:

```python
    # 3. Orphan PG databases (cubeplex_* not referenced by any live entry)
    creds = _pg_creds_from_env()
    out = subprocess.run(
        [
            "psql",
            "-h", creds["host"],
            "-p", creds["port"],
            "-U", creds["user"],
            "-d", "postgres",
            "-tAc",
            "SELECT datname FROM pg_database WHERE datname LIKE 'cubeplex_%';",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PGPASSWORD": creds["password"]},
        check=True,
    )
    discovered_dbs = {line.strip() for line in out.stdout.splitlines() if line.strip()}
```

In the loop that drops orphans, replace the `_mysql_exec(f"DROP DATABASE IF EXISTS \`{schema}\`;")` line with:

```python
        _pg_exec(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{schema}';"
        )
        _pg_exec(f"DROP DATABASE IF EXISTS {_quote_ident(schema)};")
```

- [ ] **Step 7: Update the module docstring (line 1-3)**

Replace:

```
"""worktree-env — allocate ports, MySQL schemas, and Redis prefixes per
git worktree so multiple worktrees can run dev servers and E2E in parallel.
```

with:

```
"""worktree-env — allocate ports, PostgreSQL databases, and Redis prefixes
per git worktree so multiple worktrees can run dev servers and E2E in parallel.
```

Also update the comment at line 269 (`# ----- mysql + alembic`) to `# ----- postgres + alembic`.

- [ ] **Step 8: Smoke-test the rewrite against the current worktree**

```bash
cd /home/chris/cubeplex/.worktrees/feat/postgres-migration

# First, drop the MySQL schemas the original `new-worktree` created.
# Use the OLD MySQL CLI here, since the script we just rewrote no longer
# knows about MySQL. This is a one-off cleanup; after this we live in PG.
SLUG=$(grep CUBEPLEX_WORKTREE_SLUG .worktree.env | cut -d= -f2)
mysql -h 127.0.0.1 -P 3306 -uroot -p"${CUBEPLEX_DATABASE__PASSWORD:-}" \
  -e "DROP DATABASE IF EXISTS \`cubeplex_${SLUG}_dev\`; \
      DROP DATABASE IF EXISTS \`cubeplex_${SLUG}_test\`;" 2>/dev/null || true

# Now provision via the new PG path.
./scripts/worktree-env init
```

If `mysql` CLI is unavailable or local MySQL is gone, skip the cleanup — those orphan schemas are harmless.

Expected from `init`: `→ ensured PG databases: cubeplex_<slug>_dev, cubeplex_<slug>_test` and `.worktree.env` is rewritten with the PG defaults (port 5432, user postgres).

- [ ] **Step 9: Run doctor**

```bash
./scripts/worktree-env doctor
```

Expected: every check is green, including `✓ PG database cubeplex_<slug>_dev reachable` and `✓ PG database cubeplex_<slug>_test reachable`.

- [ ] **Step 10: Run alembic against the freshly provisioned worktree DB**

```bash
cd backend
uv run alembic upgrade head
```

Expected: `Running upgrade  -> <new_id>, initial postgres schema` and exit 0. (The new baseline migration from Task 6 is what runs.)

- [ ] **Step 11: Commit**

```bash
git add scripts/worktree-env
git commit -m "feat(worktree-env): switch provisioning from mysql to postgres"
```

---

### Task 9: Update CI workflow

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Replace the `mysql` service block (lines 93-103)**

Replace:

```yaml
      mysql:
        image: mysql:8.4
        env:
          MYSQL_ROOT_PASSWORD: testpass
          MYSQL_DATABASE: cubeplex_test
        ports: ['3306:3306']
        options: >-
          --health-cmd="mysqladmin ping -h 127.0.0.1 -u root -ptestpass"
          --health-interval=10s
          --health-timeout=5s
          --health-retries=10
```

with:

```yaml
      postgres:
        image: postgres:18-alpine
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: testpass
          POSTGRES_DB: cubeplex_test
        ports: ['5432:5432']
        options: >-
          --health-cmd="pg_isready -U postgres"
          --health-interval=10s
          --health-timeout=5s
          --health-retries=10
```

- [ ] **Step 2: Replace the reset-DB step (line ~178)**

Replace the `mysql -h 127.0.0.1 ... DROP DATABASE ... CREATE DATABASE` line with:

```yaml
      - name: Reset DB state for playwright
        # pytest may leave data behind; drop + recreate + migrate for a clean playwright start.
        run: |
          PGPASSWORD=testpass psql -h 127.0.0.1 -U postgres -d postgres \
            -v ON_ERROR_STOP=1 \
            -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'cubeplex_test';" \
            -c "DROP DATABASE IF EXISTS cubeplex_test;" \
            -c "CREATE DATABASE cubeplex_test;"
          make backend-migrate
```

- [ ] **Step 3: Verify locally with `act` if available, otherwise rely on CI**

`act` is optional. If the user does not have it, push the branch when the rest of the plan is done and let CI validate.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: replace mysql service with postgres:18-alpine"
```

---

### Task 10: Clean up MySQL-flavored comments

**Files:**
- Modify: `backend/cubeplex/repositories/invite_token.py`
- Modify: `backend/cubeplex/utils/time.py`

These two files contain explanatory comments that mention MySQL's TZ-stripping behavior. The behavior is identical on PG's `timestamp without time zone` (no TZ stored), so the underlying coercion code stays. Only the comments change.

- [ ] **Step 1: `backend/cubeplex/repositories/invite_token.py:29`**

Replace the comment:

```python
        # MySQL DATETIME drops tz on round-trip — coerce to UTC-aware before comparing.
```

with:

```python
        # `timestamp without time zone` columns drop tz on round-trip — coerce to UTC-aware before comparing.
```

- [ ] **Step 2: `backend/cubeplex/utils/time.py:9`**

Find the comment that references "MySQL/MariaDB DATETIME columns strip timezone info" and rewrite to:

```
    `timestamp without time zone` columns strip timezone info, so datetimes read back
```

(Adjust the surrounding sentence to match the file's existing style.)

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/repositories/invite_token.py backend/cubeplex/utils/time.py
git commit -m "docs: update MySQL-flavored comments to engine-neutral wording"
```

---

### Task 11: Update human-facing docs

**Files:**
- Modify: `AGENTS.md` (root; `CLAUDE.md` is a symlink to it)
- Modify: `backend/CLAUDE.md`

- [ ] **Step 1: `AGENTS.md` — Worktrees and parallel dev section**

Locate the section that currently says:

> Each worktree gets its own allocated ports, MySQL schemas, and Redis prefix

Replace `MySQL schemas` with `PostgreSQL databases`.

In the same section, locate the `Backend (`backend/cubeplex/config.py`) ...` paragraph and the `.worktree.env` example. Update the example block from:

```
CUBEPLEX_DATABASE__PORT=...
```

to a PG-shaped example showing port 5432. Concretely the example block becomes:

```
CUBEPLEX_WORKTREE_NAME=feat-m7-file-upload
CUBEPLEX_WORKTREE_SLOT=37
CUBEPLEX_API__PORT=8037
CUBEPLEX_DATABASE__PORT=5432
CUBEPLEX_DATABASE__USER=postgres
CUBEPLEX_DATABASE__NAME=cubeplex_feat_m7_file_upload
CUBEPLEX_REDIS__KEY_PREFIX=cubeplex-feat-m7-file-upload
CUBEPLEX_API_URL=http://localhost:8037
PORT=3037
BASE_URL=http://localhost:3037
```

(Worktree slots are still per-host; the database `port` is shared because it's a port-on-host, not a port-per-worktree.)

In "Notes for AI agents," update "Default ports (3000 / 8000) only apply in the **main** worktree" — leave that line alone (3000/8000 still describe app/API ports, not the DB). But add a new bullet:

> - The Postgres cluster lives at `~/infra/postgresql` on `localhost:5432` and is shared across worktrees; isolation is per-database, not per-port.

- [ ] **Step 2: `backend/CLAUDE.md`**

Locate the pytest marker description that currently says:

> "e2e: end-to-end tests that hit real services (MySQL/Redis/LLM/Sandbox)"

Replace `MySQL` with `Postgres`.

In the "Database" section under "Architecture / Quick Start" (search for "Migrations are managed with Alembic"), no change is required — alembic is engine-agnostic.

- [ ] **Step 3: `backend/CLAUDE.md` Environment Variables section**

If a `MYSQL_*` variable is mentioned anywhere, swap to the `CUBEPLEX_DATABASE__*` form. Quick check:

```bash
grep -in "mysql\|3306" backend/CLAUDE.md
```

Update any hits.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md backend/CLAUDE.md
git commit -m "docs: update worktree + pytest-marker docs to PostgreSQL"
```

---

### Task 12: Full-stack verification

**Files:** none changed.

- [ ] **Step 1: Make sure no MySQL strings remain in code**

```bash
cd /home/chris/cubeplex/.worktrees/feat/postgres-migration
grep -rin --include="*.py" --include="*.toml" --include="*.yaml" \
     --include="*.yml" --include="*.md" --include="*.sh" \
     "mysql\|aiomysql\|pymysql" \
     -- backend scripts .github AGENTS.md
```

Expected hits — these are all acceptable historical references:

- `docs/superpowers/specs/2026-04-29-mysql-to-postgres-design.md` (this work)
- `docs/superpowers/plans/2026-04-29-mysql-to-postgres.md` (this plan)
- Older specs/plans under `docs/superpowers/specs/` and `docs/superpowers/plans/` (out of scope per spec §8)

Any hit inside `backend/`, `scripts/`, `.github/`, `AGENTS.md`, or `backend/CLAUDE.md` is a bug. Fix and re-grep before continuing.

- [ ] **Step 2: Run `make check` inside the backend**

```bash
cd backend
make check
```

Expected: format passes, lint passes, mypy passes, tests pass. If `make test` fails because the DB isn't migrated, run `uv run alembic upgrade head` first; the production-grade path does this from the `Alembic migrate` CI step.

- [ ] **Step 3: Run the full E2E suite**

```bash
cd backend
make backend-test-e2e || uv run pytest tests/e2e -v
```

Expected: green. Streaming + checkpointer round-trip is the canonical signal.

- [ ] **Step 4: Worktree round-trip on a throwaway branch**

```bash
cd /home/chris/cubeplex  # main repo
./scripts/new-worktree feat/test-pg-rt
cd .worktrees/feat/test-pg-rt
./scripts/worktree-env doctor
cd /home/chris/cubeplex
./scripts/worktree-env destroy --slug $(grep CUBEPLEX_WORKTREE_SLUG \
  .worktrees/feat/test-pg-rt/.worktree.env | cut -d= -f2)
git worktree remove .worktrees/feat/test-pg-rt --force
git branch -D feat/test-pg-rt

# Verify no orphan databases
PGPASSWORD=postgres psql -h localhost -U postgres -d postgres \
  -c "SELECT datname FROM pg_database WHERE datname LIKE 'cubeplex_test_pg_rt%';"
```

Expected: empty result set.

- [ ] **Step 5: Push and watch CI**

```bash
cd .worktrees/feat/postgres-migration
git push -u origin feat/postgres-migration
```

Open the PR and confirm the CI run — pytest e2e + playwright e2e — is green. (Do not merge until the user explicitly asks; this plan ends at green CI.)

- [ ] **Step 6: No new commit** — Task 12 is verification only.

---

## Definition of done

- All 11 modification tasks committed and pushed.
- `make check` and `make backend-test-e2e` pass inside the worktree.
- Worktree round-trip on a throwaway branch leaves no orphan databases.
- CI is green on `feat/postgres-migration`.
- `grep -ri "mysql\|aiomysql\|pymysql"` returns only the design + plan docs and historical specs/plans.

## Rollback

This change is squashed-history at the alembic level, so rollback is non-trivial:

- The branch can be discarded entirely (`git worktree remove .worktrees/feat/postgres-migration --force` + `git branch -D feat/postgres-migration`).
- Local PG databases provisioned by `worktree-env` are dropped via `worktree-env destroy`.
- No production state exists to roll back.

If a partial state needs to be salvaged after Task 6 has landed but before Tasks 7-12 complete, the only forward path is to finish the plan — there is no MySQL-compatible alembic history left after Task 6.
