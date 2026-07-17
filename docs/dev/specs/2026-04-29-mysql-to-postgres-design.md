# MySQL → PostgreSQL Migration

**Date:** 2026-04-29
**Branch:** `feat/postgres-migration`
**Worktree:** `.worktrees/feat/postgres-migration`

## 1. Goals & non-goals

**Goal.** Replace MySQL with PostgreSQL across the backend stack — runtime
engine, alembic migrations, LangGraph checkpointer, worktree provisioning,
CI — using the existing `~/infra/postgresql` cluster (postgres 18.2 on
`localhost:5432`, user/password `postgres/postgres`) as the local dev/test
target, and the official `langgraph-checkpoint-postgres` package for thread
state persistence.

**Non-goals.**

- No data preservation. Dev/CI state is disposable; alembic history is
  squashed to a fresh PG-only baseline. Production is not yet running.
- No dual-driver support. MySQL is removed in the same change; nothing
  retains a fallback path.
- No production cluster setup. Prod-time creds flow through the existing
  `CUBEPLEX_DATABASE__*` env vars; standing up a managed PG instance is a
  separate operational task.
- No primary-key scheme change. Today's UUIDv7-as-`varchar(36)` shape is
  preserved. A "TypeID-style prefixed IDs" project is parked as a
  follow-up spec.

## 2. Driver choice

`psycopg` (v3) for everything: app, alembic, LangGraph checkpointer.

- Both `psycopg` and `asyncpg` support async. The choice is not
  async-vs-not — it's "one driver" vs "two drivers."
- `langgraph-checkpoint-postgres` is built on `psycopg`, so it lands in
  the dependency tree regardless. Adding `asyncpg` would mean two
  drivers, two error hierarchies, and two pool-tuning surfaces with no
  measurable performance benefit at our scale.
- `psycopg` v3 is DBAPI 2.0 compliant, exposes both sync and async modes
  in one library, and is the modern successor to psycopg2.

`pyproject.toml` change set:

- **Remove:** `langgraph-checkpoint-mysql[aiomysql]`
- **Add:** `langgraph-checkpoint-postgres>=3.0.0`,
  `psycopg[binary,pool]>=3.2`

`[binary]` ships the C extension; `[pool]` enables the `AsyncConnectionPool`
the checkpointer consumes.

## 3. Configuration & connection URL

### Config files

`backend/config.yaml`, `backend/config.development.yaml`,
`backend/config.test.yaml`, `backend/config.production.yaml` all switch
the `database:` block defaults to:

```yaml
database:
  host: "localhost"
  port: 5432
  user: "postgres"
  password: "postgres"   # dev default; prod overrides via env
  name: "cubeplex"        # config.test.yaml: "cubeplex_test"
  pool_size: 10
  max_overflow: 20
  echo: false
```

### Secrets contract

Configuration values are read exclusively via the `CUBEPLEX_*` env
contract (dynaconf prefix). No hardcoded credentials in source.

- Real values for non-development environments come from
  `CUBEPLEX_DATABASE__HOST` / `CUBEPLEX_DATABASE__PORT` /
  `CUBEPLEX_DATABASE__USER` / `CUBEPLEX_DATABASE__PASSWORD` /
  `CUBEPLEX_DATABASE__NAME`.
- Local dev secrets live in `backend/.env` (gitignored) or
  `backend/config.development.local.yaml` (gitignored). The worktree
  toolchain writes to the same env contract.
- Future runtime-injected secrets (e.g., a browser-side flow that asks
  the user to type a DB password and feeds it to the backend) plug into
  this same layer — by writing the env vars before process spawn, or by
  calling `config.set("database.password", ...)` at runtime. The
  migration does not need to anticipate that UX, but it must keep the
  env-var contract clean so the feature has a single, stable hook.

### Engine (`backend/cubeplex/db/engine.py`)

```python
return f"postgresql+psycopg://{user}:{encoded_password}@{host}:{port}/{name}"
```

Drop the MySQL-specific `pool_recycle=280` (it existed to avoid MySQL's
600s `wait_timeout` killing idle connections; PG has no analogous
default). Keep `pool_pre_ping=True`. Drop the MySQL-shaped
`connect_args={"connect_timeout": 10}`; psycopg accepts
`connect_timeout` via the libpq URL or `connect_args` — pick whichever
shape mypy is happiest with and verify in implementation.

### Alembic env (`backend/alembic/env.py`)

Switch URL builder to `postgresql+psycopg://`. The existing
`include_object` filter that excludes the four LangGraph checkpoint
tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`,
`checkpoint_migrations`) stays as-is.

## 4. Checkpointer (`backend/cubeplex/agents/checkpointer.py`)

Drop the manual `aiomysql.connect()` plumbing. Use the official
`AsyncPostgresSaver` backed by a shared `AsyncConnectionPool`.

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
```

Lifecycle:

- The connection pool is created **once at app startup** and torn down
  in the FastAPI lifespan handler. One pool per process.
- `await saver.setup()` is called once at boot (idempotent) to create
  the four checkpoint tables.
- Per-request handlers obtain the saver from app state (or build a
  lightweight wrapper around the pool); no per-request `connect()`.
- The lifespan handler must `await pool.close()` on shutdown to avoid
  leaked-connection warnings in tests.

This is a behavioral simplification from today: the existing code
opens a fresh aiomysql connection per checkpointer call (a workaround
for the `langgraph-checkpoint-mysql` async-loop binding). PG's official
checkpointer + psycopg pool removes that workaround.

## 5. Alembic — squash to fresh baseline

Action:

1. Delete every file under `backend/alembic/versions/` (16 files,
   including the MySQL-dialect-typed `1d1dab71f0fa_*`).
2. With an empty `cubeplex` database on the local PG cluster, run
   `alembic revision --autogenerate -m "initial postgres schema"`.
3. Hand-review the generated migration: confirm `varchar(36)` widths
   for identity IDs, JSON columns map to PG `JSON` (or `JSONB` — see
   below), no leftover MySQL dialect imports.
4. Commit the single new baseline migration.

Decision: **JSON vs JSONB.** Today's models use `from sqlalchemy.types
import JSON` (portable). Autogenerate against PG will emit `JSON` by
default. JSONB is faster to query and supports indexing, but no
existing code queries inside the JSON columns — it's all whole-blob
read/write. Keep `JSON` to minimize diff; revisit JSONB only if/when a
query hot-path needs it.

Decision: **`varchar(36)` vs PG-native `uuid`.** Today's models declare
identity IDs as `str` with `max_length=36`, producing `varchar(36)`.
Autogenerate will reproduce that shape. Switching to `uuid` is more
efficient (16 bytes vs 36 chars, faster B-tree) but requires updating
every model field type and every string-typed FK reference. Out of
scope; tackle alongside the deferred TypeID work.

From this point forward, alembic history starts at the new baseline.
The migration list grows additively as before.

## 6. Worktree provisioning (`scripts/worktree-env`)

The script provisions per-worktree isolation today via two MySQL
schemas: `cubeplex_<slug>_dev` and `cubeplex_<slug>_test`. PG keeps the
same names — they're databases on a shared cluster, semantically
identical to MySQL "schemas" for our purposes.

Change set:

- `_mysql_creds_from_env()` → `_pg_creds_from_env()`. Reads
  `CUBEPLEX_DATABASE__HOST` (default `localhost`),
  `CUBEPLEX_DATABASE__PORT` (default `5432`),
  `CUBEPLEX_DATABASE__USER` (default `postgres`),
  `CUBEPLEX_DATABASE__PASSWORD` (default `postgres`).
- `_mysql_exec(sql)` → `_pg_exec(sql, db="postgres")`. Invokes `psql`
  with `PGPASSWORD` env, `-h/-p/-U`, `-d <db>`, `-v ON_ERROR_STOP=1`,
  `-c <sql>`. Connects to the maintenance DB `postgres` for
  `CREATE/DROP DATABASE` calls.
- `db_dev_schema(slug)` / `db_test_schema(slug)` → unchanged names
  (`cubeplex_<slug>_dev`, `cubeplex_<slug>_test`).
- `ensure_schemas(slug)` → PG has no `CREATE DATABASE IF NOT EXISTS`.
  Pre-check via `SELECT 1 FROM pg_database WHERE datname = $1`; only
  issue `CREATE DATABASE "<name>"` when absent.
- `drop_schemas(slug)` → first
  `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1`
  to evict any straggler connections, then `DROP DATABASE IF EXISTS "<name>"`.
- `doctor` → replace MySQL `USE` connectivity probe with a `psql -d
  <name> -c "SELECT 1"` check.
- `clean-orphans` → query `pg_database` for `datname LIKE 'cubeplex_%'`
  instead of `information_schema.schemata`.

**Identifier quoting.** Slugs may contain hyphens (e.g.,
`cubeplex_feat-postgres-migration_dev`). PG folds unquoted identifiers
to lowercase but rejects unquoted hyphens entirely. All `psql -c`
statements that mention a worktree DB name must double-quote the
identifier. Add a `_quote_ident(name)` helper used in every
DDL-generating call site.

`.worktree.env` keeps every variable name it has today — only the
defaults change (port `3306` → `5432`, user/password). Existing
loaders in `backend/cubeplex/config.py`, `frontend/next.config.ts`, and
`frontend/playwright.config.ts` see no behavior change beyond the new
defaults.

## 7. CI (`.github/workflows/ci.yml`)

Replace the `mysql` service block with PG:

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

Replace the manual reset step (`mysql -e "DROP DATABASE..."`) with the
PG equivalent:

```bash
PGPASSWORD=testpass psql -h 127.0.0.1 -U postgres -d postgres \
  -v ON_ERROR_STOP=1 \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'cubeplex_test';" \
  -c "DROP DATABASE IF EXISTS cubeplex_test;" \
  -c "CREATE DATABASE cubeplex_test;"
```

`config.test.yaml` keeps `password: "testpass"` for CI; CI's PG service
takes that same value via `POSTGRES_PASSWORD`.

## 8. Docs to update

In scope:

- **`AGENTS.md`** (root) — "Worktrees and parallel dev" section says
  "MySQL schemas". Rewrite to "Postgres databases on the shared
  `~/infra/postgresql` cluster." Update the `.worktree.env` example to
  show port 5432.
- **`backend/CLAUDE.md`** — pytest marker description references
  "MySQL/Redis/...". Update to "Postgres/Redis/...".
- **`backend/.env.example`** — port, user, default values.

Out of scope:

- Historical specs/plans under `docs/superpowers/specs/` and
  `docs/superpowers/plans/` that mention MySQL. Those describe past
  decisions; rewriting them rewrites history. The single MySQL→PG
  migration spec (this document) is the canonical record.

## 9. Testing strategy

Primary verification — E2E. Acceptance bar:

1. `make dev-install` cleanly installs the new deps (no leftover
   `aiomysql`, `langgraph-checkpoint-mysql`).
2. `alembic upgrade head` against an empty `cubeplex_test` database on
   `~/infra/postgresql` succeeds and produces the expected schema.
3. `make backend-test-e2e` passes locally against
   `~/infra/postgresql`. The existing `tests/e2e/test_agents.py`
   exercises the SSE streaming + checkpointer round-trip and is the
   primary gate for the LangGraph wiring.
4. Worktree round-trip:
   `./scripts/new-worktree feat/test-pg-rt` provisions databases +
   migrates, `./scripts/worktree-env doctor` is green, then
   `./scripts/worktree-env destroy` cleans up. Confirm via
   `psql -d postgres -c "\l"` that no orphaned `cubeplex_test_pg_rt_*`
   databases remain.
5. CI green on the branch.

Out of scope: standing up unit tests for the worktree script's PG
plumbing. Doctor + the round-trip in (4) is the integration check.

## 10. Risks / things to watch

- **`AsyncConnectionPool` lifecycle.** Forgetting `await pool.close()`
  on shutdown leaks connections and prints noisy warnings in pytest.
  Wire it into the FastAPI lifespan handler in `cubeplex/api/app.py`.
- **Identifier quoting.** Worktree DB names can contain hyphens. Every
  `psql -c` that names one must double-quote it. Encapsulate in
  `_quote_ident()` to make the rule unmissable.
- **Transaction semantics.** psycopg defaults to autocommit-off. The
  checkpointer's `setup()`, alembic's online migrations, and any DDL
  the worktree script issues must run on connections with `autocommit
  = True` (PG forbids `CREATE DATABASE` inside a transaction). Verify
  in implementation; the typical fix is `conn.autocommit = True`
  before issuing the DDL.
- **`AGENTS.md` worktree note.** Subagents don't inherit `cwd`. After
  the rewrite, the note must still emphasize "always read
  `.worktree.env` first" — even more important during the transition,
  since stale `.worktree.env` files from old worktrees will still
  reference port 3306. Worktrees created before this lands need their
  `.worktree.env` regenerated (or the worktree itself recreated).
- **Existing worktrees.** Any live worktree on this branch lineage is
  pinned to MySQL ports. The implementation plan must call out
  `worktree-env clean-orphans` + recreate as the upgrade path; we are
  not building a forward migration for in-flight MySQL worktrees.

## 11. Acceptance criteria

This change is done when:

- All MySQL imports, packages, and config defaults are removed from the
  repo (verified by `grep -ri "mysql\|aiomysql\|pymysql"` returning
  only this design doc and historical specs/plans).
- `make check` passes inside the worktree.
- `make backend-test-e2e` passes inside the worktree against the
  `~/infra/postgresql` cluster.
- A round-trip `new-worktree → doctor → destroy` cycle on a throwaway
  branch succeeds without orphaned databases.
- CI passes on `feat/postgres-migration`.

## 12. Deferred / parked

- **TypeID-style prefixed business IDs.** UUID7 stays in this
  migration. Follow-up spec covers (a) PG-native `uuid` column type,
  (b) prefix scheme (`usr_…`, `org_…`, etc.), (c) API/frontend type
  rollout. Not bundled here because the blast radius is larger than
  the engine swap and the two changes are independent.
- **JSONB vs JSON.** Stay on `JSON` until a query hot-path inside JSON
  columns appears.
