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
- Seeds a **dev agent** (a registered user + onboarding org/workspace + a
  personal-access API token) into the dev DB by exercising the real
  `/auth/register` -> `/auth/login` -> `/onboarding` -> `/me/api-keys`
  endpoints. The token is written to `.worktree.env` (see
  [Dev Agent](#dev-agent-seeded-user--api-token)). Pass `--no-seed-agent`
  to skip; the seed is best-effort and never blocks worktree creation.
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
./scripts/worktree-env reseed-db       # drop + recreate DBs, re-run all migrations (re-seeds the dev agent)
./scripts/worktree-env seed-dev-agent  # (re)seed just the dev agent into the dev DB
```

Run `destroy` **before** `git worktree remove`.

`reseed-db` is **destructive — wipes every row.** Pass `--yes` to skip
the prompt. Because it wipes every row, it re-runs the dev agent seed
afterwards so the token in `.worktree.env` is refreshed against the new DB.

## Dev Agent (seeded user + API token)

`init` (and `reseed-db`) seed a dev agent into the worktree's **dev** DB
so you - or an external test agent - can hit the dev server
(`python main.py`) without manually registering + onboarding through the
UI. The seed runs the real HTTP flow against a temporary uvicorn (reusing
a running dev server if one is already up on the worktree port).

After `init`, `.worktree.env` carries a `# Dev agent` section:

```
CUBEPLEX_DEV_AGENT_EMAIL=dev-agent-<slug>@example.com
CUBEPLEX_DEV_AGENT_PASSWORD=DevAgent1!-<slug>
CUBEPLEX_DEV_AGENT_TOKEN=sk-...                # use as `Authorization: Bearer`
CUBEPLEX_DEV_AGENT_ORG_ID=org-...
CUBEPLEX_DEV_AGENT_WORKSPACE_ID=ws-...         # prepend to /api/v1/ws/{...}/ routes
```

(`CUBEPLEX_API_URL` is already in `.worktree.env` and points at the
worktree's dev server.) A test agent uses it directly:

```bash
curl -H "Authorization: Bearer $CUBEPLEX_DEV_AGENT_TOKEN" \
     "$CUBEPLEX_API_URL/api/v1/auth/me"
curl -H "Authorization: Bearer $CUBEPLEX_DEV_AGENT_TOKEN" \
     "$CUBEPLEX_API_URL/api/v1/ws/$CUBEPLEX_DEV_AGENT_WORKSPACE_ID/conversations"
```

The token acts as its owning user - same workspace membership, same RBAC
(the seeder's onboarding grants ADMIN). Re-running `seed-dev-agent` reuses
the user/org/workspace and **rotates** the token (the plaintext is shown
once and never stored, so it can only be regenerated, not recovered); the
old labeled key is deleted first to stay under the per-user quota.

Notes:

- The temporary server's lifespan requires **Redis** (same as the real dev
  server). If Redis is down the seed fails fast - start Redis, then
  `./scripts/worktree-env seed-dev-agent`.
- The temporary server runs with email verification disabled
  (`CUBEPLEX_AUTH__EMAIL_VERIFICATION__ENABLED=false`) so login doesn't
  require an OTP. When reusing a running dev server that *does* have SMTP
  verification on, the seeder verifies the user directly in the dev DB and
  retries (the OTP is sent over SMTP and cannot be captured).
- Need a second dev agent (multi-user test)? `./scripts/worktree-env
  seed-dev-agent --email other@example.com --label other`.

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
