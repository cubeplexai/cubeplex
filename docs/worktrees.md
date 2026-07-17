# Worktrees & Parallel Dev

This repo uses `git worktree` for parallel feature development. Each
worktree gets its own allocated ports, PostgreSQL databases, and Redis
prefix to avoid collisions when multiple worktrees run dev servers or E2E
suites at the same time. All allocations land in
`<worktree_root>/.worktree.env` (gitignored).

## Creating a New Worktree

Always run from the main repo root. The wrapper branches from latest
`<remote>/main` (`origin/main` by default):

```bash
./scripts/new-worktree feat/<branch-name>
# or choose a non-origin remote:
./scripts/new-worktree --remote upstream feat/<branch-name>
```

This:

- Fetches `<remote>/main` (`origin` by default).
- Creates the worktree.
- Allocates a slot.
- Provisions PostgreSQL databases on the shared `~/infra/postgresql`
  cluster.
- Runs `alembic upgrade head`.
- Copies `backend/.env` and `config.development.local.yaml` from main
  if missing.
- Writes `.worktree.env`.
- Pushes the new branch to `<remote>` with `--no-verify` and sets the
  branch upstream.

## Working Inside a Worktree

**First thing on entry — read the allocated values:**

```bash
./scripts/worktree-env show
# or just:
cat .worktree.env
```

`.worktree.env` declares values like:

```
CUBEPLEX_WORKTREE_NAME=feat-m7-file-upload
CUBEPLEX_WORKTREE_SLOT=37
CUBEPLEX_API__PORT=8037
CUBEPLEX_DATABASE__NAME=cubeplex_feat_m7_file_upload
CUBEPLEX_REDIS__KEY_PREFIX=cubeplex-feat-m7-file-upload
CUBEPLEX_API_URL=http://localhost:8037
PORT=3037
BASE_URL=http://localhost:3037
```

Backend (`backend/cubeplex/config.py`), Next (`next.config.ts`), and
Playwright (`playwright.config.ts`) all auto-load this file. So
`python main.py`, `pnpm dev`, and `pnpm test:e2e` just work with the
allocated ports — but **never assume 3000 / 8000** when checking
manually with `curl` / `lsof`.

The frontend `pnpm dev` is wrapped via
`frontend/scripts/with-worktree-env.mjs`. Bypassing the wrapper means
PORT defaults to 3000 and silently collides with the main worktree.

## Running tests in a worktree

The dev server and the test suite use **separate** databases — the suite
must never touch your dev data:

| | dynaconf env | config | database |
|---|---|---|---|
| `python main.py` (dev server) | `development` | `config.development(.local).yaml` | dev DB (`cubeplex_<slug>`, from `.worktree.env`) |
| `pytest` / e2e | `test` | `config.test.yaml` | per-slot test DB (`cubeplex_test_<slug>`) |

`tests/conftest.py` handles the routing for you. In a worktree it reads
`.worktree.env`'s dev DB name and **force-derives the per-slot test DB**
(`cubeplex_<slug>` → `cubeplex_test_<slug>`), so a plain `uv run pytest`
runs against the test DB and **cannot** clobber your dev data — no env
juggling needed. (It also pins the object-store creds to the local rustfs
values and refuses to start if the resolved DB name isn't a test DB.)

```bash
# Just works — routed to cubeplex_test_<slug>, dev DB untouched:
cd backend && uv run pytest tests/e2e/...
```

Prerequisites:

- The per-slot test DB must be migrated. Worktree provisioning runs
  `alembic upgrade head` on it; if it's behind, `worktree-env reseed-db`
  (or `CUBEPLEX_DATABASE__NAME=cubeplex_test_<slug> uv run alembic upgrade
  head`) fixes it.
- S3-backed tests (skills, attachments) need the local **rustfs** object
  store on `:9000` — see `~/infra/rustfs` (`docker compose up -d`). conftest
  pins the `rustfsadmin` creds; the bucket is `cubeplex-test`.

Why conftest pins these: a dynaconf env var beats `config.test.yaml`, so a
developer's `.env` (real dev DB name + real Aliyun OSS creds) would
otherwise leak into the test env — the DB leak truncates your dev DB, the
creds leak breaks S3 with `InvalidAccessKeyId`. The conftest force-set
neutralizes both.

## Subcommands

```bash
./scripts/worktree-env doctor          # health check: DBs, ports, alembic
./scripts/worktree-env destroy         # drop DBs, clear redis prefix, delete .worktree.env
./scripts/worktree-env clean-orphans   # interactive cleanup of orphaned slots
./scripts/worktree-env reseed-db       # drop + recreate DBs, re-run all migrations
```

Run `destroy` **before** `git worktree remove`.

`reseed-db` is **destructive — wipes every row.** Pass `--yes` to skip
the prompt.

## When to Use `reseed-db`

After a rebase that pulls in `main` migrations newer than this branch's
own migrations, alembic's stored revision pointer on the DB already shows
`head` (because the branch's old head was applied before the rebase), so
`alembic upgrade head` is a no-op even though tables are still missing
the columns the skipped migrations were supposed to add.

**Symptom:** 500s with `psycopg.errors.UndefinedColumn: column X does
not exist` against tables that landed on `main` while this branch was
diverged (common offenders: `workspace_mcp_overrides`, `conversations`).

**Fix:** `./scripts/worktree-env reseed-db` from inside the worktree.
Drops both databases, recreates them, re-runs alembic from base — clean
slate, no manual `ALTER` patching. Costs you all your local
conversation/run/attachment data, so don't run it mid-test.

## Notes for AI Agents

- **Subagents do not inherit `cwd`.** When dispatching work into a
  worktree, pin the absolute path AND tell the agent to
  `cat .worktree.env` first.
- **Default ports (3000 / 8000) only apply in the main worktree.**
  Inside any other worktree they are wrong; port collisions silently
  test the wrong code.
- **CI runs in the main checkout** (no `.worktree.env`); all the dotenv
  loaders no-op there, so CI behavior is unchanged.
- The Postgres cluster lives at `~/infra/postgresql` on
  `localhost:5432` and is shared across worktrees; isolation is
  per-database, not per-port.
