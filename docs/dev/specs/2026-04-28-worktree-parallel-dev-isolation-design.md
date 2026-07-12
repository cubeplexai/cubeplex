# Worktree Parallel Dev Isolation — Design

**Date:** 2026-04-28
**Status:** Draft
**Owner:** xfgong

## Problem

Multiple `git worktree`s under `.worktrees/` collide when developers (or AI
agents) run dev servers and E2E tests in parallel:

- **Dev servers:** `pnpm dev` (port 3000) and `python main.py` (port 8000)
  conflict across worktrees.
- **Backend E2E:** Tests share Redis (the `_flush_test_redis` autouse fixture
  in `backend/tests/e2e/conftest.py` calls `FLUSHDB` on every e2e test) and
  share MySQL (single `cubeplex_test` database). Two worktrees running
  `make test` simultaneously corrupt each other's state.
- **Alembic versions:** Two worktrees on different branches modifying the
  schema race against the single `alembic_version` row in the shared DB.
- **Agent confusion:** Subagents and dev tooling don't know which port the
  current worktree is using; defaults are wrong; no canonical lookup exists.

Today there are 9 active worktrees and the pain is real.

## Goals

1. Multiple worktrees can run dev servers and full backend/frontend E2E
   suites in parallel without interfering with each other.
2. Each worktree's allocated ports / DB schema / Redis prefix are
   discoverable from a single file at the worktree root, by both humans
   and AI agents.
3. Setup is one command (`./scripts/new-worktree <branch>`); cleanup is one
   command (`./scripts/worktree-env destroy`).
4. CI behavior is unchanged — runners check out the main branch on a fresh
   filesystem, with no `.worktree.env`, and use existing defaults.

## Non-goals

- Per-worktree isolation of external services (sandbox, docling-serve,
  external LLM gateway, object store). These are shared. The object store
  uses a single bucket cleaned periodically.
- Concurrent worktree setup races. Single developer, no need.
- Replacing `git worktree` with anything else.

## High-level architecture

```
main repo  /home/chris/cubeplex/
  scripts/
    new-worktree         # wraps `git worktree add` + `worktree-env init`
    worktree-env         # init / show / doctor / destroy / clean-orphans
  .worktrees/
    registry.json        # gitignored; offset registry across all worktrees
    <slug>/              # each worktree
      .worktree.env      # script-managed, gitignored, single source of truth
      backend/.env       # user-managed, copied from main on first init
      backend/config.development.local.yaml  # same
  backend/cubeplex/config.py                # +5 lines: load .worktree.env
  frontend/packages/web/next.config.ts     # +3 lines: load .worktree.env
  frontend/playwright.config.ts            # +3 lines: load .worktree.env
  AGENTS.md                                # +section explaining workflow
```

## File ownership

| File | Owner | Lifetime |
|---|---|---|
| `.worktree.env` | `worktree-env` script (overwritten on each `init`) | per-worktree |
| `.worktrees/registry.json` | `worktree-env` script (atomic update) | shared by all worktrees |
| `backend/.env` | user (copied once from main if missing) | per-worktree |
| `backend/config.development.local.yaml` | user (copied once from main if missing) | per-worktree |

Mixing script-managed allocation values with user-managed secrets in the
same file is rejected: re-running `init` would risk clobbering hand-edited
LLM keys, sandbox keys, DB passwords. The two-file split makes ownership
unambiguous and `init` trivially idempotent.

## Allocation algorithm

```python
def allocate(branch_name: str, registry: dict) -> int:
    slug = slugify(branch_name)               # "feat/m7-file-upload" → "feat-m7-file-upload"
    if slug in registry:
        return registry[slug]["offset"]       # already allocated; stable
    initial = int(sha1(slug.encode()).hexdigest()[:8], 16) % 100
    used = {entry["offset"] for entry in registry.values()}
    slot = initial
    while slot in used:
        slot = (slot + 1) % 100
        if slot == initial:
            raise RuntimeError("all 100 slots taken; run clean-orphans")
    registry[slug] = {
        "offset": slot,
        "branch": branch_name,
        "path": str(worktree_path),
        "created_at": now_iso(),
    }
    return slot
```

- **Hash for compute, JSON for collision** — the user's chosen strategy.
  Hash gives a stable starting slot per slug; the registry is only
  consulted when collisions need to be resolved.
- **Main worktree special case:** if `branch == "main"` and worktree path
  equals the main repo root, offset = 0 → ports stay 3000 / 8000 →
  full backwards compatibility.
- **Slug rules:** lowercase, replace `/_.` with `-`, collapse repeats,
  strip leading/trailing `-`. DB schema name uses underscores
  (`cubeplex_<slug_with_underscores>`).
- **Slot range 0–99** gives 100 worktrees. Plenty for one developer.
  `clean-orphans` reclaims slots from removed worktrees.

### Derived values

```
frontend_port  = 3000 + offset
backend_port   = 8000 + offset
db_dev_schema  = f"cubeplex_{slug_with_underscores}"
db_test_schema = f"cubeplex_test_{slug_with_underscores}"
redis_prefix   = f"cubeplex-{slug}"
```

### `.worktree.env` example

```
# Worktree: feat/m7-file-upload (slot 37)
CUBEPLEX_WORKTREE_NAME=feat-m7-file-upload
CUBEPLEX_WORKTREE_SLOT=37

# Backend
CUBEPLEX_API__HOST=127.0.0.1
CUBEPLEX_API__PORT=8037
CUBEPLEX_DATABASE__NAME=cubeplex_feat_m7_file_upload
CUBEPLEX_REDIS__KEY_PREFIX=cubeplex-feat-m7-file-upload

# Frontend (Next dev / SSR rewrite / Playwright)
CUBEPLEX_API_URL=http://localhost:8037
PORT=3037
BASE_URL=http://localhost:3037
```

## Helper scripts

### `scripts/new-worktree <branch> [git-worktree-args...]`

```bash
# Always branch from latest main
git fetch origin main
git worktree add ".worktrees/${slug}" -b "${branch}" origin/main "$@"
cd ".worktrees/${slug}"
"${MAIN_REPO}/scripts/worktree-env" init
```

- Always fetches `origin/main` and branches from it. This is a hard rule:
  every worktree starts from latest main.
- Extra args pass through to `git worktree add` (e.g. `--track`).
- For checking out an existing branch into a worktree (e.g. cherry-pick
  scenarios), users run raw `git worktree add ... && worktree-env init`
  themselves — wrapper stays opinionated for the common case.

### `scripts/worktree-env <subcommand>`

Single Python script, runs from any worktree. Resolves the main repo via
`git worktree list --porcelain | head -1`, locates
`<main>/.worktrees/registry.json`.

| Subcommand | Behavior |
|---|---|
| `init` | Read branch from `git`, slugify, allocate slot, atomically update `registry.json`, copy `backend/.env` and `config.development.local.yaml` from main if missing in current worktree, write `.worktree.env`, create dev+test MySQL schemas (`CREATE DATABASE IF NOT EXISTS`), run `alembic upgrade head` against dev schema (failure → exit non-zero, schema is **not** rolled back), then run `show`. **Order matters:** `.worktree.env` is written before alembic runs so `backend/cubeplex/config.py` resolves to the worktree's dev schema when alembic loads the app config. Idempotent. |
| `show` | Pretty-print all values from `.worktree.env`. Used by humans and agents. |
| `doctor` | Verify dev+test schemas exist, alembic head matches latest revision, `.worktree.env` is consistent with registry, backend port is free *or* held by this worktree's process, Redis is reachable, MySQL is reachable. |
| `destroy` | Drop dev+test schemas (`DROP DATABASE IF EXISTS`), `SCAN + DEL` keys matching `${redis_prefix}:*`, remove this worktree's entry from registry, delete `.worktree.env`. Does **not** run `git worktree remove` and does **not** touch `backend/.env`. |
| `clean-orphans` | List registry entries whose `path` no longer exists or is no longer in `git worktree list`; list MySQL `cubeplex_*` schemas not referenced by any current worktree. Prompt for confirmation, then drop schemas and prune registry entries. |

### Atomic registry update

`registry.json` is read, modified, written to a temp file, then `rename`d.
Single-developer workflow makes locking unnecessary; rename is atomic on
Linux which is enough.

## Configuration loader hooks

### Backend — `backend/cubeplex/config.py`

Insert before line 26 (`config = dynaconf.Dynaconf(...)`):

```python
from dotenv import load_dotenv

worktree_env_path = backend_dir.parent / ".worktree.env"
if worktree_env_path.exists():
    load_dotenv(worktree_env_path, override=False)
```

`override=False` preserves the precedence: shell exports > `.worktree.env`
> `backend/.env` > YAML files. This way real shell exports still win,
which CI relies on.

### Frontend — `frontend/packages/web/next.config.ts`

```typescript
import dotenv from 'dotenv'
import path from 'path'
dotenv.config({
  path: path.resolve(__dirname, '../../../.worktree.env'),
  override: false,
})
```

Loaded before the existing `next.config` so the rewrite rule
`process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'` picks up
the worktree's backend port.

### Frontend — `frontend/playwright.config.ts`

```typescript
import dotenv from 'dotenv'
import path from 'path'
dotenv.config({
  path: path.resolve(__dirname, '../.worktree.env'),
  override: false,
})
```

Existing `BASE_URL = process.env.BASE_URL ?? 'http://localhost:3000'` and
`webServer.command = 'pnpm --filter web dev'` then automatically use the
allocated `PORT` and `BASE_URL`.

## How isolation actually fires for E2E

**Backend E2E** uses `httpx` `TestClient` against the in-process ASGI app.
No port involved. But the app reads:

- `config.database.name` → from `CUBEPLEX_DATABASE__NAME` → worktree's
  dedicated dev schema. The test conftest connects to this schema.
- `config.redis.key_prefix` → routed through `app.state.redis_key_prefix`
  (set in `backend/cubeplex/api/app.py:128–130`) → all stream / cache /
  rate-limit keys are namespaced. The autouse `_flush_test_redis` fixture
  calls `FLUSHDB` which deletes only keys in the connected DB **but** all
  key access elsewhere goes through the prefix, so two parallel worktrees
  each only see their own keys. Note: `FLUSHDB` still empties the entire
  DB — see "Open question" below.

**Frontend E2E** (Playwright) uses the worktree's allocated 3000+offset
port for the dev server, and Next's rewrite uses 8000+offset for the
backend. Two parallel `pnpm test:e2e` invocations cannot collide.

### Open question: FLUSHDB vs key prefix

The current `_flush_test_redis` fixture calls `FLUSHDB`, which clears the
entire database — agnostic to key prefix. With two parallel worktrees on
the same Redis DB, one worktree's `FLUSHDB` deletes the other's keys.

**Resolution (in this design):** change `_flush_test_redis` to delete only
keys matching the worktree's prefix:

```python
prefix = _cubeplex_config.get("redis.key_prefix", "cubeplex")
env_suffix = _cubeplex_config.get("env", "test")
pattern = f"{prefix}:{env_suffix}:*"
async for key in client.scan_iter(match=pattern, count=500):
    await client.delete(key)
```

This is required for parallel E2E. It's a small change in
`backend/tests/e2e/conftest.py`; we document it in the implementation plan.

## CI compatibility

- CI checks out main on a clean GitHub runner. No `.worktrees/`, no
  `.worktree.env`. The dotenv loaders all no-op (path doesn't exist).
- `config.test.yaml` continues to set `port: 8001`, `database.name:
  cubeplex_test`, default Redis prefix.
- The new `_flush_test_redis` prefix-scoped delete behaves identically to
  `FLUSHDB` when only one worktree (the runner) uses the DB, because the
  scan covers all keys under the single prefix.

## Agent discoverability

Add a section to `AGENTS.md` (which `CLAUDE.md` symlinks to). It tells any
agent landing in the repo:

```markdown
## Worktrees and parallel dev

This repo uses `git worktree` for parallel feature development. To avoid
port / DB / Redis collisions, each worktree gets its own allocated ports
and DB schema, written to `<worktree_root>/.worktree.env`.

### Creating a new worktree
Always run from the main repo root:

    ./scripts/new-worktree feat/<branch-name>

This branches from latest `origin/main`, allocates ports/schema/prefix,
creates the MySQL schema, runs `alembic upgrade head`, and copies
`backend/.env` and `config.development.local.yaml` from main.

### Working inside a worktree
First thing on entry: read the allocated values.

    ./scripts/worktree-env show
    # or just: cat .worktree.env

**Never assume ports 3000 / 8000 inside a worktree.** Always read
`.worktree.env`. The backend / frontend / Playwright configs auto-load it,
so `python main.py`, `pnpm dev`, and `pnpm test:e2e` all just work — but
when you `curl` or check ports manually, you must use the allocated ones.

### Other subcommands
- `worktree-env doctor` — verify schema exists, ports free, services reachable
- `worktree-env destroy` — drop schemas, clear redis prefix, delete `.worktree.env` (run before `git worktree remove`)
- `worktree-env clean-orphans` — clean residual schemas from removed worktrees
```

## Edge cases

| Case | Handling |
|---|---|
| User removes worktree without running `destroy` | `clean-orphans` reclaims slot + drops schemas |
| User runs `init` twice | Idempotent: registry returns existing slot, schema `IF NOT EXISTS`, alembic upgrade is naturally idempotent, `.worktree.env` overwritten with same values |
| User renames the worktree branch | Slug changes → new entry → new slot. Old entry becomes orphan; `clean-orphans` handles it |
| Worktree path is moved manually | Registry's stored `path` becomes stale; `doctor` flags it; user re-runs `init` to refresh |
| Slot 0 collision (someone tries to slug something to "main") | Reserved: main is always slot 0, registry rejects re-allocation |
| All 100 slots taken | `init` errors with instruction to run `clean-orphans` |
| User edits `.worktree.env` by hand | Next `init` overwrites; doctor flags drift between file and registry |

## Implementation outline (for the plan)

1. Write `scripts/worktree-env` (Python). Subcommands, registry I/O, slug,
   allocation, alembic upgrade, redis prefix scan-delete. Note: `init`
   loads its own `.worktree.env` into `os.environ` before invoking
   `alembic upgrade head` as a subprocess, so the subprocess inherits the
   worktree's `CUBEPLEX_DATABASE__NAME` and writes migrations to the right
   schema.
2. Write `scripts/new-worktree` (bash, ~15 lines).
3. Patch `backend/cubeplex/config.py` with the dotenv preamble.
4. Patch `frontend/packages/web/next.config.ts` and
   `frontend/playwright.config.ts` with their dotenv preambles.
5. Patch `backend/tests/e2e/conftest.py` `_flush_test_redis` to use
   prefix-scoped scan-delete.
6. Add `Worktrees and parallel dev` section to `AGENTS.md`.
7. Add `.worktrees/registry.json` to `.gitignore` (already covered by
   `.worktrees/`, but make explicit).
8. Smoke test: create two parallel worktrees, run backend E2E in each
   simultaneously, confirm no interference.

## Out of scope

- Object store key prefixing (shared bucket is fine; periodic cleanup
  separate).
- Sandbox / docling / external LLM tenant isolation (shared, not used by
  CI in a contended way).
- Multi-developer locking on registry (single developer assumption).
- Auto-running `worktree-env destroy` from a `git worktree remove` hook
  (git doesn't have a worktree-remove hook; documented as manual step).
